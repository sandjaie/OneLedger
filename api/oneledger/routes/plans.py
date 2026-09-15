"""Phase 1.7 Task 2: commitments — the owner's one-off, monthly or annual signed amount
assumptions (V43) — and the read-only 30/60/90-day forecast built from them. Commitments carry
no SECURITY DEFINER write path (spec "Commitments (V43)": an owner assumption, not an evidenced
fact); every write here locks the household row and filters explicitly by household id, the same
owner-scoping convention as `routes/accounts.py`. `GET /forecast` opens `SET TRANSACTION READ
ONLY` before any other statement and calls `forecast.py`'s pure functions to do the math — it
posts nothing, changes no balance, and reads through the same valuation-period selection as
`GET /dashboard` (`dashboard.VALUATION_PERIOD_FOR_ACCOUNT`)."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..forecast import is_occurrence, next_occurrence, occurrence_dates, project_balances
from ..money import _money
from .dashboard import VALUATION_PERIOD_FOR_ACCOUNT

router = APIRouter(tags=["plans"])

Cadence = Literal["once", "monthly", "annual"]
Certainty = Literal["confirmed", "estimated"]
CommitmentStatus = Literal["upcoming", "due", "overdue", "finished", "cancelled"]
ExcludedAccountReason = Literal["not_ledger_valued", "no_balance_date", "balance_after_as_of"]
ExcludedCommitmentReason = Literal["account_closed", "account_excluded", "currency_mismatch"]

# The open occurrence is "due" through this many days past `asOf` (Phase 3.1 spec "A commitment's
# lifecycle"); shared by the commitments list, the forecast and, in Task 2, the action list.
DUE_WINDOW_DAYS = 7


def _normalize_label(value: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(collapsed) <= 80:
        raise ValueError("label must be 1-80 characters")
    return collapsed


def _signed_nonzero(value: str) -> str:
    checked = _money(value)
    assert checked is not None
    if Decimal(checked) == 0:
        raise ValueError("amount must not be zero")
    return checked


class _CommitmentFields(BaseModel):
    """The fields `CommitmentCreate` and `CommitmentUpdate` share — everything but the
    create-only idempotency key."""

    model_config = ConfigDict(extra="forbid")

    accountId: UUID
    label: str
    amount: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    cadence: Cadence
    firstDate: date
    endDate: date | None = None
    certainty: Certainty = "confirmed"
    nextDueDate: date | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _normalize_label(value)

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: str) -> str:
        return _signed_nonzero(value)

    @model_validator(mode="after")
    def _end_date_rule(self) -> _CommitmentFields:
        if self.endDate is not None:
            if self.cadence == "once":
                raise ValueError("a one-off commitment must not have an end date")
            if self.endDate < self.firstDate:
                raise ValueError("end date must be on or after the first date")
        return self


class CommitmentCreate(_CommitmentFields):
    idempotencyKey: UUID


class CommitmentUpdate(_CommitmentFields):
    pass


class CommitmentRow(BaseModel):
    id: UUID
    accountId: UUID
    accountName: str
    label: str
    amount: str
    currency: str
    cadence: Cadence
    firstDate: date
    endDate: date | None
    archivedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    certainty: Certainty
    nextDueDate: date | None
    status: CommitmentStatus
    paidTowardNext: str
    partlyPaid: bool
    remainingNext: str | None


class CommitmentsResponse(BaseModel):
    commitments: list[CommitmentRow]


class ForecastPoint(BaseModel):
    days: int
    date: date
    balance: str


class ForecastOccurrence(BaseModel):
    commitmentId: UUID
    label: str
    date: date
    amount: str
    dueByAsOf: bool
    certainty: Certainty


class ForecastAccount(BaseModel):
    accountId: UUID
    accountName: str
    accountClass: Literal["asset", "liability"]
    currency: str
    startBalance: str
    balanceDate: date
    points: list[ForecastPoint]
    occurrences: list[ForecastOccurrence]


class ExcludedAccount(BaseModel):
    accountId: UUID
    accountName: str
    reason: ExcludedAccountReason


class ExcludedCommitment(BaseModel):
    commitmentId: UUID
    label: str
    accountId: UUID
    reason: ExcludedCommitmentReason


class ForecastResponse(BaseModel):
    asOf: date
    accounts: list[ForecastAccount]
    excludedAccounts: list[ExcludedAccount]
    excludedCommitments: list[ExcludedCommitment]


def _translate_database_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_commitment_idempotency":
        return HTTPException(
            status_code=409, detail="idempotency key reused with a different request"
        )
    if constraint == "commitment_amount_is_nonzero":
        return HTTPException(status_code=422, detail="commitment amount must not be zero")
    if constraint == "commitment_end_date_agrees":
        return HTTPException(
            status_code=422,
            detail="end date must be on or after the first date and unset for a one-off commitment",
        )
    if constraint == "commitment_label_is_bounded":
        return HTTPException(status_code=422, detail="label must be 1-80 characters")
    if constraint == "fk_commitment_account_scope":
        return HTTPException(status_code=404, detail="account not found")
    if constraint == "commitment_next_due_in_schedule":
        return HTTPException(
            status_code=422, detail="next due date is not on this commitment's schedule"
        )
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="commitment violates its required field matrix"
        )
    raise exc


def _assert_account(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID, currency: str
) -> None:
    cur.execute(
        "SELECT currency, is_active FROM account WHERE household_id = %s AND id = %s",
        (household_id, account_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="account not found")
    account_currency, is_active = found
    if not is_active:
        raise HTTPException(status_code=422, detail="the account is not active")
    if str(account_currency).strip() != currency:
        raise HTTPException(
            status_code=422, detail="commitment currency must match the account's currency"
        )


def _default_next_due_date(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    cadence: Cadence,
    first_date: date,
    end_date: date | None,
) -> date | None:
    """Spec "Occurrence rules": `first_date` for `once`; otherwise the first occurrence strictly
    after the account's `current_balance_as_of::date` when that is before today (so an occurrence
    the forecast already treats as unposted stays open), else the first occurrence on or after
    today. `None` means no occurrence remains — the caller answers 422."""
    if cadence == "once":
        return first_date
    cur.execute(
        "SELECT current_balance_as_of FROM account WHERE household_id = %s AND id = %s",
        (household_id, account_id),
    )
    found = cur.fetchone()
    balance_as_of = found[0] if found is not None else None
    today = date.today()
    balance_date = balance_as_of.date() if balance_as_of is not None else None
    if balance_date is not None and balance_date < today:
        return next_occurrence(cadence, first_date, end_date, balance_date)
    return next_occurrence(cadence, first_date, end_date, today - timedelta(days=1))


def _resolved_next_due_date(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    cadence: Cadence,
    first_date: date,
    end_date: date | None,
    given: date | None,
) -> date:
    """A given `nextDueDate` must be on the commitment's schedule (422 otherwise); an omitted one
    takes the default rule, itself 422 when no occurrence remains."""
    if given is not None:
        if not is_occurrence(cadence, first_date, end_date, given):
            raise HTTPException(
                status_code=422, detail="next due date is not on this commitment's schedule"
            )
        return given
    default = _default_next_due_date(cur, household_id, account_id, cadence, first_date, end_date)
    if default is None:
        raise HTTPException(status_code=422, detail="this schedule has no occurrence left")
    return default


def _commitment_progress(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    as_of: date,
    commitments: list[tuple[UUID, datetime | None, date | None, Decimal]],
) -> dict[UUID, tuple[CommitmentStatus, bool, str, str | None]]:
    """One query over `commitment_resolution` joined to `transaction`: for each commitment (given
    as its id, `archived_at`, `next_due_date` and `amount`) its lifecycle status (spec "A
    commitment's lifecycle"), whether a payment is recorded against its open occurrence, the sum
    paid toward it and what remains. Reused by the commitments list, the resolutions router
    (after a write) and, in Task 2, the action list."""
    open_ids = [cid for cid, _, next_due, _ in commitments if next_due is not None]
    open_dates = [next_due for _, _, next_due, _ in commitments if next_due is not None]
    paid_by_commitment: dict[UUID, Decimal] = {}
    if open_ids:
        cur.execute(
            """SELECT r.commitment_id, sum(t.amount)
                 FROM commitment_resolution r
                 JOIN v_transaction_posting t
                   ON t.household_id = r.household_id AND t.id = r.transaction_id
                 JOIN unnest(%s::uuid[], %s::date[]) AS open_next(commitment_id, next_due_date)
                   ON open_next.commitment_id = r.commitment_id
                  AND open_next.next_due_date = r.due_date
                WHERE r.household_id = %s AND r.kind = 'payment'
                GROUP BY r.commitment_id""",
            (open_ids, open_dates, household_id),
        )
        for commitment_id, total in cur.fetchall():
            paid_by_commitment[UUID(str(commitment_id))] = Decimal(total)

    result: dict[UUID, tuple[CommitmentStatus, bool, str, str | None]] = {}
    for commitment_id, archived_at, next_due, amount in commitments:
        status: CommitmentStatus
        if archived_at is not None:
            status = "cancelled"
        elif next_due is None:
            status = "finished"
        elif next_due < as_of:
            status = "overdue"
        elif next_due <= as_of + timedelta(days=DUE_WINDOW_DAYS):
            status = "due"
        else:
            status = "upcoming"

        if next_due is None:
            paid_toward_next = Decimal("0.0000")
            partly_paid = False
            remaining_next: str | None = None
        else:
            paid_toward_next = paid_by_commitment.get(commitment_id, Decimal("0.0000"))
            partly_paid = commitment_id in paid_by_commitment
            remaining_next = (
                str(amount - paid_toward_next) if abs(paid_toward_next) < abs(amount) else "0.0000"
            )
        result[commitment_id] = (status, partly_paid, str(paid_toward_next), remaining_next)
    return result


# Column order shared by every commitment read; _commitment_row() maps one result tuple.
_COMMITMENT_COLUMNS = """c.id, c.account_id, a.name, c.label, c.amount, c.currency, c.cadence::text,
                         c.first_date, c.end_date, c.archived_at, c.created_at, c.updated_at,
                         c.certainty::text, c.next_due_date"""
_COMMITMENT_FROM = """FROM commitment c
                       JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id"""


def _commitment_row(
    row: tuple[Any, ...], progress: tuple[CommitmentStatus, bool, str, str | None]
) -> CommitmentRow:
    status, partly_paid, paid_toward_next, remaining_next = progress
    return CommitmentRow(
        id=row[0],
        accountId=row[1],
        accountName=row[2],
        label=row[3],
        amount=str(row[4]),
        currency=str(row[5]).strip(),
        cadence=row[6],
        firstDate=row[7],
        endDate=row[8],
        archivedAt=row[9],
        createdAt=row[10],
        updatedAt=row[11],
        certainty=row[12],
        nextDueDate=row[13],
        status=status,
        paidTowardNext=paid_toward_next,
        partlyPaid=partly_paid,
        remainingNext=remaining_next,
    )


def _row(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    commitment_id: UUID,
    as_of: date | None = None,
) -> CommitmentRow:
    cur.execute(
        f"SELECT {_COMMITMENT_COLUMNS} {_COMMITMENT_FROM} WHERE c.household_id = %s AND c.id = %s",
        (household_id, commitment_id),
    )
    row = cur.fetchone()
    assert row is not None
    progress = _commitment_progress(
        cur, household_id, as_of or date.today(), [(row[0], row[9], row[13], Decimal(row[4]))]
    )
    return _commitment_row(row, progress[UUID(str(row[0]))])


@router.get("/commitments", response_model=CommitmentsResponse)
def list_commitments(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    includeArchived: Annotated[bool, Query()] = False,
    asOf: Annotated[date | None, Query()] = None,
) -> CommitmentsResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        archived_filter = "" if includeArchived else " AND c.archived_at IS NULL"
        cur.execute(
            f"""SELECT {_COMMITMENT_COLUMNS} {_COMMITMENT_FROM}
                 WHERE c.household_id = %s{archived_filter}
                 ORDER BY a.account_class, a.country, a.display_order, a.name,
                          c.first_date, c.label""",
            (owner.household_id,),
        )
        raw = cur.fetchall()
        progress = _commitment_progress(
            cur, owner.household_id, as_of, [(r[0], r[9], r[13], Decimal(r[4])) for r in raw]
        )
        rows = [_commitment_row(r, progress[UUID(str(r[0]))]) for r in raw]
    return CommitmentsResponse(commitments=rows)


@router.post("/commitments", response_model=CommitmentRow)
def create_commitment(
    body: CommitmentCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CommitmentRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT id, account_id, label, amount, currency, cadence::text,
                          first_date, end_date, certainty::text, next_due_date
                     FROM commitment WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                (
                    eid,
                    eaccount,
                    elabel,
                    eamount,
                    ecurrency,
                    ecadence,
                    efirst,
                    eend,
                    ecertainty,
                    enext,
                ) = existing
                if (
                    UUID(str(eaccount)) == body.accountId
                    and elabel == body.label
                    and Decimal(eamount) == Decimal(body.amount)
                    and str(ecurrency).strip() == body.currency
                    and ecadence == body.cadence
                    and efirst == body.firstDate
                    and eend == body.endDate
                    and ecertainty == body.certainty
                    and (body.nextDueDate is None or enext == body.nextDueDate)
                ):
                    response.status_code = status.HTTP_200_OK
                    return _row(cur, owner.household_id, UUID(str(eid)))
                raise HTTPException(
                    status_code=409, detail="idempotency key reused with a different request"
                )
            _assert_account(cur, owner.household_id, body.accountId, body.currency)
            next_due = _resolved_next_due_date(
                cur,
                owner.household_id,
                body.accountId,
                body.cadence,
                body.firstDate,
                body.endDate,
                body.nextDueDate,
            )
            cur.execute(
                """INSERT INTO commitment (household_id, idempotency_key, account_id, label,
                                            amount, currency, cadence, first_date, end_date,
                                            certainty, next_due_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::commitment_cadence, %s, %s,
                           %s::commitment_certainty, %s)
                   RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.accountId,
                    body.label,
                    Decimal(body.amount),
                    body.currency,
                    body.cadence,
                    body.firstDate,
                    body.endDate,
                    body.certainty,
                    next_due,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            return _row(cur, owner.household_id, UUID(str(created[0])))
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None


_RESOLVED_SCHEDULE_FIXED = (
    "a commitment with resolved occurrences keeps its account and schedule; "
    "archive it and add a new one"
)


@router.put("/commitments/{commitment_id}", response_model=CommitmentRow)
def update_commitment(
    commitment_id: UUID,
    body: CommitmentUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> CommitmentRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT archived_at, account_id, currency, cadence::text, first_date, end_date,
                          amount, next_due_date
                     FROM commitment WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, commitment_id),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="commitment not found")
            (
                archived_at,
                stored_account,
                stored_currency,
                stored_cadence,
                stored_first,
                stored_end,
                stored_amount,
                stored_next_due,
            ) = found
            if archived_at is not None:
                raise HTTPException(status_code=409, detail="an archived commitment is fixed")
            cur.execute(
                """SELECT count(*) FROM commitment_resolution
                    WHERE household_id = %s AND commitment_id = %s""",
                (owner.household_id, commitment_id),
            )
            has_resolution = cur.fetchone()[0] > 0

            if has_resolution:
                if (
                    body.accountId != UUID(str(stored_account))
                    or body.currency != str(stored_currency).strip()
                    or body.cadence != stored_cadence
                    or body.firstDate != stored_first
                    or (Decimal(body.amount) < 0) != (Decimal(stored_amount) < 0)
                    or (body.nextDueDate is not None and body.nextDueDate != stored_next_due)
                ):
                    raise HTTPException(status_code=409, detail=_RESOLVED_SCHEDULE_FIXED)
                next_due = stored_next_due
                latest_closed = None
                if stored_next_due is None:
                    cur.execute(
                        """SELECT max(due_date) FROM commitment_resolution
                            WHERE household_id = %s AND commitment_id = %s AND closes""",
                        (owner.household_id, commitment_id),
                    )
                    latest_closed = cur.fetchone()[0]
                floor = stored_next_due if stored_next_due is not None else latest_closed
                if body.endDate is not None and floor is not None and body.endDate < floor:
                    raise HTTPException(
                        status_code=422,
                        detail="end date must not fall before the next due date",
                    )
                if stored_next_due is None and latest_closed is not None:
                    # A finished commitment whose end date is extended (or removed) reopens at
                    # the occurrence after its latest closed one, so the newly allowed
                    # occurrences are projected and settleable rather than silently dropped.
                    next_due = next_occurrence(
                        stored_cadence, stored_first, body.endDate, latest_closed
                    )
            else:
                _assert_account(cur, owner.household_id, body.accountId, body.currency)
                if (
                    body.nextDueDate is None
                    and stored_next_due is not None
                    and is_occurrence(body.cadence, body.firstDate, body.endDate, stored_next_due)
                ):
                    next_due = stored_next_due
                elif (
                    body.nextDueDate is None
                    and stored_next_due is None
                    and (body.cadence, body.firstDate, body.endDate)
                    == (stored_cadence, stored_first, stored_end)
                ):
                    # An already-finished commitment (e.g. an ended Phase 1.7 row backfilled to
                    # NULL) keeps accepting edits that leave its schedule alone.
                    next_due = None
                else:
                    next_due = _resolved_next_due_date(
                        cur,
                        owner.household_id,
                        body.accountId,
                        body.cadence,
                        body.firstDate,
                        body.endDate,
                        body.nextDueDate,
                    )

            cur.execute(
                """UPDATE commitment
                      SET account_id = %s, label = %s, amount = %s, currency = %s,
                          cadence = %s::commitment_cadence, first_date = %s, end_date = %s,
                          certainty = %s::commitment_certainty, next_due_date = %s,
                          updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (
                    body.accountId,
                    body.label,
                    Decimal(body.amount),
                    body.currency,
                    body.cadence,
                    body.firstDate,
                    body.endDate,
                    body.certainty,
                    next_due,
                    owner.household_id,
                    commitment_id,
                ),
            )
            return _row(cur, owner.household_id, commitment_id)
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None


@router.post("/commitments/{commitment_id}/archive", response_model=CommitmentRow)
def archive_commitment(
    commitment_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> CommitmentRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "SELECT id FROM commitment WHERE household_id = %s AND id = %s FOR UPDATE",
            (owner.household_id, commitment_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="commitment not found")
        cur.execute(
            """UPDATE commitment SET archived_at = coalesce(archived_at, now())
                WHERE household_id = %s AND id = %s""",
            (owner.household_id, commitment_id),
        )
        return _row(cur, owner.household_id, commitment_id)


# GET /accounts's own order (spec rule 1); shared verbatim with accounts.py/dashboard.py would be
# nicer still, but the plan license only extracts the valuation-period selection.
_ACCOUNT_ORDER = "a.account_class, a.country, a.display_order, a.name"

FORECAST_ACCOUNTS = f"""
    SELECT a.id, a.name, a.account_class::text, a.currency, a.current_balance,
           a.current_balance_as_of, period.valuation_mode::text
      FROM account a
      JOIN account_valuation_period period ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
     WHERE a.household_id = %s AND a.is_active
     ORDER BY {_ACCOUNT_ORDER}"""

FORECAST_COMMITMENTS = """
    SELECT c.id, c.account_id, a.is_active, a.currency, c.label, c.amount, c.currency,
           c.cadence::text, c.first_date, c.end_date, c.next_due_date, c.certainty::text
      FROM commitment c
      JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
     WHERE c.household_id = %s AND c.archived_at IS NULL"""


@router.get("/forecast", response_model=ForecastResponse)
def read_forecast(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> ForecastResponse:
    as_of = asOf or date.today()
    through = as_of + timedelta(days=90)
    with connect() as conn, conn.cursor() as cur:
        # No SECURITY DEFINER function is called, so explicit household filters are the whole
        # authorization boundary here; READ ONLY is the database's own guarantee that this
        # route, present or future, cannot write (spec "forecast writes nothing").
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(FORECAST_ACCOUNTS, (as_of, as_of, owner.household_id))

        included: dict[UUID, dict[str, Any]] = {}
        ordered_ids: list[UUID] = []
        excluded_accounts: list[ExcludedAccount] = []
        for (
            account_id,
            name,
            account_class,
            currency,
            balance,
            balance_as_of,
            mode,
        ) in cur.fetchall():
            account_id = UUID(str(account_id))
            if mode != "ledger_balance":
                excluded_accounts.append(
                    ExcludedAccount(
                        accountId=account_id, accountName=name, reason="not_ledger_valued"
                    )
                )
                continue
            if balance_as_of is None:
                excluded_accounts.append(
                    ExcludedAccount(
                        accountId=account_id, accountName=name, reason="no_balance_date"
                    )
                )
                continue
            balance_date = balance_as_of.date()
            if balance_date > as_of:
                excluded_accounts.append(
                    ExcludedAccount(
                        accountId=account_id, accountName=name, reason="balance_after_as_of"
                    )
                )
                continue
            included[account_id] = {
                "name": name,
                "accountClass": account_class,
                "currency": str(currency).strip(),
                "start": Decimal(balance),
                "balanceDate": balance_date,
                "occurrences": [],
                "shown": [],
            }
            ordered_ids.append(account_id)

        cur.execute(FORECAST_COMMITMENTS, (owner.household_id,))
        commitment_rows = cur.fetchall()
        remaining_by_commitment = {
            commitment_id: remaining
            for commitment_id, (_status, _partly, _paid, remaining) in _commitment_progress(
                cur,
                owner.household_id,
                as_of,
                [(row[0], None, row[10], Decimal(row[5])) for row in commitment_rows],
            ).items()
        }

        excluded_commitments: list[ExcludedCommitment] = []
        for (
            commitment_id,
            account_id,
            account_active,
            account_currency,
            label,
            amount,
            currency,
            cadence,
            first_date,
            end_date,
            next_due_date,
            certainty,
        ) in commitment_rows:
            commitment_id = UUID(str(commitment_id))
            account_id = UUID(str(account_id))
            currency = str(currency).strip()
            account_currency = str(account_currency).strip()
            if not account_active:
                reason: ExcludedCommitmentReason = "account_closed"
            elif account_id not in included:
                reason = "account_excluded"
            elif currency != account_currency:
                reason = "currency_mismatch"
            else:
                slot = included[account_id]
                amount_decimal = Decimal(amount)
                if next_due_date is not None:
                    dates = occurrence_dates(
                        cadence, first_date, end_date, slot["balanceDate"], through
                    )
                    remaining_next = remaining_by_commitment.get(commitment_id)
                    for occurred in dates:
                        if occurred < next_due_date:
                            continue
                        if occurred == next_due_date:
                            if remaining_next is None or Decimal(remaining_next) == 0:
                                continue
                            occurrence_amount = Decimal(remaining_next)
                        else:
                            occurrence_amount = amount_decimal
                        slot["occurrences"].append((occurred, occurrence_amount))
                        slot["shown"].append(
                            ForecastOccurrence(
                                commitmentId=commitment_id,
                                label=label,
                                date=occurred,
                                amount=str(occurrence_amount),
                                dueByAsOf=occurred <= as_of,
                                certainty=certainty,
                            )
                        )
                continue
            excluded_commitments.append(
                ExcludedCommitment(
                    commitmentId=commitment_id, label=label, accountId=account_id, reason=reason
                )
            )

        accounts_out = []
        for account_id in ordered_ids:
            slot = included[account_id]
            points = project_balances(slot["start"], slot["occurrences"], as_of)
            accounts_out.append(
                ForecastAccount(
                    accountId=account_id,
                    accountName=slot["name"],
                    accountClass=slot["accountClass"],
                    currency=slot["currency"],
                    startBalance=str(slot["start"]),
                    balanceDate=slot["balanceDate"],
                    points=[
                        ForecastPoint(days=days, date=point_date, balance=str(balance))
                        for days, point_date, balance in points
                    ],
                    occurrences=sorted(slot["shown"], key=lambda o: (o.date, o.label)),
                )
            )
    return ForecastResponse(
        asOf=as_of,
        accounts=accounts_out,
        excludedAccounts=excluded_accounts,
        excludedCommitments=excluded_commitments,
    )
