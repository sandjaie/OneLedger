# api/oneledger/routes/transactions.py
"""M1b: manual transactions over the ledger. Both reporting amounts freeze at save time under
the household row lock (Phase 1 spec B3.5); a missing FX rate refuses the save — never a
fallback to 1.0 or today's rate. The listing is the one deliberate non-report reader of the
base table: transfer legs and duplicates stay visible in a ledger (IA section 4), while every
spending or income aggregate reads v_transaction_reportable."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..auth import OwnerSession, require_owner
from ..db import connect, first_line
from ..money import RATE, _frozen, _money, _out, translate
from .layouts import _preview_redactor

router = APIRouter(tags=["transactions"])

# The manual-entry set: linked types (transfers, investments, loans, imports) arrive with
# their owning features, and opening_balance/adjustment are derivations, not entries. The web
# dropdown mirrors this list; the API is the boundary.
MANUAL_TYPES = frozenset({"expense", "income", "fee", "interest", "tax", "refund"})
_DESCRIPTION_REDACTOR = _preview_redactor(None)


class TransactionRow(BaseModel):
    id: UUID
    accountId: UUID
    accountName: str
    bookedDate: date
    descriptionRaw: str
    descriptionClean: str | None
    amount: str
    currency: str = Field(min_length=3, max_length=3)
    originalAmount: str | None
    originalCurrency: str | None
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
    isSplitParent: bool
    isExcluded: bool
    classifiedBy: str
    source: str
    notes: str | None
    sourceDocumentId: UUID | None
    sourcePageNumber: int | None
    sourceRowNumber: int | None


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
    """The two owner-editable fields, always sent — omitting notes would silently erase an
    existing note, so it is required and an explicit null is the deliberate clear. Money is
    immutable (I1/I5); the database trigger enforces what this model does not offer."""

    categoryId: UUID | None
    notes: str | None


COLUMNS = """t.id, t.account_id, a.name, t.booked_date, t.description_raw, t.description_clean,
             t.amount, t.currency, t.original_amount, t.original_currency,
             t.amount_base, t.amount_secondary, t.fx_rate_date,
             t.fx_rate_is_carried, t.fx_rate_to_base, t.fx_rate_to_secondary,
             t.txn_type::text, t.category_id, c.name, t.merchant_id,
             t.transfer_id, t.parent_transaction_id,
             EXISTS (SELECT 1 FROM transaction c WHERE c.parent_transaction_id = t.id),
             t.is_excluded, t.classified_by::text,
             t.source::text, t.notes,
             t.source_document_id, t.source_page_number, t.source_row_number"""

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
    "originalAmount",
    "originalCurrency",
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
    "isSplitParent",
    "isExcluded",
    "classifiedBy",
    "source",
    "notes",
    "sourceDocumentId",
    "sourcePageNumber",
    "sourceRowNumber",
]
MONEY_FIELDS = {
    "amount",
    "originalAmount",
    "amountBase",
    "amountSecondary",
    "fxRateToBase",
    "fxRateToSecondary",
}


def transaction_row(values: tuple[Any, ...]) -> TransactionRow:
    fields = dict(zip(FIELDS, values, strict=True))
    for name in MONEY_FIELDS:
        fields[name] = _out(fields[name])
    return TransactionRow(**fields)


def public_transaction_row(row: TransactionRow) -> TransactionRow:
    """Screen the wire copy while retaining original audit/scoring text in memory."""
    return row.model_copy(
        update={
            "descriptionRaw": _DESCRIPTION_REDACTOR(row.descriptionRaw),
            "descriptionClean": (
                None
                if row.descriptionClean is None
                else _DESCRIPTION_REDACTOR(row.descriptionClean)
            ),
        }
    )


def _one(cur: psycopg.Cursor[Any], txn_id: UUID, household_id: UUID) -> TransactionRow:
    cur.execute(
        f"SELECT {COLUMNS} {FROM} WHERE t.id = %s AND t.household_id = %s",
        (txn_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such transaction")
    return public_transaction_row(transaction_row(row))


def _money_query(value: str | None) -> Decimal | None:
    """Filter money arrives as a query string; a malformed one is the caller's 422, never a
    500 from deep inside the route."""
    if value is None:
        return None
    try:
        return Decimal(_money(value) or "0")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _assert_category_allowed(
    cur: psycopg.Cursor[Any],
    category_id: UUID | None,
    household_id: UUID,
    kind: str | None = None,
) -> None:
    """The category foreign key is not tenant-aware; the boundary is this predicate. Only the
    seeded taxonomy and the household's own categories may be assigned. `kind`, when given,
    additionally requires the category's kind to match — the payslip deduction-spending rules
    need an expense category specifically, not any visible one."""
    if category_id is None:
        return
    cur.execute(
        """SELECT 1 FROM category c
             LEFT JOIN category_override o
                    ON o.category_id = c.id AND o.household_id = %s
            WHERE c.id = %s AND (c.household_id IS NULL OR c.household_id = %s)
              AND (%s::category_kind IS NULL OR c.kind = %s::category_kind)
              AND COALESCE(o.is_active, c.is_active)""",
        (household_id, category_id, household_id, kind, kind),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=422, detail="category is not available to this household")


def post_ledger_transaction(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    booked_date: date,
    description: str,
    amount: Decimal,
    txn_type: str,
    category_id: UUID | None,
    *,
    country: str | None = None,
    payment_method: str | None = None,
    notes: str | None = None,
) -> UUID:
    """The manual posting path (household lock, account lifetime/anchor, category check, frozen
    FX freeze, dedupe hash, insert) — factored out of `create_transaction` so the payslip
    deduction-spending pair freezes money identically (I5) rather than through a second copy.
    Raises the same `HTTPException`s `create_transaction` would for an invalid account, date or
    category; the caller (a route already inside its own try/except) translates them."""
    cur.execute(
        "SELECT base_currency, secondary_currency FROM household WHERE id = %s FOR UPDATE",
        (household_id,),
    )
    pair = cur.fetchone()
    if pair is None:
        raise HTTPException(status_code=409, detail="no household row")
    base, secondary = str(pair[0]).strip(), str(pair[1]).strip()

    cur.execute(
        """SELECT currency, opened_on, closed_on, opening_balance_date FROM account
            WHERE id = %s AND household_id = %s""",
        (account_id, household_id),
    )
    account = cur.fetchone()
    if account is None:
        raise HTTPException(status_code=404, detail="no such account")
    currency = str(account[0]).strip()
    opened_on, closed_on, anchor = account[1], account[2], account[3]
    if opened_on is not None and booked_date < opened_on:
        raise HTTPException(status_code=422, detail=f"the account opened on {opened_on}")
    if closed_on is not None and booked_date > closed_on:
        raise HTTPException(status_code=422, detail=f"the account closed on {closed_on}")
    if anchor is not None and booked_date < anchor:
        raise HTTPException(
            status_code=422,
            detail=(
                f"the balance is anchored on {anchor}; earlier movements are already "
                "inside the opening balance"
            ),
        )
    _assert_category_allowed(cur, category_id, household_id)

    rate_to_base, rate_to_secondary, carried, rate_date = _frozen(
        cur, booked_date, currency, base, secondary
    )
    try:
        rate_to_base = rate_to_base.quantize(RATE, rounding=ROUND_HALF_UP)
        rate_to_secondary = rate_to_secondary.quantize(RATE, rounding=ROUND_HALF_UP)
        amount_base = translate(amount, rate_to_base)
        amount_secondary = translate(amount, rate_to_secondary)
    except InvalidOperation as exc:
        raise HTTPException(
            status_code=422, detail="the converted amount exceeds the storable range"
        ) from exc

    cur.execute(
        """SELECT count(*) FROM transaction
            WHERE household_id = %s AND account_id = %s AND booked_date = %s
              AND amount = %s AND description_raw = %s""",
        (household_id, account_id, booked_date, amount, description),
    )
    counted = cur.fetchone()
    occurrence = int(counted[0]) if counted else 0
    while True:
        dedupe = hashlib.sha256(
            "|".join(
                [
                    str(account_id),
                    booked_date.isoformat(),
                    str(amount),
                    description,
                    str(occurrence),
                ]
            ).encode()
        ).hexdigest()
        cur.execute(
            """SELECT 1 FROM transaction
                WHERE household_id = %s AND account_id = %s AND dedupe_hash = %s""",
            (household_id, account_id, dedupe),
        )
        if cur.fetchone() is None:
            break
        occurrence += 1

    cur.execute(
        """INSERT INTO transaction (
               household_id, account_id, booked_date, description_raw, amount,
               currency, fx_rate_date, fx_rate_is_carried, fx_rate_to_base,
               fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
               category_id, country, payment_method, source, dedupe_hash,
               classified_by, classification_confidence, is_reviewed, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::txn_type,
                   %s, %s, %s::payment_method, 'manual', %s, %s::classified_by, %s, %s, %s)
           RETURNING id""",
        (
            household_id,
            account_id,
            booked_date,
            description,
            amount,
            currency,
            rate_date,
            carried,
            rate_to_base,
            rate_to_secondary,
            amount_base,
            amount_secondary,
            txn_type,
            category_id,
            country,
            payment_method,
            dedupe,
            "user_manual" if category_id is not None else "unclassified",
            Decimal("1.000") if category_id is not None else Decimal("0.000"),
            category_id is not None,
            notes,
        ),
    )
    created = cur.fetchone()
    if created is None:
        raise HTTPException(status_code=500, detail="the transaction could not be created")
    return UUID(str(created[0]))


def delete_ledger_transaction(
    cur: psycopg.Cursor[Any], household_id: UUID, transaction_id: UUID
) -> None:
    """Removes one posting this module wrote. The V10 balance trigger restores
    `current_balance` on delete the same way it applies it on insert, so undoing a payslip
    deduction pair leaves the account balance exactly where it was."""
    cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (household_id,))
    cur.execute(
        "DELETE FROM transaction WHERE household_id = %s AND id = %s",
        (household_id, transaction_id),
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
    merchantId: Annotated[UUID | None, Query()] = None,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    amountMin: Annotated[str | None, Query(max_length=25)] = None,
    amountMax: Annotated[str | None, Query(max_length=25)] = None,
    tag: Annotated[str | None, Query(max_length=60)] = None,
    reviewState: Annotated[str | None, Query(pattern="^(reviewed|unreviewed)$")] = None,
    classifiedBy: Annotated[
        str | None,
        Query(
            pattern=(
                "^(unclassified|seed_rule|merchant_alias|recurring_series|prior_match|"
                "user_rule|user_manual|ai)$"
            )
        ),
    ] = None,
    sourceDocumentId: Annotated[UUID | None, Query()] = None,
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
        # The V10 GIN index is on to_tsvector('simple', description_raw); this predicate is
        # the one it serves. Word search, parameterised, never interpolated.
        clauses.append("to_tsvector('simple', t.description_raw) @@ plainto_tsquery('simple', %s)")
        params.append(q.strip())
    if merchantId is not None:
        clauses.append("t.merchant_id = %s")
        params.append(merchantId)
    if currency is not None:
        clauses.append("t.currency = %s")
        params.append(currency)
    if country is not None:
        clauses.append("t.country = %s")
        params.append(country)
    if amountMin is not None:
        clauses.append("t.amount >= %s")
        params.append(_money_query(amountMin))
    if amountMax is not None:
        clauses.append("t.amount <= %s")
        params.append(_money_query(amountMax))
    if tag is not None:
        clauses.append("%s = ANY(t.tags)")
        params.append(tag)
    if reviewState is not None:
        clauses.append("t.is_reviewed = %s")
        params.append(reviewState == "reviewed")
    if classifiedBy is not None:
        clauses.append("t.classified_by = %s::classified_by")
        params.append(classifiedBy)
    if sourceDocumentId is not None:
        clauses.append("t.source_document_id = %s")
        params.append(sourceDocumentId)
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
                     ORDER BY {order}, t.created_at DESC, t.id DESC
                     LIMIT %s OFFSET %s""",
                (*params, limit, offset),
            )
            rows = [public_transaction_row(transaction_row(row)) for row in cur.fetchall()]
    except psycopg.errors.InvalidTextRepresentation as exc:
        raise HTTPException(status_code=422, detail=first_line(exc)) from exc
    return TransactionsResponse(transactions=rows, total=total)


@router.post("/transactions", status_code=201)
def create_transaction(
    body: TransactionCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> TransactionRow:
    amount = Decimal(body.amount)
    try:
        with connect() as conn, conn.cursor() as cur:
            new_id = post_ledger_transaction(
                cur,
                owner.household_id,
                body.accountId,
                body.bookedDate,
                body.description,
                amount,
                body.txnType,
                body.categoryId,
                country=body.country,
                payment_method=body.paymentMethod,
                notes=body.notes,
            )
            return _one(cur, new_id, owner.household_id)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.UniqueViolation,
        psycopg.errors.RaiseException,
        psycopg.errors.NumericValueOutOfRange,
    ) as exc:
        raise HTTPException(status_code=422, detail=first_line(exc)) from exc


@router.patch("/transactions/{transaction_id}")
def update_transaction(
    transaction_id: UUID,
    body: TransactionUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> TransactionRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            # The same household lock every ledger writer takes: without it a concurrent
            # splitter could add children between the container check and the update.
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            # A split parent is a container: reports read its children, so recategorizing it
            # would change the ledger row while every report kept the old categories.
            cur.execute(
                """SELECT EXISTS (SELECT FROM transaction c WHERE c.parent_transaction_id = %s),
                          t.category_id
                     FROM transaction t WHERE t.id = %s AND t.household_id = %s""",
                (transaction_id, transaction_id, owner.household_id),
            )
            container = cur.fetchone()
            if container is None:
                raise HTTPException(status_code=404, detail="no such transaction")
            category_unchanged = container[1] == body.categoryId
            # A container's notes stay editable — the split trigger permits them — but its
            # category belongs to its children, which is where reports read.
            if bool(container[0]) and not category_unchanged:
                raise HTTPException(
                    status_code=409,
                    detail="a split parent is a container; recategorize its children",
                )
            # Availability gates NEW assignments only: a historical category the owner later
            # deactivated stays on its rows, and a note-only edit resending it must pass.
            if not category_unchanged:
                _assert_category_allowed(cur, body.categoryId, owner.household_id)
            if category_unchanged:
                # A note-only edit changes no classification: the audit trail must not claim
                # the owner chose a category they did not change.
                cur.execute(
                    """UPDATE transaction
                          SET notes = %s, updated_at = now()
                        WHERE id = %s AND household_id = %s
                       RETURNING id""",
                    (body.notes, transaction_id, owner.household_id),
                )
            else:
                cur.execute(
                    """UPDATE transaction
                          SET category_id = %s, notes = %s,
                              -- One answer to "why this category": the owner said so — or
                              -- said nothing, which is unclassified (0.000, queued).
                              classified_by = %s::classified_by,
                              is_reviewed = %s,
                              classification_rule_id = NULL,
                              classification_confidence = %s,
                              updated_at = now()
                        WHERE id = %s AND household_id = %s
                       RETURNING id""",
                    (
                        body.categoryId,
                        body.notes,
                        "user_manual" if body.categoryId is not None else "unclassified",
                        body.categoryId is not None,
                        Decimal("1.000") if body.categoryId is not None else Decimal("0.000"),
                        transaction_id,
                        owner.household_id,
                    ),
                )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such transaction")
            return _one(cur, transaction_id, owner.household_id)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.RaiseException,
    ) as exc:
        raise HTTPException(status_code=422, detail=first_line(exc)) from exc
