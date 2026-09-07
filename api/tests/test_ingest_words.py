"""TDD for oneledger/ingest/words.py: physical-line grouping by baseline tolerance, and
scanned-page (zero words) detection."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_line_tolerance_scales_with_modal_character_height(tmp_path: Path) -> None:
    # 2pt baseline jitter is distinct at 4pt text size, but 4pt jitter belongs
    # together at 20pt size. A fixed 3pt tolerance gets both cases wrong.
    for size, jitter, expected in [(4, 2, 2), (20, 4, 1)]:
        path = tmp_path / "scaled.pdf"
        path.write_bytes(
            build_pdf(
                [
                    Placement(page=0, x=20, y=700, text="FIRST", size=size),
                    Placement(page=0, x=250, y=700 - jitter, text="SECOND", size=size),
                ]
            )
        )
        assert len(extract_pages(path)[0].lines) == expected


def test_parser_programmer_errors_are_not_treated_as_upload_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException

    def broken(*args: object, **kwargs: object) -> None:
        raise PdfminerException(TypeError("programmer error"))

    monkeypatch.setattr(pdfplumber, "open", broken)
    with pytest.raises(PdfminerException):
        extract_pages(tmp_path / "unused.pdf")


@pytest.mark.parametrize("space", [False, True])
def test_raw_text_uses_the_same_first_anchor_membership_as_words(space: bool) -> None:
    from oneledger.ingest.words import Page, Word, _group_lines, with_raw_text

    a = Word(x0=0, x1=5, top=0, bottom=10, text="A")
    b = Word(x0=20, x1=25, top=2.9, bottom=12.9, text="B")
    c = Word(x0=5, x1=10, top=3.1, bottom=13.1, text="C")
    characters = [a, b, c]
    if space:
        characters.append(Word(x0=10, x1=15, top=2.9, bottom=12.9, text=" "))
    lines = _group_lines([a, b, c], tolerance=3)
    result = with_raw_text(
        lines, Page(lines=lines, has_text=True, characters=characters, line_tolerance=3)
    )

    assert [[word.text for word in line.words] for line in result] == [["A", "B"], ["C"]]
    assert [line.raw_text for line in result] == ["A B" if space else "AB", "C"]
