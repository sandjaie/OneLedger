"""Phase 3.3 Task 2: pure baseline and coverage math (spec "Emergency coverage"). Database-free —
every case here is an edge in `emergency.py`'s functions, not a route."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from oneledger.emergency import (
    coverage,
    eligible_native,
    manual_baseline,
    reference_period,
    reference_period_baseline,
)

_ZERO = Decimal("0.0000")


# ---------------------------------------------------------------------------------------
# reference_period / reference_period_baseline


def test_reference_period_is_the_complete_months_before_as_of_month() -> None:
    start, end = reference_period(date(2026, 9, 16), 6)
    assert start == date(2026, 3, 1)
    assert end == date(2026, 8, 31)


def test_reference_period_crosses_a_year_boundary() -> None:
    start, end = reference_period(date(2026, 2, 10), 3)
    assert start == date(2025, 11, 1)
    assert end == date(2026, 1, 31)


def test_bucket_baseline_over_n_months_divides_evenly_at_four_places() -> None:
    baseline = reference_period_baseline(
        as_of=date(2026, 9, 16),
        reference_months=6,
        spending=Decimal("12000.0000"),
        uncategorised=Decimal("300.0000"),
    )
    assert baseline.status == "available"
    assert baseline.monthlyBase == Decimal("2000.0000")
    assert baseline.referenceStart == date(2026, 3, 1)
    assert baseline.referenceEnd == date(2026, 8, 31)
    assert baseline.uncategorisedBase == Decimal("300.0000")


def test_zero_spending_is_not_enough_information() -> None:
    baseline = reference_period_baseline(
        as_of=date(2026, 9, 16), reference_months=6, spending=_ZERO, uncategorised=_ZERO
    )
    assert baseline.status == "not_enough_information"
    assert baseline.monthlyBase is None


def test_negative_spending_sum_is_not_enough_information() -> None:
    # A household with net income booked in a spending bucket (a refund, say) can sum negative;
    # the spec calls "not positive" not_enough_information, never a negative monthly figure.
    baseline = reference_period_baseline(
        as_of=date(2026, 9, 16),
        reference_months=6,
        spending=Decimal("-100.0000"),
        uncategorised=_ZERO,
    )
    assert baseline.status == "not_enough_information"


# ---------------------------------------------------------------------------------------
# manual_baseline


def test_manual_baseline_uses_the_stated_amount() -> None:
    baseline = manual_baseline(Decimal("2500.0000"))
    assert baseline.status == "available"
    assert baseline.method == "manual"
    assert baseline.monthlyBase == Decimal("2500.0000")
    assert baseline.referenceStart is None


def test_manual_baseline_of_zero_is_not_enough_information() -> None:
    assert manual_baseline(_ZERO).status == "not_enough_information"


# ---------------------------------------------------------------------------------------
# eligible_native


def test_eligible_native_subtracts_goal_reservations_not_below_zero() -> None:
    assert eligible_native(Decimal("6000.0000"), Decimal("4000.0000")) == Decimal("2000.0000")
    assert eligible_native(Decimal("1000.0000"), Decimal("4000.0000")) == _ZERO


def test_eligible_native_is_none_without_a_value() -> None:
    assert eligible_native(None, _ZERO) is None


# ---------------------------------------------------------------------------------------
# coverage


def test_p08_six_thousand_eligible_over_two_thousand_baseline_is_three_months() -> None:
    baseline = reference_period_baseline(
        as_of=date(2026, 9, 16),
        reference_months=6,
        spending=Decimal("12000.0000"),
        uncategorised=_ZERO,
    )
    result = coverage(
        baseline=baseline,
        target_months=Decimal("6.0"),
        eligible_bases=[Decimal("6000.0000")],
        emergency_reserved_base=_ZERO,
    )
    assert result.status == "available"
    assert result.coverageMonths == Decimal("3.00")
    assert result.targetBase == Decimal("12000.0000")
    assert result.gapBase == Decimal("6000.0000")


def test_coverage_is_not_enough_information_when_baseline_is_unknown() -> None:
    baseline = manual_baseline(_ZERO)
    result = coverage(
        baseline=baseline,
        target_months=Decimal("6.0"),
        eligible_bases=[Decimal("1000.0000")],
        emergency_reserved_base=_ZERO,
    )
    assert result.status == "not_enough_information"
    assert result.coverageMonths is None


def test_coverage_is_unavailable_with_a_missing_rate() -> None:
    baseline = manual_baseline(Decimal("1000.0000"))
    result = coverage(
        baseline=baseline,
        target_months=Decimal("6.0"),
        eligible_bases=[Decimal("500.0000"), None],
        emergency_reserved_base=_ZERO,
    )
    assert result.status == "unavailable"
    assert result.eligibleBase is None


def test_gap_is_zero_once_target_is_met() -> None:
    baseline = manual_baseline(Decimal("1000.0000"))
    result = coverage(
        baseline=baseline,
        target_months=Decimal("3.0"),
        eligible_bases=[Decimal("5000.0000")],
        emergency_reserved_base=Decimal("4000.0000"),
    )
    assert result.status == "available"
    assert result.gapBase == _ZERO
    assert result.reservedMonths == Decimal("4.00")
