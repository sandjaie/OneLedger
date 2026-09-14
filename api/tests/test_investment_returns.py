"""XIRR, event classification and allocation grouping: pure Decimal, no database."""

from __future__ import annotations

import random
import time
from datetime import date, timedelta
from decimal import Decimal, localcontext

from oneledger.investments.returns import (
    _GRID_STEPS,
    _PERCENT_QUANT,
    _PRECISION,
    _RATE_MAX,
    _RATE_MIN,
    _RATE_TOLERANCE,
    _RESIDUAL_TOLERANCE,
    CashFlow,
    PositionValue,
    XirrResult,
    _aggregate_by_date,
    _bisect,
    _residual,
    classify_event_flows,
    group_allocation,
    xirr,
)


def _exact_reference_xirr(flows: list[CashFlow]) -> XirrResult:
    """The pre-optimization algorithm: every grid point through the exact 50-digit `_residual`,
    with no float fast-path. Exists only so `xirr`'s float-scan optimization can be proven to
    change nothing (OL-001) — this must stay a byte-for-byte copy of `xirr`'s old control flow,
    not a second implementation of the maths."""
    points = _aggregate_by_date(flows)
    scale = sum((abs(flow.amount) for flow in points), Decimal(0))
    if len(points) < 2 or scale == 0:
        return XirrResult(None, "no_sign_change")

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        d0 = min(flow.on for flow in points)
        days = [((flow.on - d0).days, flow.amount) for flow in points]

        def residual(rate: Decimal) -> Decimal:
            return _residual(days, rate, scale)

        y_min = (_RATE_MIN + 1).ln()
        y_max = (_RATE_MAX + 1).ln()
        step = (y_max - y_min) / _GRID_STEPS

        grid_r: list[Decimal] = []
        grid_f: list[Decimal] = []
        for i in range(_GRID_STEPS + 1):
            r = (y_min + step * i).exp() - 1
            grid_r.append(r)
            grid_f.append(residual(r))

        roots: list[Decimal] = [
            grid_r[i] for i, value in enumerate(grid_f) if abs(value) <= _RESIDUAL_TOLERANCE
        ]
        for i in range(_GRID_STEPS):
            lo_f, hi_f = grid_f[i], grid_f[i + 1]
            if lo_f == 0 or hi_f == 0 or (lo_f < 0) == (hi_f < 0):
                continue
            root, converged = _bisect(residual, grid_r[i], grid_r[i + 1], lo_f)
            if not converged:
                return XirrResult(None, "not_converged")
            roots.append(root)

        if not roots:
            return XirrResult(None, "no_sign_change")

        roots.sort()
        merged = [roots[0]]
        for root in roots[1:]:
            if root - merged[-1] > _RATE_TOLERANCE:
                merged.append(root)
        if len(merged) > 1:
            return XirrResult(None, "multiple_roots")

        percent = (merged[0] * 100).quantize(_PERCENT_QUANT)
        return XirrResult(percent, None)


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


def _random_series(rng: random.Random, count: int) -> list[CashFlow]:
    """An outflow, `count - 2` mixed-sign flows, then a clear positive terminal value — plausible
    portfolio shape, distinct dates, so `_aggregate_by_date` never collapses it below `count`."""
    d0 = date(2018, 1, 1)
    used_days = {0}
    flows = [CashFlow(d0, Decimal(-rng.randint(1_000, 100_000)))]
    for _ in range(count - 2):
        day = rng.randint(1, 3000)
        while day in used_days:
            day = rng.randint(1, 3000)
        used_days.add(day)
        sign = rng.choice([-1, 1])
        flows.append(CashFlow(d0 + timedelta(days=day), Decimal(sign * rng.randint(10, 5_000))))
    flows.append(CashFlow(d0 + timedelta(days=3500), Decimal(rng.randint(1_000, 200_000))))
    return flows


def test_fast_xirr_matches_the_exact_reference_on_diverse_series() -> None:
    """OL-001: the float grid pre-scan must never change a result, only how fast it arrives."""
    rng = random.Random(20260914)
    monthly = [
        CashFlow(date(2020, 1, 1) + timedelta(days=30 * i), Decimal("-100")) for i in range(60)
    ]
    monthly.append(CashFlow(date(2020, 1, 1) + timedelta(days=30 * 60), Decimal("7500")))
    series = [
        monthly,
        [CashFlow(date(2020, 1, 1), Decimal("100")), CashFlow(date(2021, 1, 1), Decimal("200"))],
        [
            CashFlow(date(2020, 1, 1), Decimal("-4000")),
            CashFlow(date(2021, 1, 1), Decimal("25000")),
            CashFlow(date(2022, 1, 1), Decimal("-25000")),
        ],
        *(_random_series(rng, rng.randint(5, 40)) for _ in range(5)),
    ]

    for flows in series:
        assert xirr(flows) == _exact_reference_xirr(flows)


def test_xirr_completes_a_twelve_hundred_distinct_date_series_quickly() -> None:
    """~20 holdings x 60 monthly flows is close to 1,200 dates — the portfolio-scale case
    OL-001 exists for. Before the float pre-scan this took several seconds."""
    flows = [CashFlow(date(2015, 1, 1) + timedelta(days=i), Decimal("-10")) for i in range(1199)]
    flows.append(CashFlow(date(2015, 1, 1) + timedelta(days=1199), Decimal("14000")))

    started = time.monotonic()
    result = xirr(flows)
    elapsed = time.monotonic() - started

    assert result.reason is None
    assert result.rate_percent is not None
    assert elapsed < 1.5, f"xirr took {elapsed:.2f}s for 1,200 distinct-date flows"
