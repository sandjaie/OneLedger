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
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..coverage import HISTORY_ANCHOR
from ..db import connect
from ..ingest.classify import (
    Candidate,
    PatternKind,
    Rule,
    _first_matching,
    classify_row,
    proposed_pattern,
    rule_matches,
    validate_pattern,
)
from ..ingest.knowledge import load_knowledge
from .layouts import _preview_redactor
from .transactions import _assert_category_allowed

router = APIRouter(tags=["rules"])

# `origin` is not settable through this module. A rule the owner writes is `user_created`; the
# seed pack sets `seed` when it loads (3a4), and `learned_from_correction` is Phase 2's.
ORIGIN_USER = "user_created"
ORIGIN_SEED = "seed"

# The matchers `has_matcher` counts, and the outcomes `has_outcome` counts. Named here so the
# 422 can say which half is missing rather than letting a CHECK answer with a constraint name.
MATCHER_FIELDS = (
    "matchDescriptionPattern",
    "matchAccountId",
    "matchAmountExact",
    "matchReferencePattern",
)
OUTCOME_FIELDS = ("setCategoryId", "setMerchantId", "setTxnType", "setPaymentMethod")


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
                    validate_pattern(pattern, self.matchPatternKind)
        return self

    # A subclass may name a category some other way — `PackRule` names it by slug — and the
    # outcome check has to count it, or a pack rule with only a slug reads as deciding
    # nothing and is refused before anything can resolve it.
    _EXTRA_OUTCOMES: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def matches_something_and_decides_something(self) -> RuleBody:
        """The two CHECKs V19 carries, refused here so the owner reads a sentence rather than
        a constraint name — and so the half that is missing is named."""
        if not any(getattr(self, field) is not None for field in MATCHER_FIELDS):
            raise ValueError(
                "a rule needs something to match on: a description pattern, an account, an "
                "exact amount or a reference pattern"
            )
        outcomes = OUTCOME_FIELDS + self._EXTRA_OUTCOMES
        decides = any(getattr(self, field) is not None for field in outcomes)
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


def _assert_references(
    cur: psycopg.Cursor[Any], body: RuleBody, household_id: UUID, category_id: UUID | None = None
) -> None:
    _assert_category_allowed(cur, category_id or body.setCategoryId, household_id)
    _assert_owned(cur, "merchant", body.setMerchantId, household_id, "merchant")
    _assert_owned(cur, "account", body.matchAccountId, household_id, "the matched account")
    _assert_owned(
        cur, "account", body.setCounterpartyAccountId, household_id, "the counterparty account"
    )


# `accounts.py`'s pattern: the enum in the database is the vocabulary, not a second copy here
# that would drift from it, and an unknown value becomes a 422 rather than a 500.
WRITE_ERRORS = (
    psycopg.errors.InvalidTextRepresentation,
    psycopg.errors.CheckViolation,
    psycopg.errors.ForeignKeyViolation,
)


def _write_error(exc: psycopg.Error) -> str:
    """The database's own sentence, first line only. It names the enum and the value it
    refused, which is what the owner needs; it carries no document content."""
    return str(exc).splitlines()[0]


def _values(body: RuleBody, household_id: UUID, category_id: UUID | None = None) -> tuple[Any, ...]:
    """`category_id` is how a pack passes the id it resolved from a slug, without
    mutating the validated body to carry it."""
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
        category_id if category_id is not None else body.setCategoryId,
        body.setMerchantId,
        body.setTxnType,
        body.setPaymentMethod,
        body.setCountry,
        body.setTags,
        body.setIsTransferCandidate,
        body.setCounterpartyAccountId,
    )


INSERT_RULE = f"""INSERT INTO classification_rule (
    household_id, name, priority, is_active,
    match_description_pattern, match_pattern_kind, match_account_id,
    match_currency, match_amount_min, match_amount_max, match_amount_exact,
    match_direction, match_country, match_reference_pattern,
    set_category_id, set_merchant_id, set_txn_type, set_payment_method,
    set_country, set_tags, set_is_transfer_candidate,
    set_counterparty_account_id, origin)
VALUES (%s, %s, %s, %s, %s, %s::pattern_type, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s::txn_type, %s::payment_method, %s, %s, %s, %s, %s)
RETURNING {COLUMNS}"""


def _insert_rule(
    cur: psycopg.Cursor[Any],
    body: RuleBody,
    household_id: UUID,
    origin: str,
    category_id: UUID | None = None,
) -> RuleRow:
    """The one INSERT both writers share. `origin` is a parameter because it is the only thing
    that differs between a rule the owner writes and a rule a pack loads, and two copies of a
    twenty-three-column statement drift the day a twenty-fourth column arrives."""
    cur.execute(INSERT_RULE, (*_values(body, household_id, category_id), origin))
    row = cur.fetchone()
    assert row is not None
    return rule_row(row)


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
    try:
        with connect() as conn, conn.cursor() as cur:
            _assert_references(cur, body, owner.household_id)
            return _insert_rule(cur, body, owner.household_id, ORIGIN_USER)
    except WRITE_ERRORS as exc:
        raise HTTPException(status_code=422, detail=_write_error(exc)) from None


class PackRule(RuleBody):
    """A rule as a pack file writes it. The one addition is `setCategorySlug`: a pack cannot
    carry a category id, because V9 generates a different one in every database, so it names
    the slug and the load resolves it (`db/seeds/README.md`)."""

    _EXTRA_OUTCOMES: ClassVar[tuple[str, ...]] = ("setCategorySlug",)

    setCategorySlug: str | None = None

    @model_validator(mode="after")
    def one_way_of_naming_a_category(self) -> PackRule:
        if self.setCategorySlug is not None and self.setCategoryId is not None:
            raise ValueError("a rule names its category by slug or by id, not both")
        return self


class Pack(BaseModel):
    rules: list[PackRule] = Field(min_length=1)


def _category_by_slug(cur: psycopg.Cursor[Any], slug: str, household_id: UUID) -> UUID:
    """`categories.py`'s scoping: the household's own categories and the system seeds. Where
    both answer to a slug the household's own wins, which is what an override is for."""
    cur.execute(
        """SELECT id FROM category
            WHERE slug = %s AND (household_id IS NULL OR household_id = %s)
            ORDER BY household_id NULLS LAST
            LIMIT 1""",
        (slug, household_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=422, detail=f"no category has the slug {slug}")
    return UUID(str(found[0]))


@router.post("/rules/pack", status_code=201)
def load_pack(pack: Pack, owner: Annotated[OwnerSession, Depends(require_owner)]) -> list[RuleRow]:
    """The shipped packs in `db/seeds/rules/`, loaded once per household as `seed` rules.

    This exists because `POST /rules` refuses to set `origin` and must keep refusing: a rule
    the owner writes in Settings is `user_created`, and a body that could claim otherwise
    would put itself in the wrong half of the cascade and lie about where it came from.
    `origin` is decided by what did the writing, and this route is that writer.

    One pack is one transaction. A slug nothing answers to refuses the whole load rather than
    leaving the rules before it behind, because half a pack is a cascade nobody designed.
    """
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM classification_rule"
                " WHERE household_id = %s AND origin = 'seed' LIMIT 1",
                (owner.household_id,),
            )
            if cur.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="this household already holds seed rules; a pack loads once",
                )
            loaded = []
            for rule in pack.rules:
                category_id = (
                    None
                    if rule.setCategorySlug is None
                    else _category_by_slug(cur, rule.setCategorySlug, owner.household_id)
                )
                _assert_references(cur, rule, owner.household_id, category_id)
                loaded.append(_insert_rule(cur, rule, owner.household_id, ORIGIN_SEED, category_id))
            return loaded
    except WRITE_ERRORS as exc:
        raise HTTPException(status_code=422, detail=_write_error(exc)) from None


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: UUID, body: RuleBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RuleRow:
    """A full replacement of the editable fields. `origin` and the counters are not editable:
    a rule that could rewrite its own provenance could claim to be a seed rule."""
    try:
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
    except WRITE_ERRORS as exc:
        raise HTTPException(status_code=422, detail=_write_error(exc)) from None


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


# ---------------------------------------------------------------------------------------
# Read-then-act: what a candidate rule would do, and running the cascade over rows that have
# no answer yet. Both read the base table rather than `v_transaction_reportable` — deliberately,
# and for the reason `GET /transactions` already does: neither is a report. A spending figure
# must exclude transfers and adjustments; "which rows would this rule touch" must not, or a rule
# about a card payment would preview as matching nothing.
# ---------------------------------------------------------------------------------------

# A sample, not a page. The count is the answer; the rows are there so the owner can see the
# rule is matching what they meant, and twenty is enough to tell that.
SAMPLE_ROWS = 20
_DESCRIPTION_REDACTOR = _preview_redactor(None)


class MatchedRow(BaseModel):
    """One row a rule would touch. Money is a string; nothing here computes with it."""

    id: UUID
    bookedDate: str
    descriptionRaw: str
    amount: str
    currency: str
    classifiedBy: str


class PreviewResult(BaseModel):
    affected: int
    unclassified: int
    sample: list[MatchedRow]


class ClassifyResult(BaseModel):
    """`preview=true` reports what would change and writes nothing; otherwise `changed` is what
    did. `considered` is every row with no answer, so a small `changed` reads as "the rules do
    not cover these yet" rather than as a failure."""

    considered: int
    changed: int
    sample: list[MatchedRow]
    applied: bool


def _matched_row(values: tuple[Any, ...]) -> MatchedRow:
    return MatchedRow(
        id=values[0],
        bookedDate=values[1].isoformat(),
        descriptionRaw=_DESCRIPTION_REDACTOR(values[2]),
        amount=str(values[3]),
        currency=values[4],
        classifiedBy=values[5],
    )


LEDGER_COLUMNS = """id, booked_date, description_raw, amount, currency, classified_by::text,
                    account_id, description_clean, reference_raw, country"""


def _candidates(
    cur: psycopg.Cursor[Any], household_id: UUID, unclassified_only: bool
) -> list[tuple[Any, ...]]:
    cur.execute(
        f"""SELECT {LEDGER_COLUMNS} FROM transaction
             WHERE household_id = %s
               AND (%s IS FALSE OR classified_by = 'unclassified')
             ORDER BY booked_date DESC, id""",
        (household_id, unclassified_only),
    )
    return list(cur.fetchall())


def _as_candidate(values: tuple[Any, ...]) -> Candidate:
    return Candidate(
        account_id=values[6],
        description_clean=values[7] or values[2],
        amount=values[3],
        currency=values[4],
        reference_raw=values[8],
        country=values[9],
    )


@router.post("/rules/preview")
def preview_rule(
    body: RuleBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> PreviewResult:
    """What a candidate rule would match, before it exists. Writes nothing."""
    draft = Rule(
        id=UUID(int=0),
        priority=body.priority,
        origin=ORIGIN_USER,
        match_description_pattern=body.matchDescriptionPattern,
        match_pattern_kind=body.matchPatternKind,
        match_account_id=body.matchAccountId,
        match_currency=body.matchCurrency,
        match_amount_min=_decimal(body.matchAmountMin),
        match_amount_max=_decimal(body.matchAmountMax),
        match_amount_exact=_decimal(body.matchAmountExact),
        match_direction=body.matchDirection,
        match_country=body.matchCountry,
        match_reference_pattern=body.matchReferencePattern,
    )
    with connect() as conn, conn.cursor() as cur:
        _assert_references(cur, body, owner.household_id)
        matched = [
            row
            for row in _candidates(cur, owner.household_id, unclassified_only=False)
            if rule_matches(draft, _as_candidate(row))
        ]
    return PreviewResult(
        affected=len(matched),
        unclassified=sum(row[5] == "unclassified" for row in matched),
        sample=[_matched_row(row) for row in matched[:SAMPLE_ROWS]],
    )


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


@router.post("/classify")
def classify_unclassified(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    preview: bool = False,
    ruleId: UUID | None = None,
) -> ClassifyResult:
    """Run the cascade over the rows that have no answer, and over nothing else.

    This can only fill a row that is `unclassified`. It can never overwrite `user_manual`,
    `user_rule`, `merchant_alias`, `prior_match` or `seed_rule` — rules never silently change
    past data — and it never runs as a side effect of creating or editing a rule. Re-applying a
    rule over rows that already have an answer is Phase 2's learn-from-correction.
    """
    with connect() as conn, conn.cursor() as cur:
        if ruleId is not None:
            cur.execute(
                """SELECT 1 FROM classification_rule
                    WHERE id = %s AND household_id = %s AND is_active""",
                (ruleId, owner.household_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such active rule")
        knowledge = load_knowledge(cur, owner.household_id)
        rows = _candidates(cur, owner.household_id, unclassified_only=True)

        decided: list[tuple[tuple[Any, ...], Any]] = []
        for row in rows:
            answer = classify_row(_as_candidate(row), knowledge)
            if answer.classified_by != "unclassified" and (
                ruleId is None or answer.classification_rule_id == ruleId
            ):
                decided.append((row, answer))

        written: list[tuple[tuple[Any, ...], Any]] = []
        if not preview:
            for row, answer in decided:
                cur.execute(
                    """UPDATE transaction SET
                           category_id = COALESCE(%s, category_id),
                           merchant_id = COALESCE(%s, merchant_id),
                           txn_type = COALESCE(%s::txn_type, txn_type),
                           payment_method = COALESCE(%s::payment_method, payment_method),
                           classified_by = %s::classified_by,
                           classification_rule_id = %s,
                           classification_confidence = %s
                         WHERE id = %s AND household_id = %s
                           AND classified_by = 'unclassified'""",
                    (
                        answer.category_id,
                        answer.merchant_id,
                        answer.txn_type,
                        answer.payment_method,
                        answer.classified_by,
                        answer.classification_rule_id,
                        answer.classification_confidence,
                        row[0],
                        owner.household_id,
                    ),
                )
                if cur.rowcount == 1:
                    written.append((row, answer))
            # The counters V19 marks as the cascade's to maintain. Written once per rule per
            # run, not once per row, so a run over four thousand rows is not four thousand
            # updates to one rule.
            applied: dict[UUID, int] = {}
            for _, answer in written:
                if answer.classification_rule_id is not None:
                    applied[answer.classification_rule_id] = (
                        applied.get(answer.classification_rule_id, 0) + 1
                    )
            for rule_id, count in applied.items():
                cur.execute(
                    """UPDATE classification_rule
                          SET times_applied = times_applied + %s, last_applied_at = now()
                        WHERE id = %s AND household_id = %s""",
                    (count, rule_id, owner.household_id),
                )

    changed = decided if preview else written
    return ClassifyResult(
        considered=len(rows),
        changed=len(changed),
        sample=[_matched_row(row) for row, _ in changed[:SAMPLE_ROWS]],
        applied=not preview,
    )


# ---------------------------------------------------------------------------------------
# Phase 2.3: read-only measurement over what the cascade has already decided (coverage, per-rule
# hits and disagreements, the top unclassified descriptions), and read-only suggestions of narrow
# rules derived only from the owner's own corrections. Neither route writes a rule or a
# transaction; every count a suggestion carries is computed the same way `POST /rules/preview`
# already computes `affected`/`unclassified`, over the same base-table row set, so the figures a
# suggestion shows are exactly what creating and previewing that rule would show.
# ---------------------------------------------------------------------------------------

CLASSIFIED_BY_VALUES = (
    "user_manual",
    "user_rule",
    "merchant_alias",
    "recurring_series",
    "prior_match",
    "seed_rule",
    "unclassified",
    "ai",
)
CONFIDENCE_BANDS = ("none", "below_0.5", "0.5_to_0.8", "above_0.8")


class CountAndPercent(BaseModel):
    count: int
    percent: str | None


def _count_and_percent(count: int, total: int) -> CountAndPercent:
    if total == 0:
        return CountAndPercent(count=count, percent=None)
    percent = (Decimal(count) * 100 / total).quantize(Decimal("0.01"))
    return CountAndPercent(count=count, percent=str(percent))


class RuleAnalyticsCoverage(BaseModel):
    totalRows: int
    byClassifiedBy: dict[str, CountAndPercent]
    confidenceBands: dict[str, CountAndPercent]
    reportableUncategorized: CountAndPercent


def _coverage(cur: psycopg.Cursor[Any], household_id: UUID, today: date) -> RuleAnalyticsCoverage:
    window = (household_id, HISTORY_ANCHOR, today)
    cur.execute(
        "SELECT classified_by::text, count(*) FROM v_transaction_posting"
        " WHERE household_id = %s AND booked_date BETWEEN %s AND %s GROUP BY classified_by",
        window,
    )
    by_status = dict(cur.fetchall())
    total = sum(by_status.values())
    cur.execute(
        """SELECT CASE WHEN classification_confidence IS NULL THEN 'none'
                        WHEN classification_confidence < 0.5 THEN 'below_0.5'
                        WHEN classification_confidence < 0.8 THEN '0.5_to_0.8'
                        ELSE 'above_0.8' END,
                  count(*)
             FROM v_transaction_posting
            WHERE household_id = %s AND booked_date BETWEEN %s AND %s
            GROUP BY 1""",
        window,
    )
    by_band = dict(cur.fetchall())
    # `v_transaction_reportable` already carries the reportable predicate (I3); reading it here
    # is cheaper and more honest than re-deriving that predicate from `REPORT_EXCLUDING_TYPES`.
    # Its own count, not `total`, is this figure's denominator: "reportable rows with no
    # category" is a question about reportable rows, and a household with many transfers would
    # otherwise show a percentage no reportable-row count actually supports.
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE category_id IS NULL)"
        " FROM v_transaction_reportable WHERE household_id = %s AND booked_date BETWEEN %s AND %s",
        window,
    )
    reportable_total, uncategorized = cur.fetchone()  # type: ignore[misc]
    return RuleAnalyticsCoverage(
        totalRows=total,
        byClassifiedBy={
            status: _count_and_percent(by_status.get(status, 0), total)
            for status in CLASSIFIED_BY_VALUES
        },
        confidenceBands={
            band: _count_and_percent(by_band.get(band, 0), total) for band in CONFIDENCE_BANDS
        },
        reportableUncategorized=_count_and_percent(uncategorized, reportable_total),
    )


class DisagreementSample(BaseModel):
    bookedDate: str
    description: str
    amount: str


class RuleHit(BaseModel):
    ruleId: UUID
    name: str | None
    origin: str
    hits: int
    lastHitDate: str | None
    disagreements: int
    disagreementSample: list[DisagreementSample]


ANALYTICS_ROW_COLUMNS = """id, booked_date, description_raw, description_clean, amount, currency,
                           account_id, reference_raw, country, category_id, txn_type::text,
                           classification_rule_id"""


def _rule_outcome_differs(rule: Rule, category_id: UUID | None, txn_type: str | None) -> bool:
    """Only the fields the rule actually decides count as a disagreement — a rule silent on
    category was never claiming one, so a row that merely has some category is not a dispute."""
    if rule.set_category_id is not None and category_id != rule.set_category_id:
        return True
    return rule.set_txn_type is not None and txn_type != rule.set_txn_type


def _rule_hits(cur: psycopg.Cursor[Any], household_id: UUID) -> list[RuleHit]:
    cur.execute(
        f"SELECT {COLUMNS} FROM classification_rule"
        " WHERE household_id = %s AND is_active ORDER BY priority, id",
        (household_id,),
    )
    active = [rule_row(row) for row in cur.fetchall()]
    if not active:
        return []

    knowledge = load_knowledge(cur, household_id)

    cur.execute(
        """SELECT classification_rule_id, count(*), max(booked_date)
             FROM v_transaction_posting
            WHERE household_id = %s AND classified_by IN ('user_rule', 'seed_rule')
            GROUP BY classification_rule_id""",
        (household_id,),
    )
    hit_stats = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    cur.execute(
        f"SELECT {ANALYTICS_ROW_COLUMNS} FROM v_transaction_posting"
        " WHERE household_id = %s ORDER BY booked_date DESC, id",
        (household_id,),
    )
    disagreement_samples: dict[UUID, list[tuple[Any, ...]]] = defaultdict(list)
    for values in cur.fetchall():
        (
            _id,
            booked_date,
            description_raw,
            description_clean,
            amount,
            currency,
            account_id,
            reference_raw,
            country,
            category_id,
            txn_type,
            classification_rule_id,
        ) = values
        candidate = Candidate(
            account_id=account_id,
            description_clean=description_clean or description_raw,
            amount=amount,
            currency=currency,
            reference_raw=reference_raw,
            country=country,
        )
        # Each rule origin only ever competes within its own group in the real cascade (user
        # rules at step 2, seed rules at step 5) — `_first_matching` per group is what the
        # classifier actually evaluates, so disagreements are computed the same way per group.
        for group in (knowledge.user_rules, knowledge.seed_rules):
            chosen = _first_matching(group, candidate)
            if (
                chosen is not None
                and _rule_outcome_differs(chosen, category_id, txn_type)
                and classification_rule_id != chosen.id
            ):
                disagreement_samples[chosen.id].append((booked_date, description_raw, amount))

    result: list[RuleHit] = []
    for rule in active:
        hits, last_hit = hit_stats.get(rule.id, (0, None))
        samples = disagreement_samples.get(rule.id, [])
        result.append(
            RuleHit(
                ruleId=rule.id,
                name=rule.name,
                origin=rule.origin,
                hits=hits,
                lastHitDate=last_hit.isoformat() if last_hit is not None else None,
                disagreements=len(samples),
                disagreementSample=[
                    DisagreementSample(
                        bookedDate=booked.isoformat(),
                        description=_DESCRIPTION_REDACTOR(description),
                        amount=str(amount),
                    )
                    for booked, description, amount in samples[:SAMPLE_ROWS]
                ],
            )
        )
    return result


class TopUnclassifiedDescription(BaseModel):
    description: str
    count: int
    totalAbsAmountBase: str


def _normalise_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _top_unclassified(
    cur: psycopg.Cursor[Any], household_id: UUID
) -> list[TopUnclassifiedDescription]:
    # Ordered so that, among rows a whitespace/case difference groups together, the group's
    # displayed text is always the same one — the earliest booked row's — rather than whichever
    # row the database happens to return first.
    cur.execute(
        "SELECT description_raw, description_clean, amount_base FROM v_transaction_posting"
        " WHERE household_id = %s AND classified_by = 'unclassified'"
        " ORDER BY booked_date, id",
        (household_id,),
    )
    groups: dict[str, dict[str, Any]] = {}
    for description_raw, description_clean, amount_base in cur.fetchall():
        text = _normalise_whitespace(description_clean or description_raw)
        key = text.casefold()
        bucket = groups.setdefault(key, {"description": text, "count": 0, "total": Decimal(0)})
        bucket["count"] += 1
        bucket["total"] += abs(amount_base)
    ordered = sorted(groups.values(), key=lambda g: (-g["count"], -g["total"]))
    return [
        TopUnclassifiedDescription(
            description=_DESCRIPTION_REDACTOR(g["description"]),
            count=g["count"],
            totalAbsAmountBase=str(g["total"]),
        )
        for g in ordered[:SAMPLE_ROWS]
    ]


class RuleAnalytics(BaseModel):
    coverage: RuleAnalyticsCoverage
    rules: list[RuleHit]
    topUnclassified: list[TopUnclassifiedDescription]


@router.get("/rules/analytics")
def rule_analytics(owner: Annotated[OwnerSession, Depends(require_owner)]) -> RuleAnalytics:
    """Owner-only, read-only measurement of what the cascade has already decided. Nothing here
    writes a rule or a transaction (`classification.html` §7's measurement section)."""
    with connect() as conn, conn.cursor() as cur:
        coverage = _coverage(cur, owner.household_id, date.today())
        rules = _rule_hits(cur, owner.household_id)
        top_unclassified = _top_unclassified(cur, owner.household_id)
    return RuleAnalytics(coverage=coverage, rules=rules, topUnclassified=top_unclassified)


class RuleSuggestion(BaseModel):
    pattern: str
    direction: Literal["debit", "credit"]
    categoryId: UUID | None
    categoryName: str | None
    txnType: str
    evidenceRows: int
    affected: int
    unclassified: int


class RuleSuggestionsResponse(BaseModel):
    suggestions: list[RuleSuggestion]


def _already_covered(
    rule: Rule,
    pattern: str,
    direction: Literal["debit", "credit"],
    category_id: UUID | None,
    txn_type: str,
) -> bool:
    """An active rule already produces this exact outcome for this exact pattern — the reason a
    suggestion built from the same corrections would be redundant.

    A rule silent on transaction type — every rule the classify prompt itself creates, which sets
    only a category (`classify-prompt.tsx`'s `ruleBody`) — is not thereby claiming "no type": it
    is claiming every type, so it already covers a suggestion regardless of that suggestion's
    transaction type.
    """
    return (
        rule.match_pattern_kind == "contains"
        and rule.match_description_pattern is not None
        and rule.match_description_pattern.casefold() == pattern.casefold()
        and rule.match_direction in ("any", direction)
        and rule.set_category_id == category_id
        and (rule.set_txn_type is None or rule.set_txn_type == txn_type)
    )


MIN_SUGGESTION_EVIDENCE = 3
MAX_SUGGESTIONS = 20


@router.get("/rules/suggestions")
def rule_suggestions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RuleSuggestionsResponse:
    """A suggestion is a candidate rule derived only from owner corrections the owner has not
    already turned into a rule. Never writes a rule; creating one still goes through
    `POST /rules`, and applying it to past rows is a separate owner step through the existing
    `POST /classify?ruleId=` cascade pass — previewed (`?preview=true`) before it writes, and
    reaching only rows that are currently `unclassified` (`classification.html` §7)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT t.description_raw, t.description_clean, t.amount, t.category_id, c.name,
                      t.txn_type::text
                 FROM v_transaction_reportable t
            LEFT JOIN category c ON c.id = t.category_id
                WHERE t.household_id = %s AND t.classified_by = 'user_manual'
                ORDER BY t.booked_date, t.id""",
            (owner.household_id,),
        )
        manual_rows = cur.fetchall()
        knowledge = load_knowledge(cur, owner.household_id)
        active_rules = [*knowledge.user_rules, *knowledge.seed_rules]

        groups: dict[tuple[str, str, UUID | None, str], dict[str, Any]] = {}
        for (
            description_raw,
            description_clean,
            amount,
            category_id,
            category_name,
            txn_type,
        ) in manual_rows:
            pattern = proposed_pattern(description_clean or description_raw)
            if not pattern:
                continue
            direction = "debit" if amount < 0 else "credit"
            key = (pattern.casefold(), direction, category_id, txn_type)
            bucket = groups.setdefault(
                key,
                {
                    "pattern": pattern,
                    "direction": direction,
                    "categoryId": category_id,
                    "categoryName": category_name,
                    "txnType": txn_type,
                    "evidenceRows": 0,
                },
            )
            bucket["evidenceRows"] += 1

        candidates = [
            group for group in groups.values() if group["evidenceRows"] >= MIN_SUGGESTION_EVIDENCE
        ]
        candidates = [
            group
            for group in candidates
            if not any(
                _already_covered(
                    rule,
                    group["pattern"],
                    group["direction"],
                    group["categoryId"],
                    group["txnType"],
                )
                for rule in active_rules
            )
        ]
        candidates.sort(key=lambda group: (-group["evidenceRows"], group["pattern"].casefold()))
        candidates = candidates[:MAX_SUGGESTIONS]

        rows = _candidates(cur, owner.household_id, unclassified_only=False)
        suggestions = []
        for group in candidates:
            draft = Rule(
                id=UUID(int=0),
                priority=100,
                origin=ORIGIN_USER,
                match_description_pattern=group["pattern"],
                match_pattern_kind="contains",
                match_direction=group["direction"],
                set_category_id=group["categoryId"],
                set_txn_type=group["txnType"],
            )
            matched = [row for row in rows if rule_matches(draft, _as_candidate(row))]
            suggestions.append(
                RuleSuggestion(
                    pattern=group["pattern"],
                    direction=group["direction"],
                    categoryId=group["categoryId"],
                    categoryName=group["categoryName"],
                    txnType=group["txnType"],
                    evidenceRows=group["evidenceRows"],
                    affected=len(matched),
                    unclassified=sum(row[5] == "unclassified" for row in matched),
                )
            )
    return RuleSuggestionsResponse(suggestions=suggestions)
