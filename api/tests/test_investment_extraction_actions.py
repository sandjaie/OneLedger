"""Explicitly grouped unit-action extraction for Phase 1.6 Task 11.3."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.words import extract_pages
from oneledger.investments.adapters import StatementExtraction, extract_statement
from tests.fixtures.investment_pdf import (
    InvestmentActionLine,
    InvestmentEventLine,
    build_detailed_investment_pdf,
)


def _action(kind: str, **changes: str) -> InvestmentActionLine:
    values = {
        "event_date": "2026-07-15",
        "event_kind": kind,
        "units": "",
        "unit_ratio": "",
        "price": "",
        "gross_amount": "",
        "charges": "0.0000",
        "withheld_tax": "0.0000",
        "net_amount": "0.0000",
        "action_reference": f"SYNTHETIC-GROUP-{kind}",
        "source_event_id": f"SYNTHETIC-ACTION-{kind}",
    }
    values.update(changes)
    return InvestmentActionLine(**values)


def _purchase() -> InvestmentEventLine:
    return InvestmentEventLine(
        event_date="2026-07-01",
        event_kind="PURCHASE",
        units="1.00000000",
        price="10.000000",
        gross_amount="10.0000",
        charges="0.0000",
        withheld_tax="0.0000",
        net_amount="-10.0000",
        source_event_id="SYNTHETIC-PURCHASE-INDEPENDENT",
    )


def _extract(
    tmp_path: Path,
    actions: list[InvestmentActionLine],
    *,
    events: list[InvestmentEventLine] | None = None,
    page_targets: list[tuple[str | None, str | None]] | None = None,
    action_rows_per_page: int | None = None,
    name: str = "actions",
    data_size: float = 10.0,
) -> StatementExtraction:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(
        build_detailed_investment_pdf(
            events or [],
            actions=actions,
            action_rows_per_page=action_rows_per_page,
            page_targets=page_targets,
            data_size=data_size,
        )
    )
    return extract_statement(extract_pages(path))


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            _action("SPLIT", unit_ratio="2.5000000000"),
            ("split", None, "2.5000000000", None, None, "0.0000"),
        ),
        (
            _action("BONUS", units="1.25000000"),
            ("bonus", "1.25000000", None, None, None, "0.0000"),
        ),
        (
            _action(
                "RIGHTS",
                units="3.00000000",
                price="4.000000",
                gross_amount="12.0000",
                charges="0.1000",
                withheld_tax="0.2000",
                net_amount="-12.3000",
            ),
            ("rights", "3.00000000", None, "4.000000", "12.0000", "-12.3000"),
        ),
    ],
)
def test_single_unit_actions_preserve_the_exact_source_facts(
    tmp_path: Path,
    action: InvestmentActionLine,
    expected: tuple[str, str | None, str | None, str | None, str | None, str],
) -> None:
    result = _extract(tmp_path, [action])

    assert result.supported is True
    assert result.extraction_version == "3"
    assert result.reasons == ()
    group = result.groups[0]
    assert group.reasons == ()
    assert len(group.drafts) == 1
    draft = group.drafts[0]
    assert (
        draft.event_kind,
        None if draft.units is None else str(draft.units),
        None if draft.unit_ratio is None else str(draft.unit_ratio),
        None if draft.price is None else str(draft.price),
        None if draft.gross_amount is None else str(draft.gross_amount),
        str(draft.net_amount),
    ) == expected
    assert draft.currency == "XAA"
    assert draft.evidence.fingerprint_version == "typed_action_v1"


@pytest.mark.parametrize(
    ("in_kind", "out_kind", "with_price"),
    [
        ("SWITCH_IN", "SWITCH_OUT", True),
        ("SYSTEMATIC_TRANSFER_IN", "SYSTEMATIC_TRANSFER_OUT", True),
        ("PLATFORM_TRANSFER_IN", "PLATFORM_TRANSFER_OUT", False),
    ],
)
def test_paired_actions_use_the_explicit_source_group_and_preserve_directions(
    tmp_path: Path, in_kind: str, out_kind: str, with_price: bool
) -> None:
    shared_reference = f"SYNTHETIC-PAIR-{in_kind}"
    facts = {"price": "5.000000", "gross_amount": "10.0000"} if with_price else {}
    actions = [
        _action(
            out_kind,
            units="-2.00000000",
            action_reference=shared_reference,
            source_event_id=f"SYNTHETIC-{out_kind}",
            **facts,
        ),
        _action(
            in_kind,
            units="2.00000000",
            action_reference=shared_reference,
            source_event_id=f"SYNTHETIC-{in_kind}",
            **facts,
        ),
    ]
    result = _extract(
        tmp_path,
        actions,
        action_rows_per_page=1,
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"),
            (
                "folio MASKED ****B2C3",
                "SYNTHETIC-SECURITY-A"
                if in_kind == "PLATFORM_TRANSFER_IN"
                else "SYNTHETIC-SECURITY-B",
            ),
        ],
    )

    assert result.reasons == ()
    assert len(result.groups) == 2
    assert all(group.reasons == () for group in result.groups)
    drafts = [group.drafts[0] for group in result.groups]
    assert [draft.event_kind for draft in drafts] == [out_kind.lower(), in_kind.lower()]
    assert [draft.units for draft in drafts] == [Decimal("-2.00000000"), Decimal("2.00000000")]
    assert [draft.price for draft in drafts] == (
        [Decimal("5.000000"), Decimal("5.000000")] if with_price else [None, None]
    )
    correlations = [draft.evidence.source_correlation_id for draft in drafts]
    assert all(correlation is not None for correlation in correlations)
    assert correlations[0] == correlations[1]
    assert correlations[0] is not None and correlations[0].value == shared_reference
    assert shared_reference not in repr(result)
    with pytest.raises(FrozenInstanceError):
        drafts[0].evidence.source_correlation_id = correlations[1]  # type: ignore[misc]


@pytest.mark.parametrize(
    ("out_units", "in_units", "second_account", "second_security"),
    [
        ("-2.00000000", "1.00000000", "****B2C3", "SYNTHETIC-SECURITY-A"),
        ("-2.00000000", "2.00000000", "****B2C3", "SYNTHETIC-SECURITY-B"),
        ("-2.00000000", "2.00000000", "****A1B2", "SYNTHETIC-SECURITY-A"),
    ],
)
def test_platform_transfer_requires_balanced_units_one_security_and_distinct_accounts(
    tmp_path: Path,
    out_units: str,
    in_units: str,
    second_account: str,
    second_security: str,
) -> None:
    shared_reference = "SYNTHETIC-PLATFORM-PAIR"
    result = _extract(
        tmp_path,
        [
            _action(
                "PLATFORM_TRANSFER_OUT",
                units=out_units,
                action_reference=shared_reference,
            ),
            _action(
                "PLATFORM_TRANSFER_IN",
                units=in_units,
                action_reference=shared_reference,
            ),
        ],
        action_rows_per_page=1,
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"),
            (f"folio MASKED {second_account}", second_security),
        ],
    )

    assert all(group.drafts == () for group in result.groups)
    assert all(
        group.reasons and all(reason.code == "unrecognized_event" for reason in group.reasons)
        for group in result.groups
    )


def test_a_blocker_on_one_correlated_leg_blocks_both_while_an_unrelated_group_remains_usable(
    tmp_path: Path,
) -> None:
    shared_reference = "SYNTHETIC-PRIVATE-ATOMIC-PAIR"
    result = _extract(
        tmp_path,
        [
            _action(
                "PLATFORM_TRANSFER_OUT",
                units="-2.00000000",
                action_reference=shared_reference,
            ),
            _action(
                "PLATFORM_TRANSFER_IN",
                units="2.00000000",
                action_reference=shared_reference,
            ),
            _action("UNKNOWN_ACTION", action_reference="SYNTHETIC-LOCAL-BLOCKER"),
        ],
        events=[_purchase()],
        action_rows_per_page=1,
        page_targets=[
            ("folio MASKED ****E5F6", "SYNTHETIC-SECURITY-E"),
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-P"),
            ("folio MASKED ****B2C3", "SYNTHETIC-SECURITY-P"),
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-P"),
        ],
    )

    assert [draft.event_kind for draft in result.groups[0].drafts] == ["purchase"]
    assert result.groups[0].reasons == ()
    assert all(group.drafts == () for group in result.groups[1:])
    assert all(
        any(reason.code == "unrecognized_event" for reason in group.reasons)
        for group in result.groups[1:]
    )
    assert shared_reference not in repr(result)


def test_matching_nearby_values_without_the_same_explicit_group_never_pair(tmp_path: Path) -> None:
    result = _extract(
        tmp_path,
        [
            _action("SWITCH_OUT", units="-2.00000000", price="5.000000", gross_amount="10.0000"),
            _action("SWITCH_IN", units="2.00000000", price="5.000000", gross_amount="10.0000"),
        ],
        action_rows_per_page=1,
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"),
            ("folio MASKED ****B2C3", "SYNTHETIC-SECURITY-A"),
        ],
    )

    assert len(result.groups) == 2
    assert all(group.drafts == () for group in result.groups)
    assert [[reason.code for reason in group.reasons] for group in result.groups] == [
        ["unrecognized_event"],
        ["unrecognized_event"],
    ]


@pytest.mark.parametrize(
    "actions",
    [
        [_action("SWITCH_IN", units="2.00000000", price="5.000000", gross_amount="10.0000")],
        [
            _action(
                "SWITCH_IN",
                units="2.00000000",
                price="5.000000",
                gross_amount="10.0000",
                action_reference="SYNTHETIC-CONTRADICTORY",
            ),
            _action(
                "SYSTEMATIC_TRANSFER_OUT",
                units="-2.00000000",
                price="5.000000",
                gross_amount="10.0000",
                action_reference="SYNTHETIC-CONTRADICTORY",
            ),
        ],
    ],
)
def test_partial_or_contradictory_pairs_block_only_their_affected_groups(
    tmp_path: Path, actions: list[InvestmentActionLine]
) -> None:
    page_targets = [("folio MASKED ****C3D4", "SYNTHETIC-SECURITY-C")]
    if len(actions) == 2:
        page_targets.append(("folio MASKED ****D4E5", "SYNTHETIC-SECURITY-D"))
    result = _extract(
        tmp_path,
        actions,
        events=[_purchase()],
        action_rows_per_page=1,
        page_targets=[("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"), *page_targets],
    )

    assert [draft.event_kind for draft in result.groups[0].drafts] == ["purchase"]
    assert result.groups[0].reasons == ()
    assert all(group.drafts == () for group in result.groups[1:])
    assert all(
        [reason.code for reason in group.reasons] == ["unrecognized_event"]
        for group in result.groups[1:]
    )


def test_unknown_action_blocks_its_group_while_an_unrelated_group_remains_usable(
    tmp_path: Path,
) -> None:
    result = _extract(
        tmp_path,
        [_action("UNKNOWN_ACTION")],
        events=[_purchase()],
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"),
            ("folio MASKED ****B2C3", "SYNTHETIC-SECURITY-A"),
        ],
    )

    assert [draft.event_kind for draft in result.groups[0].drafts] == ["purchase"]
    assert result.groups[0].reasons == ()
    assert result.groups[1].drafts == ()
    assert [reason.code for reason in result.groups[1].reasons] == ["unrecognized_event"]


@pytest.mark.parametrize(
    "ratio", ["1.0000000000", "0.0000000000", "-2.0000000000", "2.00000000001"]
)
def test_invalid_or_excess_precision_split_ratio_is_refused_atomically(
    tmp_path: Path, ratio: str
) -> None:
    action = _action("SPLIT", unit_ratio=ratio, source_event_id="SYNTHETIC-PRIVATE-RATIO")
    result = _extract(tmp_path, [action])

    group = result.groups[0]
    assert group.drafts == ()
    assert [reason.code for reason in group.reasons] == ["unrecognized_event"]
    assert action.source_event_id not in repr(result)


def test_maximum_ratio_precision_is_exact_and_changes_the_typed_fingerprint(tmp_path: Path) -> None:
    first = _extract(
        tmp_path,
        [_action("SPLIT", unit_ratio="9999999999.9999999999")],
        name="ratio-max",
        data_size=5.0,
    )
    second = _extract(tmp_path, [_action("SPLIT", unit_ratio="2.0000000000")], name="ratio-two")
    left = first.groups[0].drafts[0]
    right = second.groups[0].drafts[0]

    assert left.unit_ratio == Decimal("9999999999.9999999999")
    assert left.evidence.fingerprint_version == "typed_action_v1"
    assert left.evidence.fingerprint != right.evidence.fingerprint


def test_action_page_breaks_keep_ordered_evidence_and_private_grouping_ids(tmp_path: Path) -> None:
    private_group = "ＳＹＮＴＨＥＴＩＣ-GROUP-１２３４５６７８"
    private_sources = ("SYNTHETIC-ACTION-1234-5678-OUT", "SYNTHETIC-ACTION-1234-5678-IN")
    result = _extract(
        tmp_path,
        [
            _action(
                "PLATFORM_TRANSFER_OUT",
                units="-1.00000000",
                action_reference=private_group,
                source_event_id=private_sources[0],
            ),
            _action(
                "PLATFORM_TRANSFER_IN",
                units="1.00000000",
                action_reference=private_group,
                source_event_id=private_sources[1],
            ),
        ],
        action_rows_per_page=1,
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-A"),
            ("folio MASKED ****B2C3", "SYNTHETIC-SECURITY-A"),
        ],
    )

    drafts = [group.drafts[0] for group in result.groups]
    assert [draft.source_order for draft in drafts] == [1, 2]
    assert [draft.evidence.page_number for draft in drafts] == [1, 2]
    assert all(draft.evidence.row_number > 0 for draft in drafts)
    for private_value in (private_group, *private_sources):
        assert private_value not in repr(result)
        reasons = tuple(reason for group in result.groups for reason in group.reasons)
        assert private_value not in str(reasons)
        assert all(private_value not in draft.evidence.fingerprint for draft in drafts)
