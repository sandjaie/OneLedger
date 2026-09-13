"""Owner preview, staging, replay and cancellation for investment statements."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
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
from ..investments.staging import (
    PreparedImport,
    PreparedTarget,
    StagedImportResult,
    StagingError,
    TargetResolution,
    cancel_staged_import,
    prepare_import,
    stage_prepared_import_in_transaction,
    validate_prepared_import,
)

router = APIRouter(prefix="/investment-imports", tags=["investment imports"])


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


def _target(target: PreparedTarget, masks: dict[UUID, str]) -> TargetResponse:
    return TargetResponse(
        sourceGroupNumber=target.source_group_number,
        accountId=target.account_id,
        securityId=target.security_id,
        accountMask=None if target.account_id is None else masks.get(target.account_id),
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
    staged: StagedImportResult | None = None,
) -> InvestmentParseResponse:
    persisted = (
        {} if staged is None else {state.group_number: state for state in staged.group_states}
    )
    added_blockers = 0
    groups: list[GroupResponse] = []
    for group in prepared.preview_groups:
        state = persisted.get(group.group_number)
        prepared_codes = {reason.code for reason in group.reasons}
        added_reasons = (
            ()
            if state is None
            else tuple(reason for reason in state.reasons if reason not in prepared_codes)
        )
        added_blockers += len(added_reasons)
        groups.append(
            GroupResponse(
                groupNumber=group.group_number,
                stageable=group.stageable if state is None else state.status == "ready",
                targets=[_target(target, masks) for target in group.targets],
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
        target.account_id
        for group in prepared.groups
        for target in group.targets
        if target.account_id is not None
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
                return _response(prepared, _masks(owner, prepared))
            if not prepared.stageable:
                raise StagingError("investment import is not stageable")
            uploaded = storage.store(data, owner.household_id, file.filename or "statement.pdf")
            result = _document(
                owner,
                uploaded,
                page_count=len(pages),
                encrypted=password is not None,
                prepared=prepared,
            )
        response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
        return _response(prepared, _masks(owner, prepared), result)
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
