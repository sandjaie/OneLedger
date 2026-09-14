"""Phase 2.1 Task 1: `GET /coverage` and its acknowledgements (spec "API"). Coverage is computed
on read from committed import batch periods and active payslips; `coverage_gap_acknowledgement`
(V45) is an owner assumption table like `commitment` (V43) — no SECURITY DEFINER write path, just
household-scoped RLS and explicit `household_id` filters as the authorization boundary, the same
convention as `routes/commitments.py` (`routes/plans.py`'s comment on `GET /forecast` explains why
that is enough: no DEFINER function is called here either)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict

from ..auth import OwnerSession, require_owner
from ..coverage import HISTORY_ANCHOR, account_life, month_end, month_status, months_from_anchor
from ..db import connect

router = APIRouter(tags=["coverage"])

# Spec "Account rows": the account types a bank or card statement can evidence.
STATEMENT_SOURCED_TYPES = ("current", "savings", "salary", "credit_card")

CoverageStatus = Literal[
    "outside_account_life", "reconciled", "acknowledged", "partial", "in_progress", "missing"
]
CoverageGapReason = Literal[
    "not_issued", "unobtainable", "unsupported_format", "account_not_in_use"
]
CoverageSubject = Literal["account", "payslips"]


class MonthCell(BaseModel):
    status: CoverageStatus
    reason: CoverageGapReason | None = None
    note: str | None = None


class CoverageCounts(BaseModel):
    reconciled: int = 0
    acknowledged: int = 0
    partial: int = 0
    inProgress: int = 0
    missing: int = 0
    outsideAccountLife: int = 0

    def add(self, status_value: str) -> None:
        field = "inProgress" if status_value == "in_progress" else _camel(status_value)
        setattr(self, field, getattr(self, field) + 1)


def _camel(value: str) -> str:
    head, *rest = value.split("_")
    return head + "".join(word.capitalize() for word in rest)


class CoverageRow(BaseModel):
    subject: CoverageSubject
    accountId: UUID | None
    accountName: str | None
    lifeStart: date | None
    openingBalanceDate: date | None
    anchorAfterHistory: bool | None
    months: list[MonthCell]
    counts: CoverageCounts


class CoverageResponse(BaseModel):
    historyAnchor: date
    months: list[date]
    rows: list[CoverageRow]


class AcknowledgementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: CoverageSubject
    accountId: UUID | None = None
    month: date
    reason: CoverageGapReason
    note: str | None = None


class AcknowledgementRow(BaseModel):
    subject: CoverageSubject
    accountId: UUID | None
    month: date
    reason: CoverageGapReason
    note: str | None


def _row_cells(
    months: list[date],
    life_start: date,
    life_end: date,
    periods: list[tuple[date, date]],
    acknowledged: dict[date, tuple[str, str | None]],
    as_of: date,
) -> tuple[list[MonthCell], CoverageCounts]:
    counts = CoverageCounts()
    cells = []
    for month in months:
        status_value = cast(
            CoverageStatus,
            month_status(month, life_start, life_end, periods, month in acknowledged, as_of),
        )
        counts.add(status_value)
        reason, note = acknowledged.get(month, (None, None))
        cells.append(
            MonthCell(
                status=status_value,
                reason=cast(CoverageGapReason, reason) if status_value == "acknowledged" else None,
                note=note if status_value == "acknowledged" else None,
            )
        )
    return cells, counts


def coverage_rows(cur: psycopg.Cursor[Any], household_id: UUID, as_of: date) -> list[CoverageRow]:
    """Every statement-sourced account's row plus the payslip row. Shared with `routes/doctor.py`'s
    Coverage section so its counts and earliest-gap month come from the same computation `GET
    /coverage` shows, not a second copy."""
    months = months_from_anchor(as_of)
    rows: list[CoverageRow] = []

    cur.execute(
        """SELECT id, name, opened_on, closed_on, opening_balance_date
             FROM account
            WHERE household_id = %s AND account_type::text = ANY(%s)
            ORDER BY account_class, country, display_order, name""",
        (household_id, list(STATEMENT_SOURCED_TYPES)),
    )
    accounts = cur.fetchall()
    account_ids = [UUID(str(a[0])) for a in accounts]

    periods_by_account: dict[UUID, list[tuple[date, date]]] = {aid: [] for aid in account_ids}
    if account_ids:
        cur.execute(
            """SELECT account_id, period_start, period_end FROM import_batch
                WHERE household_id = %s AND status = 'completed'
                  AND period_start IS NOT NULL AND period_end IS NOT NULL
                  AND account_id = ANY(%s)""",
            (household_id, account_ids),
        )
        for account_id, period_start, period_end in cur.fetchall():
            periods_by_account[UUID(str(account_id))].append((period_start, period_end))

    ack_by_account: dict[UUID, dict[date, tuple[str, str | None]]] = {
        aid: {} for aid in account_ids
    }
    cur.execute(
        """SELECT account_id, month, reason::text, note FROM coverage_gap_acknowledgement
            WHERE household_id = %s AND subject = 'account'""",
        (household_id,),
    )
    for account_id, month, reason, note in cur.fetchall():
        aid = UUID(str(account_id))
        if aid in ack_by_account:
            ack_by_account[aid][month] = (reason, note)

    for account_id, name, opened_on, closed_on, opening_balance_date in accounts:
        aid = UUID(str(account_id))
        life_start, life_end = account_life(opened_on, closed_on, as_of)
        cells, counts = _row_cells(
            months, life_start, life_end, periods_by_account[aid], ack_by_account[aid], as_of
        )
        rows.append(
            CoverageRow(
                subject="account",
                accountId=aid,
                accountName=name,
                lifeStart=life_start,
                openingBalanceDate=opening_balance_date,
                anchorAfterHistory=opening_balance_date > HISTORY_ANCHOR,
                months=cells,
                counts=counts,
            )
        )

    rows.append(_payslip_row(cur, household_id, months, as_of))
    return rows


def _payslip_row(
    cur: psycopg.Cursor[Any], household_id: UUID, months: list[date], as_of: date
) -> CoverageRow:
    cur.execute(
        """SELECT p.pay_month FROM payslip p
            WHERE p.household_id = %s
              AND NOT EXISTS (SELECT FROM payslip s WHERE s.supersedes_payslip_id = p.id)""",
        (household_id,),
    )
    periods = [(pay_month, month_end(pay_month)) for (pay_month,) in cur.fetchall()]

    ack: dict[date, tuple[str, str | None]] = {}
    cur.execute(
        """SELECT month, reason::text, note FROM coverage_gap_acknowledgement
            WHERE household_id = %s AND subject = 'payslips'""",
        (household_id,),
    )
    for month, reason, note in cur.fetchall():
        ack[month] = (reason, note)

    cells, counts = _row_cells(months, HISTORY_ANCHOR, as_of, periods, ack, as_of)
    return CoverageRow(
        subject="payslips",
        accountId=None,
        accountName=None,
        lifeStart=None,
        openingBalanceDate=None,
        anchorAfterHistory=None,
        months=cells,
        counts=counts,
    )


@router.get("/coverage", response_model=CoverageResponse)
def read_coverage(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> CoverageResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        rows = coverage_rows(cur, owner.household_id, as_of)
    return CoverageResponse(
        historyAnchor=HISTORY_ANCHOR, months=months_from_anchor(as_of), rows=rows
    )


def _assert_month_in_grid(month: date) -> None:
    today = date.today()
    if month != month.replace(day=1):
        raise HTTPException(status_code=422, detail="month must be the first day of a month")
    if month < HISTORY_ANCHOR or (month.year, month.month) > (today.year, today.month):
        raise HTTPException(status_code=422, detail="month is outside the coverage grid")


def _account_life_or_404(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID, month: date, today: date
) -> tuple[date, date]:
    # Same STATEMENT_SOURCED_TYPES filter as `coverage_rows`: an account outside the grid (an
    # investment account, say) has no coverage row to acknowledge a gap on, so it is 404 here
    # exactly as a foreign account is, not a row `GET /coverage` never shows.
    cur.execute(
        """SELECT opened_on, closed_on FROM account
            WHERE household_id = %s AND id = %s AND account_type::text = ANY(%s)""",
        (household_id, account_id, list(STATEMENT_SOURCED_TYPES)),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="account not found")
    life_start, life_end = account_life(found[0], found[1], today)
    if month_end(month) < life_start or month > life_end:
        raise HTTPException(status_code=422, detail="month is outside the account's life")
    return life_start, life_end


def _current_status(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    subject: CoverageSubject,
    account_id: UUID | None,
    month: date,
    life_start: date,
    life_end: date,
    today: date,
) -> str:
    if subject == "account":
        cur.execute(
            """SELECT period_start, period_end FROM import_batch
                WHERE household_id = %s AND account_id = %s AND status = 'completed'
                  AND period_start IS NOT NULL AND period_end IS NOT NULL""",
            (household_id, account_id),
        )
        periods = [(row[0], row[1]) for row in cur.fetchall()]
    else:
        cur.execute(
            """SELECT 1 FROM payslip p
                WHERE p.household_id = %s AND p.pay_month = %s
                  AND NOT EXISTS (SELECT FROM payslip s WHERE s.supersedes_payslip_id = p.id)""",
            (household_id, month),
        )
        periods = [(month, month_end(month))] if cur.fetchone() else []
    return month_status(month, life_start, life_end, periods, False, today)


@router.put("/coverage/acknowledgements", response_model=AcknowledgementRow)
def upsert_acknowledgement(
    body: AcknowledgementIn, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> AcknowledgementRow:
    if body.subject == "account" and body.accountId is None:
        raise HTTPException(status_code=422, detail="an account acknowledgement needs accountId")
    if body.subject == "payslips" and body.accountId is not None:
        raise HTTPException(
            status_code=422, detail="a payslips acknowledgement must not carry accountId"
        )
    _assert_month_in_grid(body.month)
    today = date.today()
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            if body.subject == "account":
                assert body.accountId is not None
                life_start, life_end = _account_life_or_404(
                    cur, owner.household_id, body.accountId, body.month, today
                )
            else:
                life_start, life_end = HISTORY_ANCHOR, today
            current = _current_status(
                cur,
                owner.household_id,
                body.subject,
                body.accountId,
                body.month,
                life_start,
                life_end,
                today,
            )
            if current == "reconciled":
                raise HTTPException(status_code=409, detail="this month is already covered")
            cur.execute(
                """INSERT INTO coverage_gap_acknowledgement
                       (household_id, subject, account_id, month, reason, note)
                   VALUES (%s, %s, %s, %s, %s::coverage_gap_reason, %s)
                   ON CONFLICT (household_id, subject, account_id, month)
                   DO UPDATE SET reason = EXCLUDED.reason, note = EXCLUDED.note, updated_at = now()
                   RETURNING subject, account_id, month, reason::text, note""",
                (
                    owner.household_id,
                    body.subject,
                    body.accountId,
                    body.month,
                    body.reason,
                    body.note,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            subject, account_id, month, reason, note = created
            return AcknowledgementRow(
                subject=subject,
                accountId=None if account_id is None else UUID(str(account_id)),
                month=month,
                reason=reason,
                note=note,
            )
    except psycopg.errors.CheckViolation as exc:
        raise HTTPException(
            status_code=422, detail="acknowledgement violates its field matrix"
        ) from exc


@router.delete("/coverage/acknowledgements", status_code=status.HTTP_204_NO_CONTENT)
def delete_acknowledgement(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    subject: Annotated[CoverageSubject, Query()],
    month: Annotated[date, Query()],
    accountId: Annotated[UUID | None, Query()] = None,
) -> Response:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            """DELETE FROM coverage_gap_acknowledgement
                WHERE household_id = %s AND subject = %s
                  AND account_id IS NOT DISTINCT FROM %s AND month = %s""",
            (owner.household_id, subject, accountId, month),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
