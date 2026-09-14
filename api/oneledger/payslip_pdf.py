"""oneledger/payslip_pdf.py — content-selected extraction of monthly pay-advice PDFs.

Pure functions over `Sequence[Page]` (the `ingest.words` boundary); no pdfplumber import here
and no I/O. One layout needs no adapter registry (docs/superpowers/plans/2026-09-14-phase-1-7-
salary-forecast.md Task 1) — add one only when a second layout exists. Implements import rules
1-7 of docs/superpowers/specs/2026-09-14-phase-1-7-salary-forecast.md "Payslip PDF import"; the
`unknown_currency` reason is applied by the caller after checking the currency table (rule 5),
since this module has no database access.

ponytail: a "PAY ADVICE FOR <month> <year>" line only starts a month block when the month name
and year are both well-formed, so the spec's "missing month" incomplete_month cause is
unreachable by construction here (a malformed marker is just ignored text, not a block with no
month). Add real handling if a source ever prints a block marker with an unparseable month.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from .ingest.words import Line, Page, Word

EXTRACTION_VERSION = "1"

PayslipReason = Literal[
    "incomplete_month",
    "unreadable_line",
    "too_many_lines",
    "unknown_currency",
    "payments_total_disagrees",
    "deductions_total_disagrees",
    "net_pay_disagrees",
]
PayslipLineKind = Literal["earning", "deduction"]

_MAX_LINES = 40  # payslip_line_no_is_bounded (V42)
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "JANUARY",
            "FEBRUARY",
            "MARCH",
            "APRIL",
            "MAY",
            "JUNE",
            "JULY",
            "AUGUST",
            "SEPTEMBER",
            "OCTOBER",
            "NOVEMBER",
            "DECEMBER",
        ),
        start=1,
    )
}
_TABLE_HEADER = "PAYMENTS AMOUNT DEDUCTIONS AMOUNT"
_NET_HEADER_WORDS = ("NET", "PAY", "AMOUNT")
_PAY_ADVICE = re.compile(r"PAY ADVICE FOR ([A-Z]+) (\d{4})$")
_CURRENCY_LINE = re.compile(r"^ALL AMOUNTS IN ([A-Z]{3})$")
# The decimal part is required, not optional: a digit-only run (a remark continuation line, a
# reference number) is common table filler in the older layout and must never read as an amount.
_AMOUNT_TOKEN = re.compile(r"^[\d,]+\.\d{1,4}$")


class _RowRefusal(ValueError):
    """Internal marker: this side/row is unreadable. Carries no source-controlled text."""


@dataclass(frozen=True, slots=True)
class PayslipLineDraft:
    kind: PayslipLineKind
    label: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PayslipMonthDraft:
    pay_month: date | None
    currency: str
    gross_earnings: Decimal
    total_deductions: Decimal
    net_pay: Decimal
    lines: tuple[PayslipLineDraft, ...]
    reason: PayslipReason | None


@dataclass(frozen=True, slots=True)
class PayslipExtraction:
    supported: bool
    extraction_version: str
    months: tuple[PayslipMonthDraft, ...]


def _normalized(line: Line) -> str:
    text = " ".join(word.text for word in line.words)
    return " ".join(unicodedata.normalize("NFKC", text).upper().split())


def _month_start(line: Line) -> date | None:
    # A `search`, not a `fullmatch`: the real source prints "CONFIDENTIAL" (and possibly other
    # boilerplate) ahead of the marker on the same or a preceding line. Anchoring only at the end
    # keeps the marker exact while admitting whatever comes before it.
    match = _PAY_ADVICE.search(_normalized(line))
    if match is None:
        return None
    month = _MONTHS.get(match.group(1))
    if month is None:
        return None
    return date(int(match.group(2)), month, 1)


def _table_boundary(line: Line) -> float | None:
    if _normalized(line) != _TABLE_HEADER:
        return None
    for word in line.words:
        if word.text.upper() == "DEDUCTIONS":
            return word.x0
    return None  # unreachable: the text check above already requires the word


def _net_anchor(line: Line) -> float | None:
    """The x0 of the header's own `Net` word, or `None`.

    A suffix match, not a whole-line match: the real source prints this header as the tail of a
    longer row — "Bank - Branch Name Account Number Net Pay Amount" — so bank, branch and account
    columns sit to its left by construction, never parsed or retained (rule 4).
    """
    words = line.words
    if len(words) < 3:
        return None
    tail = tuple(unicodedata.normalize("NFKC", word.text).upper() for word in words[-3:])
    return words[-3].x0 if tail == _NET_HEADER_WORDS else None


def _currency_code(line: Line) -> str | None:
    match = _CURRENCY_LINE.fullmatch(_normalized(line))
    return match.group(1) if match else None


def _parse_amount(token: str) -> Decimal | None:
    if _AMOUNT_TOKEN.fullmatch(token) is None:
        return None
    cleaned = token.replace(",", "")
    if re.fullmatch(r"\d+\.\d{1,4}", cleaned) is None:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _side(words: Sequence[Word], *, allow_zero: bool) -> tuple[str, Decimal] | None:
    """A table side: label then trailing amount, `None` for an empty or amount-less side.

    An older supported layout nests a sub-heading, a lone "-", a wrapped label's remaining
    words, or a remark row (digits, slashes, a name — never stored) inside the table, none of
    which end in an amount-shaped token; these carry no entry and are silently ignored, not
    refused, so reconciliation — not this row — is what stops a real amount going missing.
    Raises `_RowRefusal` when an amount-shaped token appears anywhere but last (misplaced, not
    absent) or the label is out of bounds (rule 2); `allow_zero` is used only for a Total row's
    own sides (rule 3), which may state a zero total, while every other side must be positive,
    as the stored line is.
    """
    if not words:
        return None
    amount = _parse_amount(words[-1].text)
    if amount is None:
        if any(_parse_amount(word.text) is not None for word in words):
            raise _RowRefusal
        return None
    if amount < 0 or (amount == 0 and not allow_zero):
        raise _RowRefusal
    label_words = words[:-1]
    if label_words and label_words[0].text == "*":
        label_words = label_words[1:]  # a leading "*" marks the entry, not part of its label
    label = " ".join(word.text for word in label_words).strip()
    if not 1 <= len(label) <= 80:
        raise _RowRefusal
    return label, amount


@dataclass(slots=True)
class _MonthState:
    boundary: float | None = None
    table_active: bool = False
    table_found: bool = False
    total_found: bool = False
    unreadable: bool = False
    payments_total: Decimal | None = None
    deductions_total: Decimal | None = None
    payment_lines: list[tuple[str, Decimal]] = field(default_factory=list)
    deduction_lines: list[tuple[str, Decimal]] = field(default_factory=list)
    net_anchor: float | None = None
    net_search_active: bool = False
    net_pay: Decimal | None = None
    currency: str | None = None


def _extract_month(pay_month: date, block: Sequence[Line]) -> PayslipMonthDraft:
    s = _MonthState()
    for line in block:
        if not s.table_found:
            boundary = _table_boundary(line)
            if boundary is not None:
                s.boundary, s.table_found, s.table_active = boundary, True, True
                continue
        elif s.table_active:
            if line.words and line.words[0].text.upper() == "TOTAL":
                assert s.boundary is not None
                left = [w for w in line.words if w.x0 < s.boundary]
                right = [w for w in line.words if w.x0 >= s.boundary]
                try:
                    left_side = _side(left, allow_zero=True)
                except _RowRefusal:
                    s.unreadable = True
                else:
                    s.payments_total = None if left_side is None else left_side[1]
                try:
                    right_side = _side(right, allow_zero=True)
                except _RowRefusal:
                    s.unreadable = True
                else:
                    s.deductions_total = None if right_side is None else right_side[1]
                s.table_active, s.total_found = False, True
                continue
            if _table_boundary(line) is not None:
                s.unreadable = True
                continue
            assert s.boundary is not None
            left = [w for w in line.words if w.x0 < s.boundary]
            right = [w for w in line.words if w.x0 >= s.boundary]
            try:
                left_side = _side(left, allow_zero=False)
            except _RowRefusal:
                s.unreadable, left_side = True, None
            try:
                right_side = _side(right, allow_zero=False)
            except _RowRefusal:
                s.unreadable, right_side = True, None
            if left_side is not None:
                s.payment_lines.append(left_side)
            if right_side is not None:
                s.deduction_lines.append(right_side)
            continue

        if s.net_anchor is None:
            anchor = _net_anchor(line)
            if anchor is not None:
                s.net_anchor, s.net_search_active = anchor, True
                continue
        elif s.net_search_active:
            column = [word for word in line.words if word.x0 >= s.net_anchor]
            if column:
                # The last amount-shaped token in the column, not simply its last word: a masked
                # account token never parses as an amount (rule 4), but is never trusted to sit
                # only left of the anchor either.
                amounts = [
                    amount
                    for word in column
                    if (amount := _parse_amount(word.text)) is not None and amount >= 0
                ]
                s.net_pay = amounts[-1] if amounts else None
                s.net_search_active = False
                continue

        if s.currency is None:
            code = _currency_code(line)
            if code is not None:
                s.currency = code

    lines = tuple(
        PayslipLineDraft(kind="earning", label=label, amount=amount)
        for label, amount in s.payment_lines
    ) + tuple(
        PayslipLineDraft(kind="deduction", label=label, amount=amount)
        for label, amount in s.deduction_lines
    )
    gross = s.payments_total if s.payments_total is not None else Decimal(0)
    deductions = s.deductions_total if s.deductions_total is not None else Decimal(0)
    net = s.net_pay if s.net_pay is not None else Decimal(0)

    reason: PayslipReason | None
    if (
        not s.table_found
        or not s.total_found
        or s.payments_total is None
        or s.deductions_total is None
        or s.net_pay is None
        or s.currency is None
    ):
        reason = "incomplete_month"
    elif s.unreadable:
        reason = "unreadable_line"
    elif len(lines) > _MAX_LINES:
        reason = "too_many_lines"
    elif sum((line.amount for line in lines if line.kind == "earning"), Decimal(0)) != gross:
        reason = "payments_total_disagrees"
    elif sum((line.amount for line in lines if line.kind == "deduction"), Decimal(0)) != deductions:
        reason = "deductions_total_disagrees"
    elif gross - deductions != net:
        reason = "net_pay_disagrees"
    else:
        reason = None

    return PayslipMonthDraft(
        pay_month=pay_month,
        currency=s.currency or "",
        gross_earnings=gross,
        total_deductions=deductions,
        net_pay=net,
        lines=lines,
        reason=reason,
    )


def extract_payslips(pages: Sequence[Page]) -> PayslipExtraction:
    """Selected by content only (rule): a month marker, the payments/deductions table header
    and an `All amounts in <code>` line must each appear somewhere in the file."""
    numbered_lines = [line for page in pages for line in page.lines]
    if (
        not any(_month_start(line) is not None for line in numbered_lines)
        or not any(_table_boundary(line) is not None for line in numbered_lines)
        or not any(_currency_code(line) is not None for line in numbered_lines)
    ):
        return PayslipExtraction(supported=False, extraction_version=EXTRACTION_VERSION, months=())

    starts: list[date] = []
    blocks: list[list[Line]] = []
    current: list[Line] | None = None
    current_month: date | None = None
    for line in numbered_lines:
        month = _month_start(line)
        if month is not None:
            if month == current_month:
                # The real source repeats the marker on every page of a block, including its own
                # detail page — a marker naming the block's own month continues it; only a
                # marker naming a different month starts a new one.
                continue
            starts.append(month)
            current = []
            blocks.append(current)
            current_month = month
            continue
        if current is not None:
            current.append(line)

    months = tuple(
        _extract_month(month, block) for month, block in zip(starts, blocks, strict=True)
    )
    return PayslipExtraction(supported=True, extraction_version=EXTRACTION_VERSION, months=months)
