"""Pure preparation of extracted investment evidence for later persistence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import psycopg

from ..auth import OwnerSession
from ..db import connect, scope_to_owner
from ..identifiers import CompleteIdentifier, Identifier
from .adapters import (
    EvidenceDraft,
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
_INVALID_TARGET = "investment target is invalid"


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


type StagedImportStatus = Literal[
    "staged", "awaiting_review", "partially_committed", "completed", "cancelled", "failed"
]


class StagingError(Exception):
    """A fixed privacy-safe refusal from investment staging."""


@dataclass(frozen=True, slots=True)
class StagedImportResult:
    """Safe identity and state returned by persistence without source evidence values."""

    import_id: UUID
    status: StagedImportStatus
    group_ids: tuple[UUID, ...]
    created: bool


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
    blocker_count = sum(_is_blocking(reason) for reason in extraction.reasons) + sum(
        _is_blocking(reason) for group in prepared for reason in group.reasons
    )
    return PreparedImport(
        supported=extraction.supported,
        source_contract=extraction.source_contract,
        extraction_version=extraction.extraction_version,
        groups=prepared,
        reasons=extraction.reasons,
        total_group_count=len(prepared),
        total_draft_count=sum(len(target.drafts) for group in prepared for target in group.targets),
        total_blocker_count=blocker_count,
        preview_truncated=len(prepared) > PREVIEW_GROUP_LIMIT,
        stageable=extraction.supported
        and bool(prepared)
        and not global_blocked
        and all(target.resolved for group in prepared for target in group.targets),
    )


def resolve_existing_investment_account(owner: OwnerSession, identifier: Identifier) -> UUID | None:
    """Reuse only one active household account named by complete normalized evidence."""
    if not isinstance(identifier, CompleteIdentifier):
        return None
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT account.id
                 FROM account_identifier identity
                 JOIN account ON account.household_id = identity.household_id
                             AND account.id = identity.account_id
                WHERE identity.household_id = %s AND identity.kind = %s
                  AND identity.normalized_value = %s AND identity.is_active
                  AND account.is_active
                  AND account.account_type IN ('brokerage', 'mutual_fund_folio')
                ORDER BY account.id""",
            (owner.household_id, identifier.kind, identifier.normalized_value),
        )
        rows = cur.fetchall()
    return UUID(str(rows[0][0])) if len(rows) == 1 else None


def _load_result(
    cur: psycopg.Cursor[Any], household_id: UUID, import_id: UUID, *, created: bool
) -> StagedImportResult:
    cur.execute(
        """SELECT status::text FROM investment_import
             WHERE household_id = %s AND id = %s""",
        (household_id, import_id),
    )
    row = cur.fetchone()
    if row is None:
        raise StagingError("investment import not found")
    cur.execute(
        """SELECT id FROM investment_import_group
             WHERE household_id = %s AND import_id = %s ORDER BY group_number""",
        (household_id, import_id),
    )
    return StagedImportResult(
        import_id,
        row[0],
        tuple(UUID(str(group[0])) for group in cur.fetchall()),
        created,
    )


def _validate_target(cur: psycopg.Cursor[Any], household_id: UUID, target: PreparedTarget) -> None:
    if target.account_id is None or target.security_id is None:
        raise StagingError("investment targets are unresolved")
    cur.execute(
        """SELECT currency::text FROM account
             WHERE household_id = %s AND id = %s AND is_active
               AND account_type IN ('brokerage', 'mutual_fund_folio')
             FOR KEY SHARE""",
        (household_id, target.account_id),
    )
    account = cur.fetchone()
    cur.execute(
        """SELECT currency::text FROM security
             WHERE id = %s AND (household_id IS NULL OR household_id = %s) AND is_active
             FOR KEY SHARE""",
        (target.security_id, household_id),
    )
    security = cur.fetchone()
    fact_currencies = {draft.currency for draft in target.drafts} | {
        checkpoint.currency for checkpoint in target.checkpoints if checkpoint.currency is not None
    }
    if (
        account is None
        or security is None
        or str(account[0]).strip() != str(security[0]).strip()
        or (fact_currencies and fact_currencies != {str(account[0]).strip()})
    ):
        raise StagingError(_INVALID_TARGET)


def _committed_overlap(
    cur: psycopg.Cursor[Any], household_id: UUID, source_contract: str, draft: InvestmentEventDraft
) -> tuple[Any, ...] | None:
    evidence = draft.evidence
    if evidence.source_event_id is not None:
        predicate = "evidence.source_event_id = %s"
        params: tuple[object, ...] = (
            household_id,
            source_contract,
            evidence.source_event_id.value,
        )
    else:
        predicate = "evidence.fingerprint_version = %s AND evidence.evidence_fingerprint = %s"
        params = (
            household_id,
            source_contract,
            evidence.fingerprint_version,
            evidence.fingerprint,
        )
    cur.execute(
        f"""SELECT event.id, event.account_id, event.security_id, event.event_date,
                   event.event_kind::text, event.units, event.unit_ratio, event.price,
                   event.gross_amount, event.charges, event.withheld_tax, event.net_amount,
                   event.currency::text
              FROM investment_evidence evidence
              JOIN investment_transaction event
                ON event.household_id = evidence.household_id
               AND event.id = evidence.investment_transaction_id
             WHERE evidence.household_id = %s AND evidence.source_contract = %s
               AND {predicate}
             ORDER BY evidence.created_at, evidence.id LIMIT 1""",  # noqa: S608 - fixed predicates
        params,
    )
    return cur.fetchone()


def _overlap_is_exact(
    overlap: tuple[Any, ...], target: PreparedTarget, draft: InvestmentEventDraft
) -> bool:
    return (
        UUID(str(overlap[1])),
        UUID(str(overlap[2])),
        overlap[3],
        overlap[4],
        overlap[5],
        overlap[6],
        overlap[7],
        overlap[8],
        overlap[9],
        overlap[10],
        overlap[11],
        str(overlap[12]).strip(),
    ) == (
        target.account_id,
        target.security_id,
        draft.event_date,
        draft.event_kind,
        draft.units,
        draft.unit_ratio,
        draft.price,
        draft.gross_amount,
        draft.charges,
        draft.withheld_tax,
        draft.net_amount,
        draft.currency,
    )


def _draft_signature(target: PreparedTarget, draft: InvestmentEventDraft) -> tuple[Any, ...]:
    evidence = draft.evidence
    return (
        target.account_id,
        target.security_id,
        draft.event_date,
        draft.event_kind,
        draft.units,
        draft.unit_ratio,
        draft.price,
        draft.gross_amount,
        draft.charges,
        draft.withheld_tax,
        draft.net_amount,
        draft.currency,
        None if evidence.source_event_id is None else evidence.source_event_id.value,
        evidence.fingerprint_version,
        evidence.fingerprint,
    )


def _checkpoint_signature(
    target: PreparedTarget, checkpoint: PositionCheckpointDraft
) -> tuple[Any, ...]:
    evidence = checkpoint.evidence
    return (
        target.account_id,
        target.security_id,
        checkpoint.as_of_date,
        checkpoint.reported_units,
        checkpoint.reported_cost_native,
        checkpoint.reported_value_native,
        checkpoint.currency,
        checkpoint.declared_unit_scale,
        None if evidence.source_event_id is None else evidence.source_event_id.value,
        evidence.fingerprint_version,
        evidence.fingerprint,
    )


def _retry_matches(
    cur: psycopg.Cursor[Any], household_id: UUID, import_id: UUID, prepared: PreparedImport
) -> bool:
    cur.execute(
        """SELECT id, group_number FROM investment_import_group
             WHERE household_id = %s AND import_id = %s ORDER BY group_number""",
        (household_id, import_id),
    )
    stored_groups = cur.fetchall()
    if [row[1] for row in stored_groups] != [group.group_number for group in prepared.groups]:
        return False
    assert prepared.source_contract is not None
    for stored, group in zip(stored_groups, prepared.groups, strict=True):
        group_id = stored[0]
        expected_events: list[tuple[Any, ...]] = []
        expected_reasons = {reason.code for reason in group.reasons}
        for target in group.targets:
            for draft in target.drafts:
                overlap = _committed_overlap(cur, household_id, prepared.source_contract, draft)
                if overlap is not None and not _overlap_is_exact(overlap, target, draft):
                    expected_reasons.add("duplicate_conflict")
                else:
                    expected_events.append(_draft_signature(target, draft))
        cur.execute(
            """SELECT coalesce(d.account_id, event.account_id),
                      coalesce(d.security_id, event.security_id),
                      coalesce(d.event_date, event.event_date),
                      coalesce(d.event_kind, event.event_kind)::text,
                      coalesce(d.units, event.units), coalesce(d.unit_ratio, event.unit_ratio),
                      coalesce(d.price, event.price), coalesce(d.gross_amount, event.gross_amount),
                      coalesce(d.charges, event.charges),
                      coalesce(d.withheld_tax, event.withheld_tax),
                      coalesce(d.net_amount, event.net_amount),
                      rtrim(coalesce(d.currency, event.currency)), evidence.source_event_id,
                      evidence.fingerprint_version, evidence.evidence_fingerprint
                 FROM investment_evidence evidence
                 LEFT JOIN investment_event_draft d ON d.id = evidence.draft_id
                 LEFT JOIN investment_transaction event
                   ON event.id = evidence.investment_transaction_id
                WHERE evidence.household_id = %s AND evidence.group_id = %s
                  AND evidence.checkpoint_id IS NULL""",
            (household_id, group_id),
        )
        if Counter(cur.fetchall()) != Counter(expected_events):
            return False
        expected_checkpoints = Counter(
            _checkpoint_signature(target, checkpoint)
            for target in group.targets
            for checkpoint in target.checkpoints
        )
        cur.execute(
            """SELECT checkpoint.account_id, checkpoint.security_id, checkpoint.as_of_date,
                      checkpoint.reported_units, checkpoint.reported_cost_native,
                      checkpoint.reported_value_native, rtrim(checkpoint.currency),
                      checkpoint.declared_unit_scale, evidence.source_event_id,
                      evidence.fingerprint_version, evidence.evidence_fingerprint
                 FROM investment_evidence evidence
                 JOIN investment_position_checkpoint checkpoint
                   ON checkpoint.id = evidence.checkpoint_id
                WHERE evidence.household_id = %s AND evidence.group_id = %s""",
            (household_id, group_id),
        )
        if Counter(cur.fetchall()) != expected_checkpoints:
            return False
        cur.execute(
            """SELECT reason::text FROM investment_review_item
                 WHERE household_id = %s AND import_id = %s AND group_id = %s
                   AND status = 'open'""",
            (household_id, import_id, group_id),
        )
        if {row[0] for row in cur.fetchall()} != expected_reasons:
            return False
    return True


def _record_evidence(
    cur: psycopg.Cursor[Any],
    group_id: UUID,
    document_id: UUID,
    source_contract: str,
    extraction_version: str,
    evidence: EvidenceDraft,
    *,
    draft_id: UUID | None = None,
    checkpoint_id: UUID | None = None,
    event_id: UUID | None = None,
) -> None:
    cur.execute(
        """SELECT record_investment_evidence(
             %s, %s, %s, %s, %s, %s::SMALLINT, %s, %s, %s, %s, %s, %s)""",
        (
            group_id,
            document_id,
            draft_id,
            checkpoint_id,
            event_id,
            evidence.page_number,
            evidence.row_number,
            source_contract,
            extraction_version,
            None if evidence.source_event_id is None else evidence.source_event_id.value,
            evidence.fingerprint_version,
            evidence.fingerprint,
        ),
    )


def _insert_review(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    import_id: UUID,
    group_id: UUID,
    reason: ReviewReason,
) -> None:
    cur.execute(
        """INSERT INTO investment_review_item (household_id, import_id, group_id, reason)
           VALUES (%s, %s, %s, %s::investment_review_reason) ON CONFLICT DO NOTHING""",
        (household_id, import_id, group_id, reason),
    )


def _insert_draft(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    group_id: UUID,
    target: PreparedTarget,
    draft: InvestmentEventDraft,
    *,
    blocked: bool,
) -> UUID:
    cur.execute(
        """INSERT INTO investment_event_draft (
             household_id, group_id, source_order, account_id, security_id, event_date,
             event_kind, units, unit_ratio, price, gross_amount, charges, withheld_tax,
             net_amount, currency, state)
           VALUES (%s, %s, %s, %s, %s, %s, %s::investment_event_kind, %s, %s, %s,
                   %s, %s, %s, %s, %s, %s::investment_draft_status) RETURNING id""",
        (
            household_id,
            group_id,
            draft.source_order,
            target.account_id,
            target.security_id,
            draft.event_date,
            draft.event_kind,
            draft.units,
            draft.unit_ratio,
            draft.price,
            draft.gross_amount,
            draft.charges,
            draft.withheld_tax,
            draft.net_amount,
            draft.currency,
            "blocked" if blocked else "ready",
        ),
    )
    return UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def _insert_checkpoint(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    group_id: UUID,
    target: PreparedTarget,
    checkpoint: PositionCheckpointDraft,
    *,
    blocked: bool,
) -> UUID:
    cur.execute(
        """INSERT INTO investment_position_checkpoint (
             household_id, group_id, source_order, account_id, security_id, as_of_date,
             reported_units, reported_cost_native, reported_value_native, currency,
             declared_unit_scale, reconciliation_status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   %s::investment_checkpoint_status) RETURNING id""",
        (
            household_id,
            group_id,
            checkpoint.source_order,
            target.account_id,
            target.security_id,
            checkpoint.as_of_date,
            checkpoint.reported_units,
            checkpoint.reported_cost_native,
            checkpoint.reported_value_native,
            checkpoint.currency,
            checkpoint.declared_unit_scale,
            "blocked" if blocked else "pending",
        ),
    )
    return UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def stage_prepared_import(
    owner: OwnerSession, document_id: UUID, prepared: PreparedImport
) -> StagedImportResult:
    """Persist one prepared import atomically without creating financial facts."""
    if (
        not prepared.supported
        or prepared.source_contract is None
        or prepared.extraction_version is None
    ):
        raise StagingError("investment extraction is unsupported")
    if not prepared.groups or any(
        not target.resolved for group in prepared.groups for target in group.targets
    ):
        raise StagingError("investment targets are unresolved")
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            if cur.fetchone() is None:
                raise StagingError("investment import not found")
            cur.execute(
                """SELECT document_type::text FROM document
                     WHERE household_id = %s AND id = %s FOR KEY SHARE""",
                (owner.household_id, document_id),
            )
            document = cur.fetchone()
            if document is None or document[0] not in {"investment_statement", "cas"}:
                raise StagingError("investment document not found")
            for group in prepared.groups:
                for target in group.targets:
                    _validate_target(cur, owner.household_id, target)
            cur.execute(
                """SELECT id, source_contract, extraction_version, status::text
                     FROM investment_import
                    WHERE household_id = %s AND document_id = %s FOR UPDATE""",
                (owner.household_id, document_id),
            )
            existing = cur.fetchone()
            if existing is not None:
                if existing[3] in {"cancelled", "failed", "completed", "partially_committed"}:
                    raise StagingError("investment import is terminal")
                if (existing[1], existing[2]) != (
                    prepared.source_contract,
                    prepared.extraction_version,
                ) or not _retry_matches(cur, owner.household_id, UUID(str(existing[0])), prepared):
                    raise StagingError("investment import retry conflicts")
                return _load_result(cur, owner.household_id, UUID(str(existing[0])), created=False)

            cur.execute(
                """INSERT INTO investment_import (
                     household_id, document_id, source_contract, extraction_version)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    document_id,
                    prepared.source_contract,
                    prepared.extraction_version,
                ),
            )
            import_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
            for group in prepared.groups:
                cur.execute(
                    """INSERT INTO investment_import_group (
                         household_id, import_id, group_number, status)
                       VALUES (%s, %s, %s, 'ready') RETURNING id""",
                    (owner.household_id, import_id, group.group_number),
                )
                group_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
                for reason in group.reasons:
                    _insert_review(cur, owner.household_id, import_id, group_id, reason.code)
                blocked = not group.stageable
                for target in group.targets:
                    for draft in target.drafts:
                        overlap = _committed_overlap(
                            cur, owner.household_id, prepared.source_contract, draft
                        )
                        if overlap is not None:
                            if _overlap_is_exact(overlap, target, draft):
                                _record_evidence(
                                    cur,
                                    group_id,
                                    document_id,
                                    prepared.source_contract,
                                    prepared.extraction_version,
                                    draft.evidence,
                                    event_id=UUID(str(overlap[0])),
                                )
                            else:
                                _insert_review(
                                    cur,
                                    owner.household_id,
                                    import_id,
                                    group_id,
                                    "duplicate_conflict",
                                )
                            continue
                        draft_id = _insert_draft(
                            cur, owner.household_id, group_id, target, draft, blocked=blocked
                        )
                        _record_evidence(
                            cur,
                            group_id,
                            document_id,
                            prepared.source_contract,
                            prepared.extraction_version,
                            draft.evidence,
                            draft_id=draft_id,
                        )
                    for checkpoint in target.checkpoints:
                        checkpoint_id = _insert_checkpoint(
                            cur, owner.household_id, group_id, target, checkpoint, blocked=blocked
                        )
                        _record_evidence(
                            cur,
                            group_id,
                            document_id,
                            prepared.source_contract,
                            prepared.extraction_version,
                            checkpoint.evidence,
                            checkpoint_id=checkpoint_id,
                        )
            return _load_result(cur, owner.household_id, import_id, created=True)
    except psycopg.Error:
        raise StagingError("investment import could not be staged") from None


def cancel_staged_import(owner: OwnerSession, import_id: UUID) -> StagedImportResult:
    """Cancel once, retain provenance, and refuse every terminal reopen."""
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT status::text FROM investment_import
                     WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, import_id),
            )
            row = cur.fetchone()
            if row is None:
                raise StagingError("investment import not found")
            if row[0] == "cancelled":
                return _load_result(cur, owner.household_id, import_id, created=False)
            if row[0] in {"completed", "partially_committed", "failed"}:
                raise StagingError("investment import is terminal")
            cur.execute(
                """UPDATE investment_import_group SET status = 'cancelled', updated_at = now()
                     WHERE household_id = %s AND import_id = %s
                       AND status NOT IN ('committed', 'cancelled')""",
                (owner.household_id, import_id),
            )
            cur.execute(
                """UPDATE investment_import
                      SET status = 'cancelled', updated_at = now(), completed_at = NULL
                    WHERE household_id = %s AND id = %s""",
                (owner.household_id, import_id),
            )
            return _load_result(cur, owner.household_id, import_id, created=False)
    except psycopg.Error:
        raise StagingError("investment import could not be cancelled") from None
