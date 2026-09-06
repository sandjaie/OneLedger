# api/oneledger/routes/transactions.py
"""M1b: manual transactions over the ledger. Both reporting amounts freeze at save time under
the household row lock (Phase 1 spec B3.5); a missing FX rate refuses the save — never a
fallback to 1.0 or today's rate. The listing is the one deliberate non-report reader of the
base table: transfer legs and duplicates stay visible in a ledger (IA section 4), while every
spending or income aggregate reads v_transaction_reportable."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(tags=["transactions"])

# Money crosses the wire as a decimal string in both directions. api-contracts.html section 6.
MONEY = re.compile(r"-?\d{1,16}(\.\d{1,4})?")

# The manual-entry set: linked types (transfers, investments, loans, imports) arrive with
# their owning features, and opening_balance/adjustment are derivations, not entries. The web
# dropdown mirrors this list; the API is the boundary.
MANUAL_TYPES = frozenset({"expense", "income", "fee", "interest", "tax", "refund"})

CENT = Decimal("0.0001")  # amounts store at 4 dp
RATE = Decimal("0.0000000001")  # rates store at 10 dp


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ValueError("money must be a decimal string with at most four decimal places")
    return value


def _out(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0]


class TransactionRow(BaseModel):
    id: UUID
    accountId: UUID
    accountName: str
    bookedDate: date
    descriptionRaw: str
    descriptionClean: str | None
    amount: str
    currency: str = Field(min_length=3, max_length=3)
    amountBase: str
    amountSecondary: str
    fxRateDate: date
    fxRateIsCarried: bool
    fxRateToBase: str
    fxRateToSecondary: str
    txnType: str
    categoryId: UUID | None
    categoryName: str | None
    merchantId: UUID | None
    transferId: UUID | None
    parentTransactionId: UUID | None
    isExcluded: bool
    classifiedBy: str
    source: str
    notes: str | None


class TransactionsResponse(BaseModel):
    transactions: list[TransactionRow]
    total: int


class TransactionCreate(BaseModel):
    accountId: UUID
    bookedDate: date
    description: str = Field(min_length=1, max_length=500)
    amount: str
    # Validated by the txn_type enum in the database rather than a second copy that would
    # drift from it. An unknown value fails as 422.
    txnType: str = Field(min_length=1, max_length=40)
    categoryId: UUID | None = None
    notes: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    paymentMethod: str | None = Field(default=None, max_length=40)

    @field_validator("amount", mode="before")
    @classmethod
    def exact_decimal_string(cls, value: str | None) -> str | None:
        return _money(value)

    @field_validator("txnType")
    @classmethod
    def manual_types_only(cls, value: str) -> str:
        if value not in MANUAL_TYPES:
            raise ValueError(
                "manual entry records unlinked types only: " + ", ".join(sorted(MANUAL_TYPES))
            )
        return value


class TransactionUpdate(BaseModel):
    """The two owner-editable fields, always sent. Money is immutable (I1/I5); the database
    trigger enforces what this model does not offer."""

    categoryId: UUID | None
    notes: str | None = None


COLUMNS = """t.id, t.account_id, a.name, t.booked_date, t.description_raw, t.description_clean,
             t.amount, t.currency, t.amount_base, t.amount_secondary, t.fx_rate_date,
             t.fx_rate_is_carried, t.fx_rate_to_base, t.fx_rate_to_secondary,
             t.txn_type::text, t.category_id, c.name, t.merchant_id,
             t.transfer_id, t.parent_transaction_id, t.is_excluded, t.classified_by::text,
             t.source::text, t.notes"""

FROM = (
    "FROM transaction t"
    " JOIN account a ON a.id = t.account_id"
    " LEFT JOIN category c ON c.id = t.category_id"
)

FIELDS = [
    "id",
    "accountId",
    "accountName",
    "bookedDate",
    "descriptionRaw",
    "descriptionClean",
    "amount",
    "currency",
    "amountBase",
    "amountSecondary",
    "fxRateDate",
    "fxRateIsCarried",
    "fxRateToBase",
    "fxRateToSecondary",
    "txnType",
    "categoryId",
    "categoryName",
    "merchantId",
    "transferId",
    "parentTransactionId",
    "isExcluded",
    "classifiedBy",
    "source",
    "notes",
]
MONEY_FIELDS = {"amount", "amountBase", "amountSecondary", "fxRateToBase", "fxRateToSecondary"}


def transaction_row(values: tuple[Any, ...]) -> TransactionRow:
    fields = dict(zip(FIELDS, values, strict=True))
    for name in MONEY_FIELDS:
        fields[name] = _out(fields[name])
    return TransactionRow(**fields)


def _one(cur: psycopg.Cursor[Any], txn_id: UUID, household_id: UUID) -> TransactionRow:
    cur.execute(
        f"SELECT {COLUMNS} {FROM} WHERE t.id = %s AND t.household_id = %s",
        (txn_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such transaction")
    return transaction_row(row)


def _assert_category_allowed(
    cur: psycopg.Cursor[Any], category_id: UUID | None, household_id: UUID
) -> None:
    """The category foreign key is not tenant-aware; the boundary is this predicate. Only the
    seeded taxonomy and the household's own categories may be assigned."""
    if category_id is None:
        return
    cur.execute(
        """SELECT 1 FROM category WHERE id = %s
            AND (household_id IS NULL OR household_id = %s)""",
        (category_id, household_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=422, detail="category is not available to this household")


def _rate_leg(
    cur: psycopg.Cursor[Any], on: date, base: str, quote: str
) -> tuple[Decimal, bool, date]:
    """One stored fx_rate row: (rate, carried, the date the rate is actually from)."""
    cur.execute(
        """SELECT rate, is_carried_forward, carried_from FROM fx_rate
            WHERE rate_date = %s AND base_currency = %s AND quote_currency = %s""",
        (on, base, quote),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"no FX rate for {base}/{quote} on {on} — run the backfill first",
        )
    rate, carried, carried_from = row
    return Decimal(rate), bool(carried), (carried_from if carried else on)


def _frozen(
    cur: psycopg.Cursor[Any], on: date, txn_currency: str, base: str, secondary: str
) -> tuple[Decimal, Decimal, bool, date]:
    """(rate_to_base, rate_to_secondary, carried, effective rate date).

    fx_rate quotes everything against the household base, so the secondary leg is a cross
    through it. Intermediates stay unrounded; storage rounds once, half-up
    (multi-currency-and-fx.html)."""
    if txn_currency == base:
        rate_sb, carried, effective = _rate_leg(cur, on, secondary, base)
        return Decimal(1), Decimal(1) / rate_sb, carried, effective
    if txn_currency == secondary:
        rate_sb, carried, effective = _rate_leg(cur, on, secondary, base)
        return rate_sb, Decimal(1), carried, effective
    rate_cb, carried_cb, effective_cb = _rate_leg(cur, on, txn_currency, base)
    rate_sb, carried_sb, effective_sb = _rate_leg(cur, on, secondary, base)
    return (
        rate_cb,
        rate_cb / rate_sb,
        carried_cb or carried_sb,
        min(effective_cb, effective_sb),
    )


@router.get("/transactions")
def list_transactions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    accountId: Annotated[UUID | None, Query()] = None,
    categoryId: Annotated[UUID | None, Query()] = None,
    txnType: Annotated[str | None, Query(max_length=40)] = None,
    dateFrom: Annotated[date | None, Query()] = None,
    dateTo: Annotated[date | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[str, Query(pattern="^(bookedDate|amount|description|account)$")] = "bookedDate",
    direction: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionsResponse:
    clauses = ["t.household_id = %s"]
    params: list[Any] = [owner.household_id]
    if accountId is not None:
        clauses.append("t.account_id = %s")
        params.append(accountId)
    if categoryId is not None:
        clauses.append("t.category_id = %s")
        params.append(categoryId)
    if txnType is not None:
        clauses.append("t.txn_type = %s::txn_type")
        params.append(txnType)
    if dateFrom is not None:
        clauses.append("t.booked_date >= %s")
        params.append(dateFrom)
    if dateTo is not None:
        clauses.append("t.booked_date <= %s")
        params.append(dateTo)
    if q is not None and q.strip():
        clauses.append("t.description_raw ILIKE '%%' || %s || '%%'")
        params.append(q.strip())
    where = " AND ".join(clauses)
    # A dictionary of static fragments: user input selects a key, never SQL text.
    order = {
        "bookedDate": "t.booked_date",
        "amount": "t.amount",
        "description": "t.description_raw",
        "account": "a.name",
    }[sort] + (" ASC" if direction == "asc" else " DESC")

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM transaction t WHERE {where}".replace(
                    "t.household_id", "household_id"
                ).replace("t.", ""),
                tuple(params),
            )
            counted = cur.fetchone()
            total = int(counted[0]) if counted else 0
            cur.execute(
                f"""SELECT {COLUMNS} {FROM}
                     WHERE {where}
                     ORDER BY {order}, t.created_at DESC
                     LIMIT %s OFFSET %s""",
                (*params, limit, offset),
            )
            rows = [transaction_row(row) for row in cur.fetchall()]
    except psycopg.errors.InvalidTextRepresentation as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc
    return TransactionsResponse(transactions=rows, total=total)


@router.post("/transactions", status_code=201)
def create_transaction(
    body: TransactionCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> TransactionRow:
    amount = Decimal(body.amount)
    try:
        with connect() as conn, conn.cursor() as cur:
            # B3.5: every writer of a frozen amount takes the household row lock BEFORE
            # reading the reporting pair, so a pair change cannot interleave with this freeze.
            cur.execute(
                """SELECT base_currency, secondary_currency FROM household
                    WHERE id = %s FOR UPDATE""",
                (owner.household_id,),
            )
            pair = cur.fetchone()
            if pair is None:
                raise HTTPException(status_code=409, detail="no household row")
            base, secondary = str(pair[0]).strip(), str(pair[1]).strip()

            cur.execute(
                "SELECT currency FROM account WHERE id = %s AND household_id = %s",
                (body.accountId, owner.household_id),
            )
            account = cur.fetchone()
            if account is None:
                raise HTTPException(status_code=404, detail="no such account")
            currency = str(account[0]).strip()
            _assert_category_allowed(cur, body.categoryId, owner.household_id)

            rate_to_base, rate_to_secondary, carried, rate_date = _frozen(
                cur, body.bookedDate, currency, base, secondary
            )
            amount_base = (amount * rate_to_base).quantize(CENT, rounding=ROUND_HALF_UP)
            amount_secondary = (amount * rate_to_secondary).quantize(CENT, rounding=ROUND_HALF_UP)

            # The manual dedupe disambiguator mirrors ingestion section 6: occurrence index
            # within the day, never a row ordinal.
            cur.execute(
                """SELECT count(*) FROM transaction
                    WHERE household_id = %s AND account_id = %s AND booked_date = %s
                      AND amount = %s AND description_raw = %s""",
                (owner.household_id, body.accountId, body.bookedDate, amount, body.description),
            )
            counted = cur.fetchone()
            occurrence = int(counted[0]) if counted else 0
            dedupe = hashlib.sha256(
                "|".join(
                    [
                        str(body.accountId),
                        body.bookedDate.isoformat(),
                        str(amount),
                        body.description,
                        str(occurrence),
                    ]
                ).encode()
            ).hexdigest()

            cur.execute(
                """INSERT INTO transaction (
                       household_id, account_id, booked_date, description_raw, amount,
                       currency, fx_rate_date, fx_rate_is_carried, fx_rate_to_base,
                       fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
                       category_id, country, payment_method, source, dedupe_hash,
                       classified_by, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::txn_type,
                           %s, %s, %s::payment_method, 'manual', %s, %s::classified_by, %s)
                   RETURNING id""",
                (
                    owner.household_id,
                    body.accountId,
                    body.bookedDate,
                    body.description,
                    amount,
                    currency,
                    rate_date,
                    carried,
                    rate_to_base.quantize(RATE, rounding=ROUND_HALF_UP),
                    rate_to_secondary.quantize(RATE, rounding=ROUND_HALF_UP),
                    amount_base,
                    amount_secondary,
                    body.txnType,
                    body.categoryId,
                    body.country,
                    body.paymentMethod,
                    dedupe,
                    "user_manual" if body.categoryId is not None else "unclassified",
                    body.notes,
                ),
            )
            created = cur.fetchone()
            if created is None:
                raise HTTPException(status_code=500, detail="the transaction could not be created")
            return _one(cur, UUID(str(created[0])), owner.household_id)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.UniqueViolation,
        psycopg.errors.RaiseException,
    ) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc


@router.patch("/transactions/{transaction_id}")
def update_transaction(
    transaction_id: UUID,
    body: TransactionUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> TransactionRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            _assert_category_allowed(cur, body.categoryId, owner.household_id)
            cur.execute(
                """UPDATE transaction
                      SET category_id = %s, notes = %s,
                          classified_by = 'user_manual', is_reviewed = true,
                          updated_at = now()
                    WHERE id = %s AND household_id = %s
                   RETURNING id""",
                (body.categoryId, body.notes, transaction_id, owner.household_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such transaction")
            return _one(cur, transaction_id, owner.household_id)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.RaiseException,
    ) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc
