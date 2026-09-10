"""Slice 1.3 task 3a3a: classification rules through the app.

A rule is data the owner writes, so it is managed here and never by `psql` — the same reason
`db/seeds/README.md` gives for seeding a layout through `POST /layouts`. Everything a rule can
say is validated at write time, because `classification_rule` is unconstrained `TEXT` and
`pattern_type` in the places that matter and the service is what keeps garbage out of them.

**Creating or editing a rule changes nothing that already exists.** Rules never silently change
past data (`classification.html`), and nothing in this module writes to `transaction`. The
cascade is re-run only when the owner asks for it, by the route 3a3b adds.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..ingest.classify import PatternKind

router = APIRouter(tags=["rules"])

PATTERN_KINDS: frozenset[str] = frozenset({"exact", "prefix", "contains", "regex"})
DIRECTIONS: frozenset[str] = frozenset({"debit", "credit", "any"})
# `origin` is not settable through this module. A rule the owner writes is `user_created`; the
# seed pack sets `seed` when it loads (3a4), and `learned_from_correction` is Phase 2's.
ORIGIN_USER = "user_created"

# The matchers `has_matcher` counts, and the outcomes `has_outcome` counts. Named here so the
# 422 can say which half is missing rather than letting a CHECK answer with a constraint name.
MATCHER_FIELDS = (
    "matchDescriptionPattern",
    "matchAccountId",
    "matchAmountExact",
    "matchReferencePattern",
)
OUTCOME_FIELDS = ("setCategoryId", "setMerchantId", "setTxnType", "setPaymentMethod")

# A regex that can backtrack catastrophically is refused at creation, never at match time: at
# match time it is a request that never returns, on a route that holds a database connection.
# Nested quantifiers over an overlapping alternation are the shape that does it.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*]|\(\?:[^()]*[+*][^()]*\)\s*[+*]")


def _money(value: str | None) -> str | None:
    """A money matcher arrives as a decimal string and stays exact. No float, ever."""
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError("must be a decimal string") from None
    if not parsed.is_finite():
        raise ValueError("must be a finite decimal string")
    return str(parsed)


def _compiled(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression ({exc})") from None
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError(
            "this pattern nests one quantifier inside another, which can take exponential time "
            "on an input that nearly matches; rewrite it without the nested repeat"
        )


class RuleBody(BaseModel):
    """Everything a rule can say. Sent in full on create and on edit, as `AccountUpdate` is:
    a full replacement has no absent-versus-null ambiguity and the screen holds the whole row."""

    name: str | None = Field(default=None, max_length=120)
    priority: int = Field(default=100, ge=0, le=32767)
    isActive: bool = True

    matchDescriptionPattern: str | None = Field(default=None, max_length=500)
    matchPatternKind: PatternKind = "contains"
    matchAccountId: UUID | None = None
    matchCurrency: str | None = Field(default=None, min_length=3, max_length=3)
    matchAmountMin: str | None = None
    matchAmountMax: str | None = None
    matchAmountExact: str | None = None
    matchDirection: Literal["debit", "credit", "any"] = "any"
    matchCountry: str | None = Field(default=None, min_length=2, max_length=2)
    matchReferencePattern: str | None = Field(default=None, max_length=500)

    setCategoryId: UUID | None = None
    setMerchantId: UUID | None = None
    setTxnType: str | None = Field(default=None, max_length=20)
    setPaymentMethod: str | None = Field(default=None, max_length=20)
    setCountry: str | None = Field(default=None, min_length=2, max_length=2)
    setTags: list[str] = Field(default_factory=list)
    setIsTransferCandidate: bool = False
    setCounterpartyAccountId: UUID | None = None

    @field_validator("matchAmountMin", "matchAmountMax", "matchAmountExact", mode="before")
    @classmethod
    def exact_decimal_string(cls, value: str | None) -> str | None:
        return _money(value)

    @model_validator(mode="after")
    def patterns_compile(self) -> RuleBody:
        if self.matchPatternKind == "regex":
            for pattern in (self.matchDescriptionPattern, self.matchReferencePattern):
                if pattern is not None:
                    _compiled(pattern)
        return self

    @model_validator(mode="after")
    def matches_something_and_decides_something(self) -> RuleBody:
        """The two CHECKs V19 carries, refused here so the owner reads a sentence rather than
        a constraint name — and so the half that is missing is named."""
        if not any(getattr(self, field) is not None for field in MATCHER_FIELDS):
            raise ValueError(
                "a rule needs something to match on: a description pattern, an account, an "
                "exact amount or a reference pattern"
            )
        decides = any(getattr(self, field) is not None for field in OUTCOME_FIELDS)
        if not decides and not self.setIsTransferCandidate:
            raise ValueError(
                "a rule needs something to decide: a category, a merchant, a transaction type, "
                "a payment method, or marking the row a transfer candidate"
            )
        return self

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> RuleBody:
        if self.matchAmountMin is not None and self.matchAmountMax is not None:
            if Decimal(self.matchAmountMin) > Decimal(self.matchAmountMax):
                raise ValueError("matchAmountMin must not exceed matchAmountMax")
        return self


class RuleRow(RuleBody):
    """A saved rule. `origin` and the counters are read-only: the first is set by whichever
    path created the rule, and the second two are the cascade's to maintain."""

    id: UUID
    origin: str
    timesApplied: int
    lastAppliedAt: str | None


COLUMNS = """id, name, priority, is_active,
             match_description_pattern, match_pattern_kind::text, match_account_id,
             match_currency, match_amount_min, match_amount_max, match_amount_exact,
             match_direction, match_country, match_reference_pattern,
             set_category_id, set_merchant_id, set_txn_type::text, set_payment_method::text,
             set_country, set_tags, set_is_transfer_candidate, set_counterparty_account_id,
             origin, times_applied, last_applied_at"""


def _out(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def rule_row(values: tuple[Any, ...]) -> RuleRow:
    return RuleRow(
        id=values[0],
        name=values[1],
        priority=values[2],
        isActive=values[3],
        matchDescriptionPattern=values[4],
        matchPatternKind=values[5],
        matchAccountId=values[6],
        matchCurrency=values[7],
        matchAmountMin=_out(values[8]),
        matchAmountMax=_out(values[9]),
        matchAmountExact=_out(values[10]),
        matchDirection=values[11],
        matchCountry=values[12],
        matchReferencePattern=values[13],
        setCategoryId=values[14],
        setMerchantId=values[15],
        setTxnType=values[16],
        setPaymentMethod=values[17],
        setCountry=values[18],
        setTags=list(values[19] or []),
        setIsTransferCandidate=values[20],
        setCounterpartyAccountId=values[21],
        origin=values[22],
        timesApplied=values[23],
        lastAppliedAt=values[24].isoformat() if values[24] is not None else None,
    )


def _assert_category_allowed(
    cur: psycopg.Cursor[Any], category_id: UUID | None, household_id: UUID
) -> None:
    """The same predicate `routes/transactions.py` applies to a transaction's category, for the
    same reason: the foreign key is not tenant-aware, so this is the boundary."""
    if category_id is None:
        return
    cur.execute(
        """SELECT 1 FROM category c
             LEFT JOIN category_override o
                    ON o.category_id = c.id AND o.household_id = %s
            WHERE c.id = %s AND (c.household_id IS NULL OR c.household_id = %s)
              AND COALESCE(o.is_active, c.is_active)""",
        (household_id, category_id, household_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(status_code=422, detail="category is not available to this household")


def _assert_owned(
    cur: psycopg.Cursor[Any], table: str, row_id: UUID | None, household_id: UUID, subject: str
) -> None:
    """A merchant or an account named by a rule must be this household's. The table name is a
    literal chosen here, never interpolated from a request."""
    if row_id is None:
        return
    query = {
        "merchant": "SELECT 1 FROM merchant WHERE id = %s AND household_id = %s",
        "account": "SELECT 1 FROM account WHERE id = %s AND household_id = %s",
    }[table]
    cur.execute(query, (row_id, household_id))
    if cur.fetchone() is None:
        raise HTTPException(status_code=422, detail=f"{subject} is not available to this household")


def _assert_references(cur: psycopg.Cursor[Any], body: RuleBody, household_id: UUID) -> None:
    _assert_category_allowed(cur, body.setCategoryId, household_id)
    _assert_owned(cur, "merchant", body.setMerchantId, household_id, "merchant")
    _assert_owned(cur, "account", body.matchAccountId, household_id, "the matched account")
    _assert_owned(
        cur, "account", body.setCounterpartyAccountId, household_id, "the counterparty account"
    )


def _values(body: RuleBody, household_id: UUID) -> tuple[Any, ...]:
    return (
        household_id,
        body.name,
        body.priority,
        body.isActive,
        body.matchDescriptionPattern,
        body.matchPatternKind,
        body.matchAccountId,
        body.matchCurrency,
        body.matchAmountMin,
        body.matchAmountMax,
        body.matchAmountExact,
        body.matchDirection,
        body.matchCountry,
        body.matchReferencePattern,
        body.setCategoryId,
        body.setMerchantId,
        body.setTxnType,
        body.setPaymentMethod,
        body.setCountry,
        body.setTags,
        body.setIsTransferCandidate,
        body.setCounterpartyAccountId,
    )


@router.get("/rules")
def list_rules(owner: Annotated[OwnerSession, Depends(require_owner)]) -> list[RuleRow]:
    """Ascending priority, which is evaluation order — the list reads as the cascade runs."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {COLUMNS} FROM classification_rule WHERE household_id = %s"
            " ORDER BY priority, id",
            (owner.household_id,),
        )
        return [rule_row(row) for row in cur.fetchall()]


@router.post("/rules", status_code=201)
def create_rule(body: RuleBody, owner: Annotated[OwnerSession, Depends(require_owner)]) -> RuleRow:
    with connect() as conn, conn.cursor() as cur:
        _assert_references(cur, body, owner.household_id)
        cur.execute(
            f"""INSERT INTO classification_rule (
                    household_id, name, priority, is_active,
                    match_description_pattern, match_pattern_kind, match_account_id,
                    match_currency, match_amount_min, match_amount_max, match_amount_exact,
                    match_direction, match_country, match_reference_pattern,
                    set_category_id, set_merchant_id, set_txn_type, set_payment_method,
                    set_country, set_tags, set_is_transfer_candidate,
                    set_counterparty_account_id, origin)
                VALUES (%s, %s, %s, %s, %s, %s::pattern_type, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s::txn_type, %s::payment_method, %s, %s, %s, %s, '{ORIGIN_USER}')
                RETURNING {COLUMNS}""",
            _values(body, owner.household_id),
        )
        row = cur.fetchone()
        assert row is not None
        return rule_row(row)


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: UUID, body: RuleBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RuleRow:
    """A full replacement of the editable fields. `origin` and the counters are not editable:
    a rule that could rewrite its own provenance could claim to be a seed rule."""
    with connect() as conn, conn.cursor() as cur:
        _assert_references(cur, body, owner.household_id)
        cur.execute(
            f"""UPDATE classification_rule SET
                    name = %s, priority = %s, is_active = %s,
                    match_description_pattern = %s, match_pattern_kind = %s::pattern_type,
                    match_account_id = %s, match_currency = %s, match_amount_min = %s,
                    match_amount_max = %s, match_amount_exact = %s, match_direction = %s,
                    match_country = %s, match_reference_pattern = %s,
                    set_category_id = %s, set_merchant_id = %s, set_txn_type = %s::txn_type,
                    set_payment_method = %s::payment_method, set_country = %s, set_tags = %s,
                    set_is_transfer_candidate = %s, set_counterparty_account_id = %s,
                    updated_at = now()
                  WHERE id = %s AND household_id = %s
                RETURNING {COLUMNS}""",
            (*_values(body, owner.household_id)[1:], rule_id, owner.household_id),
        )
        row = cur.fetchone()
        if row is None:
            # One answer for "no such rule" and "another household's rule", as every sibling
            # route does: telling them apart tells a caller whether an id they guessed exists.
            raise HTTPException(status_code=404, detail="no such rule")
        return rule_row(row)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]) -> None:
    """Refused while any row names it, by V19's `RESTRICT`. A rule that classified something is
    that row's provenance (I6), and deactivating is how such a rule is retired."""
    with connect() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "DELETE FROM classification_rule WHERE id = %s AND household_id = %s",
                (rule_id, owner.household_id),
            )
        except psycopg.errors.RestrictViolation, psycopg.errors.ForeignKeyViolation:
            # RESTRICT raises 23001, not 23503. Both are caught because the distinction is
            # the constraint's action, not this route's business, and a future NO ACTION here
            # would otherwise turn a clear 409 into a 500.
            raise HTTPException(
                status_code=409,
                detail=(
                    "this rule classified rows that are already in the ledger, and they name it "
                    "as the reason; deactivate it instead so those rows keep their provenance"
                ),
            ) from None
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="no such rule")
