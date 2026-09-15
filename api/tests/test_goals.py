"""Phase 3.3 Task 1: pure reservation and goal progress math (spec "Reservation rules", "Goal
progress"). Database-free — every case here is an edge in `goals.py`'s functions, not a route."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from oneledger.goals import (
    ReservationProgress,
    goal_totals,
    months_remaining,
    reservation_progress,
    resource_totals,
)

_ZERO = Decimal("0.0000")


# ---------------------------------------------------------------------------------------
# reservation_progress: amount vs whole-account decomposition, FX-only, market-only, both.


def test_an_amount_reservation_has_no_market_movement_only_fx_drift() -> None:
    """An amount reservation's native_now never differs from what was reserved (change is
    always zero) — only the rate can move it, so market and inflow are both zero."""
    progress = reservation_progress(
        native_amount=Decimal("1000.0000"),
        rate_at_reservation=Decimal("0.2500000000"),
        native_now=Decimal("1000.0000"),
        rate_now=Decimal("0.3000000000"),
        inflow_native=_ZERO,
    )
    assert progress is not None
    assert progress.change == _ZERO
    assert progress.market == _ZERO
    assert progress.contributed == Decimal("250.0000")
    assert progress.current == Decimal("300.0000")
    assert progress.fx == Decimal("50.0000")
    assert progress.contributed + progress.market + progress.fx == progress.current


def test_a_ledger_balance_whole_account_treats_all_change_as_contribution() -> None:
    """A balance moves only by postings, so its whole change counts as new money, not market
    movement — the same-currency, same-rate case isolates this to zero FX and zero market."""
    progress = reservation_progress(
        native_amount=Decimal("1000.0000"),
        rate_at_reservation=Decimal("1.0000000000"),
        native_now=Decimal("1500.0000"),
        rate_now=Decimal("1.0000000000"),
        inflow_native=Decimal("500.0000"),  # ledger_balance: inflow = change
    )
    assert progress is not None
    assert progress.market == _ZERO
    assert progress.fx == _ZERO
    assert progress.contributed == Decimal("1500.0000")
    assert progress.current == Decimal("1500.0000")


def test_a_holdings_account_with_no_purchase_reports_pure_market_movement() -> None:
    """No purchase since reserved_on means the holdings query's inflow is zero, so the whole
    change is market movement (price appreciation), not a contribution."""
    progress = reservation_progress(
        native_amount=Decimal("1000.0000"),
        rate_at_reservation=Decimal("1.0000000000"),
        native_now=Decimal("1300.0000"),
        rate_now=Decimal("1.0000000000"),
        inflow_native=_ZERO,
    )
    assert progress is not None
    assert progress.contributed == Decimal("1000.0000")
    assert progress.market == Decimal("300.0000")
    assert progress.fx == _ZERO
    assert progress.current == Decimal("1300.0000")


def test_market_and_fx_movement_both_apply_and_sum_exactly_to_current() -> None:
    progress = reservation_progress(
        native_amount=Decimal("1000.0000"),
        rate_at_reservation=Decimal("1.0000000000"),
        native_now=Decimal("1300.0000"),
        rate_now=Decimal("1.1000000000"),
        inflow_native=_ZERO,
    )
    assert progress is not None
    assert progress.contributed == Decimal("1000.0000")
    assert progress.market == Decimal("330.0000")
    assert progress.current == Decimal("1430.0000")
    assert progress.fx == Decimal("100.0000")
    assert progress.contributed + progress.market + progress.fx == progress.current


def test_inflow_market_and_fx_all_apply_together_and_sum_exactly() -> None:
    """A holdings account with both a purchase since reservation and a further rate move: the
    purchase is a contribution, the rest of the change is market movement, and the rate delta on
    every native dollar (reserved or contributed) is FX — the three always sum to current."""
    progress = reservation_progress(
        native_amount=Decimal("1000.0000"),
        rate_at_reservation=Decimal("1.0000000000"),
        native_now=Decimal("1500.0000"),
        rate_now=Decimal("1.2000000000"),
        inflow_native=Decimal("200.0000"),
    )
    assert progress is not None
    assert progress.contributed == Decimal("1240.0000")
    assert progress.market == Decimal("360.0000")
    assert progress.current == Decimal("1800.0000")
    assert progress.fx == Decimal("200.0000")
    assert progress.contributed + progress.market + progress.fx == progress.current


def test_a_missing_value_or_rate_returns_none() -> None:
    assert (
        reservation_progress(
            native_amount=Decimal("1000.0000"),
            rate_at_reservation=Decimal("1.0000000000"),
            native_now=None,
            rate_now=Decimal("1.0000000000"),
            inflow_native=_ZERO,
        )
        is None
    )
    assert (
        reservation_progress(
            native_amount=Decimal("1000.0000"),
            rate_at_reservation=Decimal("1.0000000000"),
            native_now=Decimal("1000.0000"),
            rate_now=None,
            inflow_native=_ZERO,
        )
        is None
    )


# ---------------------------------------------------------------------------------------
# months_remaining: the count of month-starts after asOf up to and including target_date.


def test_months_remaining_at_a_month_end_boundary() -> None:
    assert months_remaining(date(2026, 1, 31), date(2026, 2, 1)) == 1


def test_months_remaining_is_zero_once_due() -> None:
    assert months_remaining(date(2026, 2, 1), date(2026, 2, 1)) == 0
    assert months_remaining(date(2026, 3, 1), date(2026, 2, 1)) == 0


def test_months_remaining_within_the_same_month_is_zero() -> None:
    assert months_remaining(date(2026, 1, 1), date(2026, 1, 31)) == 0


def test_months_remaining_across_a_year_boundary() -> None:
    assert months_remaining(date(2026, 12, 15), date(2027, 1, 1)) == 1


def test_months_remaining_counts_every_month_start_in_between() -> None:
    assert months_remaining(date(2026, 1, 15), date(2026, 4, 15)) == 3


# ---------------------------------------------------------------------------------------
# goal_totals: sums, shortfall/surplus, percent, unavailable propagation.


def test_goal_totals_with_no_reservations_is_zero_and_available() -> None:
    progress = goal_totals(
        target_amount=Decimal("1000.0000"),
        planned_monthly=None,
        target_date=date(2026, 6, 1),
        as_of=date(2026, 1, 1),
        reservation_progresses=[],
        over_reserved=False,
    )
    assert progress.status == "available"
    assert progress.current == _ZERO
    assert progress.percent == Decimal("0.00")


def test_goal_totals_sums_every_reservation_exactly() -> None:
    a = ReservationProgress(
        change=_ZERO,
        inflow=_ZERO,
        contributed=Decimal("250.0000"),
        market=_ZERO,
        current=Decimal("300.0000"),
        fx=Decimal("50.0000"),
    )
    b = ReservationProgress(
        change=Decimal("500.0000"),
        inflow=Decimal("500.0000"),
        contributed=Decimal("1500.0000"),
        market=_ZERO,
        current=Decimal("1500.0000"),
        fx=_ZERO,
    )
    progress = goal_totals(
        target_amount=Decimal("2000.0000"),
        planned_monthly=Decimal("100.0000"),
        target_date=date(2026, 4, 1),
        as_of=date(2026, 1, 1),
        reservation_progresses=[a, b],
        over_reserved=False,
    )
    assert progress.status == "available"
    assert progress.current == Decimal("1800.0000")
    assert progress.contributed == Decimal("1750.0000")
    assert progress.market == _ZERO
    assert progress.fx == Decimal("50.0000")
    # asOf 1 Jan, target 1 Apr: month-starts after asOf through target are Feb, Mar, Apr — 3.
    assert progress.monthsRemaining == 3
    assert progress.projected == Decimal("1800.0000") + Decimal("300.0000")
    assert progress.shortfall == _ZERO
    assert progress.surplus == progress.projected - Decimal("2000.0000")


def test_goal_totals_reports_shortfall_when_projected_misses_target() -> None:
    progress = goal_totals(
        target_amount=Decimal("10000.0000"),
        planned_monthly=Decimal("0.0000"),
        target_date=date(2026, 1, 1),
        as_of=date(2026, 1, 1),
        reservation_progresses=[
            ReservationProgress(
                change=_ZERO,
                inflow=_ZERO,
                contributed=Decimal("6000.0000"),
                market=_ZERO,
                current=Decimal("6000.0000"),
                fx=_ZERO,
            )
        ],
        over_reserved=False,
    )
    assert progress.shortfall == Decimal("4000.0000")
    assert progress.surplus == _ZERO


def test_goal_totals_percent_rounds_half_to_even() -> None:
    progress = goal_totals(
        target_amount=Decimal("1000.0000"),
        planned_monthly=None,
        target_date=date(2026, 6, 1),
        as_of=date(2026, 1, 1),
        reservation_progresses=[
            ReservationProgress(
                change=_ZERO,
                inflow=_ZERO,
                contributed=Decimal("123.4500"),
                market=_ZERO,
                current=Decimal("123.4500"),
                fx=_ZERO,
            )
        ],
        over_reserved=False,
    )
    # 123.45 / 1000 * 100 = 12.345 exactly — half-even rounds to the even neighbour, 12.34.
    assert progress.percent == Decimal("12.34")


def test_goal_totals_is_unavailable_when_any_reservation_is_missing_data() -> None:
    progress = goal_totals(
        target_amount=Decimal("1000.0000"),
        planned_monthly=None,
        target_date=date(2026, 6, 1),
        as_of=date(2026, 1, 1),
        reservation_progresses=[
            ReservationProgress(
                change=_ZERO,
                inflow=_ZERO,
                contributed=Decimal("100.0000"),
                market=_ZERO,
                current=Decimal("100.0000"),
                fx=_ZERO,
            ),
            None,
        ],
        over_reserved=False,
    )
    assert progress.status == "unavailable"
    assert progress.current is None
    assert progress.contributed is None
    assert progress.percent is None
    assert "missing_value_or_rate" in progress.reasons


def test_goal_totals_flags_over_reserved_without_hiding_the_total() -> None:
    progress = goal_totals(
        target_amount=Decimal("1000.0000"),
        planned_monthly=None,
        target_date=date(2026, 6, 1),
        as_of=date(2026, 1, 1),
        reservation_progresses=[
            ReservationProgress(
                change=_ZERO,
                inflow=_ZERO,
                contributed=Decimal("100.0000"),
                market=_ZERO,
                current=Decimal("100.0000"),
                fx=_ZERO,
            )
        ],
        over_reserved=True,
    )
    assert progress.status == "available"
    assert progress.reasons == ["over_reserved"]
    assert progress.current == Decimal("100.0000")


# ---------------------------------------------------------------------------------------
# resource_totals: reserved/unreserved, whole-account override, over-reservation.


def test_resource_totals_sums_active_amount_reservations() -> None:
    totals = resource_totals(
        Decimal("1000.0000"),
        [Decimal("300.0000"), Decimal("200.0000")],
        whole_account_active=False,
    )
    assert totals.reserved == Decimal("500.0000")
    assert totals.unreserved == Decimal("500.0000")
    assert totals.overReserved is False
    assert totals.wholeAccountReserved is False


def test_resource_totals_whole_account_reserves_the_entire_value() -> None:
    totals = resource_totals(Decimal("800.0000"), [], whole_account_active=True)
    assert totals.reserved == Decimal("800.0000")
    assert totals.unreserved == _ZERO
    assert totals.wholeAccountReserved is True


def test_resource_totals_unreserved_never_goes_below_zero_when_over_reserved() -> None:
    totals = resource_totals(
        Decimal("500.0000"),
        [Decimal("300.0000"), Decimal("300.0000")],
        whole_account_active=False,
    )
    assert totals.reserved == Decimal("600.0000")
    assert totals.unreserved == _ZERO
    assert totals.overReserved is True


def test_resource_totals_with_no_value_still_sums_frozen_amounts() -> None:
    totals = resource_totals(None, [Decimal("300.0000")], whole_account_active=False)
    assert totals.reserved == Decimal("300.0000")
    assert totals.unreserved is None
    assert totals.overReserved is False
