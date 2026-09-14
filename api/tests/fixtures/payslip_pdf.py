"""Synthetic monthly pay-advice PDFs built on the shared byte fixture.

Layout mirrors the spec's supported source: a "PAY ADVICE FOR <month> <year>" marker, a
side-by-side "Payments Amount Deductions Amount" table ending at a "Total" row, a
"Net Pay Amount" header and value line (with bank/branch/account text to its left, ignored), an
"All amounts in <code>" line, and an optional page-2 "Element Remarks Amount" detail block that
must be ignored. All labels, references and amounts are synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.fixtures.synthetic_pdf import Placement, build_pdf

# Column x-positions: the payments side starts at PAYMENTS_X, the deductions side (and the
# header's own "Deductions" word) at DEDUCTIONS_X, and the net-pay value column at NET_X — each
# gap is wide enough that no placement's rendered text can drift into the next column.
PAYMENTS_X = 50.0
DEDUCTIONS_X = 300.0
NET_X = 420.0
_ROW_HEIGHT = 24.0


@dataclass(frozen=True, slots=True)
class PayslipMonthSpec:
    month_name: str  # e.g. "JANUARY", matched case-insensitively against a real month name
    year: int
    currency: str
    payment_lines: list[tuple[str, str]] = field(default_factory=list)  # (label, amount text)
    deduction_lines: list[tuple[str, str]] = field(default_factory=list)
    payments_total: str = "0.00"
    deductions_total: str = "0.00"
    net_pay: str = "0.00"
    bank_detail: str = "A1B2C3D4E5F6 Synthetic Branch 000111222"
    include_detail_page: bool = False


def build_payslip_pdf(months: list[PayslipMonthSpec]) -> bytes:
    placements: list[Placement] = []
    page = 0
    y = 740.0

    def line(x: float, text: str) -> None:
        nonlocal y
        placements.append(Placement(page=page, x=x, y=y, text=text, size=10.0))

    def advance(rows: int = 1) -> None:
        nonlocal y
        y -= _ROW_HEIGHT * rows

    for month in months:
        if y < 120.0:  # start the next month clear of the page bottom
            page += 1
            y = 740.0
        line(PAYMENTS_X, f"PAY ADVICE FOR {month.month_name} {month.year}")
        advance(2)
        line(PAYMENTS_X, "Payments")
        line(PAYMENTS_X + 100.0, "Amount")
        line(DEDUCTIONS_X, "Deductions")
        line(DEDUCTIONS_X + 100.0, "Amount")
        advance()
        row_count = max(len(month.payment_lines), len(month.deduction_lines))
        for index in range(row_count):
            if index < len(month.payment_lines):
                label, amount = month.payment_lines[index]
                line(PAYMENTS_X, f"{label} {amount}")
            if index < len(month.deduction_lines):
                label, amount = month.deduction_lines[index]
                line(DEDUCTIONS_X, f"{label} {amount}")
            advance()
        line(PAYMENTS_X, f"Total {month.payments_total}")
        line(DEDUCTIONS_X, f"Total {month.deductions_total}")
        advance(2)
        line(NET_X, "Net")
        line(NET_X + 70.0, "Pay")
        line(NET_X + 140.0, "Amount")
        advance()
        line(PAYMENTS_X, month.bank_detail)
        line(NET_X, month.net_pay)
        advance(2)
        line(PAYMENTS_X, f"All amounts in {month.currency}")
        advance(3)

        if month.include_detail_page:
            page += 1
            y = 740.0
            line(PAYMENTS_X, "Element Remarks Amount")
            advance()
            line(PAYMENTS_X, "Basic detail-row-not-a-total 12345.00")
            advance(2)

    return build_pdf(placements, page_count=page + 1, page_size=(612.0, 792.0))
