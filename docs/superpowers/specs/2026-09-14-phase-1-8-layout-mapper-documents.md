# Phase 1.8 — layout mapper and documents

Owner-approved outcome, planned at personal scale on 14 September 2026. The
[dated plan](../plans/2026-09-14-phase-1-8-layout-mapper-documents.md) declares both branches.
Workspace: `/Users/sandjaieravi/dev/OneLedger`; every branch starts from and targets `develop`.

## Outcome

An unseen statement layout becomes supported without a code change. The owner maps it on the
import screen, sees the draft reconcile against that file, saves it, and the same file continues
through account confirmation, staging and review. Stored documents can be filtered by type,
account, period and import status. Each document shows its parse and reconciliation status and
opens its original. Every imported ledger row links back to its document, page and row.

## Authority

- The [roadmap](../../product/roadmap.html) owns acceptance; the
  [master plan](../../product/master-implementation-plan.html#phase-1-delivery) places this phase
  after Phase 1.7.
- [ADR-0012](../../adr/0012-statement-layouts-as-data.html): a layout is a versioned row created
  through a mapping UI; the application proposes and the owner confirms; reconciliation makes it
  safe. Numeric band inputs over a live parsed preview satisfy that decision; dragging band edges
  over a rendered page is deferred through the project register.
- [Statement ingestion](../../design/statement-ingestion.html): the mapper's draft preview is the
  one place a verdict renders without blocking.
- [Security and privacy](../../operations/security-and-privacy.html): originals are served only
  through an owner route; filenames, passwords and complete identifiers never leave it.

No migration, new ADR or dependency is added; ADR-0012 records the numeric-band consequence.

## Reused capabilities

| Capability | Where |
| --- | --- |
| Header discovery and proposed bands | `api/oneledger/ingest/engine.py` |
| Edited-draft preview without persistence | `api/oneledger/routes/layouts.py` (`POST /layouts/propose` with `draft`) |
| Layout validation and versioned save | `routes/layouts.py` (`POST /layouts`) |
| Normalization and reconciliation | `ingest/normalize.py`, `ingest/reconcile.py` |
| Fingerprint score and identification floor | `ingest/engine.py`, `ingest/pipeline.py` |
| Verdict and row-failure wire models | `routes/ingest.py` (`Reconciliation`, `ParseFailure`) |
| Parse, account confirmation, review, commit | `routes/ingest.py` |
| Stored-original path, link and SHA-256 checks | `routes/description_reparse.py` |
| Guarded description re-read with a newer layout | `POST /import/{batch}/reparse-descriptions` |
| Ledger source-document filter | `routes/transactions.py` |

## API boundary

### Draft preview — `POST /layouts/propose` (Task 1)

The request is unchanged. `LayoutProposal` gains fields that are null or empty for a proposal and
filled for a `draft` preview: `reconciliation` (`Reconciliation | null`), `failures`
(`ParseFailure[]`), `rowsExtracted` (`int | null`), `identifies` (`bool | null`, the draft's
fingerprint score reaches the identification floor) and `accountNumberCaptures` (`int | null`).

- `Reconciliation`, `ParseFailure` and their converter move unchanged into `routes/layouts.py`;
  `routes/ingest.py` imports them, so OpenAPI names are unchanged.
- The preview reads the PDF once and opens no database connection. Rows remain the first twenty
  screened raw rows.
- The echoed draft is returned as plain data and is never validated again after redaction (a
  redacted capture group such as an account-number pattern must not fail the response). Incoming
  drafts, in preview and in `POST /layouts`, refuse any value containing `[redacted]` (422, "a
  redacted value cannot be saved; rewrite it"). A regular-expression error names the error, not
  the pattern.
- In draft mode every string field of `reconciliation` and each `failures[].reason` passes through
  the same identifier redactor as the preview rows; status, row numbers and candidate rows are
  unchanged.

### Document library — `GET /documents` (Task 2)

Owner-only and read-only. Query: `documentType`, `accountId`, `periodFrom`/`periodTo` (overlap;
documents without a period are excluded when either is set; inverted range 422), `importStatus`,
`limit` 1–200 (default 50), `offset` ≥ 0. Newest upload first, then id. Response `documents` plus
`total`. Each row: `id`, `documentType`, `pageCount`, `isPasswordProtected`, `parsedStatus`,
`uploadedOn`, `accountId`, `accountName`, `accountNumberMasked`, `period {start, end, isDerived}`,
`importKind` (`investment` for investment statements, `payslip` for payslips, else `statement`),
`importId`, `importStatus`, `reconciliationStatus` (statements only), `layoutId`, `layoutVersion`
and `newerLayoutId` (the newest same-name household layout with a higher version).

- Statements take `importId`/`importStatus` from the newest `import_batch` for the document.
- Investment statements take them from the document's `investment_import`.
- Payslip documents have `importId` null and `importStatus` `completed` when any payslip
  references the document, else null.
- `importStatus` accepts the union of the statement and investment import status values; anything
  else is 422.
- Payslip and investment documents carry null `period` and `accountId`, so period and account
  filters exclude them.
- `accountNumberMasked` is the newest active identifier's mask for the account; `period.isDerived`
  is `bool | null`. Every join
repeats the owner's household predicate. The payload never contains the original filename, title,
storage location, hash, size, notes, tags, parse error, extracted metadata or a complete identifier.

### Original download — `GET /documents/{documentId}/original` (Task 2)

Owner-only; an unknown or foreign id is 404. The service requires the documents store, resolves a
relative or absolute storage path under it, refuses a symbolic link or a path outside it, and
requires `storage_bucket = 'documents'` and the bytes' SHA-256 to equal the recorded hash (the
configured documents root is absolute); refusals are 409 with fixed sentences.
Success returns the bytes with `Content-Type: application/pdf`,
`Content-Disposition: attachment; filename="document.pdf"`, `Cache-Control: no-store`,
`Pragma: no-cache` and `X-Content-Type-Options: nosniff`. One helper,
`verified_original(storage_path, expected_sha256) -> bytes`, serves this route and the description
re-read.

### Ledger provenance — `GET /transactions` (Task 2)

`TransactionRow` gains nullable `sourceDocumentId`, `sourcePageNumber` and `sourceRowNumber`,
appended to the shared column and field lists together so transfer legs still decode.

## Web workflows

### Map or remap a layout (Task 1)

- The import screen offers **Map this layout** when a check is refused with a 422 whose detail
  starts with "no layouts are saved yet" or "no saved layout identifies this statement", and
  **Remap this layout** for a checked file that cannot stage. In Remap the name is read-only, so
  saving always creates that layout's next version.
- Map seeds the form from a proposal (a blank draft with the reason when proposal is refused);
  Remap seeds from the saved profile, without `id` and `version`.
- The form edits every `LayoutCreate` field: name, account type, fingerprints, skip lines, header
  pattern, bands (field, `xMin`, `xMax`, label; add and remove), date format, sign convention and
  inversion, row order, row-start and continuation rules, quirks, opening and closing patterns and
  metadata patterns. Number inputs read `valueAsNumber`.
- **Preview** posts the form as `draft` and shows mapped rows, the verdict and row failures,
  whether the draft identifies the file and the capture count. The echoed draft never replaces the
  form.
- **Save** is enabled only when the last preview matches the form, the verdict is matched with no
  failures, the draft identifies the file, there is exactly one account-number capture and an
  account type is chosen. A name equal to a saved layout warns that saving creates its next
  version.
- After saving, the file is checked again and follows the normal account proposal, staging and
  review. The file and password stay in memory; nothing reaches browser storage, a URL or a log.

### Documents and provenance (Task 2)

- **Documents** joins the workspace navigation at `/documents` with filters (type, account, period
  from and to, import status), rows (type, account and mask, period labelled "derived from the
  rows" when derived, pages, upload date, import status, verdict) and **Load more**.
- Row actions: **Original** (plain link to the original route), **Transactions**
  (`/transactions?sourceDocumentId=<id>` for statements), **Review** (`/import/{importId}` for a
  statement awaiting review; `/investments/import/{importId}` for an investment import that is
  staged, awaiting review or partially committed).
- **Re-read descriptions** appears for a completed statement import with `newerLayoutId`: preview
  the counts, then Apply; a password field appears only for a protected document.
- The transactions screen applies `sourceDocumentId` from its URL; an imported row shows
  "Page P · row R" linked to its original.

## Failure behaviour

| Condition | Result |
| --- | --- |
| Proposal refused | Blank draft with the reason |
| Invalid draft or redacted value | 422; messages shown; form kept |
| Verdict not matched, failures, not identifying, captures not one | Preview 200; Save disabled with the reason |
| Form edited after preview | Save disabled until previewed again |
| Version race on save | 409; retry |
| Invalid filter or inverted period | 422 |
| Original missing, linked, outside the store, altered, or store unset | 409 fixed sentence |
| Foreign or unknown document | 404; omitted from the list |

## Required test evidence

Major behaviour only:

- **Task 1:** a draft preview of a reconciling synthetic statement with an `Account No: (\d+)`
  metadata capture returns 200, matched, no failures, `identifies` true and one capture; a draft
  whose amount or balance band covers the account number shows no unredacted identifier in the
  verdict or failures; a shifted band returns 200 with a non-matched verdict or
  failures; `[redacted]` refused; live chain with no saved layout — propose, preview, save, parse,
  confirm account, stage, commit — and a second statement in that layout identified without
  mapping; web Save gating and stale preview.
- **Task 2:** filters and period overlap including a payslip and an investment document; newest
  batch wins; private-column sentinels never appear; foreign household empty list and 404 original;
  original bytes and headers for relative and absolute paths; symbolic link, escape, tampering and
  unset store 409; transactions and transfer legs carry provenance; web links and the transactions
  pre-filter.

## Acceptance

1. A synthetic statement with no saved layout is mapped in the app, reconciles in preview, is
   saved and imports end to end with no code change.
2. Documents filter by type, account, period and import status and show parse and reconciliation
   status.
3. Every imported ledger row links to its document, page and row; the original opens only through
   the owner route.
4. Owner-operated: remap the encrypted layout whose narration keeps a repeated header word and
   re-read descriptions for its completed imports, recording structural counts only.

## Non-goals

Document content search and OCR; drag-drawn bands or a rendered page; CSV or XLSX layouts and
automatic layout learning; document deletion, tagging or notes; a layout management screen;
download auditing; links from investment events to their source document; a migration or ADR.

## Documents to reconcile

`docs/adr/0012-statement-layouts-as-data.html` (numeric-band consequence, with Task 1),
`docs/design/api-contracts.html` (layout preview, documents, original, transaction provenance),
`docs/design/statement-ingestion.html` (mapping an unsupported format),
`docs/operations/security-and-privacy.html` (document library and original download),
`docs/superpowers/project-register.md` and `docs/superpowers/progress.md`.
