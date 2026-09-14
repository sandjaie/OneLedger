"""Phase 2.2 Task 1: `POST /wealth-history/refresh`, `GET /wealth-history` and
`GET /wealth-history/{monthEnd}` (spec "API"). Owner-only, like `routes/coverage.py`'s
acknowledgements: no `SECURITY DEFINER` write path, `require_owner` and an explicit
`household_id` filter are the authorization boundary. `GET` opens `SET TRANSACTION READ ONLY`
and never recomputes — it reads exactly what the last refresh stored in `month_end_position`
and `wealth_history_refresh` (the household's own `computed_through`/`computed_at`, so every
month in one response — including one with no position row — carries the one real refresh
timestamp). An incomplete month's totals are null rather than a partial sum (fail closed): a
smaller, confident, wrong figure is worse than an absent one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import attribution
from ..auth import OwnerSession, require_owner
from ..db import connect
from ..investments.positions import current_rate_pair
from ..money import _out
from ..wealth_history import RefreshCounts, refresh, stored_month_ends

router = APIRouter(tags=["wealth-history"])

_ZERO = Decimal("0.0000")


class MonthSummary(BaseModel):
    monthEnd: date
    # Null on an incomplete month (fails closed): a partial sum would be a smaller, confident,
    # wrong figure. `complete` and `statusCounts` say why.
    totalBase: str | None
    totalSecondary: str | None
    complete: bool
    statusCounts: dict[str, int]
    computedAt: datetime


class WealthHistoryResponse(BaseModel):
    baseCurrency: str
    secondaryCurrency: str
    months: list[MonthSummary]


class MonthEndPosition(BaseModel):
    accountId: UUID
    accountName: str
    holdingId: UUID | None
    securityName: str | None
    valuationMode: str
    currency: str
    nativeValue: str | None
    evidenceDate: date | None
    rateToBase: str | None
    rateToBaseDate: date | None
    rateToBaseCarried: bool | None
    rateToSecondary: str | None
    rateToSecondaryDate: date | None
    rateToSecondaryCarried: bool | None
    valueBase: str | None
    valueSecondary: str | None
    status: str


class MonthPositionsResponse(BaseModel):
    monthEnd: date
    baseCurrency: str
    secondaryCurrency: str
    positions: list[MonthEndPosition]


def _household_pair(cur: psycopg.Cursor[Any], household_id: UUID) -> tuple[str, str]:
    cur.execute(
        "SELECT base_currency, secondary_currency FROM household WHERE id = %s", (household_id,)
    )
    row = cur.fetchone()
    assert row is not None
    return str(row[0]).strip(), str(row[1]).strip()


@router.post("/wealth-history/refresh")
def post_refresh(owner: Annotated[OwnerSession, Depends(require_owner)]) -> RefreshCounts:
    with connect() as conn, conn.cursor() as cur:
        base, secondary = _household_pair(cur, owner.household_id)
        return refresh(cur, owner.household_id, base, secondary, date.today())


_REFRESH_STATE = """
    SELECT computed_through, computed_at FROM wealth_history_refresh WHERE household_id = %s
"""

_MONTHS = """
    SELECT month_end,
           round(sum(value_base) FILTER (WHERE status = 'available'), 4),
           round(sum(value_secondary) FILTER (WHERE status = 'available'), 4),
           bool_and(status = 'available') AS complete
      FROM month_end_position
     WHERE household_id = %s
     GROUP BY month_end
"""

_STATUS_COUNTS = """
    SELECT month_end, status::text, count(*)
      FROM month_end_position
     WHERE household_id = %s
     GROUP BY month_end, status
"""


@router.get("/wealth-history")
def read_wealth_history(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> WealthHistoryResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        base, secondary = _household_pair(cur, owner.household_id)
        cur.execute(_REFRESH_STATE, (owner.household_id,))
        state = cur.fetchone()
        if state is None:
            # Never refreshed: GET never recomputes, so there is nothing to list yet.
            return WealthHistoryResponse(baseCurrency=base, secondaryCurrency=secondary, months=[])
        computed_through, computed_at = state

        cur.execute(_STATUS_COUNTS, (owner.household_id,))
        counts_by_month: dict[date, dict[str, int]] = {}
        for month_end_value, status, count in cur.fetchall():
            counts_by_month.setdefault(month_end_value, {})[status] = int(count)

        cur.execute(_MONTHS, (owner.household_id,))
        totals_by_month: dict[date, tuple[Decimal | None, Decimal | None, bool]] = {
            row[0]: (row[1], row[2], bool(row[3])) for row in cur.fetchall()
        }

        months = []
        for month_end_value in stored_month_ends(computed_through):
            found = totals_by_month.get(month_end_value)
            total_base: Decimal | None
            total_secondary: Decimal | None
            if found is None:
                # No stored position row at all for this month (e.g. no account existed yet):
                # nothing was omitted from a sum, so the month is vacuously complete at zero.
                total_base, total_secondary, complete = _ZERO, _ZERO, True
            else:
                total_base, total_secondary, complete = found
            months.append(
                MonthSummary(
                    monthEnd=month_end_value,
                    totalBase=_out(total_base) if complete else None,
                    totalSecondary=_out(total_secondary) if complete else None,
                    complete=complete,
                    statusCounts=counts_by_month.get(month_end_value, {}),
                    computedAt=computed_at,
                )
            )
    return WealthHistoryResponse(baseCurrency=base, secondaryCurrency=secondary, months=months)


_POSITIONS_FOR_MONTH = """
    SELECT p.account_id, a.name, p.holding_id, security.name, p.valuation_mode::text, p.currency,
           p.native_value, p.evidence_date, p.rate_to_base, p.rate_to_base_date,
           p.rate_to_base_carried, p.rate_to_secondary, p.rate_to_secondary_date,
           p.rate_to_secondary_carried, p.value_base, p.value_secondary, p.status::text
      FROM month_end_position p
      JOIN account a ON a.household_id = p.household_id AND a.id = p.account_id
      LEFT JOIN holding h ON h.household_id = p.household_id AND h.id = p.holding_id
      LEFT JOIN security ON security.id = h.security_id
     WHERE p.household_id = %s AND p.month_end = %s
     ORDER BY a.name, security.name NULLS FIRST
"""


@router.get("/wealth-history/{monthEnd}")
def read_month_positions(
    monthEnd: date, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> MonthPositionsResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        base, secondary = _household_pair(cur, owner.household_id)
        cur.execute(_POSITIONS_FOR_MONTH, (owner.household_id, monthEnd))
        positions = [
            MonthEndPosition(
                accountId=row[0],
                accountName=row[1],
                holdingId=row[2],
                securityName=row[3],
                valuationMode=row[4],
                currency=str(row[5]).strip(),
                nativeValue=_out(row[6]),
                evidenceDate=row[7],
                rateToBase=_out(row[8]),
                rateToBaseDate=row[9],
                rateToBaseCarried=row[10],
                rateToSecondary=_out(row[11]),
                rateToSecondaryDate=row[12],
                rateToSecondaryCarried=row[13],
                valueBase=_out(row[14]),
                valueSecondary=_out(row[15]),
                status=row[16],
            )
            for row in cur.fetchall()
        ]
    return MonthPositionsResponse(
        monthEnd=monthEnd, baseCurrency=base, secondaryCurrency=secondary, positions=positions
    )


# --- Phase 2.2 Task 2: GET /wealth-history/{monthEnd}/attribution (spec "Attribution for one
# month"). `attribution.attribute` is pure; this route's own job is loading the two stored month
# ends, the window's frozen flows and funding events, and the closing rates it needs, once per
# reporting currency (`_LEG` below), and shaping the result.


@dataclass(frozen=True)
class _StoredPosition:
    account_id: UUID
    account_name: str
    holding_id: UUID | None
    valuation_mode: str
    currency: str
    native_value: Decimal | None
    value_base: Decimal | None
    value_secondary: Decimal | None
    status: str


def _position_key(account_id: UUID, holding_id: UUID | None) -> str:
    return str(account_id) if holding_id is None else f"{account_id}:{holding_id}"


def _load_positions(
    cur: psycopg.Cursor[Any], household_id: UUID, month_end: date
) -> list[_StoredPosition]:
    # Reuses _POSITIONS_FOR_MONTH exactly: the same rows GET /wealth-history/{monthEnd} shows.
    cur.execute(_POSITIONS_FOR_MONTH, (household_id, month_end))
    return [
        _StoredPosition(
            account_id=row[0],
            account_name=row[1],
            holding_id=row[2],
            valuation_mode=row[4],
            currency=str(row[5]).strip(),
            native_value=row[6],
            value_base=row[14],
            value_secondary=row[15],
            status=row[16],
        )
        for row in cur.fetchall()
    ]


# Spec "Holding positions": the event kinds that move cash with a holding.
_FUNDING_KINDS = (
    "purchase",
    "recurring_purchase",
    "rights",
    "sale",
    "redemption",
    "maturity",
)

_WINDOW_FLOWS = """
    SELECT p.account_id, p.currency, p.amount, p.amount_base, p.amount_secondary,
           p.txn_type::text, p.is_excluded, (r.id IS NOT NULL) AS is_reportable
      FROM v_transaction_posting p
      LEFT JOIN v_transaction_reportable r ON r.id = p.id
     WHERE p.household_id = %s AND p.account_id = ANY(%s)
       AND p.booked_date > %s AND p.booked_date <= %s
"""

_WINDOW_FUNDING = f"""
    SELECT event.account_id, event.holding_id, event.currency, event.net_amount,
           event.net_amount_base, event.net_amount_secondary
      FROM investment_transaction event
     WHERE event.household_id = %s AND event.holding_id = ANY(%s)
       AND event.event_date > %s AND event.event_date <= %s
       AND event.event_kind::text IN {_FUNDING_KINDS}
       AND NOT EXISTS (
           SELECT 1 FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
"""


class AttributionLeg(BaseModel):
    available: bool
    reasons: list[str]
    opening: str | None
    closing: str | None
    actualChange: str | None
    retainedIncome: str | None
    investmentIncome: str | None
    internalMovementsAndCosts: str | None
    otherExternalChanges: str | None
    marketMovement: str | None
    assetRevaluation: str | None
    fxTranslation: str | None
    residual: str | None


class MonthAttributionResponse(BaseModel):
    monthEnd: date
    baseCurrency: str
    secondaryCurrency: str
    base: AttributionLeg
    secondary: AttributionLeg


def _to_leg(result: attribution.Attribution) -> AttributionLeg:
    return AttributionLeg(
        available=result.available,
        reasons=list(result.reasons),
        opening=_out(result.opening),
        closing=_out(result.closing),
        actualChange=_out(result.actual_change),
        retainedIncome=_out(result.retained_income),
        investmentIncome=_out(result.investment_income),
        internalMovementsAndCosts=_out(result.internal_movements_and_costs),
        otherExternalChanges=_out(result.other_external_changes),
        marketMovement=_out(result.market_movement),
        assetRevaluation=_out(result.asset_revaluation),
        fxTranslation=_out(result.fx_translation),
        residual=_out(result.residual),
    )


def _attribute_leg(
    opening_rows: list[_StoredPosition],
    closing_rows: list[_StoredPosition],
    flow_rows: list[tuple[Any, ...]],
    funding_rows: list[tuple[Any, ...]],
    closing_rates: dict[str, Decimal],
    unavailable_reasons: list[str],
    *,
    leg: str,
) -> attribution.Attribution:
    opening_by_key = {_position_key(r.account_id, r.holding_id): r for r in opening_rows}
    closing_by_key = {_position_key(r.account_id, r.holding_id): r for r in closing_rows}

    def value(row: _StoredPosition) -> Decimal:
        chosen = row.value_base if leg == "base" else row.value_secondary
        assert chosen is not None  # both months are known complete before this is called
        return chosen

    def native(row: _StoredPosition) -> Decimal:
        assert row.native_value is not None  # ditto
        return row.native_value

    positions = []
    for key in opening_by_key.keys() | closing_by_key.keys():
        opening = opening_by_key.get(key)
        closing = closing_by_key.get(key)
        anchor = closing or opening
        assert anchor is not None
        positions.append(
            attribution.Position(
                key=key,
                kind=anchor.valuation_mode,  # type: ignore[arg-type]
                currency=anchor.currency,
                opening_native=native(opening) if opening else _ZERO,
                opening_value=value(opening) if opening else _ZERO,
                closing_native=native(closing) if closing else _ZERO,
                closing_value=value(closing) if closing else _ZERO,
            )
        )

    def flow_from_row(row: tuple[Any, ...]) -> attribution.Flow:
        (
            account_id,
            currency,
            amount,
            amount_base,
            amount_secondary,
            txn_type,
            is_excluded,
            is_reportable,
        ) = row
        return attribution.Flow(
            position_key=str(account_id),
            currency=str(currency).strip(),
            native_amount=amount,
            frozen_amount=amount_base if leg == "base" else amount_secondary,
            is_reportable=bool(is_reportable),
            is_excluded=bool(is_excluded),
            txn_type=txn_type,
        )

    def funding_from_row(row: tuple[Any, ...]) -> attribution.HoldingFunding:
        account_id, holding_id, currency, net_amount, net_amount_base, net_amount_secondary = row
        return attribution.HoldingFunding(
            position_key=_position_key(account_id, holding_id),
            currency=str(currency).strip(),
            native_net=net_amount,
            frozen_net=net_amount_base if leg == "base" else net_amount_secondary,
        )

    flows = [flow_from_row(row) for row in flow_rows]
    funding = [funding_from_row(row) for row in funding_rows]
    return attribution.attribute(
        positions=positions,
        flows=flows,
        holding_funding=funding,
        closing_rates=closing_rates,
        unavailable_reasons=unavailable_reasons,
    )


@router.get("/wealth-history/{monthEnd}/attribution")
def read_month_attribution(
    monthEnd: date, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> MonthAttributionResponse:
    t1 = monthEnd
    t0 = date(t1.year, t1.month, 1) - timedelta(days=1)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        base, secondary = _household_pair(cur, owner.household_id)

        opening_rows = _load_positions(cur, owner.household_id, t0)
        closing_rows = _load_positions(cur, owner.household_id, t1)
        reasons = [
            f"{row.account_name} is {row.status} at {t0}"
            for row in opening_rows
            if row.status != "available"
        ] + [
            f"{row.account_name} is {row.status} at {t1}"
            for row in closing_rows
            if row.status != "available"
        ]

        ledger_account_ids = sorted(
            {
                row.account_id
                for row in (*opening_rows, *closing_rows)
                if row.valuation_mode == "ledger_balance"
            },
            key=str,
        )
        holding_ids = sorted(
            {
                row.holding_id
                for row in (*opening_rows, *closing_rows)
                if row.valuation_mode == "holdings" and row.holding_id is not None
            },
            key=str,
        )
        flow_rows: list[tuple[Any, ...]] = []
        funding_rows: list[tuple[Any, ...]] = []
        if not reasons:
            if ledger_account_ids:
                cur.execute(_WINDOW_FLOWS, (owner.household_id, ledger_account_ids, t0, t1))
                flow_rows = cur.fetchall()
            if holding_ids:
                cur.execute(_WINDOW_FUNDING, (owner.household_id, holding_ids, t0, t1))
                funding_rows = cur.fetchall()

            needed_currencies = (
                {row.currency for row in (*opening_rows, *closing_rows)}
                | {str(row[1]).strip() for row in flow_rows}
                | {str(row[2]).strip() for row in funding_rows}
            )
            base_rates: dict[str, Decimal] = {}
            secondary_rates: dict[str, Decimal] = {}
            for currency in needed_currencies:
                pair = current_rate_pair(cur, currency, base, secondary, t1)
                if pair.to_base is not None:
                    base_rates[currency] = pair.to_base.rate
                if pair.to_secondary is not None:
                    secondary_rates[currency] = pair.to_secondary.rate
        else:
            base_rates = secondary_rates = {}

    base_leg = _attribute_leg(
        opening_rows, closing_rows, flow_rows, funding_rows, base_rates, reasons, leg="base"
    )
    secondary_leg = _attribute_leg(
        opening_rows,
        closing_rows,
        flow_rows,
        funding_rows,
        secondary_rates,
        reasons,
        leg="secondary",
    )
    return MonthAttributionResponse(
        monthEnd=monthEnd,
        baseCurrency=base,
        secondaryCurrency=secondary,
        base=_to_leg(base_leg),
        secondary=_to_leg(secondary_leg),
    )
