"""Phase 2.1 Task 1: the history coverage grid's pure math (spec "Coverage computation").

`HISTORY_ANCHOR` is fixed at 1 January 2024 per ADR-0011 (the 36-month history horizon) and is
not a setting: nothing in this module or its caller can silently shorten history by configuring
a later anchor. `month_statuses` implements the spec's first-match status order over dates and
period intervals only — no database access, so its edge cases (a split month, a month outside an
account's life, the in-progress current month) are cheap to prove directly
(`tests/test_coverage.py`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

HISTORY_ANCHOR = date(2024, 1, 1)


def months_from_anchor(as_of: date) -> list[date]:
    """First-of-month dates from the anchor through the month containing `as_of`, inclusive."""
    months = []
    year, month = HISTORY_ANCHOR.year, HISTORY_ANCHOR.month
    while (year, month) <= (as_of.year, as_of.month):
        months.append(date(year, month, 1))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return months


def month_end(month: date) -> date:
    """The last day of `month`'s calendar month."""
    if month.month == 12:
        return date(month.year, 12, 31)
    return date(month.year, month.month + 1, 1) - timedelta(days=1)


def account_life(opened_on: date | None, closed_on: date | None, as_of: date) -> tuple[date, date]:
    """(life_start, life_end): the anchor when never opened, `as_of` while still open."""
    return (opened_on or HISTORY_ANCHOR, closed_on or as_of)


def previous_month(today: date) -> date:
    """The first day of the calendar month before `today`. Shared by the monthly review's own
    default month and `GET /actions`'s `previousMonthReview`, so both name the same month."""
    first_of_this_month = today.replace(day=1)
    return (first_of_this_month - timedelta(days=1)).replace(day=1)


def _covered_days(periods: Sequence[tuple[date, date]], start: date, end: date) -> int:
    """Count of distinct days in [start, end] covered by the union of `periods`.

    Day-by-day rather than interval arithmetic: a month is at most 31 days and this runs once
    per account row per request, so the simple version is the honest one.
    ponytail: enumerating days over a whole account life instead of one month would not scale
    the same way — callers must pass a single month's [start, end], not the account's full life.
    """
    covered: set[date] = set()
    for period_start, period_end in periods:
        lo = max(period_start, start)
        hi = min(period_end, end)
        d = lo
        while d <= hi:
            covered.add(d)
            d += timedelta(days=1)
    return len(covered)


def month_status(
    month: date,
    life_start: date,
    life_end: date,
    periods: Sequence[tuple[date, date]],
    acknowledged: bool,
    as_of: date,
) -> str:
    """One month's status, first-match order (spec "Month status")."""
    end = month_end(month)
    if end < life_start or month > life_end:
        return "outside_account_life"
    in_life_start = max(month, life_start)
    in_life_end = min(end, life_end)
    total = (in_life_end - in_life_start).days + 1
    covered = _covered_days(periods, in_life_start, in_life_end)
    if covered == total:
        return "reconciled"
    if acknowledged:
        return "acknowledged"
    if covered > 0:
        return "partial"
    if (month.year, month.month) == (as_of.year, as_of.month) and as_of < end:
        return "in_progress"
    return "missing"


def month_statuses(
    months: Sequence[date],
    life_start: date,
    life_end: date,
    periods: Sequence[tuple[date, date]],
    acknowledged: Mapping[date, str],
    as_of: date,
) -> list[str]:
    """The status for each of `months`, in order. `acknowledged` maps month -> reason."""
    return [
        month_status(month, life_start, life_end, periods, month in acknowledged, as_of)
        for month in months
    ]
