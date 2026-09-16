"""Phase 3.2 Task 2: a monthly review the owner walks and marks complete (spec "Monthly review
API"). Every step is computed on read from existing tables and views — nothing here duplicates a
scorer or a totals query; `transfers.candidates_for_window` and `wealth_history.month_totals` are
the same extracted helpers `GET /transfers/candidates` and `GET /wealth-history` use.
`monthly_review` (V50) holds only the one fact nothing else does: that the owner marked the month
reviewed, and when. No `SECURITY DEFINER` write path — the household row is locked before every
write, the same convention as `routes/commitments.py` and `routes/actions.py`."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..coverage import month_end, months_from_anchor, previous_month
from ..db import connect
from ..money import _out
from ..resources import household_currencies
from .actions import action_list
from .coverage import coverage_rows
from .transfers import candidates_for_window
from .wealth_history import month_totals

router = APIRouter(tags=["monthly-review"])

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_BAD_MONTH = "month must be YYYY-MM"
_FUTURE_MONTH = "month must not be after the current month"


class CoverageStep(BaseModel):
    gapCells: int
    rowsWithGaps: int


class TransfersStep(BaseModel):
    candidateCount: int


class ClassificationStep(BaseModel):
    unclassifiedCount: int


class PositionStep(BaseModel):
    monthEnd: date
    totalBase: str | None
    complete: bool
    previousMonthEnd: date
    previousTotalBase: str | None
    previousComplete: bool
    # The server's own totalBase - previousTotalBase, only when both months are complete; the
    # browser computes no difference (spec "position").
    changeBase: str | None
    baseCurrency: str


class CommitmentsStep(BaseModel):
    dueNext30Days: int
    overdue: int


class ActionsStep(BaseModel):
    open: int
    snoozed: int


class MonthlyReviewSteps(BaseModel):
    coverage: CoverageStep
    transfers: TransfersStep
    classification: ClassificationStep
    position: PositionStep
    commitments: CommitmentsStep
    actions: ActionsStep


class MonthlyReviewResponse(BaseModel):
    month: date
    completedAt: datetime | None
    steps: MonthlyReviewSteps


def _parse_month(value: str) -> date:
    if not _MONTH_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail=_BAD_MONTH)
    year_text, month_text = value.split("-")
    try:
        return date(int(year_text), int(month_text), 1)
    except ValueError:
        raise HTTPException(status_code=422, detail=_BAD_MONTH) from None


def _assert_not_future(review_month: date, today: date) -> None:
    if (review_month.year, review_month.month) > (today.year, today.month):
        raise HTTPException(status_code=422, detail=_FUTURE_MONTH)


def _coverage_step(
    cur: psycopg.Cursor[Any], household_id: UUID, review_month: date, as_of: date
) -> CoverageStep:
    """Missing/partial cells for `review_month` alone, across every `coverage_rows()` row —
    unlike the forecast's three-month `coverageGapMonths`, this is one month, so `gapCells` and
    `rowsWithGaps` count the same rows (one cell per row)."""
    months = months_from_anchor(as_of)
    if review_month not in months:
        return CoverageStep(gapCells=0, rowsWithGaps=0)
    index = months.index(review_month)
    rows = coverage_rows(cur, household_id, as_of)
    gaps = sum(1 for row in rows if row.months[index].status in ("missing", "partial"))
    return CoverageStep(gapCells=gaps, rowsWithGaps=gaps)


def _classification_step(
    cur: psycopg.Cursor[Any], household_id: UUID, month_start: date, month_finish: date
) -> ClassificationStep:
    cur.execute(
        """SELECT count(*) FROM v_transaction_reportable
            WHERE household_id = %s AND booked_date BETWEEN %s AND %s
              AND classified_by = 'unclassified'""",
        (household_id, month_start, month_finish),
    )
    found = cur.fetchone()
    return ClassificationStep(unclassifiedCount=int(found[0]) if found else 0)


def _position_step(
    cur: psycopg.Cursor[Any], household_id: UUID, review_month: date
) -> PositionStep:
    base, _secondary = household_currencies(cur, household_id)
    this_end = month_end(review_month)
    previous_start = previous_month(review_month)
    previous_end = month_end(previous_start)
    totals = month_totals(cur, household_id, [this_end, previous_end])
    total_base, _total_secondary, complete = totals.get(this_end, (None, None, False))
    previous_total_base, _prev_secondary, previous_complete = totals.get(
        previous_end, (None, None, False)
    )
    change_base = None
    if complete and previous_complete:
        # `complete` is true only when `month_totals` found rows to aggregate, so its total is
        # never actually null (the column type stays nullable for the incomplete case only).
        assert total_base is not None and previous_total_base is not None
        change_base = str(total_base - previous_total_base)
    return PositionStep(
        monthEnd=this_end,
        totalBase=_out(total_base) if complete else None,
        complete=complete,
        previousMonthEnd=previous_end,
        previousTotalBase=_out(previous_total_base) if previous_complete else None,
        previousComplete=previous_complete,
        changeBase=change_base,
        baseCurrency=base,
    )


def _commitments_step(cur: psycopg.Cursor[Any], household_id: UUID, today: date) -> CommitmentsStep:
    cur.execute(
        """SELECT count(*) FILTER (WHERE next_due_date BETWEEN %s AND %s),
                  count(*) FILTER (WHERE next_due_date < %s)
             FROM commitment
            WHERE household_id = %s AND archived_at IS NULL AND next_due_date IS NOT NULL""",
        (today, today + timedelta(days=30), today, household_id),
    )
    row = cur.fetchone()
    assert row is not None
    return CommitmentsStep(dueNext30Days=int(row[0]), overdue=int(row[1]))


def _actions_step(cur: psycopg.Cursor[Any], household_id: UUID, today: date) -> ActionsStep:
    rows = action_list(cur, household_id, today, apply_marks=True)
    open_count = sum(1 for r in rows if r.snoozedUntil is None or r.snoozedUntil <= today)
    snoozed_count = sum(1 for r in rows if r.snoozedUntil is not None and r.snoozedUntil > today)
    return ActionsStep(open=open_count, snoozed=snoozed_count)


def _steps(cur: psycopg.Cursor[Any], household_id: UUID, review_month: date) -> MonthlyReviewSteps:
    today = date.today()
    as_of = min(month_end(review_month), today)
    month_finish = month_end(review_month)
    return MonthlyReviewSteps(
        coverage=_coverage_step(cur, household_id, review_month, as_of),
        transfers=TransfersStep(
            candidateCount=len(candidates_for_window(cur, household_id, review_month, month_finish))
        ),
        classification=_classification_step(cur, household_id, review_month, month_finish),
        position=_position_step(cur, household_id, review_month),
        commitments=_commitments_step(cur, household_id, today),
        actions=_actions_step(cur, household_id, today),
    )


def _response(
    cur: psycopg.Cursor[Any], household_id: UUID, review_month: date, completed_at: datetime | None
) -> MonthlyReviewResponse:
    return MonthlyReviewResponse(
        month=review_month, completedAt=completed_at, steps=_steps(cur, household_id, review_month)
    )


def _read_completed_at(
    cur: psycopg.Cursor[Any], household_id: UUID, review_month: date
) -> datetime | None:
    cur.execute(
        "SELECT completed_at FROM monthly_review WHERE household_id = %s AND review_month = %s",
        (household_id, review_month),
    )
    found = cur.fetchone()
    return found[0] if found else None


@router.get("/monthly-review")
def read_monthly_review(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    month: Annotated[str | None, Query()] = None,
) -> MonthlyReviewResponse:
    """`?month=YYYY-MM`, default the calendar month before today; a month after the current month
    is 422. Read-only: `SET TRANSACTION READ ONLY` before any other statement."""
    today = date.today()
    review_month = _parse_month(month) if month is not None else previous_month(today)
    _assert_not_future(review_month, today)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        completed_at = _read_completed_at(cur, owner.household_id, review_month)
        return _response(cur, owner.household_id, review_month, completed_at)


@router.put("/monthly-review/{month}/completion")
def complete_monthly_review(
    month: str, response: Response, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> MonthlyReviewResponse:
    """201 the first time, 200 (keeping the original `completedAt`) on a replay; a future month
    is 422. `ON CONFLICT DO NOTHING` decides which happened without a separate existence check."""
    review_month = _parse_month(month)
    _assert_not_future(review_month, date.today())
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            """INSERT INTO monthly_review (household_id, review_month)
               VALUES (%s, %s)
               ON CONFLICT (household_id, review_month) DO NOTHING
               RETURNING completed_at""",
            (owner.household_id, review_month),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            response.status_code = status.HTTP_201_CREATED
            completed_at = inserted[0]
        else:
            response.status_code = status.HTTP_200_OK
            completed_at = _read_completed_at(cur, owner.household_id, review_month)
            assert completed_at is not None
        return _response(cur, owner.household_id, review_month, completed_at)


@router.delete("/monthly-review/{month}/completion", status_code=204)
def reopen_monthly_review(
    month: str, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    """Idempotent: 204 whether or not the month was marked complete."""
    review_month = _parse_month(month)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "DELETE FROM monthly_review WHERE household_id = %s AND review_month = %s",
            (owner.household_id, review_month),
        )
    return Response(status_code=204)
