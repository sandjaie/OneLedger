"""Validation on `RuleBody`, on values, with no database.

What the model refuses is what keeps `classification_rule`'s unconstrained columns honest, and
what turns V19's two `CHECK`s into a sentence naming the half that is missing rather than a
constraint name. Every id here is invented.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from oneledger.routes.rules import RuleBody


def body(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "matchDescriptionPattern": "GROCER",
        "setCategoryId": str(uuid4()),
    }
    values.update(changes)
    return values


def test_a_rule_needs_something_to_match_on() -> None:
    with pytest.raises(ValidationError) as refused:
        RuleBody.model_validate({"setCategoryId": str(uuid4())})
    assert "something to match on" in str(refused.value)


def test_a_rule_needs_something_to_decide() -> None:
    with pytest.raises(ValidationError) as refused:
        RuleBody.model_validate({"matchDescriptionPattern": "GROCER"})
    assert "something to decide" in str(refused.value)


def test_marking_a_row_a_transfer_candidate_is_a_decision() -> None:
    rule = RuleBody.model_validate(
        {"matchDescriptionPattern": "CARD PAYMENT", "setIsTransferCandidate": True}
    )
    assert rule.setIsTransferCandidate is True
    assert rule.setCategoryId is None


@pytest.mark.parametrize(
    "matcher",
    ["matchDescriptionPattern", "matchAccountId", "matchAmountExact", "matchReferencePattern"],
)
def test_each_counted_matcher_satisfies_the_requirement_alone(matcher: str) -> None:
    value: object = {
        "matchDescriptionPattern": "X",
        "matchAccountId": str(uuid4()),
        "matchAmountExact": "10.00",
        "matchReferencePattern": "REF",
    }[matcher]
    RuleBody.model_validate({matcher: value, "setTxnType": "expense"})


def test_a_currency_matcher_alone_is_not_something_to_match_on() -> None:
    """`has_matcher` counts four columns and a currency is not one of them: a rule matching
    every row in a currency is a rule that matches almost everything."""
    with pytest.raises(ValidationError) as refused:
        RuleBody.model_validate({"matchCurrency": "XAE", "setTxnType": "expense"})
    assert "something to match on" in str(refused.value)


def test_money_matchers_stay_exact_decimal_strings() -> None:
    rule = RuleBody.model_validate(body(matchAmountMin="10.5", matchAmountMax="1000.0000"))
    assert (rule.matchAmountMin, rule.matchAmountMax) == ("10.5", "1000.0000")


def test_a_money_matcher_that_is_not_a_decimal_is_refused() -> None:
    for value in ("ten", "", "1.0.0", "NaN", "Infinity"):
        with pytest.raises(ValidationError):
            RuleBody.model_validate(body(matchAmountExact=value))


def test_bounds_the_wrong_way_round_are_refused() -> None:
    with pytest.raises(ValidationError) as refused:
        RuleBody.model_validate(body(matchAmountMin="100.00", matchAmountMax="10.00"))
    assert "matchAmountMin must not exceed matchAmountMax" in str(refused.value)


def test_equal_bounds_are_allowed() -> None:
    RuleBody.model_validate(body(matchAmountMin="10.00", matchAmountMax="10.00"))


def test_an_uncompilable_regex_is_refused_at_creation() -> None:
    with pytest.raises(ValidationError) as refused:
        RuleBody.model_validate(body(matchPatternKind="regex", matchDescriptionPattern="([a-z"))
    assert "not a valid regular expression" in str(refused.value)


def test_a_catastrophically_backtracking_regex_is_refused_at_creation() -> None:
    """At match time this is a request that never returns, on a route holding a connection."""
    for pattern in [r"(a+)+$", r"(?:x*)*y", r"(\d+\s*)+!"]:
        with pytest.raises(ValidationError) as refused:
            RuleBody.model_validate(body(matchPatternKind="regex", matchDescriptionPattern=pattern))
        assert "nests one quantifier inside another" in str(refused.value)


def test_an_ordinary_regex_is_accepted() -> None:
    for pattern in [r"^SALARY", r"ATM\s+WITHDRAWAL", r"REF:\d{6}", r"(GROCER|BAKERY)"]:
        RuleBody.model_validate(body(matchPatternKind="regex", matchDescriptionPattern=pattern))


def test_a_pattern_is_only_compiled_when_the_kind_says_regex() -> None:
    """`(a+)+$` is a literal under `contains`, and refusing it there would refuse a narration
    that legitimately contains brackets."""
    RuleBody.model_validate(body(matchPatternKind="contains", matchDescriptionPattern="(a+)+$"))


def test_priority_stays_inside_the_smallint_the_column_holds() -> None:
    RuleBody.model_validate(body(priority=0))
    RuleBody.model_validate(body(priority=32767))
    for out_of_range in (-1, 32768):
        with pytest.raises(ValidationError):
            RuleBody.model_validate(body(priority=out_of_range))


def test_an_unknown_direction_or_pattern_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        RuleBody.model_validate(body(matchDirection="sideways"))
    with pytest.raises(ValidationError):
        RuleBody.model_validate(body(matchPatternKind="fuzzy"))


def test_origin_is_not_settable_through_the_body() -> None:
    """A rule that could rewrite its own provenance could claim to be a seed rule."""
    rule = RuleBody.model_validate(body(origin="seed"))
    assert not hasattr(rule, "origin")


def test_the_defaults_are_the_ones_the_cascade_expects() -> None:
    rule = RuleBody.model_validate(body())
    assert (rule.priority, rule.matchDirection, rule.matchPatternKind) == (100, "any", "contains")
    assert rule.isActive is True
    assert rule.setIsTransferCandidate is False
    assert rule.setTags == []
