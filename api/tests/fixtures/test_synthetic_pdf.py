"""Spike: proves synthetic_pdf.build_pdf() round-trips through pdfplumber with usable
geometry, before anything in oneledger/ingest/ is written against it."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from tests.fixtures.synthetic_pdf import Placement, build_pdf


def test_round_trips_through_pdfplumber(tmp_path: Path) -> None:
    placements = [
        Placement(page=0, x=72, y=700, text="01/02/2024", size=10),
        Placement(page=0, x=200, y=700, text="COFFEE SHOP", size=10),
        Placement(page=0, x=450, y=700, text="12.50", size=10),
    ]
    pdf_path = tmp_path / "spike.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    with pdfplumber.open(pdf_path) as pdf:
        assert len(pdf.pages) == 1
        words = pdf.pages[0].extract_words()

    texts = [w["text"] for w in words]
    assert "01/02/2024" in texts
    assert "12.50" in texts
    # "COFFEE SHOP" has an internal space -> two words, in reading order.
    assert "COFFEE" in texts and "SHOP" in texts

    for w in words:
        assert w["x0"] < w["x1"]
        assert isinstance(w["top"], float)
        # our y=700 in PDF space (origin bottom-left) -> pdfplumber's top is measured from
        # the top of the page, so it should land near page_height - y for the date word.
    date_word = next(w for w in words if w["text"] == "01/02/2024")
    assert abs(date_word["top"] - (792 - 700)) < 15


def test_zero_placement_page_has_zero_words(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(build_pdf([], page_count=1))

    with pdfplumber.open(pdf_path) as pdf:
        assert len(pdf.pages) == 1
        assert pdf.pages[0].extract_words() == []


def test_arabic_range_text_round_trips_through_unicode_mapping(tmp_path: Path) -> None:
    placements = [Placement(page=0, x=72, y=700, text="مرحبا", size=10)]
    pdf_path = tmp_path / "arabic.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    with pdfplumber.open(pdf_path) as pdf:
        words = pdf.pages[0].extract_words()

    texts = [w["text"] for w in words]
    assert texts == ["مرحبا"]
