"""M2 Task 4: normalization — what the bank wrote becomes the ledger's shape.

`statement-ingestion.html` section 5 is the contract. Pure functions over values: no database,
no network, no pdfplumber. Every refusal is a named row failure rather than a substituted
value, because absent data announces itself and wrong data does not (Invariant I7).

A failure names the field and the row and never echoes the cell. These reasons are parse
diagnostics and reach an HTTP response, and the plan's section 5.6 forbids any digit sequence
of seven or more digits from the document there — a rule a date alone already trips, before any
card number does. The row number and `raw_row` are how the offending value is found.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Protocol

from .engine import DATE_FORMATS, NUMBER, ExtractionResult, ProfileLike, RawRow
from .words import ExtractionError

CENTS = Decimal("0.0001")
# NUMERIC(20,4) holds sixteen integer digits. A longer run of digits is an OCR artefact, not an
# amount, and it must fail its own row rather than raise out of the parser.
LEDGER_LIMIT = Decimal(10) ** 16
# A two-digit year cannot be windowed against the period the period itself is written in, so a
# period capture is read only in the four-digit formats. Anchoring a century on a guess is the
# error this whole section exists to prevent.
PERIOD_FORMATS = tuple(key for key in DATE_FORMATS if "yyyy" in key)
CREDIT_DEBIT_SUFFIX = re.compile(r"(CR|DR)\s*$", re.IGNORECASE)
GROUPING = re.compile(r",")


class NormalizationError(ExtractionError):
    """A refusal that stops the whole statement, not one row of it."""


class NormalizableProfile(ProfileLike, Protocol):
    """The engine's profile plus the two semantics only normalization reads."""

    @property
    def invertsSign(self) -> bool: ...

    @property
    def chronologicalOrder(self) -> str: ...


@dataclass(frozen=True)
class StatementPeriod:
    start: date
    end: date
    is_derived: bool  # True when no period capture existed and the row dates supplied it


@dataclass(frozen=True)
class SignedAmount:
    """One signed figure, and whether the statement itself stated its direction.

    A debit or credit column and a Dr/Cr suffix each state it; an amount's own sign and a
    balance delta do not — they are written in whichever frame the issuer chose. That
    distinction is what `inverts_sign` may act on, and it is a property of the row rather than
    of the convention alone: `signed` and `cr_suffix` may both be declared with a debit/credit
    pair, and then the columns have spoken. A balance states its direction the same way and
    for the same reason, so `parse_balance` returns this shape too.
    """

    value: Decimal
    direction_is_stated: bool


@dataclass(frozen=True)
class RowFailure:
    source_page_number: int | None
    source_row_number: int
    reason: str


@dataclass(frozen=True)
class NormalizedRow:
    """One statement line in the ledger's shape, before dedupe and before FX."""

    source_page_number: int | None
    source_row_number: int
    raw_row: dict[str, str]
    booked_date: date
    value_date: date | None
    description_raw: str
    description_clean: str
    description_rules_applied: tuple[str, ...]
    reference_raw: str | None
    amount: Decimal
    currency: str
    original_amount: Decimal | None
    original_currency: str | None
    proposed_txn_type: str
    running_balance: Decimal | None
    # Whether `running_balance` was signed by its own Dr/Cr suffix. It travels with the
    # value because reconciliation reframes the statement's balances, and a figure that
    # stated its direction has nothing left for `inverts_sign` to reframe.
    balance_direction_is_stated: bool


@dataclass(frozen=True)
class NormalizedBatch:
    rows: list[NormalizedRow]
    failures: list[RowFailure]
    period: StatementPeriod


def parse_amount(text: str) -> Decimal | None:
    """The statement's number as a Decimal, or None when the cell is not a number.

    The sign comes from a leading `-` or from accounting parentheses. A `CR`/`DR` suffix is
    *not* read here: it means something only under the cr_suffix convention, and reading it
    everywhere would let a stray suffix silently flip a sign under another one.
    """
    candidate = text.strip()
    if not candidate or not NUMBER.fullmatch(candidate):
        return None
    body = CREDIT_DEBIT_SUFFIX.sub("", candidate).strip()
    negative = body.startswith("(") and body.endswith(")")
    body = GROUPING.sub("", body.strip("()").strip())
    try:
        value = Decimal(body)
        if value.copy_abs() >= LEDGER_LIMIT:
            return None
        # The column is NUMERIC(20,4), and Postgres rounds half away from zero. Rounding here,
        # where the row can be named, beats rounding silently inside Postgres.
        value = value.quantize(CENTS, rounding=ROUND_HALF_UP)
        if value.copy_abs() >= LEDGER_LIMIT:
            # Rounding can cross the ceiling the check above just cleared, and a value the
            # column cannot hold must fail its row rather than reach Postgres.
            return None
    except InvalidOperation:
        return None
    return -value if negative else value


def is_credit_suffixed(text: str) -> bool:
    """True when the cell carries a `CR` suffix. Under cr_suffix, everything else is a debit."""
    match = CREDIT_DEBIT_SUFFIX.search(text.strip())
    return match is not None and match.group(1).upper() == "CR"


def frame_balance(balance: SignedAmount, invert: bool) -> Decimal:
    """A balance in the ledger's frame.

    `inverts_sign` reframes a balance that stated no direction of its own; one that did has
    nothing left to reframe, and applying both would reverse the statement rather than reframe
    it. Reconciliation reads this for its anchors and its rows, and `_balance_deltas` reads it
    before subtracting: a difference taken between two frames belongs to neither.
    """
    return -balance.value if invert and not balance.direction_is_stated else balance.value


def parse_balance(text: str) -> tuple[SignedAmount | None, str | None]:
    """A balance signed by its own `Dr`/`Cr` suffix, or the reason there is no balance.

    A balance is a position rather than a movement, so the suffix means one thing wherever it
    appears: `100.00 Dr` is an overdraft and `100.00 Cr` a credit balance, whatever convention
    the amount column follows (`statement-ingestion.html`, "Signs (Invariant I2)": Dr negative, Cr
    positive). `parse_amount` refuses to read the suffix because on an *amount* it means
    something only under `cr_suffix`; that refusal is right there and wrong here, and a running
    balance read a suffix short reports a false break on every overdrawn statement.

    `direction_is_stated` travels with the value for the reason it does on a row's amount: a
    figure that said which way it goes has nothing left for `inverts_sign` to reframe.

    The refusal names itself. A cell that is not a number at all returns `(None, None)`, which
    every caller already has a word for; a cell that *is* a number and contradicts itself
    returns the reason, because telling an owner that `-100.00 Cr` "is not a number" sends them
    looking for the wrong thing in a cell they can read.
    """
    value = parse_amount(text)
    if value is None:
        return None, None
    match = CREDIT_DEBIT_SUFFIX.search(text.strip())
    if match is None:
        return SignedAmount(value, False), None
    credit = match.group(1).upper() == "CR"
    signed = value.copy_abs() if credit else -value.copy_abs()
    if value < 0 and signed != value:
        # `-100.00 Cr` and `(100.00) Cr` state a direction twice and disagree. Every sibling
        # here refuses that rather than picking one — `_from_columns` and `_signed_amount` both
        # fail the row — because resolving it silently is the invisible sign error I2 exists to
        # prevent. The likeliest cause is a band wide enough to have taken the sign from the
        # column beside it, which is why the reason points at the cell rather than the number.
        return None, "states a sign and a Dr/Cr suffix that disagree"
    return SignedAmount(signed, True), None


def _parse_date(value: str, date_format: str, period: StatementPeriod | None) -> date | None:
    """The declared format, applied explicitly. No inference, ever (section 5's first rule)."""
    pattern = DATE_FORMATS.get(date_format)
    if pattern is None:
        return None
    try:
        parsed = datetime.strptime(value.strip(), pattern).date()
    except ValueError:
        return None
    if "%y" not in pattern or period is None:
        return parsed
    return _windowed(parsed, period)


def _windowed(parsed: date, period: StatementPeriod) -> date | None:
    """Two-digit years are windowed against the statement period, not a fixed pivot.

    Python reads `99` as 1999; a 2099 statement says otherwise, and the statement is the
    authority. The period anchors the century — it does not confine the date: a settlement
    date days after the statement closes is ordinary, and resolving by containment alone
    would drop it back to Python's pivot. The neighbouring centuries are candidates too, since
    a December 2099 statement settling on 2 January belongs to 2100.

    The year is chosen first and the date built second, so a date the anchored century cannot
    represent — 29 February in a 2100 statement — is refused rather than filed a century away
    in the one year that happens to be a leap year. The period check still governs
    `booked_date` separately.
    """
    two_digit = parsed.year % 100
    years = {
        (base - base % 100) + offset + two_digit
        for base in (period.start.year, period.end.year)
        for offset in (-100, 0, 100)
    }
    anchored = min(years, key=lambda year: (_year_distance(year, period), year))
    return _replace_year(parsed, anchored)


def _year_distance(year: int, period: StatementPeriod) -> int:
    if period.start.year <= year <= period.end.year:
        return 0
    return min(abs(year - period.start.year), abs(year - period.end.year))


def _replace_year(value: date, year: int) -> date | None:
    try:
        return value.replace(year=year)
    except ValueError:  # 29 February in a year that has none
        return None


def _period_bounds(start_text: str, end_text: str) -> tuple[date, date]:
    """Both bounds, read together under one date format.

    The header period is often written differently from the transaction lines, so the profile's
    own `dateFormat` cannot be required of it. But the two bounds are one statement's single
    statement of its own period: reading each under whichever format happens to fit lets a
    mis-captured fragment that only resembles a date pair with a genuine one and gate every row
    against a period the statement never claimed. Requiring one shared format makes that
    pairing impossible while leaving a differently-written header readable.
    """
    readings = {
        (start, end)
        for date_format in PERIOD_FORMATS
        if (start := _parse_date(start_text, date_format, None)) is not None
        and (end := _parse_date(end_text, date_format, None)) is not None
    }
    if not readings:
        raise NormalizationError(
            "the captured statement period is not a pair of four-digit-year dates written in "
            "one date format"
        )
    if len(readings) > 1:
        raise NormalizationError(
            "the captured statement period reads as a different pair of dates under more than "
            "one date format"
        )
    return readings.pop()


def _period(profile: NormalizableProfile, result: ExtractionResult) -> StatementPeriod | None:
    """The captured period is authoritative; it is what the statement itself states."""
    mapped = set(profile.metadataPatterns or {}) & {"period_start", "period_end"}
    if not mapped:
        return None
    if len(mapped) == 1:
        # Deriving the other bound from the row dates would let a date outside the captured
        # one widen the period and survive — which is the opposite of what capturing it means.
        # LayoutCreate refuses this shape too; the engine does not depend on that refusal.
        raise NormalizationError(
            "the layout captures only one statement period bound; capture both or neither"
        )
    start_text = result.metadata.get("period_start")
    end_text = result.metadata.get("period_end")
    if start_text is None or end_text is None:
        raise NormalizationError(
            "the layout captures the statement period but this statement did not yield it"
        )
    start, end = _period_bounds(start_text, end_text)
    if end < start:
        raise NormalizationError("the captured statement period ends before it starts")
    return StatementPeriod(start=start, end=end, is_derived=False)


def _reads_a_two_digit_year(date_format: str) -> bool:
    pattern = DATE_FORMATS.get(date_format)
    return pattern is not None and "%y" in pattern


def _derived_period(dates: list[date], date_format: str) -> StatementPeriod:
    if not dates:
        # Not a row problem: a statement whose dates never parse is a mis-mapped layout, and
        # without one parsed date there is no period to place the rest of them in either.
        raise NormalizationError(
            f"no row's booked_date parsed as {date_format}, so the statement period cannot "
            "be derived"
        )
    return StatementPeriod(start=min(dates), end=max(dates), is_derived=True)


# Ordered, inspectable, and deliberately not clever: the owner must be able to see exactly what
# was stripped, so each rule is named and only the rules that changed the text are reported.
# Every pattern is anchored at an end, so nothing inside a merchant name is eaten, and each one
# is narrow on purpose — an under-cleaned name is a cosmetic disappointment, while an
# over-cleaned one silently renames the merchant every later match is made against.
DESCRIPTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The terminal number belongs to the prefix, which is why it is consumed with it.
    ("pos_atm_prefix", re.compile(r"^\s*(?:POS|ATM)\b\s*\d*\s*", re.I)),
    (
        # A reference is a number: at least one digit, or the token is part of the name.
        "reference_number",
        re.compile(
            r"\s*\b(?:REF(?:\.?\s*NO)?|RRN|TXN(?:\s*ID)?)\b\.?:?\s*"
            r"(?=[A-Z0-9-]*\d)[A-Z0-9-]{4,}\s*$",
            re.I,
        ),
    ),
    # ddmmyy and ddmmyyyy, or a separated date. Seven digits is not a date, and a trailing
    # store number is not one either.
    ("trailing_date", re.compile(r"\s+(?:\d{6}|\d{8}|\d{2}[/-]\d{2}[/-]\d{2,4})\s*$")),
)
# A country or currency code is enumerated nowhere in this codebase (ADR-0012), so this rule
# cannot recognise one — it can only recognise the *position*: a short uppercase token in the
# tail metadata, which is why it runs only where a date or reference has just been removed
# from beside it. Left to itself it would rename "THE PUB" to "THE".
TRAILING_CODE = re.compile(r"\s+[A-Z]{2,3}\s*$")
TRAILING_CODE_RULE = "trailing_country_or_currency_code"
REPEATED_WHITESPACE = re.compile(r"\s{2,}")


def _apply(pattern: re.Pattern[str], text: str) -> str:
    """Repeat until the pattern stops matching: two trailing codes are still trailing codes."""
    stripped = pattern.sub("", text)
    while stripped != text:
        text = stripped
        stripped = pattern.sub("", text)
    return text


def clean_description(raw: str) -> tuple[str, tuple[str, ...]]:
    """`description_clean` and the rules that produced it. `description_raw` is never touched."""
    text = raw
    applied: list[str] = []
    for name, pattern in DESCRIPTION_RULES:
        stripped = _apply(pattern, text)
        if stripped != text:
            applied.append(name)
            text = stripped
    if {"reference_number", "trailing_date"} & set(applied):
        stripped = _apply(TRAILING_CODE, text)
        if stripped != text:
            applied.append(TRAILING_CODE_RULE)
            text = stripped
    collapsed = REPEATED_WHITESPACE.sub(" ", text).strip()
    if collapsed != text.strip():
        applied.append("repeated_whitespace")
    if not collapsed:
        # The rules found no merchant. An empty label helps nobody, and the raw text is at
        # least true.
        return raw, ()
    return collapsed, tuple(applied)


def _proposed_type(amount: Decimal) -> str:
    if amount > 0:
        return "income"
    if amount < 0:
        return "expense"
    return "adjustment"


def _cell(row: RawRow, field: str) -> str:
    return row.fields.get(field, "").strip()


def _from_columns(convention: str, row: RawRow) -> tuple[SignedAmount | None, str | None]:
    """Debit and credit columns, which state the direction whatever the convention is called."""
    debit, credit = _cell(row, "debit"), _cell(row, "credit")
    if debit and credit:
        return None, "the row states both a debit and a credit"
    cell = debit or credit
    if not cell:
        return None, "the row states neither a debit nor a credit"
    value = parse_amount(cell)
    if value is None:
        return None, f"{'debit' if debit else 'credit'} is not a number"
    if CREDIT_DEBIT_SUFFIX.search(cell):
        return None, (
            f"{'debit' if debit else 'credit'} carries a Dr/Cr suffix under the "
            f"{convention} convention"
        )
    return SignedAmount(-abs(value) if debit else abs(value), True), None


def _signed_amount(
    profile: NormalizableProfile, row: RawRow, delta: SignedAmount | None
) -> tuple[SignedAmount | None, str | None]:
    """The ledger's signed amount for one row, or the reason it has none (Invariant I2)."""
    convention = profile.signConvention
    if convention == "balance_delta":
        if delta is None:
            return None, "running_balance is not a number"
        return delta, None
    if convention == "debit_credit_columns":
        return _from_columns(convention, row)
    cell = _cell(row, "amount")
    if not cell:
        # LayoutCreate accepts signed and cr_suffix with a debit/credit pair instead of an
        # amount column, and the engine counts those cells as an amount when starting a row.
        # A layout the rest of the system accepts must normalize here too.
        if _cell(row, "debit") or _cell(row, "credit"):
            return _from_columns(convention, row)
        return None, "the row states no amount"
    value = parse_amount(cell)
    if value is None:
        return None, "amount is not a number"
    if convention == "cr_suffix":
        # The suffix marks the credit; a bare amount is a debit. Either way the row has said
        # which way it goes.
        return SignedAmount(abs(value) if is_credit_suffixed(cell) else -abs(value), True), None
    if CREDIT_DEBIT_SUFFIX.search(cell):
        # Reading it would be guessing at the convention; ignoring it would flip a sign in
        # silence. The layout says signed, and this cell says otherwise.
        return None, f"amount carries a Dr/Cr suffix under the {convention} convention"
    return SignedAmount(value, False), None


def _balance_deltas(
    profile: NormalizableProfile, result: ExtractionResult, reasons: dict[int, str]
) -> dict[int, SignedAmount | None]:
    """Amounts derived from consecutive running balances, evaluated chronologically.

    `balanceₙ = balanceₙ₋₁ + amountₙ` reads the *previous chronological* row, so a descending
    statement is walked in reverse; the deltas are then returned against document row numbers.

    Each balance is carried into the ledger's frame *before* the subtraction, by the same rule
    reconciliation applies to its anchors. Subtracting first and framing the difference works
    only while every balance in the chain agrees about stating its direction: a statement whose
    opening anchor is captured bare — which an owner's anchor pattern may well do, capturing the
    number without its suffix — and whose column cells carry `Dr` would otherwise have its
    difference taken between two frames and belong to neither. Framed first, the delta is in
    the ledger's frame whatever the mix, and it says so, so nothing reframes it again.
    """
    if profile.signConvention != "balance_delta":
        return {}
    invert = profile.invertsSign
    opening, refusal = parse_balance(result.declared_opening or "")
    if opening is None:
        because = (
            f"its opening balance {refusal}" if refusal else "this statement did not yield one"
        )
        raise NormalizationError(
            f"balance_delta needs the statement's opening balance, and {because} — a first "
            "amount cannot be derived without it"
        )
    ordered = list(result.rows)
    if profile.chronologicalOrder == "descending":
        ordered.reverse()
    deltas: dict[int, SignedAmount | None] = {}
    previous = frame_balance(opening, invert)
    broken_at: int | None = None
    for row in ordered:
        if broken_at is not None:
            # Every later difference would span the unusable row as well as this one, handing
            # this description money that belongs to another — and the batch could still
            # reconcile, because the total stays right and only the attribution is wrong.
            deltas[row.row_number] = None
            reasons[row.row_number] = (
                f"the running-balance chain is broken at row {broken_at}, so this row's "
                "amount cannot be derived"
            )
            continue
        balance, refusal = parse_balance(_cell(row, "running_balance"))
        if balance is None:
            deltas[row.row_number] = None
            if refusal:
                # The cell is a number; saying it is not would send the owner looking for the
                # wrong thing in a cell they can read.
                reasons[row.row_number] = f"running_balance {refusal}"
            broken_at = row.row_number
            continue
        framed = frame_balance(balance, invert)
        delta = framed - previous
        if delta.copy_abs() >= LEDGER_LIMIT:
            # Both balances fit the column; their difference need not. Nothing downstream
            # re-checks an amount the parser did not produce.
            deltas[row.row_number] = None
            reasons[row.row_number] = "the derived amount is outside the ledger's range"
            broken_at = row.row_number
            continue
        deltas[row.row_number] = SignedAmount(delta, True)
        previous = framed
    return deltas


def normalize(
    profile: NormalizableProfile,
    result: ExtractionResult,
    *,
    account_currency: str,
) -> NormalizedBatch:
    """Extracted rows become dated, signed, typed values in the account's currency."""
    period = _period(profile, result)
    if period is None and _reads_a_two_digit_year(profile.dateFormat):
        # There is nothing left to window against. A derived period is made of these same
        # dates, so it cannot correct them: Python reads 99 as 1999, and the derived period
        # then agrees, making a century-early statement look internally consistent. A layout
        # can reach this shape before its period capture is mapped, and a refusal naming what
        # is missing is worth more than a preview that is quietly a hundred years out.
        raise NormalizationError(
            f"{profile.dateFormat} states a two-digit year and this layout captures no "
            "statement period, so the century cannot be anchored"
        )
    # Filled by the balance walk for rows it cannot derive because an earlier balance broke
    # the chain; the generic refusal does not know why they are underivable.
    chain_reasons: dict[int, str] = {}
    deltas = _balance_deltas(profile, result, chain_reasons)
    rows: list[NormalizedRow] = []
    failures: list[RowFailure] = []
    booked: dict[int, date] = {}

    # Two passes: a derived period is made of the row dates, so the dates come first. A
    # captured period is authoritative and is already in hand.
    for row in result.rows:
        parsed = _parse_date(_cell(row, "booked_date"), profile.dateFormat, period)
        if parsed is None:
            failures.append(
                RowFailure(
                    row.page_number,
                    row.row_number,
                    f"booked_date is not a {profile.dateFormat} date",
                )
            )
            continue
        booked[row.row_number] = parsed
    effective = period or _derived_period(list(booked.values()), profile.dateFormat)

    for row in result.rows:
        booked_date = booked.get(row.row_number)
        if booked_date is None:
            continue
        if not (effective.start <= booked_date <= effective.end):
            failures.append(
                RowFailure(
                    row.page_number,
                    row.row_number,
                    "booked_date is outside the statement period",
                )
            )
            continue
        signed, refusal = _signed_amount(profile, row, deltas.get(row.row_number))
        if signed is None:
            reason = chain_reasons.get(row.row_number) or refusal or "no amount"
            failures.append(RowFailure(row.page_number, row.row_number, reason))
            continue
        amount = signed.value
        if profile.invertsSign and not signed.direction_is_stated:
            # Applied last, to the ledger amount, never to the statement's own cells: a card
            # purchase presented as a positive amount is negative in the ledger (Invariant I2).
            # A row that stated its own direction has nothing left to invert, and inverting it
            # would reverse the card instead of reframing it.
            amount = -amount
        description_raw = _cell(row, "description")
        if not description_raw:
            failures.append(
                RowFailure(row.page_number, row.row_number, "the row states no description")
            )
            continue
        value_date_cell = _cell(row, "value_date")
        value_date = (
            _parse_date(value_date_cell, profile.dateFormat, effective) if value_date_cell else None
        )
        if value_date_cell and value_date is None:
            # Leaving it None would make the row indistinguishable from one whose value-date
            # cell was empty, silently discarding what the statement said.
            failures.append(
                RowFailure(
                    row.page_number,
                    row.row_number,
                    f"value_date is not a {profile.dateFormat} date",
                )
            )
            continue
        original_amount_cell = _cell(row, "original_amount")
        original_amount = parse_amount(original_amount_cell) if original_amount_cell else None
        original_currency = _cell(row, "original_currency").upper() or None
        if original_amount_cell and original_amount is None:
            # With both halves absent the row would look exactly like one whose statement
            # stated no foreign amount, discarding the bank's own conversion without a word.
            failures.append(
                RowFailure(
                    row.page_number,
                    row.row_number,
                    "original_amount is not a number",
                )
            )
            continue
        if (original_amount is None) != (original_currency is None):
            failures.append(
                RowFailure(
                    row.page_number,
                    row.row_number,
                    "an original amount and its currency must appear together",
                )
            )
            continue
        if original_amount is not None:
            # Magnitude from the statement, direction from the row it belongs to: 310 leaving
            # the account beside a positive 75 in its original currency would contradict
            # itself permanently, whichever convention or inversion produced the sign.
            original_amount = -abs(original_amount) if amount < 0 else abs(original_amount)
        clean, applied = clean_description(description_raw)
        balance, balance_refusal = parse_balance(_cell(row, "running_balance"))
        if balance_refusal is not None:
            failures.append(
                RowFailure(row.page_number, row.row_number, f"running_balance {balance_refusal}")
            )
            continue
        rows.append(
            NormalizedRow(
                source_page_number=row.page_number,
                source_row_number=row.row_number,
                # The banded cells plus the physical line they came from: whatever fell
                # outside every column exists only in the line, and "what did the statement
                # actually say?" is the question this field exists to answer. `raw_text` is
                # not a mappable column field, so it cannot collide with a cell.
                raw_row={**row.fields, "raw_text": row.raw_text},
                booked_date=booked_date,
                value_date=value_date,
                description_raw=description_raw,
                description_clean=clean,
                description_rules_applied=applied,
                reference_raw=_cell(row, "reference") or None,
                amount=amount,
                currency=account_currency,
                original_amount=original_amount,
                original_currency=original_currency,
                # A mechanical, labelled default shown in review and overridable there: the
                # sign is the only evidence a statement line carries about direction. Zero is
                # neither, and transaction.amount_not_zero permits it under exactly one type,
                # so that is the only one a zero row could ever commit under.
                proposed_txn_type=_proposed_type(amount),
                running_balance=balance.value if balance is not None else None,
                balance_direction_is_stated=balance is not None and balance.direction_is_stated,
            )
        )
    return NormalizedBatch(rows=rows, failures=failures, period=effective)
