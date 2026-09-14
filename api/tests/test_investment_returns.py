"""XIRR, event classification and allocation grouping: pure Decimal, no database."""

from __future__ import annotations

import time
from datetime import date, timedelta
from decimal import Decimal

from oneledger.investments.returns import (
    CashFlow,
    PositionValue,
    XirrResult,
    classify_event_flows,
    group_allocation,
    xirr,
)


def _bisection_reference(flows: list[tuple[date, float]]) -> float:
    """An independent float bisection over the same equation, for the 0.01pp acceptance check."""
    d0 = flows[0][0]

    def f(rate: float) -> float:
        return float(sum(amount / (1 + rate) ** ((on - d0).days / 365) for on, amount in flows))

    lo, hi = -0.999999, 100.0
    lo_f = f(lo)
    for _ in range(200):
        mid = (lo + hi) / 2
        mid_f = f(mid)
        if (mid_f < 0) == (lo_f < 0):
            lo, lo_f = mid, mid_f
        else:
            hi = mid
    return (lo + hi) / 2 * 100


def test_xirr_matches_an_independently_computed_rate_within_one_hundredth_of_a_point() -> None:
    raw = [
        (date(2020, 1, 1), -10000.0),
        (date(2020, 3, 1), -2000.0),
        (date(2020, 10, 30), 1500.0),
        (date(2021, 2, 15), 2000.0),
        (date(2021, 4, 1), 10000.0),
    ]
    flows = [CashFlow(on, Decimal(str(amount))) for on, amount in raw]

    result = xirr(flows)

    assert result.reason is None
    assert result.rate_percent is not None
    reference = _bisection_reference(raw)
    assert abs(float(result.rate_percent) - reference) <= 0.01


def test_xirr_completes_a_sixty_one_flow_monthly_series_quickly() -> None:
    """A holding with five years of monthly contributions must not make the portfolio wait."""
    flows = [
        CashFlow(date(2020, 1, 1) + timedelta(days=30 * i), Decimal("-100")) for i in range(60)
    ]
    flows.append(CashFlow(date(2020, 1, 1) + timedelta(days=30 * 60), Decimal("7500")))

    started = time.monotonic()
    result = xirr(flows)
    elapsed = time.monotonic() - started

    assert result.reason is None
    assert result.rate_percent is not None
    assert elapsed < 2.0, f"xirr took {elapsed:.2f}s for 61 flows"


def test_xirr_refuses_a_series_with_no_sign_change() -> None:
    flows = [
        CashFlow(date(2020, 1, 1), Decimal("100")),
        CashFlow(date(2021, 1, 1), Decimal("200")),
    ]

    assert xirr(flows) == XirrResult(None, "no_sign_change")


def test_xirr_reports_multiple_roots_rather_than_choosing_one() -> None:
    # The textbook two-IRR cash flow: invest, get paid back more than invested, then pay back
    # more than that — solvable at two different discount rates.
    flows = [
        CashFlow(date(2020, 1, 1), Decimal("-4000")),
        CashFlow(date(2021, 1, 1), Decimal("25000")),
        CashFlow(date(2022, 1, 1), Decimal("-25000")),
    ]

    assert xirr(flows) == XirrResult(None, "multiple_roots")


def test_internal_events_add_no_flow() -> None:
    events = [
        ("purchase", date(2025, 1, 1), Decimal("-1000")),
        ("reinvested_distribution", date(2025, 6, 1), Decimal("0")),
        ("bonus", date(2025, 6, 1), Decimal("0")),
        ("split", date(2025, 7, 1), Decimal("0")),
    ]

    classified = classify_event_flows(events)

    assert classified.flows == [CashFlow(date(2025, 1, 1), Decimal("-1000"))]
    assert not classified.has_blocking_leg
    assert not classified.has_unknown_kind


def test_switch_leg_marks_incomplete_history_but_contributes_no_flow() -> None:
    events = [
        ("switch_in", date(2025, 1, 1), Decimal("0")),
        ("sale", date(2025, 6, 1), Decimal("500")),
    ]

    classified = classify_event_flows(events)

    assert classified.has_blocking_leg
    assert classified.flows == [CashFlow(date(2025, 6, 1), Decimal("500"))]


def test_unknown_event_kind_is_blocked_rather_than_silently_dropped() -> None:
    events = [("mystery_kind", date(2025, 1, 1), Decimal("-10"))]

    classified = classify_event_flows(events)

    assert classified.has_unknown_kind


def test_charges_and_tax_are_counted_once() -> None:
    events = [
        ("purchase", date(2025, 1, 1), Decimal("-1021")),  # gross + charges + tax, frozen once
        ("charge", date(2025, 3, 1), Decimal("-5")),
        ("withheld_tax", date(2025, 3, 1), Decimal("-2")),
        ("sale", date(2025, 12, 1), Decimal("1100")),
    ]

    classified = classify_event_flows(events)

    assert [flow.amount for flow in classified.flows] == [
        Decimal("-1021"),
        Decimal("-5"),
        Decimal("-2"),
        Decimal("1100"),
    ]


def test_allocation_is_exact_and_reconciles_to_the_whole_when_complete() -> None:
    positions = [
        PositionValue("equity", "account-1", Decimal("600.0000"), True),
        PositionValue("equity", "account-2", Decimal("300.0000"), True),
        PositionValue("fixed_income", "account-1", Decimal("100.0000"), True),
    ]

    result = group_allocation(positions)

    assert result.complete is True
    assert result.total_value_base == Decimal("1000.0000")
    by_class = {group.label: group for group in result.by_asset_class}
    assert by_class["equity"].value_base == Decimal("900.0000")
    assert by_class["equity"].ratio == Decimal("0.9000000000")
    assert by_class["fixed_income"].ratio == Decimal("0.1000000000")
    ratios = [group.ratio for group in result.by_asset_class]
    assert all(ratio is not None for ratio in ratios)
    total_ratio = sum((ratio for ratio in ratios if ratio is not None), Decimal(0))
    assert total_ratio == Decimal("1.0000000000")


def test_allocation_is_incomplete_when_an_included_holding_is_unvalued() -> None:
    positions = [
        PositionValue("equity", "account-1", Decimal("600.0000"), True),
        PositionValue("fixed_income", "account-1", Decimal("0"), False),
    ]

    result = group_allocation(positions)

    assert result.complete is False
    assert all(group.ratio is None for group in result.by_asset_class)
    # The valued subset's sum is still exact — missing coverage never masquerades as zero.
    by_class = {group.label: group for group in result.by_asset_class}
    assert by_class["equity"].value_base == Decimal("600.0000")
    assert "fixed_income" not in by_class
