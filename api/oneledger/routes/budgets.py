# api/oneledger/routes/budgets.py
"""Phase 4.1 Task 2: budgets, rollover and variance (spec "Budgets are owner assumptions, not
postings", `docs/superpowers/plans/2026-09-16-phase-4-1-budgets-recurring.md` Task 2).

A budget line is an assumption in the household base currency. It posts nothing, changes no
transaction, and never becomes income or spending. It is scoped to exactly one of a category or a
bucket for one month (V55's `budget_line_scope_shape`), so a category line and a bucket line
cannot silently double-count the same row: a bucket line's actual excludes categories that carry
their own line that month, and the response states that exclusion via `excludedCategoryIds`.

`category` is a shared seed table with no `UNIQUE (household_id, id)` (V55's own comment), so
`budget_line.category_id` is a plain `REFERENCES category(id)` and the tenant boundary is this
module's job: `_check_budget_category` reuses `routes/transactions.py::_assert_category_allowed`
for the combined tenant-and-active check, after a cheap existence lookup that lets an unknown or
inactive category answer 422 while a category that exists but belongs to another household
answers 404, matching the plan's contract exactly.

Rollover is a per-line choice, computed on read and never stored (spec): when it is on, the
unspent or overspent remainder of the previous month's same line carries into this month's
available amount, recursing while the previous month's own line also has rollover enabled, at
most twelve months back (`_carry`). Every month in the chain computes its own actual with that
month's own separately budgeted category set, never the requested month's — `_line_actual` always
takes the month it is computing for, not the month the caller is reading.

`GET /budgets` opens `SET TRANSACTION READ ONLY` and writes nothing, as every other GET in this
codebase does. `PATCH /budgets/{lineId}` — never `PUT`, which `main.py`'s CORS `allow_methods`
does not carry — updates amount, rollover and note only, never the scope or month."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..categories_sql import EFFECTIVE_CATEGORY
from ..db import IDEMPOTENCY_REUSED, connect
from ..money import _decimal_eq
from ..resources import household_currencies
from ..text import normalize_bounded
from .goals import _positive_money
from .transactions import _assert_category_allowed

router = APIRouter(tags=["budgets"])

Bucket = Literal["need", "want", "investment", "debt_service", "income", "ignore"]

# Carry looks back at most twelve months (spec "Budgets are owner assumptions, not postings").
_CARRY_BOUND = 12


def _is_month_start(value: date) -> bool:
    return value.day == 1


def _previous_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def _month_range(month: date) -> tuple[date, date]:
    last_day = calendar.monthrange(month.year, month.month)[1]
    return month, date(month.year, month.month, last_day)


def _note(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return normalize_bounded(value, "note", 200)


class BudgetLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotencyKey: UUID
    periodMonth: date
    categoryId: UUID | None = None
    bucket: Bucket | None = None
    amountBase: str
    rolloverEnabled: bool = False
    note: str | None = None

    @field_validator("periodMonth")
    @classmethod
    def _period_month(cls, value: date) -> date:
        if not _is_month_start(value):
            raise ValueError("periodMonth must be the first day of the month")
        return value

    @field_validator("amountBase")
    @classmethod
    def _amount(cls, value: str) -> str:
        return _positive_money(value)

    @field_validator("note")
    @classmethod
    def _validate_note(cls, value: str | None) -> str | None:
        return _note(value)

    @model_validator(mode="after")
    def _scope(self) -> BudgetLineCreate:
        if (self.categoryId is not None) == (self.bucket is not None):
            raise ValueError("exactly one of categoryId or bucket is required")
        return self


class BudgetLineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amountBase: str
    rolloverEnabled: bool
    note: str | None = None

    @field_validator("amountBase")
    @classmethod
    def _amount(cls, value: str) -> str:
        return _positive_money(value)

    @field_validator("note")
    @classmethod
    def _validate_note(cls, value: str | None) -> str | None:
        return _note(value)


class BudgetCopyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fromMonth: date
    toMonth: date
    idempotencyKey: UUID

    @field_validator("fromMonth", "toMonth")
    @classmethod
    def _months(cls, value: date) -> date:
        if not _is_month_start(value):
            raise ValueError("fromMonth and toMonth must be the first day of the month")
        return value


class BudgetLineRow(BaseModel):
    id: UUID
    categoryId: UUID | None
    categoryName: str | None
    bucket: str | None
    amountBase: str
    currency: str
    rolloverEnabled: bool
    note: str | None
    actualBase: str
    carriedBase: str
    availableBase: str
    varianceBase: str
    carryMonths: int
    carryStoppedReason: str
    excludedCategoryIds: list[UUID]


class BudgetTotals(BaseModel):
    amountBase: str
    actualBase: str
    carriedBase: str
    availableBase: str
    varianceBase: str


class BudgetsResponse(BaseModel):
    lines: list[BudgetLineRow]
    totals: BudgetTotals
    # Python cannot name a field `from`; the wire field is aliased for output only, matching
    # `routes/spending.py::SpendingResponse` — this route only ever constructs the model.
    from_: date = Field(serialization_alias="from")
    to: date
    month: date


class BudgetCopyResponse(BaseModel):
    created: list[BudgetLineRow]
    skippedCount: int


def _translate_budget_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_budget_line_idempotency":
        return HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
    if constraint in ("uq_budget_line_month_category", "uq_budget_line_month_bucket"):
        return HTTPException(
            status_code=409, detail="a budget line already exists for this scope and month"
        )
    if constraint == "budget_line_scope_shape":
        return HTTPException(
            status_code=422, detail="exactly one of a category or a bucket is required"
        )
    if constraint == "budget_line_period_month_is_first_day":
        return HTTPException(
            status_code=422, detail="periodMonth must be the first day of the month"
        )
    if constraint == "budget_line_amount_is_positive":
        return HTTPException(status_code=422, detail="amount must be positive")
    if constraint == "budget_line_note_is_bounded":
        return HTTPException(status_code=422, detail="note must be at most 200 characters")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="budget line violates its required field matrix"
        )
    raise exc


def _check_budget_category(
    cur: psycopg.Cursor[Any], category_id: UUID | None, household_id: UUID
) -> None:
    """The tenant boundary a plain `REFERENCES category(id)` cannot carry: an id that names no
    row is 422 (unknown), an id whose category belongs to a different household is 404 (foreign),
    and `_assert_category_allowed` — the same predicate `routes/rules.py` and
    `routes/payslips.py` already reuse — then enforces the active flag (merged over any household
    override) as a second 422, so a deactivated category cannot be budgeted either."""
    if category_id is None:
        return
    cur.execute("SELECT household_id FROM category WHERE id = %s", (category_id,))
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=422, detail="unknown category")
    category_household = found[0]
    if category_household is not None and UUID(str(category_household)) != household_id:
        raise HTTPException(status_code=404, detail="category not found")
    _assert_category_allowed(cur, category_id, household_id)


_CATEGORY_ACTUAL = """
    SELECT coalesce(-sum(amount_base), 0.0000)
      FROM v_transaction_reportable
     WHERE household_id = %s AND txn_type <> 'income'
       AND booked_date >= %s AND booked_date <= %s AND category_id = %s
"""

_BUCKET_ACTUAL = f"""
    SELECT coalesce(-sum(t.amount_base), 0.0000)
      FROM v_transaction_reportable t
      {EFFECTIVE_CATEGORY}
     WHERE t.household_id = %s AND t.txn_type <> 'income'
       AND t.booked_date >= %s AND t.booked_date <= %s
       AND COALESCE(o.bucket, c.bucket)::text = %s
       AND (t.category_id IS NULL OR NOT (t.category_id = ANY(%s)))
"""


def _category_actual(
    cur: psycopg.Cursor[Any], household_id: UUID, month: date, category_id: UUID
) -> Decimal:
    start, end = _month_range(month)
    cur.execute(_CATEGORY_ACTUAL, (household_id, start, end, category_id))
    row = cur.fetchone()
    assert row is not None
    return Decimal(row[0])


def _bucket_actual(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    month: date,
    bucket: str,
    excluded_category_ids: list[UUID],
) -> Decimal:
    start, end = _month_range(month)
    cur.execute(_BUCKET_ACTUAL, (household_id, start, end, bucket, excluded_category_ids))
    row = cur.fetchone()
    assert row is not None
    return Decimal(row[0])


def _month_category_ids(cur: psycopg.Cursor[Any], household_id: UUID, month: date) -> list[UUID]:
    """Every category with its own line in `month` — a bucket line's actual excludes them
    (spec), and each historical month in a rollover chain computes this set for itself."""
    cur.execute(
        """SELECT category_id FROM budget_line
            WHERE household_id = %s AND period_month = %s AND category_id IS NOT NULL""",
        (household_id, month),
    )
    return [UUID(str(row[0])) for row in cur.fetchall()]


def _line_actual(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    month: date,
    category_id: UUID | None,
    bucket: str | None,
    excluded_category_ids: list[UUID],
) -> Decimal:
    if category_id is not None:
        return _category_actual(cur, household_id, month, category_id)
    assert bucket is not None
    return _bucket_actual(cur, household_id, month, bucket, excluded_category_ids)


def _line_for_scope(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    month: date,
    category_id: UUID | None,
    bucket: str | None,
) -> tuple[Decimal, bool] | None:
    if category_id is not None:
        cur.execute(
            """SELECT amount_base, rollover_enabled FROM budget_line
                WHERE household_id = %s AND period_month = %s AND category_id = %s""",
            (household_id, month, category_id),
        )
    else:
        cur.execute(
            """SELECT amount_base, rollover_enabled FROM budget_line
                WHERE household_id = %s AND period_month = %s AND bucket = %s""",
            (household_id, month, bucket),
        )
    row = cur.fetchone()
    if row is None:
        return None
    return Decimal(row[0]), bool(row[1])


def _carry(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    month: date,
    category_id: UUID | None,
    bucket: str | None,
    rollover_enabled: bool,
) -> tuple[Decimal, int, str]:
    """`carriedBase`, `carryMonths` and `carryStoppedReason` for a line scoped to `category_id`
    or `bucket` at `month`. Walks backward at most `_CARRY_BOUND` months, stopping the first time
    a month has no line for this scope (`no_prior_line`), a month's own line has rollover
    disabled (`rollover_off`, its own contribution still counted), or the bound is reached while
    every month so far kept rolling (`bound_reached`). Each visited month computes its own actual
    against its own budgeted category set (`_month_category_ids`), never the requested month's."""
    if not rollover_enabled:
        return Decimal("0.0000"), 0, "rollover_off"

    chain: list[tuple[Decimal, Decimal]] = []
    reason = "no_prior_line"
    cursor_month = month
    for _ in range(_CARRY_BOUND):
        prev_month = _previous_month(cursor_month)
        found = _line_for_scope(cur, household_id, prev_month, category_id, bucket)
        if found is None:
            reason = "no_prior_line"
            break
        prev_amount, prev_rollover = found
        excluded = (
            [] if category_id is not None else _month_category_ids(cur, household_id, prev_month)
        )
        prev_actual = _line_actual(cur, household_id, prev_month, category_id, bucket, excluded)
        chain.append((prev_amount, prev_actual))
        if not prev_rollover:
            reason = "rollover_off"
            break
        cursor_month = prev_month
    else:
        reason = "bound_reached"

    carried = Decimal("0.0000")
    for amount, actual in reversed(chain):
        carried = (amount + carried) - actual
    return carried, len(chain), reason


def _budget_line_row(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    line_id: UUID,
    month: date,
    month_excluded_category_ids: list[UUID],
) -> BudgetLineRow:
    cur.execute(
        """SELECT bl.category_id, c.name, bl.bucket::text, bl.amount_base, bl.currency,
                  bl.rollover_enabled, bl.note
             FROM budget_line bl
             LEFT JOIN category c ON c.id = bl.category_id
            WHERE bl.household_id = %s AND bl.id = %s""",
        (household_id, line_id),
    )
    row = cur.fetchone()
    assert row is not None
    raw_category_id, category_name, bucket, raw_amount, currency, rollover_enabled, note = row
    category_id = None if raw_category_id is None else UUID(str(raw_category_id))
    amount_base = Decimal(raw_amount)

    excluded = month_excluded_category_ids if category_id is None else []
    actual = _line_actual(cur, household_id, month, category_id, bucket, excluded)
    carried, carry_months, carry_reason = _carry(
        cur, household_id, month, category_id, bucket, rollover_enabled
    )
    available = amount_base + carried
    variance = available - actual

    return BudgetLineRow(
        id=line_id,
        categoryId=category_id,
        categoryName=category_name,
        bucket=bucket,
        amountBase=str(amount_base),
        currency=str(currency).strip(),
        rolloverEnabled=rollover_enabled,
        note=note,
        actualBase=str(actual),
        carriedBase=str(carried),
        availableBase=str(available),
        varianceBase=str(variance),
        carryMonths=carry_months,
        carryStoppedReason=carry_reason,
        excludedCategoryIds=excluded,
    )


def _sum_field(lines: list[BudgetLineRow], field: str) -> str:
    return str(sum((Decimal(getattr(line, field)) for line in lines), Decimal("0.0000")))


@router.get("/budgets", response_model=BudgetsResponse)
def read_budgets(
    owner: Annotated[OwnerSession, Depends(require_owner)], month: Annotated[date, Query()]
) -> BudgetsResponse:
    if not _is_month_start(month):
        raise HTTPException(status_code=422, detail="month must be the first day of the month")
    start, end = _month_range(month)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            """SELECT id FROM budget_line WHERE household_id = %s AND period_month = %s
                ORDER BY category_id NULLS LAST, bucket""",
            (owner.household_id, month),
        )
        line_ids = [UUID(str(r[0])) for r in cur.fetchall()]
        excluded = _month_category_ids(cur, owner.household_id, month)
        lines = [
            _budget_line_row(cur, owner.household_id, line_id, month, excluded)
            for line_id in line_ids
        ]

    totals = BudgetTotals(
        amountBase=_sum_field(lines, "amountBase"),
        actualBase=_sum_field(lines, "actualBase"),
        carriedBase=_sum_field(lines, "carriedBase"),
        availableBase=_sum_field(lines, "availableBase"),
        varianceBase=_sum_field(lines, "varianceBase"),
    )
    return BudgetsResponse(lines=lines, totals=totals, from_=start, to=end, month=month)


@router.post("/budgets", response_model=BudgetLineRow)
def create_budget_line(
    body: BudgetLineCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> BudgetLineRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            base, _secondary = household_currencies(cur, owner.household_id)
            cur.execute(
                """SELECT id, period_month, category_id, bucket, amount_base, rollover_enabled,
                          note
                     FROM budget_line WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                eid, emonth, ecategory, ebucket, eamount, erollover, enote = existing
                matches = (
                    emonth == body.periodMonth
                    and (None if ecategory is None else UUID(str(ecategory))) == body.categoryId
                    and ebucket == body.bucket
                    and _decimal_eq(Decimal(eamount), body.amountBase)
                    and bool(erollover) == body.rolloverEnabled
                    and enote == body.note
                )
                if not matches:
                    raise HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
                response.status_code = status.HTTP_200_OK
                excluded = (
                    _month_category_ids(cur, owner.household_id, emonth)
                    if ecategory is None
                    else []
                )
                return _budget_line_row(cur, owner.household_id, UUID(str(eid)), emonth, excluded)

            _check_budget_category(cur, body.categoryId, owner.household_id)
            cur.execute(
                """INSERT INTO budget_line (household_id, idempotency_key, period_month,
                       category_id, bucket, amount_base, currency, rollover_enabled, note)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.periodMonth,
                    body.categoryId,
                    body.bucket,
                    body.amountBase,
                    base,
                    body.rolloverEnabled,
                    body.note,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            excluded = (
                _month_category_ids(cur, owner.household_id, body.periodMonth)
                if body.categoryId is None
                else []
            )
            return _budget_line_row(
                cur, owner.household_id, UUID(str(created[0])), body.periodMonth, excluded
            )
    except psycopg.Error as exc:
        raise _translate_budget_error(exc) from None


@router.patch("/budgets/{lineId}", response_model=BudgetLineRow)
def update_budget_line(
    lineId: UUID, body: BudgetLineUpdate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> BudgetLineRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT period_month, category_id FROM budget_line
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, lineId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="budget line not found")
            month, raw_category_id = found
            cur.execute(
                """UPDATE budget_line SET amount_base = %s, rollover_enabled = %s, note = %s,
                          updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (body.amountBase, body.rolloverEnabled, body.note, owner.household_id, lineId),
            )
            excluded = (
                _month_category_ids(cur, owner.household_id, month)
                if raw_category_id is None
                else []
            )
            return _budget_line_row(cur, owner.household_id, lineId, month, excluded)
    except psycopg.Error as exc:
        raise _translate_budget_error(exc) from None


@router.delete("/budgets/{lineId}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget_line(
    lineId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM budget_line WHERE household_id = %s AND id = %s RETURNING id",
            (owner.household_id, lineId),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="budget line not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/budgets/copy", response_model=BudgetCopyResponse)
def copy_budgets(
    body: BudgetCopyBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> BudgetCopyResponse:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            base, _secondary = household_currencies(cur, owner.household_id)
            cur.execute(
                """SELECT category_id, bucket, amount_base, rollover_enabled, note
                     FROM budget_line WHERE household_id = %s AND period_month = %s""",
                (owner.household_id, body.fromMonth),
            )
            source_lines = cur.fetchall()
            cur.execute(
                """SELECT category_id, bucket FROM budget_line
                    WHERE household_id = %s AND period_month = %s""",
                (owner.household_id, body.toMonth),
            )
            existing_scopes = {
                (None if row[0] is None else UUID(str(row[0])), row[1]) for row in cur.fetchall()
            }

            created_ids: list[UUID] = []
            skipped = 0
            for category_id, bucket, amount_base, rollover_enabled, note in source_lines:
                scope_key = (None if category_id is None else UUID(str(category_id)), bucket)
                if scope_key in existing_scopes:
                    skipped += 1
                    continue
                # One idempotency key names the whole copy; each inserted row still needs its own
                # (household_id, idempotency_key) per V55, so a per-scope key is derived
                # deterministically from it — a replay derives the same keys, finds every scope
                # already has a line (from the first call) and skips them all, which is this
                # endpoint's own idempotent-replay behaviour without a second stored request row.
                per_line_key = uuid5(
                    body.idempotencyKey, f"{scope_key[0] or scope_key[1]}:{body.toMonth}"
                )
                cur.execute(
                    """INSERT INTO budget_line (household_id, idempotency_key, period_month,
                           category_id, bucket, amount_base, currency, rollover_enabled, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (household_id, idempotency_key) DO NOTHING
                       RETURNING id""",
                    (
                        owner.household_id,
                        per_line_key,
                        body.toMonth,
                        category_id,
                        bucket,
                        amount_base,
                        base,
                        rollover_enabled,
                        note,
                    ),
                )
                inserted = cur.fetchone()
                if inserted is None:
                    skipped += 1
                    continue
                created_ids.append(UUID(str(inserted[0])))
                existing_scopes.add(scope_key)

            excluded = _month_category_ids(cur, owner.household_id, body.toMonth)
            created = [
                _budget_line_row(cur, owner.household_id, line_id, body.toMonth, excluded)
                for line_id in created_ids
            ]
            return BudgetCopyResponse(created=created, skippedCount=skipped)
    except psycopg.Error as exc:
        raise _translate_budget_error(exc) from None
