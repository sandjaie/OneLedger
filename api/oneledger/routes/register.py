"""Phase 3.4 Task 1: the register — insurance policies, deposits, claims, nominee records and
administration deadlines (spec "API boundary", `routes/register.py`). A register record is an
owner assumption like `commitment` and `goal` — no SECURITY DEFINER write path; every write locks
the household row first and filters explicitly by it. Kind-forbidden fields are refused in
Pydantic before the database (`_check_record_shape`, shared by create and update); the database's
own `register_kind_shape` CHECK is the backstop.

Task 2 adds the two document-link routes and `RegisterRow.documents[]`/`plannedCommitment` — a
record's documents are `register_document` rows joined to `document` (only an upload-type
document may be linked, spec "Document upload (Task 2)"), and its planned commitment reuses
`commitments.commitment_progress`, the same helper `GET /commitments` and the action list already
share, so there is no second remaining-amount computation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..auth import OwnerSession, require_owner
from ..commitments import CommitmentStatus, commitment_progress
from ..db import IDEMPOTENCY_REUSED, connect, first_line
from ..money import _decimal_eq, _out
from ..register_records import review_due
from ..text import normalize_bounded
from .documents import _UPLOAD_TYPES
from .goals import _positive_money
from .plans import Cadence

router = APIRouter(tags=["register"])

RegisterKind = Literal["insurance_policy", "deposit", "claim", "nominee", "administration"]
PremiumCadence = Literal["monthly", "annual"]

_DEPOSIT_ACCOUNT_TYPES = ("fixed_deposit", "recurring_deposit")

# The fields each kind uses (spec "Register (V53)" meanings by kind and `register_kind_shape`); any
# other field must be null, refused in Pydantic before the database.
_KIND_FIELDS: dict[str, frozenset[str]] = {
    "insurance_policy": frozenset(
        {
            "counterparty",
            "covered",
            "amount",
            "currency",
            "premiumAmount",
            "premiumCadence",
            "keyDate",
        }
    ),
    "deposit": frozenset(
        {"counterparty", "amount", "currency", "keyDate", "accountId", "instruction"}
    ),
    "claim": frozenset({"counterparty", "amount", "currency", "keyDate"}),
    "nominee": frozenset({"accountId", "nomineeRecorded"}),
    "administration": frozenset({"keyDate"}),
}
_KIND_REQUIRED: dict[str, frozenset[str]] = {
    "insurance_policy": frozenset({"keyDate"}),
    "deposit": frozenset({"keyDate", "accountId"}),
    "claim": frozenset({"counterparty", "amount", "currency", "keyDate"}),
    "nominee": frozenset({"accountId", "nomineeRecorded"}),
    "administration": frozenset({"keyDate"}),
}
_ALL_KIND_FIELDS = frozenset().union(*_KIND_FIELDS.values())


class _RegisterFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    counterparty: str | None = None
    covered: str | None = None
    amount: str | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    premiumAmount: str | None = None
    premiumCadence: PremiumCadence | None = None
    keyDate: date | None = None
    accountId: UUID | None = None
    instruction: str | None = None
    nomineeRecorded: bool | None = None

    @model_validator(mode="after")
    def _normalize_fields(self) -> _RegisterFields:
        self.title = normalize_bounded(self.title, "title", 120)
        if self.counterparty is not None:
            self.counterparty = normalize_bounded(self.counterparty, "counterparty", 120)
        if self.covered is not None:
            self.covered = normalize_bounded(self.covered, "covered", 120)
        if self.instruction is not None:
            self.instruction = normalize_bounded(self.instruction, "instruction", 200)
        if self.amount is not None:
            self.amount = _positive_money(self.amount)
        if self.premiumAmount is not None:
            self.premiumAmount = _positive_money(self.premiumAmount)
        if (self.premiumAmount is None) != (self.premiumCadence is None):
            raise ValueError("premiumAmount and premiumCadence must be given together")
        if (self.amount is not None or self.premiumAmount is not None) and self.currency is None:
            raise ValueError("currency is required when amount or premium is given")
        return self


def _check_record_shape(kind: str, fields: _RegisterFields) -> None:
    """Refuses a field its kind forbids or omits a field its kind requires (spec `RegisterCreate`
    "a field its kind forbids is 422, before the database"); the database's `register_kind_shape`
    CHECK mirrors this exactly as the backstop."""
    allowed = _KIND_FIELDS[kind]
    for field in _ALL_KIND_FIELDS:
        if field not in allowed and getattr(fields, field) is not None:
            raise ValueError(f"{field} is not used by a {kind} record")
    for field in _KIND_REQUIRED[kind]:
        if getattr(fields, field) is None:
            raise ValueError(f"{field} is required for a {kind} record")


class RegisterCreate(_RegisterFields):
    kind: RegisterKind
    idempotencyKey: UUID

    @model_validator(mode="after")
    def _shape(self) -> RegisterCreate:
        _check_record_shape(self.kind, self)
        return self


class RegisterUpdate(_RegisterFields):
    pass


class RegisterRenewed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nextRenewalDate: date


class RegisterDocumentRow(BaseModel):
    documentId: UUID
    documentType: str
    title: str | None
    uploadedAt: datetime


class PlannedCommitment(BaseModel):
    commitmentId: UUID
    cadence: Cadence
    amount: str
    currency: str
    accountName: str
    nextDueDate: date | None
    status: CommitmentStatus
    paidTowardNext: str
    remainingNext: str | None
    partlyPaid: bool


class RegisterRow(BaseModel):
    id: UUID
    kind: RegisterKind
    title: str
    counterparty: str | None
    covered: str | None
    amount: str | None
    currency: str | None
    premiumAmount: str | None
    premiumCadence: str | None
    keyDate: date | None
    accountId: UUID | None
    accountName: str | None
    instruction: str | None
    nomineeRecorded: bool | None
    lastReviewedOn: date | None
    reviewDue: bool
    completedAt: datetime | None
    archivedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    documents: list[RegisterDocumentRow]
    plannedCommitment: PlannedCommitment | None


class RegisterResponse(BaseModel):
    records: list[RegisterRow]


def _translate_register_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_register_idempotency":
        return HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
    if constraint == "fk_register_account_scope":
        return HTTPException(status_code=404, detail="account not found")
    if constraint == "register_texts_are_bounded":
        return HTTPException(status_code=422, detail="a text field is outside its length limit")
    if constraint == "register_amounts_are_valid":
        return HTTPException(status_code=422, detail="an amount or premium field is invalid")
    if constraint == "register_kind_shape":
        return HTTPException(status_code=422, detail="these fields do not match the record's kind")
    if exc.sqlstate == "23514" and "archived register record is immutable" in first_line(exc):
        return HTTPException(status_code=409, detail="an archived register record cannot be edited")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(status_code=422, detail="record violates its required field matrix")
    raise exc


def _validate_account(
    cur: psycopg.Cursor[Any], household_id: UUID, kind: str, account_id: UUID | None
) -> None:
    if account_id is None:
        return
    cur.execute(
        "SELECT is_active, account_type::text FROM account WHERE household_id = %s AND id = %s",
        (household_id, account_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="account not found")
    if kind == "deposit":
        is_active, account_type = found
        if not is_active or account_type not in _DEPOSIT_ACCOUNT_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    "a deposit's account must be an active fixed deposit or recurring "
                    "deposit account"
                ),
            )


_ROW_COLUMNS = """r.kind::text, r.title, r.counterparty, r.covered, r.amount, r.currency,
                   r.premium_amount, r.premium_cadence::text, r.key_date, r.account_id, a.name,
                   r.instruction, r.nominee_recorded, r.last_reviewed_on, r.completed_at,
                   r.archived_at, r.created_at, r.updated_at"""
_ROW_FROM = """FROM register_record r
               LEFT JOIN account a ON a.household_id = r.household_id AND a.id = r.account_id"""


def _register_row(
    cur: psycopg.Cursor[Any], household_id: UUID, record_id: UUID, as_of: date
) -> RegisterRow:
    cur.execute(
        f"SELECT {_ROW_COLUMNS} {_ROW_FROM} WHERE r.household_id = %s AND r.id = %s",
        (household_id, record_id),
    )
    row = cur.fetchone()
    assert row is not None
    (
        kind,
        title,
        counterparty,
        covered,
        amount,
        currency,
        premium_amount,
        premium_cadence,
        key_date,
        account_id,
        account_name,
        instruction,
        nominee_recorded,
        last_reviewed_on,
        completed_at,
        archived_at,
        created_at,
        updated_at,
    ) = row

    cur.execute(
        """SELECT d.id, d.document_type::text, d.title, d.uploaded_at
             FROM register_document rd
             JOIN document d ON d.household_id = rd.household_id AND d.id = rd.document_id
            WHERE rd.household_id = %s AND rd.record_id = %s
            ORDER BY d.uploaded_at DESC, d.id""",
        (household_id, record_id),
    )
    documents = [
        RegisterDocumentRow(
            documentId=UUID(str(doc_id)),
            documentType=doc_type,
            title=doc_title,
            uploadedAt=uploaded,
        )
        for doc_id, doc_type, doc_title, uploaded in cur.fetchall()
    ]

    cur.execute(
        """SELECT c.id, c.account_id, a.name, c.amount, c.currency, c.cadence::text,
                  c.next_due_date, c.archived_at
             FROM commitment c
             JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
            WHERE c.household_id = %s AND c.source_register_record_id = %s
              AND c.archived_at IS NULL""",
        (household_id, record_id),
    )
    commitment_found = cur.fetchone()
    planned_commitment: PlannedCommitment | None = None
    if commitment_found is not None:
        (
            commitment_id,
            _commitment_account_id,
            commitment_account_name,
            commitment_amount,
            commitment_currency,
            commitment_cadence,
            commitment_next_due,
            commitment_archived_at,
        ) = commitment_found
        progress = commitment_progress(
            cur,
            household_id,
            as_of,
            [
                (
                    UUID(str(commitment_id)),
                    commitment_archived_at,
                    commitment_next_due,
                    Decimal(commitment_amount),
                )
            ],
        )
        c_status, partly_paid, paid_toward_next, remaining_next = progress[UUID(str(commitment_id))]
        planned_commitment = PlannedCommitment(
            commitmentId=UUID(str(commitment_id)),
            cadence=commitment_cadence,
            amount=str(Decimal(commitment_amount)),
            currency=str(commitment_currency).strip(),
            accountName=commitment_account_name,
            nextDueDate=commitment_next_due,
            status=c_status,
            paidTowardNext=paid_toward_next,
            remainingNext=remaining_next,
            partlyPaid=partly_paid,
        )

    return RegisterRow(
        id=record_id,
        kind=kind,
        title=title,
        counterparty=counterparty,
        covered=covered,
        amount=_out(None if amount is None else Decimal(amount)),
        currency=None if currency is None else str(currency).strip(),
        premiumAmount=_out(None if premium_amount is None else Decimal(premium_amount)),
        premiumCadence=premium_cadence,
        keyDate=key_date,
        accountId=None if account_id is None else UUID(str(account_id)),
        accountName=account_name,
        instruction=instruction,
        nomineeRecorded=nominee_recorded,
        lastReviewedOn=last_reviewed_on,
        reviewDue=review_due(kind, last_reviewed_on, created_at, as_of),
        completedAt=completed_at,
        archivedAt=archived_at,
        createdAt=created_at,
        updatedAt=updated_at,
        documents=documents,
        plannedCommitment=planned_commitment,
    )


@router.get("/register", response_model=RegisterResponse)
def list_register(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    kind: RegisterKind | None = None,
    includeArchived: Annotated[bool, Query()] = False,
    asOf: Annotated[date | None, Query()] = None,
) -> RegisterResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        conditions = ["household_id = %s"]
        params: list[object] = [owner.household_id]
        if kind is not None:
            conditions.append("kind = %s")
            params.append(kind)
        if not includeArchived:
            conditions.append("archived_at IS NULL")
        cur.execute(
            f"""SELECT id FROM register_record WHERE {" AND ".join(conditions)}
                 ORDER BY key_date NULLS LAST, title""",
            params,
        )
        records = [
            _register_row(cur, owner.household_id, UUID(str(r[0])), as_of) for r in cur.fetchall()
        ]
    return RegisterResponse(records=records)


@router.post("/register", response_model=RegisterRow)
def create_register(
    body: RegisterCreate, response: Response, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RegisterRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT id, kind::text, title, counterparty, covered, amount, currency,
                          premium_amount, premium_cadence::text, key_date, account_id, instruction,
                          nominee_recorded
                     FROM register_record WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                (
                    eid,
                    ekind,
                    etitle,
                    ecounterparty,
                    ecovered,
                    eamount,
                    ecurrency,
                    epremium,
                    ecadence,
                    ekeydate,
                    eaccount,
                    einstruction,
                    enominee,
                ) = existing
                matches = (
                    ekind == body.kind
                    and etitle == body.title
                    and ecounterparty == body.counterparty
                    and ecovered == body.covered
                    and _decimal_eq(None if eamount is None else Decimal(eamount), body.amount)
                    and (None if ecurrency is None else str(ecurrency).strip()) == body.currency
                    and _decimal_eq(
                        None if epremium is None else Decimal(epremium), body.premiumAmount
                    )
                    and ecadence == body.premiumCadence
                    and ekeydate == body.keyDate
                    and (None if eaccount is None else UUID(str(eaccount))) == body.accountId
                    and einstruction == body.instruction
                    and enominee == body.nomineeRecorded
                )
                if matches:
                    response.status_code = status.HTTP_200_OK
                    return _register_row(cur, owner.household_id, UUID(str(eid)), date.today())
                raise HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)

            _validate_account(cur, owner.household_id, body.kind, body.accountId)
            cur.execute(
                """INSERT INTO register_record (household_id, idempotency_key, kind, title,
                       counterparty, covered, amount, currency, premium_amount, premium_cadence,
                       key_date, account_id, instruction, nominee_recorded)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.kind,
                    body.title,
                    body.counterparty,
                    body.covered,
                    body.amount,
                    body.currency,
                    body.premiumAmount,
                    body.premiumCadence,
                    body.keyDate,
                    body.accountId,
                    body.instruction,
                    body.nomineeRecorded,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            return _register_row(cur, owner.household_id, UUID(str(created[0])), date.today())
    except psycopg.Error as exc:
        raise _translate_register_error(exc) from None


@router.put("/register/{recordId}", response_model=RegisterRow)
def update_register(
    recordId: UUID,
    body: RegisterUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RegisterRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                "SELECT kind::text, archived_at FROM register_record "
                "WHERE household_id = %s AND id = %s FOR UPDATE",
                (owner.household_id, recordId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="record not found")
            kind, archived_at = found
            if archived_at is not None:
                raise HTTPException(
                    status_code=409, detail="an archived register record cannot be edited"
                )
            try:
                _check_record_shape(kind, body)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            _validate_account(cur, owner.household_id, kind, body.accountId)
            cur.execute(
                """UPDATE register_record SET title = %s, counterparty = %s, covered = %s,
                       amount = %s, currency = %s, premium_amount = %s, premium_cadence = %s,
                       key_date = %s, account_id = %s, instruction = %s, nominee_recorded = %s,
                       updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (
                    body.title,
                    body.counterparty,
                    body.covered,
                    body.amount,
                    body.currency,
                    body.premiumAmount,
                    body.premiumCadence,
                    body.keyDate,
                    body.accountId,
                    body.instruction,
                    body.nomineeRecorded,
                    owner.household_id,
                    recordId,
                ),
            )
            return _register_row(cur, owner.household_id, recordId, date.today())
    except psycopg.Error as exc:
        raise _translate_register_error(exc) from None


def _locked_record(
    cur: psycopg.Cursor[Any], household_id: UUID, record_id: UUID, columns: str
) -> tuple[Any, ...]:
    cur.execute(
        f"SELECT {columns} FROM register_record WHERE household_id = %s AND id = %s FOR UPDATE",
        (household_id, record_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="record not found")
    return tuple(found)


@router.post("/register/{recordId}/archive", response_model=RegisterRow)
def archive_register(
    recordId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        (archived_at,) = _locked_record(cur, owner.household_id, recordId, "archived_at")
        if archived_at is None:
            cur.execute(
                "UPDATE register_record SET archived_at = now() "
                "WHERE household_id = %s AND id = %s",
                (owner.household_id, recordId),
            )
        return _register_row(cur, owner.household_id, recordId, date.today())


@router.post("/register/{recordId}/reviewed", response_model=RegisterRow)
def mark_reviewed(
    recordId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        archived_at, last_reviewed_on = _locked_record(
            cur, owner.household_id, recordId, "archived_at, last_reviewed_on"
        )
        if archived_at is not None:
            raise HTTPException(
                status_code=409, detail="an archived register record cannot be edited"
            )
        today = date.today()
        if last_reviewed_on != today:
            cur.execute(
                "UPDATE register_record SET last_reviewed_on = %s, updated_at = now() "
                "WHERE household_id = %s AND id = %s",
                (today, owner.household_id, recordId),
            )
        return _register_row(cur, owner.household_id, recordId, today)


@router.post("/register/{recordId}/renewed", response_model=RegisterRow)
def mark_renewed(
    recordId: UUID,
    body: RegisterRenewed,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        kind, archived_at, key_date = _locked_record(
            cur, owner.household_id, recordId, "kind::text, archived_at, key_date"
        )
        if archived_at is not None:
            raise HTTPException(
                status_code=409, detail="an archived register record cannot be edited"
            )
        if kind != "insurance_policy":
            raise HTTPException(status_code=422, detail="only a policy can be marked renewed")
        if body.nextRenewalDate <= key_date:
            raise HTTPException(
                status_code=422,
                detail="the next renewal date must be after the current renewal date",
            )
        cur.execute(
            "UPDATE register_record SET key_date = %s, updated_at = now() "
            "WHERE household_id = %s AND id = %s",
            (body.nextRenewalDate, owner.household_id, recordId),
        )
        return _register_row(cur, owner.household_id, recordId, date.today())


@router.post("/register/{recordId}/complete", response_model=RegisterRow)
def complete_register(
    recordId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        kind, archived_at, completed_at = _locked_record(
            cur, owner.household_id, recordId, "kind::text, archived_at, completed_at"
        )
        if archived_at is not None:
            raise HTTPException(
                status_code=409, detail="an archived register record cannot be edited"
            )
        if kind not in ("claim", "administration"):
            raise HTTPException(
                status_code=422, detail="only a claim or administration record can be completed"
            )
        if completed_at is None:
            cur.execute(
                "UPDATE register_record SET completed_at = now(), updated_at = now() "
                "WHERE household_id = %s AND id = %s",
                (owner.household_id, recordId),
            )
        return _register_row(cur, owner.household_id, recordId, date.today())


@router.post("/register/{recordId}/reopen", response_model=RegisterRow)
def reopen_register(
    recordId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        kind, archived_at, completed_at = _locked_record(
            cur, owner.household_id, recordId, "kind::text, archived_at, completed_at"
        )
        if archived_at is not None:
            raise HTTPException(
                status_code=409, detail="an archived register record cannot be edited"
            )
        if kind not in ("claim", "administration"):
            raise HTTPException(
                status_code=422, detail="only a claim or administration record can be reopened"
            )
        if completed_at is not None:
            cur.execute(
                "UPDATE register_record SET completed_at = NULL, updated_at = now() "
                "WHERE household_id = %s AND id = %s",
                (owner.household_id, recordId),
            )
        return _register_row(cur, owner.household_id, recordId, date.today())


# ---------------------------------------------------------------------------------------
# Task 2: document links. Only a household document of an upload type may be linked (spec
# "Task 2 — documents on records and commitments from records"); the upload itself is
# `POST /documents`.


@router.put("/register/{recordId}/documents/{documentId}", response_model=RegisterRow)
def link_document(
    recordId: UUID,
    documentId: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        (archived_at,) = _locked_record(cur, owner.household_id, recordId, "archived_at")
        if archived_at is not None:
            raise HTTPException(
                status_code=409, detail="an archived register record cannot be edited"
            )
        cur.execute(
            "SELECT document_type::text FROM document WHERE household_id = %s AND id = %s",
            (owner.household_id, documentId),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="document not found")
        if found[0] not in _UPLOAD_TYPES:
            raise HTTPException(
                status_code=422, detail="only an uploaded document can be linked to a record"
            )
        cur.execute(
            """INSERT INTO register_document (household_id, record_id, document_id)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (owner.household_id, recordId, documentId),
        )
        return _register_row(cur, owner.household_id, recordId, date.today())


@router.delete("/register/{recordId}/documents/{documentId}", response_model=RegisterRow)
def unlink_document(
    recordId: UUID,
    documentId: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RegisterRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        (archived_at,) = _locked_record(cur, owner.household_id, recordId, "archived_at")
        if archived_at is not None:
            raise HTTPException(
                status_code=409, detail="an archived register record cannot be edited"
            )
        cur.execute(
            """DELETE FROM register_document
                WHERE household_id = %s AND record_id = %s AND document_id = %s""",
            (owner.household_id, recordId, documentId),
        )
        return _register_row(cur, owner.household_id, recordId, date.today())
