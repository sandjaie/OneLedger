"""Pure, immutable investment-statement adapter contracts."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from ..identifiers import Identifier
from ..ingest.words import Page

type InvestmentEventKind = Literal[
    "purchase",
    "recurring_purchase",
    "sale",
    "redemption",
    "maturity",
    "cash_distribution",
    "interest",
    "reinvested_distribution",
    "charge",
    "withheld_tax",
    "switch_in",
    "switch_out",
    "systematic_transfer_in",
    "systematic_transfer_out",
    "split",
    "bonus",
    "rights",
    "platform_transfer_in",
    "platform_transfer_out",
]
type ReviewReason = Literal[
    "unsupported_format",
    "unknown_security",
    "ambiguous_account",
    "ambiguous_identifier",
    "unrecognized_event",
    "duplicate_conflict",
    "unit_mismatch",
    "cost_mismatch",
    "missing_price",
    "unmatched_cash",
    "incomplete_lot_allocation",
    "stale_valuation",
]


class SourceEventIdentity:
    """Private source identity whose ordinary representation is always opaque."""

    __slots__ = ("_value",)
    _value: str

    def __init__(self, value: str) -> None:
        if (
            not value
            or len(value) > 128
            or len(value.encode("utf-8")) > 512
            or any(unicodedata.category(character).startswith("C") for character in value)
        ):
            raise ValueError("source event identity is invalid")
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SourceEventIdentity is immutable")

    @property
    def value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SourceEventIdentity(<private>)"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SourceEventIdentity) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)


@dataclass(frozen=True, slots=True)
class SafeReason:
    code: ReviewReason
    page_number: int | None = None
    row_number: int | None = None


@dataclass(frozen=True, slots=True)
class SourceTarget:
    account_identifier: Identifier
    security_reference: str

    def __repr__(self) -> str:
        return (
            f"SourceTarget(account_identifier={self.account_identifier!r}, "
            "security_reference=<private>)"
        )


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    page_number: int
    row_number: int
    source_event_id: SourceEventIdentity | None
    fingerprint_version: str
    fingerprint: str
    source_correlation_id: SourceEventIdentity | None = None


@dataclass(frozen=True, slots=True)
class InvestmentEventDraft:
    source_order: int
    event_date: date
    event_kind: InvestmentEventKind
    units: Decimal | None
    unit_ratio: Decimal | None
    price: Decimal | None
    gross_amount: Decimal | None
    charges: Decimal
    withheld_tax: Decimal
    net_amount: Decimal
    currency: str
    evidence: EvidenceDraft


@dataclass(frozen=True, slots=True)
class PositionCheckpointDraft:
    source_order: int
    as_of_date: date
    reported_units: Decimal | None
    reported_cost_native: Decimal | None
    reported_value_native: Decimal | None
    currency: str | None
    declared_unit_scale: int | None
    evidence: EvidenceDraft


@dataclass(frozen=True, slots=True)
class StatementGroup:
    group_number: int
    target: SourceTarget
    drafts: tuple[InvestmentEventDraft, ...]
    checkpoints: tuple[PositionCheckpointDraft, ...] = ()
    reasons: tuple[SafeReason, ...] = ()


@dataclass(frozen=True, slots=True)
class StatementExtraction:
    supported: bool
    source_contract: str | None
    extraction_version: str | None
    groups: tuple[StatementGroup, ...]
    reasons: tuple[SafeReason, ...] = ()


@runtime_checkable
class InvestmentStatementAdapter(Protocol):
    @property
    def source_contract(self) -> str: ...

    @property
    def extraction_version(self) -> str: ...

    def matches(self, pages: Sequence[Page]) -> bool: ...

    def extract(self, pages: Sequence[Page]) -> StatementExtraction: ...


def _unsupported() -> StatementExtraction:
    return StatementExtraction(
        supported=False,
        source_contract=None,
        extraction_version=None,
        groups=(),
        reasons=(SafeReason("unsupported_format"),),
    )


def extract_statement(
    pages: Sequence[Page], adapters: Sequence[InvestmentStatementAdapter] | None = None
) -> StatementExtraction:
    """Select exactly one adapter using content only, then return its pure result."""
    if adapters is None:
        from .detailed_fund_statement import DetailedFundStatementAdapter
        from .summary_fund_statement import SummaryFundStatementAdapter

        selected_adapters: Sequence[InvestmentStatementAdapter] = (
            DetailedFundStatementAdapter(),
            SummaryFundStatementAdapter(),
        )
    else:
        selected_adapters = adapters
    matching = tuple(adapter for adapter in selected_adapters if adapter.matches(pages))
    if len(matching) != 1:
        return _unsupported()
    return matching[0].extract(pages)
