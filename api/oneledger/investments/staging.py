"""Pure preparation of extracted investment evidence for later persistence."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .adapters import (
    InvestmentEventDraft,
    PositionCheckpointDraft,
    ReviewReason,
    SafeReason,
    SourceEventIdentity,
    StatementExtraction,
    StatementGroup,
)

PREVIEW_GROUP_LIMIT = 50

_BLOCKING_REASONS: frozenset[ReviewReason] = frozenset(
    {
        "unsupported_format",
        "unknown_security",
        "ambiguous_account",
        "ambiguous_identifier",
        "unrecognized_event",
        "duplicate_conflict",
        "unit_mismatch",
        "cost_mismatch",
    }
)
_RESOLUTION_ERROR = "target resolutions do not match the extraction"


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """An explicit UUID choice keyed only by the safe extraction group number."""

    source_group_number: int
    account_id: UUID
    security_id: UUID


@dataclass(frozen=True, slots=True)
class PreparedTarget:
    """One source target with its private source identity deliberately discarded."""

    source_group_number: int
    account_id: UUID | None
    security_id: UUID | None
    drafts: tuple[InvestmentEventDraft, ...]
    checkpoints: tuple[PositionCheckpointDraft, ...]
    reasons: tuple[SafeReason, ...]

    @property
    def resolved(self) -> bool:
        return self.account_id is not None and self.security_id is not None


@dataclass(frozen=True, slots=True)
class PreparedGroup:
    """One persistence unit, including every explicitly correlated target leg."""

    group_number: int
    targets: tuple[PreparedTarget, ...]
    reasons: tuple[SafeReason, ...]
    stageable: bool


@dataclass(frozen=True, slots=True)
class PreparedImport:
    """The full typed plan plus a bounded view for a later API preview."""

    supported: bool
    source_contract: str | None
    extraction_version: str | None
    groups: tuple[PreparedGroup, ...]
    reasons: tuple[SafeReason, ...]
    total_group_count: int
    total_draft_count: int
    total_blocker_count: int
    preview_truncated: bool
    stageable: bool

    @property
    def preview_groups(self) -> tuple[PreparedGroup, ...]:
        return self.groups[:PREVIEW_GROUP_LIMIT]


def _is_blocking(reason: SafeReason) -> bool:
    return reason.code in _BLOCKING_REASONS


def _correlations(group: StatementGroup) -> frozenset[SourceEventIdentity]:
    return frozenset(
        draft.evidence.source_correlation_id
        for draft in group.drafts
        if draft.evidence.source_correlation_id is not None
    )


def _components(groups: tuple[StatementGroup, ...]) -> tuple[tuple[StatementGroup, ...], ...]:
    """Join groups by exact opaque correlation identity, preserving source order."""
    parents = list(range(len(groups)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parents[right_root] = left_root

    owners: dict[SourceEventIdentity, int] = {}
    for index, group in enumerate(groups):
        for correlation in _correlations(group):
            prior = owners.setdefault(correlation, index)
            join(prior, index)

    members: dict[int, list[StatementGroup]] = {}
    order: list[int] = []
    for index, group in enumerate(groups):
        component = root(index)
        if component not in members:
            order.append(component)
            members[component] = []
        members[component].append(group)
    return tuple(tuple(members[component]) for component in order)


def _resolution_map(
    groups: tuple[StatementGroup, ...], resolutions: tuple[TargetResolution, ...]
) -> dict[int, TargetResolution]:
    group_numbers = {group.group_number for group in groups}
    resolved: dict[int, TargetResolution] = {}
    for resolution in resolutions:
        if (
            resolution.source_group_number <= 0
            or not isinstance(resolution.account_id, UUID)
            or not isinstance(resolution.security_id, UUID)
            or resolution.source_group_number not in group_numbers
            or resolution.source_group_number in resolved
        ):
            raise ValueError(_RESOLUTION_ERROR)
        resolved[resolution.source_group_number] = resolution
    return resolved


def _target(group: StatementGroup, resolutions: dict[int, TargetResolution]) -> PreparedTarget:
    resolution = resolutions.get(group.group_number)
    return PreparedTarget(
        source_group_number=group.group_number,
        account_id=None if resolution is None else resolution.account_id,
        security_id=None if resolution is None else resolution.security_id,
        drafts=group.drafts,
        checkpoints=group.checkpoints,
        reasons=group.reasons,
    )


def prepare_import(
    extraction: StatementExtraction,
    resolutions: tuple[TargetResolution, ...],
) -> PreparedImport:
    """Prepare exact extracted facts without storage, lookup or source reinterpretation."""
    groups = extraction.groups
    if len({group.group_number for group in groups}) != len(groups) or any(
        group.group_number <= 0 for group in groups
    ):
        raise ValueError(_RESOLUTION_ERROR)
    resolution_by_group = _resolution_map(groups, resolutions)
    prepared_groups: list[PreparedGroup] = []
    for group_number, component in enumerate(_components(groups), start=1):
        targets = tuple(_target(group, resolution_by_group) for group in component)
        reasons = tuple(reason for target in targets for reason in target.reasons)
        prepared_groups.append(
            PreparedGroup(
                group_number=group_number,
                targets=targets,
                reasons=reasons,
                stageable=all(target.resolved for target in targets)
                and not any(_is_blocking(reason) for reason in reasons),
            )
        )
    prepared = tuple(prepared_groups)
    global_blocked = any(_is_blocking(reason) for reason in extraction.reasons)
    blocked_groups = sum(
        any(_is_blocking(reason) for reason in group.reasons) for group in prepared
    )
    return PreparedImport(
        supported=extraction.supported,
        source_contract=extraction.source_contract,
        extraction_version=extraction.extraction_version,
        groups=prepared,
        reasons=extraction.reasons,
        total_group_count=len(prepared),
        total_draft_count=sum(len(target.drafts) for group in prepared for target in group.targets),
        total_blocker_count=blocked_groups + int(global_blocked),
        preview_truncated=len(prepared) > PREVIEW_GROUP_LIMIT,
        stageable=extraction.supported
        and bool(prepared)
        and not global_blocked
        and all(target.resolved for group in prepared for target in group.targets),
    )
