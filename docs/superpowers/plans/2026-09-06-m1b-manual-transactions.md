# M1b Implementation Plan — manual transactions and taxonomy

> **For agentic workers:** this plan follows the CLAUDE.md rule **"Plans are execution maps"**:
> it carries contracts, behaviours and acceptance criteria, not pre-written patches. The
> implementer inspects the repository before deciding the exact implementation and may deviate
> from intended detail when the contracts and acceptance criteria stay satisfied, recording the
> deviation in the PR — never by editing this plan mid-task. Task state lives only in
> `docs/superpowers/progress.md`. Plan and implementation live on **the same branch and PR**;
> the delivery cycle is: plan and implement → Codex review → patch the findings → Codex
> thumbs-up → merge.

**Goal:** Manual transactions on the delivered account inventory — the ledger schema with its
reporting view, the seed taxonomy, manual entry with FX frozen on save, and the transactions
screen — completing roadmap milestone M1.

**Architecture:** Everything financial computes in the Python service and Postgres; the Next.js
app renders decimal strings and never does money arithmetic. Reporting reads only
`v_transaction_reportable` and frozen amounts.

**Tech stack:** FastAPI + psycopg + Decimal; Postgres 18 + Flyway forward-only migrations;
Next.js + generated API types; pytest, vitest, `db/tests/*.sql`.

**Authority:** scope is the [Phase 1 specification §7](../specs/2026-09-06-phase-1-trusted-financial-position.md)
M1b row; acceptance is roadmap M1's "Done when" list; the design documents under `docs/design/`
(data-model, classification, multi-currency, api-contracts) are the specification of record per
subsystem and win over any restatement here.

**Entry criterion:** met — M1a merged to `develop` (PR #30), recorded in the progress file.

## Global constraints

- Money: `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a string on the wire; aggregation in
  Postgres; TypeScript formats and never computes.
- No currency, institution or country name is ever an identifier (ADR-0012); fixtures use the
  synthetic `XIN`/`XAE` codes; taxonomy and merchant names are rows, never code.
- Reports read `v_transaction_reportable` and frozen `amount_base`/`amount_secondary` only;
  never the base table, never multiplied by a rate.
- Every financial route depends on `require_owner()`; every `SECURITY DEFINER` function calls
  `assert_is_owner()` first; every service query carries an explicit `household_id` predicate.
- Migrations are forward-only Flyway — M1b uses **V9** and **V10**; every table change updates
  `docs/superpowers/schema.md` and `docs/design/data-model.html` in the same commit. Applied
  migrations are never edited.
- TDD: each task's required test behaviours are written first and observed failing for the
  right reason before implementation. Commits stay small and task-shaped inside the one PR.
- No LLM calls (ADR-0003). The eight invariants I1–I8 (`testing-and-data-quality.html` §1) gate
  the merge.

## Workspace and branch

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` — the dispatch machine's active repository
checkout (confirm with `git rev-parse --show-toplevel`; all commands repo-relative).
**Branch:** `feature/m1b-manual-transactions`, created from `develop`.
**Base and PR target:** `develop` — one PR carrying this plan and the implementation.

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

The pre-push hook runs all three; a failing push is fixed, never bypassed.

---

### Task 12: Categories, merchants and the seed taxonomy

**Outcome:** The classification reference data exists: the category tree with buckets, and the
merchant and alias tables, with the seed taxonomy loaded by the migration.
**Files:** create `db/migrations/V9__categories_and_merchants.sql` (implements `schema.md`
`0006` and seeds the category taxonomy from classification §3); create
`db/tests/0008_categories.sql` (the `db/tests/` sequence is independent of `schema.md`
numbering — 0006/0007 are taken); update `schema.md` `0006` and `data-model.html` in the same
commit. The classification **rule** packs are not created here — their destination table
arrives with M4, and the packs arrive with it. Merchant tables are created empty; merchant seed
rows arrive with the M4 packs.
**Contracts:** the `0006` DDL in `schema.md` is the table shape, with one reconciliation first:
its `max_two_levels` CHECK contains a subquery, which PostgreSQL rejects in CHECK constraints —
`schema.md` is the bug, so the two-level rule is enforced by a trigger (both directions: a
child of a child, and re-parenting a category that has children) and `schema.md` is corrected
in the same commit as V9. Every category carries a bucket in {need, want, investment,
debt_service, income, ignore} (ADR-0008) as an editable default; the seeded tree is
classification §3's, with plain subcategory names — the bracketed brand names there are
seed-pack examples, not category names. System seed rows have `household_id IS NULL`; RLS
follows the institution pattern (system rows readable, only household rows writable). The seed
is idempotent (unique system slug, `ON CONFLICT DO NOTHING`) so a later seed-update migration
never duplicates rows.
**Invariants/security:** ADR-0012 — no category, merchant or region content becomes an
identifier; the taxonomy seed is versioned data inside the migration.
**Required test behaviours (failing first):** taxonomy loads from empty with every category
carrying a valid bucket and every child pointing at a root; a third category level is rejected
in both directions; RLS hides another household's categories and shows system rows;
re-inserting the seed set is a no-op; no test names a real currency or bank.
**Validation:** the DB gate.
**Acceptance:** DB gate green from empty; `schema.md`/`data-model.html` updated with V9.

### Task 13: The transaction ledger and its reporting view

**Outcome:** The `transaction` table with frozen dual amounts, provenance and classification
stamps, plus `v_transaction_reportable` — the only surface reports may read.
**Files:** create `db/migrations/V10__transaction_ledger.sql` (implements `schema.md` `0007`
including the posting layer and `v_transaction_reportable`); create
`db/tests/0009_transactions.sql`; update `schema.md`/`data-model.html` same commit.
**Contracts:** `0007` DDL is the shape: frozen `amount_base`/`amount_secondary`, `classified_by` +
`classification_rule_id`, `source` with provenance (`source = 'statement_import'` requires
`source_document_id`, I6), `dedupe_hash` unique on `(household_id, account_id, dedupe_hash)`,
`fx_rate_is_carried`/`fx_rate_date` labels (I7). The split-safe posting layer ships with the
view: split children are one level deep, inheriting account, date, provenance and frozen FX
from their parent; the deferred `trg_split_sums_to_parent` constraint trigger holds every
split's children to exactly the parent's amount; and `v_transaction_reportable` selects only
leaf rows, created `WITH (security_invoker = true)` — load-bearing, not style (data-model on
splits and the reporting guard). Columns referencing tables that arrive in later milestones
(`source_document_id`, `receipt_document_id`, `statement_layout_id`, `classification_rule_id`,
the import-batch reference) are created as bare UUID columns — the same pattern `schema.md`
`0018` already applies to `transfer_id` and its siblings; they are constrained by the M4
migration that completes their targets, and each `0018` clause proper lands in the forward
migration that creates its own target.
**Invariants/security:** I1 — `UPDATE` of `amount` or `currency` raises (trigger); I2 — signed
balances sum with no `CASE`; I5 — frozen columns never recomputed; the view excludes transfers
and duplicates by construction (I3, I4 groundwork); RLS household isolation on `transaction`.
**Required test behaviours (failing first):** immutability trigger raises on amount/currency
update; an insert without frozen amounts is refused; the view returns exactly non-transfer,
non-duplicate rows; provenance constraint rejects an import-sourced row without a document id;
the signed-balance sum matches to 4 dp for an asset and a liability fixture; a split parent is
excluded from the reportable view while its children report, so spending is never
double-counted; children that do not sum to the parent are rejected at commit; a child of a
child is rejected.
**Validation:** the DB gate.
**Acceptance:** all I1/I2/I5/I6/I7 behaviours proven in `db/tests/0009_transactions.sql`.

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
reporting pair (Phase 1 spec B3.5); a missing FX rate fails the save — never a fallback to 1.0
or today's rate; `require_owner()` on all routes.
**Required test behaviours (failing first):** the pair-change/first-frozen-write interleaving
test lands here (B3.5 — both orders serialize correctly); a save on a rate-less date is refused
with a labelled error; a carried-forward Sunday rate stamps `fx_rate_is_carried=true` and the
earlier `fx_rate_date`; category edit stamps `user_manual` and never touches amounts; scoping
and money-string behaviours per the existing `test_accounts.py` patterns.
**Validation:** the API gate; the DB gate for the interleaving test.
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
**Acceptance:** roadmap M1 “done when” items verifiable end-to-end against a live stack; M1b
completion recorded in `docs/superpowers/progress.md`.
