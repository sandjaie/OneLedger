"""Capability-neutral detailed investment statement extraction."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ..identifiers import (
    CompleteIdentifier,
    Identifier,
    IdentifierValueError,
    identifier_from_source,
)
from ..ingest.words import Line, Page
from .adapters import (
    EvidenceDraft,
    InvestmentEventDraft,
    InvestmentEventKind,
    PositionCheckpointDraft,
    SafeReason,
    SourceEventIdentity,
    SourceTarget,
    StatementExtraction,
    StatementGroup,
)

SOURCE_CONTRACT = "detailed_fund_statement_v1"
EXTRACTION_VERSION = "1"
FINGERPRINT_VERSION = "typed_event_v1"
_TITLE = "DETAILED INVESTMENT STATEMENT V1"
_HEADERS: tuple[str, ...] = (
    "DATE",
    "EVENT",
    "UNITS",
    "PRICE",
    "GROSS",
    "CHARGES",
    "TAX",
    "NET",
    "CURRENCY",
    "REFERENCE",
)
_POSITION_HEADERS: tuple[str, ...] = (
    "AS_OF",
    "UNITS",
    "COST",
    "VALUE",
    "CURRENCY",
    "NAV",
    "REFERENCE",
)
_KINDS: dict[str, InvestmentEventKind] = {
    "PURCHASE": "purchase",
    "RECURRING_PURCHASE": "recurring_purchase",
    "SALE": "sale",
    "REDEMPTION": "redemption",
    "MATURITY": "maturity",
    "CASH_DISTRIBUTION": "cash_distribution",
    "INTEREST": "interest",
    "REINVESTED_DISTRIBUTION": "reinvested_distribution",
    "CHARGE": "charge",
    "WITHHELD_TAX": "withheld_tax",
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class _RowRefusal(ValueError):
    """Internal marker with no source-controlled message or value."""


@dataclass(slots=True)
class _GroupBuilder:
    target: SourceTarget
    drafts: list[InvestmentEventDraft] = field(default_factory=list)
    checkpoints: list[PositionCheckpointDraft] = field(default_factory=list)
    reasons: list[SafeReason] = field(default_factory=list)


def _line_text(line: Line) -> str:
    return " ".join(word.text for word in line.words).strip()


def _normalized_text(line: Line) -> str:
    return " ".join(unicodedata.normalize("NFKC", _line_text(line)).upper().split())


def _anchors(line: Line, headers: tuple[str, ...]) -> tuple[float, ...] | None:
    by_name = {word.text.upper(): word.x0 for word in line.words}
    if any(header not in by_name for header in headers):
        return None
    anchors = tuple(by_name[header] for header in headers)
    return anchors if tuple(sorted(anchors)) == anchors else None


def _header_anchors(line: Line) -> tuple[float, ...] | None:
    return _anchors(line, _HEADERS)


def _position_anchors(line: Line) -> tuple[float, ...] | None:
    return _anchors(line, _POSITION_HEADERS)


def _cells(
    line: Line, anchors: tuple[float, ...], headers: tuple[str, ...] = _HEADERS
) -> dict[str, str]:
    boundaries = tuple(
        (left + right) / 2 for left, right in zip(anchors, anchors[1:], strict=False)
    )
    cells: list[list[str]] = [[] for _ in anchors]
    for word in line.words:
        midpoint = (word.x0 + word.x1) / 2
        cells[bisect_right(boundaries, midpoint)].append(word.text)
    return {heading: " ".join(parts).strip() for heading, parts in zip(headers, cells, strict=True)}


def _decimal(value: str, *, integer_digits: int, scale: int, allow_negative: bool) -> Decimal:
    sign = "-?" if allow_negative else ""
    if re.fullmatch(rf"{sign}\d{{1,{integer_digits}}}(?:\.\d{{1,{scale}}})?", value) is None:
        raise _RowRefusal
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _RowRefusal from None
    if not parsed.is_finite():
        raise _RowRefusal
    return parsed


def _optional_decimal(
    value: str, *, integer_digits: int, scale: int, allow_negative: bool
) -> Decimal | None:
    return (
        None
        if not value
        else _decimal(
            value, integer_digits=integer_digits, scale=scale, allow_negative=allow_negative
        )
    )


def _event_date(value: str) -> date:
    try:
        if _DATE.fullmatch(value) is None:
            raise ValueError
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise _RowRefusal from None


def _canonical(value: Decimal | None) -> str:
    if value is None:
        return "<null>"
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _target_key(identifier: Identifier) -> str:
    if isinstance(identifier, CompleteIdentifier):
        return f"{identifier.kind}:complete:{identifier.normalized_value}"
    return f"{identifier.kind}:masked:{identifier.visible_suffix}"


def _security_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def event_fingerprint(target: SourceTarget, draft: InvestmentEventDraft) -> str:
    values = (
        SOURCE_CONTRACT,
        _target_key(target.account_identifier),
        _security_key(target.security_reference),
        draft.event_date.isoformat(),
        draft.event_kind,
        _canonical(draft.units),
        _canonical(draft.price),
        _canonical(draft.gross_amount),
        _canonical(draft.charges),
        _canonical(draft.withheld_tax),
        _canonical(draft.net_amount),
        draft.currency,
    )
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _checkpoint_fingerprint(
    target: SourceTarget, checkpoint: PositionCheckpointDraft, statement_nav: Decimal | None
) -> str:
    values = (
        SOURCE_CONTRACT,
        _target_key(target.account_identifier),
        _security_key(target.security_reference),
        "checkpoint",
        checkpoint.as_of_date.isoformat(),
        _canonical(checkpoint.reported_units),
        _canonical(checkpoint.reported_cost_native),
        _canonical(checkpoint.reported_value_native),
        checkpoint.currency or "<null>",
        str(checkpoint.declared_unit_scale),
        _canonical(statement_nav),
    )
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _target(account_line: str, security_line: str) -> SourceTarget:
    account = account_line.split(maxsplit=3)
    if (
        len(account) != 4
        or account[0].upper() != "ACCOUNT"
        or account[2].upper() not in {"MASKED", "COMPLETE"}
    ):
        raise _RowRefusal
    try:
        identifier = identifier_from_source(
            account[1].lower(), account[3], source_is_masked=account[2].upper() == "MASKED"
        )
    except IdentifierValueError:
        raise _RowRefusal from None
    security_parts = security_line.split(maxsplit=1)
    security = security_parts[1].strip() if len(security_parts) == 2 else ""
    if (
        not security_parts
        or security_parts[0].upper() != "SECURITY"
        or not security
        or len(security) > 128
        or any(unicodedata.category(character).startswith("C") for character in security)
    ):
        raise _RowRefusal
    return SourceTarget(account_identifier=identifier, security_reference=security)


def _draft(
    values: dict[str, str], *, source_order: int, page_number: int, row_number: int
) -> InvestmentEventDraft:
    try:
        kind = _KINDS[values["EVENT"]]
    except KeyError:
        raise _RowRefusal from None
    units = _optional_decimal(values["UNITS"], integer_digits=20, scale=8, allow_negative=True)
    price = _optional_decimal(values["PRICE"], integer_digits=14, scale=6, allow_negative=False)
    gross = _optional_decimal(values["GROSS"], integer_digits=16, scale=4, allow_negative=False)
    charges = _decimal(values["CHARGES"], integer_digits=16, scale=4, allow_negative=False)
    tax = _decimal(values["TAX"], integer_digits=16, scale=4, allow_negative=False)
    net = _decimal(values["NET"], integer_digits=16, scale=4, allow_negative=True)
    currency = values["CURRENCY"]
    if (
        _CURRENCY.fullmatch(currency) is None
        or (price is not None and price <= 0)
        or (gross is not None and gross <= 0)
        or charges < 0
        or tax < 0
    ):
        raise _RowRefusal
    acquisition = kind in {"purchase", "recurring_purchase"}
    if acquisition:
        if units is None or units <= 0 or price is None or gross is None:
            raise _RowRefusal
        valid = net == -(gross + charges + tax)
    elif kind in {"sale", "redemption", "maturity"}:
        if units is None or units >= 0 or price is None or gross is None:
            raise _RowRefusal
        valid = net == gross - charges - tax and net > 0
    elif kind in {"cash_distribution", "interest"}:
        if units is not None or price is not None or gross is None:
            raise _RowRefusal
        valid = net == gross - charges - tax and net > 0
    elif kind == "reinvested_distribution":
        valid = (
            units is not None
            and units > 0
            and price is not None
            and gross is not None
            and charges == 0
            and net == 0
        )
    elif kind == "charge":
        valid = (
            units is None
            and price is None
            and gross is None
            and charges > 0
            and tax == 0
            and net == -charges
        )
    else:
        valid = (
            units is None
            and price is None
            and gross is None
            and charges == 0
            and tax > 0
            and net == -tax
        )
    if not valid:
        raise _RowRefusal
    try:
        source_event_id = SourceEventIdentity(values["REFERENCE"]) if values["REFERENCE"] else None
    except ValueError:
        raise _RowRefusal from None
    evidence = EvidenceDraft(
        page_number=page_number,
        row_number=row_number,
        source_event_id=source_event_id,
        fingerprint_version=FINGERPRINT_VERSION,
        fingerprint="",
    )
    draft = InvestmentEventDraft(
        source_order=source_order,
        event_date=_event_date(values["DATE"]),
        event_kind=kind,
        units=units,
        unit_ratio=None,
        price=price,
        gross_amount=gross,
        charges=charges,
        withheld_tax=tax,
        net_amount=net,
        currency=currency,
        evidence=evidence,
    )
    return draft


def _checkpoint(
    values: dict[str, str],
    target: SourceTarget,
    *,
    source_order: int,
    page_number: int,
    row_number: int,
) -> PositionCheckpointDraft:
    units = _optional_decimal(values["UNITS"], integer_digits=20, scale=8, allow_negative=False)
    cost = _optional_decimal(values["COST"], integer_digits=16, scale=4, allow_negative=False)
    value = _optional_decimal(values["VALUE"], integer_digits=16, scale=4, allow_negative=False)
    nav = _optional_decimal(values["NAV"], integer_digits=14, scale=6, allow_negative=False)
    currency = values["CURRENCY"] or None
    if (
        units is cost is value is None
        or any(item is not None and item < 0 for item in (units, cost, value))
        or (nav is not None and nav <= 0)
        or (currency is not None and _CURRENCY.fullmatch(currency) is None)
        or ((cost is not None or value is not None) and currency is None)
    ):
        raise _RowRefusal
    try:
        source_id = SourceEventIdentity(values["REFERENCE"]) if values["REFERENCE"] else None
    except ValueError:
        raise _RowRefusal from None
    scale = len(values["UNITS"].partition(".")[2]) if units is not None else None
    evidence = EvidenceDraft(page_number, row_number, source_id, "typed_checkpoint_v1", "")
    checkpoint = PositionCheckpointDraft(
        source_order,
        _event_date(values["AS_OF"]),
        units,
        cost,
        value,
        currency,
        scale,
        evidence,
    )
    return PositionCheckpointDraft(
        source_order,
        checkpoint.as_of_date,
        units,
        cost,
        value,
        currency,
        scale,
        EvidenceDraft(
            page_number,
            row_number,
            source_id,
            "typed_checkpoint_v1",
            _checkpoint_fingerprint(target, checkpoint, nav),
        ),
    )


@dataclass(frozen=True, slots=True)
class DetailedFundStatementAdapter:
    source_contract: str = SOURCE_CONTRACT
    extraction_version: str = EXTRACTION_VERSION

    def matches(self, pages: Sequence[Page]) -> bool:
        lines = tuple(line for page in pages for line in page.lines)
        return any(_normalized_text(line) == _TITLE for line in lines) and any(
            _header_anchors(line) is not None or _position_anchors(line) is not None
            for line in lines
        )

    def extract(self, pages: Sequence[Page]) -> StatementExtraction:
        builders: dict[tuple[str, str], _GroupBuilder] = {}
        result_reasons: list[SafeReason] = []
        current_account: str | None = None
        current_security: str | None = None
        target_ready = False
        anchors: tuple[float, ...] | None = None
        headers: tuple[str, ...] = _HEADERS
        position = False
        source_order = 0
        for page_number, page in enumerate(pages, start=1):
            for row_number, line in enumerate(page.lines, start=1):
                source_text = _line_text(line)
                text = _normalized_text(line)
                if text.startswith("ACCOUNT "):
                    if target_ready:
                        current_security = None
                    current_account = source_text
                    target_ready = current_security is not None
                    continue
                if text.startswith("SECURITY "):
                    if target_ready:
                        current_account = None
                    current_security = source_text
                    target_ready = current_account is not None
                    continue
                header = _header_anchors(line)
                if header is not None:
                    anchors = header
                    headers = _HEADERS
                    position = False
                    continue
                header = _position_anchors(line)
                if header is not None:
                    anchors = header
                    headers = _POSITION_HEADERS
                    position = True
                    continue
                if (
                    anchors is None
                    or not line.words
                    or not text
                    or text in {_TITLE, "TRANSACTIONS", "END POSITION"}
                ):
                    continue
                values = _cells(line, anchors, headers)
                date_column = "AS_OF" if position else "DATE"
                if not values[date_column]:
                    continue
                source_order += 1
                if current_account is None or current_security is None:
                    result_reasons.append(SafeReason("ambiguous_account", page_number, row_number))
                    continue
                try:
                    target = _target(current_account, current_security)
                except _RowRefusal:
                    result_reasons.append(
                        SafeReason("ambiguous_identifier", page_number, row_number)
                    )
                    continue
                key = (
                    _target_key(target.account_identifier),
                    _security_key(target.security_reference),
                )
                builder = builders.setdefault(key, _GroupBuilder(target=target))
                if position:
                    try:
                        builder.checkpoints.append(
                            _checkpoint(
                                values,
                                target,
                                source_order=source_order,
                                page_number=page_number,
                                row_number=row_number,
                            )
                        )
                    except _RowRefusal:
                        builder.reasons.append(
                            SafeReason("unrecognized_event", page_number, row_number)
                        )
                    continue
                try:
                    parsed = _draft(
                        values,
                        source_order=source_order,
                        page_number=page_number,
                        row_number=row_number,
                    )
                except _RowRefusal:
                    builder.reasons.append(
                        SafeReason("unrecognized_event", page_number, row_number)
                    )
                    continue
                builder.drafts.append(
                    InvestmentEventDraft(
                        source_order=parsed.source_order,
                        event_date=parsed.event_date,
                        event_kind=parsed.event_kind,
                        units=parsed.units,
                        unit_ratio=parsed.unit_ratio,
                        price=parsed.price,
                        gross_amount=parsed.gross_amount,
                        charges=parsed.charges,
                        withheld_tax=parsed.withheld_tax,
                        net_amount=parsed.net_amount,
                        currency=parsed.currency,
                        evidence=EvidenceDraft(
                            page_number=parsed.evidence.page_number,
                            row_number=parsed.evidence.row_number,
                            source_event_id=parsed.evidence.source_event_id,
                            fingerprint_version=FINGERPRINT_VERSION,
                            fingerprint=event_fingerprint(target, parsed),
                        ),
                    )
                )
        groups = tuple(
            StatementGroup(
                group_number=index,
                target=builder.target,
                drafts=tuple(builder.drafts),
                checkpoints=tuple(builder.checkpoints),
                reasons=tuple(builder.reasons),
            )
            for index, builder in enumerate(builders.values(), start=1)
        )
        return StatementExtraction(
            supported=True,
            source_contract=self.source_contract,
            extraction_version=self.extraction_version,
            groups=groups,
            reasons=tuple(result_reasons),
        )
