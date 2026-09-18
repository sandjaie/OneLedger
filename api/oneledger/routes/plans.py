"""Phase 1.7 Task 2: commitments — the owner's one-off, monthly or annual signed amount
assumptions (V43) — and the read-only 30/60/90-day forecast built from them. Commitments carry
no SECURITY DEFINER write path (spec "Commitments (V43)": an owner assumption, not an evidenced
fact); every write here locks the household row and filters explicitly by household id, the same
owner-scoping convention as `routes/accounts.py`. `GET /forecast` opens `SET TRANSACTION READ
ONLY` before any other statement and calls `forecast.py`'s pure functions to do the math — it
posts nothing, changes no balance, and reads through the same valuation-period selection as
`GET /dashboard` (`dashboard.VALUATION_PERIOD_FOR_ACCOUNT`)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..commitments import Certainty, CommitmentStatus, commitment_progress
from ..db import IDEMPOTENCY_REUSED, connect
from ..forecast import (
    aggregate_currency,
    is_occurrence,
    lowest_balance,
    next_occurrence,
    occurrence_dates,
    project_balances,
    select_funding_accounts,
)
from ..money import _money, chart_coordinates
from ..text import normalize_bounded
from .actions import action_list
from .coverage import coverage_rows
from .dashboard import VALUATION_PERIOD_FOR_ACCOUNT

router = APIRouter(tags=["plans"])

Cadence = Literal["once", "monthly", "annual"]
CommitmentSource = Literal["owner", "card_statement", "register_record"]
ExcludedAccountReason = Literal["not_ledger_valued", "no_balance_date", "balance_after_as_of"]
ExcludedCommitmentReason = Literal[
    "account_closed", "account_excluded", "currency_mismatch", "card_settlement_not_projected"
]
ForecastOccurrenceKind = Literal["commitment", "card_settlement"]

# Phase 3.2 spec rule 6: a member is stale once its balance is this many days old.
_STALE_BALANCE_AGE_DAYS = 35
# Phase 3.2 spec rule 2: an unapplied occurrence list is capped per commitment.
_UNAPPLIED_OCCURRENCE_CAP = 12


def _normalize_label(value: str) -> str:
    return normalize_bounded(value, "label", 80)


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
    # Task 2: planning a card statement's due amount. Fixed at creation — CommitmentUpdate carries
    # no such field, so an existing commitment's source can never change.
    sourceCardStatementId: UUID | None = None
    # Phase 3.4 Task 2: planning a register record's premium, expected proceeds or expected
    # reimbursement — the same fixed-at-creation shape as the card source above.
    sourceRegisterRecordId: UUID | None = None

    @model_validator(mode="after")
    def _one_source(self) -> CommitmentCreate:
        if self.sourceCardStatementId is not None and self.sourceRegisterRecordId is not None:
            raise ValueError(_REGISTER_SOURCE_TWO)
        return self


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
    source: CommitmentSource
    sourceCardStatementId: UUID | None
    sourceCardAccountName: str | None
    sourceRegisterRecordId: UUID | None
    sourceRegisterTitle: str | None


class CommitmentsResponse(BaseModel):
    commitments: list[CommitmentRow]


class ForecastPoint(BaseModel):
    days: int
    date: date
    balance: str
    # Phase 3.2 spec rule 3: the same point over confirmed occurrences only.
    confirmedBalance: str
    chartBalanceCoordinate: int | None = None
    chartConfirmedCoordinate: int | None = None


def _attach_forecast_coordinates(points: list[ForecastPoint]) -> list[ForecastPoint]:
    """Use one Decimal scale for both series within this account or currency only."""
    values = {
        (point.days, series): Decimal(value)
        for point in points
        for series, value in (("projected", point.balance), ("confirmed", point.confirmedBalance))
    }
    coordinates = chart_coordinates(values)
    for point in points:
        point.chartBalanceCoordinate = coordinates[(point.days, "projected")]
        point.chartConfirmedCoordinate = coordinates[(point.days, "confirmed")]
    return points


class ForecastOccurrence(BaseModel):
    commitmentId: UUID
    label: str
    date: date
    amount: str
    dueByAsOf: bool
    certainty: Certainty
    # Phase 3.2 spec rule 9: "card_settlement" only for a mirror occurrence (rule 1).
    kind: ForecastOccurrenceKind


class LowestPoint(BaseModel):
    balance: str
    date: date


class FundingAccount(BaseModel):
    accountId: UUID
    accountName: str
    lowestBalance: str


class Shortfall(BaseModel):
    amount: str
    date: date
    fundingAccounts: list[FundingAccount]


class ForecastAccount(BaseModel):
    accountId: UUID
    accountName: str
    accountClass: Literal["asset", "liability"]
    currency: str
    startBalance: str
    balanceDate: date
    points: list[ForecastPoint]
    occurrences: list[ForecastOccurrence]
    # Phase 3.2 spec rule 2: occurrences on or after next_due_date and on or before balanceDate —
    # already possibly in the ledger, not applied, listed so the owner settles or dismisses them.
    unappliedOccurrences: list[ForecastOccurrence]
    lowest: LowestPoint
    lowestConfirmed: LowestPoint
    # Phase 3.2 spec rule 6: asOf - balanceDate, and the missing/partial cell count over the
    # three calendar months ending with asOf's month from coverage_rows() (null: no such row).
    balanceAgeDays: int
    coverageGapMonths: int | None
    # Phase 3.2 spec rule 5: only ever set on an asset account whose lowest balance is negative.
    shortfall: Shortfall | None = None


class ExcludedAccount(BaseModel):
    accountId: UUID
    accountName: str
    reason: ExcludedAccountReason


class ExcludedCommitment(BaseModel):
    commitmentId: UUID
    label: str
    accountId: UUID
    reason: ExcludedCommitmentReason


class ForecastCurrency(BaseModel):
    """Phase 3.2 spec rule 7: one entry per currency with at least one member (an included,
    active liquid asset account or credit card); no conversion, ordered by currency code."""

    currency: str
    accountIds: list[UUID]
    startBalance: str
    points: list[ForecastPoint]
    lowest: LowestPoint
    lowestConfirmed: LowestPoint
    excludedAccountIds: list[UUID]
    staleAccountIds: list[UUID]


class UnplannedCardDue(BaseModel):
    """Phase 3.2 spec rule 8: a card statement meeting the Phase 3.1 `card_payment_unplanned`
    rule regardless of snooze or dismissal marks, due within the forecast window."""

    cardStatementId: UUID
    accountId: UUID
    accountName: str
    currency: str
    statementBalance: str
    paymentDueDate: date


class ForecastResponse(BaseModel):
    asOf: date
    accounts: list[ForecastAccount]
    excludedAccounts: list[ExcludedAccount]
    excludedCommitments: list[ExcludedCommitment]
    currencies: list[ForecastCurrency]
    unplannedCardDues: list[UnplannedCardDue]


def _translate_database_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_commitment_idempotency":
        return HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
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
    if constraint == "fk_commitment_card_statement_scope":
        return HTTPException(status_code=404, detail="card statement not found")
    if constraint == "commitment_card_source_is_one_payment":
        return HTTPException(status_code=422, detail=_CARD_SOURCE_SCHEDULE)
    if constraint == "uq_commitment_open_card_statement":
        return HTTPException(
            status_code=409, detail="a payment is already planned for this card statement"
        )
    if constraint == "fk_commitment_register_scope":
        return HTTPException(status_code=404, detail="record not found")
    if constraint == "commitment_has_one_source":
        return HTTPException(status_code=422, detail=_REGISTER_SOURCE_TWO)
    if constraint == "uq_commitment_open_register_record":
        return HTTPException(status_code=409, detail=_RECORD_ALREADY_PLANNED)
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


_CARD_SOURCE_SCHEDULE = "a card-sourced commitment must be a one-off payment with a negative amount"
_CARD_SOURCE_ACCOUNT = "choose the account that pays the card"
_CARD_SOURCE_CURRENCY = "the paying account must be in the card statement's currency"


def _card_statement_facts(
    cur: psycopg.Cursor[Any], household_id: UUID, statement_id: UUID
) -> tuple[UUID, str, Decimal]:
    """The statement's own account, currency and what it states is due — 404 unless it is this
    household's (spec "Card dues and actions (Task 2)")."""
    cur.execute(
        """SELECT account_id, currency, statement_balance
             FROM card_statement WHERE household_id = %s AND id = %s""",
        (household_id, statement_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="card statement not found")
    return UUID(str(found[0])), str(found[1]).strip(), Decimal(found[2])


def _assert_card_source_fields(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    card_account_id: UUID,
    card_currency: str,
    account_id: UUID,
    currency: str,
    cadence: Cadence,
    amount: str,
) -> None:
    """The account, currency, cadence and sign rules a card-sourced commitment always carries —
    checked on create (alongside the statement's own facts, `_card_statement_facts`) and
    re-applied on every update of an already card-sourced commitment."""
    if cadence != "once" or Decimal(amount) >= 0:
        raise HTTPException(status_code=422, detail=_CARD_SOURCE_SCHEDULE)
    cur.execute(
        "SELECT account_class::text FROM account WHERE household_id = %s AND id = %s",
        (household_id, account_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="account not found")
    if account_id == card_account_id or found[0] != "asset":
        raise HTTPException(status_code=422, detail=_CARD_SOURCE_ACCOUNT)
    if currency != card_currency:
        raise HTTPException(status_code=422, detail=_CARD_SOURCE_CURRENCY)


_REGISTER_SOURCE_TWO = (
    "a commitment must not carry both a card statement and a register record source"
)
_RECORD_ALREADY_PLANNED = "money is already planned for this record"
_RECORD_NOT_PLANNABLE = "this record does not plan money"
_POLICY_NO_PREMIUM = "this policy has no premium"
_POLICY_SCHEDULE = (
    "a policy-sourced commitment must use its premium's cadence with a negative amount"
)
_POLICY_CURRENCY = "the paying account must be in the policy's currency"
_DEPOSIT_NO_PROCEEDS = "this deposit has no expected proceeds"
_DEPOSIT_SCHEDULE = "a deposit-sourced commitment must be a one-off payment with a positive amount"
_DEPOSIT_CURRENCY = "the receiving account must be in the deposit's currency"
_DEPOSIT_OWN_ACCOUNT = "choose the account that receives the proceeds"
_CLAIM_SCHEDULE = "a claim-sourced commitment must be a one-off payment with a positive amount"
_CLAIM_CURRENCY = "the receiving account must be in the claim's currency"
_COMPLETED_CLAIM = "a completed claim cannot plan money"
_NOT_AN_ASSET = "choose an active asset account to receive the money"
_ARCHIVED_RECORD_SOURCE = "an archived register record cannot plan money"


def _assert_asset_account(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID, currency: str
) -> None:
    """The receiving-account rule a deposit's proceeds or a claim's reimbursement always carries:
    an active asset account in the record's currency (spec "Task 2 — documents on records and
    commitments from records")."""
    cur.execute(
        "SELECT is_active, account_class::text, currency FROM account "
        "WHERE household_id = %s AND id = %s",
        (household_id, account_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="account not found")
    is_active, account_class, account_currency = found
    if not is_active or account_class != "asset" or str(account_currency).strip() != currency:
        raise HTTPException(status_code=422, detail=_NOT_AN_ASSET)


def _assert_register_source_fields(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    record_id: UUID,
    account_id: UUID,
    currency: str,
    cadence: Cadence,
    amount: str,
) -> None:
    """Every kind-specific rule a register-sourced commitment carries (spec "Task 2 — documents on
    records and commitments from records") — checked on create and re-applied on every update of
    an already register-sourced commitment, the same convention as `_assert_card_source_fields`."""
    cur.execute(
        """SELECT kind::text, archived_at, amount, currency, premium_amount, premium_cadence::text,
                  account_id, completed_at
             FROM register_record WHERE household_id = %s AND id = %s""",
        (household_id, record_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="record not found")
    (
        kind,
        archived_at,
        record_amount,
        record_currency,
        premium_amount,
        premium_cadence,
        deposit_account_id,
        completed_at,
    ) = found
    if archived_at is not None:
        raise HTTPException(status_code=409, detail=_ARCHIVED_RECORD_SOURCE)
    record_currency_str = None if record_currency is None else str(record_currency).strip()
    amount_decimal = Decimal(amount)

    if kind == "insurance_policy":
        if premium_amount is None:
            raise HTTPException(status_code=422, detail=_POLICY_NO_PREMIUM)
        if cadence != premium_cadence or amount_decimal >= 0:
            raise HTTPException(status_code=422, detail=_POLICY_SCHEDULE)
        if currency != record_currency_str:
            raise HTTPException(status_code=422, detail=_POLICY_CURRENCY)
        _assert_account(cur, household_id, account_id, currency)
    elif kind == "deposit":
        if record_amount is None or record_currency_str is None:
            raise HTTPException(status_code=422, detail=_DEPOSIT_NO_PROCEEDS)
        if cadence != "once" or amount_decimal <= 0:
            raise HTTPException(status_code=422, detail=_DEPOSIT_SCHEDULE)
        if currency != record_currency_str:
            raise HTTPException(status_code=422, detail=_DEPOSIT_CURRENCY)
        if account_id == UUID(str(deposit_account_id)):
            raise HTTPException(status_code=422, detail=_DEPOSIT_OWN_ACCOUNT)
        _assert_asset_account(cur, household_id, account_id, currency)
    elif kind == "claim":
        if completed_at is not None:
            raise HTTPException(status_code=409, detail=_COMPLETED_CLAIM)
        if cadence != "once" or amount_decimal <= 0:
            raise HTTPException(status_code=422, detail=_CLAIM_SCHEDULE)
        if currency != record_currency_str:
            raise HTTPException(status_code=422, detail=_CLAIM_CURRENCY)
        _assert_asset_account(cur, household_id, account_id, currency)
    else:
        raise HTTPException(status_code=422, detail=_RECORD_NOT_PLANNABLE)


def _replay_source_matches(stored: object, given: UUID | None) -> bool:
    if given is None:
        return stored is None
    return stored is not None and UUID(str(stored)) == given


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


# Column order shared by every commitment read; _commitment_row() maps one result tuple. The
# card-statement and register-record sources are each read through a left join — most
# commitments have neither.
_COMMITMENT_COLUMNS = """c.id, c.account_id, a.name, c.label, c.amount, c.currency, c.cadence::text,
                         c.first_date, c.end_date, c.archived_at, c.created_at, c.updated_at,
                         c.certainty::text, c.next_due_date, c.source_card_statement_id,
                         card_account.name, c.source_register_record_id, source_record.title"""
_COMMITMENT_FROM = """FROM commitment c
                       JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
                       LEFT JOIN card_statement cs
                         ON cs.household_id = c.household_id
                        AND cs.id = c.source_card_statement_id
                       LEFT JOIN account card_account
                         ON card_account.household_id = cs.household_id
                        AND card_account.id = cs.account_id
                       LEFT JOIN register_record source_record
                         ON source_record.household_id = c.household_id
                        AND source_record.id = c.source_register_record_id"""


def _commitment_row(
    row: tuple[Any, ...], progress: tuple[CommitmentStatus, bool, str, str | None]
) -> CommitmentRow:
    status, partly_paid, paid_toward_next, remaining_next = progress
    source_card_statement_id = row[14]
    source_register_record_id = row[16]
    source: CommitmentSource
    if source_card_statement_id is not None:
        source = "card_statement"
    elif source_register_record_id is not None:
        source = "register_record"
    else:
        source = "owner"
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
        source=source,
        sourceCardStatementId=source_card_statement_id,
        sourceCardAccountName=row[15],
        sourceRegisterRecordId=source_register_record_id,
        sourceRegisterTitle=row[17],
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
    progress = commitment_progress(
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
        progress = commitment_progress(
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
                          first_date, end_date, certainty::text, next_due_date,
                          source_card_statement_id, source_register_record_id
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
                    esource,
                    eregister,
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
                    and _replay_source_matches(esource, body.sourceCardStatementId)
                    and _replay_source_matches(eregister, body.sourceRegisterRecordId)
                ):
                    response.status_code = status.HTTP_200_OK
                    return _row(cur, owner.household_id, UUID(str(eid)))
                raise HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
            if body.sourceCardStatementId is not None:
                card_account_id, card_currency, statement_balance = _card_statement_facts(
                    cur, owner.household_id, body.sourceCardStatementId
                )
                if statement_balance <= 0:
                    raise HTTPException(
                        status_code=422, detail="nothing is due on this card statement"
                    )
                _assert_card_source_fields(
                    cur,
                    owner.household_id,
                    card_account_id,
                    card_currency,
                    body.accountId,
                    body.currency,
                    body.cadence,
                    body.amount,
                )
            if body.sourceRegisterRecordId is not None:
                _assert_register_source_fields(
                    cur,
                    owner.household_id,
                    body.sourceRegisterRecordId,
                    body.accountId,
                    body.currency,
                    body.cadence,
                    body.amount,
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
                                            certainty, next_due_date, source_card_statement_id,
                                            source_register_record_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::commitment_cadence, %s, %s,
                           %s::commitment_certainty, %s, %s, %s)
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
                    body.sourceCardStatementId,
                    body.sourceRegisterRecordId,
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
                          amount, next_due_date, source_card_statement_id,
                          source_register_record_id
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
                stored_source_card_statement_id,
                stored_source_register_record_id,
            ) = found
            if archived_at is not None:
                raise HTTPException(status_code=409, detail="an archived commitment is fixed")
            if stored_source_card_statement_id is not None:
                card_account_id, card_currency, _balance = _card_statement_facts(
                    cur, owner.household_id, UUID(str(stored_source_card_statement_id))
                )
                _assert_card_source_fields(
                    cur,
                    owner.household_id,
                    card_account_id,
                    card_currency,
                    body.accountId,
                    body.currency,
                    body.cadence,
                    body.amount,
                )
            elif stored_source_register_record_id is not None:
                _assert_register_source_fields(
                    cur,
                    owner.household_id,
                    UUID(str(stored_source_register_record_id)),
                    body.accountId,
                    body.currency,
                    body.cadence,
                    body.amount,
                )
            cur.execute(
                """SELECT count(*) FROM commitment_resolution
                    WHERE household_id = %s AND commitment_id = %s""",
                (owner.household_id, commitment_id),
            )
            counted = cur.fetchone()
            assert counted is not None
            has_resolution = counted[0] > 0

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
                    closed_row = cur.fetchone()
                    assert closed_row is not None
                    latest_closed = closed_row[0]
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
    SELECT a.id, a.name, a.account_class::text, a.account_type::text, a.is_liquid, a.currency,
           a.current_balance, a.current_balance_as_of, period.valuation_mode::text
      FROM account a
      JOIN account_valuation_period period ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
     WHERE a.household_id = %s AND a.is_active
     ORDER BY {_ACCOUNT_ORDER}"""

# The card-sourced commitment's own card account (its currency is read straight off `included`,
# a live fact of this request, not a second column here) — NULL when the commitment is not
# card-sourced.
FORECAST_COMMITMENTS = """
    SELECT c.id, c.account_id, a.is_active, a.currency, c.label, c.amount, c.currency,
           c.cadence::text, c.first_date, c.end_date, c.next_due_date, c.certainty::text,
           c.source_card_statement_id, cs.account_id
      FROM commitment c
      JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
      LEFT JOIN card_statement cs
        ON cs.household_id = c.household_id AND cs.id = c.source_card_statement_id
     WHERE c.household_id = %s AND c.archived_at IS NULL"""


def _is_currency_member(account_class: str, account_type: str, is_liquid: bool) -> bool:
    """Phase 3.2 spec rule 7: an active liquid asset account or a credit card."""
    return (account_class == "asset" and is_liquid) or account_type == "credit_card"


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
        # Every account's currency/class/type/liquidity, included or not — rule 7's
        # excludedAccountIds needs the facts of an account that never entered `included`.
        account_facts: dict[UUID, dict[str, Any]] = {}
        for (
            account_id,
            name,
            account_class,
            account_type,
            is_liquid,
            currency,
            balance,
            balance_as_of,
            mode,
        ) in cur.fetchall():
            account_id = UUID(str(account_id))
            currency = str(currency).strip()
            account_facts[account_id] = {
                "currency": currency,
                "accountClass": account_class,
                "accountType": account_type,
                "isLiquid": is_liquid,
            }
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
                "accountType": account_type,
                "isLiquid": is_liquid,
                "currency": currency,
                "start": Decimal(balance),
                "balanceDate": balance_date,
                # (date, amount, certainty) triples — applied occurrences, including any card
                # settlement mirror (rule 1); used for both this account's own points/lowest and
                # its currency's aggregation.
                "occurrences": [],
                "shown": [],
                "unapplied": [],
            }
            ordered_ids.append(account_id)

        cur.execute(FORECAST_COMMITMENTS, (owner.household_id,))
        commitment_rows = cur.fetchall()
        remaining_by_commitment = {
            commitment_id: remaining
            for commitment_id, (_status, _partly, _paid, remaining) in commitment_progress(
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
            source_card_statement_id,
            card_account_id,
        ) in commitment_rows:
            commitment_id = UUID(str(commitment_id))
            account_id = UUID(str(account_id))
            currency = str(currency).strip()
            account_currency = str(account_currency).strip()
            card_account_id = UUID(str(card_account_id)) if card_account_id is not None else None
            if not account_active:
                reason: ExcludedCommitmentReason = "account_closed"
            elif account_id not in included:
                reason = "account_excluded"
            elif currency != account_currency:
                reason = "currency_mismatch"
            else:
                slot = included[account_id]
                amount_decimal = Decimal(amount)
                remaining_next = remaining_by_commitment.get(commitment_id)
                mirror_not_projected = False
                if next_due_date is not None:
                    # Rule 2: on or after next_due_date, on or before the account's own balance
                    # date — already possibly in the ledger, so not applied, capped per commitment.
                    unapplied_dates = occurrence_dates(
                        cadence,
                        first_date,
                        end_date,
                        next_due_date - timedelta(days=1),
                        slot["balanceDate"],
                    )[:_UNAPPLIED_OCCURRENCE_CAP]
                    for occurred in unapplied_dates:
                        if occurred == next_due_date:
                            if remaining_next is None or Decimal(remaining_next) == 0:
                                continue
                            occurrence_amount = Decimal(remaining_next)
                        else:
                            occurrence_amount = amount_decimal
                        slot["unapplied"].append(
                            ForecastOccurrence(
                                commitmentId=commitment_id,
                                label=label,
                                date=occurred,
                                amount=str(occurrence_amount),
                                dueByAsOf=True,
                                certainty=certainty,
                                kind="commitment",
                            )
                        )

                    # Rule 4's window: applied occurrences are strictly after the balance date.
                    dates = occurrence_dates(
                        cadence, first_date, end_date, slot["balanceDate"], through
                    )
                    for occurred in dates:
                        if occurred < next_due_date:
                            continue
                        if occurred == next_due_date:
                            if remaining_next is None or Decimal(remaining_next) == 0:
                                continue
                            occurrence_amount = Decimal(remaining_next)
                        else:
                            occurrence_amount = amount_decimal
                        slot["occurrences"].append((occurred, occurrence_amount, certainty))
                        slot["shown"].append(
                            ForecastOccurrence(
                                commitmentId=commitment_id,
                                label=label,
                                date=occurred,
                                amount=str(occurrence_amount),
                                dueByAsOf=occurred <= as_of,
                                certainty=certainty,
                                kind="commitment",
                            )
                        )
                        if source_card_statement_id is not None:
                            # Rule 1: mirror onto the card account only when it is included, in
                            # the same currency, and its own balance is not already past this
                            # payment date.
                            mirror_target = (
                                included.get(card_account_id)
                                if card_account_id is not None
                                else None
                            )
                            if (
                                mirror_target is not None
                                and mirror_target["currency"] == currency
                                and occurred > mirror_target["balanceDate"]
                            ):
                                mirror_amount = -occurrence_amount
                                mirror_target["occurrences"].append(
                                    (occurred, mirror_amount, certainty)
                                )
                                mirror_target["shown"].append(
                                    ForecastOccurrence(
                                        commitmentId=commitment_id,
                                        label=label,
                                        date=occurred,
                                        amount=str(mirror_amount),
                                        dueByAsOf=occurred <= as_of,
                                        certainty=certainty,
                                        kind="card_settlement",
                                    )
                                )
                            else:
                                mirror_not_projected = True
                if mirror_not_projected:
                    excluded_commitments.append(
                        ExcludedCommitment(
                            commitmentId=commitment_id,
                            label=label,
                            accountId=account_id,
                            reason="card_settlement_not_projected",
                        )
                    )
                continue
            excluded_commitments.append(
                ExcludedCommitment(
                    commitmentId=commitment_id, label=label, accountId=account_id, reason=reason
                )
            )

        # Coverage.py's own row order/shape (rule 6): one lookup per household, not per account.
        coverage_by_account = {
            row.accountId: row
            for row in coverage_rows(cur, owner.household_id, as_of)
            if row.accountId is not None
        }

        def _points(
            start: Decimal, occurrences: list[tuple[date, Decimal, str]], point_as_of: date
        ) -> list[ForecastPoint]:
            all_2tuple = [(d, a) for d, a, _c in occurrences]
            confirmed_2tuple = [(d, a) for d, a, c in occurrences if c == "confirmed"]
            all_points = project_balances(start, all_2tuple, point_as_of)
            confirmed_points = project_balances(start, confirmed_2tuple, point_as_of)
            return _attach_forecast_coordinates(
                [
                    ForecastPoint(
                        days=days, date=point_date, balance=str(balance), confirmedBalance=str(cbal)
                    )
                    for (days, point_date, balance), (_, _, cbal) in zip(
                        all_points, confirmed_points, strict=True
                    )
                ]
            )

        accounts_out: dict[UUID, ForecastAccount] = {}
        account_lowest: dict[UUID, Decimal] = {}
        for account_id in ordered_ids:
            slot = included[account_id]
            all_2tuple = [(d, a) for d, a, _c in slot["occurrences"]]
            confirmed_2tuple = [(d, a) for d, a, c in slot["occurrences"] if c == "confirmed"]
            lowest_bal, lowest_date = lowest_balance(slot["start"], slot["balanceDate"], all_2tuple)
            lowest_conf_bal, lowest_conf_date = lowest_balance(
                slot["start"], slot["balanceDate"], confirmed_2tuple
            )
            balance_age_days = (as_of - slot["balanceDate"]).days
            coverage_row = coverage_by_account.get(account_id)
            gap_months = (
                None
                if coverage_row is None
                else sum(
                    1 for cell in coverage_row.months[-3:] if cell.status in ("missing", "partial")
                )
            )
            account_lowest[account_id] = lowest_bal
            accounts_out[account_id] = ForecastAccount(
                accountId=account_id,
                accountName=slot["name"],
                accountClass=slot["accountClass"],
                currency=slot["currency"],
                startBalance=str(slot["start"]),
                balanceDate=slot["balanceDate"],
                points=_points(slot["start"], slot["occurrences"], as_of),
                occurrences=sorted(slot["shown"], key=lambda o: (o.date, o.label)),
                unappliedOccurrences=sorted(slot["unapplied"], key=lambda o: (o.date, o.label)),
                lowest=LowestPoint(balance=str(lowest_bal), date=lowest_date),
                lowestConfirmed=LowestPoint(balance=str(lowest_conf_bal), date=lowest_conf_date),
                balanceAgeDays=balance_age_days,
                coverageGapMonths=gap_months,
            )

        # Rule 5: a shortfall only on an asset account whose lowest balance went negative, funded
        # by the other included liquid asset accounts in the same currency that can cover it on
        # their own — informational, nothing here moves money.
        for account_id in ordered_ids:
            slot = included[account_id]
            lowest_bal = account_lowest[account_id]
            if slot["accountClass"] != "asset" or lowest_bal >= 0:
                continue
            amount_needed = -lowest_bal
            candidates = [
                (other_id, included[other_id]["name"], account_lowest[other_id])
                for other_id in ordered_ids
                if other_id != account_id
                and included[other_id]["currency"] == slot["currency"]
                and included[other_id]["accountClass"] == "asset"
                and included[other_id]["isLiquid"]
            ]
            funding = select_funding_accounts(amount_needed, candidates)
            accounts_out[account_id].shortfall = Shortfall(
                amount=str(amount_needed),
                date=accounts_out[account_id].lowest.date,
                fundingAccounts=[
                    FundingAccount(accountId=fid, accountName=fname, lowestBalance=str(flow))
                    for fid, fname, flow in funding
                ],
            )

        # Rule 7: one entry per currency with at least one member; excluded/stale accounts named.
        currencies_out: list[ForecastCurrency] = []
        member_currencies = sorted(
            {
                included[aid]["currency"]
                for aid in ordered_ids
                if _is_currency_member(
                    included[aid]["accountClass"],
                    included[aid]["accountType"],
                    included[aid]["isLiquid"],
                )
            }
        )
        for currency in member_currencies:
            member_ids = [
                aid
                for aid in ordered_ids
                if included[aid]["currency"] == currency
                and _is_currency_member(
                    included[aid]["accountClass"],
                    included[aid]["accountType"],
                    included[aid]["isLiquid"],
                )
            ]
            start_sum = sum((included[aid]["start"] for aid in member_ids), Decimal(0))
            all_occurrences = [
                (d, a) for aid in member_ids for d, a, _c in included[aid]["occurrences"]
            ]
            confirmed_occurrences = [
                (d, a)
                for aid in member_ids
                for d, a, c in included[aid]["occurrences"]
                if c == "confirmed"
            ]
            folded_start, rest = aggregate_currency(start_sum, all_occurrences, as_of)
            folded_confirmed_start, rest_confirmed = aggregate_currency(
                start_sum, confirmed_occurrences, as_of
            )
            all_points = project_balances(folded_start, rest, as_of)
            confirmed_points = project_balances(folded_confirmed_start, rest_confirmed, as_of)
            points = _attach_forecast_coordinates(
                [
                    ForecastPoint(
                        days=days, date=point_date, balance=str(balance), confirmedBalance=str(cbal)
                    )
                    for (days, point_date, balance), (_, _, cbal) in zip(
                        all_points, confirmed_points, strict=True
                    )
                ]
            )
            lowest_bal, lowest_date = lowest_balance(folded_start, as_of, rest)
            lowest_conf_bal, lowest_conf_date = lowest_balance(
                folded_confirmed_start, as_of, rest_confirmed
            )
            excluded_ids = [
                aid
                for aid, facts in account_facts.items()
                if aid not in included
                and facts["currency"] == currency
                and _is_currency_member(
                    facts["accountClass"], facts["accountType"], facts["isLiquid"]
                )
            ]
            stale_ids = [
                aid
                for aid in member_ids
                if accounts_out[aid].balanceAgeDays > _STALE_BALANCE_AGE_DAYS
            ]
            currencies_out.append(
                ForecastCurrency(
                    currency=currency,
                    accountIds=member_ids,
                    startBalance=str(folded_start),
                    points=points,
                    lowest=LowestPoint(balance=str(lowest_bal), date=lowest_date),
                    lowestConfirmed=LowestPoint(
                        balance=str(lowest_conf_bal), date=lowest_conf_date
                    ),
                    excludedAccountIds=excluded_ids,
                    staleAccountIds=stale_ids,
                )
            )

        # Rule 8: every card statement meeting the Phase 3.1 unplanned-due rule, marks ignored,
        # due within the forecast window.
        unplanned_card_dues: list[UnplannedCardDue] = []
        for item in action_list(cur, owner.household_id, as_of, apply_marks=False):
            if item.kind != "card_due" or item.dueDate is None or item.dueDate > through:
                continue
            assert item.cardStatementId is not None
            assert item.accountId is not None
            assert item.accountName is not None
            assert item.currency is not None
            assert item.amount is not None
            unplanned_card_dues.append(
                UnplannedCardDue(
                    cardStatementId=item.cardStatementId,
                    accountId=item.accountId,
                    accountName=item.accountName,
                    currency=item.currency,
                    statementBalance=item.amount,
                    paymentDueDate=item.dueDate,
                )
            )

    return ForecastResponse(
        asOf=as_of,
        accounts=[accounts_out[aid] for aid in ordered_ids],
        excludedAccounts=excluded_accounts,
        excludedCommitments=excluded_commitments,
        currencies=currencies_out,
        unplannedCardDues=unplanned_card_dues,
    )
