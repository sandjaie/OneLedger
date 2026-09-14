"""The official NAV text-publication parser: exact Decimal prices, malformed rows skipped."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from oneledger.investments.price_adapters import NavRecord, _price, parse_official_nav_text_v1

_PUBLICATION = """Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;\
Net Asset Value;Date

Open Ended Schemes(Synthetic Category)

100001;INF000A01011;INF000A01029;Synthetic Growth Fund - Growth;123.456700;01-Sep-2026
100002;-;-;Synthetic Growth Fund - IDCW;45.670000;01-Sep-2026
100003;INF000A01037;-;Malformed Price Row;N.A.;01-Sep-2026
100004;INF000A01045;-;Malformed Date Row;10.0000;not-a-date
abcde;INF000A01052;-;Non-Numeric Scheme Code;10.0000;01-Sep-2026
100005;INF000A01060;-;Missing Fields Row;10.0000
100006;INF000A01078;-;Zero Price Row;0.000000;01-Sep-2026
100007;INF000A01086;-;Seven Decimals Row;12.3456789;01-Sep-2026
100008;INF000A01094;-;Too Many Integer Digits Row;123456789012345.000000;01-Sep-2026
"""


def test_parses_exact_decimal_prices_and_dates() -> None:
    records = parse_official_nav_text_v1(_PUBLICATION)

    assert (
        NavRecord(
            scheme_code="100001",
            isin_payout="INF000A01011",
            isin_reinvestment="INF000A01029",
            scheme_name="Synthetic Growth Fund - Growth",
            price=Decimal("123.456700"),
            price_date=date(2026, 9, 1),
        )
        in records
    )
    assert (
        NavRecord(
            scheme_code="100002",
            isin_payout=None,
            isin_reinvestment=None,
            scheme_name="Synthetic Growth Fund - IDCW",
            price=Decimal("45.670000"),
            price_date=date(2026, 9, 1),
        )
        in records
    )


def test_skips_na_unparsable_and_non_positive_rows() -> None:
    records = parse_official_nav_text_v1(_PUBLICATION)
    codes = {record.scheme_code for record in records}

    assert "100003" not in codes  # N.A. price
    assert "100004" not in codes  # unparsable date
    assert "abcde" not in codes  # non-numeric scheme code — not a real row, not skipped silently
    assert "100005" not in codes  # wrong field count
    assert "100006" not in codes  # zero price is not a valid observation
    assert "100007" not in codes  # seven decimal places would round under NUMERIC(20,6)
    assert "100008" not in codes  # more integer digits than NUMERIC(20,6) can hold
    assert codes == {"100001", "100002"}


def test_a_price_outside_numeric_20_6_is_skipped_not_rounded() -> None:
    """Rounding here would make an identical later re-fetch of the true value look like a
    conflicting observation — see price_snapshot's uq_price_snapshot_observation."""
    assert _price("12.3456789") is None  # 7 decimal places
    assert _price("123456789012345.000000") is None  # 15 integer digits
    assert _price("12345678901234.000000") == Decimal("12345678901234.000000")  # exactly 14 fits
    assert _price("1.000000") == Decimal("1.000000")  # exactly 6 decimals fits


def test_category_and_header_lines_are_not_data_rows() -> None:
    records = parse_official_nav_text_v1(_PUBLICATION)

    assert all(record.scheme_name != "Scheme Name" for record in records)
