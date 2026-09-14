"""Phase 1.7 Task 2: pure forecast math (spec "Forecast computation" rules 4-6). Database-free —
every case here is an edge in `forecast.py`'s two functions, not a route or a fixture."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from oneledger.forecast import occurrence_dates, project_balances


def test_monthly_clamps_the_31st_to_the_shorter_month_end() -> None:
    dates = occurrence_dates(
        "monthly", date(2026, 1, 31), None, date(2026, 1, 31), date(2026, 4, 30)
    )
    assert dates == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_monthly_clamps_to_february_29_in_a_leap_year() -> None:
    dates = occurrence_dates(
        "monthly", date(2027, 12, 31), None, date(2027, 12, 31), date(2028, 2, 29)
    )
    assert dates == [date(2028, 1, 31), date(2028, 2, 29)]


def test_annual_clamps_february_29_to_28_outside_a_leap_year() -> None:
    dates = occurrence_dates(
        "annual", date(2024, 2, 29), None, date(2025, 1, 1), date(2026, 12, 31)
    )
    assert dates == [date(2025, 2, 28), date(2026, 2, 28)]


def test_end_date_is_inclusive() -> None:
    dates = occurrence_dates(
        "monthly", date(2026, 1, 1), date(2026, 3, 1), date(2025, 12, 31), date(2026, 12, 31)
    )
    assert dates == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_once_occurs_a_single_time_inside_the_window() -> None:
    inside = occurrence_dates("once", date(2026, 2, 14), None, date(2026, 1, 1), date(2026, 12, 31))
    before_window = occurrence_dates(
        "once", date(2026, 2, 14), None, date(2026, 3, 1), date(2026, 12, 31)
    )
    after_window = occurrence_dates(
        "once", date(2026, 2, 14), None, date(2026, 1, 1), date(2026, 2, 13)
    )
    assert inside == [date(2026, 2, 14)]
    assert before_window == []
    assert after_window == []


def test_balance_date_is_exclusive_and_day_90_is_inclusive() -> None:
    as_of = date(2026, 1, 1)
    through = as_of + timedelta(days=90)
    on_balance_date = occurrence_dates("monthly", through, None, through, through)
    on_day_90 = occurrence_dates("once", through, None, as_of, through)
    assert on_balance_date == []
    assert on_day_90 == [through]


def test_project_balances_sums_exact_decimal_amounts_at_each_point() -> None:
    as_of = date(2026, 1, 1)
    occurrences = [
        (date(2026, 1, 15), Decimal("-100.5000")),
        (date(2026, 2, 1), Decimal("2000.0000")),
        (date(2026, 4, 2), Decimal("-1.2345")),  # one day past day 90, never counted
    ]
    points = project_balances(Decimal("1000.0000"), occurrences, as_of)
    assert points == [
        (30, date(2026, 1, 31), Decimal("899.5000")),
        (60, date(2026, 3, 2), Decimal("2899.5000")),
        (90, date(2026, 4, 1), Decimal("2899.5000")),
    ]
