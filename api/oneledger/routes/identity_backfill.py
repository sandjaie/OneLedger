"""Preview statement-backed upgrades for legacy account identities without writing."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import AwareDatetime, BaseModel, Field

from ..account_resolution import resolve_statement_account
from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..identifiers import (
    CompleteIdentifier,
    Identifier,
    IdentifierValueError,
    MaskedIdentifier,
    identifier_from_source,
)
from ..ingest import engine
from ..ingest.words import ExtractionError
from ..institutions import (
    InstitutionCandidate,
    InstitutionResolution,
    normalize_institution_name,
    resolve_institution,
)
from . import layouts
from .accounts import _replace_account_identifier
from .description_reparse import _verified_pages as verified_pages

__all__ = ["router", "verified_pages"]

router = APIRouter(prefix="/account-identifiers/backfill", tags=["accounts"])


def _now() -> datetime:
    return datetime.now(UTC)


type Outcome = Literal["complete", "mask_only", "missing", "conflicting", "unsupported"]
type Reason = Literal[
    "complete_identity",
    "source_mask_only",
    "identity_missing",
    "capture_conflict",
    "institution_unresolved",
    "account_mismatch",
    "layout_provenance_missing",
    "layout_unsupported",
    "storage_unsupported",
    "source_path_invalid",
    "source_unreadable",
    "source_hash_mismatch",
    "password_required_or_invalid",
]


class IdentityBackfillPreviewRequest(BaseModel):
    accountId: UUID | None = None
    password: str | None = Field(default=None, max_length=4096, repr=False)


class DocumentIdentityPreview(BaseModel):
    documentId: UUID
    outcome: Outcome
    reason: Reason


class AccountIdentityPreview(BaseModel):
    accountId: UUID
    outcome: Outcome
    eligible: bool
    documents: list[DocumentIdentityPreview]


class IdentityBackfillCounts(BaseModel):
    complete: int
    mask_only: int
    missing: int
    conflicting: int
    unsupported: int


class IdentityBackfillPreview(BaseModel):
    accounts: list[AccountIdentityPreview]
    counts: IdentityBackfillCounts
    eligibleCount: int
    previewedAt: datetime
    expectedState: str


class IdentityBackfillApplyRequest(BaseModel):
    accountId: UUID
    password: str | None = Field(default=None, max_length=4096, repr=False)
    previewedAt: AwareDatetime
    expectedState: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class IdentityBackfillApplyResult(BaseModel):
    accountId: UUID
    status: Literal["applied", "unchanged"]


@dataclass(frozen=True)
class Source:
    batch_id: UUID
    account_id: UUID
    document_id: UUID
    status: str
    recorded_layout_id: UUID | None
    recorded_layout_version: int | None
    completed_at: datetime | None
    storage_bucket: str
    storage_path: str = field(repr=False)
    sha256: str = field(repr=False)
    byte_size: int
    parsed_status: str
    document_layout_id: UUID | None
    document_layout_version: int | None
    layout_name: str | None
    newest_layout_id: UUID | None
    newest_layout_version: int | None
    institution_id: UUID | None
    account_type: str
    currency: str
    account_updated_at: datetime

    def safe_state(self) -> list[str | int | None]:
        """Ordered provenance/version state; private path and checksum stay digest-only."""
        return [
            str(self.batch_id),
            str(self.account_id),
            str(self.document_id),
            self.status,
            None if self.recorded_layout_id is None else str(self.recorded_layout_id),
            self.recorded_layout_version,
            None if self.completed_at is None else self.completed_at.isoformat(),
            self.storage_bucket,
            hashlib.sha256(self.storage_path.encode()).hexdigest(),
            hashlib.sha256(self.sha256.encode()).hexdigest(),
            self.byte_size,
            self.parsed_status,
            None if self.document_layout_id is None else str(self.document_layout_id),
            self.document_layout_version,
            None
            if self.layout_name is None
            else hashlib.sha256(self.layout_name.encode()).hexdigest(),
            None if self.newest_layout_id is None else str(self.newest_layout_id),
            self.newest_layout_version,
            None if self.institution_id is None else str(self.institution_id),
            self.account_type,
            self.currency,
            self.account_updated_at.isoformat(),
        ]


@dataclass(frozen=True)
class Assessed:
    public: DocumentIdentityPreview
    normalized: str | None = field(default=None, repr=False)
    suffix: str | None = field(default=None, repr=False)
    resolver_state: tuple[str, ...] = ()
    identifier: Identifier | None = field(default=None, repr=False, compare=False)
    pending_alias: str | None = field(default=None, repr=False, compare=False)
    pending_alias_institution_id: UUID | None = None
    pending_alias_country: str | None = None
    replace_identifier: bool = False
    eligible: bool = False

    def safe_state(self) -> list[str | None]:
        private = [self.normalized, self.suffix, self.pending_alias]
        return [
            self.public.outcome,
            self.public.reason,
            str(self.eligible),
            str(self.replace_identifier),
            *(
                None if value is None else hashlib.sha256(value.encode()).hexdigest()
                for value in private
            ),
            None
            if self.pending_alias_institution_id is None
            else str(self.pending_alias_institution_id),
            self.pending_alias_country,
            *self.resolver_state,
        ]


def _result(document_id: UUID, outcome: Outcome, reason: Reason) -> Assessed:
    return Assessed(DocumentIdentityPreview(documentId=document_id, outcome=outcome, reason=reason))


_SOURCES = """SELECT b.id, b.account_id, b.document_id, b.status::text,
                      b.statement_layout_id, b.layout_version, b.completed_at,
                      d.storage_bucket, d.storage_path, d.sha256, d.byte_size,
                      d.parsed_status::text, d.statement_layout_id, d.layout_version,
                      used.name, newest.id, newest.version,
                      a.institution_id, a.account_type::text, a.currency, a.updated_at
                 FROM import_batch b
                 JOIN document d
                   ON d.id = b.document_id AND d.household_id = b.household_id
                 JOIN account a
                   ON a.id = b.account_id AND a.household_id = b.household_id
                 LEFT JOIN statement_layout used
                   ON used.id = b.statement_layout_id AND used.household_id = b.household_id
                 LEFT JOIN LATERAL (
                     SELECT candidate.id, candidate.version
                       FROM statement_layout candidate
                      WHERE candidate.household_id = b.household_id
                        AND candidate.name = used.name
                      ORDER BY candidate.version DESC, candidate.id
                      LIMIT 1
                 ) newest ON true
                WHERE b.household_id = %s AND b.status = 'completed'"""


def _sources(cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID | None) -> list[Source]:
    if account_id is not None:
        cur.execute(
            "SELECT 1 FROM account WHERE id = %s AND household_id = %s",
            (account_id, household_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="no such account")
    query = _SOURCES
    values: tuple[Any, ...] = (household_id,)
    if account_id is not None:
        query += " AND b.account_id = %s"
        values += (account_id,)
    query += " ORDER BY b.account_id, b.document_id, b.id"
    cur.execute(query, values)
    return [Source(*row) for row in cur.fetchall()]


def _identifier_state(cur: psycopg.Cursor[Any], household_id: UUID) -> list[list[str | None]]:
    cur.execute(
        """SELECT id, account_id, kind, provenance, is_active, updated_at,
                  source_document_id, source_sha256, source_layout_id, source_layout_version,
                  source_capture_key
             FROM account_identifier
            WHERE household_id = %s
            ORDER BY account_id, kind, created_at, id""",
        (household_id,),
    )
    return [
        [
            None
            if value is None
            else value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            for value in row
        ]
        for row in cur.fetchall()
    ]


def _state_digest(
    previewed_at: datetime,
    sources: list[Source],
    identifiers: list[list[str | None]],
    assessed: list[Assessed],
) -> str:
    material = json.dumps(
        [
            "oneledger-identity-backfill-preview-v1",
            previewed_at.isoformat(),
            [source.safe_state() for source in sources],
            identifiers,
            [item.safe_state() for item in assessed],
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _source_failure(document_id: UUID, detail: str) -> Assessed:
    if "path check" in detail:
        reason: Reason = "source_path_invalid"
    elif "could not be read" in detail:
        reason = "source_unreadable"
    elif "integrity check" in detail:
        reason = "source_hash_mismatch"
    elif "malformed or unsupported" in detail:
        reason = "layout_unsupported"
    else:
        reason = "password_required_or_invalid"
    return _result(document_id, "unsupported", reason)


def _account_institution_resolution(
    cur: psycopg.Cursor[Any],
    source: Source,
    household_id: UUID,
    alias: str | None,
    expected_country: str | None,
    sensitive_captures: tuple[str, ...],
) -> tuple[InstitutionResolution, str, str] | None:
    """Use completed-import provenance only as the target of this explicit backfill decision."""
    if (
        source.institution_id is None
        or alias is None
        or not alias
        or layouts._preview_redactor(None)(alias) != alias
        or any(
            layouts._preview_redactor(capture)(alias) != alias
            or layouts._preview_redactor(capture.casefold())(alias.casefold()) != alias.casefold()
            for capture in sensitive_captures
        )
    ):
        return None
    cur.execute(
        """SELECT id, name, short_name, country, institution_type
             FROM institution
            WHERE id = %s AND (household_id IS NULL OR household_id = %s)""",
        (source.institution_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    country = str(row[3]).strip()
    if expected_country and expected_country.strip().upper() != country:
        return None
    candidate = InstitutionCandidate(
        id=row[0],
        name=row[1],
        short_name=row[2],
        country=country,
        institution_type=row[4],
    )
    return (
        InstitutionResolution(
            status="matched",
            proposed_name=None,
            country=country,
            candidates=(candidate,),
        ),
        alias,
        country,
    )


def _assess_for_household(
    cur: psycopg.Cursor[Any], source: Source, household_id: UUID, password: str | None
) -> Assessed:
    if (
        source.recorded_layout_id is None
        or source.recorded_layout_version is None
        or source.layout_name is None
        or source.newest_layout_id is None
        or source.newest_layout_version is None
    ):
        return _result(source.document_id, "unsupported", "layout_provenance_missing")
    if source.storage_bucket != "documents":
        return _result(source.document_id, "unsupported", "storage_unsupported")
    try:
        profile = layouts._one(cur, source.newest_layout_id, household_id)
        if profile.accountType is None:
            raise ValueError
        with verified_pages(source.storage_path, source.sha256, password) as pages:
            extraction = engine.extract_pages(profile, pages)
    except HTTPException as exc:
        return _source_failure(source.document_id, str(exc.detail))
    except ValueError, ExtractionError:
        return _result(source.document_id, "unsupported", "layout_unsupported")

    raw = extraction.account_number_raw
    if raw is None:
        return _result(source.document_id, "missing", "identity_missing")
    if extraction.account_number_capture_count != 1:
        return _result(source.document_id, "conflicting", "capture_conflict")
    identifier_kind = "card" if profile.accountType == "credit_card" else "bank_account"
    try:
        captured_identifier = identifier_from_source(
            identifier_kind,
            raw,
            source_is_masked=extraction.account_number_is_masked,
        )
    except IdentifierValueError:
        return _result(source.document_id, "missing", "identity_missing")
    sensitive_captures: tuple[str, ...] = (raw,)
    if isinstance(captured_identifier, CompleteIdentifier):
        sensitive_captures += (captured_identifier.normalized_value,)
    institution = resolve_institution(
        cur,
        household_id,
        extraction.institution_name_raw or "",
        country=extraction.metadata.get("institution_country"),
    )
    resolver_state = (institution.status, *(str(item.id) for item in institution.candidates))
    pending_alias: str | None = None
    pending_alias_country: str | None = None
    if institution.status == "needs_confirmation":
        source_resolution = _account_institution_resolution(
            cur,
            source,
            household_id,
            institution.proposed_name,
            extraction.metadata.get("institution_country"),
            sensitive_captures,
        )
        if source_resolution is not None:
            institution, pending_alias, pending_alias_country = source_resolution
            resolver_state = (
                "source_account_institution",
                str(source.institution_id),
            )
    if institution.status != "matched":
        result = _result(source.document_id, "conflicting", "institution_unresolved")
        return Assessed(result.public, resolver_state=resolver_state)
    try:
        resolution = resolve_statement_account(
            cur,
            household_id=household_id,
            institution=institution,
            account_type=profile.accountType,
            currency=extraction.metadata.get("currency"),
            captured_value=raw,
            source_is_masked=extraction.account_number_is_masked,
            legacy_salt=os.environ.get("ACCOUNT_HASH_SALT", "").strip() or None,
        )
    except IdentifierValueError:
        return _result(source.document_id, "missing", "identity_missing")
    candidates = {candidate.id for candidate in resolution.candidates}
    statement_currency = extraction.metadata.get("currency")
    historical_mask_adoption = (
        isinstance(resolution.identifier, MaskedIdentifier)
        and resolution.status == "needs_confirmation"
        and not resolution.candidates
        and not resolution.conflicts
        and len(institution.candidates) == 1
        and institution.candidates[0].id == source.institution_id
        and profile.accountType == source.account_type
        and statement_currency is not None
        and statement_currency.strip().upper() == source.currency.strip().upper()
    )
    if (
        source.account_id not in candidates and not historical_mask_adoption
    ) or resolution.conflicts:
        result = _result(source.document_id, "conflicting", "account_mismatch")
        return Assessed(result.public, resolver_state=resolver_state)
    if historical_mask_adoption:
        resolver_state += ("historical_source_account", str(source.account_id))
    identifier = resolution.identifier
    outcome: Outcome = "complete" if isinstance(identifier, CompleteIdentifier) else "mask_only"
    reason: Reason = "complete_identity" if outcome == "complete" else "source_mask_only"
    replace_identifier = (
        isinstance(identifier, CompleteIdentifier)
        and not next(
            item for item in resolution.candidates if item.id == source.account_id
        ).can_reveal
    ) or historical_mask_adoption
    return Assessed(
        DocumentIdentityPreview(documentId=source.document_id, outcome=outcome, reason=reason),
        normalized=identifier.normalized_value
        if isinstance(identifier, CompleteIdentifier)
        else None,
        suffix=identifier.visible_suffix,
        resolver_state=resolver_state,
        identifier=identifier,
        pending_alias=pending_alias,
        pending_alias_institution_id=(source.institution_id if pending_alias is not None else None),
        pending_alias_country=pending_alias_country,
        replace_identifier=replace_identifier,
        eligible=replace_identifier or pending_alias is not None,
    )


def _account_outcome(items: list[Assessed]) -> Outcome:
    usable = [item for item in items if item.public.outcome in {"complete", "mask_only"}]
    complete = {item.normalized for item in usable if item.normalized is not None}
    suffixes = {item.suffix for item in usable if item.suffix is not None}
    if (
        len(complete) > 1
        or len(suffixes) > 1
        or any(
            normalized is not None and suffix is not None and not normalized.endswith(suffix)
            for normalized in complete
            for suffix in suffixes
        )
    ):
        return "conflicting"
    if any(item.public.outcome == "conflicting" for item in items):
        return "conflicting"
    if any(item.public.outcome == "unsupported" for item in items):
        return "unsupported"
    if any(item.public.outcome == "missing" for item in items):
        return "missing"
    if any(item.public.outcome == "complete" for item in items):
        return "complete"
    return "mask_only"


def _preview(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID | None,
    password: str | None,
    previewed_at: datetime,
) -> tuple[IdentityBackfillPreview, list[Source], list[Assessed]]:
    sources = _sources(cur, household_id, account_id)
    identifiers = _identifier_state(cur, household_id)
    grouped: dict[UUID, list[Assessed]] = {}
    assessed: list[Assessed] = []
    for source in sources:
        item = _assess_for_household(cur, source, household_id, password)
        assessed.append(item)
        grouped.setdefault(source.account_id, []).append(item)
    accounts = [
        AccountIdentityPreview(
            accountId=account_id,
            outcome=_account_outcome(items),
            eligible=_account_outcome(items) in {"complete", "mask_only"}
            and any(item.eligible for item in items),
            documents=[item.public for item in items],
        )
        for account_id, items in grouped.items()
    ]
    counts = IdentityBackfillCounts(
        **{
            outcome: sum(account.outcome == outcome for account in accounts)
            for outcome in ("complete", "mask_only", "missing", "conflicting", "unsupported")
        }
    )
    return (
        IdentityBackfillPreview(
            accounts=accounts,
            counts=counts,
            eligibleCount=sum(account.eligible for account in accounts),
            previewedAt=previewed_at,
            expectedState=_state_digest(previewed_at, sources, identifiers, assessed),
        ),
        sources,
        assessed,
    )


@router.post("/preview")
def preview_identity_backfill(
    body: IdentityBackfillPreviewRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> IdentityBackfillPreview:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        preview, _, _ = _preview(cur, owner.household_id, body.accountId, body.password, _now())
    return preview


@router.post("/apply")
def apply_identity_backfill(
    body: IdentityBackfillApplyRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> IdentityBackfillApplyResult:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        preview, sources, assessed = _preview(
            cur, owner.household_id, body.accountId, body.password, body.previewedAt
        )
        if not hmac.compare_digest(preview.expectedState, body.expectedState):
            raise HTTPException(status_code=409, detail="identity backfill preview is stale")
        if len(preview.accounts) != 1 or preview.accounts[0].outcome not in {
            "complete",
            "mask_only",
        }:
            raise HTTPException(
                status_code=409, detail="identity backfill source documents do not agree"
            )
        eligible = [item for item in assessed if item.eligible]
        if not eligible:
            return IdentityBackfillApplyResult(accountId=body.accountId, status="unchanged")
        try:
            for item in eligible:
                if item.pending_alias is not None:
                    assert item.pending_alias_institution_id is not None
                    assert item.pending_alias_country is not None
                    cur.execute(
                        """INSERT INTO institution_alias (
                               household_id, institution_id, alias, alias_normalized, country,
                               provenance)
                           VALUES (%s, %s, %s, %s, %s, 'owner_confirmed')
                           ON CONFLICT (household_id, institution_id, country, alias_normalized)
                             WHERE is_active DO NOTHING""",
                        (
                            owner.household_id,
                            item.pending_alias_institution_id,
                            item.pending_alias,
                            normalize_institution_name(item.pending_alias),
                            item.pending_alias_country,
                        ),
                    )
            chosen = next((item for item in eligible if item.replace_identifier), None)
            if chosen is not None:
                identifier = chosen.identifier
                assert identifier is not None
                source = next(
                    item for item in sources if item.document_id == chosen.public.documentId
                )
                _replace_account_identifier(
                    cur,
                    owner.household_id,
                    body.accountId,
                    identifier.kind,
                    identifier,
                    provenance="statement_backfill",
                    source_sha256=source.sha256,
                    source_document_id=source.document_id,
                    source_layout_id=source.newest_layout_id,
                    source_layout_version=source.newest_layout_version,
                    source_capture_key=(
                        "account_number"
                        if isinstance(identifier, CompleteIdentifier)
                        else "account_number_masked"
                    ),
                )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(status_code=409, detail="account identity already exists") from None
    return IdentityBackfillApplyResult(accountId=body.accountId, status="applied")
