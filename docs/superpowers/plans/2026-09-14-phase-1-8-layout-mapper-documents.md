# Phase 1.8 layout mapper and documents implementation plan

> Execute sequentially in the active checkout. Track completion only in `../progress.md`.

**Goal:** Map an unseen statement layout in the app, and browse stored documents with row
provenance.

**Architecture:** The draft preview gains a non-blocking verdict and the import screen hosts a
form mapper over `LayoutCreate`. A read-only documents route lists safe facts and serves verified
originals; ledger rows expose their source.

**Stack:** Existing Python/FastAPI and Next.js/React; no migration or dependency. See the
[Phase 1.8 specification](../specs/2026-09-14-phase-1-8-layout-mapper-documents.md).

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees. Each
  branch starts from current `develop` and opens one PR to `develop` titled `[P1.8] …`. This plan
  and its specification land with Task 1.
- Tests cover major behaviour (authorization, privacy, file integrity, the end-to-end mapping
  chain); obvious screen code needs none.
- Routes use `require_owner` and household predicates; `api/tests/test_health.py` keeps every
  non-public route protected.
- No filename, storage path, hash, password, complete identifier, extracted metadata or parse error
  in a response, URL, browser storage, log or fixture. Synthetic PDFs only.
- TypeScript formats money and never computes it, and never calls `Number(`, `parseFloat(` or
  `parseInt(`; number inputs read `valueAsNumber`.
- Shared commands: docs `python3 scripts/check-docs.py`; Python
  `cd api && uv run ruff check . && uv run mypy .`; types
  `cd web && npm run api:types && npm run api:types:check`; web `cd web && npm run typecheck`.
- One test server: `eval "$(scripts/test-db.sh up)"`, reused with `eval "$(scripts/test-db.sh env)"`.
- After affected tests: one `/oneledger-code-review`, one repair, `scripts/verify.sh` once, push,
  PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | layout mapper | `feature/p1-8a-layout-mapper` | 750 | 700 | Phase 1.7 merged |
| 2 | document library and row provenance | `feature/p1-8b-document-library` | 900 | 700 | 1 |

## Task 1 — layout mapper

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-8a-layout-mapper` / `develop` / `develop`.
**Depends:** Phase 1.7 merged, including its Task 3. **Forecast:** 550–750 production, 500–700
tests.
**Files:** update `api/oneledger/routes/layouts.py`, `api/oneledger/routes/ingest.py`,
`api/tests/test_layouts.py`, `web/app/import/import-screen.tsx` and its test,
`web/lib/api/client.ts` and its test, `web/app/globals.css`,
`docs/adr/0012-statement-layouts-as-data.html`; create
`api/tests/test_layout_mapper_live.py`, `web/app/import/layout-mapper.tsx` and its test; generated
types; API contracts, statement ingestion, register and progress documents.
**Contract:**

- Move `ParseFailure`, `Reconciliation` and `_reconciliation` unchanged into `layouts.py`;
  `ingest.py` imports them. `LayoutProposal` adds the five preview fields with null or empty
  defaults.
- Draft mode, without a database connection and keeping existing screening: read pages once, run
  engine extraction (refusing a file with no text), normalize with an empty currency label,
  reconcile, and compare the fingerprint score over page-one text with the identification floor.
- Draft-mode responses return the echoed draft as plain data (no re-validation after redaction);
  `_parse_draft` and `POST /layouts` refuse `[redacted]` in any string; the pattern compiler's
  message omits the pattern; draft-mode verdict strings and failure reasons use the preview
  redactor.
- `client.ts` `problem()` joins the `msg` strings of a list `detail` with `"; "`.
- `layout-mapper.tsx` exports `LayoutMapper({ file, password, seed, savedNames, request, onSaved,
  onCancel })`; `import-screen.tsx` owns the triggers, seeding and re-check.

**Tests:** the specification's Task 1 evidence. The live test seeds manual FX rates as
`tests/test_ingest_route_live.py` does and cleans only its own synthetic rows.
**Commands:** `(cd api && uv run pytest -q tests/test_layouts.py tests/test_layout_mapper_live.py
tests/test_ingest_route_live.py tests/test_health.py)`; shared Python and types commands;
`(cd web && npx vitest run app/import lib/api && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** specification acceptance criterion 1.

## Task 2 — document library and row provenance

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-8b-document-library` / `develop` / `develop`.
**Depends:** Task 1. **Forecast:** 700–900 production, 550–700 tests.
**Files:** create `api/oneledger/routes/documents.py`, `api/tests/test_documents_live.py`,
`web/app/documents/page.tsx`, `web/app/documents/documents-screen.tsx`,
`web/app/documents/description-repair.tsx` and its test; update `api/oneledger/main.py`,
`api/oneledger/routes/description_reparse.py`, `api/oneledger/routes/transactions.py`, their live
tests, `web/app/workspace.tsx` and its test, `web/app/transactions/transactions-screen.tsx` and its
test, `web/app/globals.css`; generated types; API contracts, security and privacy, register and
progress documents.
**Contract:**

- `GET /documents` and `GET /documents/{documentId}/original` exactly as specified; the list is one
  query with lateral lookups for the newest batch, the mask and a newer layout, each carrying the
  household predicate.
- `description_reparse.py` gains `verified_original(storage_path: str, expected_sha256: str) ->
  bytes`, raising the fixed 409 sentences and mapping a storage error to "the stored original
  could not be read"; both the re-read and the original route use it.
- `TransactionRow` adds the three nullable source fields to the shared column and field lists
  together.
- The workspace adds a `documents` section and forwards `sourceDocumentId` from the transactions
  URL to its first ledger request.

**Tests:** the specification's Task 2 evidence.
**Commands:** `(cd api && uv run pytest -q tests/test_documents_live.py
tests/test_transactions_live.py tests/test_transfers_live.py tests/test_description_reparse_live.py
tests/test_health.py)`; shared Python and types commands; `(cd web && npx vitest run app/documents
app/transactions app/__tests__/workspace.test.tsx && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** specification acceptance criteria 2 and 3; criterion 4 becomes an owner-operated
gate in progress.

## Stop conditions

- Stop rather than persist a refused upload, expose a stored private column, or gate Save on a
  fact the API does not report.
- Stop before push when review blocks, verification is red or not run, or the diff passes the hard
  stop.
