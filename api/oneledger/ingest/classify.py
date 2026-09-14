"""The classification cascade, as a function of values (`classification.html` §1).

Evaluated in order, first match wins, evaluation stops:

    user override → user rule → merchant alias → prior match → seed rule → unclassified

`recurring_series` is Phase 2 and is **absent, not stubbed**: a step that always declines is
indistinguishable from one that is broken, and the confidence table would then carry a row no
code can produce.

This module issues no query and imports nothing from `routes/`
(`testing-and-data-quality.html` §2). Rules, aliases and priors arrive as arguments, so the
cascade is provable on values with no database and no HTTP — which is the point, because the
answer it gives at import must be the same answer it gives when the rules screen re-runs it.

**There is deliberately no bare `classify` exported here.** `pipeline.py` already imports a
`classify` from `dedupe`, and two of them in one module is how the wrong one gets called. The
entry point is `classify_row`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal
from uuid import UUID

# `classification.html` §1's table, exactly. These are the confidences the review screen bands
# on, so they are constants here rather than magic numbers at each return.
CONFIDENCE_USER_MANUAL = Decimal("1.000")
CONFIDENCE_USER_RULE = Decimal("0.980")
CONFIDENCE_MERCHANT_ALIAS = Decimal("0.950")
CONFIDENCE_PRIOR_EXACT = Decimal("0.900")
CONFIDENCE_SEED_RULE = Decimal("0.800")
CONFIDENCE_PRIOR_FUZZY = Decimal("0.700")
CONFIDENCE_UNCLASSIFIED = Decimal("0.000")

# Below this a row surfaces for review rather than arriving pre-ticked (§1).
PRE_TICK_THRESHOLD = Decimal("0.800")
# A fuzzy prior below this is not evidence. §1 fixes it at 0.85.
FUZZY_FLOOR = 0.85

PatternKind = Literal["exact", "prefix", "contains", "regex"]
Direction = Literal["debit", "credit", "any"]

# A regex with nested repeats can take exponential time on a narration that nearly matches.
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*[+*]|\(\?:[^()]*[+*][^()]*\)\s*[+*]")


def validate_pattern(pattern: str, kind: PatternKind) -> None:
    """Validate every persisted matcher at its write boundary."""
    if kind != "regex":
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression ({exc})") from None
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError(
            "this pattern nests one quantifier inside another, which can take exponential time "
            "on an input that nearly matches; rewrite it without the nested repeat"
        )


@dataclass(frozen=True)
class Rule:
    """One `classification_rule` row, as values. Every matcher is optional; a null one is
    ignored, so a rule with only a description pattern matches on description alone (§2)."""

    id: UUID
    priority: int
    origin: str
    match_description_pattern: str | None = None
    match_pattern_kind: PatternKind = "contains"
    match_account_id: UUID | None = None
    match_currency: str | None = None
    match_amount_min: Decimal | None = None
    match_amount_max: Decimal | None = None
    match_amount_exact: Decimal | None = None
    match_direction: Direction = "any"
    match_country: str | None = None
    match_reference_pattern: str | None = None
    set_category_id: UUID | None = None
    set_merchant_id: UUID | None = None
    set_txn_type: str | None = None
    set_payment_method: str | None = None
    set_country: str | None = None
    set_tags: tuple[str, ...] = ()
    set_is_transfer_candidate: bool = False
    set_counterparty_account_id: UUID | None = None


@dataclass(frozen=True)
class Alias:
    """A merchant alias or an evidenced V24 counterparty alias."""

    merchant_id: UUID | None
    pattern: str
    pattern_kind: PatternKind = "contains"
    priority: int = 100
    category_id: UUID | None = None
    counterparty_id: UUID | None = None
    knowledge_version_id: UUID | None = None
    role: str | None = None
    direction: Direction = "any"
    account_id: UUID | None = None
    txn_type: str | None = None
    payment_method: str | None = None


@dataclass(frozen=True)
class Prior:
    """A row already classified in this account, as the evidence a later row matches against."""

    account_id: UUID
    description_clean: str
    category_id: UUID | None = None
    merchant_id: UUID | None = None
    txn_type: str | None = None
    payment_method: str | None = None
    trusted: bool = True
    direction: Direction = "any"


@dataclass(frozen=True)
class Candidate:
    """The row being classified, in the ledger's frame — `amount` is already signed (I2)."""

    account_id: UUID
    description_clean: str
    amount: Decimal
    currency: str
    reference_raw: str | None = None
    country: str | None = None
    override_category_id: UUID | None = None
    override_merchant_id: UUID | None = None


@dataclass(frozen=True)
class Classification:
    """What the cascade decided, and which step decided it."""

    classified_by: str
    classification_confidence: Decimal
    classification_rule_id: UUID | None = None
    category_id: UUID | None = None
    merchant_id: UUID | None = None
    txn_type: str | None = None
    payment_method: str | None = None
    country: str | None = None
    tags: tuple[str, ...] = ()
    # Read by 3b2's matcher. A rule is how a household says "this narration is a transfer".
    is_transfer_candidate: bool = False
    counterparty_account_id: UUID | None = None
    counterparty_id: UUID | None = None
    knowledge_version_id: UUID | None = None
    counterparty_role: str | None = None
    unresolved_reason: str | None = None

    @property
    def is_pre_ticked(self) -> bool:
        return self.classification_confidence >= PRE_TICK_THRESHOLD


UNCLASSIFIED = Classification(
    classified_by="unclassified", classification_confidence=CONFIDENCE_UNCLASSIFIED
)


def matches_pattern(pattern: str, kind: PatternKind, text: str) -> bool:
    """Case-insensitive throughout: a statement's narration is upper-case on three of the four
    producers this repository has met, and a rule the owner typed in lower case must still
    match. A `regex` pattern is compiled at rule creation, not here — an uncompilable one can
    never reach this function, and one that backtracks catastrophically is refused there too."""
    haystack = text.casefold()
    needle = pattern.casefold()
    if kind == "exact":
        return haystack == needle
    if kind == "prefix":
        return haystack.startswith(needle)
    if kind == "regex":
        return re.search(pattern, text, re.IGNORECASE) is not None
    return needle in haystack


def proposed_pattern(description: str) -> str:
    """The Python twin of `classify-prompt.tsx`'s `proposedPattern`: the longest usable
    substring of one row's description, never an identifier-bearing one.

    Splits on runs of Unicode decimal digits (any script, matching the web's `\\p{Nd}`) and of
    `/ * # :`, trims each piece, keeps only pieces of at least 3 characters, and returns the
    longest — the first one seen when two tie, exactly as `Array.reduce`'s `>` does. Proven
    identical to the web function on the shared vectors in `proposed-pattern-vectors.json`
    (`tests/test_ingest_classify.py`, `app/transactions/__tests__/proposed-pattern.test.ts`).
    """

    def is_separator(ch: str) -> bool:
        return ch in "/*#:" or unicodedata.category(ch) == "Nd"

    parts: list[str] = []
    current: list[str] = []
    for ch in description:
        if is_separator(ch):
            if current:
                parts.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))

    longest = ""
    for part in parts:
        trimmed = part.strip()
        if len(trimmed) >= 3 and len(trimmed) > len(longest):
            longest = trimmed
    return longest


def _amount_matches(rule: Rule, amount: Decimal) -> bool:
    """The direction matcher decides which sign the row must carry; the bounds compare against
    the magnitude, because a rule reading "over 500" means 500 out or 500 in, not −500."""
    if rule.match_direction == "debit" and amount >= 0:
        return False
    if rule.match_direction == "credit" and amount <= 0:
        return False
    magnitude = amount.copy_abs()
    if rule.match_amount_exact is not None and magnitude != rule.match_amount_exact.copy_abs():
        return False
    if rule.match_amount_min is not None and magnitude < rule.match_amount_min:
        return False
    if rule.match_amount_max is not None and magnitude > rule.match_amount_max:
        return False
    return True


def rule_matches(rule: Rule, row: Candidate) -> bool:
    """Every non-null condition must match; absent conditions are ignored (§2)."""
    if rule.match_description_pattern is not None and not matches_pattern(
        rule.match_description_pattern, rule.match_pattern_kind, row.description_clean
    ):
        return False
    if rule.match_reference_pattern is not None and not matches_pattern(
        rule.match_reference_pattern, rule.match_pattern_kind, row.reference_raw or ""
    ):
        return False
    if rule.match_account_id is not None and rule.match_account_id != row.account_id:
        return False
    if rule.match_currency is not None and rule.match_currency != row.currency:
        return False
    if rule.match_country is not None and rule.match_country != row.country:
        return False
    return _amount_matches(rule, row.amount)


def _from_rule(rule: Rule, confidence: Decimal, step: str) -> Classification:
    return Classification(
        classified_by=step,
        classification_confidence=confidence,
        classification_rule_id=rule.id,
        category_id=rule.set_category_id,
        merchant_id=rule.set_merchant_id,
        txn_type=rule.set_txn_type,
        payment_method=rule.set_payment_method,
        country=rule.set_country,
        tags=tuple(rule.set_tags),
        is_transfer_candidate=rule.set_is_transfer_candidate,
        counterparty_account_id=rule.set_counterparty_account_id,
    )


def _first_matching(rules: list[Rule], row: Candidate) -> Rule | None:
    """Ascending priority, then a stable tiebreak. Two rules at the same priority both matching
    is a household's own ambiguity, and resolving it by whichever the database happened to
    return would make the same ledger classify differently on two runs."""
    ordered = sorted(rules, key=lambda rule: (rule.priority, str(rule.id)))
    return next((rule for rule in ordered if rule_matches(rule, row)), None)


def _alias_matches(alias: Alias, row: Candidate) -> bool:
    if alias.account_id is not None and alias.account_id != row.account_id:
        return False
    if alias.direction == "debit" and row.amount >= 0:
        return False
    if alias.direction == "credit" and row.amount <= 0:
        return False
    return matches_pattern(alias.pattern, alias.pattern_kind, row.description_clean)


def _alias_rank(alias: Alias) -> tuple[int, int, int]:
    """Priority first, then matcher precision and service-name specificity."""
    kind_rank = {"exact": 0, "prefix": 1, "contains": 2, "regex": 3}
    return alias.priority, kind_rank[alias.pattern_kind], -len(alias.pattern)


def _alias_outcome(alias: Alias) -> tuple[object, ...]:
    return (
        alias.merchant_id,
        alias.category_id,
        alias.txn_type,
        alias.payment_method,
        alias.counterparty_id,
        alias.role,
    )


def _from_alias(aliases: list[Alias], row: Candidate) -> Classification | None:
    matched = [alias for alias in aliases if _alias_matches(alias, row)]
    if not matched:
        return None
    best_rank = min(_alias_rank(alias) for alias in matched)
    strongest = [alias for alias in matched if _alias_rank(alias) == best_rank]
    if len({_alias_outcome(alias) for alias in strongest}) > 1:
        return Classification(
            classified_by="unclassified",
            classification_confidence=CONFIDENCE_UNCLASSIFIED,
            unresolved_reason="conflicting highest-priority knowledge",
        )
    chosen = min(
        strongest,
        key=lambda alias: (
            str(alias.counterparty_id or UUID(int=0)),
            str(alias.knowledge_version_id or UUID(int=0)),
            str(alias.merchant_id or UUID(int=0)),
        ),
    )
    return Classification(
        classified_by="merchant_alias",
        classification_confidence=CONFIDENCE_MERCHANT_ALIAS,
        merchant_id=chosen.merchant_id,
        category_id=chosen.category_id,
        txn_type=chosen.txn_type,
        payment_method=chosen.payment_method,
        counterparty_id=chosen.counterparty_id,
        knowledge_version_id=chosen.knowledge_version_id,
        counterparty_role=chosen.role,
    )


def _from_prior(priors: list[Prior], row: Candidate) -> Classification | None:
    """Same account and same text is the evidence; a near-identical text is weaker evidence and
    says so. Fuzzy below the floor is not evidence at all and returns nothing — the engine
    abstains rather than guessing, which is what keeps `unclassified` meaningful."""
    report_excluding = {
        "transfer_in",
        "transfer_out",
        "investment_buy",
        "investment_sell",
        "investment_income",
        "investment_fee",
        "loan_disbursement",
        "loan_repayment",
        "adjustment",
        "opening_balance",
    }
    direction = "debit" if row.amount < 0 else "credit"
    same_account = [
        prior
        for prior in priors
        if prior.account_id == row.account_id
        and prior.trusted
        and prior.direction in {"any", direction}
        and prior.txn_type not in report_excluding
    ]
    text = row.description_clean.casefold()

    exact = [prior for prior in same_account if prior.description_clean.casefold() == text]
    if exact:
        return _resolve_priors(exact, CONFIDENCE_PRIOR_EXACT)

    scored: list[tuple[float, Prior]] = []
    for prior in same_account:
        score = SequenceMatcher(None, prior.description_clean.casefold(), text).ratio()
        if score >= FUZZY_FLOOR:
            scored.append((score, prior))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    return _resolve_priors(
        [prior for score, prior in scored if score == best_score], CONFIDENCE_PRIOR_FUZZY
    )


def _prior_outcome(prior: Prior) -> tuple[object, ...]:
    return prior.category_id, prior.merchant_id, prior.txn_type, prior.payment_method


def _resolve_priors(priors: list[Prior], confidence: Decimal) -> Classification:
    if len({_prior_outcome(prior) for prior in priors}) > 1:
        return Classification(
            classified_by="unclassified",
            classification_confidence=CONFIDENCE_UNCLASSIFIED,
            unresolved_reason="conflicting prior evidence",
        )
    return _prior_classification(priors[0], confidence)


def _prior_classification(prior: Prior, confidence: Decimal) -> Classification:
    return Classification(
        classified_by="prior_match",
        classification_confidence=confidence,
        category_id=prior.category_id,
        merchant_id=prior.merchant_id,
        txn_type=prior.txn_type,
        payment_method=prior.payment_method,
    )


@dataclass(frozen=True)
class Knowledge:
    """Everything the cascade may consult, gathered by the caller and passed as values."""

    user_rules: list[Rule] = field(default_factory=list)
    seed_rules: list[Rule] = field(default_factory=list)
    aliases: list[Alias] = field(default_factory=list)
    priors: list[Prior] = field(default_factory=list)


def classify_row(row: Candidate, knowledge: Knowledge) -> Classification:
    """The cascade. First match wins and evaluation stops (`classification.html` §1)."""
    if row.override_category_id is not None or row.override_merchant_id is not None:
        return Classification(
            classified_by="user_manual",
            classification_confidence=CONFIDENCE_USER_MANUAL,
            category_id=row.override_category_id,
            merchant_id=row.override_merchant_id,
        )

    rule = _first_matching(knowledge.user_rules, row)
    if rule is not None:
        return _from_rule(rule, CONFIDENCE_USER_RULE, "user_rule")

    alias = _from_alias(knowledge.aliases, row)
    if alias is not None:
        return alias

    prior = _from_prior(knowledge.priors, row)
    if prior is not None:
        return prior

    rule = _first_matching(knowledge.seed_rules, row)
    if rule is not None:
        return _from_rule(rule, CONFIDENCE_SEED_RULE, "seed_rule")

    return UNCLASSIFIED
