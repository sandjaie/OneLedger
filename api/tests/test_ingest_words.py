"""TDD for oneledger/ingest/words.py: physical-line grouping by baseline tolerance, and
scanned-page (zero words) detection."""

from __future__ import annotations

from pathlib import Path

from oneledger.ingest.words import extract_pages
from tests.fixtures.synthetic_pdf import Placement, build_pdf


def test_two_baselines_within_tolerance_group_into_one_line(tmp_path: Path) -> None:
    placements = [
        Placement(page=0, x=72, y=700.0, text="ONE", size=10),
        Placement(page=0, x=200, y=698.5, text="TWO", size=10),  # 1.5pt off: within tolerance
    ]
    pdf_path = tmp_path / "same_line.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    pages = extract_pages(pdf_path)

    assert len(pages) == 1
    assert pages[0].has_text is True
    assert len(pages[0].lines) == 1
    assert [w.text for w in pages[0].lines[0].words] == ["ONE", "TWO"]


def test_baselines_beyond_tolerance_split_into_two_lines(tmp_path: Path) -> None:
    placements = [
        Placement(page=0, x=72, y=700.0, text="ONE", size=10),
        Placement(page=0, x=72, y=670.0, text="TWO", size=10),  # 30pt lower: a new line
    ]
    pdf_path = tmp_path / "two_lines.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    pages = extract_pages(pdf_path)

    assert len(pages[0].lines) == 2
    assert pages[0].lines[0].words[0].text == "ONE"
    assert pages[0].lines[1].words[0].text == "TWO"
    # top-to-bottom order: ONE sits higher on the page (bigger PDF y), so its pdfplumber
    # `top` (measured down from the page top) is the smaller one.
    assert pages[0].lines[0].top < pages[0].lines[1].top


def test_words_within_a_line_are_left_to_right(tmp_path: Path) -> None:
    placements = [
        Placement(page=0, x=450, y=700.0, text="LAST", size=10),
        Placement(page=0, x=72, y=700.0, text="FIRST", size=10),
    ]
    pdf_path = tmp_path / "order.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    pages = extract_pages(pdf_path)

    assert [w.text for w in pages[0].lines[0].words] == ["FIRST", "LAST"]


def test_scanned_page_has_zero_words(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(build_pdf([], page_count=1))

    pages = extract_pages(pdf_path)

    assert len(pages) == 1
    assert pages[0].has_text is False
    assert pages[0].lines == []


def test_multiple_pages_are_extracted_independently(tmp_path: Path) -> None:
    placements = [
        Placement(page=0, x=72, y=700.0, text="PAGE1", size=10),
        Placement(page=1, x=72, y=700.0, text="PAGE2", size=10),
    ]
    pdf_path = tmp_path / "multi.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    pages = extract_pages(pdf_path)

    assert len(pages) == 2
    assert pages[0].lines[0].words[0].text == "PAGE1"
    assert pages[1].lines[0].words[0].text == "PAGE2"
