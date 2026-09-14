"""Capability-neutral summary investment statement extraction."""

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
    IdentifierValueError,
    identifier_from_source,
)
from ..ingest.words import Line, Page
from .adapters import (
    ISO_DATE,
    EvidenceDraft,
    PositionCheckpointDraft,
    SafeReason,
    SourceEventIdentity,
    SourceTarget,
    StatementExtraction,
    StatementGroup,
    canonical_decimal,
    identifier_key,
    line_text,
    normalized_line_text,
    security_key,
)

SOURCE_CONTRACT = "summary_fund_statement_v1"
EXTRACTION_VERSION = "1"
FINGERPRINT_VERSION = "typed_summary_checkpoint_v1"
_TITLE = "SUMMARY INVESTMENT STATEMENT V1"
_HEADERS = ("AS_OF", "UNITS", "COST", "VALUE", "CURRENCY", "REFERENCE")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class _RowRefusal(ValueError):
    """Internal refusal whose message never contains source evidence."""


class _SecurityRefusal(_RowRefusal):
    """Internal marker for an unusable security target."""


@dataclass(slots=True)
class _GroupBuilder:
    target: SourceTarget
    checkpoints: list[PositionCheckpointDraft] = field(default_factory=list)
    reasons: list[SafeReason] = field(default_factory=list)


def _anchors(line: Line) -> tuple[float, ...] | None:
    by_name = {word.text.upper(): word.x0 for word in line.words}
    if any(header not in by_name for header in _HEADERS):
        return None
    anchors = tuple(by_name[header] for header in _HEADERS)
    return anchors if anchors == tuple(sorted(anchors)) else None


def _cells(line: Line, anchors: tuple[float, ...]) -> dict[str, str]:
    boundaries = tuple(
        (left + right) / 2 for left, right in zip(anchors, anchors[1:], strict=False)
    )
    cells: list[list[str]] = [[] for _ in anchors]
    for word in line.words:
        midpoint = (word.x0 + word.x1) / 2
        cells[bisect_right(boundaries, midpoint)].append(word.text)
    return {
        heading: " ".join(parts).strip() for heading, parts in zip(_HEADERS, cells, strict=True)
    }


def _decimal(value: str, *, integer_digits: int, scale: int) -> Decimal | None:
    if not value:
        return None
    if re.fullmatch(rf"\d{{1,{integer_digits}}}(?:\.\d{{1,{scale}}})?", value) is None:
        raise _RowRefusal
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise _RowRefusal from None
    if not parsed.is_finite():
        raise _RowRefusal
    return parsed


def _as_of_date(value: str) -> date:
    try:
        if ISO_DATE.fullmatch(value) is None:
            raise ValueError
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise _RowRefusal from None


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
        raise _SecurityRefusal
    return SourceTarget(identifier, security)


def _fingerprint(target: SourceTarget, checkpoint: PositionCheckpointDraft) -> str:
    values = (
        SOURCE_CONTRACT,
        identifier_key(target.account_identifier),
        security_key(target.security_reference),
        checkpoint.as_of_date.isoformat(),
        canonical_decimal(checkpoint.reported_units),
        canonical_decimal(checkpoint.reported_cost_native),
        canonical_decimal(checkpoint.reported_value_native),
        checkpoint.currency or "<null>",
        str(checkpoint.declared_unit_scale),
    )
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _checkpoint(
    values: dict[str, str],
    target: SourceTarget,
    *,
    source_order: int,
    page_number: int,
    row_number: int,
) -> PositionCheckpointDraft:
    units = _decimal(values["UNITS"], integer_digits=20, scale=8)
    cost = _decimal(values["COST"], integer_digits=16, scale=4)
    value = _decimal(values["VALUE"], integer_digits=16, scale=4)
    currency = values["CURRENCY"] or None
    if (
        units is cost is value is None
        or (currency is not None and _CURRENCY.fullmatch(currency) is None)
        or ((cost is not None or value is not None) and currency is None)
    ):
        raise _RowRefusal
    try:
        source_id = SourceEventIdentity(values["REFERENCE"]) if values["REFERENCE"] else None
    except ValueError:
        raise _RowRefusal from None
    scale = len(values["UNITS"].partition(".")[2]) if units is not None else None
    evidence = EvidenceDraft(
        page_number,
        row_number,
        source_id,
        FINGERPRINT_VERSION,
        "",
    )
    checkpoint = PositionCheckpointDraft(
        source_order,
        _as_of_date(values["AS_OF"]),
        units,
        cost,
        value,
        currency,
        scale,
        evidence,
    )
    return PositionCheckpointDraft(
        checkpoint.source_order,
        checkpoint.as_of_date,
        checkpoint.reported_units,
        checkpoint.reported_cost_native,
        checkpoint.reported_value_native,
        checkpoint.currency,
        checkpoint.declared_unit_scale,
        EvidenceDraft(
            page_number,
            row_number,
            source_id,
            FINGERPRINT_VERSION,
            _fingerprint(target, checkpoint),
        ),
    )


@dataclass(frozen=True, slots=True)
class SummaryFundStatementAdapter:
    source_contract: str = SOURCE_CONTRACT
    extraction_version: str = EXTRACTION_VERSION

    def matches(self, pages: Sequence[Page]) -> bool:
        lines = tuple(line for page in pages for line in page.lines)
        return any(normalized_line_text(line) == _TITLE for line in lines) and any(
            _anchors(line) is not None for line in lines
        )

    def extract(self, pages: Sequence[Page]) -> StatementExtraction:
        builders: dict[tuple[str, str], _GroupBuilder] = {}
        reasons: list[SafeReason] = []
        current_account: str | None = None
        current_security: str | None = None
        target_ready = False
        anchors: tuple[float, ...] | None = None
        source_order = 0
        for page_number, page in enumerate(pages, start=1):
            for row_number, line in enumerate(page.lines, start=1):
                source_text = line_text(line)
                text = normalized_line_text(line)
                if text.startswith("ACCOUNT "):
                    if target_ready:
                        current_security = None
                    current_account = source_text
                    target_ready = current_security is not None
                    continue
                if text.startswith("SECURITY"):
                    if target_ready:
                        current_account = None
                    current_security = source_text
                    target_ready = current_account is not None
                    continue
                header = _anchors(line)
                if header is not None:
                    anchors = header
                    continue
                if (
                    anchors is None
                    or not line.words
                    or not text
                    or text in {_TITLE, "POSITION SUMMARY"}
                ):
                    continue
                values = _cells(line, anchors)
                if not values["AS_OF"]:
                    continue
                source_order += 1
                if current_security is None:
                    reasons.append(SafeReason("unknown_security", page_number, row_number))
                    continue
                if current_account is None:
                    reasons.append(SafeReason("ambiguous_account", page_number, row_number))
                    continue
                try:
                    target = _target(current_account, current_security)
                except _SecurityRefusal:
                    reasons.append(SafeReason("unknown_security", page_number, row_number))
                    continue
                except _RowRefusal:
                    reasons.append(SafeReason("ambiguous_identifier", page_number, row_number))
                    continue
                key = (
                    identifier_key(target.account_identifier),
                    security_key(target.security_reference),
                )
                builder = builders.setdefault(key, _GroupBuilder(target))
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
        groups = tuple(
            StatementGroup(
                group_number=index,
                target=builder.target,
                drafts=(),
                checkpoints=tuple(builder.checkpoints),
                reasons=tuple(builder.reasons),
            )
            for index, builder in enumerate(builders.values(), start=1)
        )
        return StatementExtraction(
            True,
            self.source_contract,
            self.extraction_version,
            groups,
            tuple(reasons),
        )
