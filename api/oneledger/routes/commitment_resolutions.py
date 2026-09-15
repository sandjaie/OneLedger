"""Phase 3.1 Task 1: settling a commitment's one open occurrence by linking a ledger posting
(in full or in part), marking it done without a posting, or dismissing it with a reason.
`commitment_resolution` (V48) is a plain owner-scoped table — the V48 trigger
`assert_commitment_payment()` decides payment eligibility, so this router maps its refusal and
the transaction-reuse conflict to HTTP rather than re-deriving eligibility itself; every write
still locks the household row first, exactly like `routes/plans.py`. Closing an occurrence
advances `commitment.next_due_date` to `forecast.next_occurrence(...)` in the same transaction as
the resolution that closed it, so a recurring commitment only ever holds its next occurrence."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..forecast import next_occurrence
from .layouts import _preview_redactor
from .plans import CommitmentRow, _row

router = APIRouter(tags=["plans"])

ResolutionKind = Literal["payment", "completed", "dismissed"]

_RESOLUTION_ALREADY = "this occurrence is already resolved"
_ONLY_LATEST_UNDOABLE = "only the latest resolution can be undone"

# Mirrors V48's (V54's CREATE OR REPLACE of) assert_commitment_payment() predicate in
# db/migrations/V54__register_commitments.sql — keep both in sync; a change to either eligibility
# rule must update the other. Placeholders, in order: account_id, currency, a signed amount whose
# sign the posting must match. The income-refusal clause is appended only when the commitment's
# source record is a claim or a deposit (spec "Register-sourced commitments (V54)") — see
# `_eligible_predicate()`.
_ELIGIBLE_POSTING_PREDICATE = """
    NOT t.is_excluded
    AND t.txn_type NOT IN ('opening_balance', 'adjustment')
    AND t.account_id = %s
    AND rtrim(t.currency) = rtrim(%s)
    AND sign(t.amount) = sign(%s)
    AND NOT EXISTS (
      SELECT FROM payslip_line_posting p
       WHERE p.household_id = t.household_id
         AND (p.income_transaction_id = t.id OR p.expense_transaction_id = t.id))
"""
_INCOME_REFUSED_CLAUSE = "AND t.txn_type <> 'income'\n"


def _eligible_predicate(income_refused: bool) -> str:
    return _ELIGIBLE_POSTING_PREDICATE + (_INCOME_REFUSED_CLAUSE if income_refused else "")


def _source_income_refused(
    cur: psycopg.Cursor[Any], household_id: UUID, source_register_record_id: object
) -> bool:
    """Whether this commitment's source record is a claim or a deposit — the one condition V54
    added to `assert_commitment_payment()`, mirrored here for the candidates read and the
    resolutions list's own eligibility check."""
    if source_register_record_id is None:
        return False
    cur.execute(
        "SELECT kind::text FROM register_record WHERE household_id = %s AND id = %s",
        (household_id, source_register_record_id),
    )
    found = cur.fetchone()
    return found is not None and found[0] in ("claim", "deposit")


_CANDIDATE_WINDOW_DAYS = 45
_CANDIDATE_LIMIT = 20


def _normalize_reason(value: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(collapsed) <= 200:
        raise ValueError("reason must be 1-200 characters")
    return collapsed


class ResolutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dueDate: date
    kind: ResolutionKind
    transactionId: UUID | None = None
    closes: bool | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> ResolutionCreate:
        if self.kind == "payment":
            if self.transactionId is None:
                raise ValueError("a payment resolution needs a transaction")
            if self.closes is None:
                raise ValueError("a payment resolution needs closes")
            if self.reason is not None:
                raise ValueError("a payment resolution must not carry a reason")
        else:
            if self.transactionId is not None:
                raise ValueError(f"a {self.kind} resolution must not carry a transaction")
            if self.closes is not None:
                raise ValueError(f"a {self.kind} resolution must not carry closes")
            if self.kind == "dismissed":
                if self.reason is None:
                    raise ValueError("a dismissed resolution needs a reason")
                self.reason = _normalize_reason(self.reason)
            elif self.reason is not None:
                raise ValueError("a completed resolution must not carry a reason")
        return self


class ResolutionTransaction(BaseModel):
    transactionId: UUID
    bookedDate: date
    amount: str
    description: str
    eligible: bool


class ResolutionRow(BaseModel):
    id: UUID
    dueDate: date
    kind: ResolutionKind
    closes: bool
    reason: str | None
    createdAt: datetime
    undoable: bool
    transaction: ResolutionTransaction | None = None


class ResolutionsResponse(BaseModel):
    resolutions: list[ResolutionRow]


class PaymentCandidate(BaseModel):
    transactionId: UUID
    bookedDate: date
    amount: str
    currency: str
    description: str


class PaymentCandidatesResponse(BaseModel):
    dueDate: date | None
    candidates: list[PaymentCandidate]


def _translate_resolution_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "commitment_payment_is_eligible":
        return HTTPException(status_code=422, detail="this posting cannot settle this commitment")
    if constraint == "uq_commitment_resolution_transaction":
        return HTTPException(status_code=409, detail="this posting already settles a commitment")
    if constraint == "uq_commitment_resolution_closing":
        return HTTPException(status_code=409, detail=_RESOLUTION_ALREADY)
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="commitment resolution violates its required field matrix"
        )
    raise exc


def _commitment_core(
    cur: psycopg.Cursor[Any], household_id: UUID, commitment_id: UUID, *, lock: bool
) -> tuple[Any, ...] | None:
    suffix = " FOR UPDATE" if lock else ""
    cur.execute(
        f"""SELECT archived_at, next_due_date, account_id, currency, amount, cadence::text,
                   first_date, end_date, source_register_record_id
             FROM commitment WHERE household_id = %s AND id = %s{suffix}""",
        (household_id, commitment_id),
    )
    return cur.fetchone()


def _latest_closed_due_date(
    cur: psycopg.Cursor[Any], household_id: UUID, commitment_id: UUID
) -> date | None:
    cur.execute(
        """SELECT max(due_date) FROM commitment_resolution
            WHERE household_id = %s AND commitment_id = %s AND closes""",
        (household_id, commitment_id),
    )
    row = cur.fetchone()
    return row[0] if row is not None else None


def _has_payment_on(
    cur: psycopg.Cursor[Any], household_id: UUID, commitment_id: UUID, due_date: date
) -> bool:
    cur.execute(
        """SELECT EXISTS (
             SELECT FROM commitment_resolution
              WHERE household_id = %s AND commitment_id = %s AND kind = 'payment'
                AND due_date = %s)""",
        (household_id, commitment_id, due_date),
    )
    row = cur.fetchone()
    return bool(row[0]) if row is not None else False


def _is_undoable(
    *,
    archived: bool,
    kind: str,
    closes: bool,
    due_date: date,
    next_due_date: date | None,
    latest_closed_due_date: date | None,
    next_occurrence_has_payment: bool,
) -> bool:
    if archived:
        return False
    if not closes:
        return kind == "payment" and due_date == next_due_date
    if due_date != latest_closed_due_date:
        return False
    if next_due_date is not None and next_occurrence_has_payment:
        return False
    return True


def _transaction_eligible(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    transaction_id: UUID,
    account_id: UUID,
    currency: str,
    amount: Decimal,
    *,
    income_refused: bool,
) -> bool:
    cur.execute(
        f"""SELECT EXISTS (
              SELECT FROM v_transaction_posting t
               WHERE t.household_id = %s AND t.id = %s AND {_eligible_predicate(income_refused)}
            )""",
        (household_id, transaction_id, account_id, currency, amount),
    )
    row = cur.fetchone()
    return bool(row[0]) if row is not None else False


@router.get(
    "/commitments/{commitment_id}/payment-candidates", response_model=PaymentCandidatesResponse
)
def payment_candidates(
    commitment_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> PaymentCandidatesResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        core = _commitment_core(cur, owner.household_id, commitment_id, lock=False)
        if core is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        (
            archived_at,
            next_due_date,
            account_id,
            currency,
            amount,
            *_rest,
            source_register_record_id,
        ) = core
        if archived_at is not None or next_due_date is None:
            return PaymentCandidatesResponse(dueDate=None, candidates=[])
        income_refused = _source_income_refused(cur, owner.household_id, source_register_record_id)
        redact = _preview_redactor(None)
        cur.execute(
            f"""SELECT t.id, t.booked_date, t.amount, t.currency, t.description_clean,
                       t.description_raw
                  FROM v_transaction_posting t
                 WHERE t.household_id = %s AND {_eligible_predicate(income_refused)}
                   AND NOT EXISTS (
                     SELECT FROM commitment_resolution r
                      WHERE r.household_id = t.household_id AND r.transaction_id = t.id)
                   AND t.booked_date BETWEEN %s AND %s
                 ORDER BY abs(t.booked_date - %s::date), t.booked_date, t.id
                 LIMIT %s""",
            (
                owner.household_id,
                account_id,
                currency,
                amount,
                next_due_date - timedelta(days=_CANDIDATE_WINDOW_DAYS),
                next_due_date + timedelta(days=_CANDIDATE_WINDOW_DAYS),
                next_due_date,
                _CANDIDATE_LIMIT,
            ),
        )
        candidates = [
            PaymentCandidate(
                transactionId=row[0],
                bookedDate=row[1],
                amount=str(row[2]),
                currency=str(row[3]).strip(),
                description=redact(row[4] or row[5] or ""),
            )
            for row in cur.fetchall()
        ]
    return PaymentCandidatesResponse(dueDate=next_due_date, candidates=candidates)


@router.get("/commitments/{commitment_id}/resolutions", response_model=ResolutionsResponse)
def list_resolutions(
    commitment_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> ResolutionsResponse:
    with connect() as conn, conn.cursor() as cur:
        core = _commitment_core(cur, owner.household_id, commitment_id, lock=False)
        if core is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        (
            archived_at,
            next_due_date,
            account_id,
            currency,
            amount,
            *_rest,
            source_register_record_id,
        ) = core
        income_refused = _source_income_refused(cur, owner.household_id, source_register_record_id)
        latest_closed = _latest_closed_due_date(cur, owner.household_id, commitment_id)
        next_has_payment = (
            _has_payment_on(cur, owner.household_id, commitment_id, next_due_date)
            if next_due_date is not None
            else False
        )
        redact = _preview_redactor(None)
        cur.execute(
            """SELECT id, due_date, kind::text, closes, reason, created_at, transaction_id
                 FROM commitment_resolution
                WHERE household_id = %s AND commitment_id = %s
                ORDER BY due_date DESC, created_at DESC""",
            (owner.household_id, commitment_id),
        )
        out: list[ResolutionRow] = []
        for rid, due_date, kind, closes, reason, created_at, txn_id in cur.fetchall():
            transaction = None
            if txn_id is not None:
                cur.execute(
                    """SELECT booked_date, amount, description_clean, description_raw
                         FROM transaction WHERE household_id = %s AND id = %s""",
                    (owner.household_id, txn_id),
                )
                trow = cur.fetchone()
                assert trow is not None
                transaction = ResolutionTransaction(
                    transactionId=txn_id,
                    bookedDate=trow[0],
                    amount=str(trow[1]),
                    description=redact(trow[2] or trow[3] or ""),
                    eligible=_transaction_eligible(
                        cur,
                        owner.household_id,
                        txn_id,
                        account_id,
                        currency,
                        amount,
                        income_refused=income_refused,
                    ),
                )
            out.append(
                ResolutionRow(
                    id=rid,
                    dueDate=due_date,
                    kind=kind,
                    closes=closes,
                    reason=reason,
                    createdAt=created_at,
                    undoable=_is_undoable(
                        archived=archived_at is not None,
                        kind=kind,
                        closes=closes,
                        due_date=due_date,
                        next_due_date=next_due_date,
                        latest_closed_due_date=latest_closed,
                        next_occurrence_has_payment=next_has_payment,
                    ),
                    transaction=transaction,
                )
            )
    return ResolutionsResponse(resolutions=out)


@router.post("/commitments/{commitment_id}/resolutions", response_model=CommitmentRow)
def create_resolution(
    commitment_id: UUID,
    body: ResolutionCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CommitmentRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                "SELECT id FROM commitment WHERE household_id = %s AND id = %s FOR UPDATE",
                (owner.household_id, commitment_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="commitment not found")

            cur.execute(
                """SELECT id, kind::text, closes, transaction_id, reason
                     FROM commitment_resolution
                    WHERE household_id = %s AND commitment_id = %s AND due_date = %s""",
                (owner.household_id, commitment_id, body.dueDate),
            )
            existing_rows = cur.fetchall()
            closing_row = next((r for r in existing_rows if r[2]), None)
            if body.kind == "payment":
                match = next(
                    (
                        r
                        for r in existing_rows
                        if r[1] == "payment"
                        and r[3] is not None
                        and UUID(str(r[3])) == body.transactionId
                        and bool(r[2]) == body.closes
                    ),
                    None,
                )
                if match is not None:
                    response.status_code = status.HTTP_200_OK
                    return _row(cur, owner.household_id, commitment_id)
                if closing_row is not None:
                    raise HTTPException(status_code=409, detail=_RESOLUTION_ALREADY)
            elif closing_row is not None:
                if closing_row[1] == body.kind and closing_row[4] == body.reason:
                    response.status_code = status.HTTP_200_OK
                    return _row(cur, owner.household_id, commitment_id)
                raise HTTPException(status_code=409, detail=_RESOLUTION_ALREADY)

            core = _commitment_core(cur, owner.household_id, commitment_id, lock=False)
            assert core is not None
            (
                archived_at,
                next_due_date,
                account_id,
                currency,
                amount,
                cadence,
                first_date,
                end_date,
                _source_register_record_id,
            ) = core
            if archived_at is not None:
                raise HTTPException(status_code=409, detail="an archived commitment is fixed")
            if next_due_date is None:
                raise HTTPException(
                    status_code=409, detail="this commitment has no open occurrence"
                )
            if body.dueDate != next_due_date:
                raise HTTPException(
                    status_code=409,
                    detail="this occurrence is not the commitment's next open occurrence",
                )

            effective_closes = body.closes if body.kind == "payment" else True
            cur.execute(
                """INSERT INTO commitment_resolution (
                       household_id, commitment_id, due_date, kind, closes, transaction_id,
                       reason)
                   VALUES (%s, %s, %s, %s::commitment_resolution_kind, %s, %s, %s)""",
                (
                    owner.household_id,
                    commitment_id,
                    body.dueDate,
                    body.kind,
                    effective_closes,
                    body.transactionId,
                    body.reason,
                ),
            )
            if effective_closes:
                new_next = next_occurrence(cadence, first_date, end_date, body.dueDate)
                cur.execute(
                    "UPDATE commitment SET next_due_date = %s WHERE household_id = %s AND id = %s",
                    (new_next, owner.household_id, commitment_id),
                )
            response.status_code = status.HTTP_201_CREATED
            return _row(cur, owner.household_id, commitment_id)
    except psycopg.Error as exc:
        raise _translate_resolution_error(exc) from None


@router.delete(
    "/commitments/{commitment_id}/resolutions/{resolution_id}", response_model=CommitmentRow
)
def undo_resolution(
    commitment_id: UUID,
    resolution_id: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CommitmentRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        core = _commitment_core(cur, owner.household_id, commitment_id, lock=True)
        if core is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        archived_at, next_due_date = core[0], core[1]
        cur.execute(
            """SELECT due_date, kind::text, closes FROM commitment_resolution
                WHERE household_id = %s AND id = %s AND commitment_id = %s""",
            (owner.household_id, resolution_id, commitment_id),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="resolution not found")
        due_date, kind, closes = found
        if archived_at is not None:
            raise HTTPException(status_code=409, detail="an archived commitment is fixed")

        if not closes:
            if kind != "payment" or due_date != next_due_date:
                raise HTTPException(status_code=409, detail=_ONLY_LATEST_UNDOABLE)
            cur.execute(
                "DELETE FROM commitment_resolution WHERE household_id = %s AND id = %s",
                (owner.household_id, resolution_id),
            )
        else:
            latest_closed = _latest_closed_due_date(cur, owner.household_id, commitment_id)
            if due_date != latest_closed:
                raise HTTPException(status_code=409, detail=_ONLY_LATEST_UNDOABLE)
            if next_due_date is not None and _has_payment_on(
                cur, owner.household_id, commitment_id, next_due_date
            ):
                raise HTTPException(
                    status_code=409,
                    detail="remove the payments recorded against the next occurrence first",
                )
            cur.execute(
                "DELETE FROM commitment_resolution WHERE household_id = %s AND id = %s",
                (owner.household_id, resolution_id),
            )
            cur.execute(
                "UPDATE commitment SET next_due_date = %s WHERE household_id = %s AND id = %s",
                (due_date, owner.household_id, commitment_id),
            )
        return _row(cur, owner.household_id, commitment_id)
