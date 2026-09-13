"""Income, checkpoint and hostile-evidence coverage for Phase 1.6 Task 11.2."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.words import extract_pages
from oneledger.investments.adapters import StatementExtraction, extract_statement
from tests.fixtures.investment_pdf import (
    InvestmentCheckpointLine,
    InvestmentEventLine,
    build_detailed_investment_pdf,
)


def _event(kind: str, **changes: str) -> InvestmentEventLine:
    values = {
        "event_date": "2026-06-30",
        "event_kind": kind,
        "units": "",
        "price": "",
        "gross_amount": "12.5000",
        "charges": "0.1000",
        "withheld_tax": "0.2000",
        "net_amount": "12.2000",
        "source_event_id": f"SYNTHETIC-{kind}-A",
    }
    values.update(changes)
    return InvestmentEventLine(**values)


def _checkpoint(**changes: str) -> InvestmentCheckpointLine:
    values = {
        "as_of_date": "2026-06-30",
        "reported_units": "3.23456789",
        "reported_cost_native": "32.4999",
        "reported_value_native": "40.0000",
        "currency": "XAA",
        "statement_nav": "12.366275",
        "source_event_id": "SYNTHETIC-CHECKPOINT-A",
    }
    values.update(changes)
    return InvestmentCheckpointLine(**values)


def _extract(
    tmp_path: Path,
    *,
    events: list[InvestmentEventLine],
    checkpoints: list[InvestmentCheckpointLine] | None = None,
    name: str = "coverage",
    rows_per_page: int | None = None,
    checkpoint_rows_per_page: int | None = None,
    page_targets: list[tuple[str | None, str | None]] | None = None,
    data_size: float = 10.0,
) -> StatementExtraction:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(
        build_detailed_investment_pdf(
            events,
            checkpoints=checkpoints,
            rows_per_page=rows_per_page,
            checkpoint_rows_per_page=checkpoint_rows_per_page,
            page_targets=page_targets,
            data_size=data_size,
        )
    )
    return extract_statement(extract_pages(path))


@pytest.mark.parametrize(
    "event",
    [
        _event(
            "PURCHASE",
            units="1.00000000",
            price="12.500000",
            net_amount="-12.8000",
        ),
        _event("INTEREST"),
    ],
)
def test_core_and_new_content_report_the_coverage_extraction_version(
    tmp_path: Path, event: InvestmentEventLine
) -> None:
    result = _extract(tmp_path, events=[event])

    assert result.extraction_version == "3"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            _event("CASH_DISTRIBUTION"),
            ("cash_distribution", None, None, "12.5000", "0.1000", "0.2000", "12.2000"),
        ),
        (
            _event("INTEREST"),
            ("interest", None, None, "12.5000", "0.1000", "0.2000", "12.2000"),
        ),
        (
            _event(
                "REINVESTED_DISTRIBUTION",
                units="1.25000000",
                price="10.000000",
                charges="0.0000",
                withheld_tax="0.0000",
                net_amount="0.0000",
            ),
            (
                "reinvested_distribution",
                "1.25000000",
                "10.000000",
                "12.5000",
                "0.0000",
                "0.0000",
                "0.0000",
            ),
        ),
        (
            _event(
                "CHARGE",
                gross_amount="",
                charges="1.2500",
                withheld_tax="0.0000",
                net_amount="-1.2500",
            ),
            ("charge", None, None, None, "1.2500", "0.0000", "-1.2500"),
        ),
        (
            _event(
                "WITHHELD_TAX",
                gross_amount="",
                charges="0.0000",
                withheld_tax="0.7500",
                net_amount="-0.7500",
            ),
            ("withheld_tax", None, None, None, "0.0000", "0.7500", "-0.7500"),
        ),
    ],
)
def test_income_fee_and_tax_rows_preserve_the_exact_kind_matrix(
    tmp_path: Path,
    event: InvestmentEventLine,
    expected: tuple[str, str | None, str | None, str | None, str, str, str],
) -> None:
    result = _extract(tmp_path, events=[event])

    assert result.reasons == ()
    assert len(result.groups) == 1
    assert result.groups[0].reasons == ()
    draft = result.groups[0].drafts[0]
    actual = (
        draft.event_kind,
        None if draft.units is None else str(draft.units),
        None if draft.price is None else str(draft.price),
        None if draft.gross_amount is None else str(draft.gross_amount),
        str(draft.charges),
        str(draft.withheld_tax),
        str(draft.net_amount),
    )
    assert actual == expected
    assert draft.currency == "XAA"


@pytest.mark.parametrize(
    "event",
    [
        _event("CASH_DISTRIBUTION", units="1.00000000"),
        _event("INTEREST", price="1.000000"),
        _event(
            "REINVESTED_DISTRIBUTION",
            units="-1.00000000",
            price="12.500000",
            charges="0.0000",
            withheld_tax="0.0000",
            net_amount="0.0000",
        ),
        _event(
            "REINVESTED_DISTRIBUTION",
            units="1.00000000",
            price="12.500000",
            net_amount="0.0000",
        ),
        _event("CHARGE", gross_amount="1.0000", charges="1.0000", net_amount="-1.0000"),
        _event("WITHHELD_TAX", charges="0.1000", gross_amount="", net_amount="-0.2000"),
        _event("INTEREST", net_amount="12.3000"),
    ],
)
def test_income_fee_and_tax_rows_refuse_incompatible_fields(
    tmp_path: Path, event: InvestmentEventLine
) -> None:
    result = _extract(tmp_path, events=[event])

    assert result.groups[0].drafts == ()
    assert [reason.code for reason in result.groups[0].reasons] == ["unrecognized_event"]


def test_end_position_is_checkpoint_evidence_and_not_a_terminal_price(tmp_path: Path) -> None:
    first = _extract(tmp_path, events=[], checkpoints=[_checkpoint()], name="nav-a")
    second = _extract(
        tmp_path,
        events=[],
        checkpoints=[_checkpoint(statement_nav="12.366276")],
        name="nav-b",
    )

    assert first.reasons == ()
    assert len(first.groups) == 1
    assert first.groups[0].drafts == ()
    checkpoint = first.groups[0].checkpoints[0]
    assert checkpoint.as_of_date.isoformat() == "2026-06-30"
    assert checkpoint.reported_units == Decimal("3.23456789")
    assert checkpoint.reported_cost_native == Decimal("32.4999")
    assert checkpoint.reported_value_native == Decimal("40.0000")
    assert checkpoint.currency == "XAA"
    assert checkpoint.declared_unit_scale == 8
    assert checkpoint.evidence.source_event_id is not None
    assert checkpoint.evidence.source_event_id.value == "SYNTHETIC-CHECKPOINT-A"
    assert checkpoint.evidence.fingerprint_version == "typed_checkpoint_v1"
    assert len(checkpoint.evidence.fingerprint) == 64
    assert not hasattr(checkpoint, "price")
    assert checkpoint.evidence.fingerprint != second.groups[0].checkpoints[0].evidence.fingerprint


@pytest.mark.parametrize(
    ("checkpoint", "expected"),
    [
        (
            _checkpoint(
                reported_cost_native="",
                reported_value_native="",
                currency="",
                statement_nav="",
            ),
            (Decimal("3.23456789"), None, None, None, 8),
        ),
    ],
)
def test_checkpoint_preserves_each_supported_nullable_measurement_shape(
    tmp_path: Path,
    checkpoint: InvestmentCheckpointLine,
    expected: tuple[Decimal | None, Decimal | None, Decimal | None, str | None, int | None],
) -> None:
    result = _extract(tmp_path, events=[], checkpoints=[checkpoint])
    parsed = result.groups[0].checkpoints[0]

    assert (
        parsed.reported_units,
        parsed.reported_cost_native,
        parsed.reported_value_native,
        parsed.currency,
        parsed.declared_unit_scale,
    ) == expected


def test_repeated_event_and_checkpoint_headers_keep_ordered_evidence_across_pages(
    tmp_path: Path,
) -> None:
    checkpoints = [
        _checkpoint(source_event_id="SYNTHETIC-CHECKPOINT-A"),
        _checkpoint(
            as_of_date="2026-07-31",
            reported_units="4.00000000",
            reported_cost_native="42.0000",
            reported_value_native="44.0000",
            statement_nav="11.000000",
            source_event_id="SYNTHETIC-CHECKPOINT-B",
        ),
    ]
    result = _extract(
        tmp_path,
        events=[_event("INTEREST")],
        checkpoints=checkpoints,
        checkpoint_rows_per_page=1,
    )

    assert result.reasons == ()
    assert len(result.groups) == 1
    group = result.groups[0]
    assert len(group.drafts) == 1
    assert len(group.checkpoints) == 2
    assert [item.evidence.page_number for item in group.checkpoints] == [2, 3]
    assert [item.source_order for item in group.checkpoints] == [2, 3]
    assert all(item.evidence.row_number > 0 for item in group.checkpoints)


def test_maximum_supported_event_and_checkpoint_precision_is_exact(tmp_path: Path) -> None:
    event = _event(
        "REINVESTED_DISTRIBUTION",
        units="99999999999999999999.99999999",
        price="99999999999999.999999",
        gross_amount="9999999999999999.9999",
        charges="0.0000",
        withheld_tax="0.0000",
        net_amount="0.0000",
    )
    checkpoint = _checkpoint(
        reported_units="99999999999999999999.99999999",
        reported_cost_native="9999999999999999.9999",
        reported_value_native="9999999999999999.9999",
        statement_nav="99999999999999.999999",
    )
    result = _extract(tmp_path, events=[event], checkpoints=[checkpoint], data_size=4.0)

    group = result.groups[0]
    assert group.reasons == ()
    assert group.drafts[0].units == Decimal("99999999999999999999.99999999")
    assert group.drafts[0].price == Decimal("99999999999999.999999")
    assert group.drafts[0].gross_amount == Decimal("9999999999999999.9999")
    assert group.checkpoints[0].reported_units == Decimal("99999999999999999999.99999999")
    assert group.checkpoints[0].reported_cost_native == Decimal("9999999999999999.9999")
    assert group.checkpoints[0].reported_value_native == Decimal("9999999999999999.9999")


@pytest.mark.parametrize(
    "invalid",
    [
        _event(
            "REINVESTED_DISTRIBUTION",
            units="1.000000001",
            price="10.000000",
            charges="0.0000",
            withheld_tax="0.0000",
            net_amount="0.0000",
            source_event_id="SYNTHETIC-EXCESS-UNITS",
        ),
        _event(
            "REINVESTED_DISTRIBUTION",
            units="1.00000000",
            price="10.0000001",
            charges="0.0000",
            withheld_tax="0.0000",
            net_amount="0.0000",
            source_event_id="SYNTHETIC-EXCESS-PRICE",
        ),
        _event("INTEREST", gross_amount="12.50001", source_event_id="SYNTHETIC-EXCESS-GROSS"),
        _event("INTEREST", charges="0.10001", source_event_id="SYNTHETIC-EXCESS-CHARGE"),
        _event("INTEREST", withheld_tax="0.20001", source_event_id="SYNTHETIC-EXCESS-TAX"),
        _event("INTEREST", net_amount="12.20001", source_event_id="SYNTHETIC-EXCESS-NET"),
    ],
)
def test_excess_event_precision_refuses_the_whole_row_without_partial_facts(
    tmp_path: Path, invalid: InvestmentEventLine
) -> None:
    result = _extract(tmp_path, events=[_event("INTEREST"), invalid])

    group = result.groups[0]
    assert len(group.drafts) == 1
    assert [reason.code for reason in group.reasons] == ["unrecognized_event"]
    assert invalid.source_event_id not in repr(result)


@pytest.mark.parametrize(
    "changes",
    [
        {"reported_units": "3.234567891"},
        {"reported_cost_native": "32.49991"},
        {"reported_value_native": "40.00001"},
        {"statement_nav": "12.3662751"},
    ],
)
def test_excess_checkpoint_precision_refuses_the_whole_row_without_partial_facts(
    tmp_path: Path, changes: dict[str, str]
) -> None:
    invalid = _checkpoint(source_event_id="SYNTHETIC-EXCESS-CHECKPOINT", **changes)
    result = _extract(tmp_path, events=[], checkpoints=[_checkpoint(), invalid])

    group = result.groups[0]
    assert len(group.checkpoints) == 1
    assert [reason.code for reason in group.reasons] == ["unrecognized_event"]
    assert invalid.source_event_id not in repr(result)


@pytest.mark.parametrize(
    ("account_source", "security_reference", "source_event_id"),
    [
        ("SYNTHETIC12345678", "SYNTHETIC87654321", "SYNTHETIC-EVIDENCE-12345678"),
        (
            "SYNTHETIC-1234-5678",
            "SYNTHETIC-8765-4321",
            "SYNTHETIC-EVIDENCE-1234-5678",
        ),
        (
            "ＳＹＮＴＨＥＴＩＣ１２３４５６７８",
            "ＳＹＮＴＨＥＴＩＣ８７６５４３２１",
            "ＳＹＮＴＨＥＴＩＣ-EVIDENCE-１２３４５６７８",
        ),
    ],
)
def test_hostile_identity_evidence_stays_private_in_results_reasons_and_fingerprints(
    tmp_path: Path,
    account_source: str,
    security_reference: str,
    source_event_id: str,
) -> None:
    result = _extract(
        tmp_path,
        events=[
            _event("INTEREST", source_event_id=source_event_id),
            _event("UNKNOWN_UNIT_ACTION", source_event_id="SYNTHETIC-BLOCKED-PRIVATE"),
        ],
        checkpoints=[_checkpoint(source_event_id=source_event_id)],
        page_targets=[
            (f"folio COMPLETE {account_source}", security_reference),
            (f"folio COMPLETE {account_source}", security_reference),
        ],
    )
    group = result.groups[0]
    source_identity = group.drafts[0].evidence.source_event_id

    assert group.target.account_identifier.full_value == account_source  # type: ignore[union-attr]
    assert group.target.security_reference == security_reference
    assert source_identity is not None and source_identity.value == source_event_id
    for private_value in (account_source, security_reference, source_event_id):
        assert private_value not in repr(group.target)
        assert private_value not in repr(group)
        assert private_value not in repr(result)
        assert private_value not in str(group.reasons)
        assert private_value not in group.drafts[0].evidence.fingerprint
        assert private_value not in group.checkpoints[0].evidence.fingerprint


def test_unknown_unit_action_blocks_only_its_own_group(tmp_path: Path) -> None:
    result = _extract(
        tmp_path,
        events=[
            _event("UNKNOWN_UNIT_ACTION", source_event_id="SYNTHETIC-UNKNOWN-A"),
            _event("INTEREST", source_event_id="SYNTHETIC-INCOME-B"),
        ],
        rows_per_page=1,
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"),
            ("folio MASKED ****B2C3", "SYNTHETIC-SECURITY-B"),
        ],
    )

    assert result.reasons == ()
    assert len(result.groups) == 2
    assert result.groups[0].drafts == ()
    assert [reason.code for reason in result.groups[0].reasons] == ["unrecognized_event"]
    assert [draft.event_kind for draft in result.groups[1].drafts] == ["interest"]
    assert result.groups[1].reasons == ()
