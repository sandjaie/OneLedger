# api/oneledger/ingest/pipeline.py
"""M2 Task 5: the import pipeline and the staging transaction.

`statement-ingestion.html` §1 orders the stages and this module is that order made executable:
identify, extract, normalize, dedupe, reconcile, enrich FX, stage. Two properties shape the
code more than anything else.

**A dry run writes nothing.** Not a staged row, not a `document`, not an `fx_rate`. `POST /parse`
defaults to it (spec §4.3), and the import screen shows the whole summary before anything is
committed to. So the FX step — the one stage that writes reference data of its own — runs only
on the staging path.

**A blocked batch leaves nothing behind** (spec §5.5). Every refusal below happens before the
transaction opens, or inside it, so there is no state to clean up either way. That is stricter
than `data-model.html` §12's "a blocked import records why it blocked", and deliberately:
`document.sha256` is unique per household, so a persisted blocked document would reject the
owner's own retry after they re-mapped the layout.

This is the only module under `ingest/` that may open a database connection, and none of them
may import from `routes/` (`testing-and-data-quality.html` §2). The account lookup is therefore
inverted: the caller passes a resolver, and the hashing that lookup needs stays in the route
that already owns `ACCOUNT_HASH_SALT`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import psycopg
from fastapi import HTTPException
from psycopg.types.json import Json

from ..fxrange import RateSource, ensure_rates
from ..money import _frozen, translate
from ..schemas.staging import StagedTransaction
from . import storage

# `Candidate` is aliased: this module already has one, for a layout that might match the
# statement, and two Candidates in one file is the same trap the cascade's own entry point
# is named `classify_row` to avoid.
from .classify import Alias, Knowledge, Prior, Rule, classify_row
from .classify import Candidate as ClassifiableRow
from .dedupe import CommittedRow, DedupeVerdict, classify
from .engine import ExtractionResult, extract_pages, matches
from .normalize import (
    MetadataFailure,
    NormalizableProfile,
    NormalizedRow,
    RowFailure,
    StatementPeriod,
    card_facts,
    normalize,
)
from .reconcile import STATUS_MATCHED, ReconcilableProfile, Verdict, reconcile
from .words import ExtractionError, Page

# Fingerprint evidence below this is not evidence. `engine.matches` returns the fraction of a
# layout's fingerprints that appear, so half of them is the weakest reading that still says the
# document resembles this layout at all; anything under it is noise from a shared word.
IDENTIFICATION_FLOOR = 0.5

# The documents volume compose mounts at `/var/lib/oneledger/documents` (ADR-0014). The column
# records which store a blob lives in, so that a later move has something to migrate from.
_BUCKET = "documents"


class PipelineError(ExtractionError):
    """A refusal the owner can act on, phrased for them.

    Subclasses `ExtractionError` so a route catches one exception for the whole ingest surface,
    the way `POST /layouts/propose` already does. Never carries the raw filename, the file bytes
    or any digit run from the document (spec §5.6).
    """


class ImportableProfile(NormalizableProfile, ReconcilableProfile, Protocol):
    """A saved layout: everything the engine, normalization and reconciliation read, plus the
    three fields that pin which mapping produced a row (Invariant I6)."""

    @property
    def id(self) -> UUID: ...

    @property
    def version(self) -> int: ...

    @property
    def name(self) -> str: ...


@dataclass(frozen=True)
class AccountContext:
    """The account a statement is being imported into, resolved before normalization."""

    id: UUID
    currency: str


@dataclass(frozen=True)
class HouseholdContext:
    """The reporting pair every staged row freezes against — roles, never currency names."""

    id: UUID
    base_currency: str
    secondary_currency: str


@dataclass(frozen=True)
class Candidate:
    profile: ImportableProfile
    score: float


@dataclass(frozen=True)
class Identification:
    """`chosen` is None whenever the answer is "ask" — nothing scored, or two scored alike."""

    chosen: ImportableProfile | None
    candidates: list[Candidate]


@dataclass(frozen=True)
class Analysis:
    """What one profile made of one statement, before anything is written.

    `rows` and `verdicts` are parallel and in statement order. `counts` is taken over the
    verdicts rather than the extracted lines: a line that failed to normalize has no verdict,
    and counting it would overstate what is about to be staged.
    """

    layout_id: UUID
    layout_version: int
    rows: list[NormalizedRow]
    verdicts: list[DedupeVerdict]
    failures: list[RowFailure]
    period: StatementPeriod
    reconciliation: Verdict
    counts: dict[str, int]
    rows_extracted: int
    page_count: int
    metadata: dict[str, str]
    # The statement's own account number, for the caller's resolver. Kept out of `repr` for the
    # same reason `ExtractionResult` keeps it out of its own: spec §5.6 forbids it reaching any
    # log line, and a dataclass repr is how it would get there.
    account_number_raw: str | None = field(repr=False, default=None)
    # Reported, never gating: `blocks_staging` does not read these. A due date that will not
    # read is not worth the statement's transactions.
    metadata_failures: list[MetadataFailure] = field(default_factory=list)


@dataclass(frozen=True)
class ParseResult:
    """The `POST /parse` payload. `batch_id` and `document_id` are set only on a staged run."""

    analysis: Analysis
    account: AccountContext
    staged: bool
    batch_id: UUID | None = None
    document_id: UUID | None = None


def _page_one_text(pages: Sequence[Page]) -> str:
    """Page one only, as `engine.matches` expects.

    A fingerprint is a letterhead. Searching the whole document would match a layout's name
    printed in a footnote on page nine and identify the statement from it.
    """
    if not pages:
        return ""
    return "\n".join(
        line.raw_text if line.raw_text is not None else " ".join(word.text for word in line.words)
        for line in pages[0].lines
    )


def identify(
    profiles: Sequence[ImportableProfile], pages: Sequence[Page], filename: str
) -> Identification:
    """Score every saved layout and choose one only when the choice is unambiguous.

    `statement-ingestion.html` §1 orders it "text fingerprint → filename hint → ask". The
    filename hint is not implemented and `engine.matches` discards the argument deliberately: a
    statement's filename is owner-chosen and routinely carries a card number, so scoring on it
    would both mis-identify and read a value the rest of this module works to keep out of
    everything it touches. That leaves fingerprint, then ask.

    A tie asks rather than picking the higher-scoring half of a coin toss. Column bands are
    geometry, not meaning, so the losing choice would still parse every row — into the wrong
    fields, with no error anywhere. One click is cheaper than that.
    """
    text = _page_one_text(pages)
    scored = sorted(
        (
            Candidate(profile=profile, score=matches(profile, text, filename))
            for profile in profiles
        ),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    if not scored or scored[0].score < IDENTIFICATION_FLOOR:
        return Identification(chosen=None, candidates=scored)
    if len(scored) > 1 and scored[1].score == scored[0].score:
        return Identification(chosen=None, candidates=scored)
    return Identification(chosen=scored[0].profile, candidates=scored)


def _maps_reference(profile: ImportableProfile) -> bool:
    """Whether the layout captures a bank reference, which layer 2 uses to disambiguate.

    Read off the profile rather than taken as an argument: the fingerprint's disambiguator is a
    property of the mapping, and a caller passing the wrong answer would silently change every
    hash the batch produces.
    """
    return any(column.field == "reference" for column in profile.columns)


def analyse(
    profile: ImportableProfile,
    pages: Sequence[Page],
    *,
    account_id: UUID,
    account_currency: str,
    committed: Sequence[CommittedRow],
) -> Analysis:
    """Extract, normalize, dedupe and reconcile — in that order, over values, writing nothing.

    Reconciliation runs before FX enrichment, never after: the check is local and exact, it
    needs only the parsed rows and the statement's own declared balances, and running it first
    means a statement that does not reconcile costs no rate fetch at all
    (`statement-ingestion.html` §1).
    """
    return _analysed(
        profile,
        _extracted(profile, pages),
        account_id=account_id,
        account_currency=account_currency,
        committed=committed,
    )


def _extracted(profile: ImportableProfile, pages: Sequence[Page]) -> ExtractionResult:
    """Extraction, plus the one thing a page-level caller must not be allowed to skip.

    Split out so `parse` can read the statement's account number off the result and resolve the
    account before normalization needs its currency, without running the engine twice.
    """
    extraction = extract_pages(profile, list(pages))
    if not extraction.has_text:
        # Zero rows and no anchors would otherwise reconcile 0 == 0 and stage nothing, with no
        # sign that the file was an image rather than an empty month.
        raise PipelineError("this file is scanned rather than text, so no rows can be read from it")
    return extraction


def _analysed(
    profile: ImportableProfile,
    extraction: ExtractionResult,
    *,
    account_id: UUID,
    account_currency: str,
    committed: Sequence[CommittedRow],
) -> Analysis:
    batch = normalize(profile, extraction, account_currency=account_currency)
    verdicts = classify(
        batch.rows,
        account_id=account_id,
        maps_reference=_maps_reference(profile),
        committed=list(committed),
    )
    verdict = reconcile(
        profile,
        batch.rows,
        declared_opening=extraction.declared_opening,
        declared_closing=extraction.declared_closing,
    )
    facts, metadata_failures = card_facts(extraction.metadata, profile.dateFormat, batch.period)
    return Analysis(
        layout_id=profile.id,
        layout_version=profile.version,
        rows=batch.rows,
        verdicts=verdicts,
        failures=batch.failures,
        period=batch.period,
        reconciliation=verdict,
        counts=dict(Counter(item.status for item in verdicts)),
        rows_extracted=len(batch.rows) + len(batch.failures),
        page_count=extraction.page_count,
        metadata=facts,
        account_number_raw=extraction.account_number_raw,
        metadata_failures=metadata_failures,
    )


class LayoutNotIdentified(PipelineError):
    """No saved layout fingerprinted this file well enough, or two fingerprinted it alike.

    Carries the scored candidates so the import screen can render `api-contracts.html` §4's
    layout picker — "a layout picker listing the four, with the fingerprint scores" — rather
    than a dead end the owner can only retry.
    """

    def __init__(self, candidates: list[Candidate]) -> None:
        self.candidates = candidates
        super().__init__(
            "no saved layout identifies this statement; choose one and parse again"
            if not candidates
            else "no saved layout identifies this statement unambiguously; choose one and "
            "parse again"
        )


class AccountNotResolved(PipelineError):
    """The statement's own account number matched no account this household holds."""


# Only the account's own committed rows: layer 3's "same account" condition is enforced in
# `dedupe.classify`, and handing it a wider set would make it filter rows it was given for
# nothing. Unbounded by date on purpose — a statement may restate a row from any month, and the
# near-duplicate window is applied per row rather than per query.
_COMMITTED = """SELECT id, account_id, dedupe_hash, booked_date, amount, description_raw
                  FROM transaction
                 WHERE household_id = %s AND account_id = %s"""


def committed_rows(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID
) -> list[CommittedRow]:
    """What layers 2 and 3 compare against. Committed rows only — never staged ones.

    V13 gives `duplicate_of_transaction_id` and `near_duplicate_of_id` a foreign key to
    `transaction(id)`, so a staged row is not something either verdict can name (spec §7 item
    11). The gap that leaves — two unreviewed batches of the same statement, differing by a byte
    so layer 1 misses them — is closed by `_refuse_second_staging` below, which is the check a
    verdict column cannot express.
    """
    cur.execute(_COMMITTED, (household_id, account_id))
    return [
        CommittedRow(
            id=row[0],
            account_id=row[1],
            dedupe_hash=row[2],
            booked_date=row[3],
            amount=Decimal(row[4]),
            description_raw=row[5],
        )
        for row in cur.fetchall()
    ]


# Anything not finished is unreviewed. `failed` and `cancelled` are finished in the sense that
# matters here: neither can still commit rows, so neither can collide with this batch.
_UNREVIEWED = """SELECT id FROM import_batch
                  WHERE household_id = %s AND account_id = %s
                    AND status NOT IN ('completed','failed','cancelled')
                    AND period_start = %s AND period_end = %s
                  LIMIT 1"""


def _refuse_second_staging(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID, period: StatementPeriod
) -> None:
    """Refuse a second staging of a period this account already has open (spec §7 item 11).

    Layer 1 stops the identical file. It cannot stop the same statement re-downloaded a byte
    apart — a regenerated PDF, a different timestamp in the trailer — and every row of that
    second copy is verdict `new`, because dedupe compares against committed rows and neither
    batch has committed. Both would then commit and collide on V10's `idx_txn_dedupe`, or worse,
    commit twice under two hashes. This is the pipeline's job precisely because the verdict
    columns cannot reference a staged row.
    """
    cur.execute(_UNREVIEWED, (household_id, account_id, period.start, period.end))
    existing = cur.fetchone()
    if existing is not None:
        raise PipelineError(
            f"this account already has an unreviewed import for {period.start} to "
            f"{period.end} (batch {existing[0]}); review or discard it before staging another"
        )


def _knowledge_for(cur: psycopg.Cursor[Any], household_id: UUID) -> Knowledge:
    """Everything the cascade may consult, read once for the batch.

    `ingest/` may not import from `routes/` (`testing-and-data-quality.html` §2), so the query
    lives here and the cascade stays a function of values. It is deliberately the same
    knowledge `POST /classify` gathers: one cascade with one set of inputs means the answer a
    row gets at import is by construction the answer it gets when the rules screen re-runs it.
    """
    cur.execute(
        """SELECT id, priority, origin, match_description_pattern, match_pattern_kind::text,
                  match_account_id, match_currency, match_amount_min, match_amount_max,
                  match_amount_exact, match_direction, match_country, match_reference_pattern,
                  set_category_id, set_merchant_id, set_txn_type::text,
                  set_payment_method::text, set_country, set_tags,
                  set_is_transfer_candidate, set_counterparty_account_id
             FROM classification_rule
            WHERE household_id = %s AND is_active
            ORDER BY priority, id""",
        (household_id,),
    )
    saved = [
        Rule(
            id=row[0],
            priority=row[1],
            origin=row[2],
            match_description_pattern=row[3],
            match_pattern_kind=row[4],
            match_account_id=row[5],
            match_currency=row[6],
            match_amount_min=row[7],
            match_amount_max=row[8],
            match_amount_exact=row[9],
            match_direction=row[10],
            match_country=row[11],
            match_reference_pattern=row[12],
            set_category_id=row[13],
            set_merchant_id=row[14],
            set_txn_type=row[15],
            set_payment_method=row[16],
            set_country=row[17],
            set_tags=tuple(row[18] or []),
            set_is_transfer_candidate=row[19],
            set_counterparty_account_id=row[20],
        )
        for row in cur.fetchall()
    ]

    cur.execute(
        """SELECT a.merchant_id, a.pattern, a.pattern_kind::text, a.priority,
                  m.default_category_id
             FROM merchant_alias a
             JOIN merchant m ON m.id = a.merchant_id
            WHERE a.is_active AND (a.household_id IS NULL OR a.household_id = %s)
              AND m.household_id = %s""",
        (household_id, household_id),
    )
    aliases = [
        Alias(
            merchant_id=row[0],
            pattern=row[1],
            pattern_kind=row[2],
            priority=row[3],
            category_id=row[4],
        )
        for row in cur.fetchall()
    ]

    # Committed rows that already carry an answer. A row staged in this same batch is not
    # evidence: it has not been reviewed, and letting the first row of a statement decide the
    # rest of it would turn one wrong proposal into a page of them.
    cur.execute(
        """SELECT account_id, COALESCE(description_clean, description_raw), category_id,
                  merchant_id, txn_type::text, payment_method::text
             FROM transaction
            WHERE household_id = %s AND classified_by <> 'unclassified'""",
        (household_id,),
    )
    priors = [
        Prior(
            account_id=row[0],
            description_clean=row[1],
            category_id=row[2],
            merchant_id=row[3],
            txn_type=row[4],
            payment_method=row[5],
        )
        for row in cur.fetchall()
    ]

    return Knowledge(
        user_rules=[rule for rule in saved if rule.origin != "seed"],
        seed_rules=[rule for rule in saved if rule.origin == "seed"],
        aliases=aliases,
        priors=priors,
    )


def _staged(
    cur: psycopg.Cursor[Any],
    analysis: Analysis,
    *,
    account: AccountContext,
    household: HouseholdContext,
) -> list[StagedTransaction]:
    """Freeze every row against the household's reporting pair and validate the contract.

    The freeze happens here rather than at commit because `staged_transaction` carries
    `amount_base` and `amount_secondary` itself: a staged batch already locks the reporting pair
    (V7's `reporting_pair_is_locked`), and a row whose translation is inconsistent must fail in
    the service where the error names the row, not in Postgres where it names a constraint.
    """
    # The cascade runs here — after FX freezing, before staging (`statement-ingestion.html`
    # stages 6-7). It proposes; nothing it decides reaches the ledger without the review click.
    knowledge = _knowledge_for(cur, household.id)

    rows: list[StagedTransaction] = []
    for row, verdict in zip(analysis.rows, analysis.verdicts, strict=True):
        try:
            to_base, to_secondary, carried, effective = _frozen(
                cur,
                row.booked_date,
                row.currency,
                household.base_currency,
                household.secondary_currency,
            )
        except HTTPException as exc:
            # `money._frozen` answers a manual save, where "run the backfill first" is the
            # owner's next action. Here the backfill has just run — `ensure_rates` proved every
            # booked date has a rate for the legs this batch reads — so the only way through is
            # the case it cannot check: two legs whose stored rates come from different days.
            # Repeating its advice would send the owner to do again what just succeeded.
            raise PipelineError(
                f"row {row.source_row_number} on {row.booked_date} cannot be priced: {exc.detail}"
            ) from exc
        proposal = classify_row(
            ClassifiableRow(
                account_id=account.id,
                description_clean=row.description_clean or row.description_raw,
                amount=row.amount,
                currency=row.currency,
                reference_raw=row.reference_raw,
            ),
            knowledge,
        )
        rows.append(
            StagedTransaction(
                account_id=account.id,
                booked_date=row.booked_date,
                value_date=row.value_date,
                description_raw=row.description_raw,
                description_clean=row.description_clean,
                reference_raw=row.reference_raw,
                amount=row.amount,
                currency=row.currency,
                original_amount=row.original_amount,
                original_currency=row.original_currency,
                fx_rate_date=effective,
                fx_rate_is_carried=carried,
                fx_rate_to_base=to_base,
                fx_rate_to_secondary=to_secondary,
                amount_base=translate(row.amount, to_base),
                amount_secondary=translate(row.amount, to_secondary),
                dedupe_hash=verdict.dedupe_hash,
                source_page_number=row.source_page_number,
                source_row_number=row.source_row_number,
                raw_row=row.raw_row,
                # The cascade's `txn_type` wins over the sign-derived one when it has an
                # opinion: "INTEREST" is an interest posting whichever way the money went, and
                # the sign only ever knew income from expense.
                proposed_txn_type=proposal.txn_type or row.proposed_txn_type,
                proposed_category_id=proposal.category_id,
                proposed_merchant_id=proposal.merchant_id,
                proposed_payment_method=proposal.payment_method,
                proposed_country=proposal.country,
                classified_by=proposal.classified_by,
                classification_rule_id=proposal.classification_rule_id,
                classification_confidence=proposal.classification_confidence,
                status=verdict.status,
                duplicate_of_transaction_id=verdict.duplicate_of_transaction_id,
                near_duplicate_of_id=verdict.near_duplicate_of_id,
            )
        )
    return rows


AccountResolver = Callable[[str], AccountContext | None]

# The conflict is only ever a file whose every batch was cancelled: layer 1 has refused any other
# by the time this runs (`_staged_batch`). That file keeps its one row, and the re-import
# re-describes it — the account, layout or period may be exactly what the owner cancelled to
# correct, and the commit reads the row back. Each cancelled batch keeps its own copy of those,
# so what was attempted stays on record.
_DOCUMENT = """INSERT INTO document
                 (household_id, account_id, document_type, original_filename, storage_bucket,
                  storage_path, mime_type, byte_size, sha256, page_count, parsed_status,
                  is_password_protected, statement_layout_id, layout_version, period_start,
                  period_end, currency, extracted_metadata)
               VALUES (%s, %s, %s, %s, %s, %s, 'application/pdf', %s, %s, %s,
                       'parsed', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (household_id, sha256) DO UPDATE
               SET account_id = EXCLUDED.account_id, document_type = EXCLUDED.document_type,
                   original_filename = EXCLUDED.original_filename,
                   statement_layout_id = EXCLUDED.statement_layout_id,
                   layout_version = EXCLUDED.layout_version,
                   period_start = EXCLUDED.period_start, period_end = EXCLUDED.period_end,
                   currency = EXCLUDED.currency,
                   extracted_metadata = EXCLUDED.extracted_metadata, uploaded_at = now()
            RETURNING id"""

_BATCH = """INSERT INTO import_batch
              (household_id, document_id, account_id, status, statement_layout_id,
               layout_version, rows_extracted, rows_new, rows_duplicate, rows_needs_review,
               declared_opening_balance, declared_closing_balance, computed_closing_balance,
               reconciliation_status, reconciliation_delta, period_start, period_end,
               period_is_derived, started_at)
            VALUES (%s, %s, %s, 'awaiting_review', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, now())
         RETURNING id"""

_STAGED = """INSERT INTO staged_transaction
               (import_batch_id, household_id, account_id, raw_row, source_page_number,
                source_row_number, booked_date, value_date, description_raw, description_clean,
                reference_raw, amount, currency, original_amount, original_currency,
                fx_rate_date, fx_rate_is_carried, fx_rate_to_base, fx_rate_to_secondary,
                amount_base, amount_secondary, proposed_txn_type, dedupe_hash,
                duplicate_of_transaction_id, near_duplicate_of_id, status,
                proposed_category_id, proposed_merchant_id, proposed_payment_method,
                proposed_country, classified_by, classification_rule_id,
                classification_confidence)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::payment_method, %s,
                     %s::classified_by, %s, %s)"""


def parse(
    conn: psycopg.Connection,
    *,
    pages: Sequence[Page],
    data: bytes,
    original_filename: str,
    household: HouseholdContext,
    profiles: Sequence[ImportableProfile],
    account: AccountContext | None,
    resolve_account: AccountResolver,
    stage: bool,
    encrypted: bool = False,
    documents_root: Path | None = None,
    rate_source: RateSource | None = None,
) -> ParseResult:
    """Run the whole pipeline over one file. Writes nothing unless `stage` is true.

    The caller supplies `pages` because reading a PDF is the route's business — it owns the
    upload, the temporary file and the password prompt — and because it lets every stage below
    be exercised on values. `encrypted` says whether that file needed a password; the password
    itself never arrives here and is never stored.

    **Precondition: `conn` holds nothing worth keeping.** This function rolls the connection
    back — it must, because `fxrange.ensure_rates` refuses to run inside an open transaction
    and would otherwise commit whatever the caller had written. Reads are fine; a caller that
    wraps this in a transaction of its own will lose it silently.

    The order of the refusals is the contract: every one of them happens before a byte is
    written, so `api-contracts.html` §4's "database state: unchanged" holds for each without a
    cleanup path existing at all.
    """
    digest = storage.checksum(data)

    # Layer 1, on both paths and first: it is the cheapest of the three (spec §4.7), and a dry
    # run reports it too — the owner asking "what is in this file" is owed "you already
    # imported it" just as much as the one staging it.
    with conn.cursor() as cur:
        storage.refuse_if_already_imported(cur, household.id, digest)

    # Before identification, not after. A wholly scanned document yields no words, so its page
    # one text is empty, every fingerprint scores 0.00 and the owner is sent to a layout picker
    # that cannot help: choosing a layout re-scores the same empty string and refuses again,
    # with nothing anywhere saying the file has no text layer.
    if not any(page.has_text for page in pages):
        raise PipelineError("this file is scanned rather than text, so no rows can be read from it")

    identification = identify(profiles, pages, original_filename)
    if identification.chosen is None:
        raise LayoutNotIdentified(identification.candidates)
    profile = identification.chosen

    extraction = _extracted(profile, pages)
    resolved = account if account is not None else _resolved(extraction, resolve_account)

    with conn.cursor() as cur:
        analysis = _analysed(
            profile,
            extraction,
            account_id=resolved.id,
            account_currency=resolved.currency,
            committed=committed_rows(cur, household.id, resolved.id),
        )

    if not stage or blocks_staging(analysis):
        # Nothing above this line wrote anything, and the FX step below is the only stage that
        # would. Discard the reads so the connection is idle for whatever the caller does next.
        #
        # A blocked batch returns rather than raises, and that is spec §7.5's decision, not a
        # convenience: the diagnostics panel needs the verdict, the delta and the divergent row,
        # and an HTTP error carries a `detail` string that could hold none of them. The caller
        # reads `staged` — which is false here whatever was asked for — and renders the reason
        # out of the analysis it already has.
        conn.rollback()
        return ParseResult(analysis=analysis, account=resolved, staged=False)

    with conn.cursor() as cur:
        _refuse_second_staging(cur, household.id, resolved.id, analysis.period)
    # `ensure_rates` commits, and it refuses to run inside someone else's transaction for that
    # reason. The reads above are ours and hold nothing worth keeping.
    conn.rollback()
    ensure_rates(
        conn,
        {row.booked_date for row in analysis.rows},
        currencies={row.currency for row in analysis.rows} | {household.secondary_currency},
        source=rate_source,
    )

    # Original first (§5.4): a staged batch referencing a blob that failed to write is worse
    # than an orphaned file, which is inert and content-addressed.
    stored = storage.store(data, household.id, original_filename, root=documents_root)
    return _stage(
        conn,
        analysis,
        stored,
        account=resolved,
        household=household,
        profile=profile,
        encrypted=encrypted,
    )


def blocks_staging(analysis: Analysis) -> bool:
    """Whether this analysis may not be staged, whatever the caller asked for.

    Invariant I8 is the first half: a statement that does not reconcile stages zero rows. The
    second is a row that would not normalize — a batch missing rows reconciles only by
    coincidence, and staging part of a statement produces a ledger wrong in a way no later
    check looks for, because the missing amount is simply absent rather than wrong.
    """
    return analysis.reconciliation.status != STATUS_MATCHED or bool(analysis.failures)


def _resolved(extraction: ExtractionResult, resolve: AccountResolver) -> AccountContext:
    number = extraction.account_number_raw
    if not number:
        raise AccountNotResolved(
            "this statement states no account number, so choose the account to import it into"
        )
    account = resolve(number)
    if account is None:
        # The number itself is never echoed: spec §5.6 forbids any digit run of seven or more
        # from the document reaching a response, and an account number is exactly that.
        raise AccountNotResolved(
            "the account this statement names is not one this household holds; choose the "
            "account to import it into, or add it first"
        )
    return account


def _locked_pair(cur: psycopg.Cursor[Any], household: HouseholdContext) -> HouseholdContext:
    """The reporting pair, read off the household row under `FOR UPDATE` (spec B3.5).

    Every writer of a frozen amount takes this lock before reading the pair, and this branch
    makes staging the second such writer — `routes/transactions.py` is the first. Without it
    the window is the whole parse: `PATCH /household` may change the pair while a statement is
    being extracted and priced, see no frozen rows (this batch's are uncommitted, so invisible
    under MVCC), and succeed — after which these rows commit frozen against the old pair and
    `reporting_pair_is_locked()` reports the new pair locked by exactly the rows that
    contradict it. Silent, permanent, and self-sealing.

    A pair that moved is refused rather than re-frozen: the FX range was already fetched for
    the old secondary currency, so the rates this batch holds are the wrong ones.
    """
    cur.execute(
        "SELECT base_currency, secondary_currency FROM household WHERE id = %s FOR UPDATE",
        (household.id,),
    )
    row = cur.fetchone()
    if row is None:  # pragma: no cover - a session cannot outlive its household
        raise PipelineError("no household row")
    locked = HouseholdContext(
        id=household.id, base_currency=str(row[0]).strip(), secondary_currency=str(row[1]).strip()
    )
    if (locked.base_currency, locked.secondary_currency) != (
        household.base_currency,
        household.secondary_currency,
    ):
        raise PipelineError(
            "the household's reporting pair changed while this statement was being parsed, so "
            "the rates it was priced against are no longer the right ones; parse it again"
        )
    return locked


def _document_type(account_type: str) -> str:
    """`document.document_type` from the account's own stored type, never from the layout.

    The account is resolved before staging and `account.account_type` is a stored fact, so a
    card statement needs no heuristic over the fingerprints — which is what ADR-0012 would
    forbid. Only the card distinction is drawn here: a loan statement is `loan_statement` in
    the enum, but a loan account's statements are M7's, and guessing ahead of the milestone
    that will map them is how the wrong value gets written to a forward-only column.
    """
    return "card_statement" if account_type == "credit_card" else "bank_statement"


def _stage(
    conn: psycopg.Connection,
    analysis: Analysis,
    stored: storage.StoredDocument,
    *,
    account: AccountContext,
    household: HouseholdContext,
    profile: ImportableProfile,
    encrypted: bool,
) -> ParseResult:
    """`document`, `import_batch` and every staged row, in one transaction (§5.4).

    One transaction is what makes staging atomic: a failure anywhere leaves no partial batch,
    so there is nothing for a later sweep to recognise or clean up.
    """
    return _staged_batch(
        conn,
        analysis,
        stored,
        account=account,
        household=household,
        profile=profile,
        encrypted=encrypted,
        counters=Counter(analysis.counts),
    )


def _staged_batch(
    conn: psycopg.Connection,
    analysis: Analysis,
    stored: storage.StoredDocument,
    *,
    account: AccountContext,
    household: HouseholdContext,
    profile: ImportableProfile,
    encrypted: bool,
    counters: Counter[str],
) -> ParseResult:
    with conn.transaction(), conn.cursor() as cur:
        pair = _locked_pair(cur, household)
        # Serialises this staging against a concurrent one for the same account. The period
        # check below is a bare SELECT: under READ COMMITTED it takes no lock and cannot see a
        # sibling transaction's uncommitted `import_batch` row, and `import_batch` carries no
        # unique constraint to fall back on — so without this lock two uploads of the same
        # statement, seconds apart through the FX fetch, would both find nothing and both
        # stage. The account row is the rendezvous because a batch belongs to one account.
        cur.execute(
            "SELECT account_type FROM account WHERE id = %s AND household_id = %s FOR UPDATE",
            (account.id, household.id),
        )
        held = cur.fetchone()
        if held is None:
            raise PipelineError("the account this statement imports into no longer exists")

        _refuse_second_staging(cur, household.id, account.id, analysis.period)
        rows = _staged(cur, analysis, account=account, household=pair)

        # Layer 1 again, in a statement of its own. It answered before the parse, and the parse
        # is long — the FX fetch sits inside it. `_locked_pair` has since taken the household
        # row, which serialises every staging in the household, so under READ COMMITTED this
        # sees any rival that staged in between, and nothing can stage between it and the
        # write below. That is what keeps `_DOCUMENT`'s conflict to a cancelled file: the
        # checksum index cannot, because a cancelled file's row already exists.
        storage.refuse_if_already_imported(cur, household.id, stored.sha256)

        cur.execute(
            _DOCUMENT,
            (
                household.id,
                account.id,
                _document_type(str(held[0])),
                stored.masked_filename,
                _BUCKET,
                f"{household.id}/{stored.sha256}.pdf",
                stored.byte_size,
                stored.sha256,
                analysis.page_count,
                encrypted,
                profile.id,
                profile.version,
                analysis.period.start,
                analysis.period.end,
                account.currency,
                Json(analysis.metadata),
            ),
        )
        document_id = _returned(cur)

        cur.execute(
            _BATCH,
            (
                household.id,
                document_id,
                account.id,
                profile.id,
                profile.version,
                analysis.rows_extracted,
                counters["new"],
                counters["duplicate"] + counters["near_duplicate"],
                counters["needs_review"],
                # A derived opening is left NULL rather than written into the column that means
                # "the statement declared this" (Invariant I7). `import_batch` has no companion
                # flag, and the label the parse response carries lives only as long as that
                # response — the review screen reads this row, which is exactly where a computed
                # number must not be readable as a stated one. The verdict's own message says
                # the opening was derived, and the walk can recompute it.
                None
                if analysis.reconciliation.opening_is_derived
                else analysis.reconciliation.declared_opening,
                analysis.reconciliation.declared_closing,
                analysis.reconciliation.computed_closing,
                analysis.reconciliation.status,
                analysis.reconciliation.delta,
                analysis.period.start,
                analysis.period.end,
                # Stored rather than left to be inferred later: a period derived from rows dated
                # 1 and 2 March is indistinguishable afterwards from one a statement stated as
                # 1-2 March, and the review screen must not read a computed period as a declared
                # one (V17, Invariant I7).
                analysis.period.is_derived,
            ),
        )
        batch_id = _returned(cur)

        cur.executemany(
            _STAGED,
            [
                (
                    batch_id,
                    household.id,
                    row.account_id,
                    Json(row.raw_row),
                    row.source_page_number,
                    row.source_row_number,
                    row.booked_date,
                    row.value_date,
                    row.description_raw,
                    row.description_clean,
                    row.reference_raw,
                    row.amount,
                    row.currency,
                    row.original_amount,
                    row.original_currency,
                    row.fx_rate_date,
                    row.fx_rate_is_carried,
                    row.fx_rate_to_base,
                    row.fx_rate_to_secondary,
                    row.amount_base,
                    row.amount_secondary,
                    row.proposed_txn_type,
                    row.dedupe_hash,
                    row.duplicate_of_transaction_id,
                    row.near_duplicate_of_id,
                    row.status,
                    row.proposed_category_id,
                    row.proposed_merchant_id,
                    row.proposed_payment_method,
                    row.proposed_country,
                    row.classified_by,
                    row.classification_rule_id,
                    row.classification_confidence,
                )
                for row in rows
            ],
        )

    return ParseResult(
        analysis=analysis,
        account=account,
        staged=True,
        batch_id=batch_id,
        document_id=document_id,
    )


def _returned(cur: psycopg.Cursor[Any]) -> UUID:
    row = cur.fetchone()
    if row is None:  # pragma: no cover - RETURNING on a successful INSERT always yields a row
        raise PipelineError("the import could not be staged")
    identifier: UUID = row[0]
    return identifier
