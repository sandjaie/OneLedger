"""oneledger/ingest/words.py — the only module in this package that imports pdfplumber.

Wraps `page.extract_words()` per page and groups words into physical lines by `top`
(within a tolerance), so `engine.py` can own row-assembly semantics on plain dataclasses
without ever touching pdfplumber itself (plan section 4.4).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import PSEOF, PSSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

# The profile's default line-grouping tolerance (plan section 4.4, rule 1). A statement's own
# line pitch is usually 10pt+; this only needs to absorb sub-pixel baseline jitter between
# glyphs placed on the same row.
DEFAULT_LINE_TOLERANCE = 3.0


class ExtractionError(ValueError):
    """Expected, sanitized extraction refusal suitable for an API response."""


@dataclass(frozen=True)
class Word:
    x0: float
    x1: float
    top: float
    bottom: float
    text: str


@dataclass(frozen=True)
class Line:
    top: float  # the line's anchor top (its first-grouped word's top)
    words: list[Word]  # left-to-right by x0
    raw_text: str | None = None


@dataclass(frozen=True)
class Page:
    lines: list[Line]  # top-to-bottom
    has_text: bool  # False when extract_words() returned nothing: the scanned case
    characters: list[Word] = field(default_factory=list)
    line_tolerance: float = DEFAULT_LINE_TOLERANCE


def _group_lines(words: list[Word], tolerance: float) -> list[Line]:
    """Group words sharing a baseline into physical lines. Each line is anchored to the
    `top` of the first (topmost) word assigned to it; a later word joins that line when its
    own `top` is within `tolerance` of the anchor, so two words on one physical line always
    stay within tolerance of each other by construction — not of a running average that could
    drift line by line across a whole paragraph."""
    ordered = sorted(words, key=lambda w: (w.top, w.x0))
    groups: list[list[Word]] = []
    anchors: list[float] = []
    for w in ordered:
        for i, anchor in enumerate(anchors):
            if abs(w.top - anchor) <= tolerance:
                groups[i].append(w)
                break
        else:
            groups.append([w])
            anchors.append(w.top)
    return [
        Line(top=anchors[i], words=sorted(group, key=lambda w: w.x0))
        for i, group in enumerate(groups)
    ]


def extract_pages(
    pdf_path: Path,
    password: str | None = None,
    line_tolerance: float | None = None,
) -> list[Page]:
    try:
        return _extract_pages(pdf_path, password, line_tolerance)
    except PdfminerException as exc:
        # Current pdfplumber wraps PDFMiner errors (including programmer errors).
        # Only documented parse/encryption failures are an expected upload refusal.
        cause = exc.args[0] if exc.args else exc.__context__
        if isinstance(cause, PDFEncryptionError):
            raise ExtractionError("PDF is encrypted or the password is incorrect") from None
        if isinstance(cause, (PDFSyntaxError, PSEOF, PSSyntaxError)):
            raise ExtractionError("PDF is malformed or unsupported") from None
        raise
    except PDFEncryptionError:
        raise ExtractionError("PDF is encrypted or the password is incorrect") from None
    except PDFSyntaxError, PSEOF, PSSyntaxError:
        raise ExtractionError("PDF is malformed or unsupported") from None


def _extract_pages(
    pdf_path: Path, password: str | None, line_tolerance: float | None
) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(pdf_path, password=password) as pdf:
        for pdf_page in pdf.pages:
            characters = [
                Word(x0=c["x0"], x1=c["x1"], top=c["top"], bottom=c["bottom"], text=c["text"])
                for c in pdf_page.chars
            ]
            tolerance = (
                line_tolerance if line_tolerance is not None else _line_tolerance(characters)
            )
            words = [
                Word(x0=w["x0"], x1=w["x1"], top=w["top"], bottom=w["bottom"], text=w["text"])
                for w in pdf_page.extract_words(y_tolerance=tolerance)
            ]
            page = Page(
                lines=[], has_text=bool(words), characters=characters, line_tolerance=tolerance
            )
            pages.append(
                Page(
                    lines=with_raw_text(_group_lines(words, tolerance), page),
                    has_text=page.has_text,
                    characters=characters,
                    line_tolerance=tolerance,
                )
            )
    return pages


def _line_tolerance(characters: list[Word]) -> float:
    heights = Counter(
        round(c.bottom - c.top, 1)
        for c in characters
        if c.text.strip() and isfinite(c.bottom - c.top) and c.bottom > c.top
    )
    if not heights:
        return DEFAULT_LINE_TOLERANCE
    ranked = heights.most_common()
    if ranked[0][1] < 2 or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
        return DEFAULT_LINE_TOLERANCE
    return ranked[0][0] * 0.3


def with_raw_text(lines: list[Line], page: Page) -> list[Line]:
    """Attach original characters independently of the words retained for cell assembly."""
    source = page.characters or [word for line in page.lines for word in line.words]
    grouped: list[list[Word]] = [[] for _ in lines]
    for character in source:
        if not lines:
            break
        index = min(range(len(lines)), key=lambda i: abs(character.top - lines[i].top))
        if abs(character.top - lines[index].top) <= page.line_tolerance:
            grouped[index].append(character)
    return [
        Line(
            top=line.top,
            words=line.words,
            raw_text=("" if page.characters else " ").join(
                c.text for c in sorted(chars, key=lambda c: c.x0)
            ),
        )
        for line, chars in zip(lines, grouped, strict=True)
    ]
