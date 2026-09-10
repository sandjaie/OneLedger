"""Slice 1.4 task 4b2a: the dashboard's balance-sheet half — net worth and cards due.

`GET /dashboard` answers in one payload. This half reads `account` and `card_statement`, and never a
flow; 4b2b adds the month and the trend, which read `v_transaction_reportable`. The screen tolerates
either half arriving first.

**Net worth is one SUM of signed balances** (ADR-0010) — assets positive, liabilities negative, no
CASE on the account's class and no subtraction, because each of those is a place a sign error
hides. The asset, liability and liquid figures are FILTERs on the same sum. Each balance is
translated at the rate for the date being viewed (`multi-currency-and-fx.html` §6), multiplied and
aggregated in Postgres, with the rates passed in as parameters.
`asOf` moves the rates and the card cycles, not the balances, which are the accounts' current
ones; a past date's balance sheet is `GET /networth`'s.

**Which rate, and how it is labelled (Invariant 7).** The date's own row when the table holds one —
itself labelled carried over a weekend, with the date it really comes from. The table is filled
forward only as far as its last fetch, so past it the dashboard fills forward the same way the table
fills its weekends, never interpolating, and labels the rate carried with its real date. A held
currency with no rate at or before the date makes that reporting currency's net worth **null**,
naming the currency and the date — never a partial sum, which would be a smaller, confident, wrong
number. This route fetches no rate: reads write nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..money import _fixed, _out

router = APIRouter(tags=["dashboard"])


class RateUsed(BaseModel):
    currency: str
    rateToBase: str | None
    rateToSecondary: str | None
    # The date the rate is really from, and whether that is not the day being viewed.
    rateDate: date | None
    carried: bool


class MissingRate(BaseModel):
    currency: str
    reportingCurrency: str
    asOf: date


class NetWorth(BaseModel):
    asOf: date
    baseCurrency: str
    secondaryCurrency: str
    totalBase: str | None
    totalSecondary: str | None
    assetsBase: str | None
    assetsSecondary: str | None
    liabilitiesBase: str | None
    liabilitiesSecondary: str | None
    liquidBase: str | None
    liquidSecondary: str | None
    rates: list[RateUsed]
    missing: list[MissingRate]


class CardDue(BaseModel):
    accountId: UUID
    accountName: str
    currency: str
    periodEnd: date
    statementBalance: str
    minimumDue: str | None
    paymentDueDate: date
    daysRemaining: int
    isPaidInFull: bool | None
    # statement_balance ÷ credit_limit at statement date, human-readable: 22.5000 is 22.5%.
    utilisationPct: str | None


class DashboardResponse(BaseModel):
    netWorth: NetWorth
    cardsDue: list[CardDue]


_RATE = """SELECT rate, rate_date, is_carried_forward, carried_from FROM fx_rate
            WHERE base_currency = %s AND quote_currency = %s AND rate_date <= %s
            ORDER BY rate_date DESC LIMIT 1"""

_COUNTED = "a.household_id = %s AND a.is_active AND a.include_in_net_worth"

NET_WORTH = f"""
    WITH rates (currency, to_base, to_secondary) AS (
        SELECT * FROM unnest(%s::char(3)[], %s::numeric[], %s::numeric[])
    )
    SELECT coalesce(round(sum(a.current_balance * r.to_base), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_secondary), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_base)
                          FILTER (WHERE a.account_class = 'asset'), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_secondary)
                          FILTER (WHERE a.account_class = 'asset'), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_base)
                          FILTER (WHERE a.account_class = 'liability'), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_secondary)
                          FILTER (WHERE a.account_class = 'liability'), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_base) FILTER (WHERE a.is_liquid), 4), 0),
           coalesce(round(sum(a.current_balance * r.to_secondary)
                          FILTER (WHERE a.is_liquid), 4), 0)
      FROM account a JOIN rates r ON r.currency = a.currency
     WHERE {_COUNTED}"""

# The latest cycle per card known as of the date. Days and utilisation are computed here, and
# utilisation is absent when the cycle states no limit — NULLIF, so no CASE is needed.
CARDS_DUE = """
    SELECT * FROM (
        SELECT DISTINCT ON (c.account_id)
               c.account_id, a.name, c.currency, c.period_end, c.statement_balance,
               c.minimum_due, c.payment_due_date, c.payment_due_date - %s::date,
               c.is_paid_in_full,
               round(c.statement_balance / NULLIF(c.credit_limit, 0) * 100, 4)
          FROM card_statement c JOIN account a ON a.id = c.account_id
         WHERE c.household_id = %s AND a.is_active AND c.period_end <= %s
         ORDER BY c.account_id, c.period_end DESC
    ) latest
    ORDER BY payment_due_date, name"""


class _Rate:
    """1 currency = `rate` base, the date it is really from, and whether that is not `as_of`."""

    def __init__(self, rate: Decimal, real: date, carried: bool) -> None:
        self.rate, self.real, self.carried = rate, real, carried


def _to_base(cur: psycopg.Cursor[Any], currency: str, base: str, as_of: date) -> _Rate | None:
    if currency == base:
        return _Rate(Decimal(1), as_of, False)
    cur.execute(_RATE, (currency, base, as_of))
    found = cur.fetchone()
    if found is None:
        return None
    rate, on, is_carried, carried_from = found
    real = carried_from if is_carried else on
    return _Rate(Decimal(rate), real, bool(is_carried) or on < as_of)


def _net_worth(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> NetWorth:
    cur.execute(
        f"SELECT DISTINCT a.currency FROM account a WHERE {_COUNTED} ORDER BY 1", (household_id,)
    )
    held = [str(row[0]) for row in cur.fetchall()]
    secondary_rate = _to_base(cur, secondary, base, as_of)

    rates: list[RateUsed] = []
    missing: list[MissingRate] = []
    to_base: list[Decimal | None] = []
    to_secondary: list[Decimal | None] = []
    for currency in held:
        own = _to_base(cur, currency, base, as_of)
        base_leg = own.rate if own else None
        # A cross through the base, fixed at the stored precision as `money._frozen` fixes its
        # own. Its date is the older leg's, and it is carried if either leg is.
        secondary_leg = _fixed(own.rate / secondary_rate.rate) if own and secondary_rate else None
        # The legs this currency's translation actually used — none when it has no rate of its
        # own, so a missing currency never borrows another leg's date.
        dated = (
            []
            if own is None
            else [leg for leg in (own, secondary_rate if currency != secondary else None) if leg]
        )
        if own is None:
            missing.append(MissingRate(currency=currency, reportingCurrency=base, asOf=as_of))
        if secondary_leg is None:
            missing.append(MissingRate(currency=currency, reportingCurrency=secondary, asOf=as_of))
        rates.append(
            RateUsed(
                currency=currency,
                rateToBase=_out(base_leg),
                rateToSecondary=_out(secondary_leg),
                rateDate=min((leg.real for leg in dated), default=None),
                carried=any(leg.carried for leg in dated),
            )
        )
        to_base.append(base_leg)
        to_secondary.append(secondary_leg)

    cur.execute(NET_WORTH, (held, to_base, to_secondary, household_id))
    figures = cur.fetchone() or (None,) * 8
    # Python decides which total may be shown and does no arithmetic: a missing rate nulls its
    # reporting currency whole rather than letting SUM skip the account it could not translate.
    base_whole = all(rate is not None for rate in to_base)
    secondary_whole = all(rate is not None for rate in to_secondary)

    def shown(value: Decimal | None, whole: bool) -> str | None:
        return _out(value) if whole else None

    return NetWorth(
        asOf=as_of,
        baseCurrency=base,
        secondaryCurrency=secondary,
        totalBase=shown(figures[0], base_whole),
        totalSecondary=shown(figures[1], secondary_whole),
        assetsBase=shown(figures[2], base_whole),
        assetsSecondary=shown(figures[3], secondary_whole),
        liabilitiesBase=shown(figures[4], base_whole),
        liabilitiesSecondary=shown(figures[5], secondary_whole),
        liquidBase=shown(figures[6], base_whole),
        liquidSecondary=shown(figures[7], secondary_whole),
        rates=rates,
        missing=missing,
    )


@router.get("/dashboard")
def read_dashboard(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> DashboardResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT base_currency, secondary_currency FROM household WHERE id = %s",
            (owner.household_id,),
        )
        pair = cur.fetchone()
        assert pair is not None
        net_worth = _net_worth(cur, owner.household_id, str(pair[0]), str(pair[1]), as_of)
        cur.execute(CARDS_DUE, (as_of, owner.household_id, as_of))
        cards = [
            CardDue(
                accountId=row[0],
                accountName=row[1],
                currency=row[2],
                periodEnd=row[3],
                statementBalance=str(row[4]),
                minimumDue=_out(row[5]),
                paymentDueDate=row[6],
                daysRemaining=int(row[7]),
                isPaidInFull=row[8],
                utilisationPct=_out(row[9]),
            )
            for row in cur.fetchall()
        ]
    return DashboardResponse(netWorth=net_worth, cardsDue=cards)
