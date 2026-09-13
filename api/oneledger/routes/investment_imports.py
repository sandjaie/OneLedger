"""Owner preview, staging, replay and cancellation for investment statements."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, cast
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..ingest import storage
from ..ingest.uploads import uploaded_pages
from ..ingest.words import ExtractionError
from ..investments.adapters import InvestmentEventKind, ReviewReason, SafeReason, extract_statement
from ..investments.commands import InvestmentCommandError
from ..investments.commit import CommitOutcome, CommittedGroupOutcome, commit_staged_import
from ..investments.staging import (
    PreparedImport,
    PreparedTarget,
    StagedImportResult,
    StagedImportStatus,
    StagingError,
    TargetResolution,
    _expected_state,
    cancel_staged_import,
    is_blocking_reason,
    prepare_import,
    stage_prepared_import_in_transaction,
    validate_prepared_import,
)

router = APIRouter(prefix="/investment-imports", tags=["investment imports"])
_SAFE_DOCUMENT_LABEL = "investment-statement.pdf"


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sourceGroupNumber: Annotated[StrictInt, Field(gt=0)]
    accountId: UUID
    securityId: UUID


class ReasonResponse(BaseModel):
    code: ReviewReason
    pageNumber: int | None
    rowNumber: int | None


class DraftResponse(BaseModel):
    eventDate: date
    eventKind: InvestmentEventKind
    units: str | None
    unitRatio: str | None
    price: str | None
    grossAmount: str | None
    charges: str
    withheldTax: str
    netAmount: str
    currency: str
    pageNumber: int
    rowNumber: int


class CheckpointResponse(BaseModel):
    asOfDate: date
    reportedUnits: str | None
    reportedCostNative: str | None
    reportedValueNative: str | None
    currency: str | None
    declaredUnitScale: int | None
    pageNumber: int
    rowNumber: int


class TargetResponse(BaseModel):
    sourceGroupNumber: int
    accountId: UUID | None
    securityId: UUID | None
    accountMask: str | None
    securityLabel: str | None
    isin: str | None
    securityCandidates: list[UUID]
    drafts: list[DraftResponse]
    checkpoints: list[CheckpointResponse]
    reasons: list[ReasonResponse]


class GroupResponse(BaseModel):
    groupNumber: int
    stageable: bool
    targets: list[TargetResponse]
    reasons: list[ReasonResponse]


class InvestmentParseResponse(BaseModel):
    supported: bool
    staged: bool
    importId: UUID | None
    status: Literal[
        "preview",
        "staged",
        "awaiting_review",
        "partially_committed",
        "completed",
        "cancelled",
        "failed",
    ]
    groups: list[GroupResponse]
    reasons: list[ReasonResponse]
    totalGroupCount: int
    totalDraftCount: int
    totalBlockerCount: int
    previewTruncated: bool
    expectedState: str | None


class InvestmentCancelResponse(BaseModel):
    importId: UUID
    status: Literal["cancelled"]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _reason(reason: SafeReason) -> ReasonResponse:
    return ReasonResponse(
        code=reason.code,
        pageNumber=reason.page_number,
        rowNumber=reason.row_number,
    )


def _target(
    target: PreparedTarget,
    masks: dict[UUID, str],
    labels: dict[UUID, tuple[str, str | None]],
) -> TargetResponse:
    label = None if target.security_id is None else labels.get(target.security_id)
    return TargetResponse(
        sourceGroupNumber=target.source_group_number,
        accountId=target.account_id,
        securityId=target.security_id,
        accountMask=None if target.account_id is None else masks.get(target.account_id),
        securityLabel=None if label is None else label[0],
        isin=None if label is None else label[1],
        # Extraction does not yet carry a safe ISIN to match household securities against, so
        # candidate suggestion is deferred rather than reinterpreting private statement text;
        # see the Task 16 commit deviation note in progress.md.
        securityCandidates=[],
        drafts=[
            DraftResponse(
                eventDate=draft.event_date,
                eventKind=draft.event_kind,
                units=_decimal(draft.units),
                unitRatio=_decimal(draft.unit_ratio),
                price=_decimal(draft.price),
                grossAmount=_decimal(draft.gross_amount),
                charges=str(draft.charges),
                withheldTax=str(draft.withheld_tax),
                netAmount=str(draft.net_amount),
                currency=draft.currency,
                pageNumber=draft.evidence.page_number,
                rowNumber=draft.evidence.row_number,
            )
            for draft in target.drafts
        ],
        checkpoints=[
            CheckpointResponse(
                asOfDate=checkpoint.as_of_date,
                reportedUnits=_decimal(checkpoint.reported_units),
                reportedCostNative=_decimal(checkpoint.reported_cost_native),
                reportedValueNative=_decimal(checkpoint.reported_value_native),
                currency=checkpoint.currency,
                declaredUnitScale=checkpoint.declared_unit_scale,
                pageNumber=checkpoint.evidence.page_number,
                rowNumber=checkpoint.evidence.row_number,
            )
            for checkpoint in target.checkpoints
        ],
        reasons=[_reason(reason) for reason in target.reasons],
    )


def _response(
    prepared: PreparedImport,
    masks: dict[UUID, str],
    labels: dict[UUID, tuple[str, str | None]],
    staged: StagedImportResult | None = None,
) -> InvestmentParseResponse:
    persisted = (
        {} if staged is None else {state.group_number: state for state in staged.group_states}
    )
    added_blockers = 0
    added_reasons_by_group: dict[int, tuple[ReviewReason, ...]] = {}
    for group in prepared.groups:
        state = persisted.get(group.group_number)
        prepared_codes = {reason.code for reason in group.reasons}
        added_reasons = (
            ()
            if state is None
            else tuple(reason for reason in state.reasons if reason not in prepared_codes)
        )
        added_reasons_by_group[group.group_number] = added_reasons
        added_blockers += sum(is_blocking_reason(reason) for reason in added_reasons)
    groups: list[GroupResponse] = []
    for group in prepared.preview_groups:
        state = persisted.get(group.group_number)
        added_reasons = added_reasons_by_group[group.group_number]
        groups.append(
            GroupResponse(
                groupNumber=group.group_number,
                stageable=group.stageable if state is None else state.status == "ready",
                targets=[_target(target, masks, labels) for target in group.targets],
                reasons=[_reason(reason) for reason in group.reasons]
                + [
                    ReasonResponse(code=reason, pageNumber=None, rowNumber=None)
                    for reason in added_reasons
                ],
            )
        )
    return InvestmentParseResponse(
        supported=prepared.supported,
        staged=staged is not None,
        importId=None if staged is None else staged.import_id,
        status="preview" if staged is None else staged.status,
        groups=groups,
        reasons=[_reason(reason) for reason in prepared.reasons],
        totalGroupCount=prepared.total_group_count,
        totalDraftCount=prepared.total_draft_count,
        totalBlockerCount=prepared.total_blocker_count + added_blockers,
        previewTruncated=prepared.preview_truncated,
        expectedState=None if staged is None else staged.expected_state,
    )


def _resolutions(value: str) -> tuple[TargetResolution, ...]:
    try:
        supplied = TypeAdapter(list[ResolutionInput]).validate_json(value)
        if len({item.sourceGroupNumber for item in supplied}) != len(supplied):
            raise ValueError
        return tuple(
            TargetResolution(item.sourceGroupNumber, item.accountId, item.securityId)
            for item in supplied
        )
    except ValidationError, ValueError:
        raise HTTPException(
            status_code=422, detail="investment target resolutions are invalid"
        ) from None


def _masks(owner: OwnerSession, prepared: PreparedImport) -> dict[UUID, str]:
    account_ids = tuple(
        {
            target.account_id
            for group in prepared.groups
            for target in group.targets
            if target.account_id is not None
        }
    )
    if not account_ids:
        return {}
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT DISTINCT ON (account_id) account_id, masked_value
                 FROM account_identifier
                WHERE household_id = %s AND account_id = ANY(%s) AND is_active
                ORDER BY account_id, created_at DESC""",
            (owner.household_id, list(account_ids)),
        )
        return {UUID(str(row[0])): str(row[1]) for row in cur.fetchall()}


def _labels(owner: OwnerSession, prepared: PreparedImport) -> dict[UUID, tuple[str, str | None]]:
    """The owner's own catalogue name and ISIN for each already-resolved security.

    Task 16's contract wants a bounded, redacted label straight from the statement text plus
    ISIN-matched candidates; the extraction contract (adapters.py `SourceTarget`) does not carry
    a safe form of either today; see docs/superpowers/progress.md for the recorded deviation.
    Once a target is resolved to a household security, its own confirmed catalogue name and
    ISIN are safe to return as-is — they never came from statement text.
    """
    security_ids = tuple(
        {
            target.security_id
            for group in prepared.groups
            for target in group.targets
            if target.security_id is not None
        }
    )
    if not security_ids:
        return {}
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT id, name, isin FROM security
                 WHERE id = ANY(%s) AND (household_id IS NULL OR household_id = %s)""",
            (list(security_ids), owner.household_id),
        )
        return {UUID(str(row[0])): (str(row[1]), row[2]) for row in cur.fetchall()}


def _staging_error(exc: StagingError) -> HTTPException:
    detail = str(exc)
    if detail in {"investment import not found", "investment document not found"}:
        code = 404
    elif detail in {"investment import is terminal", "investment import retry conflicts"}:
        code = 409
    else:
        code = 422
    return HTTPException(status_code=code, detail=detail)


def _document(
    owner: OwnerSession,
    uploaded: storage.StoredDocument,
    *,
    page_count: int,
    encrypted: bool,
    prepared: PreparedImport,
) -> StagedImportResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            cur.execute(
                """INSERT INTO document (
                       household_id, document_type, original_filename, storage_bucket,
                       storage_path, mime_type, byte_size, sha256, page_count, parsed_status,
                       is_password_protected, uploaded_by)
                   VALUES (%s, 'investment_statement', %s, 'documents', %s,
                           'application/pdf', %s, %s, %s, 'parsed', %s, %s)
                   ON CONFLICT (household_id, sha256) DO NOTHING
                   RETURNING id""",
                (
                    owner.household_id,
                    uploaded.masked_filename,
                    str(uploaded.path),
                    uploaded.byte_size,
                    uploaded.sha256,
                    page_count,
                    encrypted,
                    owner.user_id,
                ),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    """SELECT id FROM document
                         WHERE household_id = %s AND sha256 = %s FOR KEY SHARE""",
                    (owner.household_id, uploaded.sha256),
                )
                row = cur.fetchone()
            if row is None:
                raise StagingError("investment import could not be staged")
            return stage_prepared_import_in_transaction(owner, UUID(str(row[0])), prepared, conn)
    except psycopg.Error:
        raise StagingError("investment import could not be staged") from None


@router.post(
    "/parse",
    response_model=InvestmentParseResponse,
    responses={201: {"model": InvestmentParseResponse, "description": "Import staged"}},
)
def parse_investment_statement(
    response: Response,
    file: Annotated[UploadFile, File()],
    owner: Annotated[OwnerSession, Depends(require_owner)],
    stage: Annotated[bool, Form()] = False,
    password: Annotated[str | None, Form()] = None,
    resolutions: Annotated[str, Form()] = "[]",
) -> InvestmentParseResponse:
    try:
        selected = _resolutions(resolutions)
        with uploaded_pages(file, password) as (data, pages):
            try:
                prepared = prepare_import(extract_statement(pages), selected)
            except ValueError:
                raise HTTPException(
                    status_code=422, detail="investment target resolutions are invalid"
                ) from None
            if not stage:
                if selected:
                    validate_prepared_import(owner, prepared)
                return _response(prepared, _masks(owner, prepared), _labels(owner, prepared))
            if not prepared.stageable:
                raise StagingError("investment import is not stageable")
            validate_prepared_import(owner, prepared)
            uploaded = storage.store(data, owner.household_id, _SAFE_DOCUMENT_LABEL)
            result = _document(
                owner,
                uploaded,
                page_count=len(pages),
                encrypted=password is not None,
                prepared=prepared,
            )
        response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
        return _response(prepared, _masks(owner, prepared), _labels(owner, prepared), result)
    except StagingError as exc:
        raise _staging_error(exc) from None
    except (storage.StorageError, ExtractionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=422, detail="investment statement is invalid") from None


@router.post("/{import_id}/cancel", response_model=InvestmentCancelResponse)
def cancel_investment_import(
    import_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> InvestmentCancelResponse:
    try:
        result = cancel_staged_import(owner, import_id)
    except StagingError as exc:
        raise _staging_error(exc) from None
    return InvestmentCancelResponse(importId=result.import_id, status="cancelled")


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedState: str = Field(min_length=1, max_length=128)


class CommittedGroupResponse(BaseModel):
    groupNumber: int
    status: Literal["committed", "blocked"]
    reason: Literal["open_review", "missing_fx_rate", "database_refusal"] | None


class InvestmentCommitResponse(BaseModel):
    importId: UUID
    status: StagedImportStatus
    groups: list[CommittedGroupResponse]


def _committed_group(outcome: CommittedGroupOutcome) -> CommittedGroupResponse:
    return CommittedGroupResponse(
        groupNumber=outcome.group_number, status=outcome.status, reason=outcome.reason
    )


def _commit_response(result: CommitOutcome) -> InvestmentCommitResponse:
    return InvestmentCommitResponse(
        importId=result.import_id,
        status=result.status,
        groups=[_committed_group(outcome) for outcome in result.groups],
    )


@router.post("/{import_id}/commit", response_model=InvestmentCommitResponse)
def commit_investment_import(
    import_id: UUID,
    body: CommitRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> InvestmentCommitResponse:
    try:
        result = commit_staged_import(owner, import_id, body.expectedState)
    except InvestmentCommandError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    return _commit_response(result)


def _masks_for_accounts(owner: OwnerSession, account_ids: tuple[UUID, ...]) -> dict[UUID, str]:
    if not account_ids:
        return {}
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT DISTINCT ON (account_id) account_id, masked_value
                 FROM account_identifier
                WHERE household_id = %s AND account_id = ANY(%s) AND is_active
                ORDER BY account_id, created_at DESC""",
            (owner.household_id, list(account_ids)),
        )
        return {UUID(str(row[0])): str(row[1]) for row in cur.fetchall()}


def _labels_for_securities(
    owner: OwnerSession, security_ids: tuple[UUID, ...]
) -> dict[UUID, tuple[str, str | None]]:
    if not security_ids:
        return {}
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT id, name, isin FROM security
                 WHERE id = ANY(%s) AND (household_id IS NULL OR household_id = %s)""",
            (list(security_ids), owner.household_id),
        )
        return {UUID(str(row[0])): (str(row[1]), row[2]) for row in cur.fetchall()}


def _group_targets(
    group_number: int,
    draft_rows: list[tuple[Any, ...]],
    checkpoint_rows: list[tuple[Any, ...]],
    masks: dict[UUID, str],
    labels: dict[UUID, tuple[str, str | None]],
) -> list[TargetResponse]:
    order: list[tuple[UUID, UUID]] = []
    drafts_by_key: dict[tuple[UUID, UUID], list[DraftResponse]] = {}
    checkpoints_by_key: dict[tuple[UUID, UUID], list[CheckpointResponse]] = {}
    for row in draft_rows:
        key = (UUID(str(row[0])), UUID(str(row[1])))
        drafts_by_key.setdefault(key, [])
        if key not in order:
            order.append(key)
        drafts_by_key[key].append(
            DraftResponse(
                eventDate=row[2],
                eventKind=row[3],
                units=_decimal(row[4]),
                unitRatio=_decimal(row[5]),
                price=_decimal(row[6]),
                grossAmount=_decimal(row[7]),
                charges=str(row[8]),
                withheldTax=str(row[9]),
                netAmount=str(row[10]),
                currency=str(row[11]).strip(),
                pageNumber=row[12],
                rowNumber=row[13],
            )
        )
    for row in checkpoint_rows:
        key = (UUID(str(row[0])), UUID(str(row[1])))
        checkpoints_by_key.setdefault(key, [])
        if key not in order:
            order.append(key)
        checkpoints_by_key[key].append(
            CheckpointResponse(
                asOfDate=row[2],
                reportedUnits=_decimal(row[3]),
                reportedCostNative=_decimal(row[4]),
                reportedValueNative=_decimal(row[5]),
                currency=None if row[6] is None else str(row[6]).strip(),
                declaredUnitScale=row[7],
                pageNumber=row[8],
                rowNumber=row[9],
            )
        )
    return [
        TargetResponse(
            sourceGroupNumber=group_number,
            accountId=account_id,
            securityId=security_id,
            accountMask=masks.get(account_id),
            securityLabel=None if labels.get(security_id) is None else labels[security_id][0],
            isin=None if labels.get(security_id) is None else labels[security_id][1],
            securityCandidates=[],
            drafts=drafts_by_key.get((account_id, security_id), []),
            checkpoints=checkpoints_by_key.get((account_id, security_id), []),
            reasons=[],
        )
        for account_id, security_id in order
    ]


def _load_import_state(owner: OwnerSession, import_id: UUID) -> InvestmentParseResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT status::text FROM investment_import
                 WHERE household_id = %s AND id = %s""",
            (owner.household_id, import_id),
        )
        row = cur.fetchone()
        if row is None:
            raise StagingError("investment import not found")
        import_status = cast(StagedImportStatus, str(row[0]))
        cur.execute(
            """SELECT id, group_number FROM investment_import_group
                 WHERE household_id = %s AND import_id = %s ORDER BY group_number""",
            (owner.household_id, import_id),
        )
        group_rows = cur.fetchall()
        account_ids: set[UUID] = set()
        security_ids: set[UUID] = set()
        raw_groups: list[
            tuple[int, list[tuple[Any, ...]], list[tuple[Any, ...]], list[ReviewReason]]
        ] = []
        for group_id_raw, group_number in group_rows:
            group_id = UUID(str(group_id_raw))
            cur.execute(
                """SELECT reason::text FROM investment_review_item
                     WHERE household_id = %s AND import_id = %s AND group_id = %s
                       AND status = 'open'
                     ORDER BY reason""",
                (owner.household_id, import_id, group_id),
            )
            group_reasons = cast(
                "list[ReviewReason]", [str(reason[0]) for reason in cur.fetchall()]
            )
            cur.execute(
                """SELECT draft.account_id, draft.security_id, draft.event_date,
                          draft.event_kind::text, draft.units, draft.unit_ratio, draft.price,
                          draft.gross_amount, draft.charges, draft.withheld_tax,
                          draft.net_amount, draft.currency::text,
                          evidence.page_number, evidence.row_number
                     FROM investment_event_draft draft
                     JOIN investment_evidence evidence
                       ON evidence.household_id = draft.household_id
                      AND evidence.draft_id = draft.id
                    WHERE draft.household_id = %s AND draft.group_id = %s
                    ORDER BY draft.source_order""",
                (owner.household_id, group_id),
            )
            draft_rows = cur.fetchall()
            cur.execute(
                """SELECT checkpoint.account_id, checkpoint.security_id, checkpoint.as_of_date,
                          checkpoint.reported_units, checkpoint.reported_cost_native,
                          checkpoint.reported_value_native, checkpoint.currency::text,
                          checkpoint.declared_unit_scale,
                          evidence.page_number, evidence.row_number
                     FROM investment_position_checkpoint checkpoint
                     JOIN investment_evidence evidence
                       ON evidence.household_id = checkpoint.household_id
                      AND evidence.checkpoint_id = checkpoint.id
                    WHERE checkpoint.household_id = %s AND checkpoint.group_id = %s
                    ORDER BY checkpoint.source_order""",
                (owner.household_id, group_id),
            )
            checkpoint_rows = cur.fetchall()
            account_ids.update(UUID(str(r[0])) for r in draft_rows)
            account_ids.update(UUID(str(r[0])) for r in checkpoint_rows)
            security_ids.update(UUID(str(r[1])) for r in draft_rows)
            security_ids.update(UUID(str(r[1])) for r in checkpoint_rows)
            raw_groups.append((int(group_number), draft_rows, checkpoint_rows, group_reasons))
        cur.execute(
            """SELECT reason::text FROM investment_review_item
                 WHERE household_id = %s AND import_id = %s AND group_id IS NULL
                   AND status = 'open'""",
            (owner.household_id, import_id),
        )
        top_reasons = cast("list[ReviewReason]", [str(reason[0]) for reason in cur.fetchall()])
        expected_state = _expected_state(cur, owner.household_id, import_id)
    masks = _masks_for_accounts(owner, tuple(account_ids))
    labels = _labels_for_securities(owner, tuple(security_ids))
    total_draft_count = 0
    total_blocker_count = 0
    groups: list[GroupResponse] = []
    for group_number, draft_rows, checkpoint_rows, group_reasons in raw_groups:
        total_draft_count += len(draft_rows)
        total_blocker_count += sum(is_blocking_reason(reason) for reason in group_reasons)
        groups.append(
            GroupResponse(
                groupNumber=group_number,
                stageable=not any(is_blocking_reason(reason) for reason in group_reasons),
                targets=_group_targets(group_number, draft_rows, checkpoint_rows, masks, labels),
                reasons=[
                    ReasonResponse(code=reason, pageNumber=None, rowNumber=None)
                    for reason in group_reasons
                ],
            )
        )
    total_blocker_count += sum(is_blocking_reason(reason) for reason in top_reasons)
    return InvestmentParseResponse(
        supported=True,
        staged=True,
        importId=import_id,
        status=import_status,
        groups=groups,
        reasons=[
            ReasonResponse(code=reason, pageNumber=None, rowNumber=None) for reason in top_reasons
        ],
        totalGroupCount=len(groups),
        totalDraftCount=total_draft_count,
        totalBlockerCount=total_blocker_count,
        previewTruncated=False,
        expectedState=expected_state,
    )


@router.get("/{import_id}", response_model=InvestmentParseResponse)
def get_investment_import(
    import_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> InvestmentParseResponse:
    try:
        return _load_import_state(owner, import_id)
    except StagingError as exc:
        raise _staging_error(exc) from None
