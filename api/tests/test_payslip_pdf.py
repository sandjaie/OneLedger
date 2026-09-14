"""Pure payslip PDF extraction contracts for Phase 1.7 Task 1."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from oneledger.ingest.words import Page, extract_pages
from oneledger.payslip_pdf import extract_payslips
from tests.fixtures.payslip_pdf import PayslipMonthSpec, build_payslip_pdf
from tests.fixtures.synthetic_pdf import Placement, build_pdf


def _pages(tmp_path: Path, months: list[PayslipMonthSpec]) -> list[Page]:
    path = tmp_path / "synthetic-payslip.pdf"
    path.write_bytes(build_payslip_pdf(months))
    return extract_pages(path)


def _month(**changes: object) -> PayslipMonthSpec:
    values: dict[str, object] = {
        "month_name": "JANUARY",
        "year": 2026,
        "currency": "USD",
        "payment_lines": [("Basic", "3000.00"), ("Housing Allowance", "500.00")],
        "deduction_lines": [("Tax", "400.00")],
        "payments_total": "3500.00",
        "deductions_total": "400.00",
        "net_pay": "3100.00",
    }
    values.update(changes)
    return PayslipMonthSpec(**values)  # type: ignore[arg-type]


def test_three_month_file_with_detail_page_and_one_mismatch(tmp_path: Path) -> None:
    months = [
        _month(include_detail_page=True),
        _month(
            month_name="FEBRUARY",
            payment_lines=[("Basic", "3000.00")],
            deduction_lines=[("Tax", "400.00")],
            payments_total="2900.00",  # deliberately wrong: payments_total_disagrees
            deductions_total="400.00",
            net_pay="2500.00",
        ),
        _month(
            month_name="MARCH",
            payment_lines=[("Basic", "3200.00")],
            deduction_lines=[("Tax", "450.00")],
            payments_total="3200.00",
            deductions_total="450.00",
            net_pay="2750.00",
        ),
    ]
    extraction = extract_payslips(_pages(tmp_path, months))
    assert extraction.supported
    assert [m.reason for m in extraction.months] == [None, "payments_total_disagrees", None]

    january = extraction.months[0]
    assert january.pay_month is not None and january.pay_month.isoformat() == "2026-01-01"
    assert january.currency == "USD"
    assert january.gross_earnings == Decimal("3500.00")
    assert january.total_deductions == Decimal("400.00")
    assert january.net_pay == Decimal("3100.00")
    assert [(line.kind, line.label, line.amount) for line in january.lines] == [
        ("earning", "Basic", Decimal("3000.00")),
        ("earning", "Housing Allowance", Decimal("500.00")),
        ("deduction", "Tax", Decimal("400.00")),
    ]

    march = extraction.months[2]
    assert march.pay_month is not None and march.pay_month.isoformat() == "2026-03-01"
    assert march.reason is None


def test_reason_priority_covers_each_reconciliation_failure(tmp_path: Path) -> None:
    deductions_disagree = extract_payslips(
        _pages(
            tmp_path,
            [
                _month(
                    deduction_lines=[("Tax", "300.00")],  # sums to 300, stated total 400
                )
            ],
        )
    ).months[0]
    assert deductions_disagree.reason == "deductions_total_disagrees"

    net_pay_disagrees = extract_payslips(
        _pages(tmp_path, [_month(net_pay="3000.00")])  # 3500 - 400 = 3100, not 3000
    ).months[0]
    assert net_pay_disagrees.reason == "net_pay_disagrees"

    too_many_lines = extract_payslips(
        _pages(
            tmp_path,
            [
                _month(
                    payment_lines=[(f"Earning {i}", "10.00") for i in range(41)],
                    deduction_lines=[],
                    payments_total="410.00",
                    deductions_total="0.00",
                    net_pay="410.00",
                )
            ],
        )
    ).months[0]
    assert too_many_lines.reason == "too_many_lines"


def test_incomplete_month_when_a_required_element_is_missing(tmp_path: Path) -> None:
    # A month block missing its Total row (nothing after "Payments Amount Deductions Amount")
    # never resolves a stated total, so it reconciles nothing and is incomplete.
    placements = [
        Placement(page=0, x=50, y=740, text="PAY ADVICE FOR JANUARY 2026"),
        Placement(page=0, x=50, y=700, text="Payments"),
        Placement(page=0, x=150, y=700, text="Amount"),
        Placement(page=0, x=300, y=700, text="Deductions"),
        Placement(page=0, x=400, y=700, text="Amount"),
        Placement(page=0, x=50, y=680, text="Basic 3000.00"),
        Placement(page=0, x=50, y=600, text="All amounts in USD"),
    ]
    path = tmp_path / "incomplete.pdf"
    path.write_bytes(build_pdf(placements, page_count=1))
    extraction = extract_payslips(extract_pages(path))
    assert extraction.supported
    assert extraction.months[0].reason == "incomplete_month"


def test_net_pay_reads_past_the_bank_branch_and_account_columns(tmp_path: Path) -> None:
    # The real source's net-pay header is the tail of a longer row, and its value row's masked
    # account token must never be mistaken for the net-pay amount (OL-001).
    month = _month(
        net_pay="3,100.00",
        masked_account="AE00XXXXXXXXXX0000",
        bank_name="Synthetic Bank",
        branch_name="Synthetic Branch",
    )
    extraction = extract_payslips(_pages(tmp_path, [month]))
    assert extraction.supported
    assert extraction.months[0].reason is None
    assert extraction.months[0].net_pay == Decimal("3100.00")


def test_a_repeated_marker_for_the_same_month_continues_its_block(tmp_path: Path) -> None:
    # The real source reprints "CONFIDENTIAL" then the marker on every page of a block,
    # including its own detail page; only a marker naming a different month starts a new block
    # (OL-002).
    month = _month(include_detail_page=True)
    extraction = extract_payslips(_pages(tmp_path, [month]))
    assert extraction.supported
    assert len(extraction.months) == 1
    assert extraction.months[0].reason is None
    assert [(line.kind, line.label, line.amount) for line in extraction.months[0].lines] == [
        ("earning", "Basic", Decimal("3000.00")),
        ("earning", "Housing Allowance", Decimal("500.00")),
        ("deduction", "Tax", Decimal("400.00")),
    ]


def test_a_payment_only_row_reconciles_beside_shorter_deduction_lines(tmp_path: Path) -> None:
    month = _month(
        payment_lines=[("Basic", "3000.00"), ("Housing Allowance", "500.00"), ("Bonus", "200.00")],
        deduction_lines=[("Tax", "400.00")],
        payments_total="3700.00",
        deductions_total="400.00",
        net_pay="3300.00",
    )
    extraction = extract_payslips(_pages(tmp_path, [month]))
    assert extraction.months[0].reason is None
    assert [line.label for line in extraction.months[0].lines] == [
        "Basic",
        "Housing Allowance",
        "Bonus",
        "Tax",
    ]


def test_a_non_matching_pdf_is_unsupported(tmp_path: Path) -> None:
    placements = [Placement(page=0, x=50, y=700, text="Not a payslip at all")]
    path = tmp_path / "unrelated.pdf"
    path.write_bytes(build_pdf(placements, page_count=1))
    extraction = extract_payslips(extract_pages(path))
    assert extraction.supported is False
    assert extraction.months == ()
