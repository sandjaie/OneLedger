"""oneledger/ingest/words.py — the only module in this package that imports pdfplumber.

Wraps `page.extract_words()` per page and groups words into physical lines by `top`
(within a tolerance), so `engine.py` can own row-assembly semantics on plain dataclasses
without ever touching pdfplumber itself (plan section 4.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

# The profile's default line-grouping tolerance (plan section 4.4, rule 1). A statement's own
# line pitch is usually 10pt+; this only needs to absorb sub-pixel baseline jitter between
# glyphs placed on the same row.
DEFAULT_LINE_TOLERANCE = 3.0


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


@dataclass(frozen=True)
class Page:
    lines: list[Line]  # top-to-bottom
    has_text: bool  # False when extract_words() returned nothing: the scanned case


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
    line_tolerance: float = DEFAULT_LINE_TOLERANCE,
) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(pdf_path, password=password) as pdf:
        for pdf_page in pdf.pages:
            words = [
                Word(x0=w["x0"], x1=w["x1"], top=w["top"], bottom=w["bottom"], text=w["text"])
                for w in pdf_page.extract_words()
            ]
            pages.append(Page(lines=_group_lines(words, line_tolerance), has_text=bool(words)))
    return pages
