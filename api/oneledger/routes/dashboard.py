"""Slice 1.4 task 4b2: the dashboard — net worth, this month, the trend and the cards due.

`GET /dashboard` answers in one payload. Its balance-sheet half reads the one valuation source
selected for each account plus `card_statement`; its flow half reads `v_transaction_reportable`
and nothing else.

**Net worth is one SUM of signed values** (ADR-0010) — ledger-mode signed balances, holding values
or one frozen snapshot, never two sources for one account. Assets are positive and liabilities
negative; the asset, liability and liquid figures are FILTERs on the same PostgreSQL sum. `asOf`
selects the valuation period, event-derived units, accepted non-future price, snapshot, rates and
card cycles. A ledger-mode account still contributes its current balance, so a complete historical
balance sheet remains `GET /networth`'s.

**Which rate, and how it is labelled (Invariant 7).** The date's own row when the table holds one —
itself labelled carried over a weekend, with the date it really comes from. The table is filled
forward only as far as its last fetch, so past it the dashboard fills forward the same way the table
fills its weekends, never interpolating, and labels the rate carried with its real date. A held
currency with no rate at or before the date makes that reporting currency's net worth **null**,
naming the currency and the date — never a partial sum, which would be a smaller, confident, wrong
number. This route fetches no rate: reads write nothing.

**Flows are frozen (I3, I5).** This month and the eleven before it sum `amount_base` and
`amount_secondary` as each row froze them on its own day — never the base table, never a rate.
Income is the `income` rows; spending is every other reportable row with its sign turned, so a
refund lowers spending rather than counting as income (`classification.html`); net is one SUM of
the lot. The savings rate is net over income, null when income is not positive (`finance-os.html`
F02).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..investments.positions import current_rate_pair, read_positions
from ..money import _out, translate

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


class InvestmentCoverage(BaseModel):
    accountId: UUID
    holdingId: UUID | None
    valuationMode: Literal["holdings", "snapshot"]
    status: Literal[
        "current_price",
        "stale_price",
        "missing_price",
        "future_price_only",
        "outside_supported_range",
        "current_snapshot",
        "carried_snapshot",
        "missing_snapshot",
    ]
    valueDate: date | None
    carried: bool
    ageDays: int | None
    method: Literal["owner_entered", "market_quote", "indexed", "statement"] | None
    confidence: Literal["high", "medium", "low"] | None


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
    investmentCoverage: list[InvestmentCoverage]


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


class MonthFlows(BaseModel):
    # The first day of the month the figures belong to.
    month: date
    incomeBase: str
    incomeSecondary: str
    spendingBase: str
    spendingSecondary: str
    netBase: str
    netSecondary: str
    # net ÷ income, human-readable: 15.2500 is 15.25%. Null when income is not positive.
    savingsRatePctBase: str | None
    savingsRatePctSecondary: str | None


class YearToDate(BaseModel):
    # Calendar year of `asOf`, 1 January through `asOf` inclusive.
    yearStart: date
    asOf: date
    incomeBase: str
    incomeSecondary: str
    spendingBase: str
    spendingSecondary: str
    netBase: str
    netSecondary: str
    savingsRatePctBase: str | None
    savingsRatePctSecondary: str | None
    investedBase: str
    investedSecondary: str


class DashboardResponse(BaseModel):
    netWorth: NetWorth
    thisMonth: MonthFlows
    trend: list[MonthFlows]
    cardsDue: list[CardDue]
    yearToDate: YearToDate


_COUNTED = "a.household_id = %s AND a.is_active AND a.include_in_net_worth"

# The valuation period covering `a.id` at a given date: the active period effective then, or —
# for a date before any period started — the account's first period. Shared with
# `routes/plans.py`'s forecast (spec "Forecast computation" rule 1) so both read the same
# account's mode the same way; two `%s` placeholders (the date, twice) precede the household
# filter wherever this is embedded.
VALUATION_PERIOD_FOR_ACCOUNT = """coalesce(
                    (SELECT active.id FROM account_valuation_period active
                      WHERE active.account_id = a.id AND %s >= active.effective_from
                        AND (active.effective_to IS NULL OR %s <= active.effective_to)
                      ORDER BY active.effective_from DESC LIMIT 1),
                    (SELECT initial.id FROM account_valuation_period initial
                      WHERE initial.account_id = a.id
                      ORDER BY initial.effective_from LIMIT 1))"""

NET_WORTH = """
    WITH values (account_class, is_liquid, value_base, value_secondary) AS (
        SELECT * FROM unnest(%s::account_class[], %s::boolean[], %s::numeric[], %s::numeric[])
    )
    SELECT coalesce(round(sum(value_base), 4), 0),
           coalesce(round(sum(value_secondary), 4), 0),
           coalesce(round(sum(value_base) FILTER (WHERE account_class = 'asset'), 4), 0),
           coalesce(round(sum(value_secondary) FILTER (WHERE account_class = 'asset'), 4), 0),
           coalesce(round(sum(value_base) FILTER (WHERE account_class = 'liability'), 4), 0),
           coalesce(round(sum(value_secondary) FILTER (WHERE account_class = 'liability'), 4), 0),
           coalesce(round(sum(value_base) FILTER (WHERE is_liquid), 4), 0),
           coalesce(round(sum(value_secondary) FILTER (WHERE is_liquid), 4), 0)
      FROM values"""

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


# The month of the as-of date and the eleven before it, through the as-of date, from frozen
# amounts only. A month with no rows is zeros and no savings rate; GREATEST and NULLIF make the
# rate null wherever income is not positive, so no CASE is needed.
FLOWS = """
    WITH months AS (
        SELECT generate_series(date_trunc('month', %(as_of)s::timestamp) - interval '11 months',
                               date_trunc('month', %(as_of)s::timestamp),
                               interval '1 month')::date AS month
    ), flows AS (
        SELECT date_trunc('month', booked_date::timestamp)::date AS month,
               sum(amount_base) FILTER (WHERE txn_type = 'income') AS income_base,
               sum(amount_secondary) FILTER (WHERE txn_type = 'income') AS income_secondary,
               -sum(amount_base) FILTER (WHERE txn_type <> 'income') AS spending_base,
               -sum(amount_secondary) FILTER (WHERE txn_type <> 'income') AS spending_secondary,
               sum(amount_base) AS net_base,
               sum(amount_secondary) AS net_secondary
          FROM v_transaction_reportable
         WHERE household_id = %(household)s
           AND booked_date >= date_trunc('month', %(as_of)s::timestamp) - interval '11 months'
           AND booked_date <= %(as_of)s
         GROUP BY 1
    )
    SELECT m.month,
           coalesce(f.income_base, 0.0000), coalesce(f.income_secondary, 0.0000),
           coalesce(f.spending_base, 0.0000), coalesce(f.spending_secondary, 0.0000),
           coalesce(f.net_base, 0.0000), coalesce(f.net_secondary, 0.0000),
           round(f.net_base / NULLIF(GREATEST(f.income_base, 0), 0) * 100, 4),
           round(f.net_secondary / NULLIF(GREATEST(f.income_secondary, 0), 0) * 100, 4)
      FROM months m LEFT JOIN flows f USING (month)
     ORDER BY m.month"""


# The calendar year of `asOf` through `asOf`, same classification as FLOWS, plus investment
# contributions from active, counted accounts over the same window — active (not corrected) rows
# only, so a corrected purchase counts once, via its replacement.
YEAR_TO_DATE = """
    WITH flows AS (
        SELECT sum(amount_base) FILTER (WHERE txn_type = 'income') AS income_base,
               sum(amount_secondary) FILTER (WHERE txn_type = 'income') AS income_secondary,
               -sum(amount_base) FILTER (WHERE txn_type <> 'income') AS spending_base,
               -sum(amount_secondary) FILTER (WHERE txn_type <> 'income') AS spending_secondary,
               sum(amount_base) AS net_base,
               sum(amount_secondary) AS net_secondary
          FROM v_transaction_reportable
         WHERE household_id = %(household)s
           AND booked_date >= date_trunc('year', %(as_of)s::timestamp)
           AND booked_date <= %(as_of)s
    ), invested AS (
        SELECT sum(-e.net_amount_base) AS invested_base,
               sum(-e.net_amount_secondary) AS invested_secondary
          FROM investment_transaction e
          JOIN account a ON a.household_id = e.household_id AND a.id = e.account_id
         WHERE e.household_id = %(household)s AND a.is_active AND a.include_in_net_worth
           AND e.event_kind IN ('purchase', 'recurring_purchase', 'rights')
           AND e.event_date >= date_trunc('year', %(as_of)s::timestamp)
           AND e.event_date <= %(as_of)s
           AND NOT EXISTS (
               SELECT 1 FROM investment_transaction c WHERE c.corrects_transaction_id = e.id
           )
    )
    SELECT coalesce(f.income_base, 0.0000), coalesce(f.income_secondary, 0.0000),
           coalesce(f.spending_base, 0.0000), coalesce(f.spending_secondary, 0.0000),
           coalesce(f.net_base, 0.0000), coalesce(f.net_secondary, 0.0000),
           round(f.net_base / NULLIF(GREATEST(f.income_base, 0), 0) * 100, 4),
           round(f.net_secondary / NULLIF(GREATEST(f.income_secondary, 0), 0) * 100, 4),
           coalesce(round(i.invested_base, 4), 0.0000),
           coalesce(round(i.invested_secondary, 4), 0.0000)
      FROM flows f, invested i"""


def _net_worth(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> NetWorth:
    cur.execute(
        f"""SELECT a.id, a.account_class::text, a.is_liquid, a.currency, a.current_balance,
                   period.valuation_mode::text, period.effective_from
              FROM account a
              JOIN account_valuation_period period
                ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
             WHERE {_COUNTED} ORDER BY a.id""",
        (as_of, as_of, household_id),
    )
    accounts = cur.fetchall()
    account_by_id = {row[0]: row for row in accounts}
    positions = read_positions(cur, household_id, base, secondary, as_of, included_only=True)
    held = sorted(
        {
            *(str(row[3]).strip() for row in accounts if row[5] == "ledger_balance"),
            *(position.currency for position in positions),
        }
    )
    rates: list[RateUsed] = []
    missing: list[MissingRate] = []
    rate_pairs: dict[str, tuple[Decimal | None, Decimal | None]] = {}
    for currency in held:
        pair = current_rate_pair(cur, currency, base, secondary, as_of)
        base_leg = None if pair.to_base is None else pair.to_base.rate
        secondary_leg = None if pair.to_secondary is None else pair.to_secondary.rate
        dated = [leg for leg in (pair.to_base, pair.to_secondary) if leg is not None]
        if pair.to_base is None:
            missing.append(MissingRate(currency=currency, reportingCurrency=base, asOf=as_of))
        if pair.to_secondary is None:
            missing.append(MissingRate(currency=currency, reportingCurrency=secondary, asOf=as_of))
        rates.append(
            RateUsed(
                currency=currency,
                rateToBase=_out(base_leg),
                rateToSecondary=_out(secondary_leg),
                rateDate=min((leg.real_date for leg in dated), default=None),
                carried=any(leg.carried for leg in dated),
            )
        )
        rate_pairs[currency] = (base_leg, secondary_leg)

    classes: list[str] = []
    liquidity: list[bool] = []
    values_base: list[Decimal | None] = []
    values_secondary: list[Decimal | None] = []
    coverage: list[InvestmentCoverage] = []

    for account_id, account_class, liquid, currency_raw, balance, mode, effective_from in accounts:
        currency = str(currency_raw).strip()
        if mode == "ledger_balance":
            rate_base, rate_secondary = rate_pairs[currency]
            classes.append(account_class)
            liquidity.append(bool(liquid))
            values_base.append(
                None if rate_base is None else translate(Decimal(balance), rate_base)
            )
            values_secondary.append(
                None if rate_secondary is None else translate(Decimal(balance), rate_secondary)
            )
        elif mode == "snapshot":
            cur.execute(
                """SELECT as_of_date, value_base, value_secondary,
                          method::text, confidence::text
                     FROM valuation_snapshot
                    WHERE household_id = %s AND account_id = %s
                      AND as_of_date >= %s AND as_of_date <= %s
                    ORDER BY as_of_date DESC, created_at DESC, id DESC LIMIT 1""",
                (household_id, account_id, effective_from, as_of),
            )
            snapshot = cur.fetchone()
            classes.append(account_class)
            liquidity.append(bool(liquid))
            values_base.append(None if snapshot is None else Decimal(snapshot[1]))
            values_secondary.append(None if snapshot is None else Decimal(snapshot[2]))
            value_date = None if snapshot is None else snapshot[0]
            coverage.append(
                InvestmentCoverage(
                    accountId=account_id,
                    holdingId=None,
                    valuationMode="snapshot",
                    status=(
                        "missing_snapshot"
                        if value_date is None
                        else "current_snapshot"
                        if value_date == as_of
                        else "carried_snapshot"
                    ),
                    valueDate=value_date,
                    carried=value_date is not None and value_date < as_of,
                    ageDays=None if value_date is None else (as_of - value_date).days,
                    method=None if snapshot is None else snapshot[3],
                    confidence=None if snapshot is None else snapshot[4],
                )
            )

    price_coverage = {
        "current": "current_price",
        "stale": "stale_price",
        "missing": "missing_price",
        "future_price_only": "future_price_only",
    }
    for position in positions:
        account = account_by_id[position.accountId]
        classes.append(str(account[1]))
        liquidity.append(bool(account[2]))
        values_base.append(
            None if position.currentValueBase is None else Decimal(position.currentValueBase)
        )
        values_secondary.append(
            None
            if position.currentValueSecondary is None
            else Decimal(position.currentValueSecondary)
        )
        coverage.append(
            InvestmentCoverage(
                accountId=position.accountId,
                holdingId=position.holdingId,
                valuationMode="holdings",
                status=(
                    "outside_supported_range"
                    if position.valueStatus == "outside_supported_range"
                    else price_coverage[position.priceStatus]  # type: ignore[arg-type]
                ),
                valueDate=position.priceDate,
                carried=position.priceDate is not None and position.priceDate < as_of,
                ageDays=(None if position.priceDate is None else (as_of - position.priceDate).days),
                method=None,
                confidence=None,
            )
        )

    cur.execute(NET_WORTH, (classes, liquidity, values_base, values_secondary))
    figures = cur.fetchone() or (None,) * 8
    # Python decides which total may be shown and does no arithmetic: a missing rate nulls its
    # reporting currency whole rather than letting SUM skip the account it could not translate.
    base_whole = all(value is not None for value in values_base)
    secondary_whole = all(value is not None for value in values_secondary)

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
        investmentCoverage=sorted(
            coverage,
            key=lambda item: (
                str(item.accountId),
                "" if item.holdingId is None else str(item.holdingId),
            ),
        ),
    )


@router.get("/dashboard")
def read_dashboard(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> DashboardResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            "SELECT base_currency, secondary_currency FROM household WHERE id = %s",
            (owner.household_id,),
        )
        pair = cur.fetchone()
        assert pair is not None
        net_worth = _net_worth(cur, owner.household_id, str(pair[0]), str(pair[1]), as_of)
        cur.execute(FLOWS, {"as_of": as_of, "household": owner.household_id})
        trend = [
            MonthFlows(
                month=row[0],
                incomeBase=str(row[1]),
                incomeSecondary=str(row[2]),
                spendingBase=str(row[3]),
                spendingSecondary=str(row[4]),
                netBase=str(row[5]),
                netSecondary=str(row[6]),
                savingsRatePctBase=_out(row[7]),
                savingsRatePctSecondary=_out(row[8]),
            )
            for row in cur.fetchall()
        ]
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
        cur.execute(YEAR_TO_DATE, {"as_of": as_of, "household": owner.household_id})
        ytd = cur.fetchone()
        assert ytd is not None
        year_to_date = YearToDate(
            yearStart=date(as_of.year, 1, 1),
            asOf=as_of,
            incomeBase=str(ytd[0]),
            incomeSecondary=str(ytd[1]),
            spendingBase=str(ytd[2]),
            spendingSecondary=str(ytd[3]),
            netBase=str(ytd[4]),
            netSecondary=str(ytd[5]),
            savingsRatePctBase=_out(ytd[6]),
            savingsRatePctSecondary=_out(ytd[7]),
            investedBase=str(ytd[8]),
            investedSecondary=str(ytd[9]),
        )
    return DashboardResponse(
        netWorth=net_worth,
        thisMonth=trend[-1],
        trend=trend,
        cardsDue=cards,
        yearToDate=year_to_date,
    )
