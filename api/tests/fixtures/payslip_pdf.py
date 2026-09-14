"""Synthetic monthly pay-advice PDFs built on the shared byte fixture.

Layout mirrors the real supported source (structure only — every name, reference and amount
below is synthetic, never real payslip content): every page opens with "CONFIDENTIAL" then a
"PAY ADVICE FOR <month> <year>" marker; a repeated marker naming the same month continues that
block rather than starting a new one, since the real source reprints it on every page including
its own detail page. A side-by-side "Payments Amount Deductions Amount" table ends at a "Total"
row; a "Bank - Branch Name Account Number Net Pay Amount" header is followed by a value row
carrying bank and branch text, a masked account token and a comma-grouped net amount — bank,
branch and account columns sit left of the "Net" anchor and are never parsed. A "Message" line, a
leave-balance sentence containing numbers, and an "All amounts in <code>" line follow. An optional
page-2 detail block repeats the block's own marker, carries an ignored "Element Remarks Amount"
table and a footer line, and must not be read as a second month.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests.fixtures.synthetic_pdf import Placement, build_pdf

# Column x-positions. Each gap is wide enough that no placement's rendered text can drift into
# the next column: a ~19-character masked token starting at ACCOUNT_X (roughly 105pt at size 10)
# still ends well short of NET_X.
PAYMENTS_X = 50.0
DEDUCTIONS_X = 300.0
BRANCH_X = 160.0
ACCOUNT_X = 320.0
NET_X = 480.0
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
    bank_name: str = "Synthetic Bank"
    branch_name: str = "Synthetic Branch"
    masked_account: str = "AE00XXXXXXXXXX0000"
    message: str = "No message this month."
    leave_balance: str = (
        "Annual Leave balance as on 30-Jun-2025 is 12.34 working hours which equates to "
        "1.54 calendar days."
    )
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

    def marker(month: PayslipMonthSpec) -> None:
        line(PAYMENTS_X, "CONFIDENTIAL")
        advance()
        line(PAYMENTS_X, f"PAY ADVICE FOR {month.month_name} {month.year}")
        advance(2)

    for month in months:
        if y < 160.0:  # start the next month clear of the page bottom
            page += 1
            y = 740.0
        marker(month)
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
        line(PAYMENTS_X, "Bank")
        line(PAYMENTS_X + 50.0, "-")
        line(BRANCH_X, "Branch")
        line(BRANCH_X + 60.0, "Name")
        line(ACCOUNT_X, "Account")
        line(ACCOUNT_X + 70.0, "Number")
        line(NET_X, "Net")
        line(NET_X + 40.0, "Pay")
        line(NET_X + 80.0, "Amount")
        advance()
        line(PAYMENTS_X, month.bank_name)
        line(BRANCH_X, month.branch_name)
        line(ACCOUNT_X, month.masked_account)
        line(NET_X, month.net_pay)
        advance(2)
        line(PAYMENTS_X, f"Message {month.message}")
        advance()
        line(PAYMENTS_X, month.leave_balance)
        advance()
        line(PAYMENTS_X, f"All amounts in {month.currency}")
        advance(3)

        if month.include_detail_page:
            page += 1
            y = 740.0
            marker(month)
            line(PAYMENTS_X, "Element Remarks Amount")
            advance()
            line(PAYMENTS_X, "Basic detail-row-not-a-total 12345.00")
            advance(2)
            line(PAYMENTS_X, "This is a system generated document printed by the employee")
            line(PAYMENTS_X + 400.0, "14-Sep-2026 09:00")
            advance()
            line(PAYMENTS_X, "2/2")
            advance()

    return build_pdf(placements, page_count=page + 1, page_size=(612.0, 792.0))
