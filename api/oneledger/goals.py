"""Phase 3.3 Task 1: pure Decimal math for reservation and goal progress (spec "Reservation
rules", "Goal progress"). No database access — every date, rate and native amount arrives already
read by `resources.py`, so this module is exhaustively unit-testable without a connection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from .money import translate

_PERCENT = Decimal("0.01")
_ZERO = Decimal("0.0000")


@dataclass(frozen=True)
class ResourceTotals:
    """One account's reserved/unreserved split (spec "Reservation rules"), from its current value
    and its active reservations' native amounts."""

    reserved: Decimal | None
    unreserved: Decimal | None
    overReserved: bool
    wholeAccountReserved: bool


def resource_totals(
    value: Decimal | None, active_native_amounts: list[Decimal], *, whole_account_active: bool
) -> ResourceTotals:
    if whole_account_active:
        reserved = value
    else:
        reserved = sum(active_native_amounts, _ZERO)
    unreserved = None if (value is None or reserved is None) else max(value - reserved, _ZERO)
    over_reserved = value is not None and reserved is not None and reserved > value
    return ResourceTotals(reserved, unreserved, over_reserved, whole_account_active)


@dataclass(frozen=True)
class ReservationProgress:
    """One reservation's contribution to its goal (spec "Goal progress"), in the goal's currency.
    `None` from `reservation_progress` means a missing rate or value — the caller drops the whole
    goal to `unavailable` rather than a partial total."""

    change: Decimal
    inflow: Decimal
    contributed: Decimal
    market: Decimal
    current: Decimal
    fx: Decimal


def reservation_progress(
    *,
    native_amount: Decimal,
    rate_at_reservation: Decimal,
    native_now: Decimal | None,
    rate_now: Decimal | None,
    inflow_native: Decimal,
) -> ReservationProgress | None:
    if native_now is None or rate_now is None:
        return None
    change = native_now - native_amount
    contributed = translate(native_amount, rate_at_reservation) + translate(inflow_native, rate_now)
    market = translate(change - inflow_native, rate_now)
    current = translate(native_now, rate_now)
    fx = current - contributed - market
    return ReservationProgress(
        change=change,
        inflow=inflow_native,
        contributed=contributed,
        market=market,
        current=current,
        fx=fx,
    )


@dataclass(frozen=True)
class GoalProgress:
    status: str  # "available" | "unavailable"
    reasons: list[str]
    current: Decimal | None
    contributed: Decimal | None
    market: Decimal | None
    fx: Decimal | None
    projected: Decimal | None
    shortfall: Decimal | None
    surplus: Decimal | None
    percent: Decimal | None
    monthsRemaining: int


def _next_month_start(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def months_remaining(as_of: date, target_date: date) -> int:
    """The count of month-starts after `asOf` up to and including `target_date`; 0 once due."""
    if target_date <= as_of:
        return 0
    count = 0
    cursor = _next_month_start(as_of)
    while cursor <= target_date:
        count += 1
        cursor = _next_month_start(cursor)
    return count


def goal_totals(
    *,
    target_amount: Decimal,
    planned_monthly: Decimal | None,
    target_date: date,
    as_of: date,
    reservation_progresses: list[ReservationProgress | None],
    over_reserved: bool,
) -> GoalProgress:
    """Sum a goal's active reservations' progress (spec "Goal progress"). Any `None` entry (a
    missing rate or value on one reservation) makes the whole goal `unavailable` with null
    figures — summing the rest would be a smaller, confident, wrong number."""
    months = months_remaining(as_of, target_date)
    if any(progress is None for progress in reservation_progresses):
        reasons = ["missing_value_or_rate"]
        if over_reserved:
            reasons.append("over_reserved")
        return GoalProgress(
            status="unavailable",
            reasons=reasons,
            current=None,
            contributed=None,
            market=None,
            fx=None,
            projected=None,
            shortfall=None,
            surplus=None,
            percent=None,
            monthsRemaining=months,
        )
    available = [p for p in reservation_progresses if p is not None]
    current = sum((p.current for p in available), _ZERO)
    contributed = sum((p.contributed for p in available), _ZERO)
    market = sum((p.market for p in available), _ZERO)
    fx = sum((p.fx for p in available), _ZERO)
    planned = planned_monthly if planned_monthly is not None else _ZERO
    projected = current + planned * months
    shortfall = max(target_amount - projected, _ZERO)
    surplus = max(projected - target_amount, _ZERO)
    percent = (current / target_amount * 100).quantize(_PERCENT, rounding=ROUND_HALF_EVEN)
    return GoalProgress(
        status="available",
        reasons=["over_reserved"] if over_reserved else [],
        current=current,
        contributed=contributed,
        market=market,
        fx=fx,
        projected=projected,
        shortfall=shortfall,
        surplus=surplus,
        percent=percent,
        monthsRemaining=months,
    )
