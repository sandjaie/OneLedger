"""Phase 4.3 Task 1's pure investment-analysis engine: Modified Dietz, contribution effects,
group XIRR classification, allocation-target inference and drift, and benchmark excess. No
database — `test_investment_analysis_live.py` covers the route and its SQL."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from oneledger.investment_analysis import (
    ContributionLines,
    DietzFlow,
    DietzResult,
    GroupComputation,
    GroupEvent,
    MonthFlowEvent,
    MonthPositionRow,
    MonthResult,
    compute_drift,
    compute_group,
    compute_month,
    compute_year,
    contribution_lines,
    excess_percent,
    inception_window,
    infer_target,
    modified_dietz,
)
from oneledger.investments.returns import XirrResult

# ---------------------------------------------------------------------------
# compute_group
# ---------------------------------------------------------------------------


def test_compute_group_matches_a_hand_computed_xirr_on_two_flows_and_a_terminal_value() -> None:
    events = [
        GroupEvent("purchase", date(2024, 1, 1), Decimal("-1000")),
        GroupEvent("sale", date(2024, 6, 1), Decimal("200")),
    ]

    result = compute_group(
        events,
        date(2025, 1, 1),
        any_open=True,
        value_complete=True,
        terminal_value=Decimal("1000"),
    )

    assert result.xirr_result.reason is None
    assert result.xirr_result.rate_percent is not None
    assert result.terminal_value == Decimal("1000")
    assert [flow.net_amount_base for flow in result.dietz_flows] == [
        Decimal("-1000"),
        Decimal("200"),
    ]


def test_compute_group_fails_closed_on_a_missing_price_in_the_group() -> None:
    events = [GroupEvent("purchase", date(2024, 1, 1), Decimal("-1000"))]

    result = compute_group(
        events, date(2025, 1, 1), any_open=True, value_complete=False, terminal_value=Decimal(0)
    )

    assert result.xirr_result == XirrResult(None, "missing_price")
    assert result.terminal_value is None


def test_compute_group_treats_a_switch_as_cash_neutral_when_its_counterpart_is_in_group() -> None:
    events = [
        GroupEvent("purchase", date(2024, 1, 1), Decimal("-1000")),
        GroupEvent("switch_out", date(2024, 3, 1), Decimal("0"), counterpart_in_group=True),
        GroupEvent("switch_in", date(2024, 3, 1), Decimal("0"), counterpart_in_group=True),
    ]

    result = compute_group(
        events,
        date(2025, 1, 1),
        any_open=True,
        value_complete=True,
        terminal_value=Decimal("1200"),
    )

    assert result.xirr_result.reason is None
    assert [flow.kind for flow in result.dietz_flows] == ["purchase"]


def test_compute_group_is_incomplete_history_when_a_switch_counterpart_is_outside_the_group() -> (
    None
):
    events = [
        GroupEvent("purchase", date(2024, 1, 1), Decimal("-1000")),
        GroupEvent("switch_out", date(2024, 3, 1), Decimal("0"), counterpart_in_group=False),
    ]

    result = compute_group(
        events,
        date(2025, 1, 1),
        any_open=True,
        value_complete=True,
        terminal_value=Decimal("1000"),
    )

    assert result.xirr_result == XirrResult(None, "incomplete_history")


def test_compute_group_is_incomplete_history_for_an_outside_platform_transfer_too() -> None:
    events = [
        GroupEvent("purchase", date(2024, 1, 1), Decimal("-1000")),
        GroupEvent(
            "platform_transfer_in", date(2024, 3, 1), Decimal("0"), counterpart_in_group=False
        ),
    ]

    result = compute_group(
        events,
        date(2025, 1, 1),
        any_open=True,
        value_complete=True,
        terminal_value=Decimal("1000"),
    )

    assert result.xirr_result == XirrResult(None, "incomplete_history")


def test_compute_group_blocks_on_an_unknown_event_kind() -> None:
    events = [GroupEvent("mystery_kind", date(2024, 1, 1), Decimal("-10"))]

    result = compute_group(
        events, date(2025, 1, 1), any_open=True, value_complete=True, terminal_value=Decimal(10)
    )

    assert result.xirr_result == XirrResult(None, "blocked_action")


def test_compute_group_reports_no_holdings_as_a_missing_terminal_value() -> None:
    result = compute_group(
        [], date(2025, 1, 1), any_open=False, value_complete=True, terminal_value=Decimal(0)
    )

    assert result.xirr_result == XirrResult(None, "missing_terminal_value")
    assert result.terminal_value is None


# ---------------------------------------------------------------------------
# modified_dietz
# ---------------------------------------------------------------------------


def test_modified_dietz_agrees_with_the_formula_on_a_mid_month_purchase() -> None:
    t0, t1 = date(2024, 1, 31), date(2024, 2, 29)
    # A 1,000 purchase 10 days before month end (weight = 10/29), BMV 5,000, EMV 6,100.
    flows = [DietzFlow("purchase", date(2024, 2, 19), Decimal("-1000"))]

    result = modified_dietz(flows, t0, t1, Decimal("5000"), Decimal("6100"))

    span = (t1 - t0).days
    weight = Decimal((t1 - date(2024, 2, 19)).days) / Decimal(span)
    expected = (
        (Decimal("6100") - Decimal("5000") - Decimal("1000"))
        / (Decimal("5000") + Decimal("1000") * weight)
        * 100
    )
    assert result.reason is None
    assert result.percent == expected.quantize(Decimal("0.00000001"))


def test_modified_dietz_reports_no_denominator_when_opening_and_weight_are_zero() -> None:
    result = modified_dietz([], date(2024, 1, 31), date(2024, 2, 29), Decimal(0), Decimal(0))

    assert result == DietzResult(None, "no_denominator")


def test_modified_dietz_is_missing_price_when_either_endpoint_is_unavailable() -> None:
    assert modified_dietz([], date(2024, 1, 1), date(2024, 2, 1), None, Decimal(100)) == (
        DietzResult(None, "missing_price")
    )
    assert modified_dietz([], date(2024, 1, 1), date(2024, 2, 1), Decimal(100), None) == (
        DietzResult(None, "missing_price")
    )


# ---------------------------------------------------------------------------
# contribution_lines
# ---------------------------------------------------------------------------


def test_contribution_lines_follow_the_spec_table_and_plug_market_and_fx() -> None:
    flows = [
        DietzFlow("purchase", date(2024, 2, 1), Decimal("-1000")),
        DietzFlow("sale", date(2024, 2, 10), Decimal("200")),
        DietzFlow("cash_distribution", date(2024, 2, 15), Decimal("50")),
        DietzFlow("charge", date(2024, 2, 20), Decimal("-5")),
    ]

    lines = contribution_lines(flows, Decimal("5000"), Decimal("6100"))

    assert lines.contributions == Decimal("1000")
    assert lines.withdrawals == Decimal("-200")
    assert lines.income == Decimal("-50")
    assert lines.charges == Decimal("5")
    # opening + C + marketAndFx == closing, definitionally, but is not itself the test.
    assert lines.opening is not None
    assert lines.contributions is not None
    assert lines.withdrawals is not None
    assert lines.income is not None
    assert lines.charges is not None
    assert lines.market_and_fx is not None
    assert (
        lines.opening
        + lines.contributions
        + lines.withdrawals
        + lines.income
        + lines.charges
        + lines.market_and_fx
    ) == lines.closing


def test_contribution_lines_are_null_throughout_when_an_endpoint_is_missing() -> None:
    lines = contribution_lines([], None, Decimal(100))

    assert lines == ContributionLines(None, None, None, None, None, None, None)


# ---------------------------------------------------------------------------
# infer_target
# ---------------------------------------------------------------------------


def test_infer_target_sums_to_one_after_remainder_assignment() -> None:
    ratios = {
        "equity": Decimal("0.6666666667"),
        "fixed_income": Decimal("0.3333333333"),
    }

    groups = infer_target(ratios)

    total = sum((group.ratio for group in groups), Decimal(0))
    assert total == Decimal("1.0000000000")
    # The remainder (0.0000000000... rounding slack) goes to the largest ratio, equity.
    by_label = {group.label: group.ratio for group in groups}
    assert by_label["equity"] >= ratios["equity"]


def test_infer_target_breaks_a_tie_by_enum_order() -> None:
    ratios = {"cash": Decimal("0.5000000000"), "equity": Decimal("0.5000000000")}

    groups = infer_target(ratios)

    by_label = {group.label: group.ratio for group in groups}
    # equity precedes cash in ASSET_CLASS_ORDER, so any remainder goes to equity.
    assert by_label["equity"] + by_label["cash"] == Decimal("1.0000000000")
    assert by_label["equity"] >= by_label["cash"]


def test_infer_target_is_empty_with_no_complete_month_yet() -> None:
    assert infer_target({}) == []


# ---------------------------------------------------------------------------
# compute_drift / excess_percent
# ---------------------------------------------------------------------------


def test_compute_drift_is_null_when_actual_or_target_is_missing() -> None:
    assert compute_drift(None, Decimal("0.5")) is None
    assert compute_drift(Decimal("0.5"), None) is None


def test_compute_drift_is_actual_minus_target_at_ten_decimal_places() -> None:
    drift = compute_drift(Decimal("0.6000000000"), Decimal("0.5000000000"))

    assert drift == Decimal("0.1000000000")


def test_excess_percent_is_null_when_either_side_is_unavailable() -> None:
    subject = DietzResult(Decimal("5.00000000"), None)
    unavailable = DietzResult(None, "missing_price")

    assert excess_percent(subject, unavailable) is None
    assert excess_percent(unavailable, subject) is None


def test_excess_percent_is_subject_minus_benchmark() -> None:
    subject = DietzResult(Decimal("5.00000000"), None)
    benchmark = DietzResult(Decimal("3.50000000"), None)

    assert excess_percent(subject, benchmark) == Decimal("1.50000000")


# ---------------------------------------------------------------------------
# compute_month
# ---------------------------------------------------------------------------


def test_compute_month_is_incomplete_when_a_row_is_not_available() -> None:
    opening = [MonthPositionRow("equity", Decimal("1000"), "available")]
    closing = [MonthPositionRow("equity", None, "unpriced_holding")]

    result = compute_month(
        date(2024, 2, 29),
        date(2024, 1, 31),
        opening,
        closing,
        [],
        {"available": 1, "unpriced_holding": 1},
        {},
    )

    assert result.complete is False
    assert result.portfolio_dietz == DietzResult(None, "missing_price")
    assert result.portfolio_flows == ()
    assert all(effect.dietz == DietzResult(None, "missing_price") for effect in result.classes)
    assert all(effect.ratio is None for effect in result.classes)


def test_compute_month_class_lines_sum_to_the_portfolio_line() -> None:
    opening = [
        MonthPositionRow("equity", Decimal("3000"), "available"),
        MonthPositionRow("fixed_income", Decimal("2000"), "available"),
    ]
    closing = [
        MonthPositionRow("equity", Decimal("3900"), "available"),
        MonthPositionRow("fixed_income", Decimal("2000"), "available"),
    ]
    events = [
        MonthFlowEvent("equity", "purchase", date(2024, 2, 15), Decimal("-800")),
    ]

    result = compute_month(
        date(2024, 2, 29),
        date(2024, 1, 31),
        opening,
        closing,
        events,
        {"available": 4},
        {"equity": Decimal("0.6"), "fixed_income": Decimal("0.4")},
    )

    assert result.complete is True
    by_label = {effect.label: effect for effect in result.classes}
    for field in ("contributions", "withdrawals", "income", "charges", "market_and_fx"):
        class_sum = sum((getattr(effect.lines, field) for effect in result.classes), Decimal(0))
        assert class_sum == getattr(result.portfolio_lines, field)
    assert by_label["equity"].lines.contributions == Decimal("800")
    assert by_label["fixed_income"].lines.contributions == Decimal("0")
    # Actual ratio and drift are reported once every class has a valued month end.
    assert by_label["equity"].ratio is not None
    assert by_label["equity"].drift is not None


def test_compute_month_gives_a_target_only_class_a_zero_actual_ratio() -> None:
    opening = [MonthPositionRow("equity", Decimal("1000"), "available")]
    closing = [MonthPositionRow("equity", Decimal("1000"), "available")]

    result = compute_month(
        date(2024, 2, 29),
        date(2024, 1, 31),
        opening,
        closing,
        [],
        {"available": 2},
        {"equity": Decimal("0.5000000000"), "cash": Decimal("0.5000000000")},
    )

    by_label = {effect.label: effect for effect in result.classes}
    assert by_label["cash"].ratio == Decimal("0.0000000000")
    assert by_label["cash"].target_ratio == Decimal("0.5000000000")
    assert by_label["cash"].drift == Decimal("-0.5000000000")


def test_compute_month_gives_an_actual_only_class_a_zero_target_ratio() -> None:
    opening = [MonthPositionRow("equity", Decimal("1000"), "available")]
    closing = [MonthPositionRow("equity", Decimal("1000"), "available")]

    result = compute_month(
        date(2024, 2, 29),
        date(2024, 1, 31),
        opening,
        closing,
        [],
        {"available": 2},
        {"cash": Decimal("1.0000000000")},
    )

    by_label = {effect.label: effect for effect in result.classes}
    assert by_label["equity"].target_ratio == Decimal("0.0000000000")
    assert by_label["equity"].ratio == Decimal("1.0000000000")
    assert by_label["equity"].drift == Decimal("1.0000000000")


def test_compute_month_drift_is_null_with_no_target_at_all() -> None:
    opening = [MonthPositionRow("equity", Decimal("1000"), "available")]
    closing = [MonthPositionRow("equity", Decimal("1000"), "available")]

    result = compute_month(
        date(2024, 2, 29), date(2024, 1, 31), opening, closing, [], {"available": 2}, {}
    )

    by_label = {effect.label: effect for effect in result.classes}
    assert by_label["equity"].target_ratio is None
    assert by_label["equity"].drift is None


# ---------------------------------------------------------------------------
# compute_year
# ---------------------------------------------------------------------------


def _complete_month(month_end: date, previous: date, value: Decimal) -> MonthResult:
    rows = [MonthPositionRow("equity", value, "available")]
    return compute_month(month_end, previous, rows, rows, [], {"available": 2}, {})


def test_compute_year_excludes_an_incomplete_month_and_reports_it() -> None:
    complete = _complete_month(date(2024, 1, 31), date(2023, 12, 31), Decimal("1000"))
    incomplete = compute_month(
        date(2024, 2, 29),
        date(2024, 1, 31),
        [MonthPositionRow("equity", None, "unpriced_holding")],
        [MonthPositionRow("equity", None, "unpriced_holding")],
        [],
        {"unpriced_holding": 2},
        {},
    )

    year = compute_year(2024, [complete, incomplete])

    assert year.incomplete_months == 1
    assert year.portfolio_dietz == DietzResult(None, "missing_price")
    assert year.portfolio_lines == ContributionLines(None, None, None, None, None, None, None)
    assert year.classes == ()


def test_compute_year_sums_complete_months_contribution_lines() -> None:
    jan = compute_month(
        date(2024, 1, 31),
        date(2023, 12, 31),
        [MonthPositionRow("equity", Decimal("1000"), "available")],
        [MonthPositionRow("equity", Decimal("1000"), "available")],
        [],
        {"available": 2},
        {},
    )
    feb = compute_month(
        date(2024, 2, 29),
        date(2024, 1, 31),
        [MonthPositionRow("equity", Decimal("1000"), "available")],
        [MonthPositionRow("equity", Decimal("1500"), "available")],
        [MonthFlowEvent("equity", "purchase", date(2024, 2, 10), Decimal("-400"))],
        {"available": 2},
        {},
    )

    year = compute_year(2024, [jan, feb])

    assert year.incomplete_months == 0
    assert year.portfolio_lines.opening == Decimal("1000")
    assert year.portfolio_lines.closing == Decimal("1500")
    assert year.portfolio_lines.contributions == Decimal("400")
    assert len(year.classes) == 1
    assert year.classes[0].label == "equity"
    assert year.classes[0].lines.contributions == Decimal("400")


# ---------------------------------------------------------------------------
# inception_window
# ---------------------------------------------------------------------------


def test_inception_window_uses_zero_opening_and_the_groups_own_terminal_value() -> None:
    computation = GroupComputation(
        XirrResult(Decimal("12.34567800"), None),
        (DietzFlow("purchase", date(2024, 1, 1), Decimal("-1000")),),
        Decimal("1200"),
    )

    dietz, lines = inception_window(computation, date(2023, 12, 31), date(2024, 6, 1))

    assert lines.opening == Decimal(0)
    assert lines.closing == Decimal("1200")
    assert dietz.reason is None


def test_inception_window_is_missing_price_when_the_group_itself_failed() -> None:
    computation = GroupComputation(XirrResult(None, "missing_price"), (), None)

    dietz, lines = inception_window(computation, date(2023, 12, 31), date(2024, 6, 1))

    assert lines == ContributionLines(None, None, None, None, None, None, None)
    assert dietz.reason == "missing_price"


def test_inception_window_treats_an_unsolved_rate_as_a_valid_zero_value() -> None:
    # `no_sign_change`/`multiple_roots`/`not_converged`/`missing_terminal_value` mean the flows
    # and value are fine but the rate itself is unsolved (or moot) — EMV stays usable.
    computation = GroupComputation(XirrResult(None, "missing_terminal_value"), (), None)

    dietz, lines = inception_window(computation, date(2023, 12, 31), date(2024, 6, 1))

    assert lines.opening == Decimal(0)
    assert lines.closing == Decimal(0)
    assert dietz.reason == "no_denominator"
