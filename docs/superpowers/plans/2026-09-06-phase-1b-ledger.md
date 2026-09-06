# Phase 1b Implementation Plan — the ledger milestones

> **For agentic workers:** execute task-by-task with superpowers:subagent-driven-development or
> superpowers:executing-plans. This plan follows the CLAUDE.md rule **"Plans are execution
> maps"**: it carries contracts, behaviours and acceptance criteria, not pre-written patches.
> The implementer inspects the repository before deciding the exact implementation and may
> deviate from intended detail when the spec, contracts and acceptance criteria stay satisfied,
> recording the deviation in the PR and `docs/superpowers/progress.md` — never by editing this
> plan. Task state lives only in the progress file; this plan carries no checkboxes.

**Goal:** Complete Phase 1 after M1a — manual transactions, statement import, classification,
transfers, cards, investments, documents and the verified net-worth views — so the phase exit
criteria in the roadmap can be verified and recorded.

**Architecture:** Everything financial computes in the Python service and Postgres; the Next.js
app renders decimal strings and never does money arithmetic. Statement ingestion is a staged,
reconciliation-gated pipeline ending in one atomic commit; classification is a deterministic
cascade with one implementation; reporting reads only `v_transaction_reportable` and frozen
amounts.

**Tech stack:** FastAPI + psycopg + Decimal; Postgres 18 + Flyway forward-only migrations;
Next.js + generated API types; pytest, vitest, `db/tests/*.sql`.

**Spec:** [Phase 1b specification](../specs/2026-09-06-phase-1b-ledger.md) — authoritative for
scope and acceptance. Milestone entry criteria and fixed interfaces come from the
[Phase 1 specification §7](../specs/2026-09-06-phase-1-trusted-financial-position.md); design
documents under `docs/design/` are the specification of record per subsystem and win over any
restatement here.

## Global constraints

- Money: `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a string on the wire; aggregation in
  Postgres; TypeScript formats and never computes. Percentages human-readable.
- No currency, institution or country name is ever an identifier (ADR-0012); fixtures use the
  synthetic `XIN`/`XAE` codes; layouts, banks and rules are rows, never code.
- Reports read `v_transaction_reportable` and frozen `amount_base`/`amount_secondary` only;
  never the base table, never multiplied by a rate. Both are static checks.
- Every financial route depends on `require_owner()`; every `SECURITY DEFINER` function calls
  `assert_is_owner()` first; every service query carries an explicit `household_id` predicate.
- Migrations are forward-only Flyway `V<n>__<name>.sql`, numbered from **V9**; every table
  change updates `docs/superpowers/schema.md` and `docs/design/data-model.html` in the same
  commit. Applied migrations are never edited. The V-numbers below are the intended sequence
  for this milestone order; a task dispatched after the sequence shifts takes the next
  unapplied version and records the change in its PR.
- TDD: each task's required test behaviours are written first and observed failing for the
  right reason before implementation.
- Task branches `<type>/task-<n>-<description>` PR into
  `feature/phase-1-trusted-financial-position`; ≤500 lines of implementation+test per PR — a
  task expecting more declares its split at dispatch, one branch per delivery.
- Nothing imports without a click; confidence pre-sorts and pre-ticks, never decides.
- No LLM calls (ADR-0003). Never-store list absolute; real statements never enter the repo —
  redacted, layout-preserving golden fixtures only.
- The eight invariants I1–I8 (`testing-and-data-quality.html` §1) gate every merge; a failure
  halts feature work.

## Workspace and branches

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` — the dispatch machine's active repository
checkout, recorded before dispatch as AGENTS.md requires. Confirm with
`git rev-parse --show-toplevel`; run every command repo-relative from that root. If
implementation ever moves machines, the orchestrator re-records this line before dispatch —
an agent never substitutes a path mid-task. No new worktrees; one implementation agent at a
time.

**Base and PR target for every task:** `feature/phase-1-trusted-financial-position`. Phase
completion PRs to `develop` only on a separate owner request (`docs/git-workflow.md`).

**Validation commands** (referenced per task as “the API gate”, “the web gate”, “the DB gate”):

```bash
cd "$(git rev-parse --show-toplevel)/api" && uv run ruff check . && uv run mypy . && uv run pytest -q
```

```bash
cd "$(git rev-parse --show-toplevel)/web" && npm run lint && npm run typecheck && npm test && npm run api:types
```

```bash
docker compose run --rm flyway migrate && scripts/hooks/db-tests.sh
```

(`scripts/hooks/db-tests.sh` derives the DSN from `.env` via `scripts/dsn.sh`; `DATABASE_URL`
is normally unset and is never assumed.)

The pre-push hook runs all three; a failing push is fixed, never bypassed.

---

## M1b — manual transactions and taxonomy

Entry: M1a merged (Phase 1 plan Task 11 complete, recorded in the progress file).

### Task 12: Categories, merchants and the seed taxonomy

**Outcome:** The classification reference data exists: the category tree with buckets, and the
merchant and alias tables, with the seed taxonomy loaded by the migration.
**Files:** create `db/migrations/V9__categories_and_merchants.sql` (implements `schema.md`
`0006` and seeds the category taxonomy); extend `db/tests/0006_categories.sql`; update
`schema.md` `0006` (mark applied as V9) and `data-model.html` in the same commit. The
classification **rule** packs are not created here — their destination table arrives with
Task 22's V13, and the packs move there with it.
**Contracts:** the `0006` DDL in `schema.md` is the table shape, with one reconciliation first:
its `max_two_levels` CHECK contains a subquery, which PostgreSQL rejects in CHECK constraints —
`schema.md` is the bug, so the two-level rule is enforced by a trigger and `schema.md` is
corrected in the same commit as V9. Every category carries a bucket in {need, want, investment,
debt_service, income, ignore} (ADR-0008) as an editable default. Merchant names are rows.
**Invariants/security:** ADR-0012 — no category, merchant or region content becomes an
identifier; the taxonomy seed is versioned so later seed updates never disturb user edits.
**Required test behaviours (failing first):** taxonomy loads from empty with every category
carrying a valid bucket; a third category level is rejected; re-running the seed is idempotent;
no test names a real currency or bank.
**Validation:** the DB gate.
**Branch:** `feature/task-12-categories-and-seed-taxonomy`.
**Acceptance:** DB gate green from empty; `schema.md`/`data-model.html` updated in the same
commit as V9.

### Task 13: The transaction ledger and its reporting view

**Outcome:** The `transaction` table with frozen dual amounts, provenance and classification
stamps, plus `v_transaction_reportable` — the only surface reports may read.
**Files:** create `db/migrations/V10__transaction_ledger.sql` (implements `schema.md` `0007`
including `v_transaction_reportable`); create `db/tests/0007_transactions.sql`; update
`schema.md`/`data-model.html` same commit.
**Contracts:** `0007` DDL is the shape: frozen `amount_base`/`amount_secondary`, `classified_by`
+ `classification_rule_id`, `source` with provenance (`source = 'statement_import'` requires
`source_document_id`, I6), `dedupe_hash` unique on `(household_id, account_id, dedupe_hash)`,
`fx_rate_is_carried`/`fx_rate_date` labels (I7). The split-safe posting layer ships with the
view: split children are one level deep, inheriting account, date, provenance and frozen FX
from their parent; the deferred `trg_split_sums_to_parent` constraint trigger holds every
split's children to exactly the parent's amount; and `v_transaction_reportable` selects only
leaf rows through the posting view, created `WITH (security_invoker = true)` — load-bearing,
not style (data-model on splits and the reporting guard). Columns referencing tables that
arrive in later milestones (`source_document_id`, `receipt_document_id`,
`statement_layout_id`, `classification_rule_id`, the import-batch reference) are created as
bare UUID columns — the same pattern `schema.md` `0018`
already applies to `transfer_id` and its siblings; these four are constrained by Task 22's V13
once every target exists, while each `0018` clause proper lands in the forward migration that
creates its own target.
**Invariants/security:** I1 — `UPDATE` of `amount` or `currency` raises (trigger); I2 — signed
balances sum with no `CASE`; I5 — frozen columns never recomputed; the view excludes transfers
and duplicates by construction (I3, I4 groundwork).
**Required test behaviours (failing first):** immutability trigger raises on amount/currency
update; an insert without frozen amounts is refused; the view returns exactly non-transfer,
non-duplicate rows; provenance constraint rejects an import-sourced row without a document id;
the signed-balance sum matches to 4 dp for an asset and a liability fixture; a split parent is
excluded from the reportable view while its children report, so spending is never
double-counted; children that do not sum to the parent are rejected at commit; a child of a
child is rejected.
**Validation:** the DB gate.
**Branch:** `feature/task-13-transaction-ledger`.
**Acceptance:** all I1/I2/I5/I6/I7 behaviours proven in `db/tests/0007_transactions.sql`.

### Task 14: Manual transaction API with FX frozen on save

**Outcome:** Manual entry, listing and category editing over the ledger, freezing both reporting
amounts at save time under the B3.5 household lock.
**Files:** create `api/oneledger/routes/transactions.py`, `api/tests/test_transactions.py`;
modify `api/oneledger/main.py`; add the routes to `api-contracts.html` §2 and regenerate
`web/types/api/` in the same delivery.
**Interfaces (produced):** `POST /transactions` (create; freezes `amount_base`/
`amount_secondary` from the booked-date FX row, labelling a carried-forward rate per I7),
`GET /transactions` (filter/sort/search with paging; a household-scoped ledger query over the
base table — transfer legs and duplicates stay visible and labelled per IA §4's 🔗 peer link —
while spending and income aggregates read `v_transaction_reportable`; the reporting static
check governs reports, and this ledger endpoint is its one deliberate non-report reader),
`PATCH /transactions/{id}` (category override → `classified_by='user_manual'`). All money as
decimal strings; all statements household-scoped.
**Invariants/security:** every frozen write takes the household row lock **before** reading the
reporting pair (spec B3.5); a missing FX rate fails the save — never a fallback to 1.0 or
today's rate; `require_owner()` on all routes.
**Required test behaviours (failing first):** the pair-change/first-frozen-write interleaving
test lands here (B3.5 — both orders serialize correctly); a save on a rate-less date is refused
with a labelled error; a carried-forward Sunday rate stamps `fx_rate_is_carried=true` and the
earlier `fx_rate_date`; category edit stamps `user_manual` and never touches amounts; scoping
and money-string behaviours per the Task 9 (Phase 1) patterns.
**Validation:** the API gate; the DB gate for the interleaving test.
**Branch:** `feature/task-14-manual-transactions-api`.
**Acceptance:** roadmap M1 — a manual secondary-currency transaction shows the correct frozen
base value; balance equation holds to 4 dp after entries on both account classes.

### Task 15: Transactions screen and category management

**Outcome:** The ledger UI: list with filter/sort/search, manual entry, inline category edit;
category management under Settings.
**Files:** create `web/app/transactions/` (+ tests), `web/app/settings/categories/` (+ tests);
consume generated types only.
**Mockup:** `docs/product/mockups/OneLedger.dc.html` Transactions artboard;
`ui-information-architecture.html` §4 wins on disagreement (§6 for Settings content).
**Invariants/security:** `formatMoney` from decimal string + `currency` row; no arithmetic, no
`Number()`/`parseFloat` on money; keyboard operation and AA contrast per the mockups note.
**Required test behaviours (failing first):** list renders money exactly as sent; filter/search
round-trips as query parameters; inline category edit PATCHes and re-renders the returned row;
100-row fixture renders without pagination lag (roadmap M1); empty state teaches the first
action.
**Validation:** the web gate.
**Branch:** `feature/task-15-transactions-screen` (split UI/settings delivery allowed at
dispatch, each under the cap).
**Acceptance:** roadmap M1 “done when” items verifiable end-to-end against a live stack.

## M2 — importer, one layout end-to-end

Entry: M1b merged; FX backfill reaching 01 Jan 2024 verified (record the check in the progress
file). Scope carries the Phase-0 deferrals: CI and `scripts/backup-db.sh`.

### Task 16: Documents, import batches and layout profiles (schema + upload)

**Outcome:** The `0008` substrate — `document`, `import_batch`, `statement_layout` — with upload
and checksum dedupe (layer 1).
**Files:** create `db/migrations/V11__documents_and_layouts.sql` (implements `schema.md`
`0008`), `db/tests/0008_documents.sql`, `api/oneledger/routes/documents.py` (+ tests); update
`schema.md`/`data-model.html`, `api-contracts.html` §2, regenerate types.
**Contracts:** statement writes follow the api-contracts §4 error contract — every parse
failure (missing password, unknown layout, missing FX rate, reconciliation mismatch) leaves the
database unchanged, so a statement's `document` row and blob are written by Task 18's staging
transaction, never at upload; the incoming file's checksum is checked against previously staged
or committed documents (`document.sha256` unique per household), rejecting an identical file
before parsing and naming the earlier import. Non-statement vault uploads (M10) persist
immediately; `statement_layout` carries fingerprints, header pattern,
column x-bands, date format, sign convention and quirks as **data** (ADR-0012);
`statement_layout_id` + `layout_version` will be stamped on every imported transaction.
**Invariants/security:** originals stored unmodified (I1); owner-only access; filenames may
contain account numbers — never logged.
**Required test behaviours (failing first):** a duplicate of a staged or committed statement is
rejected pre-parse with the earlier batch named; a failed parse leaves no document, batch or
staged row (api-contracts §4); layout rows round-trip; a staged statement's blob is
byte-identical on retrieval.
**Validation:** the API and DB gates.
**Branch:** `feature/task-16-documents-and-layouts`.

### Task 17: Extraction engine and the mapping flow

**Outcome:** The generic `ExtractionEngine` (`matches`/`extract`/`propose`) driven entirely by
layout rows, plus the mapping endpoints.
**Files:** create `api/oneledger/ingest/engine.py` (+ tests); `POST /parse`, `POST /layouts`,
`POST /layouts/propose` in a new `routes/ingest.py`; contracts row + types regenerated.
**Contracts:** identification order is content fingerprint → filename hint (tiebreaker only) →
ask-and-offer-to-map; account resolution by `account_number_hash` — this task designs and
populates the hash (salted, per `security-and-privacy.html`) from the masked-entry flow; the
engine never guesses an account (statement-ingestion §3–§4). `propose` finds the header row and
column bands for an unseen PDF.
**Required test behaviours (failing first):** a synthetic fixture layout extracts rows with
declared date format and sign convention honoured; an unknown PDF yields a proposal, not a
guess; a changed bank layout fails to match rather than mis-parsing; scanned PDF (no text
items) is detected and reported (full refusal UX belongs to M3).
**Validation:** the API gate.
**Branch:** `feature/task-17-extraction-engine`.

### Task 18: Normalization, dedupe, FX enrichment, reconciliation and staging

**Outcome:** Pipeline stages 4–9: normalized rows staged with proposals, duplicate layers 2–3,
FX enrichment with fetch-on-missing, and the I8 reconciliation gate.
**Files:** create `db/migrations/V12__staged_transactions.sql` — the staging shape is fixed by
statement-ingestion §9 and is not yet in `schema.md`, so this task adds its reference section
there in the same commit — plus `api/oneledger/ingest/pipeline.py` (+ tests) and `db/tests`
additions; `data-model.html` updated same commit.
**Contracts:** `staged_transaction.raw_row JSONB` holds exactly what the parser saw with page
and row number; `status ∈ new|duplicate|near_duplicate|needs_review|accepted|rejected`;
dedupe layer 2 disambiguates by **occurrence index within the day, never row ordinal**
(statement-ingestion §6 — ordinal double-imports overlapping statements); layer 3 (±3 days,
similarity ≥0.85) warns and never blocks. The §9 shape's two forward references are created
bare in V12: `classification_rule_id` as a plain UUID (constrained by Task 22's V13) and the
proposed transfer kind as text validated in the service until V14 creates the enum. The staging
write is atomic — `document`, `import_batch` and staged rows commit together only once
reconciliation yields a stageable verdict (api-contracts §4). Staged rows carry frozen
`amount_base`/`amount_secondary` and `reporting_pair_is_locked()` discovers every table with an
`amount_base` column, so the staging transaction is M2's B3.5 frozen writer: it takes the
household row lock before reading the reporting pair. Reconciliation verdicts: `reconciled` proceed,
`mismatched` and `running_balance_break` raise `ImportBlocked` naming the row, `unavailable`
warns and flags the batch.
**Invariants/security:** an import never proceeds against a missing FX rate and never falls
back (I5/I7); `description_raw` is never modified; the bank's own FX conversion is
authoritative.
**Required test behaviours (failing first):** re-import stages zero rows; overlapping statement
stages only non-overlapping rows; a corrupted amount blocks the batch naming the divergent row
(I8); the running-balance layout reconciles via its running column (statement-ingestion §13);
carried-forward FX labelled per I7; a reporting-pair change racing a staging batch serializes
correctly in both interleavings (B3.5).
**Validation:** the API and DB gates.
**Branch:** `feature/task-18-ingest-pipeline` (split staging-schema/pipeline deliveries allowed
at dispatch).

### Task 19: Review screen, mapping UI and the atomic commit

**Outcome:** The human gate: grouped review, inline decisions, the layout-mapping UI, and
`POST /import/{batch}/commit` as one all-or-nothing transaction.
**Files:** create `web/app/import/` (+ tests) and the commit route in `routes/ingest.py`
(+ tests); contracts row + types regenerated.
**Mockups:** `Review.dc.html` (review), `LayoutMapping.dc.html` (mapping),
`OneLedger.dc.html` Statement-import artboard; IA §3/§6 win on disagreement.
**Contracts:** groups most-attention-first (Blocked, Transfers, Unclassified, Low confidence,
Ready, Duplicates); keyboard-first; nothing imports without a click — pre-tick threshold 0.80
pre-sorts only; commit inserts only `status='accepted'` rows, ends with
`assert_account_reconciles(...)` and rolls the whole batch back on failure; every committed row
traces to document + page + row (I6). The diagnostics panel (statement-ingestion §12) ships
here in its M2 form.
**Required test behaviours (failing first):** an induced mid-commit failure leaves zero rows;
an unticked row never imports; a blocked batch renders the divergent row with the
running-balance diagnostic; commit is refused while any blocking verdict stands.
**Validation:** the web and API gates.
**Branch:** `feature/task-19-review-and-commit` (UI/commit split allowed at dispatch).

### Task 20: M2 acceptance — golden fixture, CI and backup

**Outcome:** The first layout proven end-to-end on a redacted golden fixture, plus the deferred
Phase-0 operational pieces.
**Files:** create `api/tests/fixtures/statements/<layout-a>/` (redacted, layout-preserving,
with a README naming the structure it reproduces — never a real statement),
`.github/workflows/ci.yml`, `scripts/backup-db.sh`; an end-to-end test driving upload → map →
stage → review → commit.
**Required test behaviours (failing first):** the fixture imports and reconciles end-to-end
with zero manual corrections; the fixture test fails on an engine or profile regression;
backup runs and restores into a scratch database with row counts matching.
**Validation:** all three gates plus the new CI run green.
**Branch:** `feature/task-20-m2-acceptance`.
**Acceptance:** roadmap M2 “done when” list verifiable — the 38 real statements import locally
(owner-run evidence recorded in the progress file; real data never in the repo).

## M3 — the remaining layouts

Entry: M2 merged and its layout reconciling on real statements (owner evidence in the progress
file). M3 may run in parallel with M4.

### Task 21: Three more layouts, passwords, and the diagnostics panel

**Outcome:** All four layouts import and reconcile; hardened intake for protected, multi-line
and scanned inputs. **Sharpened at dispatch against the tree** — quirks are additive and
considered (spec §7).
**Files:** three more redacted fixture sets; quirk vocabulary extensions as layout data;
password prompt flow; diagnostics panel completion; scanned-PDF refusal UX.
**Contracts:** a statement password is prompted per file, held in a local variable for the
parse duration, never written to disk, DB, config or log, never remembered or pre-filled;
scanned PDFs are reported, not mis-parsed; no layout is named in code — the synthetic
unseen-format test (map + import with zero code changes) is the ADR-0012 acceptance test.
**Required test behaviours (failing first):** per-layout fixture regression tests; encrypted
fixture imports with the password absent from every artefact the test can inspect; the
synthetic unseen layout maps and imports through the UI flow with no code change.
**Validation:** all three gates.
**Branch:** `feature/task-21-remaining-layouts` (per-layout split expected at dispatch).
**Acceptance:** roadmap M3 — all 71 gathered statements import and every account reconciles
across the full period (owner-run evidence in the progress file).

## M4 — classification

Entry: M2 merged (parallel with M3 allowed).

### Task 22: The cascade and classification rules

**Outcome:** `classify.py` — the one deterministic cascade with one caller — and the
`classification_rule` store, stamping every transaction.
**Files:** create `db/migrations/V13__classification_rules.sql` (implements `schema.md`
`0009`), `api/oneledger/classify.py` (+ tests), `db/tests/0009_rules.sql`; wire the import
pipeline and manual paths to the single caller; create `db/seeds/rules/global.yaml` and
`db/seeds/rules/regions/<cc>.yaml`, loaded by the migration (classification §3 — region packs
selected by `SELECT DISTINCT country FROM account`, not a setup question); docs updated same
commit. V13 also constrains the deferred references whose targets now all exist — the
ledger's `source_document_id` and `receipt_document_id`, the ledger and staging
`import_batch`/`statement_layout` references (V11), and both `classification_rule_id` columns
(this migration) — with a test behaviour proving an orphan reference is rejected afterwards.
Pack loading is not a one-time snapshot: opening an account in a new country loads that
country's pack idempotently (classification §3 — "there is no list to keep in sync with
reality"), so the loader is one service path callable from the migration and from account
creation. The `schema.md` `0018` clauses are separate: each lands in
the forward migration that creates its own target (transfers, cards, investments), and the
loan and recurring clauses stay deferred with their features.
**Contracts:** cascade order (first match wins, stops): user override → user rule (priority
asc, default 100) → merchant alias → prior match (same account + text) → seed rule (priority
500) → unclassified → review. Confidence fixed: 1.000 / 0.980 / 0.950 / 0.900 (fuzzy 0.700) /
0.800 / 0.000; pre-tick at 0.80. All non-null rule conditions must match; regex validated
against catastrophic backtracking. `classified_by` + `classification_rule_id` on every row.
Statement-ingestion.html owns the pipeline stage ordering where classification.html disagrees
(the spec names this).
**Required test behaviours (failing first):** each cascade step wins over the next in a
directed fixture; a user rule beats a seed rule without either referencing the other;
evaluation stops at first match; the engine abstains rather than guesses (a low-confidence rule
returns nothing); seed rules reach ≥70% on the redacted first-import fixture; creating an
account in a country whose pack is not yet installed loads it exactly once.
**Validation:** the API and DB gates.
**Branch:** `feature/task-22-classification-cascade`.

### Task 23: Rule management and learning from corrections

**Outcome:** Rule CRUD with live preview, learn-from-correction, merchant aliases and rule
health — Settings › Rules and the review-screen inline flow.
**Files:** `POST /rules/preview` plus CRUD routes (+ tests); `web/app/settings/rules/`
(+ tests); review-screen inline rule creation; contracts row + types regenerated.
**Mockup:** Settings follows the artboards' design language and IA §6's content list (Settings
is explicitly not drawn in mockup set 01).
**Contracts:** recategorizing prompts rule creation explicitly; affected count shown before
commit; affected rows previewable; retroactive application opt-in — past classifications never
change quietly; aliases created implicitly on merchant assignment during review; rule health
surfaces never-fired, often-overridden and shadowed rules.
**Required test behaviours (failing first):** preview count equals rows actually changed on
apply; declining retroactivity leaves history untouched; an alias assignment classifies the
next matching row; a shadowed rule is reported.
**Validation:** the API and web gates.
**Branch:** `feature/task-23-rule-management`.
**Acceptance:** roadmap M4 “done when” — every classified transaction names its rule.

## M5 — transfers and household outflows

Entry: M3 and M4 merged.

### Task 24: Transfer matching, confirmation and reportable enforcement

**Outcome:** The matching engine with kind-specific windows and FX-aware tolerance, review-queue
confirmation (nothing auto-linked), and `v_transaction_reportable` enforced end-to-end.
**Sharpened at dispatch** — engine internals argue from `transfers-and-reconciliation.html` §3
and `multi-currency-and-fx.html` §5 against the tree as it stands.
**Precondition inside the task:** the `0010` shape cannot hold a pending one-sided transfer or
a confirmed external outflow — it requires a destination account, a destination amount and, via
its confirmed-has-legs check, both transaction legs — while transfers §3 and ADR-0007 require
exactly those states. `schema.md` is the bug (design documents specify; the schema file is the
table reference): reconcile `0010` with the one-sided and external-outflow requirements
**before** writing V14, updating `schema.md` and `data-model.html` together as a reviewed
`docs/` delivery — the same pattern as M9b's `0015`/F03 precondition.
**Files:** create `db/migrations/V14__transfers.sql` (implements `schema.md` `0010`),
`api/oneledger/transfers.py`, `POST /transfers`, `DELETE /transfers/{id}` (+ tests); review
integration; static check that no reporting query reads the base table; docs same commit.
**Contracts:** a confirmed transfer's legs leave spending and income (I3); a card payment is a
transfer, not spending (I4); pending one-sided transfers re-match on later imports; FX spread
and total remittance cost are recorded facts (ADR-0007); external household outflows report
separately from consumption.
**Required test behaviours (failing first):** confirming a remittance drops spending by exactly
the source amount and unlinking restores it (I3); the −310 purchase / 3,900 payment fixture
yields spending of exactly 310 (I4); two unrelated same-day similar amounts stay unlinked; the
static check fails a query reading the base table.
**Validation:** all three gates.
**Branch:** `feature/task-24-transfers` (engine/UI split expected at dispatch).
**Acceptance:** roadmap M5 — monthly spending after exclusions matches the hand-tallied month.

## M9a — early verified overview

Entry: M5 merged. Does not require M7 or M10 (ADR-0006).

### Task 25: Valuations and the verified overview

**Outcome:** Dated manual valuations and the morning screen: balances for included accounts,
spending, external outflows, retained income — every headline carrying its as-of date and
verified / partially-covered / estimated status.
**Files:** create `db/migrations/V15__valuations.sql` (implements `schema.md` `0014`),
`GET /dashboard` (+ tests), `web/app/overview/` (+ tests); docs and types same commit.
**Mockup:** `OneLedger.dc.html` Overview artboard; IA §2 wins. Multi-currency §6 governs the
dated, coverage-labelled position — no untagged converted totals.
**Contracts:** price, FX and statement dates stay separate; manually valued assets carry
explicit dates and never double-count an account balance; aggregation in Postgres.
**Required test behaviours (failing first):** each headline exposes status + as-of date; a
stale account degrades the status rather than silently including; a manual valuation with a
date renders labelled; spending equals the reportable sum exactly.
**Validation:** all three gates.
**Branch:** `feature/task-25-verified-overview`.
**Acceptance:** the M9a checkpoint — one complete recent month, opening balances acknowledged.

## M6 — credit cards

Entry: M5 merged.

### Task 26: Card statements, dues and utilisation

**Outcome:** Statement records, dues, utilisation and paid-in-full detection, with purchase
spending separated from bill settlement. **Sharpened at dispatch** from `schema.md` `0011` and
ADR-0009.
**Files:** `db/migrations/V16__card_statements.sql`; API + card fragments in Overview, Review
and Transactions (cards have no dedicated screen — IA).
**Required test behaviours (failing first):** utilisation matches the statement fixture; cycle
spending equals the statement's declared total debits; the payment appears in neither spending
nor income; a revolving card renders distinct from paid-in-full.
**Validation:** all three gates. **Branch:** `feature/task-26-cards`.

## M7 — investments

Entry: M5 merged.

### Task 27: Holdings, XIRR and allocation

**Outcome:** Lot-level holdings with lifecycle continuity (dividends, reinvestment, partial
sales, charges, splits, platform transfers), XIRR with bisection fallback, allocation.
**Sharpened at dispatch** from `schema.md` `0012` and ADR-0005.
**Files:** `db/migrations/V17__investments.sql`; API + `web/app/investments/` per the
`OneLedger.dc.html` Investments artboard (IA §6 wins).
**Contracts:** an unrecognised corporate action blocks that holding's return claim and raises a
review item — never a silent guess; Σ transaction units = holding units per holding.
**Required test behaviours (failing first):** XIRR matches the spreadsheet fixture within
0.01%; allocation sums to 100% and reconciles to portfolio value; a USD holding contributes
correctly to both reporting views; a fund purchase renders as one linked event.
**Validation:** all three gates. **Branch:** `feature/task-27-investments` (schema/engine/UI
split expected at dispatch).

## M10 — documents and owner export

Entry: M2 merged (schedulable alongside M6/M7).

### Task 28: The vault and the owner export

**Outcome:** Document library with bidirectional traceability and the owner export (open
tabular records + selected originals with provenance and a coverage manifest, F12).
**Files:** vault screen under `web/app/documents/` (IA §6; the Statement-import artboard's
Documents nav); export route. The deletion-blocked-while-referenced constraint belongs in
Task 16's V11; if it lands here instead it takes its own next-version forward migration — an
applied V11 is never edited (ADR-0015).
**Required test behaviours (failing first):** every imported transaction links to document,
page and row; deleting a referenced document is refused; re-upload detected before parsing;
disk usage reported for documents and database separately; export manifest names coverage; the
export archive is built in an owner-only temporary location and cleaned up deterministically —
no artifact remains on disk after a completed export or a failed one (Phase 1 spec §7, M10's
protected-temporary-file contract).
**Validation:** all three gates. **Branch:** `feature/task-28-documents-vault`.

## M9b — full history and attribution

Entry: M7 merged and valuation inputs available.

### Task 29: Consolidated history, the waterfall and the FX bridge

**Outcome:** Net worth on any past date in both reporting currencies, the explanatory
attribution waterfall with a visible residual, and the frozen-flow-to-closing-rate FX bridge.
**Precondition inside the task:** reconcile the unapplied `0015` attribution shape with F03
**before** writing the migration, updating `schema.md` and `data-model.html` together (spec
§7); that reconciliation is a `docs/` delivery reviewed on its own.
**Files:** `db/migrations/V18__attribution.sql` (post-reconciliation), `GET /networth`,
`GET /networth/attribution` (+ tests), `web/app/networth/` per `NetWorth.dc.html` (IA §5 wins);
multi-currency §7 and data-model §14 govern.
**Required test behaviours (failing first):** net worth on a past date equals the sum of signed
balances that date in both currencies; components + residual equal the actual change exactly;
synthetic scenarios zero the residual; internal funding and principal repayments cancel their
cash legs; dated FX-bridge fixtures prove the frozen-flow-to-closing-rate identity; dashboard
cold render under 2 s.
**Validation:** all three gates. **Branch:** `feature/task-29-history-and-attribution`
(docs-reconciliation/schema/UI split expected at dispatch).

## Phase exit

### Task 30: Exit verification and record

**Outcome:** The Phase 1 exit gates verified and recorded; the phase ready for its
owner-requested integration PR to `develop`.
**Gates (roadmap):** all scheduled milestones done (M8 excepted); 36 months of real statements
across all four layouts imported and reconciled to the 01 Jan 2024 anchor (ADR-0011) —
owner-run, evidence in the progress file, no real data in the repo; monthly import + review
under 20 minutes; every included balance reconciles to its evidence with discrepancies
disclosed separately (no tolerance absorption); monthly spending agrees with a hand-checked
month exactly; restore-from-backup verified against a scratch database; zero infrastructure
spend while local.
**Files:** `docs/superpowers/progress.md` only.
**Branch:** `docs/task-30-phase-1-exit`.
**Acceptance:** every gate recorded with dated evidence links; integration toward `develop`
awaits the separate owner request per `docs/git-workflow.md`.
