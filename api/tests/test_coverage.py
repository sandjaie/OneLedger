"""Unit tests for `coverage.month_statuses`'s first-match status order (spec "Month status").
No database: pure dates and intervals. Every date below is invented.
"""

from __future__ import annotations

from datetime import date

from oneledger.coverage import HISTORY_ANCHOR, month_statuses, months_from_anchor


def test_anchor_is_pinned_to_adr_0011() -> None:
    assert HISTORY_ANCHOR == date(2024, 1, 1)


def test_months_from_anchor_spans_to_the_month_of_as_of() -> None:
    months = months_from_anchor(date(2024, 3, 15))
    assert months == [date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1)]


def test_reconciled_month_fully_covered_by_one_period() -> None:
    months = [date(2024, 1, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [(date(2024, 1, 1), date(2024, 1, 31))],
        {},
        date(2024, 6, 1),
    )
    assert statuses == ["reconciled"]


def test_a_month_split_across_two_batches_is_reconciled() -> None:
    months = [date(2024, 1, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [(date(2024, 1, 1), date(2024, 1, 15)), (date(2024, 1, 16), date(2024, 1, 31))],
        {},
        date(2024, 6, 1),
    )
    assert statuses == ["reconciled"]


def test_partial_month_some_but_not_all_days_covered() -> None:
    months = [date(2024, 1, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [(date(2024, 1, 1), date(2024, 1, 10))],
        {},
        date(2024, 6, 1),
    )
    assert statuses == ["partial"]


def test_missing_month_with_no_evidence() -> None:
    months = [date(2024, 2, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [],
        {},
        date(2024, 6, 1),
    )
    assert statuses == ["missing"]


def test_month_before_life_start_is_outside_account_life() -> None:
    months = [date(2024, 1, 1)]
    statuses = month_statuses(
        months,
        date(2024, 3, 1),
        date(2024, 12, 31),
        [],
        {},
        date(2024, 6, 1),
    )
    assert statuses == ["outside_account_life"]


def test_month_after_life_end_is_outside_account_life() -> None:
    months = [date(2024, 6, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 3, 31),
        [],
        {},
        date(2024, 6, 1),
    )
    assert statuses == ["outside_account_life"]


def test_acknowledged_month_with_no_coverage() -> None:
    months = [date(2024, 2, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [],
        {date(2024, 2, 1): "unobtainable"},
        date(2024, 6, 1),
    )
    assert statuses == ["acknowledged"]


def test_a_later_import_outranks_an_acknowledgement() -> None:
    """Reconciled is checked before acknowledged (spec order): once an import fully covers a
    month, a stale acknowledgement no longer wins."""
    months = [date(2024, 2, 1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [(date(2024, 2, 1), date(2024, 2, 29))],
        {date(2024, 2, 1): "unobtainable"},
        date(2024, 6, 1),
    )
    assert statuses == ["reconciled"]


def test_current_month_with_no_coverage_is_in_progress_not_missing() -> None:
    as_of = date(2024, 6, 15)
    months = [as_of.replace(day=1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [],
        {},
        as_of,
    )
    assert statuses == ["in_progress"]


def test_current_month_on_its_last_day_with_no_coverage_is_missing() -> None:
    """`in_progress` requires `as_of` to be before the month's last day; on the last day itself
    the month has fully elapsed and a gap is a gap."""
    as_of = date(2024, 6, 30)
    months = [as_of.replace(day=1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [],
        {},
        as_of,
    )
    assert statuses == ["missing"]


def test_current_month_partially_covered_is_partial_not_in_progress() -> None:
    as_of = date(2024, 6, 15)
    months = [as_of.replace(day=1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        date(2024, 12, 31),
        [(date(2024, 6, 1), date(2024, 6, 10))],
        {},
        as_of,
    )
    assert statuses == ["partial"]


def test_life_end_caps_reconciled_at_as_of_for_an_open_account() -> None:
    """An open account's life ends at `as_of` (never null), so a current month covered only up
    to today is reconciled: there is no future evidence to expect yet."""
    as_of = date(2024, 6, 15)
    months = [as_of.replace(day=1)]
    statuses = month_statuses(
        months,
        date(2024, 1, 1),
        as_of,
        [(date(2024, 6, 1), date(2024, 6, 15))],
        {},
        as_of,
    )
    assert statuses == ["reconciled"]
