# api/oneledger/routes/accounts.py
"""M1a: the account inventory. Balances are native here; translating one needs an as-of-date
rate and a coverage label, which is M9a's contract (multi-currency-and-fx.html section 6)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..identifiers import (
    CompleteIdentifier,
    Identifier,
    IdentifierKind,
    IdentifierValueError,
    complete_identifier,
    masked_identifier,
)

router = APIRouter(tags=["accounts"])

# Money crosses the wire as a decimal string in both directions. api-contracts.html section 6.
MONEY = re.compile(r"-?\d{1,16}(\.\d{1,4})?")
# Same set as set_account_class() in V3. A second copy is the drift risk we accept so a
# positive liability cannot increase net worth before the trigger runs.
LIABILITY_TYPES = frozenset(
    {
        "credit_card",
        "personal_loan",
        "home_loan",
        "auto_loan",
        "education_loan",
        "gold_loan",
        "loan_other",
        "payable",
    }
)


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ValueError("money must be a decimal string with at most four decimal places")
    return value


def _masked(value: str | None) -> str | None:
    """Validate the compatibility display-mask field; complete values use accountNumber."""
    if value is not None and sum(ch.isdigit() for ch in value) > 4:
        raise ValueError(
            "accountNumberMasked takes a mask, not an account number: "
            "at most four digits may be visible"
        )
    return value


def _identifier_kind(account_type: str) -> IdentifierKind:
    return "card" if account_type == "credit_card" else "bank_account"


def _body_identifier(body: AccountCreate | AccountUpdate) -> Identifier | None:
    kind = _identifier_kind(body.accountType)
    try:
        if body.accountNumber is not None:
            return complete_identifier(kind, body.accountNumber)
        if body.accountNumberMasked is not None:
            return masked_identifier(kind, body.accountNumberMasked)
    except IdentifierValueError as exc:
        raise ValueError(str(exc)) from None
    return None


def _out(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0]


def _account_write_error(exc: psycopg.Error) -> str:
    # Database exception text may echo rejected parameters. Return only stable
    # service-owned descriptions, including the established duplicate constraint.
    if exc.diag.constraint_name == "idx_account_identifier_active_normalized_value":
        return "account identity already exists (idx_account_identifier_active_normalized_value)"
    return "account fields violate a type, relationship or balance constraint"


def _liability_opening_must_not_increase_net_worth(account_type: str, opening: str) -> None:
    if account_type in LIABILITY_TYPES and Decimal(opening) > 0:
        raise ValueError("a liability opening balance must be zero or negative")


def _credit_card_needs_a_limit(account_type: str, credit_limit: str | None) -> None:
    if account_type == "credit_card" and credit_limit is None:
        raise ValueError("a credit card needs a credit limit")
    if account_type == "credit_card" and credit_limit is not None and Decimal(credit_limit) <= 0:
        raise ValueError("a credit limit must be positive")
    if account_type != "credit_card" and credit_limit is not None:
        raise ValueError("only a credit card carries a credit limit")


class AccountIdentifierRow(BaseModel):
    id: UUID
    kind: IdentifierKind
    masked: str
    canReveal: bool
    isActive: bool


class AccountRow(BaseModel):
    id: UUID
    name: str
    institutionId: UUID | None
    institutionName: str | None
    accountType: str
    accountClass: Literal["asset", "liability"]
    accountSubtype: str | None
    currency: str = Field(min_length=3, max_length=3)
    country: str = Field(min_length=2, max_length=2)
    accountNumberMasked: str | None
    openedOn: date | None
    closedOn: date | None
    isActive: bool
    includeInNetWorth: bool
    isLiquid: bool
    openingBalance: str
    openingBalanceDate: date
    openingBalanceNote: str | None
    openingBalanceConfirmedAt: datetime | None
    currentBalance: str
    currentBalanceAsOf: datetime | None
    creditLimit: str | None
    identifiers: list[AccountIdentifierRow]


class RevealedAccountIdentifier(BaseModel):
    status: Literal["revealed"]
    value: str = Field(repr=False)
    kind: IdentifierKind


class UnavailableAccountIdentifier(BaseModel):
    status: Literal["unavailable"]
    reason: Literal["source_mask_only"]


class AccountGroupTotal(BaseModel):
    """Computed by Postgres. Native per currency: an untagged converted total would be a dated
    position without its date."""

    accountClass: Literal["asset", "liability"]
    country: str
    currency: str
    total: str


class AccountsResponse(BaseModel):
    accounts: list[AccountRow]
    totals: list[AccountGroupTotal]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    institutionId: UUID | None = None
    # Validated by the account_type enum in the database rather than a second copy here that
    # would drift from it. An unknown value fails as 422.
    accountType: str = Field(min_length=1, max_length=40)
    accountSubtype: str | None = Field(default=None, max_length=40)
    currency: str = Field(min_length=3, max_length=3)
    country: str = Field(min_length=2, max_length=2)
    # The display mask remains accepted for existing clients. accountNumber is transient:
    # create/update derive the only two values that may cross into storage.
    accountNumberMasked: str | None = Field(default=None, max_length=32)
    accountNumber: str | None = Field(default=None, repr=False)
    openedOn: date | None = None
    includeInNetWorth: bool = True
    isLiquid: bool = True
    openingBalance: str
    openingBalanceDate: date
    openingBalanceNote: str | None = None
    creditLimit: str | None = None

    @field_validator("openingBalance", "creditLimit", mode="before")
    @classmethod
    def exact_decimal_string(cls, value: str | None) -> str | None:
        return _money(value)

    @field_validator("accountNumberMasked")
    @classmethod
    def masked_never_full(cls, value: str | None) -> str | None:
        return _masked(value)

    @model_validator(mode="after")
    def liability_opening_is_signed(self) -> AccountCreate:
        _liability_opening_must_not_increase_net_worth(self.accountType, self.openingBalance)
        _credit_card_needs_a_limit(self.accountType, self.creditLimit)
        _body_identifier(self)
        return self


class AccountUpdate(BaseModel):
    """Every editable field, always sent: a full replacement has no absent-versus-null ambiguity
    and the client always holds the whole row. Spec C2 requires type, currency, country, the
    opening anchor and credit limit to be correctable; account_class stays trigger-derived."""

    name: str = Field(min_length=1, max_length=120)
    institutionId: UUID | None = None
    accountType: str = Field(min_length=1, max_length=40)
    accountSubtype: str | None = Field(default=None, max_length=40)
    currency: str = Field(min_length=3, max_length=3)
    country: str = Field(min_length=2, max_length=2)
    accountNumberMasked: str | None = Field(default=None, max_length=32)
    accountNumber: str | None = Field(default=None, repr=False)
    openedOn: date | None = None
    closedOn: date | None = None
    includeInNetWorth: bool
    isLiquid: bool
    openingBalance: str
    openingBalanceDate: date
    openingBalanceNote: str | None = None
    creditLimit: str | None = None
    acknowledgeOpeningBalance: bool

    @field_validator("openingBalance", "creditLimit", mode="before")
    @classmethod
    def exact_decimal_string(cls, value: str | None) -> str | None:
        return _money(value)

    @field_validator("accountNumberMasked")
    @classmethod
    def masked_never_full(cls, value: str | None) -> str | None:
        return _masked(value)

    @model_validator(mode="after")
    def liability_opening_is_signed(self) -> AccountUpdate:
        _liability_opening_must_not_increase_net_worth(self.accountType, self.openingBalance)
        _credit_card_needs_a_limit(self.accountType, self.creditLimit)
        _body_identifier(self)
        return self


class InstitutionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    shortName: str | None = Field(default=None, max_length=40)
    country: str = Field(min_length=2, max_length=2)
    institutionType: str = Field(min_length=1, max_length=40)


class InstitutionRow(BaseModel):
    id: UUID
    name: str
    shortName: str | None
    country: str
    institutionType: str


COLUMNS = """a.id, a.name, a.institution_id, i.name, a.account_type::text, a.account_class::text,
             a.account_subtype, a.currency, a.country,
             (SELECT ai.masked_value
                FROM account_identifier ai
               WHERE ai.household_id = a.household_id AND ai.account_id = a.id AND ai.is_active
               ORDER BY (ai.kind = CASE WHEN a.account_type = 'credit_card'::account_type
                                        THEN 'card' ELSE 'bank_account' END) DESC,
                        ai.created_at, ai.id
               LIMIT 1),
             a.opened_on,
             a.closed_on, a.is_active, a.include_in_net_worth, a.is_liquid, a.opening_balance,
             a.opening_balance_date, a.opening_balance_note, a.opening_balance_confirmed_at,
             a.current_balance, a.current_balance_as_of, a.credit_limit,
             (SELECT COALESCE(
                       jsonb_agg(jsonb_build_object(
                         'id', ai.id, 'kind', ai.kind, 'masked', ai.masked_value,
                         'canReveal', ai.full_value IS NOT NULL, 'isActive', ai.is_active)
                         ORDER BY ai.is_active DESC, ai.created_at, ai.id),
                       '[]'::jsonb)
                FROM account_identifier ai
               WHERE ai.household_id = a.household_id AND ai.account_id = a.id)"""

FROM = (
    "FROM account a LEFT JOIN institution i ON i.id = a.institution_id"
    " AND (i.household_id IS NULL OR i.household_id = a.household_id)"
)

FIELDS = [
    "id",
    "name",
    "institutionId",
    "institutionName",
    "accountType",
    "accountClass",
    "accountSubtype",
    "currency",
    "country",
    "accountNumberMasked",
    "openedOn",
    "closedOn",
    "isActive",
    "includeInNetWorth",
    "isLiquid",
    "openingBalance",
    "openingBalanceDate",
    "openingBalanceNote",
    "openingBalanceConfirmedAt",
    "currentBalance",
    "currentBalanceAsOf",
    "creditLimit",
    "identifiers",
]
MONEY_FIELDS = {"openingBalance", "currentBalance", "creditLimit"}


def account_row(values: tuple[Any, ...]) -> AccountRow:
    if len(values) == len(FIELDS) - 1:
        values = (*values, [])
    fields = dict(zip(FIELDS, values, strict=True))
    for name in MONEY_FIELDS:
        fields[name] = _out(fields[name])
    return AccountRow(**fields)


def _assert_institution_allowed(
    cur: psycopg.Cursor[Any], institution_id: UUID | None, household_id: UUID
) -> None:
    if institution_id is None:
        return
    cur.execute(
        """SELECT 1 FROM institution
            WHERE id = %s AND (household_id IS NULL OR household_id = %s)""",
        (institution_id, household_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(
            status_code=422, detail="institution is not available to this household"
        )


def _one(cur: psycopg.Cursor[Any], account_id: UUID, household_id: UUID) -> AccountRow:
    cur.execute(
        f"SELECT {COLUMNS} {FROM} WHERE a.id = %s AND a.household_id = %s",
        (account_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such account")
    return account_row(row)


def resolve_account_by_identifier(
    cur: psycopg.Cursor[Any], kind: IdentifierKind, normalized_value: str, household_id: UUID
) -> AccountRow | None:
    cur.execute(
        f"""SELECT {COLUMNS} {FROM}
              WHERE a.household_id = %s
                AND EXISTS (
                  SELECT FROM account_identifier ai
                   WHERE ai.household_id = a.household_id AND ai.account_id = a.id
                     AND ai.kind = %s AND ai.normalized_value = %s AND ai.is_active)""",
        (household_id, kind, normalized_value),
    )
    row = cur.fetchone()
    return None if row is None else account_row(row)


def resolve_account_by_legacy_fingerprint(
    cur: psycopg.Cursor[Any], digest: str, household_id: UUID
) -> AccountRow | None:
    cur.execute(
        f"""SELECT {COLUMNS} {FROM}
              WHERE a.household_id = %s
                AND EXISTS (
                  SELECT FROM account_identifier ai
                   WHERE ai.household_id = a.household_id AND ai.account_id = a.id
                     AND ai.legacy_fingerprint = decode(%s, 'hex') AND ai.is_active)""",
        (household_id, digest),
    )
    row = cur.fetchone()
    return None if row is None else account_row(row)


def _insert_account_identifier(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    identifier: Identifier,
    *,
    provenance: Literal["owner_entered", "statement", "statement_backfill"],
    source_sha256: str | None = None,
    source_document_id: UUID | None = None,
    source_layout_id: UUID | None = None,
    source_layout_version: int | None = None,
    source_capture_key: str | None = None,
) -> UUID:
    complete = identifier if isinstance(identifier, CompleteIdentifier) else None
    cur.execute(
        """INSERT INTO account_identifier (
               household_id, account_id, kind, masked_value, visible_suffix,
               full_value, normalized_value, provenance, source_sha256,
               source_document_id, source_layout_id, source_layout_version, source_capture_key)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            household_id,
            account_id,
            identifier.kind,
            identifier.masked_value,
            identifier.visible_suffix,
            None if complete is None else complete.full_value,
            None if complete is None else complete.normalized_value,
            provenance,
            source_sha256,
            source_document_id,
            source_layout_id,
            source_layout_version,
            source_capture_key,
        ),
    )
    created = cur.fetchone()
    if created is None:  # pragma: no cover - INSERT RETURNING always yields or raises
        raise HTTPException(status_code=500, detail="the account identifier could not be created")
    return UUID(str(created[0]))


def _insert_account(
    cur: psycopg.Cursor[Any],
    body: AccountCreate,
    household_id: UUID,
    *,
    identifier: Identifier | None,
    identifier_provenance: Literal[
        "owner_entered", "statement", "statement_backfill"
    ] = "owner_entered",
    source_sha256: str | None = None,
    source_document_id: UUID | None = None,
    source_layout_id: UUID | None = None,
    source_layout_version: int | None = None,
    source_capture_key: str | None = None,
) -> AccountRow:
    """The account insert shared by the ordinary form and statement confirmation."""
    _assert_institution_allowed(cur, body.institutionId, household_id)
    cur.execute(
        "SELECT id FROM person WHERE household_id = %s AND is_self",
        (household_id,),
    )
    selves = cur.fetchall()
    if len(selves) == 0:
        raise HTTPException(status_code=409, detail="no self person for this household")
    if len(selves) > 1:
        raise HTTPException(status_code=409, detail="this household has more than one self person")
    cur.execute(
        """INSERT INTO account (
               household_id, owner_person_id, institution_id, name, account_type,
               account_subtype, currency, country, opened_on,
               include_in_net_worth, is_liquid, opening_balance, opening_balance_date,
               opening_balance_note, credit_limit)
           VALUES (%s, %s, %s, %s, %s::account_type, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s, %s)
           RETURNING id""",
        (
            household_id,
            selves[0][0],
            body.institutionId,
            body.name,
            body.accountType,
            body.accountSubtype,
            body.currency,
            body.country,
            body.openedOn,
            body.includeInNetWorth,
            body.isLiquid,
            Decimal(body.openingBalance),
            body.openingBalanceDate,
            body.openingBalanceNote,
            None if body.creditLimit is None else Decimal(body.creditLimit),
        ),
    )
    created = cur.fetchone()
    if created is None:
        raise HTTPException(status_code=500, detail="the account could not be created")
    account_id = UUID(str(created[0]))
    if identifier is not None:
        _insert_account_identifier(
            cur,
            household_id,
            account_id,
            identifier,
            provenance=identifier_provenance,
            source_sha256=source_sha256,
            source_document_id=source_document_id,
            source_layout_id=source_layout_id,
            source_layout_version=source_layout_version,
            source_capture_key=source_capture_key,
        )
    return _one(cur, account_id, household_id)


def _replace_account_identifier(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    kind: IdentifierKind,
    identifier: Identifier | None,
    *,
    provenance: Literal["owner_entered", "statement", "statement_backfill"] = "owner_entered",
    source_sha256: str | None = None,
    source_document_id: UUID | None = None,
    source_layout_id: UUID | None = None,
    source_layout_version: int | None = None,
    source_capture_key: str | None = None,
) -> None:
    cur.execute(
        """SELECT masked_value, full_value, normalized_value, visible_suffix
             FROM account_identifier
            WHERE household_id = %s AND account_id = %s AND kind = %s AND is_active
            ORDER BY created_at, id
            FOR UPDATE""",
        (household_id, account_id, kind),
    )
    existing = cur.fetchall()
    if identifier is not None and not isinstance(identifier, CompleteIdentifier):
        complete_suffixes = {(row[3] or row[0][-4:]) for row in existing if row[1] is not None}
        if complete_suffixes:
            if identifier.visible_suffix not in complete_suffixes:
                raise HTTPException(
                    status_code=409,
                    detail=("provide accountNumber to change a mask linked to an account identity"),
                )
            return
    if identifier is not None and any(
        (isinstance(identifier, CompleteIdentifier) and row[2] == identifier.normalized_value)
        or (
            not isinstance(identifier, CompleteIdentifier)
            and row[1] is None
            and (row[3] or row[0][-4:]) == identifier.visible_suffix
        )
        for row in existing
    ):
        return
    if existing:
        cur.execute(
            """UPDATE account_identifier
                  SET is_active = false, deactivated_at = now(), updated_at = now()
                WHERE household_id = %s AND account_id = %s AND kind = %s AND is_active""",
            (household_id, account_id, kind),
        )
    if identifier is not None:
        _insert_account_identifier(
            cur,
            household_id,
            account_id,
            identifier,
            provenance=provenance,
            source_sha256=source_sha256,
            source_document_id=source_document_id,
            source_layout_id=source_layout_id,
            source_layout_version=source_layout_version,
            source_capture_key=source_capture_key,
        )


@router.get("/accounts")
def list_accounts(owner: Annotated[OwnerSession, Depends(require_owner)]) -> AccountsResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {COLUMNS} {FROM}
                 WHERE a.household_id = %s
                 ORDER BY a.account_class, a.country, a.display_order, a.name""",
            (owner.household_id,),
        )
        rows = [account_row(row) for row in cur.fetchall()]
        # Aggregation happens in Postgres, not in Python; money arithmetic stays in NUMERIC.
        cur.execute(
            """SELECT account_class::text, country, currency, SUM(current_balance)
                 FROM account
                WHERE household_id = %s AND is_active AND include_in_net_worth
                GROUP BY 1, 2, 3
                ORDER BY 1, 2, 3""",
            (owner.household_id,),
        )
        totals = [
            AccountGroupTotal(
                accountClass=cls, country=country, currency=currency, total=str(total)
            )
            for cls, country, currency, total in cur.fetchall()
        ]
    return AccountsResponse(accounts=rows, totals=totals)


@router.post("/accounts", status_code=201)
def create_account(
    body: AccountCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> AccountRow:
    identifier = _body_identifier(body)
    try:
        with connect() as conn, conn.cursor() as cur:
            return _insert_account(
                cur,
                body,
                owner.household_id,
                identifier=identifier,
            )
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.UniqueViolation,
    ) as exc:
        raise HTTPException(status_code=422, detail=_account_write_error(exc)) from None


@router.post(
    "/accounts/{account_id}/identifiers/{identifier_id}/reveal",
    response_model=RevealedAccountIdentifier | UnavailableAccountIdentifier,
)
def reveal_account_identifier(
    account_id: UUID,
    identifier_id: UUID,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RevealedAccountIdentifier | UnavailableAccountIdentifier:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT ai.kind, ai.full_value
                 FROM account_identifier ai
                 JOIN account a ON a.household_id = ai.household_id AND a.id = ai.account_id
                WHERE ai.id = %s AND ai.account_id = %s AND ai.household_id = %s""",
            (identifier_id, account_id, owner.household_id),
        )
        found = cur.fetchone()
    if found is None:
        raise HTTPException(
            status_code=404,
            detail="no such account identifier",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    kind, full_value = found
    if full_value is None:
        return UnavailableAccountIdentifier(status="unavailable", reason="source_mask_only")
    return RevealedAccountIdentifier(status="revealed", value=full_value, kind=kind)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: UUID,
    body: AccountUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> AccountRow:
    replace_account_identity = bool(
        {"accountNumber", "accountNumberMasked"} & body.model_fields_set
    )
    identifier = _body_identifier(body) if replace_account_identity else None
    # Changing the opening anchor recomputes the materialised balance from the ledger:
    # opening_balance + the sum of the account's posting rows (split children move nothing).
    # Postgres does the arithmetic; before M1b's ledger existed the sum was empty and this
    # reduced to the bare opening balance.
    # Changing either anchor field also clears the acknowledgment: a confirmation timestamp
    # must never predate the figure it confirms (Invariant I7), so the replacement waits for a
    # fresh, explicit acknowledgment instead of inheriting the old one from a full-replacement
    # body that still carries acknowledgeOpeningBalance=True.
    try:
        with connect() as conn, conn.cursor() as cur:
            # Serialize with transaction creation: every ledger writer takes the household row
            # lock first (B3.5), so holding it here means no insert is in flight between the
            # currency guard's read and the update below — the same lock order as
            # POST /transactions, so the two paths cannot deadlock.
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            _assert_institution_allowed(cur, body.institutionId, owner.household_id)
            # An account's currency is fixed once ledger rows exist: the rows' native amounts
            # are immutable facts in that denomination, and relabelling the account would mix
            # denominations in one balance. A wrong currency with history is a migration
            # workflow, not an edit.
            cur.execute(
                """SELECT a.currency,
                          EXISTS (SELECT FROM transaction t WHERE t.account_id = a.id),
                          a.opening_balance_date,
                          EXISTS (SELECT FROM transaction t
                                   WHERE t.account_id = a.id
                                     AND t.parent_transaction_id IS NULL
                                     AND t.booked_date < %s),
                          (SELECT min(t.booked_date) FROM transaction t
                            WHERE t.account_id = a.id),
                          (SELECT max(t.booked_date) FROM transaction t
                            WHERE t.account_id = a.id)
                     FROM account a WHERE a.id = %s AND a.household_id = %s""",
                (body.openingBalanceDate, account_id, owner.household_id),
            )
            existing = cur.fetchone()
            if (
                existing is not None
                and bool(existing[1])
                and str(existing[0]).strip() != body.currency
            ):
                raise HTTPException(
                    status_code=409,
                    detail="an account's currency is fixed once it has ledger entries",
                )
            # Re-anchoring past existing postings would define them as already inside the new
            # opening balance while they still sit in the ledger — double-counted history.
            # Nothing about money is reinterpreted silently: refuse and say why.
            if (
                existing is not None
                and existing[2] != body.openingBalanceDate
                and bool(existing[3])
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "ledger entries exist before the new anchor date; the opening balance "
                        "would double-count them. Delete those entries or pick an earlier date"
                    ),
                )
            # The lifetime must keep holding what the ledger already holds: POST refuses
            # out-of-lifetime postings, and an edit must not strand existing ones.
            if existing is not None and existing[4] is not None:
                if body.openedOn is not None and body.openedOn > existing[4]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"a ledger entry on {existing[4]} predates the new opened date",
                    )
                if (
                    body.closedOn is not None
                    and existing[5] is not None
                    and body.closedOn < existing[5]
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=f"a ledger entry on {existing[5]} postdates the new closed date",
                    )
            cur.execute(
                """UPDATE account
                      SET name = %s, institution_id = %s, account_type = %s::account_type,
                          account_subtype = %s, currency = %s, country = %s,
                          opened_on = %s, closed_on = %s,
                          is_active = (%s::date IS NULL),
                          include_in_net_worth = %s, is_liquid = %s,
                          opening_balance = %s, opening_balance_date = %s,
                          current_balance = CASE
                            WHEN opening_balance IS DISTINCT FROM %s
                              OR opening_balance_date IS DISTINCT FROM %s
                            THEN %s + (SELECT COALESCE(sum(t.amount), 0)
                                         FROM transaction t
                                        WHERE t.account_id = account.id
                                          AND t.parent_transaction_id IS NULL)
                            ELSE current_balance END,
                          current_balance_as_of = CASE
                            WHEN opening_balance IS DISTINCT FROM %s
                              OR opening_balance_date IS DISTINCT FROM %s
                            THEN GREATEST(%s::date::timestamptz,
                                          (SELECT COALESCE(max(t.booked_date)::timestamptz,
                                                           '-infinity'::timestamptz)
                                             FROM transaction t
                                            WHERE t.account_id = account.id
                                              AND t.parent_transaction_id IS NULL))
                            ELSE current_balance_as_of END,
                          opening_balance_note = %s, credit_limit = %s,
                          opening_balance_confirmed_at =
                            CASE WHEN opening_balance IS DISTINCT FROM %s
                                   OR opening_balance_date IS DISTINCT FROM %s THEN NULL
                                 WHEN %s THEN COALESCE(opening_balance_confirmed_at, now())
                                 ELSE NULL END
                    WHERE id = %s AND household_id = %s
                   RETURNING id""",
                (
                    body.name,
                    body.institutionId,
                    body.accountType,
                    body.accountSubtype,
                    body.currency,
                    body.country,
                    body.openedOn,
                    body.closedOn,
                    body.closedOn,
                    body.includeInNetWorth,
                    body.isLiquid,
                    Decimal(body.openingBalance),
                    body.openingBalanceDate,
                    Decimal(body.openingBalance),
                    body.openingBalanceDate,
                    Decimal(body.openingBalance),
                    Decimal(body.openingBalance),
                    body.openingBalanceDate,
                    body.openingBalanceDate,
                    body.openingBalanceNote,
                    None if body.creditLimit is None else Decimal(body.creditLimit),
                    Decimal(body.openingBalance),
                    body.openingBalanceDate,
                    body.acknowledgeOpeningBalance,
                    account_id,
                    owner.household_id,
                ),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such account")
            if replace_account_identity:
                _replace_account_identifier(
                    cur,
                    owner.household_id,
                    account_id,
                    _identifier_kind(body.accountType),
                    identifier,
                )
            return _one(cur, account_id, owner.household_id)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.NumericValueOutOfRange,
        psycopg.errors.UniqueViolation,
    ) as exc:
        raise HTTPException(status_code=422, detail=_account_write_error(exc)) from None


@router.get("/institutions")
def list_institutions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> list[InstitutionRow]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, short_name, country, institution_type
                 FROM institution
                WHERE household_id IS NULL OR household_id = %s
                ORDER BY name""",
            (owner.household_id,),
        )
        return [
            InstitutionRow(id=i, name=n, shortName=s, country=c, institutionType=t)
            for i, n, s, c, t in cur.fetchall()
        ]


@router.post("/institutions", status_code=201)
def create_institution(
    body: InstitutionCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> InstitutionRow:
    """Shared seeds are suggestions; any household can add its own (data-model.html §4)."""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO institution (
                       household_id, name, short_name, country, institution_type)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING id, name, short_name, country, institution_type""",
                (
                    owner.household_id,
                    body.name,
                    body.shortName,
                    body.country,
                    body.institutionType,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            i, n, s, c, t = created
            return InstitutionRow(id=i, name=n, shortName=s, country=c, institutionType=t)
    except (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc
