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
from ..forecast import occurrence_dates, project_balances
from ..money import _money
from .dashboard import VALUATION_PERIOD_FOR_ACCOUNT

router = APIRouter(tags=["plans"])

Cadence = Literal["once", "monthly", "annual"]
ExcludedAccountReason = Literal["not_ledger_valued", "no_balance_date", "balance_after_as_of"]
ExcludedCommitmentReason = Literal["account_closed", "account_excluded", "currency_mismatch"]


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


# Column order shared by every commitment read; _commitment_row() maps one result tuple.
_COMMITMENT_COLUMNS = """c.id, c.account_id, a.name, c.label, c.amount, c.currency, c.cadence::text,
                         c.first_date, c.end_date, c.archived_at, c.created_at, c.updated_at"""
_COMMITMENT_FROM = """FROM commitment c
                       JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id"""


def _commitment_row(row: tuple[Any, ...]) -> CommitmentRow:
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
    )


def _row(cur: psycopg.Cursor[Any], household_id: UUID, commitment_id: UUID) -> CommitmentRow:
    cur.execute(
        f"SELECT {_COMMITMENT_COLUMNS} {_COMMITMENT_FROM} WHERE c.household_id = %s AND c.id = %s",
        (household_id, commitment_id),
    )
    row = cur.fetchone()
    assert row is not None
    return _commitment_row(row)


@router.get("/commitments", response_model=CommitmentsResponse)
def list_commitments(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    includeArchived: Annotated[bool, Query()] = False,
) -> CommitmentsResponse:
    with connect() as conn, conn.cursor() as cur:
        archived_filter = "" if includeArchived else " AND c.archived_at IS NULL"
        cur.execute(
            f"""SELECT {_COMMITMENT_COLUMNS} {_COMMITMENT_FROM}
                 WHERE c.household_id = %s{archived_filter}
                 ORDER BY a.account_class, a.country, a.display_order, a.name,
                          c.first_date, c.label""",
            (owner.household_id,),
        )
        rows = [_commitment_row(r) for r in cur.fetchall()]
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
                          first_date, end_date
                     FROM commitment WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                (eid, eaccount, elabel, eamount, ecurrency, ecadence, efirst, eend) = existing
                if (
                    UUID(str(eaccount)) == body.accountId
                    and elabel == body.label
                    and Decimal(eamount) == Decimal(body.amount)
                    and str(ecurrency).strip() == body.currency
                    and ecadence == body.cadence
                    and efirst == body.firstDate
                    and eend == body.endDate
                ):
                    response.status_code = status.HTTP_200_OK
                    return _row(cur, owner.household_id, UUID(str(eid)))
                raise HTTPException(
                    status_code=409, detail="idempotency key reused with a different request"
                )
            _assert_account(cur, owner.household_id, body.accountId, body.currency)
            cur.execute(
                """INSERT INTO commitment (household_id, idempotency_key, account_id, label,
                                            amount, currency, cadence, first_date, end_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::commitment_cadence, %s, %s)
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
                ),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            return _row(cur, owner.household_id, UUID(str(created[0])))
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None


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
                """SELECT archived_at FROM commitment
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, commitment_id),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="commitment not found")
            if found[0] is not None:
                raise HTTPException(status_code=409, detail="an archived commitment is fixed")
            _assert_account(cur, owner.household_id, body.accountId, body.currency)
            cur.execute(
                """UPDATE commitment
                      SET account_id = %s, label = %s, amount = %s, currency = %s,
                          cadence = %s::commitment_cadence, first_date = %s, end_date = %s,
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
           c.cadence::text, c.first_date, c.end_date
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
        ) in cur.fetchall():
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
                dates = occurrence_dates(
                    cadence, first_date, end_date, slot["balanceDate"], through
                )
                for occurred in dates:
                    slot["occurrences"].append((occurred, amount_decimal))
                    slot["shown"].append(
                        ForecastOccurrence(
                            commitmentId=commitment_id,
                            label=label,
                            date=occurred,
                            amount=str(amount_decimal),
                            dueByAsOf=occurred <= as_of,
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
