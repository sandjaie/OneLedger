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

from .words import ExtractionError, Line, Page, Word, _group_lines, with_raw_text
from .words import extract_pages as read_pdf_pages


class ColumnLike(Protocol):
    @property
    def field(self) -> str: ...

    @property
    def xMin(self) -> float: ...

    @property
    def xMax(self) -> float: ...


class ProfileLike(Protocol):
    @property
    def fingerprints(self) -> Sequence[str]: ...

    @property
    def columns(self) -> Sequence[ColumnLike]: ...

    @property
    def dateFormat(self) -> str: ...

    @property
    def signConvention(self) -> str: ...

    @property
    def rowStartRule(self) -> str: ...

    @property
    def continuationRule(self) -> str | None: ...

    @property
    def skipLinePatterns(self) -> Sequence[str]: ...

    @property
    def openingBalancePattern(self) -> str | None: ...

    @property
    def closingBalancePattern(self) -> str | None: ...

    @property
    def quirks(self) -> Sequence[str]: ...

    @property
    def metadataPatterns(self) -> dict[str, str] | None: ...


DATE_FORMATS = {
    "dd/MM/yyyy": "%d/%m/%Y",
    "dd-MM-yyyy": "%d-%m-%Y",
    "dd MMM yyyy": "%d %b %Y",
    "dd/MM/yy": "%d/%m/%y",
    "yyyy-MM-dd": "%Y-%m-%d",
}
# Generic statement vocabulary only. Institution-specific names belong in saved profile data,
# never in the engine (ADR-0012).
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "booked_date": (
        "date",
        "tran date",
        "transaction date",
        "txn date",
        "value date",
        "posting date",
    ),
    "description": ("description", "narration", "particulars", "details", "remarks"),
    "reference": ("ref", "ref no", "reference", "chq no", "cheque no", "chq./ref.no."),
    "amount": ("amount",),
    "debit": ("debit", "withdrawal", "withdrawal amt", "dr", "debits"),
    "credit": ("credit", "deposit", "deposit amt", "cr", "credits"),
    "running_balance": ("balance", "closing balance", "running balance"),
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
class LayoutColumnDraft:
    field: str
    xMin: float
    xMax: float
    label: str | None = None


@dataclass(frozen=True)
class LayoutDraft:
    """Unsaved layout proposal using the public layout API's field names."""

    name: str
    institutionId: None
    fingerprints: list[str]
    headerRowPattern: str
    columns: list[LayoutColumnDraft]
    dateFormat: str
    signConvention: str
    invertsSign: bool
    chronologicalOrder: str
    rowStartRule: str
    continuationRule: str | None
    skipLinePatterns: list[str]
    openingBalancePattern: None
    closingBalancePattern: None
    hasRunningBalance: bool
    quirks: list[str]
    metadataPatterns: None


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
        line.raw_text if line.raw_text is not None else " ".join(word.text for word in line.words)
        for page in pages
        for line in page.lines
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
    return with_raw_text(_group_lines(kept, page.line_tolerance), page), len(words) - len(kept)


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
            assembled_text = " ".join(
                word.text for word in sorted(line.words, key=lambda item: item.x0)
            )
            raw_text = line.raw_text if line.raw_text is not None else assembled_text
            if any(re.search(pattern, assembled_text) for pattern in profile.skipLinePatterns):
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
        raise ExtractionError("scanned PDFs are unsupported")
    return result


@dataclass(frozen=True)
class _HeaderMatch:
    field: str
    x0: float
    x1: float
    label: str


def _header_key(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _equivalent_header_pattern(value: str) -> str:
    separator = r"[\W_]*"
    return f"(?i)^{separator.join(re.escape(character) for character in _header_key(value))}$"


def _line_header_matches(line: Line) -> list[_HeaderMatch]:
    words = sorted(line.words, key=lambda word: word.x0)
    matches: list[_HeaderMatch] = []
    for field_name, synonyms in HEADER_SYNONYMS.items():
        candidates: list[tuple[int, int, int, str]] = []
        normalized_synonyms = {_header_key(synonym): synonym for synonym in synonyms}
        for start in range(len(words)):
            for end in range(start + 1, min(len(words), start + 3) + 1):
                key = _header_key(" ".join(word.text for word in words[start:end]))
                synonym = normalized_synonyms.get(key)
                if synonym is not None:
                    candidates.append((end - start, start, end, synonym))
        if not candidates:
            continue
        _, start, end, synonym = max(candidates, key=lambda item: (item[0], len(item[3]), -item[1]))
        matches.append(
            _HeaderMatch(
                field=field_name,
                x0=words[start].x0,
                x1=words[end - 1].x1,
                label=" ".join(word.text for word in words[start:end]),
            )
        )
    return sorted(matches, key=lambda match: match.x0)


def _find_header(pages: list[Page]) -> tuple[int, int, list[_HeaderMatch]]:
    candidates: list[tuple[int, int, list[_HeaderMatch]]] = []
    amount_fields = {"amount", "debit", "credit"}
    for page_index, page in enumerate(pages):
        for line_index, line in enumerate(page.lines):
            matches = _line_header_matches(line)
            fields = {match.field for match in matches}
            if "booked_date" in fields and fields & amount_fields:
                candidates.append((page_index, line_index, matches))
    if not candidates:
        raise ExtractionError("no qualifying header found")
    best_score = max(len(matches) for _, _, matches in candidates)
    best = [candidate for candidate in candidates if len(candidate[2]) == best_score]
    signatures = {
        tuple((match.field, match.x0, match.x1, _header_key(match.label)) for match in candidate[2])
        for candidate in best
    }
    if len(signatures) != 1:
        raise ExtractionError("ambiguous header rows")
    return best[0]


def _draft_columns(page: Page, matches: list[_HeaderMatch]) -> list[LayoutColumnDraft]:
    page_words = [word for line in page.lines for word in line.words]
    left_margin = min(word.x0 for word in page_words)
    right_margin = max(word.x1 for word in page_words)
    columns: list[LayoutColumnDraft] = []
    for index, match in enumerate(matches):
        x_min = left_margin if index == 0 else (match.x0 + matches[index - 1].x1) / 2
        x_max = (
            right_margin if index == len(matches) - 1 else (match.x1 + matches[index + 1].x0) / 2
        )
        columns.append(
            LayoutColumnDraft(field=match.field, xMin=x_min, xMax=x_max, label=match.label)
        )
    return columns


DATE_SAMPLE = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})|\d{1,2}-\d{1,2}-\d{4}|"
    r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}|\d{4}-\d{1,2}-\d{1,2})$"
)


def _is_transaction_sample(cells: dict[str, str]) -> bool:
    booked_date = cells.get("booked_date", "").strip()
    has_amount = any(_is_number(cells.get(field, "")) for field in ("amount", "debit", "credit"))
    return bool(DATE_SAMPLE.fullmatch(booked_date) and has_amount)


def _sample_cells(
    pages: list[Page],
    header_page: int,
    header_line: int,
    columns: list[LayoutColumnDraft],
    header_text: str,
) -> list[dict[str, str]]:
    sampled: list[dict[str, str]] = []
    header_key = _header_key(header_text)
    for page_index, page in enumerate(pages):
        for line_index, line in enumerate(page.lines):
            if page_index < header_page or (
                page_index == header_page and line_index <= header_line
            ):
                continue
            line_text = " ".join(word.text for word in sorted(line.words, key=lambda word: word.x0))
            if _header_key(line_text) == header_key:
                continue
            cells = _cells(columns, line.words)
            if _is_transaction_sample(cells):
                sampled.append(cells)
    return sampled


def _infer_date_format(date_samples: list[str]) -> str:
    candidates = [
        date_format
        for date_format in DATE_FORMATS
        if date_samples and all(_date(value, date_format) is not None for value in date_samples)
    ]
    if len(candidates) != 1:
        raise ExtractionError("date format is ambiguous")
    if candidates[0] in {"dd/MM/yyyy", "dd/MM/yy", "dd-MM-yyyy"}:
        numeric_parts = [
            re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-]\d{2,4}", value.strip())
            for value in date_samples
        ]
        if all(
            match is not None and int(match.group(1)) <= 12 and int(match.group(2)) <= 12
            for match in numeric_parts
        ):
            # Month-first is intentionally not an output format, but it is still a competing
            # interpretation. A proposal must not silently relabel those dates as day-first.
            raise ExtractionError("date format is ambiguous")
    return candidates[0]


def _infer_order(date_samples: list[str], date_format: str) -> str:
    dates = [_date(value, date_format) for value in date_samples]
    parsed = [value for value in dates if value is not None]
    if len(parsed) < 2 or len(set(parsed)) < 2:
        raise ExtractionError("chronological order is ambiguous")
    ascending = all(left <= right for left, right in zip(parsed, parsed[1:], strict=False))
    descending = all(left >= right for left, right in zip(parsed, parsed[1:], strict=False))
    if ascending == descending:
        raise ExtractionError("chronological order is ambiguous")
    return "ascending" if ascending else "descending"


def _safe_fingerprints(page: Page, header_line: int) -> list[str]:
    fingerprints: list[str] = []
    for line_index, line in enumerate(page.lines):
        if line_index == header_line:
            continue
        text = " ".join(word.text for word in sorted(line.words, key=lambda word: word.x0)).strip()
        normalized = re.sub(r"(?<=\d)[\s\-–./]+(?=\d)", "", text)
        if not text or re.search(r"\d{4,}", normalized) or not any(c.isalpha() for c in text):
            continue
        fingerprint = re.escape(text)
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)
        if len(fingerprints) == 3:
            break
    return fingerprints


def propose(pdf_path: Path, password: str | None) -> tuple[LayoutDraft, list[RawRow]]:
    """Propose a generic layout draft from an unrecognised statement."""
    pages = read_pdf_pages(pdf_path, password=password)
    if not pages or any(not page.has_text for page in pages):
        raise ExtractionError("scanned PDFs are unsupported")
    page_index, line_index, header_matches = _find_header(pages)
    header_line = pages[page_index].lines[line_index]
    header_text = " ".join(
        word.text for word in sorted(header_line.words, key=lambda word: word.x0)
    )
    columns = _draft_columns(pages[page_index], header_matches)
    cells = _sample_cells(pages, page_index, line_index, columns, header_text)
    date_samples = [cell["booked_date"] for cell in cells if "booked_date" in cell]
    date_format = _infer_date_format(date_samples)
    fields = {column.field for column in columns}
    if {"debit", "credit"} <= fields:
        sign_convention = "debit_credit_columns"
    else:
        amount_samples = [cell.get("amount", "") for cell in cells]
        sign_convention = (
            "cr_suffix"
            if any(re.search(r"(?:CR|DR)\s*$", value, re.IGNORECASE) for value in amount_samples)
            else "signed"
        )
    header_pattern = _equivalent_header_pattern(header_text)
    draft = LayoutDraft(
        name="Proposed layout",
        institutionId=None,
        fingerprints=_safe_fingerprints(pages[0], line_index if page_index == 0 else -1),
        headerRowPattern=header_pattern,
        columns=columns,
        dateFormat=date_format,
        signConvention=sign_convention,
        invertsSign=False,
        chronologicalOrder=_infer_order(date_samples, date_format),
        rowStartRule="has_date_and_amount",
        continuationRule=None,
        skipLinePatterns=[header_pattern],
        openingBalancePattern=None,
        closingBalancePattern=None,
        hasRunningBalance="running_balance" in fields,
        quirks=[],
        metadataPatterns=None,
    )
    return draft, extract_pages(draft, pages).rows[:20]
