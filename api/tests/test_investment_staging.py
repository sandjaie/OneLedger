"""Pure preparation contracts for Phase 1.6 Task 12.1."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from oneledger.identifiers import masked_identifier
from oneledger.investments.adapters import (
    EvidenceDraft,
    InvestmentEventDraft,
    PositionCheckpointDraft,
    SafeReason,
    SourceEventIdentity,
    SourceTarget,
    StatementExtraction,
    StatementGroup,
)
from oneledger.investments.staging import (
    PreparedGroup,
    PreparedImport,
    PreparedTarget,
    TargetResolution,
    prepare_import,
)


def _uuid(number: int) -> UUID:
    return UUID(int=number)


def _evidence(
    order: int,
    *,
    correlation: SourceEventIdentity | None = None,
    source_id: str | None = None,
) -> EvidenceDraft:
    return EvidenceDraft(
        page_number=1,
        row_number=order,
        source_event_id=None if source_id is None else SourceEventIdentity(source_id),
        fingerprint_version="typed_event_v1",
        fingerprint=f"{order:064x}",
        source_correlation_id=correlation,
    )


def _draft(
    order: int,
    *,
    kind: str = "purchase",
    units: str = "1.23456789",
    correlation: SourceEventIdentity | None = None,
) -> InvestmentEventDraft:
    return InvestmentEventDraft(
        source_order=order,
        event_date=date(2026, 1, 1) + timedelta(days=order - 1),
        event_kind=kind,  # type: ignore[arg-type]
        units=Decimal(units),
        unit_ratio=None,
        price=Decimal("10.123456"),
        gross_amount=Decimal("12.4999"),
        charges=Decimal("0.0001"),
        withheld_tax=Decimal("0.0000"),
        net_amount=Decimal("-12.5000"),
        currency="XAA",
        evidence=_evidence(
            order,
            correlation=correlation,
            source_id=f"SYNTHETIC-EVENT-{order}",
        ),
    )


def _checkpoint(order: int) -> PositionCheckpointDraft:
    return PositionCheckpointDraft(
        source_order=order,
        as_of_date=date(2026, 1, 31),
        reported_units=Decimal("1.23456789"),
        reported_cost_native=Decimal("12.5000"),
        reported_value_native=Decimal("13.0000"),
        currency="XAA",
        declared_unit_scale=8,
        evidence=_evidence(order),
    )


def _group(
    number: int,
    *,
    drafts: tuple[InvestmentEventDraft, ...] | None = None,
    checkpoints: tuple[PositionCheckpointDraft, ...] = (),
    reasons: tuple[SafeReason, ...] = (),
    private_security: str | None = None,
) -> StatementGroup:
    return StatementGroup(
        group_number=number,
        target=SourceTarget(
            account_identifier=masked_identifier("folio", f"****A{number:03d}"),
            security_reference=private_security or f"SYNTHETIC-PRIVATE-SECURITY-{number}",
        ),
        drafts=(_draft(number),) if drafts is None else drafts,
        checkpoints=checkpoints,
        reasons=reasons,
    )


def _extraction(
    groups: tuple[StatementGroup, ...],
    *,
    supported: bool = True,
    reasons: tuple[SafeReason, ...] = (),
) -> StatementExtraction:
    return StatementExtraction(
        supported=supported,
        source_contract="detailed_fund_statement_v1" if supported else None,
        extraction_version="3" if supported else None,
        groups=groups,
        reasons=reasons,
    )


def _resolution(group_number: int) -> TargetResolution:
    return TargetResolution(
        source_group_number=group_number,
        account_id=_uuid(100 + group_number),
        security_id=_uuid(200 + group_number),
    )


def test_resolved_preparation_preserves_exact_typed_facts_without_source_targets() -> None:
    """Rebuilding facts or retaining SourceTarget would alter evidence or leak private text."""
    private_security = "SYNTHETIC-PRIVATE-SECURITY-EVIDENCE"
    draft = _draft(1)
    checkpoint = _checkpoint(2)

    prepared = prepare_import(
        _extraction(
            (
                _group(
                    1,
                    drafts=(draft,),
                    checkpoints=(checkpoint,),
                    private_security=private_security,
                ),
            )
        ),
        (_resolution(1),),
    )

    assert prepared.supported is True
    assert prepared.source_contract == "detailed_fund_statement_v1"
    assert prepared.extraction_version == "3"
    assert prepared.stageable is True
    assert prepared.total_group_count == 1
    assert prepared.total_draft_count == 1
    assert prepared.total_blocker_count == 0
    assert prepared.preview_truncated is False
    assert prepared.preview_groups == prepared.groups
    target = prepared.groups[0].targets[0]
    assert target.source_group_number == 1
    assert target.account_id == _uuid(101)
    assert target.security_id == _uuid(201)
    assert target.drafts[0] is draft
    assert target.checkpoints[0] is checkpoint
    assert target.drafts[0].units == Decimal("1.23456789")
    assert target.drafts[0].price == Decimal("10.123456")
    assert target.checkpoints[0].reported_value_native == Decimal("13.0000")
    assert private_security not in repr(target)
    assert private_security not in repr(prepared.groups[0])
    assert private_security not in repr(prepared)


def test_resolution_and_prepared_contracts_are_frozen_and_contain_no_source_value_field() -> None:
    """Adding source evidence to the resolution contract would expose it to later request bodies."""
    resolution = _resolution(1)
    prepared = prepare_import(_extraction((_group(1),)), (resolution,))

    assert {item.name for item in fields(TargetResolution)} == {
        "source_group_number",
        "account_id",
        "security_id",
    }
    assert all(
        "SourceTarget" not in repr(item)
        for item in (
            prepared,
            prepared.groups[0],
            prepared.groups[0].targets[0],
        )
    )
    with pytest.raises(FrozenInstanceError):
        resolution.account_id = _uuid(999)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        prepared.groups[0].targets[0].security_id = _uuid(999)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        prepared.groups[0].stageable = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        prepared.stageable = False  # type: ignore[misc]


def test_missing_resolution_preserves_preview_but_cannot_be_stageable() -> None:
    """Treating absent UUID choices as resolved would let ambiguous source targets reach storage."""
    prepared = prepare_import(_extraction((_group(1), _group(2))), (_resolution(1),))

    assert prepared.stageable is False
    assert [group.stageable for group in prepared.groups] == [True, False]
    unresolved = prepared.groups[1].targets[0]
    assert unresolved.source_group_number == 2
    assert unresolved.account_id is None
    assert unresolved.security_id is None
    assert unresolved.drafts[0].source_order == 2
    assert prepared.total_group_count == 2
    assert prepared.total_draft_count == 2
    assert prepared.total_blocker_count == 0


@pytest.mark.parametrize(
    "resolutions",
    [
        (_resolution(2),),
        (_resolution(1), _resolution(1)),
        (TargetResolution(0, _uuid(101), _uuid(201)),),
    ],
)
def test_unknown_duplicate_or_nonpositive_resolution_keys_are_refused_safely(
    resolutions: tuple[TargetResolution, ...],
) -> None:
    """Ignoring malformed keys would silently bind a selection to the wrong extracted target."""
    with pytest.raises(ValueError, match="^target resolutions do not match the extraction$") as exc:
        prepare_import(_extraction((_group(1),)), resolutions)

    assert "SYNTHETIC" not in str(exc.value)


def test_explicit_correlation_coalesces_two_legs_and_retains_each_resolved_target() -> None:
    """Grouping only by SourceTarget would let persistence stage paired legs independently."""
    private_correlation = "SYNTHETIC-PRIVATE-CORRELATION"
    correlation = SourceEventIdentity(private_correlation)
    extraction = _extraction(
        (
            _group(
                1,
                drafts=(
                    _draft(
                        1,
                        kind="platform_transfer_out",
                        units="-2.00000000",
                        correlation=correlation,
                    ),
                ),
            ),
            _group(
                2,
                drafts=(
                    _draft(
                        2,
                        kind="platform_transfer_in",
                        units="2.00000000",
                        correlation=correlation,
                    ),
                ),
            ),
        )
    )

    prepared = prepare_import(extraction, (_resolution(2), _resolution(1)))

    assert prepared.total_group_count == 1
    assert len(prepared.groups) == 1
    atomic = prepared.groups[0]
    assert atomic.group_number == 1
    assert atomic.stageable is True
    assert [target.source_group_number for target in atomic.targets] == [1, 2]
    assert [target.account_id for target in atomic.targets] == [_uuid(101), _uuid(102)]
    assert [target.security_id for target in atomic.targets] == [_uuid(201), _uuid(202)]
    assert [target.drafts[0].event_kind for target in atomic.targets] == [
        "platform_transfer_out",
        "platform_transfer_in",
    ]
    assert private_correlation not in repr(atomic)
    assert private_correlation not in repr(prepared)


def test_correlated_blocker_blocks_the_pair_while_an_unrelated_group_remains_usable() -> None:
    """Failing to propagate a leg blocker would make half of one source action stageable."""
    correlation = SourceEventIdentity("SYNTHETIC-CORRELATED-BLOCKER")
    extraction = _extraction(
        (
            _group(1, drafts=(_draft(1, correlation=correlation),)),
            _group(
                2,
                drafts=(_draft(2, correlation=correlation),),
                reasons=(SafeReason("unrecognized_event", 1, 2),),
            ),
            _group(3),
        )
    )

    prepared = prepare_import(extraction, tuple(_resolution(number) for number in (1, 2, 3)))

    assert prepared.stageable is True
    assert prepared.total_group_count == 2
    assert prepared.total_draft_count == 3
    assert prepared.total_blocker_count == 1
    correlated, unrelated = prepared.groups
    assert correlated.stageable is False
    assert [target.source_group_number for target in correlated.targets] == [1, 2]
    assert [reason.code for reason in correlated.reasons] == ["unrecognized_event"]
    assert unrelated.stageable is True
    assert unrelated.targets[0].source_group_number == 3


def test_nonblocking_reason_is_preserved_without_blocking_a_resolved_group() -> None:
    """Treating every review reason as blocking would stop usable facts with advisory evidence."""
    prepared = prepare_import(
        _extraction((_group(1, reasons=(SafeReason("unmatched_cash", 1, 1),)),)),
        (_resolution(1),),
    )

    assert prepared.stageable is True
    assert prepared.groups[0].stageable is True
    assert prepared.groups[0].reasons == (SafeReason("unmatched_cash", 1, 1),)
    assert prepared.total_blocker_count == 0


def test_component_and_target_order_are_deterministic_when_resolutions_are_reordered() -> None:
    """Mapping input order must not change replay's database group numbers."""
    extraction = _extraction((_group(3), _group(1), _group(2)))

    first = prepare_import(extraction, (_resolution(3), _resolution(1), _resolution(2)))
    second = prepare_import(extraction, (_resolution(2), _resolution(3), _resolution(1)))

    assert first == second
    assert [group.targets[0].source_group_number for group in first.groups] == [3, 1, 2]
    assert [group.group_number for group in first.groups] == [1, 2, 3]


def test_preview_is_bounded_without_discarding_the_full_prepared_plan() -> None:
    """Returning every group could make a large statement response unbounded."""
    extraction = _extraction(tuple(_group(number) for number in range(1, 53)))
    resolutions = tuple(_resolution(number) for number in range(1, 53))

    prepared = prepare_import(extraction, resolutions)

    assert len(prepared.groups) == 52
    assert len(prepared.preview_groups) == 50
    assert [group.group_number for group in prepared.preview_groups] == list(range(1, 51))
    assert prepared.total_group_count == 52
    assert prepared.total_draft_count == 52
    assert prepared.total_blocker_count == 0
    assert prepared.preview_truncated is True
    assert prepared.stageable is True


def test_unsupported_extraction_keeps_its_fixed_reason_and_never_becomes_stageable() -> None:
    """An empty unsupported result must not look like a resolved empty import."""
    prepared = prepare_import(
        _extraction(
            (),
            supported=False,
            reasons=(SafeReason("unsupported_format"),),
        ),
        (),
    )

    assert prepared == PreparedImport(
        supported=False,
        source_contract=None,
        extraction_version=None,
        groups=(),
        reasons=(SafeReason("unsupported_format"),),
        total_group_count=0,
        total_draft_count=0,
        total_blocker_count=1,
        preview_truncated=False,
        stageable=False,
    )
    assert prepared.preview_groups == ()


def test_prepared_contract_annotations_keep_exact_source_types() -> None:
    """Serialized dictionaries would invite float and date reinterpretation."""
    assert {item.name for item in fields(PreparedTarget)} == {
        "source_group_number",
        "account_id",
        "security_id",
        "drafts",
        "checkpoints",
        "reasons",
    }
    assert {item.name for item in fields(PreparedGroup)} == {
        "group_number",
        "targets",
        "reasons",
        "stageable",
    }
    assert {item.name for item in fields(PreparedImport)} == {
        "supported",
        "source_contract",
        "extraction_version",
        "groups",
        "reasons",
        "total_group_count",
        "total_draft_count",
        "total_blocker_count",
        "preview_truncated",
        "stageable",
    }
