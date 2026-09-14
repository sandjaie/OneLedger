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

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect
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
