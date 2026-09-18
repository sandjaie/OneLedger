# api/oneledger/routes/spending.py
"""Phase 4.1 Task 1: `GET /spending` and `GET /spending/export.csv` — spending readable by
category and bucket, not only as one monthly total (spec "Outcome").

Spending is `-sum(amount_base)` over `v_transaction_reportable` where `txn_type <> 'income'`,
matching `routes/dashboard.py::FLOWS` — this phase must not invent a second rule, so a refund
lowers spending rather than counting as income. A category's effective bucket and active flag
merge a household's `category_override` over the seed row, via the shared
`categories_sql.EFFECTIVE_CATEGORY` fragment. Both routes open `SET TRANSACTION READ ONLY`: a
GET never writes and never recomputes.
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..categories_sql import EFFECTIVE_CATEGORY
from ..db import connect
from ..money import chart_coordinates, chart_ratios
from ..resources import household_currencies

router = APIRouter(tags=["spending"])

_MAX_RANGE_DAYS = 400
_EXPORT_ROW_CAP = 10_000
_UNCATEGORISED = "Uncategorised"


class CategorySpending(BaseModel):
    categoryId: UUID | None
    categoryName: str
    parentCategoryId: UUID | None
    parentCategoryName: str | None
    bucket: str | None
    amountBase: str
    amountSecondary: str
    transactionCount: int
    previousAmountBase: str
    chartRatio: str
    chartCoordinate: int


class BucketSpending(BaseModel):
    bucket: str
    amountBase: str
    amountSecondary: str
    transactionCount: int
    previousAmountBase: str
    chartRatio: str
    chartCoordinate: int


class SpendingResponse(BaseModel):
    byCategory: list[CategorySpending]
    byBucket: list[BucketSpending]
    totalBase: str
    totalSecondary: str
    previousTotalBase: str
    # Python cannot name a field `from`; the wire field is aliased for output only — this route
    # only ever constructs the model, never parses one, so no validation alias is needed.
    from_: date = Field(serialization_alias="from")
    to: date
    baseCurrency: str
    secondaryCurrency: str


def _validate_range(from_: date, to: date) -> None:
    if from_ > to:
        raise HTTPException(status_code=422, detail="from must not be after to")
    if to > date.today():
        raise HTTPException(status_code=422, detail="to must not be in the future")
    if (to - from_).days + 1 > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422, detail=f"the range must be at most {_MAX_RANGE_DAYS} days"
        )


def _previous_range(from_: date, to: date) -> tuple[date, date]:
    """The immediately preceding range of equal length, for the `previous…` comparison
    figures — e.g. 1..31 Mar is compared against 1..28 Feb (with 2026 not a leap year), each 28
    days when Feb is the shorter month, since the length is counted in days, not months."""
    length = (to - from_).days + 1
    previous_to = from_ - timedelta(days=1)
    previous_from = previous_to - timedelta(days=length - 1)
    return previous_from, previous_to


# `is_current` splits the two ranges in one pass so the previous period's own amount rides along
# on the same grouped row as the current one — a category or bucket with no current-period
# spending is dropped by the HAVING clause below regardless of what the previous period shows.
_BY_CATEGORY = f"""
    WITH scoped AS (
        SELECT t.category_id AS category_id, c.name AS category_name,
               c.parent_id AS parent_category_id, p.name AS parent_category_name,
               COALESCE(o.bucket, c.bucket)::text AS bucket,
               t.amount_base, t.amount_secondary, (t.booked_date >= %(from)s) AS is_current
          FROM v_transaction_reportable t
          {EFFECTIVE_CATEGORY}
          LEFT JOIN category p ON p.id = c.parent_id
         WHERE t.household_id = %(household)s AND t.txn_type <> 'income'
           AND t.booked_date >= %(previous_from)s AND t.booked_date <= %(to)s
    )
    SELECT category_id, category_name, parent_category_id, parent_category_name, bucket,
           coalesce(-sum(amount_base) FILTER (WHERE is_current), 0.0000),
           coalesce(-sum(amount_secondary) FILTER (WHERE is_current), 0.0000),
           count(*) FILTER (WHERE is_current),
           coalesce(-sum(amount_base) FILTER (WHERE NOT is_current), 0.0000)
      FROM scoped
     GROUP BY category_id, category_name, parent_category_id, parent_category_name, bucket
    HAVING count(*) FILTER (WHERE is_current) > 0
"""

_BY_BUCKET = f"""
    WITH scoped AS (
        SELECT COALESCE(o.bucket, c.bucket)::text AS bucket,
               t.amount_base, t.amount_secondary, (t.booked_date >= %(from)s) AS is_current
          FROM v_transaction_reportable t
          {EFFECTIVE_CATEGORY}
         WHERE t.household_id = %(household)s AND t.txn_type <> 'income'
           AND t.booked_date >= %(previous_from)s AND t.booked_date <= %(to)s
           AND COALESCE(o.bucket, c.bucket) IS NOT NULL
    )
    SELECT bucket,
           coalesce(-sum(amount_base) FILTER (WHERE is_current), 0.0000),
           coalesce(-sum(amount_secondary) FILTER (WHERE is_current), 0.0000),
           count(*) FILTER (WHERE is_current),
           coalesce(-sum(amount_base) FILTER (WHERE NOT is_current), 0.0000)
      FROM scoped
     GROUP BY bucket
    HAVING count(*) FILTER (WHERE is_current) > 0
"""

_TOTAL = """
    SELECT coalesce(-sum(amount_base) FILTER (WHERE booked_date >= %(from)s), 0.0000),
           coalesce(-sum(amount_secondary) FILTER (WHERE booked_date >= %(from)s), 0.0000),
           coalesce(-sum(amount_base) FILTER (WHERE booked_date < %(from)s), 0.0000)
      FROM v_transaction_reportable
     WHERE household_id = %(household)s AND txn_type <> 'income'
       AND booked_date >= %(previous_from)s AND booked_date <= %(to)s
"""


@router.get("/spending")
def read_spending(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
) -> SpendingResponse:
    _validate_range(from_, to)
    previous_from, previous_to = _previous_range(from_, to)
    params = {
        "household": owner.household_id,
        "from": from_,
        "to": to,
        "previous_from": previous_from,
    }
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        base, secondary = household_currencies(cur, owner.household_id)

        cur.execute(_BY_CATEGORY, params)
        category_rows = [
            (
                row[0],
                _UNCATEGORISED if row[0] is None else row[1],
                row[2],
                row[3],
                row[4],
                Decimal(row[5]),
                Decimal(row[6]),
                int(row[7]),
                Decimal(row[8]),
            )
            for row in cur.fetchall()
        ]
        category_ratios = chart_ratios({row[0]: row[5] for row in category_rows})
        category_coordinates = chart_coordinates({row[0]: row[5] for row in category_rows})
        category_rows.sort(key=lambda row: (-row[5], row[1]))
        by_category = [
            CategorySpending(
                categoryId=row[0],
                categoryName=row[1],
                parentCategoryId=row[2],
                parentCategoryName=row[3],
                bucket=row[4],
                amountBase=str(row[5]),
                amountSecondary=str(row[6]),
                transactionCount=row[7],
                previousAmountBase=str(row[8]),
                chartRatio=category_ratios[row[0]],
                chartCoordinate=category_coordinates[row[0]],
            )
            for row in category_rows
        ]

        cur.execute(_BY_BUCKET, params)
        bucket_rows = [
            (row[0], Decimal(row[1]), Decimal(row[2]), int(row[3]), Decimal(row[4]))
            for row in cur.fetchall()
        ]
        bucket_ratios = chart_ratios({row[0]: row[1] for row in bucket_rows})
        bucket_coordinates = chart_coordinates({row[0]: row[1] for row in bucket_rows})
        bucket_rows.sort(key=lambda row: (-row[1], row[0]))
        by_bucket = [
            BucketSpending(
                bucket=row[0],
                amountBase=str(row[1]),
                amountSecondary=str(row[2]),
                transactionCount=row[3],
                previousAmountBase=str(row[4]),
                chartRatio=bucket_ratios[row[0]],
                chartCoordinate=bucket_coordinates[row[0]],
            )
            for row in bucket_rows
        ]

        cur.execute(_TOTAL, params)
        total = cur.fetchone()
        assert total is not None

    return SpendingResponse(
        byCategory=by_category,
        byBucket=by_bucket,
        totalBase=str(Decimal(total[0])),
        totalSecondary=str(Decimal(total[1])),
        previousTotalBase=str(Decimal(total[2])),
        from_=from_,
        to=to,
        baseCurrency=base,
        secondaryCurrency=secondary,
    )


_EXPORT_ROWS = f"""
    SELECT t.booked_date, a.name, COALESCE(t.description_clean, t.description_raw),
           c.name, COALESCE(o.bucket, c.bucket)::text, t.amount, t.currency, t.amount_base
      FROM v_transaction_reportable t
      JOIN account a ON a.household_id = t.household_id AND a.id = t.account_id
      {EFFECTIVE_CATEGORY}
     WHERE t.household_id = %(household)s AND t.txn_type <> 'income'
       AND t.booked_date >= %(from)s AND t.booked_date <= %(to)s
     ORDER BY t.booked_date, t.id
     LIMIT %(limit)s
"""


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: str) -> str:
    """A statement's own description is external text. A cell that starts with a formula prefix is
    executed by Excel and Sheets when the owner opens the file, so it is prefixed with an
    apostrophe, which those applications strip on display."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


@router.get("/spending/export.csv")
def export_spending_csv(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    from_: Annotated[date, Query(alias="from")],
    to: Annotated[date, Query()],
) -> Response:
    _validate_range(from_, to)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            _EXPORT_ROWS,
            {
                "household": owner.household_id,
                "from": from_,
                "to": to,
                "limit": _EXPORT_ROW_CAP + 1,
            },
        )
        rows: list[tuple[Any, ...]] = cur.fetchall()

    truncated = len(rows) > _EXPORT_ROW_CAP
    if truncated:
        rows = rows[:_EXPORT_ROW_CAP]

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(
        [
            "booked_date",
            "account",
            "description",
            "category",
            "bucket",
            "amount",
            "currency",
            "amount_base",
        ]
    )
    for booked_date, account, description, category, bucket, amount, currency, amount_base in rows:
        writer.writerow(
            [
                booked_date,
                _safe_cell(str(account)),
                _safe_cell(str(description)),
                _safe_cell(str(category or "")),
                bucket or "",
                amount,
                str(currency).strip(),
                amount_base,
            ]
        )
    if truncated:
        buffer.write(f"# truncated at {_EXPORT_ROW_CAP} rows\r\n")

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="spending-{from_}-{to}.csv"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
