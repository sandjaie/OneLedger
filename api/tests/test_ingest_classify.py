"""The classification cascade, proved on values with no database and no HTTP.

`classification.html` §1 fixes the order and the confidence of every step. What these tests
pin is that each step wins over every step *below* it and then stops — because the cascade's
whole value is that a row can name the step that decided it, and a step that silently loses to
a later one makes that name a lie.

Every id here is invented and no figure is a real balance.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from oneledger.ingest.classify import (
    CONFIDENCE_MERCHANT_ALIAS,
    CONFIDENCE_PRIOR_EXACT,
    CONFIDENCE_PRIOR_FUZZY,
    CONFIDENCE_SEED_RULE,
    CONFIDENCE_UNCLASSIFIED,
    CONFIDENCE_USER_MANUAL,
    CONFIDENCE_USER_RULE,
    PRE_TICK_THRESHOLD,
    Alias,
    Candidate,
    Knowledge,
    Prior,
    Rule,
    classify_row,
    matches_pattern,
    rule_matches,
)

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_ACCOUNT = UUID("22222222-2222-2222-2222-222222222222")
GROCERIES = UUID("33333333-3333-3333-3333-333333333333")
SALARY = UUID("44444444-4444-4444-4444-444444444444")
MERCHANT = UUID("55555555-5555-5555-5555-555555555555")


def row(**changes: object) -> Candidate:
    values: dict[str, object] = {
        "account_id": ACCOUNT,
        "description_clean": "GROCER MARKET",
        "amount": Decimal("-120.00"),
        "currency": "XAE",
    }
    values.update(changes)
    return Candidate(**values)  # type: ignore[arg-type]


def rule(**changes: object) -> Rule:
    values: dict[str, object] = {
        "id": uuid4(),
        "priority": 100,
        "origin": "user_created",
        "match_description_pattern": "GROCER",
        "set_category_id": GROCERIES,
    }
    values.update(changes)
    return Rule(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# The order. Each step beats every step below it, and stops.
# ---------------------------------------------------------------------------------------


def test_an_override_beats_every_rule_and_stops() -> None:
    knowledge = Knowledge(
        user_rules=[rule()],
        aliases=[Alias(merchant_id=MERCHANT, pattern="GROCER")],
        priors=[Prior(account_id=ACCOUNT, description_clean="GROCER MARKET", category_id=SALARY)],
        seed_rules=[rule(priority=500, origin="seed")],
    )

    decided = classify_row(row(override_category_id=SALARY), knowledge)

    assert decided.classified_by == "user_manual"
    assert decided.classification_confidence == CONFIDENCE_USER_MANUAL
    assert decided.category_id == SALARY
    # The override decided it, so no rule may be credited with it.
    assert decided.classification_rule_id is None


def test_a_user_rule_beats_an_alias_a_prior_and_a_seed_rule() -> None:
    user = rule()
    knowledge = Knowledge(
        user_rules=[user],
        aliases=[Alias(merchant_id=MERCHANT, pattern="GROCER", category_id=SALARY)],
        priors=[Prior(account_id=ACCOUNT, description_clean="GROCER MARKET", category_id=SALARY)],
        seed_rules=[rule(priority=500, origin="seed", set_category_id=SALARY)],
    )

    decided = classify_row(row(), knowledge)

    assert (decided.classified_by, decided.classification_rule_id) == ("user_rule", user.id)
    assert decided.classification_confidence == CONFIDENCE_USER_RULE
    assert decided.category_id == GROCERIES


def test_an_alias_beats_a_prior_and_a_seed_rule() -> None:
    knowledge = Knowledge(
        aliases=[Alias(merchant_id=MERCHANT, pattern="GROCER", category_id=GROCERIES)],
        priors=[Prior(account_id=ACCOUNT, description_clean="GROCER MARKET", category_id=SALARY)],
        seed_rules=[rule(priority=500, origin="seed", set_category_id=SALARY)],
    )

    decided = classify_row(row(), knowledge)

    assert decided.classified_by == "merchant_alias"
    assert decided.classification_confidence == CONFIDENCE_MERCHANT_ALIAS
    assert (decided.merchant_id, decided.category_id) == (MERCHANT, GROCERIES)


def test_a_prior_beats_a_seed_rule() -> None:
    knowledge = Knowledge(
        priors=[
            Prior(account_id=ACCOUNT, description_clean="GROCER MARKET", category_id=GROCERIES)
        ],
        seed_rules=[rule(priority=500, origin="seed", set_category_id=SALARY)],
    )

    decided = classify_row(row(), knowledge)

    assert decided.classified_by == "prior_match"
    assert decided.classification_confidence == CONFIDENCE_PRIOR_EXACT
    assert decided.category_id == GROCERIES


def test_a_seed_rule_decides_when_nothing_above_it_does() -> None:
    seed = rule(priority=500, origin="seed")

    decided = classify_row(row(), Knowledge(seed_rules=[seed]))

    assert (decided.classified_by, decided.classification_rule_id) == ("seed_rule", seed.id)
    assert decided.classification_confidence == CONFIDENCE_SEED_RULE


def test_a_row_matching_nothing_is_unclassified_and_names_no_rule() -> None:
    decided = classify_row(row(), Knowledge())

    assert decided.classified_by == "unclassified"
    assert decided.classification_confidence == CONFIDENCE_UNCLASSIFIED
    assert decided.classification_rule_id is None
    assert decided.category_id is None


def test_the_recurring_series_step_is_absent_rather_than_stubbed() -> None:
    """Phase 2's step must not exist yet: one that always declines is indistinguishable from
    one that is broken, and the confidence table would carry a row no code can produce."""
    from oneledger.ingest import classify as module

    produced = {
        classify_row(row(**case), Knowledge(**knowledge)).classified_by
        for case, knowledge in (
            ({"override_category_id": SALARY}, {}),
            ({}, {"user_rules": [rule()]}),
            ({}, {"aliases": [Alias(merchant_id=MERCHANT, pattern="GROCER")]}),
            ({}, {"priors": [Prior(account_id=ACCOUNT, description_clean="GROCER MARKET")]}),
            ({}, {"seed_rules": [rule(origin="seed")]}),
            ({}, {}),
        )
    }
    assert "recurring_series" not in produced
    assert not hasattr(module, "CONFIDENCE_RECURRING_SERIES")


# ---------------------------------------------------------------------------------------
# Priority, and why a user rule beats a seed rule without either knowing the other exists.
# ---------------------------------------------------------------------------------------


def test_the_lower_priority_wins_among_user_rules() -> None:
    urgent = rule(priority=10, set_category_id=SALARY)
    ordinary = rule(priority=100, set_category_id=GROCERIES)

    decided = classify_row(row(), Knowledge(user_rules=[ordinary, urgent]))

    assert decided.classification_rule_id == urgent.id
    assert decided.category_id == SALARY


def test_a_user_rule_beats_a_seed_rule_by_the_step_not_the_number() -> None:
    """Both default priorities exist so the *numbers* also agree, but the cascade decides this
    before priority is consulted: seed rules are a later step and are never compared with user
    rules at all. A seed rule at priority 1 still loses."""
    user = rule(priority=100)
    seed = rule(priority=1, origin="seed", set_category_id=SALARY)

    decided = classify_row(row(), Knowledge(user_rules=[user], seed_rules=[seed]))

    assert (decided.classified_by, decided.classification_rule_id) == ("user_rule", user.id)


def test_two_rules_at_one_priority_resolve_the_same_way_every_run() -> None:
    """A household's own ambiguity, resolved deterministically. Letting the database's row
    order decide would classify the same ledger differently on two runs."""
    first = Rule(
        id=UUID(int=1),
        priority=50,
        origin="user_created",
        match_description_pattern="GROCER",
        set_category_id=GROCERIES,
    )
    second = Rule(
        id=UUID(int=2),
        priority=50,
        origin="user_created",
        match_description_pattern="GROCER",
        set_category_id=SALARY,
    )

    forwards = classify_row(row(), Knowledge(user_rules=[first, second]))
    backwards = classify_row(row(), Knowledge(user_rules=[second, first]))

    assert forwards.classification_rule_id == backwards.classification_rule_id == first.id


# ---------------------------------------------------------------------------------------
# Matchers. Every non-null condition must match; an absent one is ignored.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "matches"),
    [
        ({}, True),
        ({"match_account_id": OTHER_ACCOUNT}, False),
        ({"match_account_id": ACCOUNT}, True),
        ({"match_currency": "XIN"}, False),
        ({"match_currency": "XAE"}, True),
        ({"match_country": "XB"}, False),
        ({"match_amount_exact": Decimal("120.00")}, True),
        ({"match_amount_exact": Decimal("999.00")}, False),
        ({"match_amount_min": Decimal("100.00")}, True),
        ({"match_amount_min": Decimal("200.00")}, False),
        ({"match_amount_max": Decimal("200.00")}, True),
        ({"match_amount_max": Decimal("100.00")}, False),
        ({"match_direction": "debit"}, True),
        ({"match_direction": "credit"}, False),
        ({"match_reference_pattern": "REF"}, False),
    ],
)
def test_each_matcher_excludes_only_the_row_that_fails_it(
    changes: dict[str, object], matches: bool
) -> None:
    assert rule_matches(rule(**changes), row()) is matches


def test_a_null_matcher_matches_any_value_in_that_field() -> None:
    """A rule with only a description pattern matches on description alone (§2)."""
    only_description = Rule(
        id=uuid4(),
        priority=100,
        origin="user_created",
        match_description_pattern="GROCER",
        set_category_id=GROCERIES,
    )

    assert rule_matches(only_description, row(currency="XIN", country="XZ"))
    assert rule_matches(only_description, row(amount=Decimal("9999.00")))
    assert rule_matches(only_description, row(account_id=OTHER_ACCOUNT))


def test_the_bounds_read_the_magnitude_and_the_direction_reads_the_sign() -> None:
    """ "Over 500" means 500 out or 500 in. The sign is the direction matcher's business, and
    only its business, or a bound would have to be written twice for a two-sided ledger."""
    over = rule(match_amount_min=Decimal("500.00"), match_description_pattern="X")

    assert rule_matches(over, row(description_clean="X", amount=Decimal("-600.00")))
    assert rule_matches(over, row(description_clean="X", amount=Decimal("600.00")))
    assert not rule_matches(over, row(description_clean="X", amount=Decimal("-400.00")))

    debits = rule(match_direction="debit", match_description_pattern="X")
    assert rule_matches(debits, row(description_clean="X", amount=Decimal("-1.00")))
    assert not rule_matches(debits, row(description_clean="X", amount=Decimal("1.00")))


@pytest.mark.parametrize(
    ("kind", "pattern", "text", "matches"),
    [
        ("exact", "GROCER MARKET", "GROCER MARKET", True),
        ("exact", "GROCER", "GROCER MARKET", False),
        ("prefix", "GROCER", "GROCER MARKET", True),
        ("prefix", "MARKET", "GROCER MARKET", False),
        ("contains", "CER MAR", "GROCER MARKET", True),
        ("contains", "BAKERY", "GROCER MARKET", False),
        ("regex", r"^GROCER\s+\w+$", "GROCER MARKET", True),
        ("regex", r"^MARKET", "GROCER MARKET", False),
        # A rule typed in lower case still matches upper-case narration, which is what three of
        # the four producers this repository has met actually print.
        ("contains", "grocer", "GROCER MARKET", True),
        ("exact", "grocer market", "GROCER MARKET", True),
    ],
)
def test_every_pattern_kind_matches_what_it_says_and_ignores_case(
    kind: str, pattern: str, text: str, matches: bool
) -> None:
    assert matches_pattern(pattern, kind, text) is matches  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# Prior match, and the abstention that keeps `unclassified` meaningful.
# ---------------------------------------------------------------------------------------


def test_a_prior_in_another_account_is_not_evidence() -> None:
    elsewhere = Prior(
        account_id=OTHER_ACCOUNT, description_clean="GROCER MARKET", category_id=GROCERIES
    )

    assert classify_row(row(), Knowledge(priors=[elsewhere])).classified_by == "unclassified"


def test_a_near_identical_prior_is_weaker_evidence_and_says_so() -> None:
    near = Prior(account_id=ACCOUNT, description_clean="GROCER MARKETT", category_id=GROCERIES)

    decided = classify_row(row(), Knowledge(priors=[near]))

    assert decided.classified_by == "prior_match"
    assert decided.classification_confidence == CONFIDENCE_PRIOR_FUZZY
    assert decided.category_id == GROCERIES
    # Below the pre-tick threshold, so the row surfaces rather than arriving ticked.
    assert decided.is_pre_ticked is False


def test_a_prior_too_far_off_is_not_evidence_at_all() -> None:
    unrelated = Prior(
        account_id=ACCOUNT, description_clean="AIRLINE TICKET REFUND", category_id=GROCERIES
    )

    assert classify_row(row(), Knowledge(priors=[unrelated])).classified_by == "unclassified"


def test_the_engine_abstains_rather_than_guessing() -> None:
    """No path returns a category below 0.700. Anything weaker is a guess wearing a number."""
    cases = [
        Knowledge(),
        Knowledge(priors=[Prior(account_id=ACCOUNT, description_clean="NOTHING ALIKE AT ALL")]),
        Knowledge(user_rules=[rule(match_description_pattern="NOPE")]),
    ]
    for knowledge in cases:
        decided = classify_row(row(), knowledge)
        assert decided.category_id is None
        assert decided.classification_confidence == CONFIDENCE_UNCLASSIFIED


def test_every_confidence_is_the_literal_the_table_states() -> None:
    """Pinned to the literals, not to the constants.

    Every other test compares a result against the constant it came from, which would agree
    with itself whatever the constant said. `classification.html` §1's table is the contract,
    and this is where it is read.
    """
    assert CONFIDENCE_USER_MANUAL == Decimal("1.000")
    assert CONFIDENCE_USER_RULE == Decimal("0.980")
    assert CONFIDENCE_MERCHANT_ALIAS == Decimal("0.950")
    assert CONFIDENCE_PRIOR_EXACT == Decimal("0.900")
    assert CONFIDENCE_SEED_RULE == Decimal("0.800")
    assert CONFIDENCE_PRIOR_FUZZY == Decimal("0.700")
    assert CONFIDENCE_UNCLASSIFIED == Decimal("0.000")
    # And the ordering the cascade depends on, so a future edit cannot make a later step
    # outrank an earlier one without this failing.
    assert (
        CONFIDENCE_UNCLASSIFIED
        < CONFIDENCE_PRIOR_FUZZY
        < CONFIDENCE_SEED_RULE
        < CONFIDENCE_PRIOR_EXACT
        < CONFIDENCE_MERCHANT_ALIAS
        < CONFIDENCE_USER_RULE
        < CONFIDENCE_USER_MANUAL
    )


def test_the_pre_tick_threshold_bands_exactly_where_the_table_says() -> None:
    assert PRE_TICK_THRESHOLD == Decimal("0.800")
    banded = {
        CONFIDENCE_USER_MANUAL: True,
        CONFIDENCE_USER_RULE: True,
        CONFIDENCE_MERCHANT_ALIAS: True,
        CONFIDENCE_PRIOR_EXACT: True,
        CONFIDENCE_SEED_RULE: True,
        CONFIDENCE_PRIOR_FUZZY: False,
        CONFIDENCE_UNCLASSIFIED: False,
    }
    for confidence, ticked in banded.items():
        assert (confidence >= PRE_TICK_THRESHOLD) is ticked


# ---------------------------------------------------------------------------------------
# The transfer hint 3b2 consumes.
# ---------------------------------------------------------------------------------------


def test_a_rule_can_mark_a_row_a_transfer_candidate_without_categorising_it() -> None:
    transfer = Rule(
        id=uuid4(),
        priority=100,
        origin="user_created",
        match_description_pattern="CARD PAYMENT",
        set_is_transfer_candidate=True,
        set_counterparty_account_id=OTHER_ACCOUNT,
    )

    decided = classify_row(
        row(description_clean="CARD PAYMENT THANK YOU"), Knowledge(user_rules=[transfer])
    )

    assert decided.is_transfer_candidate is True
    assert decided.counterparty_account_id == OTHER_ACCOUNT
    assert decided.category_id is None


def test_money_stays_decimal_through_every_comparison() -> None:
    """No float anywhere: a bound compared as a float is a bound that is wrong at the edge."""
    exact = rule(match_amount_exact=Decimal("120.00"), match_description_pattern="X")

    assert rule_matches(exact, row(description_clean="X", amount=Decimal("-120.0000")))
    assert not rule_matches(exact, row(description_clean="X", amount=Decimal("-120.0001")))
