"""Explicit-evidence unit actions for the detailed fund adapter."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from ..identifiers import CompleteIdentifier, Identifier
from .adapters import (
    EvidenceDraft,
    InvestmentEventDraft,
    InvestmentEventKind,
    SourceEventIdentity,
    SourceTarget,
)

ACTION_HEADERS = tuple(
    "DATE EVENT UNITS RATIO PRICE GROSS CHARGES TAX NET CURRENCY ACTION_REF REFERENCE".split()
)
_FINGERPRINT_VERSION = "typed_action_v1"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_KINDS: dict[str, InvestmentEventKind] = {
    "SPLIT": "split",
    "BONUS": "bonus",
    "RIGHTS": "rights",
    "SWITCH_IN": "switch_in",
    "SWITCH_OUT": "switch_out",
    "SYSTEMATIC_TRANSFER_IN": "systematic_transfer_in",
    "SYSTEMATIC_TRANSFER_OUT": "systematic_transfer_out",
    "PLATFORM_TRANSFER_IN": "platform_transfer_in",
    "PLATFORM_TRANSFER_OUT": "platform_transfer_out",
}
_PAIRS = (
    frozenset(("switch_in", "switch_out")),
    frozenset(("systematic_transfer_in", "systematic_transfer_out")),
    frozenset(("platform_transfer_in", "platform_transfer_out")),
)
_PAIRED_KINDS = frozenset(kind for pair in _PAIRS for kind in pair)


class ActionRefusal(ValueError):
    """A privacy-safe marker for a row that cannot become a typed action."""


@dataclass(frozen=True, slots=True, repr=False)
class ParsedAction:
    target_key: tuple[str, str]
    action_reference: SourceEventIdentity
    draft: InvestmentEventDraft

    def __repr__(self) -> str:
        return "ParsedAction(<private-evidence>)"


def _decimal(value: str, *, digits: int, scale: int, signed: bool) -> Decimal:
    sign = "-?" if signed else ""
    if re.fullmatch(rf"{sign}\d{{1,{digits}}}(?:\.\d{{1,{scale}}})?", value) is None:
        raise ActionRefusal
    return Decimal(value)


def _optional_decimal(value: str, *, digits: int, scale: int, signed: bool) -> Decimal | None:
    return None if not value else _decimal(value, digits=digits, scale=scale, signed=signed)


def _event_date(value: str) -> date:
    try:
        if _DATE.fullmatch(value) is None:
            raise ValueError
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise ValueError
        return result
    except ValueError:
        raise ActionRefusal from None


def _canonical(value: Decimal | None) -> str:
    if value is None:
        return "<null>"
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _identifier_key(identifier: Identifier) -> str:
    if isinstance(identifier, CompleteIdentifier):
        return f"{identifier.kind}:complete:{identifier.normalized_value}"
    return f"{identifier.kind}:masked:{identifier.visible_suffix}"


def _security_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def _fingerprint(
    source_contract: str,
    target: SourceTarget,
    action_reference: SourceEventIdentity,
    draft: InvestmentEventDraft,
) -> str:
    values = (
        source_contract,
        _identifier_key(target.account_identifier),
        _security_key(target.security_reference),
        action_reference.value,
        draft.event_date.isoformat(),
        draft.event_kind,
        _canonical(draft.units),
        _canonical(draft.unit_ratio),
        _canonical(draft.price),
        _canonical(draft.gross_amount),
        _canonical(draft.charges),
        _canonical(draft.withheld_tax),
        _canonical(draft.net_amount),
        draft.currency,
    )
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def parse_action(
    values: dict[str, str],
    target: SourceTarget,
    target_key: tuple[str, str],
    *,
    source_contract: str,
    source_order: int,
    page_number: int,
    row_number: int,
) -> ParsedAction:
    """Return one exact action candidate; pairing is validated after all rows are read."""
    try:
        kind = _KINDS[values["EVENT"]]
        action_reference = SourceEventIdentity(values["ACTION_REF"])
        source_event_id = SourceEventIdentity(values["REFERENCE"]) if values["REFERENCE"] else None
    except KeyError, ValueError:
        raise ActionRefusal from None
    units = _optional_decimal(values["UNITS"], digits=20, scale=8, signed=True)
    ratio = _optional_decimal(values["RATIO"], digits=10, scale=10, signed=False)
    price = _optional_decimal(values["PRICE"], digits=14, scale=6, signed=False)
    gross = _optional_decimal(values["GROSS"], digits=16, scale=4, signed=False)
    charges = _decimal(values["CHARGES"], digits=16, scale=4, signed=False)
    tax = _decimal(values["TAX"], digits=16, scale=4, signed=False)
    net = _decimal(values["NET"], digits=16, scale=4, signed=True)
    currency = values["CURRENCY"]
    if _CURRENCY.fullmatch(currency) is None:
        raise ActionRefusal

    zero_cash = charges == tax == net == 0
    directional = units is not None and (
        (kind.endswith("_in") and units > 0) or (kind.endswith("_out") and units < 0)
    )
    market_facts = (
        ratio is None and price is not None and price > 0 and gross is not None and gross > 0
    )
    if kind == "split":
        valid = (
            units is None
            and ratio is not None
            and ratio > 0
            and ratio != 1
            and price is gross is None
            and zero_cash
        )
    elif kind == "bonus":
        valid = units is not None and units > 0 and ratio is price is gross is None and zero_cash
    elif kind == "rights":
        valid = (
            units is not None
            and units > 0
            and market_facts
            and gross is not None
            and net == -(gross + charges + tax)
        )
    elif kind.startswith("platform_transfer_"):
        valid = directional and ratio is price is gross is None and zero_cash
    else:
        valid = directional and market_facts and zero_cash
    if not valid:
        raise ActionRefusal

    evidence = EvidenceDraft(page_number, row_number, source_event_id, _FINGERPRINT_VERSION, "")
    draft = InvestmentEventDraft(
        source_order,
        _event_date(values["DATE"]),
        kind,
        units,
        ratio,
        price,
        gross,
        charges,
        tax,
        net,
        currency,
        evidence,
    )
    return ParsedAction(
        target_key,
        action_reference,
        replace(
            draft,
            evidence=replace(
                evidence,
                fingerprint=_fingerprint(source_contract, target, action_reference, draft),
            ),
        ),
    )


def valid_action_groups(actions: Sequence[ParsedAction]) -> tuple[tuple[ParsedAction, ...], ...]:
    """Return source-named singleton actions and exact indivisible in/out pairs."""
    grouped: dict[str, list[ParsedAction]] = defaultdict(list)
    for action in actions:
        grouped[action.action_reference.value].append(action)
    accepted: list[tuple[ParsedAction, ...]] = []
    for candidates in grouped.values():
        kinds = frozenset(candidate.draft.event_kind for candidate in candidates)
        is_single = len(candidates) == 1 and not kinds.intersection(_PAIRED_KINDS)
        is_pair = (
            len(candidates) == 2
            and kinds in _PAIRS
            and len({candidate.target_key for candidate in candidates}) == 2
        )
        if kinds == frozenset(("platform_transfer_in", "platform_transfer_out")):
            accounts = {candidate.target_key[0] for candidate in candidates}
            securities = {candidate.target_key[1] for candidate in candidates}
            units = [candidate.draft.units for candidate in candidates]
            is_pair = (
                is_pair
                and len(accounts) == 2
                and len(securities) == 1
                and units[0] is not None
                and units[1] is not None
                and units[0] == -units[1]
            )
        if is_single or is_pair:
            accepted.append(tuple(candidates))
    return tuple(accepted)
