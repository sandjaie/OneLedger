# api/oneledger/routes/ingest.py
"""M2 Task 5: `POST /parse` — the import path's one route until review arrives.

Dry run by default (spec §4.3): the owner sees the identification, the reconciliation verdict,
the counts and a preview before anything is written, which is what the roadmap's "nothing is
written until Stage batch" means. `stage=true` performs the staging transaction and returns the
same summary plus `batchId`.

**A blocked statement is a 200, not an error** (spec §7.5). A reconciliation mismatch, a running
balance break and a row that would not normalize are all UI states the diagnostics panel renders
from the verdict, the delta and the divergent row — none of which fits in FastAPI's `detail`
string. `staged` says what happened; the reconciliation object says why. Genuine client errors —
a file already imported, a layout that cannot be identified, an account that cannot be resolved —
keep the established `detail` string.

Nothing here echoes the uploaded filename, and no message carries a digit run from the document
(spec §5.6). The unmasked name exists only as a local variable for the duration of the request.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, ValidationError

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..fxrange import FxCoverageError
from ..identifiers import (
    CompleteIdentifier,
    Identifier,
    IdentifierValueError,
    complete_identifier,
    identifier_from_source,
    masked_identifier,
)
from ..ingest import pipeline
from ..ingest.dedupe import OCCURRENCE, fingerprint
from ..ingest.reconcile import Verdict
from ..ingest.storage import DuplicateDocument, StorageError
from ..ingest.words import ExtractionError
from ..ingest.words import extract_pages as read_pdf_pages
from ..institutions import InstitutionResolution as ResolvedInstitution
from ..institutions import resolve_institution
from ..money import _out
from .accounts import (
    AccountCreate,
    AccountRow,
    _account_write_error,
    _insert_account,
    _one,
    _replace_account_identifier,
    resolve_account_by_identifier,
    resolve_account_by_legacy_fingerprint,
)
from .layouts import COLUMNS, _preview_redactor, _row_to_profile

router = APIRouter(tags=["ingest"])

# Spec §4.3: enough to see that the mapping worked, bounded so a 900-row statement does not
# return a megabyte to a screen that shows a dozen lines at a time.
PREVIEW_ROWS = 50


class ParseRow(BaseModel):
    """One normalized row, as the preview and the diagnostics panel read it."""

    sourcePageNumber: int | None
    sourceRowNumber: int
    bookedDate: date
    valueDate: date | None
    descriptionRaw: str
    descriptionClean: str | None
    referenceRaw: str | None
    amount: str
    currency: str
    originalAmount: str | None
    originalCurrency: str | None
    runningBalance: str | None
    proposedTxnType: str
    status: str
    dedupeHash: str


class ParseFailure(BaseModel):
    """A line that extracted and would not normalize. The panel shows these beside the rows."""

    sourcePageNumber: int | None
    sourceRowNumber: int
    reason: str


class ParseMetadataFailure(BaseModel):
    """A captured statement fact that would not read. Reported beside the rows; it never stops
    the statement staging, and it never carries the text that was captured."""

    key: str
    reason: str


class Reconciliation(BaseModel):
    """`reconcile.Verdict` on the wire. Every figure is a string; TypeScript does no arithmetic.

    On a `running_balance_break` the three closing fields are row-level rather than
    statement-level, so a consumer must read `status` first — the same warning the dataclass
    carries, restated here because this is the shape the panel actually receives.
    """

    status: str
    declaredOpening: str | None
    openingIsDerived: bool
    declaredClosing: str | None
    computedClosing: str | None
    delta: str | None
    firstDivergentRow: int | None
    previousBalance: str | None
    amount: str | None
    candidateRows: list[int]
    message: str


class ParsePeriod(BaseModel):
    """`isDerived` travels with the dates because a derived period must never be read as a
    stated one (Invariant I7)."""

    start: date
    end: date
    isDerived: bool


class SuggestedAccount(BaseModel):
    id: UUID
    name: str
    accountNumberMasked: str


class InstitutionCandidate(BaseModel):
    id: UUID
    name: str
    shortName: str | None
    country: str
    institutionType: str


class InstitutionResolution(BaseModel):
    status: Literal["matched", "needs_confirmation", "ambiguous"]
    proposedName: str | None
    country: str | None
    candidates: list[InstitutionCandidate]


class AccountProposal(BaseModel):
    accountNumberMasked: str
    suggestedAccounts: list[SuggestedAccount]
    name: str
    accountType: str | None
    institutionId: UUID | None
    institutionResolution: InstitutionResolution
    country: str | None
    currency: str | None
    openingBalance: str | None
    openingBalanceIsDerived: bool
    openingBalanceDate: date
    openingBalanceNote: str
    creditLimit: str | None


class ParseResponse(BaseModel):
    staged: bool
    batchId: UUID | None
    documentId: UUID | None
    accountId: UUID | None
    accountProposal: AccountProposal | None = None
    layoutId: UUID
    layoutVersion: int
    period: ParsePeriod
    rowsExtracted: int
    counts: dict[str, int]
    reconciliation: Reconciliation
    rows: list[ParseRow]
    failures: list[ParseFailure]
    metadataFailures: list[ParseMetadataFailure]


def _household(cur: psycopg.Cursor[Any], household_id: UUID) -> pipeline.HouseholdContext:
    cur.execute(
        "SELECT id, base_currency, secondary_currency FROM household WHERE id = %s",
        (household_id,),
    )
    row = cur.fetchone()
    if row is None:  # pragma: no cover - a session cannot outlive its household
        raise HTTPException(status_code=404, detail="no such household")
    return pipeline.HouseholdContext(id=row[0], base_currency=row[1], secondary_currency=row[2])


def _profiles(
    cur: psycopg.Cursor[Any], household_id: UUID, layout_id: UUID | None
) -> list[pipeline.ImportableProfile]:
    """The layouts to identify against: the one the owner chose, or every saved one.

    When `layoutId` names a profile the pipeline still scores it, and still declines if nothing
    matched. That is deliberate — an owner who picks the wrong layout for a file gets the same
    refusal as one who picks none, rather than a statement parsed into the wrong columns.
    """
    if layout_id is not None:
        cur.execute(
            f"SELECT {COLUMNS} FROM statement_layout WHERE id = %s AND household_id = %s",
            (layout_id, household_id),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such layout")
        return [_row_to_profile(row)]
    # Newest version per name, matching `GET /layouts`: an older version is kept so that rows
    # already imported keep pointing at the mapping that produced them, never to import with.
    cur.execute(
        f"SELECT DISTINCT ON (name) {COLUMNS} FROM statement_layout"
        " WHERE household_id = %s ORDER BY name, version DESC",
        (household_id,),
    )
    return [_row_to_profile(row) for row in cur.fetchall()]


def _account(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID | None
) -> pipeline.AccountContext | None:
    if account_id is None:
        return None
    cur.execute(
        "SELECT id, currency FROM account WHERE id = %s AND household_id = %s",
        (account_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such account")
    return pipeline.AccountContext(id=row[0], currency=row[1])


def _resolver(cur: psycopg.Cursor[Any], household_id: UUID) -> pipeline.AccountResolver:
    """Resolve current normalized identities, then temporary pre-V29 fingerprints."""

    def resolve(account_number_raw: str) -> pipeline.AccountContext | None:
        try:
            identifier = complete_identifier("bank_account", account_number_raw)
        except IdentifierValueError:
            return None
        found = resolve_account_by_identifier(
            cur, identifier.kind, identifier.normalized_value, household_id
        )
        if found is None:
            salt = os.environ.get("ACCOUNT_HASH_SALT", "").strip()
            if salt and identifier.normalized_value.isdigit():
                digest = hashlib.sha256(f"{salt}{identifier.normalized_value}".encode()).hexdigest()
                found = resolve_account_by_legacy_fingerprint(cur, digest, household_id)
        if found is None:
            return None
        return pipeline.AccountContext(id=found.id, currency=found.currency)

    return resolve


def _statement_identifier(account_type: str, value: str) -> Identifier:
    """Preserve the pre-8c3 capture-shape contract until layouts declare completeness.

    Existing layouts accepted complete ASCII digit runs with visual separators, a four-digit
    suffix, or a mask spelling with exactly four visible ASCII digits. Cards retained only the
    resulting mask even when their capture was complete. Task 8c6 replaces this compatibility
    classifier with explicit source evidence so legal identifier letters such as X need no guess.
    """
    kind = "card" if account_type == "credit_card" else "bank_account"
    numeric = re.fullmatch(r"[0-9\s–./-]+", value) and re.search(r"[0-9]", value)
    if numeric:
        digits = re.sub(r"[\s–./-]", "", value)
        if len(digits) < 4:
            raise ValueError("the statement does not state a usable account identity")
        source_is_masked = len(digits) == 4
    else:
        visible = "".join(character for character in value if character in "0123456789")
        if len(visible) != 4 or not re.fullmatch(r"[xX*•\s–./0-9-]+", value):
            raise ValueError("the statement does not state a usable account identity")
        source_is_masked = True
    try:
        identifier = identifier_from_source(kind, value, source_is_masked=source_is_masked)
    except IdentifierValueError:
        raise ValueError("the statement does not state a usable account identity") from None
    if kind == "card" and isinstance(identifier, CompleteIdentifier):
        return masked_identifier(kind, identifier.visible_suffix)
    return identifier


def _rows(result: pipeline.ParseResult) -> list[ParseRow]:
    # Every document-sourced string goes out screened — the same treatment
    # `POST /layouts/propose` gives its own preview rows, and for the same reason: a card
    # payment line reads `PAYMENT RECEIVED CARD 4111 1111 1111 1111`, and spec §5.6 keeps a
    # digit run of seven or more out of every response regardless of which field it sat in.
    # `originalCurrency` is screened with the rest even though `normalize` now refuses anything
    # but a three-letter code: a value that survives that rule can never be redacted, so this
    # costs nothing, and it means the rule above reads "every untyped field" without an
    # exception a later reader has to hold in their head.
    #
    # Dates, amounts and balances are typed and pass through untouched — screening them would
    # destroy them (`-10.0000` becomes `-[redacted]`) and defeat the only question the preview
    # is for: did the columns land where I mapped them. The account-number capture is passed in
    # so its own spelling is screened too.
    redact = _preview_redactor(result.analysis.account_number_raw)
    return [
        ParseRow(
            sourcePageNumber=row.source_page_number,
            sourceRowNumber=row.source_row_number,
            bookedDate=row.booked_date,
            valueDate=row.value_date,
            descriptionRaw=redact(row.description_raw),
            descriptionClean=None
            if row.description_clean is None
            else redact(row.description_clean),
            referenceRaw=None if row.reference_raw is None else redact(row.reference_raw),
            amount=str(row.amount),
            currency=row.currency,
            originalAmount=_out(row.original_amount),
            originalCurrency=None
            if row.original_currency is None
            else redact(row.original_currency),
            runningBalance=_out(row.running_balance),
            proposedTxnType=row.proposed_txn_type,
            status=verdict.status,
            dedupeHash=verdict.dedupe_hash,
        )
        for row, verdict in zip(
            result.analysis.rows[:PREVIEW_ROWS],
            result.analysis.verdicts[:PREVIEW_ROWS],
            strict=True,
        )
    ]


def _reconciliation(verdict: Verdict) -> Reconciliation:
    return Reconciliation(
        status=verdict.status,
        declaredOpening=_out(verdict.declared_opening),
        openingIsDerived=verdict.opening_is_derived,
        declaredClosing=_out(verdict.declared_closing),
        computedClosing=_out(verdict.computed_closing),
        delta=_out(verdict.delta),
        firstDivergentRow=verdict.first_divergent_row,
        previousBalance=_out(verdict.previous_balance),
        amount=_out(verdict.amount),
        candidateRows=verdict.candidate_rows,
        message=verdict.message,
    )


def _proposal(
    cur: psycopg.Cursor[Any], result: pipeline.ParseResult, household_id: UUID
) -> AccountProposal:
    raw = result.analysis.account_number_raw
    if raw is None:
        raise HTTPException(
            status_code=422, detail="this statement states no usable account number"
        )
    cur.execute(
        """SELECT sl.account_type::text
             FROM statement_layout sl
            WHERE sl.id = %s AND sl.household_id = %s""",
        (result.analysis.layout_id, household_id),
    )
    layout = cur.fetchone()
    if layout is None:  # pragma: no cover - the profile was selected from this household
        raise HTTPException(status_code=404, detail="no such layout")
    (account_type,) = layout
    if account_type is None:
        raise HTTPException(status_code=422, detail="this layout states no account type")
    institution = resolve_institution(
        cur,
        household_id,
        result.analysis.institution_name_raw or "",
        country=result.analysis.metadata.get("institution_country"),
    )
    public_proposed_name = (
        None
        if institution.proposed_name is None
        else _preview_redactor(raw)(institution.proposed_name)
    )
    matched_institution = institution.candidates[0] if institution.status == "matched" else None
    institution_id = None if matched_institution is None else matched_institution.id
    short_name = None if matched_institution is None else matched_institution.short_name
    institution_name = (
        public_proposed_name if matched_institution is None else matched_institution.name
    )
    country = institution.country if matched_institution is None else matched_institution.country
    try:
        identifier = _statement_identifier(account_type, raw)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="this statement states no usable account number"
        ) from None
    masked = identifier.masked_value
    currency = result.analysis.metadata.get("currency", "").strip().upper() or None
    if currency is not None:
        cur.execute("SELECT code FROM currency WHERE code = %s", (currency,))
        if cur.fetchone() is None:
            currency = None
    last_four = masked[-4:]
    cur.execute(
        """SELECT DISTINCT a.id, a.name, ai.masked_value
             FROM account a
             JOIN account_identifier ai
               ON ai.household_id = a.household_id AND ai.account_id = a.id
            WHERE a.household_id = %s AND ai.is_active
              AND ai.kind = %s
              AND COALESCE(ai.visible_suffix, right(ai.masked_value, 4)) = %s
            ORDER BY a.name, a.id""",
        (household_id, identifier.kind, last_four),
    )
    suggestions = [
        SuggestedAccount(id=row[0], name=row[1], accountNumberMasked=row[2])
        for row in cur.fetchall()
    ]
    reconciliation = result.analysis.reconciliation
    opening_date = min(
        [result.analysis.period.start, *(row.booked_date for row in result.analysis.rows)]
    )
    period = result.analysis.period
    source = (
        "Derived from the first row's running balance"
        if reconciliation.opening_is_derived
        else "Declared opening balance of the statement"
    )
    note = f"{source} for {period.start} to {period.end}"
    return AccountProposal(
        accountNumberMasked=masked,
        suggestedAccounts=suggestions,
        name=short_name or institution_name or masked,
        accountType=account_type,
        institutionId=institution_id,
        institutionResolution=_institution_resolution(institution, public_proposed_name),
        country=country,
        currency=currency,
        openingBalance=_out(reconciliation.declared_opening),
        openingBalanceIsDerived=reconciliation.opening_is_derived,
        openingBalanceDate=opening_date,
        openingBalanceNote=note,
        creditLimit=result.analysis.metadata.get("credit_limit"),
    )


def _institution_account_acceptor(
    cur: psycopg.Cursor[Any], household_id: UUID
) -> pipeline.AccountAcceptor:
    """Require PDF issuer evidence to agree before an account can be used or staged."""

    def accept(analysis: pipeline.Analysis, account: pipeline.AccountContext) -> bool:
        resolution = resolve_institution(
            cur,
            household_id,
            analysis.institution_name_raw or "",
            country=analysis.metadata.get("institution_country"),
        )
        if resolution.status != "matched":
            return False
        cur.execute(
            "SELECT institution_id FROM account WHERE id = %s AND household_id = %s",
            (account.id, household_id),
        )
        stored = cur.fetchone()
        return stored is not None and stored[0] == resolution.candidates[0].id

    return accept


def _institution_resolution(
    resolution: ResolvedInstitution, public_proposed_name: str | None
) -> InstitutionResolution:
    return InstitutionResolution(
        status=resolution.status,
        proposedName=public_proposed_name,
        country=resolution.country,
        candidates=[
            InstitutionCandidate(
                id=candidate.id,
                name=candidate.name,
                shortName=candidate.short_name,
                country=candidate.country,
                institutionType=candidate.institution_type,
            )
            for candidate in resolution.candidates
        ],
    )


def _response(
    result: pipeline.ParseResult, proposal: AccountProposal | None = None
) -> ParseResponse:
    analysis = result.analysis
    return ParseResponse(
        staged=result.staged,
        batchId=result.batch_id,
        documentId=result.document_id,
        accountId=None if result.account is None else result.account.id,
        accountProposal=proposal,
        layoutId=analysis.layout_id,
        layoutVersion=analysis.layout_version,
        period=ParsePeriod(
            start=analysis.period.start,
            end=analysis.period.end,
            isDerived=analysis.period.is_derived,
        ),
        rowsExtracted=analysis.rows_extracted,
        counts=analysis.counts,
        reconciliation=_reconciliation(analysis.reconciliation),
        rows=[] if proposal is not None else _rows(result),
        failures=[
            ParseFailure(
                sourcePageNumber=failure.source_page_number,
                sourceRowNumber=failure.source_row_number,
                reason=failure.reason,
            )
            for failure in analysis.failures
        ],
        metadataFailures=[
            ParseMetadataFailure(key=failure.key, reason=failure.reason)
            for failure in analysis.metadata_failures
        ],
    )


def _unidentified(exc: pipeline.LayoutNotIdentified) -> HTTPException:
    """Name the layouts and their scores so the picker has something to rank by.

    `api-contracts.html` §4 asks for "a layout picker listing the four, with the fingerprint
    scores"; §7.5 keeps the established `detail` string rather than introducing a second error
    envelope for one route. The names are the owner's own and carry no document content.
    """
    if not exc.candidates:
        return HTTPException(
            status_code=422,
            detail="no layouts are saved yet — map this statement first, then parse it",
        )
    ranked = ", ".join(
        f"{candidate.profile.name} ({candidate.score:.2f})" for candidate in exc.candidates[:8]
    )
    return HTTPException(status_code=422, detail=f"{exc} — scored: {ranked}")


@contextmanager
def _uploaded_pages(file: UploadFile, password: str | None) -> Iterator[tuple[bytes, list[Any]]]:
    """Read an upload once and remove its private scratch copy on every exit."""
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(prefix="oneledger-parse-", suffix=".pdf", delete=False) as temp:
            temporary_path = Path(temp.name)
            file.file.seek(0)
            copyfileobj(file.file, temp)
        yield data, read_pdf_pages(temporary_path, password=password)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.post("/parse")
def parse_statement(
    file: Annotated[UploadFile, File()],
    owner: Annotated[OwnerSession, Depends(require_owner)],
    stage: Annotated[bool, Form()] = False,
    layoutId: Annotated[UUID | None, Form()] = None,
    accountId: Annotated[UUID | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
) -> ParseResponse:
    try:
        with _uploaded_pages(file, password) as (data, pages):
            with connect() as conn, conn.cursor() as cur:
                household = _household(cur, owner.household_id)
                result = pipeline.parse(
                    conn,
                    pages=pages,
                    data=data,
                    original_filename=file.filename or "statement.pdf",
                    household=household,
                    profiles=_profiles(cur, owner.household_id, layoutId),
                    account=_account(cur, owner.household_id, accountId),
                    resolve_account=_resolver(cur, owner.household_id),
                    stage=stage,
                    accept_account=_institution_account_acceptor(cur, owner.household_id),
                    # The original encrypted bytes are the durable blob. The password remains
                    # only in this request frame and is never stored.
                    encrypted=password is not None,
                )
                proposal = (
                    _proposal(cur, result, owner.household_id) if result.account is None else None
                )
        return _response(result, proposal)
    except DuplicateDocument as exc:
        # Already carries the date it first arrived and the batch that imported it, which is
        # what the screen links to.
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except pipeline.LayoutNotIdentified as exc:
        raise _unidentified(exc) from None
    except (pipeline.AccountNotResolved, FxCoverageError, StorageError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ExtractionError as exc:
        # `PipelineError` and every parse refusal under it. Never carries the filename or a
        # digit run from the document.
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _account_body(value: str) -> AccountCreate:
    try:
        raw = json.loads(value)
        if not isinstance(raw, dict):
            raise ValueError
        if {"accountNumber", "accountNumberMasked"} & raw.keys():
            raise HTTPException(
                status_code=422,
                detail="account identity comes from the statement and must not be submitted",
            )
        return AccountCreate.model_validate(raw)
    except HTTPException:
        raise
    except json.JSONDecodeError, ValidationError, ValueError, TypeError:
        raise HTTPException(status_code=422, detail="account fields are invalid") from None


@router.post(
    "/parse/account",
    response_model=AccountRow,
    status_code=201,
    responses={200: {"model": AccountRow, "description": "Existing account adopted"}},
)
def confirm_statement_account(
    response: Response,
    file: Annotated[UploadFile, File()],
    owner: Annotated[OwnerSession, Depends(require_owner)],
    password: Annotated[str | None, Form()] = None,
    layoutId: Annotated[UUID | None, Form()] = None,
    account: Annotated[str | None, Form()] = None,
    accountId: Annotated[UUID | None, Form()] = None,
) -> AccountRow:
    """Create or adopt the account identity captured from one confirmed statement."""
    if (account is None) == (accountId is None):
        raise HTTPException(status_code=422, detail="provide exactly one of account or accountId")
    try:
        with _uploaded_pages(file, password) as (data, pages):
            with connect() as conn, conn.cursor() as cur:
                household = _household(cur, owner.household_id)
                result = pipeline.parse(
                    conn,
                    pages=pages,
                    data=data,
                    original_filename=file.filename or "statement.pdf",
                    household=household,
                    profiles=_profiles(cur, owner.household_id, layoutId),
                    account=None,
                    resolve_account=_resolver(cur, owner.household_id),
                    stage=False,
                    encrypted=password is not None,
                )
                if result.account is not None:
                    existing = _one(cur, result.account.id, owner.household_id)
                    raise HTTPException(
                        status_code=409, detail=f"this statement belongs to {existing.name}"
                    )
                captured = result.analysis.account_number_raw
                if captured is None:
                    raise HTTPException(
                        status_code=422, detail="this statement states no usable account number"
                    )
                source_sha256 = hashlib.sha256(data).hexdigest()
                source_layout_id = result.analysis.layout_id
                source_layout_version = result.analysis.layout_version

                # Serialize every statement-confirmation decision in this household. This keeps
                # a mask-only create's check adjacent to its insert and uses the same lock order
                # as account edits, so a concurrent type or identity change cannot invalidate
                # the decision made below.
                cur.execute(
                    "SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,)
                )

                if account is not None:
                    body = _account_body(account)
                    try:
                        identifier = _statement_identifier(body.accountType, captured)
                    except ValueError:
                        raise HTTPException(
                            status_code=422,
                            detail="this statement states no usable account number",
                        ) from None
                    if isinstance(identifier, CompleteIdentifier):
                        cur.execute(
                            """SELECT a.name
                                 FROM account a
                                 JOIN account_identifier ai
                                   ON ai.household_id = a.household_id AND ai.account_id = a.id
                                WHERE a.household_id = %s AND ai.kind = %s
                                  AND ai.normalized_value = %s AND ai.is_active""",
                            (
                                owner.household_id,
                                identifier.kind,
                                identifier.normalized_value,
                            ),
                        )
                    else:
                        cur.execute(
                            """SELECT a.name
                                 FROM account a
                                 JOIN account_identifier ai
                                   ON ai.household_id = a.household_id AND ai.account_id = a.id
                                WHERE a.household_id = %s
                                  AND a.account_type = %s::account_type
                                  AND ai.kind = %s AND ai.is_active
                                  AND COALESCE(ai.visible_suffix,
                                               right(ai.masked_value, 4)) = %s""",
                            (
                                owner.household_id,
                                body.accountType,
                                identifier.kind,
                                identifier.visible_suffix,
                            ),
                        )
                    duplicate = cur.fetchone()
                    if duplicate is not None:
                        raise HTTPException(
                            status_code=409, detail=f"this statement belongs to {duplicate[0]}"
                        )
                    return _insert_account(
                        cur,
                        body,
                        owner.household_id,
                        identifier=identifier,
                        identifier_provenance="statement",
                        source_sha256=source_sha256,
                        source_layout_id=source_layout_id,
                        source_layout_version=source_layout_version,
                        source_capture_key="account_number",
                    )

                assert accountId is not None
                cur.execute(
                    """SELECT account_type::text
                         FROM account WHERE id = %s AND household_id = %s FOR UPDATE""",
                    (accountId, owner.household_id),
                )
                target = cur.fetchone()
                if target is None:
                    raise HTTPException(status_code=404, detail="no such account")
                account_type = target[0]
                try:
                    identifier = _statement_identifier(account_type, captured)
                except ValueError:
                    raise HTTPException(
                        status_code=422, detail="this statement states no usable account number"
                    ) from None
                cur.execute(
                    """SELECT masked_value, normalized_value
                         FROM account_identifier
                        WHERE household_id = %s AND account_id = %s AND kind = %s AND is_active
                        ORDER BY created_at, id FOR UPDATE""",
                    (owner.household_id, accountId, identifier.kind),
                )
                existing_identifiers = cur.fetchall()
                if not existing_identifiers or all(
                    row[0][-4:] != identifier.visible_suffix for row in existing_identifiers
                ):
                    raise HTTPException(
                        status_code=409, detail="the statement and account masks do not match"
                    )
                if isinstance(identifier, CompleteIdentifier) and any(
                    row[1] is not None and row[1] != identifier.normalized_value
                    for row in existing_identifiers
                ):
                    raise HTTPException(
                        status_code=409, detail="this account already has a different identity"
                    )
                if isinstance(identifier, CompleteIdentifier) and not any(
                    row[1] == identifier.normalized_value for row in existing_identifiers
                ):
                    _replace_account_identifier(
                        cur,
                        owner.household_id,
                        accountId,
                        identifier.kind,
                        identifier,
                        provenance="statement",
                        source_sha256=source_sha256,
                        source_layout_id=source_layout_id,
                        source_layout_version=source_layout_version,
                        source_capture_key="account_number",
                    )
                response.status_code = 200
                return _one(cur, accountId, owner.household_id)
    except DuplicateDocument as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except pipeline.LayoutNotIdentified as exc:
        raise _unidentified(exc) from None
    except (pipeline.AccountNotResolved, FxCoverageError, StorageError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="account identity already exists") from None
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
    ) as exc:
        raise HTTPException(status_code=422, detail=_account_write_error(exc)) from None
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# ---------------------------------------------------------------------------------------
# The review path. `POST /parse` stages; these three turn a staged batch into ledger rows.
# ---------------------------------------------------------------------------------------

# `staged_transaction.status` (V13). `new`, `duplicate` and `near_duplicate` are what staging
# produces; `needs_review` arrives with M4's classifier. Review moves rows to the last two.
DECIDABLE = frozenset({"accepted", "rejected"})

# What `reconciliation_status` means when it is null — a batch that failed before it reconciled.
# The same words V14 uses in its own refusal, so the screen and the database say one thing.
NOT_RECONCILED = "not reconciled"

# One sentence for "nothing was accepted", said before the work and again after it, so the two
# checks below cannot drift into describing the same refusal two different ways.
EMPTY_COMMIT = "this batch has no rows accepted for import; accept some first"

# How far the confirmed-distinct override will advance the occurrence index looking for a free
# one. The index counts identical (date, amount, normalised description) rows, so reaching this
# many would mean the same transaction on the same day sixty-four times over — which is a
# statement to look at, not a number to raise.
OVERRIDE_LIMIT = 64

# Spec §4.7: a row staged `duplicate` collides with something already committed, so accepting it
# is a confirmed-distinct override rather than an ordinary decision. Nothing else is overridden —
# `near_duplicate` only warns, and V10's unique index has no quarrel with it.
COLLIDING = "duplicate"

# `import_batch.status` (V12). `awaiting_review` is the only state a batch is still a working set
# in: every route below refuses anything else, and V14 refuses it again from inside the database.
# Named rather than spelled out three times, so the routes and the function cannot drift apart on
# which state that is.
REVIEWABLE = "awaiting_review"
COMPLETED = "completed"
CANCELLED = "cancelled"


class ReviewRow(BaseModel):
    """One staged row as the review screen reads it.

    Money is a string and the frozen pair travels with the row: the screen shows what will be
    written, not a figure it recomputes (`api-contracts.html` §6).
    """

    id: UUID
    sourcePageNumber: int | None
    sourceRowNumber: int
    bookedDate: date
    valueDate: date | None
    descriptionRaw: str
    descriptionClean: str | None
    referenceRaw: str | None
    amount: str
    currency: str
    amountBase: str
    amountSecondary: str
    proposedTxnType: str | None
    status: str
    duplicateOfTransactionId: UUID | None
    nearDuplicateOfId: UUID | None
    # The cascade's proposal, and which step made it. `classificationConfidence` is a string
    # for the same reason money is: it is a NUMERIC the screen displays and never computes on.
    # The name is the category's own, never a merchant string lifted from the document — it
    # comes from `category.name`, which the owner or the seed taxonomy wrote.
    proposedCategoryId: UUID | None
    proposedCategoryName: str | None
    classifiedBy: str
    classificationConfidence: str


class ReviewGroup(BaseModel):
    """Rows sharing a status, and how many there are.

    `statement-ingestion.html` §10 groups by attention needed rather than chronologically:
    chronological order means scrolling past the rows that are fine to reach the ones that are
    not. `count` is stated rather than left to the array's length because a later page of a
    large group will not carry every row.
    """

    status: str
    count: int
    rows: list[ReviewRow]


# `statement-ingestion.html` §10's order, most attention needed first — the reason the payload
# groups at all. Alphabetical would put `duplicate` above `needs_review`, which is the sort the
# grouping exists to avoid. A status absent here sorts after these and then alphabetically, so a
# vocabulary M4 extends still renders in a stable order rather than an arbitrary one.
GROUP_ORDER = ("needs_review", "new", "near_duplicate", "accepted", "rejected", "duplicate")


class ReviewPeriod(BaseModel):
    """The period as `import_batch` recorded it, which is not always all of it.

    Every field is optional because every underlying column is. `period_start` and `period_end`
    are nullable (V12), so a batch that failed before reconciliation has no period at all and
    must still be readable — a review screen that 500s on the batch the owner needs to look at
    is the wrong failure. `isDerived` is the flag the batch recorded at staging (V17): true
    where the period came from the rows' own dates, false where the statement stated it, and
    null only for a batch staged before that column existed. It is never inferred — a period
    derived from rows dated 1 and 2 March is indistinguishable afterwards from one a statement
    stated as 1-2 March, and guessing either way labels a provenance nobody observed
    (Invariant I7).
    """

    start: date | None
    end: date | None
    isDerived: bool | None


class ReviewBatch(BaseModel):
    batchId: UUID
    documentId: UUID | None
    accountId: UUID
    status: str
    layoutId: UUID | None
    layoutVersion: int | None
    period: ReviewPeriod
    rowsExtracted: int
    reconciliation: Reconciliation
    groups: list[ReviewGroup]


class RowDecision(BaseModel):
    """A bulk decision. Reviewing 200 rows one at a time is why people abandon these tools."""

    rowIds: list[UUID]
    decision: str


class DecisionResult(BaseModel):
    updated: int


class CommitResult(BaseModel):
    batchId: UUID
    rowsImported: int
    status: str
    # A card statement's cycle, written in the same transaction as its rows — or, when the
    # statement did not yield what a cycle requires, None and the facts that were missing.
    cardStatementId: UUID | None = None
    cardStatementMissing: list[str] = Field(default_factory=list)


class CancelResult(BaseModel):
    """What cancelling did. `rowsDiscarded` is stated because the deletion is the point.

    A screen that said only "cancelled" would leave the owner unable to tell a batch whose rows
    were discarded from one that had none to discard.
    """

    batchId: UUID
    status: str
    rowsDiscarded: int


_BATCH = """SELECT b.id, b.document_id, b.account_id, b.status, b.statement_layout_id,
                   b.layout_version, b.rows_extracted, b.period_start, b.period_end,
                   b.declared_opening_balance, b.declared_closing_balance,
                   b.computed_closing_balance, b.reconciliation_status, b.reconciliation_delta,
                   b.period_is_derived
              FROM import_batch b
             WHERE b.id = %s AND b.household_id = %s"""

_ROWS = """SELECT s.id, s.source_page_number, s.source_row_number, s.booked_date,
                  s.value_date, s.description_raw, s.description_clean, s.reference_raw,
                  s.amount, s.currency, s.amount_base, s.amount_secondary,
                  s.proposed_txn_type, s.status, s.duplicate_of_transaction_id,
                  s.near_duplicate_of_id, s.proposed_category_id, c.name,
                  s.classified_by::text, s.classification_confidence
             FROM staged_transaction s
             LEFT JOIN category c ON c.id = s.proposed_category_id
            WHERE s.import_batch_id = %s AND s.household_id = %s
            ORDER BY s.source_row_number"""


# V21 holds both dates NOT NULL, and inventing either would break Invariant 7. So a card statement
# that did not yield them commits its rows and records no cycle, naming what was missing — rather
# than refusing the import and losing the transactions over a due date.
_CYCLE_REQUIRES = ("payment_due_date", "statement_date")

_CYCLE = """INSERT INTO card_statement
              (household_id, account_id, period_start, period_end, statement_date,
               payment_due_date, currency, opening_balance, closing_balance, statement_balance,
               minimum_due, credit_limit, source_document_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, greatest(-%s::numeric, 0), %s, %s, %s)
         RETURNING id"""


def _write_card_cycle(
    cur: psycopg.Cursor[Any],
    batch: tuple[Any, ...],
    household_id: UUID,
    read: dict[str, str] | None = None,
) -> tuple[UUID | None, list[str]]:
    """The cycle a committed card statement describes, inside the commit's own transaction.

    `closing_balance` is the batch's declared closing, signed as I8 reconciled it — negative on
    a card the owner owes on. `statement_balance` is what was due: its magnitude when owed and
    zero when in credit, computed in Postgres (ADR-0010; V21 refuses the swap). A refusal here
    raises, and the raise rolls the ledger rows back with it: the cycle and the rows land
    together or not at all (`statement-ingestion.html` §11).

    `read` is the facts when they were read from the original since staging — the backfill's
    case; otherwise they are the ones staging stored on the document.
    """
    cur.execute(
        """SELECT document_type::text, extracted_metadata, currency FROM document
            WHERE id = %s AND household_id = %s""",
        (batch[1], household_id),
    )
    document = cur.fetchone()
    if document is None or document[0] != "card_statement":
        return None, []
    facts: dict[str, str] = (document[1] or {}) if read is None else read
    opening, closing = batch[9], batch[10]
    missing = [key for key in _CYCLE_REQUIRES if key not in facts]
    if opening is None:
        missing.append("declared_opening_balance")
    if closing is None:
        missing.append("declared_closing_balance")
    if missing:
        return None, sorted(missing)
    try:
        cur.execute(
            _CYCLE,
            (
                household_id,
                batch[2],
                batch[7],
                batch[8],
                facts["statement_date"],
                facts["payment_due_date"],
                document[2],
                opening,
                closing,
                closing,
                facts.get("minimum_due"),
                facts.get("credit_limit"),
                batch[1],
            ),
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="this card already has a cycle ending on that date; nothing was committed",
        ) from None
    except (psycopg.errors.CheckViolation, psycopg.errors.RaiseException) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"the statement's cycle could not be recorded, so nothing was committed: "
            f"{_first_line(exc)}",
        ) from None
    written = cur.fetchone()
    assert written is not None
    return written[0], []


def _batch_row(cur: psycopg.Cursor[Any], batch_id: UUID, household_id: UUID) -> tuple[Any, ...]:
    cur.execute(_BATCH, (batch_id, household_id))
    row = cur.fetchone()
    if row is None:
        # One answer for "no such batch" and "another household's batch", matching what V14's
        # own function does and for the same reason: telling them apart tells a caller whether
        # an id they guessed exists.
        raise HTTPException(status_code=404, detail="no such import batch")
    batch: tuple[Any, ...] = row
    return batch


def _grouped(cur: psycopg.Cursor[Any], batch_id: UUID, household_id: UUID) -> list[ReviewGroup]:
    cur.execute(_ROWS, (batch_id, household_id))
    groups: dict[str, list[ReviewRow]] = {}
    for row in cur.fetchall():
        groups.setdefault(str(row[13]), []).append(
            ReviewRow(
                id=row[0],
                sourcePageNumber=row[1],
                sourceRowNumber=row[2],
                bookedDate=row[3],
                valueDate=row[4],
                # Carried raw here and screened by `read_import_batch` before the payload
                # leaves — see `_screen`. The stored text is the audit record and stays
                # unredacted in the table (spec §5.6); the redaction belongs on the way out.
                descriptionRaw=row[5],
                descriptionClean=row[6],
                referenceRaw=row[7],
                amount=str(row[8]),
                currency=row[9],
                amountBase=str(row[10]),
                amountSecondary=str(row[11]),
                proposedTxnType=row[12],
                status=str(row[13]),
                duplicateOfTransactionId=row[14],
                nearDuplicateOfId=row[15],
                proposedCategoryId=row[16],
                proposedCategoryName=row[17],
                classifiedBy=row[18],
                classificationConfidence=str(row[19]),
            )
        )
    return [
        ReviewGroup(status=status, count=len(rows), rows=rows)
        for status, rows in sorted(
            groups.items(),
            key=lambda item: (
                GROUP_ORDER.index(item[0]) if item[0] in GROUP_ORDER else len(GROUP_ORDER),
                item[0],
            ),
        )
    ]


def _screen(groups: list[ReviewGroup]) -> None:
    """Redact every untyped document string in the payload, in place.

    Spec §5.6 leaves this to 1.1b explicitly: `staged_transaction.description_raw` is stored
    unscreened because it is the audit record, and whether the review payload screens it on the
    way out was left to be decided rather than inherited. It is screened — `PAYMENT RECEIVED
    CARD 4111 1111 1111 1111` is narration, and the reviewer having already opened the PDF does
    not put a card number in a response body. Same redactor as the parse preview, so the two
    routes cannot drift apart on what a digit run is.

    No capture is passed: the statement's own account number is never stored (never-store list),
    so there is nothing here to match it against beyond the digit-run rule that already catches
    it.

    `proposedCategoryName` is deliberately **not** screened, and that is a statement about where
    it comes from rather than an omission: it is `category.name`, written by the owner or by the
    seed taxonomy, and no path puts document text into that column. A merchant name lifted from
    a statement would be a different field with a different origin, and would be screened.
    """
    redact = _preview_redactor(None)
    for group in groups:
        for row in group.rows:
            row.descriptionRaw = redact(row.descriptionRaw)
            if row.descriptionClean is not None:
                row.descriptionClean = redact(row.descriptionClean)
            if row.referenceRaw is not None:
                row.referenceRaw = redact(row.referenceRaw)


@router.get("/import/{batch_id}")
def read_import_batch(
    batch_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> ReviewBatch:
    with connect() as conn, conn.cursor() as cur:
        batch = _batch_row(cur, batch_id, owner.household_id)
        groups = _grouped(cur, batch_id, owner.household_id)

    _screen(groups)

    return ReviewBatch(
        batchId=batch[0],
        documentId=batch[1],
        accountId=batch[2],
        status=str(batch[3]),
        layoutId=batch[4],
        layoutVersion=batch[5],
        period=ReviewPeriod(start=batch[7], end=batch[8], isDerived=batch[14]),
        rowsExtracted=batch[6],
        reconciliation=Reconciliation(
            # Nullable in V12, and V14 reads it as `COALESCE(…, 'not reconciled')` — a batch
            # that failed before reconciling has no verdict. `str()` over the null put the
            # literal "None" on the wire, which is not in the column's own CHECK vocabulary and
            # is not a state any consumer can be expected to recognise.
            status=str(batch[12]) if batch[12] is not None else NOT_RECONCILED,
            declaredOpening=_out(batch[9]),
            # An opening the statement did not declare is one the walk derived. This mirrors
            # the parse-time verdict rather than restating it: the batch stores the figure, not
            # the flag.
            openingIsDerived=batch[9] is None,
            declaredClosing=_out(batch[10]),
            computedClosing=_out(batch[11]),
            delta=_out(batch[13]),
            firstDivergentRow=None,
            previousBalance=None,
            amount=None,
            candidateRows=[],
            message="",
        ),
        groups=groups,
    )


_COLLIDING_ROWS = """SELECT id, account_id, booked_date, amount, description_raw
                       FROM staged_transaction
                      WHERE import_batch_id = %s AND household_id = %s
                        AND id = ANY(%s) AND status = %s
                      ORDER BY source_row_number"""

# What a candidate hash must not already be. Both halves matter: the committed side is what
# V10's unique index rejects, and the staged side is every row still able to reach the ledger —
# the commit inserts a batch's rows together, so two overrides landing on one hash fail that
# same index a moment later, having passed every check that looked only at the ledger.
#
# Scoped by account rather than by batch. `_refuse_second_staging` refuses a second staging only
# for a period the account already has open, so two unreviewed batches covering *different*
# periods coexist — which is exactly the overlapping Jul-Aug / Aug-Sep pair section 4.7 is
# written around, and where the same transaction is staged `duplicate` in both. Stopping at this
# batch's edge would hand both of them the same next index.
_TAKEN = """SELECT dedupe_hash FROM transaction
             WHERE household_id = %s AND account_id = %s AND dedupe_hash = ANY(%s)
            UNION
            SELECT dedupe_hash FROM staged_transaction
             WHERE household_id = %s AND account_id = %s AND id <> %s
               AND dedupe_hash = ANY(%s)"""


def _override_hashes(
    cur: psycopg.Cursor[Any], batch_id: UUID, household_id: UUID, row_ids: list[UUID]
) -> None:
    """Re-fingerprint every colliding row being accepted, in place.

    Spec §4.7: accepting a `duplicate` is the owner saying this is a different transaction that
    happens to look identical. V10's unique `(household_id, account_id, dedupe_hash)` index means
    the colliding hash cannot commit unchanged, so the row is re-fingerprinted with its occurrence
    index advanced to the next free one — deterministic and stable, never a random suffix, because
    a later statement carrying both occurrences must hash them 0 and 1 and recognise both as
    duplicates. That is why the index is advanced rather than the row being marked exempt.

    Recomputed in the `occ:` namespace whatever disambiguated it before (spec §7 item 12): a
    reference-disambiguated row that collided did so on its reference, and advancing a counter
    that is not in the fingerprint would change nothing.

    One row at a time, and each write is visible to the next row's search, so two overrides
    cannot land on one hash.
    """
    cur.execute(_COLLIDING_ROWS, (batch_id, household_id, row_ids, COLLIDING))
    for row_id, account_id, booked_date, amount, description_raw in cur.fetchall():
        candidates = [
            fingerprint(
                account_id=account_id,
                booked_date=booked_date,
                amount=amount,
                description_raw=description_raw,
                disambiguator=f"{OCCURRENCE}{index}",
            )
            for index in range(OVERRIDE_LIMIT)
        ]
        cur.execute(
            _TAKEN,
            (household_id, account_id, candidates, household_id, account_id, row_id, candidates),
        )
        taken = {found[0] for found in cur.fetchall()}
        free = next((candidate for candidate in candidates if candidate not in taken), None)
        if free is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "this row already exists in the ledger too many times over to accept "
                    "another copy; check the statement before overriding"
                ),
            )
        cur.execute("UPDATE staged_transaction SET dedupe_hash = %s WHERE id = %s", (free, row_id))


@router.patch("/import/{batch_id}/rows")
def decide_import_rows(
    batch_id: UUID, body: RowDecision, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> DecisionResult:
    if body.decision not in DECIDABLE:
        raise HTTPException(
            status_code=422,
            detail="decision must be one of: " + ", ".join(sorted(DECIDABLE)),
        )

    with connect() as conn, conn.cursor() as cur:
        batch = _batch_row(cur, batch_id, owner.household_id)
        if str(batch[3]) != REVIEWABLE:
            # Once the rows are in the ledger the staged copy is a record, not a working set.
            raise HTTPException(
                status_code=409, detail=f"this batch is {batch[3]} and can no longer be decided"
            )

        row_ids = list(body.rowIds)
        if body.decision == "accepted":
            # Before the status changes, so a refusal here leaves the batch exactly as it was —
            # and so V13's accepted_row_is_committable sees the row in its final shape.
            _override_hashes(cur, batch_id, owner.household_id, row_ids)

        try:
            cur.execute(
                """UPDATE staged_transaction SET status = %s
                    WHERE import_batch_id = %s AND household_id = %s AND id = ANY(%s)""",
                (body.decision, batch_id, owner.household_id, row_ids),
            )
        except psycopg.errors.CheckViolation as exc:
            # V13's accepted_row_is_committable: an accepted row carries a txn_type, and a zero
            # amount only as an adjustment. The database is the guard — a row that can be
            # accepted but never committed is a dead end it refuses to represent — but its
            # refusal is a client error about this request, not a 500.
            raise HTTPException(
                status_code=422,
                detail=(
                    "a row cannot be accepted without a transaction type, and a zero amount "
                    "only as an adjustment"
                ),
            ) from exc
        updated = cur.rowcount
        if updated != len(set(row_ids)):
            # Refused whole rather than reported as a partial success. A count that says 1 when
            # the caller asked for 2 is how a reviewer comes to believe they decided rows they
            # did not — and the transaction rolls back, so nothing is half-applied.
            raise HTTPException(
                status_code=422,
                detail="some of those rows are not in this batch; nothing was changed",
            )
    return DecisionResult(updated=updated)


@router.post("/import/{batch_id}/commit")
def commit_import(
    batch_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> CommitResult:
    """V14's `commit_import_batch`, and nothing else.

    Every rule about what may commit lives in that function, because the insert and the balance
    verification have to share one transaction (`api-contracts.html` §3). This route resolves
    the identity, calls it once, and translates its refusals — it does not re-implement any of
    them, which is how the two would drift.
    """
    with connect() as conn, conn.cursor() as cur:
        batch = _batch_row(cur, batch_id, owner.household_id)
        if str(batch[3]) == REVIEWABLE:
            # Narrowed to the state it was written for, rather than asked of every batch. A
            # cancelled batch has no accepted rows because cancelling deleted them, and
            # answering "accept some rows first" would describe the emptiness instead of what
            # actually happened to it. V14 refuses every other state with the state named, and
            # this guard exists only to catch the one case V14 would let through.
            cur.execute(
                """SELECT count(*) FROM staged_transaction
                    WHERE import_batch_id = %s AND household_id = %s AND status = 'accepted'""",
                (batch_id, owner.household_id),
            )
            accepted = cur.fetchone()
            if accepted is None or accepted[0] == 0:
                # V14 would complete the batch with rows_imported = 0 whenever the declared
                # closing already matched the standing balance — a statement marked imported
                # with none of it imported. Committing nothing is never what the reviewer meant.
                raise HTTPException(
                    status_code=422,
                    detail=EMPTY_COMMIT,
                )

        scope_to_owner(conn, owner.user_id)
        try:
            cur.execute("SELECT commit_import_batch(%s)", (batch_id,))
        except psycopg.errors.RaiseException as exc:
            # V14 raises with a sentence written for the owner. Its own message is the detail;
            # the database's context and position are not echoed.
            raise HTTPException(status_code=409, detail=_first_line(exc)) from None
        except psycopg.errors.InsufficientPrivilege as exc:
            raise HTTPException(status_code=404, detail="no such import batch") from exc
        except psycopg.Error as exc:
            raise HTTPException(status_code=409, detail=_first_line(exc)) from None
        row = cur.fetchone()
        imported = int(row[0]) if row is not None else 0

        if imported == 0:
            # The count above is read before V14 takes the household and batch rows FOR UPDATE,
            # so it answers for a moment that has passed: a decision rejecting every row can
            # commit in between, and the batch is then marked `completed` having imported
            # nothing — the state the count exists to prevent. What the function returned is the
            # only race-free answer, and raising here rolls the commit back whole rather than
            # leaving it applied. The count stays because it says this before any work is done.
            raise HTTPException(status_code=409, detail=EMPTY_COMMIT)

        # The cycle, inside the same transaction: if it cannot be written, neither are the rows.
        cycle_id, missing = _write_card_cycle(cur, batch, owner.household_id)

        # Read back rather than restated. This route re-implements none of V14's rules and the
        # status it sets is one of them; a literal here would go on reading `completed` on the
        # day the function stops writing it.
        cur.execute("SELECT status FROM import_batch WHERE id = %s", (batch_id,))
        committed = cur.fetchone()

    return CommitResult(
        batchId=batch_id,
        rowsImported=imported,
        status=str(committed[0]) if committed is not None else "",
        cardStatementId=cycle_id,
        cardStatementMissing=missing,
    )


def _not_cancellable(status: str) -> HTTPException:
    """Why this batch cannot be cancelled, in words that fit the state it is actually in.

    One function because the route asks twice — once from the status it read, once from the
    status the guarded update found instead — and two spellings of the same refusal would
    describe one batch two ways depending on which tab got there first.
    """
    if status == CANCELLED:
        # Its own sentence. A double submit is the ordinary way to reach this, and the generic
        # refusal would report back the state the caller was asking for.
        return HTTPException(status_code=409, detail="this batch is already cancelled")
    if status == COMPLETED:
        # V14's own words, so the two refusals of an imported batch read the same.
        return HTTPException(status_code=409, detail="this batch has already been imported")
    return HTTPException(status_code=409, detail=f"a batch in state {status} cannot be cancelled")


@router.post("/import/{batch_id}/cancel")
def cancel_import(
    batch_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> CancelResult:
    """Abandon a staged batch, and take its staged rows with it.

    Not a reversal. A batch that never committed has nothing in the ledger to undo, and one that
    has committed cannot be cancelled at all. What this recovers is the **period**:
    `pipeline._refuse_second_staging` already counts `cancelled` among the states that can no
    longer commit, so an abandoned batch stops blocking its account's period the moment this
    runs. Before it existed there was no way to reach that state from the app, which is how the
    slice 1.1 acceptance run found a period blocked for good.

    **The staged rows are deleted rather than left carrying a status.** V7's
    `reporting_pair_is_locked()` discovers every table with `amount_base` beside `household_id`
    and counts rows, and `staged_transaction` has both (V13 says so in its own comment) — so
    rows left behind by a batch that can never commit would answer that question `true` for
    ever. It bites during onboarding, where the pair is still meant to be changeable: stage a
    batch, notice the pair is wrong, cancel, and otherwise still be unable to change it.

    It frees the file too, without deleting it. The document row and its stored original stay —
    the cancelled batch still names them, which is the record of what was attempted — and layer 1
    does not count a cancelled batch, so the same bytes stage again onto the same row
    (`storage.refuse_if_already_imported`, `pipeline._stage`).
    """
    with connect() as conn, conn.cursor() as cur:
        batch = _batch_row(cur, batch_id, owner.household_id)
        if str(batch[3]) != REVIEWABLE:
            raise _not_cancellable(str(batch[3]))

        # The status above answered for a moment that has already passed: V14 takes the batch
        # row FOR UPDATE and completes it, so a commit in a second tab can land between that
        # read and this write. The predicate is what closes the window — under READ COMMITTED
        # this statement blocks on that lock and re-evaluates against the row the commit left,
        # so a batch that stopped being reviewable updates nothing. It runs *before* the delete
        # for the same reason: its row lock is what stops a commit from starting underneath the
        # rows this is about to remove.
        cur.execute(
            """UPDATE import_batch SET status = %s::import_status, completed_at = now()
                WHERE id = %s AND household_id = %s AND status = %s::import_status""",
            (CANCELLED, batch_id, owner.household_id, REVIEWABLE),
        )
        if cur.rowcount == 0:
            cur.execute(
                "SELECT status FROM import_batch WHERE id = %s AND household_id = %s",
                (batch_id, owner.household_id),
            )
            current = cur.fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="no such import batch")
            raise _not_cancellable(str(current[0]))

        # The household is carried here too. The service connects privileged and bypasses RLS
        # (ADR-0013, ADR-0016), so the predicate is the boundary rather than a convenience over
        # one — and this is a delete.
        cur.execute(
            "DELETE FROM staged_transaction WHERE import_batch_id = %s AND household_id = %s",
            (batch_id, owner.household_id),
        )
        discarded = cur.rowcount
    return CancelResult(batchId=batch_id, status=CANCELLED, rowsDiscarded=discarded)


def _first_line(exc: Exception) -> str:
    """The refusal's own sentence. Postgres appends CONTEXT and a query position, and the query
    text can carry the parameters that caused the failure."""
    return str(exc).splitlines()[0].strip()
