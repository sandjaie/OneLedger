"""A card statement's own facts, normalised once at staging.

`statement_date`, `payment_due_date`, `minimum_due` and `credit_limit` are captured as the
producer printed them and stored in canonical form — ISO dates, four-place decimals — so nothing
downstream has to re-read a producer's formatting. A capture that will not read is reported and
left absent: never zero, and never silently dropped (Invariant 7). Every figure is invented.
"""

from __future__ import annotations

from datetime import date

import pytest

from oneledger.ingest.normalize import CARD_FACT_KEYS, StatementPeriod, card_facts

DAY_FIRST = "dd/MM/yyyy"


def test_the_four_facts_are_the_ones_the_plan_names() -> None:
    assert CARD_FACT_KEYS == frozenset(
        {"statement_date", "payment_due_date", "minimum_due", "credit_limit"}
    )


def test_dates_read_in_the_layout_s_own_format_become_iso() -> None:
    facts, failures = card_facts(
        {"statement_date": "15/03/2026", "payment_due_date": "04/04/2026"}, DAY_FIRST
    )
    assert facts == {"statement_date": "2026-03-15", "payment_due_date": "2026-04-04"}
    assert failures == []


def test_money_becomes_a_four_place_decimal_string() -> None:
    facts, failures = card_facts({"minimum_due": "40.00", "credit_limit": "20,000.00"}, DAY_FIRST)
    assert facts == {"minimum_due": "40.0000", "credit_limit": "20000.0000"}
    assert failures == []


def test_a_captured_zero_is_a_fact_and_is_kept() -> None:
    """A minimum due of nothing is different from a minimum due that did not extract."""
    facts, failures = card_facts({"minimum_due": "0.00"}, DAY_FIRST)
    assert facts == {"minimum_due": "0.0000"}
    assert failures == []


@pytest.mark.parametrize(
    ("key", "captured"),
    [
        ("statement_date", "31/31/2026"),
        ("payment_due_date", "2026-04-04"),
        ("minimum_due", "forty"),
        ("credit_limit", "20.000.00.1"),
    ],
)
def test_a_capture_that_will_not_read_is_reported_and_absent(key: str, captured: str) -> None:
    facts, failures = card_facts({key: captured}, DAY_FIRST)
    assert key not in facts, "a fact that did not read is absent, never zero or a guess"
    assert [failure.key for failure in failures] == [key]


def test_a_failure_never_repeats_what_was_captured() -> None:
    """Spec §5.6: no digit run of seven or more from a document reaches any response, log or
    message. A reason that echoed the capture would carry whatever the pattern swallowed."""
    captured = "99/99/12345678"
    _, failures = card_facts({"payment_due_date": captured}, DAY_FIRST)
    assert failures and captured not in failures[0].reason
    assert "12345678" not in failures[0].reason


@pytest.mark.parametrize("key", ["minimum_due", "credit_limit"])
def test_a_negative_amount_due_or_limit_is_a_mis_capture(key: str) -> None:
    """Both are magnitudes a statement prints unsigned; a sign here means the pattern caught
    something else."""
    facts, failures = card_facts({key: "-40.00"}, DAY_FIRST)
    assert key not in facts
    assert [failure.key for failure in failures] == [key]


def test_an_absent_fact_is_not_a_failure() -> None:
    facts, failures = card_facts({}, DAY_FIRST)
    assert facts == {}
    assert failures == []


def test_keys_other_than_the_four_pass_through_untouched() -> None:
    """The period's bounds are read by `_period`, under its own rules; this does not re-read
    them, and a second reading would be a second answer."""
    facts, _ = card_facts(
        {"period_start": "01 Mar 2026", "period_end": "31 Mar 2026", "minimum_due": "40.00"},
        DAY_FIRST,
    )
    assert facts == {
        "period_start": "01 Mar 2026",
        "period_end": "31 Mar 2026",
        "minimum_due": "40.0000",
    }


def test_a_two_digit_year_is_placed_in_the_statement_s_own_century() -> None:
    period = StatementPeriod(start=date(2026, 3, 1), end=date(2026, 3, 31), is_derived=False)
    facts, failures = card_facts({"payment_due_date": "04/04/26"}, "dd/MM/yy", period)
    assert facts == {"payment_due_date": "2026-04-04"}
    assert failures == []
