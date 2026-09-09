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

from datetime import date
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..fxrange import FxCoverageError
from ..ingest import pipeline
from ..ingest.reconcile import Verdict
from ..ingest.storage import DuplicateDocument, StorageError
from ..ingest.words import ExtractionError
from ..ingest.words import extract_pages as read_pdf_pages
from ..money import _out
from .accounts import _account_number_identity as account_number_identity
from .accounts import resolve_account_by_hash
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


class ParseResponse(BaseModel):
    staged: bool
    batchId: UUID | None
    documentId: UUID | None
    accountId: UUID
    layoutId: UUID
    layoutVersion: int
    period: ParsePeriod
    rowsExtracted: int
    counts: dict[str, int]
    reconciliation: Reconciliation
    rows: list[ParseRow]
    failures: list[ParseFailure]


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
    """Hash the statement's own account number and look the account up by it.

    This closure is why `ingest/` needs no import from `routes/`: the salted hash is the account
    identity `routes/accounts.py` already defines, and the never-store list forbids the number
    itself ever being stored or compared in the clear (`security-and-privacy.html` §1).
    """

    def resolve(account_number_raw: str) -> pipeline.AccountContext | None:
        # `account_number_identity` is the one rule that decides what gets hashed, shared with
        # account creation rather than re-derived. A second spelling of it — `str.isdigit()`,
        # say, which keeps non-ASCII digits and silently drops the letters account creation
        # refuses — hashes a different string from the one stored, and the lookup then misses
        # for a reason no message could explain. It also raises 503 naming ACCOUNT_HASH_SALT
        # when the salt is unset, which is the refusal that condition is owed: returning None
        # here would report a misconfigured deployment as "you do not hold this account".
        try:
            _, digest = account_number_identity(account_number_raw)
        except ValueError:
            # Not an account number at all — a capture that matched something else on the page.
            # The number itself is never echoed (spec §5.6).
            return None
        found = resolve_account_by_hash(cur, digest, household_id)
        if found is None:
            return None
        return pipeline.AccountContext(id=found.id, currency=found.currency)

    return resolve


def _rows(result: pipeline.ParseResult) -> list[ParseRow]:
    # Statement narration is untyped text out of the document, so it goes out screened — the
    # same treatment `POST /layouts/propose` gives its own preview rows, and for the same
    # reason: a card payment line reads `PAYMENT RECEIVED CARD 4111 1111 1111 1111`, and spec
    # §5.6 keeps a digit run of seven or more out of every response regardless of which field
    # it sat in. Dates, amounts and balances are typed and pass through untouched, so the
    # preview still answers the only question it is for — did the columns land where I mapped
    # them. The account-number capture is passed in so its own spelling is screened too.
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
            originalCurrency=row.original_currency,
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


def _response(result: pipeline.ParseResult) -> ParseResponse:
    analysis = result.analysis
    return ParseResponse(
        staged=result.staged,
        batchId=result.batch_id,
        documentId=result.document_id,
        accountId=result.account.id,
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
        rows=_rows(result),
        failures=[
            ParseFailure(
                sourcePageNumber=failure.source_page_number,
                sourceRowNumber=failure.source_row_number,
                reason=failure.reason,
            )
            for failure in analysis.failures
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


@router.post("/parse")
def parse_statement(
    file: Annotated[UploadFile, File()],
    owner: Annotated[OwnerSession, Depends(require_owner)],
    stage: Annotated[bool, Form()] = False,
    layoutId: Annotated[UUID | None, Form()] = None,
    accountId: Annotated[UUID | None, Form()] = None,
    password: Annotated[str | None, Form()] = None,
) -> ParseResponse:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=422, detail="the uploaded file is empty")

    temporary_path: Path | None = None
    try:
        # pdfplumber reads a path, and the upload is a stream. The temporary file is this
        # request's alone and is removed however the request ends — the durable copy is written
        # by `storage.store`, under the checksum, and only on the staging path.
        with NamedTemporaryFile(prefix="oneledger-parse-", suffix=".pdf", delete=False) as temp:
            temporary_path = Path(temp.name)
            file.file.seek(0)
            copyfileobj(file.file, temp)
        pages = read_pdf_pages(temporary_path, password=password)

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
                # The blob `storage.store` writes is the original, so for an encrypted upload it
                # is still encrypted. `document.is_password_protected` is the only record of
                # that — the password itself is on the never-store list and goes no further than
                # this frame — and a column defaulting to false would assert the opposite.
                encrypted=password is not None,
            )
        return _response(result)
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
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
