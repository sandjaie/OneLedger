"""Preview statement-backed upgrades for legacy account identities without writing."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..account_resolution import resolve_statement_account
from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..identifiers import CompleteIdentifier, IdentifierValueError
from ..ingest import engine
from ..ingest.words import ExtractionError
from ..institutions import resolve_institution
from . import layouts
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
    previewedAt: datetime
    expectedState: str


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

    def safe_state(self) -> list[str | None]:
        private = [self.normalized, self.suffix]
        return [
            self.public.outcome,
            self.public.reason,
            *(
                None if value is None else hashlib.sha256(value.encode()).hexdigest()
                for value in private
            ),
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
    institution = resolve_institution(
        cur,
        household_id,
        extraction.institution_name_raw or "",
        country=extraction.metadata.get("institution_country"),
    )
    resolver_state = (institution.status, *(str(item.id) for item in institution.candidates))
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
    if source.account_id not in candidates or resolution.conflicts:
        result = _result(source.document_id, "conflicting", "account_mismatch")
        return Assessed(result.public, resolver_state=resolver_state)
    identifier = resolution.identifier
    outcome: Outcome = "complete" if isinstance(identifier, CompleteIdentifier) else "mask_only"
    reason: Reason = "complete_identity" if outcome == "complete" else "source_mask_only"
    return Assessed(
        DocumentIdentityPreview(documentId=source.document_id, outcome=outcome, reason=reason),
        normalized=identifier.normalized_value
        if isinstance(identifier, CompleteIdentifier)
        else None,
        suffix=identifier.visible_suffix,
        resolver_state=resolver_state,
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


@router.post("/preview")
def preview_identity_backfill(
    body: IdentityBackfillPreviewRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> IdentityBackfillPreview:
    previewed_at = _now()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        sources = _sources(cur, owner.household_id, body.accountId)
        identifiers = _identifier_state(cur, owner.household_id)
        grouped: dict[UUID, list[Assessed]] = {}
        assessed: list[Assessed] = []
        for source in sources:
            item = _assess_for_household(cur, source, owner.household_id, body.password)
            assessed.append(item)
            grouped.setdefault(source.account_id, []).append(item)

    accounts = [
        AccountIdentityPreview(
            accountId=account_id,
            outcome=_account_outcome(items),
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
    return IdentityBackfillPreview(
        accounts=accounts,
        counts=counts,
        previewedAt=previewed_at,
        expectedState=_state_digest(previewed_at, sources, identifiers, assessed),
    )
