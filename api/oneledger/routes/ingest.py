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
from ..db import connect, scope_to_owner
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
    is the wrong failure. `isDerived` is null because **the batch does not record it**: the flag
    exists on the parse-time verdict and no column carries it, so it is reported as unrecorded
    rather than guessed. Guessing `true` labels a declared period derived, and guessing `false`
    labels a computed one declared; Invariant I7 forbids inventing either. Recording it needs a
    column, which is a schema change this task does not carry.
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


_BATCH = """SELECT b.id, b.document_id, b.account_id, b.status, b.statement_layout_id,
                   b.layout_version, b.rows_extracted, b.period_start, b.period_end,
                   b.declared_opening_balance, b.declared_closing_balance,
                   b.computed_closing_balance, b.reconciliation_status, b.reconciliation_delta
              FROM import_batch b
             WHERE b.id = %s AND b.household_id = %s"""

_ROWS = """SELECT id, source_page_number, source_row_number, booked_date, value_date,
                  description_raw, description_clean, reference_raw, amount, currency,
                  amount_base, amount_secondary, proposed_txn_type, status,
                  duplicate_of_transaction_id, near_duplicate_of_id
             FROM staged_transaction
            WHERE import_batch_id = %s AND household_id = %s
            ORDER BY source_row_number"""


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
        period=ReviewPeriod(start=batch[7], end=batch[8], isDerived=None),
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
        if str(batch[3]) != "awaiting_review":
            # Once the rows are in the ledger the staged copy is a record, not a working set.
            raise HTTPException(
                status_code=409, detail=f"this batch is {batch[3]} and can no longer be decided"
            )

        cur.execute(
            """UPDATE staged_transaction SET status = %s
                WHERE import_batch_id = %s AND household_id = %s AND id = ANY(%s)""",
            (body.decision, batch_id, owner.household_id, list(body.rowIds)),
        )
        updated = cur.rowcount
        if updated != len(set(body.rowIds)):
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
        _batch_row(cur, batch_id, owner.household_id)
        cur.execute(
            """SELECT count(*) FROM staged_transaction
                WHERE import_batch_id = %s AND household_id = %s AND status = 'accepted'""",
            (batch_id, owner.household_id),
        )
        accepted = cur.fetchone()
        if accepted is None or accepted[0] == 0:
            # V14 would complete the batch with rows_imported = 0 whenever the declared closing
            # already matched the standing balance — a statement marked imported with none of it
            # imported. Committing nothing is never what the reviewer meant.
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

        # Read back rather than restated. This route re-implements none of V14's rules and the
        # status it sets is one of them; a literal here would go on reading `completed` on the
        # day the function stops writing it.
        cur.execute("SELECT status FROM import_batch WHERE id = %s", (batch_id,))
        committed = cur.fetchone()

    return CommitResult(
        batchId=batch_id,
        rowsImported=imported,
        status=str(committed[0]) if committed is not None else "",
    )


def _first_line(exc: Exception) -> str:
    """The refusal's own sentence. Postgres appends CONTEXT and a query position, and the query
    text can carry the parameters that caused the failure."""
    return str(exc).splitlines()[0].strip()
