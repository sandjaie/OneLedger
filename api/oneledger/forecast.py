"""Phase 1.7 Task 2 and Phase 3.2 Task 1: the pure forecast math (spec "Forecast computation").
Every function here is database-free so their edge cases — month-end clamping, inclusive end
dates, the exclusive balance-date/inclusive day-90 window, lowest-balance ties, currency folding
— are unit-tested directly; `routes/plans.py` supplies the account and commitment facts and does
no arithmetic of its own beyond calling these.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

Cadence = Literal["once", "monthly", "annual"]

_POINTS = (30, 60, 90)


def _add_months(base: date, months: int) -> date:
    """`base` shifted by `months`, its day clamped to the target month's last day — the same
    clamp rule for a monthly or annual step, since an annual step is just twelve of these."""
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def occurrence_dates(
    cadence: Cadence, first: date, end: date | None, after: date, through: date
) -> list[date]:
    """Rule 4's occurrence sequence (`first` plus `n` or `12n` months, day-clamped; `once` is
    `first` alone), stopped after `end` inclusive, narrowed to rule 5's window: strictly after
    `after` (the account's balance date — a posting dated on it is already in the balance) and
    at or before `through` (`asOf + 90 days`).

    `first` may be years before `after`; jumping to an estimated starting index rather than
    counting from `n = 0` keeps this independent of how old the commitment is.
    """
    if cadence == "once":
        return [first] if after < first <= through and (end is None or first <= end) else []

    step = 1 if cadence == "monthly" else 12
    months_between = (after.year - first.year) * 12 + (after.month - first.month)
    n = max(0, months_between // step)
    while n > 0 and _add_months(first, n * step) > after:
        n -= 1
    while _add_months(first, n * step) <= after:
        n += 1

    dates: list[date] = []
    while True:
        candidate = _add_months(first, n * step)
        if candidate > through or (end is not None and candidate > end):
            break
        dates.append(candidate)
        n += 1
    return dates


def is_occurrence(cadence: Cadence, first: date, end: date | None, candidate: date) -> bool:
    """Phase 3.1 spec "Occurrence rules": `candidate` is `first` plus `n` (monthly) or `12n`
    (annual) clamped months for some `n >= 0`, not after `end`; `once` only ever occurs on
    `first` itself. Used to validate an owner-given `nextDueDate` against its commitment's
    schedule (`routes/plans.py`)."""
    if end is not None and candidate > end:
        return False
    if cadence == "once":
        return candidate == first
    if candidate < first:
        return False
    step = 1 if cadence == "monthly" else 12
    months_between = (candidate.year - first.year) * 12 + (candidate.month - first.month)
    if months_between % step != 0:
        return False
    return _add_months(first, months_between) == candidate


def next_occurrence(cadence: Cadence, first: date, end: date | None, due: date) -> date | None:
    """The scheduled occurrence strictly after `due` — `None` for `once` (it has no successor)
    or once the schedule has passed `end`. Clamping always restarts from `first`, so a run of
    month-end clamped dates (31 January, 28/29 February, 31 March, ...) never drifts. Used both
    to close a resolved occurrence onto the next one and, doubled with the default-next-due rule
    in `routes/plans.py`, to pick a new commitment's first open occurrence."""
    if cadence == "once":
        return None
    step = 1 if cadence == "monthly" else 12
    months_between = (due.year - first.year) * 12 + (due.month - first.month)
    n = max(0, months_between // step)
    while n > 0 and _add_months(first, n * step) > due:
        n -= 1
    while _add_months(first, n * step) <= due:
        n += 1
    candidate = _add_months(first, n * step)
    if end is not None and candidate > end:
        return None
    return candidate


def project_balances(
    start: Decimal, occurrences: list[tuple[date, Decimal]], as_of: date
) -> list[tuple[int, date, Decimal]]:
    """Rule 6's three points: `asOf + days` for `days` in 30, 60, 90, each balance the start plus
    every occurrence dated on or before that point — exact `Decimal` addition, already at four
    places because every occurrence and the start balance are."""
    results: list[tuple[int, date, Decimal]] = []
    for days in _POINTS:
        point = as_of + timedelta(days=days)
        total = start + sum(
            (amount for occurred, amount in occurrences if occurred <= point), Decimal(0)
        )
        results.append((days, point, total))
    return results


def lowest_balance(
    start: Decimal, start_date: date, occurrences: list[tuple[date, Decimal]]
) -> tuple[Decimal, date]:
    """Phase 3.2 spec rule 4/7: the minimum running balance from `start` at `start_date` through
    every later occurrence date (same-date occurrences summed together before comparing, so a
    date is one step, not one per occurrence), earliest date on a tie. `start` is compared first
    and only a strictly lower balance replaces it, so a later occurrence that merely ties the
    lowest never displaces its earlier date — exactly rule 4's "earliest date on ties, start
    balance included"."""
    by_date: dict[date, Decimal] = {}
    for occurred, amount in occurrences:
        by_date[occurred] = by_date.get(occurred, Decimal(0)) + amount
    running = start
    lowest, lowest_date = start, start_date
    for occurred in sorted(by_date):
        running += by_date[occurred]
        if running < lowest:
            lowest, lowest_date = running, occurred
    return lowest, lowest_date


def aggregate_currency(
    start: Decimal, occurrences: list[tuple[date, Decimal]], as_of: date
) -> tuple[Decimal, list[tuple[date, Decimal]]]:
    """Phase 3.2 spec rule 7: occurrences dated on or before `as_of` are folded into `start`
    (now dated `as_of`, summing every member's start already gave `start` its currency-wide
    value); the rest are returned unchanged for `lowest_balance` and `project_balances`, both of
    which already walk forward from a dated start."""
    folded = start
    rest: list[tuple[date, Decimal]] = []
    for occurred, amount in occurrences:
        if occurred <= as_of:
            folded += amount
        else:
            rest.append((occurred, amount))
    return folded, rest


def select_funding_accounts(
    amount: Decimal, candidates: list[tuple[Any, str, Decimal]]
) -> list[tuple[Any, str, Decimal]]:
    """Phase 3.2 spec rule 5: the other candidate accounts (`(id, name, own lowest balance)`,
    already in account order) whose own lowest balance could cover `amount` on its own —
    informational only; nothing here proposes a transfer or posts anything."""
    return [candidate for candidate in candidates if candidate[2] >= amount]
