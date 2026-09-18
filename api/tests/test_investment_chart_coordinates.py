"""Synthetic, pure checks for Decimal-derived portfolio chart coordinates."""

from datetime import date
from decimal import Decimal

from oneledger.investments.portfolio import _allocation_rows
from oneledger.investments.returns import AllocationGroup
from oneledger.routes.portfolio import EffectWindowOut, MonthOut, _attach_chart_coordinates


def _window(actual: str | None, benchmark: str | None) -> EffectWindowOut:
    return EffectWindowOut(
        dietzPercent=actual,
        dietzReason=None if actual is not None else "missing_price",
        contributions=None,
        withdrawals=None,
        income=None,
        charges=None,
        marketAndFx=None,
        opening=None,
        closing=None,
        benchmarkDietzPercent=benchmark,
        benchmarkDietzReason=None,
        excessPercent=None,
    )


def _month(on: date, actual: str | None, benchmark: str | None, complete: bool = True) -> MonthOut:
    return MonthOut(
        monthEnd=on,
        complete=complete,
        statusCounts={} if complete else {"missing_price": 1},
        portfolio=_window(actual, benchmark),
        classes=[],
    )


def test_monthly_chart_coordinates_share_one_exact_decimal_scale_and_leave_gaps() -> None:
    months = [
        _month(date(2024, 1, 31), "2.00000000", "1.00000000"),
        _month(date(2024, 2, 29), None, None, complete=False),
        _month(date(2024, 3, 31), "-4.00000000", None),
    ]

    _attach_chart_coordinates(months)

    assert [(m.returnCoordinate, m.benchmarkCoordinate) for m in months] == [
        (5000, 2500),
        (None, None),
        (-10000, None),
    ]
    assert months[0].portfolio.dietzPercent == "2.00000000"
    assert months[2].portfolio.dietzPercent == "-4.00000000"


def test_zero_returns_plot_zero_without_fabricating_absent_benchmark() -> None:
    months = [_month(date(2024, 1, 31), "0.00000000", None)]

    _attach_chart_coordinates(months)

    assert months[0].returnCoordinate == 0
    assert months[0].benchmarkCoordinate is None


def test_allocation_coordinates_bound_overflow_without_changing_exact_ratios() -> None:
    rows = _allocation_rows(
        [
            AllocationGroup("equity", Decimal("150.0000"), Decimal("1.5000000000")),
            AllocationGroup("cash", Decimal("-50.0000"), Decimal("-0.5000000000")),
        ]
    )

    assert [(row.chartCoordinate, row.ratio, row.valueBase) for row in rows] == [
        (10000, "1.5000000000", "150.0000"),
        (-3333, "-0.5000000000", "-50.0000"),
    ]
