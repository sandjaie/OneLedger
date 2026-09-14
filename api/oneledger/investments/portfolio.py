"""Household-level portfolio summary and one holding's detail read model (Task 19).

Both read models are thin: they reuse `positions.read_positions` for valuation and
`returns.xirr`/`returns.portfolio_flows`/`returns.group_allocation` for the return and
allocation math rather than restating either. Nothing here writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from pydantic import BaseModel

from ..money import _out
from .adapters import ReviewReason
from .positions import read_positions
from .prices import PriceStatus, read_status
from .returns import PositionValue, XirrReason, group_allocation, portfolio_flows, xirr

_MONEY_QUANT = Decimal("0.0001")


def _money(value: Decimal) -> str:
    return str(value.quantize(_MONEY_QUANT))


class AllocationGroupRow(BaseModel):
    label: str
    valueBase: str
    ratio: str | None


class PortfolioSummary(BaseModel):
    asOf: date
    baseCurrency: str
    secondaryCurrency: str
    valueBase: str
    valueSecondary: str
    valueComplete: bool
    contributionsBase: str
    contributionsSecondary: str
    gainBase: str | None
    gainSecondary: str | None
    xirrPercent: str | None
    xirrReason: XirrReason | None
    allocationByAssetClass: list[AllocationGroupRow]
    allocationByAccount: list[AllocationGroupRow]
    allocationComplete: bool
    valuedCount: int
    unvaluedCount: int
    staleCount: int
    priceStatus: PriceStatus


_ACCOUNT_NAMES_SQL = "SELECT id, name FROM account WHERE household_id = %s"


def _account_names(cur: psycopg.Cursor[Any], household_id: UUID) -> dict[UUID, str]:
    cur.execute(_ACCOUNT_NAMES_SQL, (household_id,))
    return {row[0]: row[1] for row in cur.fetchall()}


def read_summary(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> PortfolioSummary:
    positions = read_positions(cur, household_id, base, secondary, as_of)
    account_names = _account_names(cur, household_id)

    value_base = sum(
        (Decimal(p.currentValueBase) for p in positions if p.currentValueBase is not None),
        Decimal(0),
    )
    value_secondary = sum(
        (
            Decimal(p.currentValueSecondary)
            for p in positions
            if p.currentValueSecondary is not None
        ),
        Decimal(0),
    )
    value_complete = all(p.currentValueBase is not None for p in positions) and all(
        p.currentValueSecondary is not None for p in positions
    )
    contributions_base = sum(
        (Decimal(p.cumulativeContributionsBase) for p in positions), Decimal(0)
    )
    contributions_secondary = sum(
        (Decimal(p.cumulativeContributionsSecondary) for p in positions), Decimal(0)
    )
    gain_complete = all(p.gainBase is not None and p.gainSecondary is not None for p in positions)
    gain_base = (
        _money(sum((Decimal(p.gainBase) for p in positions), Decimal(0)))  # type: ignore[arg-type]
        if gain_complete
        else None
    )
    gain_secondary = (
        _money(sum((Decimal(p.gainSecondary) for p in positions), Decimal(0)))  # type: ignore[arg-type]
        if gain_complete
        else None
    )

    flows = portfolio_flows(cur, household_id, base, secondary, as_of)
    if flows.flows is None:
        xirr_percent, xirr_reason = None, flows.reason
    else:
        result = xirr(flows.flows)
        xirr_percent, xirr_reason = _out(result.rate_percent), result.reason

    # A holding is "valued" for allocation/count purposes only once it has a base-currency
    # value: a holding with a native price but no base FX leg has `valueStatus == "available"`
    # yet `currentValueBase is None`, and must not be silently counted as valued at 0 (OL-002).
    allocation = group_allocation(
        [
            PositionValue(
                label_asset_class=p.assetClass,
                label_account=account_names.get(p.accountId, str(p.accountId)),
                value_base=Decimal(p.currentValueBase)
                if p.currentValueBase is not None
                else Decimal(0),
                is_valued=p.currentValueBase is not None,
            )
            for p in positions
        ]
    )

    valued_count = sum(1 for p in positions if p.currentValueBase is not None)

    return PortfolioSummary(
        asOf=as_of,
        baseCurrency=base,
        secondaryCurrency=secondary,
        valueBase=_money(value_base),
        valueSecondary=_money(value_secondary),
        valueComplete=value_complete,
        contributionsBase=_money(contributions_base),
        contributionsSecondary=_money(contributions_secondary),
        gainBase=gain_base,
        gainSecondary=gain_secondary,
        xirrPercent=xirr_percent,
        xirrReason=xirr_reason,
        allocationByAssetClass=[
            AllocationGroupRow(label=g.label, valueBase=_money(g.value_base), ratio=_out(g.ratio))
            for g in allocation.by_asset_class
        ],
        allocationByAccount=[
            AllocationGroupRow(label=g.label, valueBase=_money(g.value_base), ratio=_out(g.ratio))
            for g in allocation.by_account
        ],
        allocationComplete=allocation.complete,
        valuedCount=valued_count,
        unvaluedCount=len(positions) - valued_count,
        staleCount=sum(1 for p in positions if p.priceStatus == "stale"),
        priceStatus=read_status(cur, household_id),
    )


class HoldingCashLink(BaseModel):
    investmentEventId: UUID
    cashTransactionId: UUID
    role: str
    allocatedAmount: str
    currency: str


class HoldingPriceRow(BaseModel):
    priceDate: date
    price: str


class HoldingEventRow(BaseModel):
    id: UUID
    eventDate: date
    eventKind: str
    units: str | None
    unitRatio: str | None
    price: str | None
    grossAmount: str | None
    charges: str | None
    withheldTax: str | None
    netAmount: str | None
    currency: str
    correctsEventId: UUID | None
    counterpartEventId: UUID | None
    correctionState: str  # "active" | "corrected"


class HoldingDetail(BaseModel):
    holdingId: UUID
    accountId: UUID
    securityId: UUID
    securityName: str
    assetClass: str
    currency: str
    units: str
    openedOn: date
    closedOn: date | None
    events: list[HoldingEventRow]
    eventsTotal: int
    cashLinks: list[HoldingCashLink]
    prices: list[HoldingPriceRow]
    openReviewReasons: list[ReviewReason]


_HOLDING_SQL = """
    SELECT h.id, h.account_id, h.security_id, security.name, security.asset_class::text,
           security.currency, h.current_units, h.opened_on, h.closed_on
      FROM holding h
      JOIN security ON security.id = h.security_id
     WHERE h.household_id = %s AND h.id = %s
"""

_EVENTS_SQL = """
    SELECT t.id, t.event_date, t.event_kind::text, t.units, t.unit_ratio, t.price,
           t.gross_amount, t.charges, t.withheld_tax, t.net_amount, t.currency,
           t.corrects_transaction_id, t.counterpart_transaction_id,
           NOT EXISTS (
               SELECT FROM investment_transaction c WHERE c.corrects_transaction_id = t.id
           ) AS is_active,
           count(*) OVER () AS total
      FROM investment_transaction t
     WHERE t.household_id = %s AND t.holding_id = %s
     ORDER BY t.event_date DESC, t.created_at DESC
     LIMIT 200
"""

_CASH_LINKS_SQL = """
    SELECT ica.investment_transaction_id, ica.cash_transaction_id, ica.role::text,
           ica.allocated_amount, cash.currency
      FROM investment_cash_allocation ica
      JOIN investment_transaction t
        ON t.id = ica.investment_transaction_id AND t.household_id = ica.household_id
      JOIN transaction cash
        ON cash.id = ica.cash_transaction_id AND cash.household_id = ica.household_id
     WHERE ica.household_id = %s AND t.holding_id = %s
     ORDER BY t.event_date DESC
"""

_PRICES_SQL = """
    SELECT price_date, price FROM price_snapshot
     WHERE security_id = %s AND is_accepted
     ORDER BY price_date DESC, observed_at DESC, id DESC
     LIMIT 30
"""

_REVIEW_REASONS_SQL = """
    SELECT DISTINCT reason::text FROM investment_review_item
     WHERE household_id = %s AND holding_id = %s AND status = 'open'
     ORDER BY reason
"""


@dataclass(frozen=True)
class _Holding:
    id: UUID
    account_id: UUID
    security_id: UUID
    name: str
    asset_class: str
    currency: str
    units: Decimal
    opened_on: date
    closed_on: date | None


def read_holding_detail(
    cur: psycopg.Cursor[Any], household_id: UUID, holding_id: UUID
) -> HoldingDetail | None:
    """The owner's own facts for one holding; no lot, evidence or identifier ever appears."""
    cur.execute(_HOLDING_SQL, (household_id, holding_id))
    row = cur.fetchone()
    if row is None:
        return None
    holding = _Holding(
        id=row[0],
        account_id=row[1],
        security_id=row[2],
        name=row[3],
        asset_class=row[4],
        currency=str(row[5]).strip(),
        units=Decimal(row[6]),
        opened_on=row[7],
        closed_on=row[8],
    )

    cur.execute(_EVENTS_SQL, (household_id, holding.id))
    event_rows = cur.fetchall()
    events_total = int(event_rows[0][14]) if event_rows else 0
    events = [
        HoldingEventRow(
            id=r[0],
            eventDate=r[1],
            eventKind=r[2],
            units=_out(None if r[3] is None else Decimal(r[3])),
            unitRatio=_out(None if r[4] is None else Decimal(r[4])),
            price=_out(None if r[5] is None else Decimal(r[5])),
            grossAmount=_out(None if r[6] is None else Decimal(r[6])),
            charges=_out(None if r[7] is None else Decimal(r[7])),
            withheldTax=_out(None if r[8] is None else Decimal(r[8])),
            netAmount=_out(None if r[9] is None else Decimal(r[9])),
            currency=str(r[10]).strip(),
            correctsEventId=r[11],
            counterpartEventId=r[12],
            correctionState="active" if r[13] else "corrected",
        )
        for r in event_rows
    ]

    cur.execute(_CASH_LINKS_SQL, (household_id, holding.id))
    cash_links = [
        HoldingCashLink(
            investmentEventId=r[0],
            cashTransactionId=r[1],
            role=r[2],
            allocatedAmount=_out(Decimal(r[3])) or "0",
            currency=str(r[4]).strip(),
        )
        for r in cur.fetchall()
    ]

    cur.execute(_PRICES_SQL, (holding.security_id,))
    prices = [
        HoldingPriceRow(priceDate=r[0], price=_out(Decimal(r[1])) or "0") for r in cur.fetchall()
    ]

    cur.execute(_REVIEW_REASONS_SQL, (household_id, holding.id))
    open_review_reasons = [r[0] for r in cur.fetchall()]

    return HoldingDetail(
        holdingId=holding.id,
        accountId=holding.account_id,
        securityId=holding.security_id,
        securityName=holding.name,
        assetClass=holding.asset_class,
        currency=holding.currency,
        # `str()` on an exact-zero NUMERIC(28,8) prints as "0E-8" (Decimal's to-string rule
        # switches to scientific notation once the adjusted exponent is below -6) — the same
        # fix positions.py applies for a closed holding's units.
        units=format(holding.units, "f"),
        openedOn=holding.opened_on,
        closedOn=holding.closed_on,
        events=events,
        eventsTotal=events_total,
        cashLinks=cash_links,
        prices=prices,
        openReviewReasons=open_review_reasons,
    )


__all__ = [
    "AllocationGroupRow",
    "HoldingCashLink",
    "HoldingDetail",
    "HoldingEventRow",
    "HoldingPriceRow",
    "PortfolioSummary",
    "read_holding_detail",
    "read_summary",
]
