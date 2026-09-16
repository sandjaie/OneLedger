"""Pure unit tests for `oneledger.salary_analysis` (Phase 4.2 Task 1, no database). Every name,
label and amount is synthetic."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from oneledger.salary_analysis import (
    Component,
    CurrencyAnalysis,
    EarningLineIn,
    PayslipIn,
    ReceiptStatus,
    SalaryReconciliationError,
    analyze_salary,
)

CCY = "USD"


def _month(text: str) -> date:
    year, month = text.split("-")
    return date(int(year), int(month), 1)


def _payslip(
    month: str,
    lines: list[tuple[str, str]],
    *,
    deductions: str = "0.0000",
    currency: str = CCY,
    receipt_status: ReceiptStatus = "unlinked",
    cash_received: str | None = None,
    payslip_id: UUID | None = None,
) -> PayslipIn:
    earning_lines = tuple(
        EarningLineIn(line_no=i, label=label, amount=Decimal(amount))
        for i, (label, amount) in enumerate(lines, start=1)
    )
    gross = sum((line.amount for line in earning_lines), Decimal("0.0000"))
    net = gross - Decimal(deductions)
    return PayslipIn(
        payslip_id=payslip_id or uuid4(),
        pay_month=_month(month),
        currency=currency,
        gross=gross,
        deductions=Decimal(deductions),
        net=net,
        lines=earning_lines,
        receipt_status=receipt_status,
        cash_received=Decimal(cash_received) if cash_received is not None else None,
    )


def _currency(
    payslips: list[PayslipIn], rules: dict[str, Component] | None = None
) -> CurrencyAnalysis:
    result = analyze_salary(payslips, rules or {})
    assert len(result) == 1
    return result[0]


def test_inference_fixed_at_the_three_month_and_three_quarter_boundary() -> None:
    # "Basic" appears in 3 of 4 consecutive months: 4*3=12 >= 3*4=12, fixed.
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
        _payslip("2026-03", [("Other", "500.0000")]),
        _payslip("2026-04", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips)
    basic = next(label for label in currency.labels if label.label == "Basic")
    assert basic.component == "fixed"
    assert basic.source == "inferred"
    assert basic.month_count == 3


def test_inference_variable_just_below_the_three_quarter_boundary() -> None:
    # "Basic" appears in 3 of 5 months: 4*3=12 < 3*5=15, variable despite 3 occurrences.
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
        _payslip("2026-03", [("Other", "500.0000")]),
        _payslip("2026-04", [("Other", "500.0000")]),
        _payslip("2026-05", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips)
    basic = next(label for label in currency.labels if label.label == "Basic")
    assert basic.component == "variable"


def test_inference_variable_below_three_occurrences() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips)
    basic = next(label for label in currency.labels if label.label == "Basic")
    assert basic.component == "variable"


def test_rule_overrides_inference_both_ways() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000"), ("Ad Hoc", "500.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
        _payslip("2026-03", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "variable", "Ad Hoc": "fixed"})
    by_label = {label.label: label for label in currency.labels}
    assert by_label["Basic"].component == "variable"
    assert by_label["Basic"].source == "rule"
    assert by_label["Ad Hoc"].component == "fixed"
    assert by_label["Ad Hoc"].source == "rule"


def test_reimbursement_leaves_compensation_but_not_net() -> None:
    payslips = [
        _payslip(
            "2026-01",
            [("Basic", "3000.0000"), ("Travel refund", "200.0000")],
            deductions="400.0000",
        ),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed", "Travel refund": "reimbursement"})
    payslip = currency.payslips[0]
    assert payslip.fixed == Decimal("3000.0000")
    assert payslip.reimbursement == Decimal("200.0000")
    assert payslip.compensation == Decimal("3000.0000")  # reimbursement excluded
    assert payslip.net_pay == Decimal("2800.0000")  # gross(3200) - deductions(400)


def test_yearly_sums_and_variable_share() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000"), ("Bonus", "1000.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed", "Bonus": "variable"})
    assert len(currency.years) == 1
    year = currency.years[0]
    assert year.compensation == Decimal("7000.0000")
    assert year.fixed == Decimal("6000.0000")
    assert year.variable == Decimal("1000.0000")
    assert year.variable_share == Decimal("14.29")  # 1000/7000*100, half-even
    assert year.payslip_months == 2
    assert year.gap_months == 0
    assert year.received_months == 0


def test_zero_compensation_gives_a_null_variable_share() -> None:
    payslips = [_payslip("2026-01", [("Refund", "100.0000")], deductions="0.0000")]
    currency = _currency(payslips, rules={"Refund": "reimbursement"})
    assert currency.years[0].variable_share is None


def test_gap_is_listed_and_counted_never_filled() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-04", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert currency.gaps == (_month("2026-02"), _month("2026-03"))
    by_year = {y.year: y for y in currency.years}
    assert by_year[2026].gap_months == 2
    assert by_year[2026].payslip_months == 2


def test_an_increment_is_reported_and_confirmed_by_a_later_month() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3200.0000")]),
        _payslip("2026-03", [("Basic", "3200.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert len(currency.increments) == 1
    inc = currency.increments[0]
    assert inc.effective_month == _month("2026-02")
    assert inc.previous_fixed == Decimal("3000.0000")
    assert inc.new_fixed == Decimal("3200.0000")
    assert inc.change == Decimal("200.0000")
    assert inc.change_percent == Decimal("6.67")
    assert inc.confirmed is True
    assert inc.across_gap is False


def test_a_change_reversed_the_next_month_is_a_one_off_not_an_increment() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "2000.0000")]),  # partial-month one-off
        _payslip("2026-03", [("Basic", "3000.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert currency.increments == ()


def test_the_latest_change_is_unconfirmed_until_a_later_month_exists() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3200.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert len(currency.increments) == 1
    assert currency.increments[0].confirmed is False


def test_an_increment_across_a_gap_says_so() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-04", [("Basic", "3200.0000")]),
        _payslip("2026-05", [("Basic", "3200.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert len(currency.increments) == 1
    assert currency.increments[0].across_gap is True


def test_a_payslip_after_a_one_off_is_compared_with_the_right_predecessor() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "2000.0000")]),  # one-off, reversed next month
        _payslip("2026-03", [("Basic", "3000.0000")]),
        _payslip("2026-04", [("Basic", "3300.0000")]),  # a real increment from January's baseline
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert len(currency.increments) == 1
    inc = currency.increments[0]
    assert inc.effective_month == _month("2026-04")
    assert inc.previous_fixed == Decimal("3000.0000")


def test_a_change_the_next_month_changes_again_is_confirmed() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3200.0000")]),
        _payslip("2026-03", [("Basic", "3400.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert len(currency.increments) == 2
    assert currency.increments[0].effective_month == _month("2026-03")
    assert currency.increments[0].confirmed is False
    assert currency.increments[1].effective_month == _month("2026-02")
    assert currency.increments[1].confirmed is True


def test_bonus_payslip_same_month_adds_no_month_no_increment_no_inference_count() -> None:
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
        _payslip("2026-03", [("Basic", "3000.0000")]),
        # A second payslip in March: a bonus, not a new payslip month.
        _payslip("2026-03", [("Bonus", "500.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed", "Bonus": "variable"})
    assert len(currency.payslips) == 4  # every payslip still listed
    march = [p for p in currency.payslips if p.pay_month == _month("2026-03")]
    assert len(march) == 2
    assert currency.increments == ()  # Basic's fixed pay never changed
    by_year = {y.year: y for y in currency.years}
    assert by_year[2026].payslip_months == 3  # January, February, March — not four


def test_bonus_payslip_same_month_counts_one_inference_month() -> None:
    # "Allowance" is on three payslips but only two payslip months: variable (below 3 months).
    # Counting payslips instead would make it 3 present over a 2-month span, i.e. fixed.
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000"), ("Allowance", "100.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000"), ("Allowance", "100.0000")]),
        _payslip("2026-02", [("Allowance", "100.0000")]),
    ]
    currency = _currency(payslips)
    allowance = next(label for label in currency.labels if label.label == "Allowance")
    assert allowance.month_count == 2
    assert (allowance.component, allowance.source) == ("variable", "inferred")


def test_bonus_payslip_with_fixed_lines_sums_into_its_month_for_increments() -> None:
    # March's two payslips together keep fixed pay at 3000; judged per payslip, either one alone
    # (2000 or 1000) would read as a change.
    payslips = [
        _payslip("2026-01", [("Basic", "3000.0000")]),
        _payslip("2026-02", [("Basic", "3000.0000")]),
        _payslip("2026-03", [("Basic", "2000.0000")]),
        _payslip("2026-03", [("Basic", "1000.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed"})
    assert currency.increments == ()


def test_yearly_totals_split_by_calendar_year_and_reconcile() -> None:
    payslips = [
        _payslip(
            "2025-12",
            [("Basic", "3000.0000"), ("Refund", "50.0000")],
            deductions="500.0000",
            receipt_status="received",
            cash_received="2550.0000",
        ),
        _payslip(
            "2026-01",
            [("Basic", "3000.0000"), ("Bonus", "1000.0000")],
            deductions="800.0000",
            receipt_status="received",
            cash_received="3200.0000",
        ),
        _payslip("2026-02", [("Basic", "3000.0000")], deductions="500.0000"),
    ]
    rules: dict[str, Component] = {"Basic": "fixed", "Bonus": "variable", "Refund": "reimbursement"}
    currency = _currency(payslips, rules=rules)
    assert [y.year for y in currency.years] == [2026, 2025]  # newest first
    y2026, y2025 = currency.years
    assert (y2025.compensation, y2025.reimbursement, y2025.deductions, y2025.net_pay) == (
        Decimal("3000.0000"),
        Decimal("50.0000"),
        Decimal("500.0000"),
        Decimal("2550.0000"),
    )
    assert (y2025.cash_received, y2025.payslip_months, y2025.received_months) == (
        Decimal("2550.0000"),
        1,
        1,
    )
    assert y2025.variable_share == Decimal("0.00")
    assert (y2026.compensation, y2026.deductions, y2026.net_pay, y2026.cash_received) == (
        Decimal("7000.0000"),
        Decimal("1300.0000"),
        Decimal("5700.0000"),
        Decimal("3200.0000"),
    )
    assert (y2026.payslip_months, y2026.received_months) == (2, 1)
    for year in currency.years:
        assert year.compensation + year.reimbursement - year.deductions == year.net_pay


def test_zero_previous_fixed_pay_gives_a_null_change_percent() -> None:
    payslips = [
        _payslip("2026-01", [("Bonus", "1000.0000")]),  # no fixed pay this month at all
        _payslip("2026-02", [("Basic", "3000.0000"), ("Bonus", "1000.0000")]),
    ]
    currency = _currency(payslips, rules={"Basic": "fixed", "Bonus": "variable"})
    assert len(currency.increments) == 1
    inc = currency.increments[0]
    assert inc.previous_fixed == Decimal("0.0000")
    assert inc.new_fixed == Decimal("3000.0000")
    assert inc.change_percent is None


def test_a_reconciliation_violation_raises_rather_than_reports() -> None:
    payslip = _payslip("2026-01", [("Basic", "3000.0000")])
    # Corrupt the stored gross so the earning lines no longer sum to it.
    broken = PayslipIn(
        payslip_id=payslip.payslip_id,
        pay_month=payslip.pay_month,
        currency=payslip.currency,
        gross=Decimal("9999.0000"),
        deductions=payslip.deductions,
        net=Decimal("9999.0000"),
        lines=payslip.lines,
        receipt_status=payslip.receipt_status,
        cash_received=payslip.cash_received,
    )
    with pytest.raises(SalaryReconciliationError):
        analyze_salary([broken], {"Basic": "fixed"})
