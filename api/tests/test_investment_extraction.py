"""Pure detailed-statement extraction contracts for Phase 1.6 Task 11.1."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.words import Page, extract_pages
from oneledger.investments.adapters import extract_statement
from tests.fixtures.investment_pdf import InvestmentEventLine, build_detailed_investment_pdf
from tests.fixtures.synthetic_pdf import Placement, build_pdf


def _pages(
    tmp_path: Path, events: list[InvestmentEventLine], *, rows_per_page: int | None = None
) -> list[Page]:
    path = tmp_path / "synthetic-investment.pdf"
    path.write_bytes(build_detailed_investment_pdf(events, rows_per_page=rows_per_page))
    return extract_pages(path)


def _purchase(**changes: str) -> InvestmentEventLine:
    values = {
        "event_date": "2026-01-02",
        "event_kind": "PURCHASE",
        "units": "1.23456789",
        "price": "10.123456",
        "gross_amount": "12.4999",
        "charges": "0.0001",
        "withheld_tax": "0.0000",
        "net_amount": "-12.5000",
        "source_event_id": "SYNTHETIC-PURCHASE-A",
    }
    values.update(changes)
    return InvestmentEventLine(**values)


def test_unsupported_content_abstains_with_only_a_fixed_safe_reason(tmp_path: Path) -> None:
    """Adding a permissive fallback parser would turn unsupported content into invented facts."""
    path = tmp_path / "unsupported.pdf"
    path.write_bytes(build_pdf([Placement(page=0, x=20, y=740, text="SYNTHETIC OTHER DOCUMENT")]))

    result = extract_statement(extract_pages(path))

    assert result.supported is False
    assert result.source_contract is None
    assert result.extraction_version is None
    assert result.groups == ()
    assert [(reason.code, reason.page_number, reason.row_number) for reason in result.reasons] == [
        ("unsupported_format", None, None)
    ]


def test_selection_and_fingerprints_depend_on_content_not_the_pdf_filename(tmp_path: Path) -> None:
    """Path-based selection would make owner-controlled metadata statement evidence."""
    pdf = build_detailed_investment_pdf([_purchase()])
    first = tmp_path / "first.pdf"
    second = tmp_path / "ignored-owner-label.pdf"
    first.write_bytes(pdf)
    second.write_bytes(pdf)

    left = extract_statement(extract_pages(first))
    right = extract_statement(extract_pages(second))

    assert left == right
    assert left.supported is True
    assert left.source_contract == "detailed_fund_statement_v1"
    assert left.extraction_version == "1"
    assert "first.pdf" not in repr(left)
    assert "ignored-owner-label.pdf" not in repr(left)


@pytest.mark.parametrize(
    ("event", "kind", "units", "net"),
    [
        (_purchase(), "purchase", "1.23456789", "-12.5000"),
        (
            _purchase(
                event_date="2026-02-02",
                event_kind="RECURRING_PURCHASE",
                units="2.00000000",
                price="5.000000",
                gross_amount="10.0000",
                charges="0.0000",
                net_amount="-10.0000",
                source_event_id="SYNTHETIC-RECURRING-A",
            ),
            "recurring_purchase",
            "2.00000000",
            "-10.0000",
        ),
        (
            _purchase(
                event_date="2026-03-02",
                event_kind="SALE",
                units="-0.50000000",
                price="12.000000",
                gross_amount="6.0000",
                charges="0.1000",
                withheld_tax="0.2000",
                net_amount="5.7000",
                source_event_id="SYNTHETIC-SALE-A",
            ),
            "sale",
            "-0.50000000",
            "5.7000",
        ),
        (
            _purchase(
                event_date="2026-04-02",
                event_kind="REDEMPTION",
                units="-1.00000000",
                price="8.000000",
                gross_amount="8.0000",
                charges="0.0000",
                net_amount="8.0000",
                source_event_id="SYNTHETIC-REDEMPTION-A",
            ),
            "redemption",
            "-1.00000000",
            "8.0000",
        ),
        (
            _purchase(
                event_date="2026-05-02",
                event_kind="MATURITY",
                units="-3.00000000",
                price="4.000000",
                gross_amount="12.0000",
                charges="0.0000",
                net_amount="12.0000",
                source_event_id="SYNTHETIC-MATURITY-A",
            ),
            "maturity",
            "-3.00000000",
            "12.0000",
        ),
    ],
)
def test_core_event_rows_preserve_exact_typed_facts(
    tmp_path: Path, event: InvestmentEventLine, kind: str, units: str, net: str
) -> None:
    """Wrong event mapping, sign inference, or binary-float parsing would change source facts."""
    result = extract_statement(_pages(tmp_path, [event]))

    assert result.reasons == ()
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.group_number == 1
    assert group.reasons == ()
    assert group.checkpoints == ()
    assert group.target.account_identifier.masked_value == "****A1B2"
    assert group.target.security_reference == "SYNTHETIC-SECURITY-A"
    draft = group.drafts[0]
    assert draft.event_date.isoformat() == event.event_date
    assert draft.event_kind == kind
    assert str(draft.units) == units
    assert draft.unit_ratio is None
    assert str(draft.price) == event.price
    assert str(draft.gross_amount) == event.gross_amount
    assert str(draft.charges) == event.charges
    assert str(draft.withheld_tax) == event.withheld_tax
    assert str(draft.net_amount) == net
    assert draft.currency == "XAA"
    assert all(
        value is None or isinstance(value, Decimal)
        for value in (
            draft.units,
            draft.unit_ratio,
            draft.price,
            draft.gross_amount,
            draft.charges,
            draft.withheld_tax,
            draft.net_amount,
        )
    )


def test_result_contract_is_deeply_immutable_and_private_source_ids_have_safe_repr(
    tmp_path: Path,
) -> None:
    """Mutable outputs or dataclass reprs could alter evidence or disclose its private identity."""
    private_reference = "SYNTHETIC-PRIVATE-EVENT-A"
    result = extract_statement(_pages(tmp_path, [_purchase(source_event_id=private_reference)]))
    group = result.groups[0]
    draft = group.drafts[0]

    assert draft.evidence.source_event_id is not None
    assert draft.evidence.source_event_id.value == private_reference
    assert private_reference not in repr(result)
    assert private_reference not in repr(draft.evidence.source_event_id)
    assert len(draft.evidence.fingerprint) == 64
    assert set(draft.evidence.fingerprint) <= set("0123456789abcdef")
    assert draft.evidence.fingerprint_version == "typed_event_v1"
    with pytest.raises(FrozenInstanceError):
        group.group_number = 2  # type: ignore[misc]
    with pytest.raises(AttributeError):
        draft.evidence.source_event_id.value = "replacement"  # type: ignore[misc]


def test_repeated_page_headers_keep_one_group_and_ordered_page_row_evidence(tmp_path: Path) -> None:
    """Resetting context at a page break would split one holding or lose the following row."""
    first = _purchase()
    second = _purchase(
        event_date="2026-02-02",
        event_kind="RECURRING_PURCHASE",
        units="2.00000000",
        price="5.000000",
        gross_amount="10.0000",
        charges="0.0000",
        net_amount="-10.0000",
        source_event_id="SYNTHETIC-RECURRING-B",
    )

    result = extract_statement(_pages(tmp_path, [first, second], rows_per_page=1))

    assert len(result.groups) == 1
    assert [draft.source_order for draft in result.groups[0].drafts] == [1, 2]
    assert [draft.evidence.page_number for draft in result.groups[0].drafts] == [1, 2]
    assert all(draft.evidence.row_number > 0 for draft in result.groups[0].drafts)


def test_source_event_identity_is_optional_but_typed_fingerprint_remains(tmp_path: Path) -> None:
    """Requiring a private reference would reject sources that correctly rely on typed dedupe."""
    result = extract_statement(_pages(tmp_path, [_purchase(source_event_id="")]))

    draft = result.groups[0].drafts[0]
    assert draft.evidence.source_event_id is None
    assert len(draft.evidence.fingerprint) == 64


def test_public_security_reference_preserves_source_case(tmp_path: Path) -> None:
    """Case-folding source evidence would silently rewrite a public instrument reference."""
    pdf = build_detailed_investment_pdf([_purchase()], security_reference="Synthetic-Security-a")
    path = tmp_path / "mixed-case.pdf"
    path.write_bytes(pdf)

    result = extract_statement(extract_pages(path))

    assert result.groups[0].target.security_reference == "Synthetic-Security-a"


def test_typed_fingerprint_distinguishes_account_identifier_kinds(tmp_path: Path) -> None:
    """Dropping identifier kind could merge distinct account evidence with the same suffix."""
    event = _purchase()
    folio_path = tmp_path / "folio.pdf"
    brokerage_path = tmp_path / "brokerage.pdf"
    folio_path.write_bytes(build_detailed_investment_pdf([event], account_kind="folio"))
    brokerage_path.write_bytes(
        build_detailed_investment_pdf([event], account_kind="brokerage_account")
    )

    folio = extract_statement(extract_pages(folio_path))
    brokerage = extract_statement(extract_pages(brokerage_path))

    assert (
        folio.groups[0].drafts[0].evidence.fingerprint
        != brokerage.groups[0].drafts[0].evidence.fingerprint
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"units": "-1.00000000"},
        {"event_kind": "SALE", "units": "1.00000000", "net_amount": "12.4998"},
        {"price": "10.1234567"},
        {"gross_amount": "12.49991", "net_amount": "-12.50001"},
        {"event_date": "02/01/2026"},
        {"net_amount": "-12.4999"},
        {"event_kind": "UNKNOWN_UNIT_ACTION"},
    ],
)
def test_invalid_direction_precision_date_or_kind_blocks_without_guessing(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    """Coercing an invalid row would manufacture a date, direction, precision, or event kind."""
    private_reference = "SYNTHETIC-PRIVATE-BLOCKED-EVENT"
    event = _purchase(source_event_id=private_reference, **changes)

    result = extract_statement(_pages(tmp_path, [event]))

    assert result.supported is True
    assert result.reasons == ()
    assert len(result.groups) == 1
    assert result.groups[0].drafts == ()
    assert [reason.code for reason in result.groups[0].reasons] == ["unrecognized_event"]
    assert private_reference not in repr(result)
    assert "UNKNOWN_UNIT_ACTION" not in repr(result)
