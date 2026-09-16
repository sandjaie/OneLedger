"""Phase 3.3 Task 2: pure Decimal math for the emergency reserve baseline and coverage (spec
"Emergency coverage"). No database access — every spending sum, native value and rate arrives
already read by `routes/emergency_reserve.py`, so this module is exhaustively unit-testable
without a connection, the same convention as `goals.py`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from .coverage import month_end, previous_month

_BASE = Decimal("0.0001")
_MONTHS = Decimal("0.01")
_ZERO = Decimal("0.0000")

BaselineStatus = str  # "available" | "not_enough_information"
CoverageStatus = str  # "available" | "not_enough_information" | "unavailable"


def _shift_months(month_start: date, delta: int) -> date:
    total = month_start.year * 12 + (month_start.month - 1) + delta
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def reference_period(as_of: date, reference_months: int) -> tuple[date, date]:
    """The `reference_months` complete calendar months before `asOf`'s month (spec "Baseline"),
    as an inclusive [start, end] date range."""
    this_month = as_of.replace(day=1)
    start = _shift_months(this_month, -reference_months)
    end = month_end(previous_month(as_of))
    return start, end


@dataclass(frozen=True)
class Baseline:
    method: str  # "reference_period" | "manual"
    monthlyBase: Decimal | None
    referenceStart: date | None
    referenceEnd: date | None
    uncategorisedBase: Decimal | None
    status: BaselineStatus


def reference_period_baseline(
    *, as_of: date, reference_months: int, spending: Decimal, uncategorised: Decimal
) -> Baseline:
    """`reference_months`' worth of bucketed spending, divided evenly (spec "Baseline"). A
    baseline that is not positive reads `not_enough_information`, never a zero or negative
    monthly figure the caller could mistake for a real one."""
    start, end = reference_period(as_of, reference_months)
    monthly = (spending / reference_months).quantize(_BASE, rounding=ROUND_HALF_EVEN)
    status: BaselineStatus = "available" if monthly > 0 else "not_enough_information"
    return Baseline(
        method="reference_period",
        monthlyBase=monthly if status == "available" else None,
        referenceStart=start,
        referenceEnd=end,
        uncategorisedBase=uncategorised,
        status=status,
    )


def manual_baseline(manual_monthly_base: Decimal) -> Baseline:
    status: BaselineStatus = "available" if manual_monthly_base > 0 else "not_enough_information"
    return Baseline(
        method="manual",
        monthlyBase=manual_monthly_base if status == "available" else None,
        referenceStart=None,
        referenceEnd=None,
        uncategorisedBase=None,
        status=status,
    )


def eligible_native(value: Decimal | None, goal_reserved: Decimal) -> Decimal | None:
    """One account's eligible native amount (spec "Resources"): its value less what a goal has
    already reserved on it, never below zero. `None` value (no current value) is not eligible."""
    return None if value is None else max(value - goal_reserved, _ZERO)


@dataclass(frozen=True)
class Coverage:
    status: CoverageStatus
    reasons: list[str]
    eligibleBase: Decimal | None
    coverageMonths: Decimal | None
    reservedMonths: Decimal | None
    targetBase: Decimal | None
    gapBase: Decimal | None


def coverage(
    *,
    baseline: Baseline,
    target_months: Decimal,
    eligible_bases: list[Decimal | None],
    emergency_reserved_base: Decimal,
) -> Coverage:
    """Coverage months, target and gap (spec "Coverage"). A baseline that is not
    `available` makes coverage `not_enough_information`; a missing rate on any eligible account's
    translation makes it `unavailable` rather than a total that silently drops that account."""
    if baseline.status != "available" or baseline.monthlyBase is None:
        return Coverage(
            "not_enough_information", ["baseline_not_enough_information"], *([None] * 5)
        )
    if any(value is None for value in eligible_bases):
        return Coverage("unavailable", ["missing_rate"], None, None, None, None, None)
    eligible_base = sum((value for value in eligible_bases if value is not None), _ZERO)
    coverage_months = (eligible_base / baseline.monthlyBase).quantize(
        _MONTHS, rounding=ROUND_HALF_EVEN
    )
    reserved_months = (emergency_reserved_base / baseline.monthlyBase).quantize(
        _MONTHS, rounding=ROUND_HALF_EVEN
    )
    target_base = (baseline.monthlyBase * target_months).quantize(_BASE, rounding=ROUND_HALF_EVEN)
    gap_base = max(target_base - eligible_base, _ZERO)
    return Coverage(
        status="available",
        reasons=[],
        eligibleBase=eligible_base,
        coverageMonths=coverage_months,
        reservedMonths=reserved_months,
        targetBase=target_base,
        gapBase=gap_base,
    )
