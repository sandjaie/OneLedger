"""Phase 4.2 Task 1: the pure salary-analysis engine (no database, `docs/superpowers/specs/
2026-09-16-phase-4-2-salary-analysis.md`). `routes/salary.py` is the only caller: it reads active
payslips, their earning lines, the household's earning rules and each payslip's receipt status,
then hands them here. Everything below is `Decimal` arithmetic at the payslip's own stored scale;
percentages are quantized to two places half-even. A currency's active payslips sharing a
`pay_month` form one payslip month (a separate bonus payslip adds no month of its own); inference,
gaps and increments all walk payslip months, not payslips.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal
from uuid import UUID

Component = Literal["fixed", "variable", "reimbursement"]
Source = Literal["rule", "inferred"]
ReceiptStatus = Literal["received", "unlinked", "ineligible"]

_PERCENT = Decimal("0.01")
_FIXED_MIN_MONTHS = 3


class SalaryReconciliationError(ValueError):
    """A payslip's earning-line components do not sum to its own stored gross, or its stored
    gross/deductions/net disagree — both already DB invariants (`payslip_totals_agree`,
    `payslip_net_is_gross_less_deductions`), so reaching this means the caller's component
    split or row selection is wrong, not that the payslip itself is. Raised, never reported."""


@dataclass(frozen=True)
class EarningLineIn:
    line_no: int
    label: str
    amount: Decimal


@dataclass(frozen=True)
class PayslipIn:
    payslip_id: UUID
    pay_month: date  # first of month
    currency: str
    gross: Decimal
    deductions: Decimal
    net: Decimal
    lines: tuple[EarningLineIn, ...]
    receipt_status: ReceiptStatus
    cash_received: Decimal | None  # non-null exactly when receipt_status == "received"


@dataclass(frozen=True)
class LineOut:
    line_no: int
    label: str
    amount: Decimal
    component: Component
    source: Source


@dataclass(frozen=True)
class PayslipOut:
    payslip_id: UUID
    pay_month: date
    compensation: Decimal
    fixed: Decimal
    variable: Decimal
    reimbursement: Decimal
    deductions: Decimal
    net_pay: Decimal
    cash_received: Decimal | None
    receipt_status: ReceiptStatus
    lines: tuple[LineOut, ...]


@dataclass(frozen=True)
class YearOut:
    year: int
    compensation: Decimal
    fixed: Decimal
    variable: Decimal
    variable_share: Decimal | None
    reimbursement: Decimal
    deductions: Decimal
    net_pay: Decimal
    cash_received: Decimal
    payslip_months: int
    gap_months: int
    received_months: int


@dataclass(frozen=True)
class IncrementOut:
    effective_month: date
    previous_fixed: Decimal
    new_fixed: Decimal
    change: Decimal
    change_percent: Decimal | None
    confirmed: bool
    across_gap: bool


@dataclass(frozen=True)
class VariablePayOut:
    payslip_id: UUID
    pay_month: date
    label: str
    amount: Decimal


@dataclass(frozen=True)
class LabelOut:
    label: str
    component: Component
    source: Source
    month_count: int
    first_month: date
    last_month: date
    latest_amount: Decimal


@dataclass(frozen=True)
class CurrencyAnalysis:
    currency: str
    years: tuple[YearOut, ...]
    payslips: tuple[PayslipOut, ...]
    gaps: tuple[date, ...]
    increments: tuple[IncrementOut, ...]
    variable_pay: tuple[VariablePayOut, ...]
    labels: tuple[LabelOut, ...]


def _month_index(on: date) -> int:
    return on.year * 12 + (on.month - 1)


def _month_from_index(index: int) -> date:
    year, month0 = divmod(index, 12)
    return date(year, month0 + 1, 1)


def _months_between(a: date, b: date) -> list[date]:
    """Every calendar month strictly between `a` and `b` (exclusive), `a` earlier than `b`."""
    return [_month_from_index(i) for i in range(_month_index(a) + 1, _month_index(b))]


def _percent(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator * 100).quantize(_PERCENT, rounding=ROUND_HALF_EVEN)


def _label_months(payslips: Sequence[PayslipIn], label: str) -> list[date]:
    return sorted({p.pay_month for p in payslips if any(line.label == label for line in p.lines)})


def _component_for(
    label: str, payslips: Sequence[PayslipIn], rules: Mapping[str, Component]
) -> tuple[Component, Source]:
    if label in rules:
        return rules[label], "rule"
    months = _label_months(payslips, label)
    present = len(months)
    if present == 0:  # defensive: every label passed in appears on at least one line
        return "variable", "inferred"
    span = _month_index(months[-1]) - _month_index(months[0]) + 1
    if present >= _FIXED_MIN_MONTHS and 4 * present >= 3 * span:
        return "fixed", "inferred"
    return "variable", "inferred"


def _analyze_currency(
    currency: str, payslips: Sequence[PayslipIn], rules: Mapping[str, Component]
) -> CurrencyAnalysis:
    labels = sorted({line.label for p in payslips for line in p.lines})
    components = {label: _component_for(label, payslips, rules) for label in labels}

    payslip_outs: list[PayslipOut] = []
    for p in sorted(payslips, key=lambda p: (p.pay_month, p.payslip_id)):
        fixed = variable = reimbursement = Decimal("0.0000")
        line_outs: list[LineOut] = []
        for line in p.lines:
            component, source = components[line.label]
            if component == "fixed":
                fixed += line.amount
            elif component == "variable":
                variable += line.amount
            else:
                reimbursement += line.amount
            line_outs.append(LineOut(line.line_no, line.label, line.amount, component, source))
        compensation = fixed + variable
        if compensation + reimbursement != p.gross:
            raise SalaryReconciliationError(
                f"payslip {p.payslip_id} earning components do not sum to gross earnings"
            )
        if p.gross - p.deductions != p.net:
            raise SalaryReconciliationError(
                f"payslip {p.payslip_id} net pay does not equal gross less deductions"
            )
        payslip_outs.append(
            PayslipOut(
                payslip_id=p.payslip_id,
                pay_month=p.pay_month,
                compensation=compensation,
                fixed=fixed,
                variable=variable,
                reimbursement=reimbursement,
                deductions=p.deductions,
                net_pay=p.net,
                cash_received=p.cash_received if p.receipt_status == "received" else None,
                receipt_status=p.receipt_status,
                lines=tuple(line_outs),
            )
        )

    months_sorted = sorted({p.pay_month for p in payslips})
    gaps: list[date] = []
    for a, b in zip(months_sorted, months_sorted[1:], strict=False):
        gaps.extend(_months_between(a, b))

    month_fixed: dict[date, Decimal] = {}
    month_received: dict[date, bool] = {}
    for month in months_sorted:
        group = [po for po in payslip_outs if po.pay_month == month]
        month_fixed[month] = sum((po.fixed for po in group), Decimal("0.0000"))
        month_received[month] = all(po.receipt_status == "received" for po in group)

    increments: list[IncrementOut] = []
    if months_sorted:
        baseline = months_sorted[0]
        i = 1
        while i < len(months_sorted):
            c = months_sorted[i]
            if month_fixed[c] != month_fixed[baseline]:
                if (
                    i + 1 < len(months_sorted)
                    and month_fixed[months_sorted[i + 1]] == month_fixed[baseline]
                ):
                    # A one-off the next month reverses: baseline unchanged, c not reported.
                    i += 1
                    continue
                change = month_fixed[c] - month_fixed[baseline]
                increments.append(
                    IncrementOut(
                        effective_month=c,
                        previous_fixed=month_fixed[baseline],
                        new_fixed=month_fixed[c],
                        change=change,
                        change_percent=_percent(change, month_fixed[baseline]),
                        confirmed=i + 1 < len(months_sorted),
                        across_gap=_month_index(c) - _month_index(baseline) > 1,
                    )
                )
                baseline = c
            else:
                # A month whose fixed pay equals the baseline's moves the baseline to it, so a
                # later increment's "across a gap" is measured from the nearest matching month.
                baseline = c
            i += 1

    year_numbers = sorted({m.year for m in months_sorted} | {g.year for g in gaps})
    years: list[YearOut] = []
    for year in year_numbers:
        year_payslips = [po for po in payslip_outs if po.pay_month.year == year]
        compensation = sum((po.compensation for po in year_payslips), Decimal("0.0000"))
        fixed = sum((po.fixed for po in year_payslips), Decimal("0.0000"))
        variable = sum((po.variable for po in year_payslips), Decimal("0.0000"))
        reimbursement = sum((po.reimbursement for po in year_payslips), Decimal("0.0000"))
        deductions = sum((po.deductions for po in year_payslips), Decimal("0.0000"))
        net_pay = sum((po.net_pay for po in year_payslips), Decimal("0.0000"))
        cash_received = sum(
            (po.cash_received for po in year_payslips if po.cash_received is not None),
            Decimal("0.0000"),
        )
        year_months = [m for m in months_sorted if m.year == year]
        years.append(
            YearOut(
                year=year,
                compensation=compensation,
                fixed=fixed,
                variable=variable,
                variable_share=_percent(variable, compensation),
                reimbursement=reimbursement,
                deductions=deductions,
                net_pay=net_pay,
                cash_received=cash_received,
                payslip_months=len(year_months),
                gap_months=len([g for g in gaps if g.year == year]),
                received_months=len([m for m in year_months if month_received[m]]),
            )
        )
    years.sort(key=lambda y: y.year, reverse=True)

    variable_pay = [
        VariablePayOut(po.payslip_id, po.pay_month, line.label, line.amount)
        for po in payslip_outs
        for line in po.lines
        if line.component == "variable"
    ]
    variable_pay.sort(key=lambda v: (v.pay_month, v.payslip_id, v.label), reverse=True)

    label_outs: list[LabelOut] = []
    for label in labels:
        months = _label_months(payslips, label)
        component, source = components[label]
        latest_amount = Decimal("0.0000")
        for po in payslip_outs:  # ascending order: the last match is the latest occurrence
            for out_line in po.lines:
                if out_line.label == label:
                    latest_amount = out_line.amount
        label_outs.append(
            LabelOut(
                label=label,
                component=component,
                source=source,
                month_count=len(months),
                first_month=months[0],
                last_month=months[-1],
                latest_amount=latest_amount,
            )
        )

    # Stable sort: payslip id ascending within a tied month, months newest first overall.
    payslips_newest_first = sorted(payslip_outs, key=lambda po: po.payslip_id)
    payslips_newest_first.sort(key=lambda po: po.pay_month, reverse=True)

    return CurrencyAnalysis(
        currency=currency,
        years=tuple(years),
        payslips=tuple(payslips_newest_first),
        gaps=tuple(sorted(gaps)),
        increments=tuple(sorted(increments, key=lambda inc: inc.effective_month, reverse=True)),
        variable_pay=tuple(variable_pay),
        labels=tuple(label_outs),
    )


def analyze_salary(
    payslips: Sequence[PayslipIn], rules: Mapping[str, Component]
) -> list[CurrencyAnalysis]:
    """One `CurrencyAnalysis` per currency present in `payslips`, sorted by currency code."""
    currencies = sorted({p.currency for p in payslips})
    return [
        _analyze_currency(currency, [p for p in payslips if p.currency == currency], rules)
        for currency in currencies
    ]
