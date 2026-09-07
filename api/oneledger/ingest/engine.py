"""Profile-driven statement extraction semantics.

`words.py` is the package's pdfplumber boundary. This module operates on plain word/page
dataclasses so every geometry and row-assembly rule remains independently testable.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from .words import DEFAULT_LINE_TOLERANCE, Line, Page, Word, _group_lines
from .words import extract_pages as read_pdf_pages


class ColumnLike(Protocol):
    field: str
    xMin: float
    xMax: float


class ProfileLike(Protocol):
    fingerprints: list[str]

    @property
    def columns(self) -> Sequence[ColumnLike]: ...

    dateFormat: str
    signConvention: str
    rowStartRule: str
    continuationRule: str | None
    skipLinePatterns: list[str]
    openingBalancePattern: str | None
    closingBalancePattern: str | None
    quirks: list[str]
    metadataPatterns: dict[str, str] | None


DATE_FORMATS = {
    "dd/MM/yyyy": "%d/%m/%Y",
    "dd-MM-yyyy": "%d-%m-%Y",
    "dd MMM yyyy": "%d %b %Y",
    "dd/MM/yy": "%d/%m/%y",
    "yyyy-MM-dd": "%Y-%m-%d",
}
ARABIC = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
NUMBER_BODY = r"\d+(?:,\d+)*(?:\.\d+)?"
NUMBER = re.compile(
    rf"^(?:[-+]?{NUMBER_BODY}|\({NUMBER_BODY}\))(?:\s*(?:CR|DR))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RawRow:
    page_number: int
    row_number: int
    raw_text: str
    fields: dict[str, str]


@dataclass(frozen=True)
class ExtractionResult:
    rows: list[RawRow]
    declared_opening: str | None
    declared_closing: str | None
    metadata: dict[str, str]
    account_number_raw: str | None = field(repr=False)
    page_count: int
    has_text: bool
    words_filtered: int
    lines_unparsed: int


def matches(profile: ProfileLike, page_one_text: str, filename: str) -> float:
    """Return fingerprint evidence only; a filename is never evidence by itself."""
    del filename
    if not profile.fingerprints:
        return 0.0
    hits = sum(
        bool(re.search(pattern, page_one_text, re.IGNORECASE)) for pattern in profile.fingerprints
    )
    return min(1.0, max(0.0, hits / len(profile.fingerprints)))


def _date(value: str, date_format: str) -> date | None:
    python_format = DATE_FORMATS.get(date_format)
    if python_format is None or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), python_format).date()
    except ValueError:
        return None


def _is_number(value: str) -> bool:
    return bool(value.strip() and NUMBER.fullmatch(value.strip()))


def _page_text(pages: list[Page]) -> str:
    return "\n".join(
        " ".join(word.text for word in line.words) for page in pages for line in page.lines
    )


def _capture(pattern: str | None, text: str) -> str | None:
    if pattern is None:
        return None
    matched = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if matched is None:
        return None
    return matched.group(1) if matched.re.groups else matched.group(0)


def _filtered_lines(page: Page, filter_arabic: bool) -> tuple[list[Line], int]:
    if not filter_arabic:
        return page.lines, 0
    words = [word for line in page.lines for word in line.words]
    kept = [word for word in words if ARABIC.search(word.text) is None]
    # Regroup after filtering: a removed word must not determine the line anchor.
    return _group_lines(kept, DEFAULT_LINE_TOLERANCE), len(words) - len(kept)


def _cells(columns: Sequence[ColumnLike], words: list[Word]) -> dict[str, str]:
    assigned: dict[str, list[Word]] = {column.field: [] for column in columns}
    for word in words:
        midpoint = (word.x0 + word.x1) / 2
        for column in columns:
            if column.xMin <= midpoint < column.xMax:
                assigned[column.field].append(word)
                break
    return {
        field: " ".join(word.text for word in sorted(cell, key=lambda item: item.x0))
        for field, cell in assigned.items()
        if cell
    }


def _has_amount(profile: ProfileLike, fields: dict[str, str]) -> bool:
    amount_fields = {
        "balance_delta": ("running_balance",),
        "debit_credit_columns": ("debit", "credit"),
    }.get(profile.signConvention, ("amount",))
    return any(_is_number(fields.get(field, "")) for field in amount_fields)


def _starts_row(profile: ProfileLike, fields: dict[str, str]) -> bool:
    has_date = _date(fields.get("booked_date", ""), profile.dateFormat) is not None
    if profile.rowStartRule == "has_date":
        return has_date
    return has_date and _has_amount(profile, fields)


def extract_pages(profile: ProfileLike, pages: list[Page]) -> ExtractionResult:
    """Apply a profile to already extracted page words.

    This is deliberately public: quirks and band semantics can be tested without a PDF parser.
    """
    source_text = _page_text(pages)
    metadata: dict[str, str] = {}
    account_number_raw: str | None = None
    for key, pattern in (profile.metadataPatterns or {}).items():
        captured = _capture(pattern, source_text)
        if captured is None:
            continue
        if key == "account_number":
            account_number_raw = captured
        else:
            metadata[key] = captured

    filter_arabic = "filter_script:arabic" in profile.quirks
    continue_description = (
        profile.continuationRule == "no_date_and_no_amount"
        or "continuation:no_date_and_no_amount" in profile.quirks
    )
    rows: list[RawRow] = []
    words_filtered = 0
    lines_unparsed = 0

    for page_number, page in enumerate(pages, start=1):
        lines, dropped = _filtered_lines(page, filter_arabic)
        words_filtered += dropped
        for line in lines:
            if not line.words:
                continue
            raw_text = " ".join(word.text for word in sorted(line.words, key=lambda item: item.x0))
            if any(re.search(pattern, raw_text) for pattern in profile.skipLinePatterns):
                continue
            fields = _cells(profile.columns, line.words)
            if _starts_row(profile, fields):
                rows.append(
                    RawRow(
                        page_number=page_number,
                        row_number=len(rows) + 1,
                        raw_text=raw_text,
                        fields=fields,
                    )
                )
                continue
            has_date = _date(fields.get("booked_date", ""), profile.dateFormat) is not None
            has_amount = _has_amount(profile, fields)
            description = fields.get("description", "").strip()
            if continue_description and rows and not has_date and not has_amount and description:
                previous = rows[-1]
                joined_fields = dict(previous.fields)
                old_description = joined_fields.get("description", "")
                joined_fields["description"] = " ".join(
                    part for part in (old_description, description) if part
                )
                rows[-1] = RawRow(
                    page_number=previous.page_number,
                    row_number=previous.row_number,
                    raw_text=f"{previous.raw_text}\n{raw_text}",
                    fields=joined_fields,
                )
            else:
                lines_unparsed += 1

    return ExtractionResult(
        rows=rows,
        declared_opening=_capture(profile.openingBalancePattern, source_text),
        declared_closing=_capture(profile.closingBalancePattern, source_text),
        metadata=metadata,
        account_number_raw=account_number_raw,
        page_count=len(pages),
        has_text=bool(pages) and all(page.has_text for page in pages),
        words_filtered=words_filtered,
        lines_unparsed=lines_unparsed,
    )


def extract(profile: ProfileLike, pdf_path: Path, password: str | None) -> ExtractionResult:
    pages = read_pdf_pages(pdf_path, password=password)
    result = extract_pages(profile, pages)
    if not result.has_text:
        raise ValueError("scanned PDFs are unsupported")
    return result
