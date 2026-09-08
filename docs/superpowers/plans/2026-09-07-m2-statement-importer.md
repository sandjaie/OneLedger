# M2 Implementation Plan — the statement importer, one layout end to end

> **For agentic workers:** this plan follows the CLAUDE.md rule **"Plans are execution maps"**.
> It carries outcomes, contracts, invariants, required test behaviours and acceptance criteria —
> not pre-written patches. The implementer inspects the repository before deciding the exact
> implementation, and may deviate from an intended detail when the contracts and acceptance
> criteria stay satisfied, recording the deviation in the PR and in
> `docs/superpowers/progress.md` — never by editing this plan mid-task. Task state lives only in
> `docs/superpowers/progress.md`.
>
> **M2 ships as task-sized PRs**, not one — §2's table is the current decomposition. Each task below
> declares its own `Workspace`,
> `Branch` and `Base and PR target`. This plan document lands with **Task 1's** PR. The delivery
> cycle per PR is: TDD implement → local gates green → push → Codex review on GitHub → patch the
> findings on the same branch → merge on Codex's thumbs-up → delete the branch → next task.
> Sequential: one implementation agent at a time, in the active checkout, no worktrees.

**Goal:** The generic, layout-driven statement importer proven end to end on one layout — upload,
identification, coordinate extraction, mapping UI, normalization, dedupe, FX enrichment,
reconciliation that blocks, staged review and one atomic commit — plus the operational pieces
deferred out of Phase 0 (CI and a restorable backup).

**Architecture:** Every stage runs in the Python service (ADR-0013); the web app renders and
decides nothing. A statement layout is a **row in `statement_layout`**, created through a mapping
UI — no bank name enters application code (ADR-0012). Extraction reads word bounding boxes from
`pdfplumber` and assigns them to column x-bands declared by the profile; it never regexes a
flattened line. Nothing reaches `transaction` until reconciliation passes and the owner clicks
commit, and that commit is one database transaction that verifies itself and rolls back whole.

**Tech stack:** FastAPI + psycopg + `Decimal`; `pdfplumber` (already declared, see below);
PostgreSQL 18 + forward-only Flyway migrations; Next.js 16 + generated API types; pytest +
hypothesis, vitest, `db/tests/*.sql`.

**Authority.** Scope is the **roadmap M2 section** (`docs/product/roadmap.html`). The subsystem
specifications of record are `docs/design/statement-ingestion.html` (the pipeline),
`docs/design/api-contracts.html` §2/§3/§4 (routes, atomicity, the staging contract),
`docs/design/data-model.html` §12 and `docs/superpowers/schema.md` `0008` (tables),
`docs/design/multi-currency-and-fx.html` §3 (rates), `docs/operations/security-and-privacy.html`
§1 (the never-store list) and `docs/operations/testing-and-data-quality.html` §1–§3 (invariants,
static checks, parser tests). ADRs decide: **ADR-0012** (layouts are data), ADR-0002/0011 (frozen
amounts, the 2024-01-01 anchor), ADR-0009 (card statement cycles), ADR-0013/0016 (the
`require_owner()` boundary), ADR-0015 (forward-only Flyway), ADR-0003 (no LLM calls). Where this
plan and a design document disagree, §7 below names the disagreement and which document is wrong;
fix it there, in the PR that touches it.

**Entry criterion — satisfied.** M2's gate is the FX backfill reaching the 01 Jan 2024 anchor
(ADR-0011). Verified by the owner on **2026-09-07** against the live compose database:

```sql
SELECT min(rate_date), max(rate_date), count(*),
       count(DISTINCT (base_currency, quote_currency)) FROM fx_rate;
```

→ earliest `2024-01-01`, latest `2026-09-06`, **1,960 rows across 2 pairs** = 980 days per pair,
complete daily coverage with no gaps. M1a and M1b are merged to `develop`; V1–V11 are applied;
the next free migration is **V12** and the next free `db/tests` ordinal is **0010**.

---

## 1. Global constraints

Every task's requirements implicitly include this section.

- **Money.** `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a **string** on the wire
  (`"-310.0000"`). Never `float`, never a JSON number, never `parseFloat`/`Number()` on a money
  field. TypeScript formats money via `formatMoney(value, currencyRow)` and does no arithmetic.
  Aggregation happens in Postgres. Rates are `NUMERIC(20,10)`; percentages are human-readable
  (`15.2500` is 15.25%).
- **Frozen amounts.** Rates are quantized to 10 dp half-up **first**, then `amount_base` and
  `amount_secondary` are derived from the quantized rates and quantized to 4 dp half-up — the
  order `transaction`'s `frozen_amounts_derive` CHECK requires. Reuse the existing helper (Task
  4b extracts it); do not write a second implementation.
- **No currency, institution or country name is ever an identifier.** Not a column, type, enum
  member, function name, module name, locale constant or required seed row (ADR-0012). Fixtures
  use ISO user-assigned codes (`XIN`/`XAE` currencies, `XA`/`XB` countries) exactly as
  `db/tests/0009_transactions.sql` already does. No file, class, test or profile name in the
  repository may identify a real bank. The layout mapped end to end is referred to throughout as
  **"layout A"**; the owner names the saved row whatever they like.
- **Reports never read the base table and never multiply by a rate.** Cash-flow, budget, spending
  and savings-rate queries read `v_transaction_reportable` and the frozen columns. M2 adds no
  reporting query; the review screen reads `staged_transaction`, and the ledger endpoint remains
  the one deliberate non-report reader of `transaction`.
- **Migrations** are forward-only Flyway, `db/migrations/V<n>__<name>.sql`, applied with
  `docker compose run --rm flyway migrate`. **Never edit an applied migration.** Every table
  change updates `docs/superpowers/schema.md` and `docs/design/data-model.html` **in the same
  commit**. M2 owns **V12, V13, V14** — see §6.
- **Authorization.** `require_owner()` on every new route; the complete public set stays
  `/health`, `/auth/login`, `/auth/callback`. Every new `SECURITY DEFINER` function calls
  `assert_is_owner()` first and is reached from Python only after `scope_to_owner(conn, user_id)`
  (the pattern in `api/oneledger/routes/household.py`). RLS with `household_isolation` on every
  new household-scoped table. Every service query carries an explicit `household_id` predicate.
  No string-interpolated SQL.
- **The never-store list is absolute** (`security-and-privacy.html` §1). Full card and account
  numbers, CVV, PIN, OTP, passwords and full tax/national IDs are never stored, never logged,
  never put in an error message. **Statement filenames contain card numbers** — see §5.6.
  Statement PDF passwords are held in a local variable for the duration of one parse and never
  written anywhere (M3 owns the password prompt; M2 must not create a place to put one).
- **Real financial data never enters the repository**, on any branch, in any commit. `.gitignore`
  excludes `pdf-statements/`, `statements/` and `*.pdf`; `scripts/check-no-real-data.sh` fails
  the commit if **any** `.pdf` is tracked. Therefore **no PDF fixture may be committed** —
  every fixture is a synthetic PDF generated at test time (Task 3 builds the generator).
- **Invariant 7.** Nothing about money is invented silently. A carried-forward rate, an
  unavailable reconciliation and a near-duplicate warning are stored as labelled facts and
  surfaced as such.
- **No LLM calls** (ADR-0003). Classification in M2 is "none": rows stage as `unclassified` at
  confidence `0.000`, and the owner categorizes by hand in review. The cascade is M4's.
- **Dependencies.** `pdfplumber>=0.11.10` is **already declared** in `api/pyproject.toml` and
  present in `api/uv.lock` (unused until now). M2 therefore adds **no new dependency**. If an
  implementer believes one is required, stop and ask the owner rather than adding it; naming the
  dependency and its justification in the PR is not sufficient authorization.
- **TDD.** Each task's required test behaviours are written first, run, and observed failing
  **for the right reason** before implementation. A test that has never failed has never been
  shown to test anything. Commits inside a PR stay small and task-shaped.

---

## 2. Delivery model, workspace and branches

**Workspace for every task:** `/Users/sandjaieravi/dev/OneLedger` — the **dispatch machine's**
active repository checkout, resolved with `git rev-parse --show-toplevel` at planning time and
recorded per AGENTS.md. The literal is a record, not a portable path: every implementer
re-resolves the active root with `git rev-parse --show-toplevel` on the machine it runs on and
works there; a reviewer or agent in a different environment (its own clone, a cloud sandbox)
reads its own resolved root wherever a task says `Workspace`. Never substitute a path from
another machine; all commands are repo-relative. **Do not create Git worktrees** (owner
preference, overriding skill defaults). Do not remove existing worktrees.

**Base and PR target for every task:** `develop`.

Every branch carries its task number, so the branch in a checkout says which task is running.

| # | Branch | Carries | Migration |
|---|---|---|---|
| 1 | `chore/m2-ci-and-backup` | This plan; CI workflow; `scripts/backup-db.sh`; stale-cap doc fix | — |
| 2 | `feature/m2-documents-and-layouts` | `document`, `import_batch`, `statement_layout`; layouts API | **V12** |
| 3 | `feature/m2-extraction-engine` | Word/band extraction, quirks, `propose`; account hashing | — |
| 4a | `feature/m2-staged-rows-and-normalization` | `staged_transaction`; `StagedTransaction`; normalization | **V13** |
| 4b | `feature/m2-t4b-dedupe` | `ingest/dedupe.py` — fingerprint, occurrence index, near-duplicate | — |
| 4c | `feature/m2-t4c-reconcile` | `ingest/reconcile.py` — the four verdicts | — |
| 4d | `fix/m2-balance-direction` | A balance signed by its own `Dr`/`Cr` suffix, in one frame | — |
| 5a | `refactor/m2-t5a-money-helpers` | `money.py`, extracted from `routes/transactions.py` | — |
| 5b | `feature/m2-t5b-fx-fetch-on-missing` | The backfill body as a callable; fetch-on-missing | — |
| 5c | `feature/m2-t5c-document-storage` | `ingest/storage.py` — blob, sha256, filename rules | — |
| 5d | `feature/m2-t5d-parse-pipeline` | `ingest/pipeline.py` — the only module holding a connection | — |
| 5e | `feature/m2-t5e-parse-route` | `routes/ingest.py` — `POST /parse` | — |
| 5f | `feature/m2-t5f-layout-draft-preview` | Draft mode gains the normalized preview and verdict | — |
| 6a | `feature/m2-t6a-import-commit-migration` | The commit and verification functions | **V14** |
| 6b | `feature/m2-t6b-review-payload` | `GET /import/{batch_id}` | — |
| 6c | `feature/m2-t6c-row-decisions` | Row decisions, including the confirmed-distinct override | — |
| 6d | `feature/m2-t6d-atomic-commit-route` | `POST /import/{batch_id}/commit` | — |
| 7a | `feature/m2-t7a-import-screen-shell` | `/import`: the page, the nav entry, multi-file drop | — |
| 7b | `feature/m2-t7b-import-file-status` | Per-file status and Stage batch | — |
| 8a | `feature/m2-t8a-layout-mapper-preview` | The mapper: header row, bands, live preview | — |
| 8b | `feature/m2-t8b-layout-mapper-editing` | The mapper: drag, re-post, save | — |
| 9a | `feature/m2-t9a-review-screen` | The review screen | — |
| 9b | `feature/m2-t9b-diagnostics-panel` | The diagnostics panel | — |
| 9c | `feature/m2-t9c-import-acceptance` | The end-to-end gate and M2 acceptance | — |

Order: 2 → 3 → 4a → {4b, 4c} → 5a → 5b → 5c → 5d → {5e, 5f} → 6a → {6b, 6c, 6d} → 7a → 7b →
{8a → 8b} → 9a → 9b → 9c. Braces mark slices independent of each other. 7a and 8a both need 5e or
5f and may land in either order.

**Nineteen branches replace the six this plan originally named for tasks 4–9.** Tasks 3 and 4a
were each three to four times the diff budget — Task 3 reached +3,380 lines and took eight review
rounds; Task 4a, already half of a split task, reached +2,094. A branch is re-read in full on
every review round, so review cost grows faster than the diff. Each slice below is forecast
against this tree's own measured sizes, and no slice exceeds 400 added production lines or 800
added production and test lines.

---

## 3. Validation gates

Referenced per task as "the API gate", "the web gate" and "the DB gate".

```bash
# The API gate
cd "$(git rev-parse --show-toplevel)/api" \
  && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy --strict . && uv run pytest -q
```

```bash
# The web gate
cd "$(git rev-parse --show-toplevel)/web" \
  && npm run lint && npm run typecheck && npm run test && npm run api:types
```

```bash
# The DB gate
docker compose run --rm flyway migrate && scripts/hooks/db-tests.sh
```

Expected output: `ruff` reports `All checks passed!`; `mypy` reports `Success: no issues found`;
`pytest` reports `N passed` with **N strictly greater than the count on `develop`** for any task
that adds tests; `vitest` likewise; `npm run api:types` leaves `web/types/api/` **unchanged**
(a diff means the task forgot to regenerate — commit the regenerated file); `flyway migrate`
reports `Successfully applied 1 migration` for a migration task and `Schema "public" is up to
date` otherwise; `db-tests.sh` reports `OK: N invariant test(s) passed` with N equal to the file
count in `db/tests/`.

**Any task that changes a Pydantic request or response model must run `npm run api:types` and
commit the regenerated `web/types/api/`.** The pre-push hook's drift check fails otherwise.

The pre-push hook runs all three gates. A failing push is fixed, never bypassed with
`--no-verify`. Install once: `pre-commit install --install-hooks -t pre-commit -t pre-push`.

### 3.1 CI-run versus owner-run

**CI runs** (Task 1 builds it; it mirrors the `verify` skill, not a new invention): docs check,
`pre-commit run --all-files`, the API gate, migrations-from-empty against a scratch stack plus
`db/tests/*.sql`, and the web gate including `api:types:check` and the bundle secret scan.
Everything CI runs is deterministic, synthetic and offline except the ECB fetch, which is
stubbed in tests — **no test may reach the network**.

**Owner decision (2026-09-07): the workflow is kept but disabled** — `workflow_dispatch` only,
no automatic runs. Every gate it mirrors already runs locally in the pre-commit and pre-push
hooks, which are the enforced gate for every M2 PR; the workflow was proven green (three runs
on PR #32) before being switched to manual, and can be dispatched on demand or re-armed by
restoring its triggers.

**Owner-run** (recorded honestly in `docs/superpowers/progress.md` with the date and the counts;
never automated, never in CI, never in the repository):

- All 38 real card statements import end to end with no manual correction of dates or amounts,
  and reconciliation passes for every one.
- Re-importing an identical real file imports zero rows and names the earlier import.
- An overlapping real statement imports only the new rows.
- A backup has run and been restored into a scratch database, with row counts and totals compared.
- The live Google identity gates still open from Phase 0 are unaffected by M2 and stay open.

A red or partial owner-run result is recorded as red. Do not mark M2 complete on CI alone.

---

## 4. Architecture decisions — see the spec

Moved to [the M2 spec](../specs/2026-09-07-m2-statement-importer.md), which keeps the same section
numbers: §4.1 module layout, §4.2 route surface, §4.3 the parse contract, §4.4 the extraction
engine, §4.5 `propose()`, §4.6 reconciliation, §4.7 dedupe, §4.8 FX enrichment, §4.9 the commit.

## 5. Table and storage contracts — see the spec

Moved to [the M2 spec](../specs/2026-09-07-m2-statement-importer.md) §5: V12 and V13 DDL, statuses,
the staging transaction, what a blocked batch leaves behind, and the filename and never-store
rules.

## 6. Migration ownership

| Migration | Task | Contents |
|---|---|---|
| `db/migrations/V12__documents_and_import.sql` | 2 | `document_type`, `parse_status`, `sign_convention` types; `document`, `import_batch`, `statement_layout`; indexes; RLS; the four deferred FKs on `transaction` (§5.1) |
| `db/migrations/V13__staged_transactions.sql` | 4 | `staged_transaction` + indexes + RLS |
| `db/migrations/V14__import_commit.sql` | 6 | `assert_account_reconciles()`, `commit_import_batch()` |

`db/tests/` ordinals: **0010** (Task 2), **0011** (Task 4a), **0012** (Task 6a). The `db/tests`
sequence is independent of `schema.md` section numbers — 0008 and 0009 are already taken by
categories and transactions.

`schema.md` gains `## V12 (applied)` and `## V13 (applied)` sections replacing its unapplied
`0008` reference section; after M2 its unapplied sequence begins at `0009`
(classification rules). `data-model.html` §17's migration list is updated to match.

**No migration is ever edited after it is applied.** If V12 is wrong after Task 2 merges, V15
fixes it forward.

---

## 7. Contradictions between the documents and the tree — see the spec

Moved to [the M2 spec](../specs/2026-09-07-m2-statement-importer.md) §7, with one resolved since:
the stage order. `statement-ingestion.html` §1 and `architecture.html` §5 now both place
reconciliation **before** FX enrichment, which is what the importer does — the deviation is no
longer a deviation and no longer needs recording in a PR.

## 8. Tasks

**A bare "Task N" elsewhere in this plan, in the spec and in earlier PRs means the whole group** —
Task 4 is 4a–4c, Task 5 is 5a–5f, Task 6 is 6a–6d, Task 7 is 7a–7b, Task 8 is 8a–8b and Task 9 is
9a–9c. References written before the split were not rewritten to name a slice they could not have
known about; where one slice is specifically meant, it is named.

Each task: implement TDD in the stated order, run its gates, push, wait for Codex, patch findings
on the same branch, merge on the thumbs-up, delete the branch, update
`docs/superpowers/progress.md` at the boundary.

---

### Task 1 — CI, backup, and the plan

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `chore/m2-ci-and-backup`
**Base and PR target:** `develop`
**Migration:** none.

**Outcome.** Every subsequent M2 PR is gated by CI on GitHub, and the database is restorable
before real data lands in it. This PR carries this plan document.

**Files.**
- Create `docs/superpowers/plans/2026-09-07-m2-statement-importer.md` (this file — already in the
  working tree; commit it here).
- Create `.github/workflows/ci.yml`.
- Create `scripts/backup-db.sh`.
- Modify `.env.example` (`BACKUP_PASSPHRASE=`).
- Modify `docs/superpowers/plans/implementation-plan.md` (the stale 500-line line — §7.1).
- Modify `docs/superpowers/progress.md` (M2 opened; entry criterion recorded as satisfied with
  the 2026-09-07 figures).

**Contracts.**
- CI **mirrors** `.claude/skills/verify/SKILL.md`; it does not invent a different gate. Jobs, in
  order, failing fast: (a) `python3 scripts/check-docs.py` and
  `scripts/check-no-real-data.sh`; (b) `pre-commit run --all-files`; (c) the API gate; (d)
  migrations from empty — a Postgres service container, `flyway migrate`, `db/seeds/*.sql`, then
  every `db/tests/*.sql`; (e) the web gate plus `npm run api:types:check`, `npm run build` and the
  `.next/static/` secret grep for
  `SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://`.
- The workflow is `workflow_dispatch`-only (the §3.1 owner decision — local hooks are the
  enforced gate; it was proven green on automatic triggers first). It must **not**
  publish anything (`docs.yml` owns Pages) and must not need a secret: every value it needs is a
  workflow-local literal for a throwaway database.
- `scripts/backup-db.sh` is `set -euo pipefail`, takes the DSN from `scripts/dsn.sh` exactly as
  `db-tests.sh` does (one source of truth for the password), and writes
  `backups/oneledger-<UTC timestamp>.dump.enc`: `pg_dump -Fc` piped straight through
  `openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE` — **an unencrypted dump
  never touches disk**, because `security-and-privacy.html` §6/§9 require backups encrypted at
  rest wherever they are stored. The passphrase comes from `.env` via `scripts/load-env.sh`
  (`.env.example` gains an empty `BACKUP_PASSPHRASE=` with a comment); an unset or empty
  passphrase refuses the backup with the variable named, exactly as `dsn.sh` treats the
  password. The pipeline writes to a **uniquely created** temporary file (`mktemp`), removes it
  on any failure, and claims the final path atomically only after both `pg_dump` and `openssl`
  succeed (a hard-link claim that fails if a concurrent same-second run published first) — a
  mid-stream failure must never leave a plausible-looking partial backup for a retention or copy
  job to preserve, and two concurrent invocations must never share a temp file. The
  script refuses to overwrite an existing file, prints the path and byte size, exits non-zero on
  any failure, and records the decrypt-and-restore line in a comment
  (`openssl enc -d … -pass env:BACKUP_PASSPHRASE < file | pg_restore …`). `backups/` and
  `*.dump` are already git-ignored and the directory ignore covers the `.enc` name — verify,
  do not re-add. The script backs up the **database only**; the documents volume is a separate
  concern and the script says so in one line rather than pretending to cover it.

**Invariants / security.** No credential, DSN or real path is written into the workflow file or
the script. The workflow's database password is a literal throwaway, matching the verify skill's
reasoning. The backup file lands outside the index.

**Required test behaviours (TDD order).**
1. Run `scripts/backup-db.sh` against the live compose database and confirm a `.dump.enc` appears
   with non-zero size; run it twice and confirm the second run refuses rather than overwriting;
   confirm the file decrypts and lists with the configured passphrase
   (`openssl enc -d … | pg_restore --list >/dev/null`) and refuses a wrong one; confirm an unset
   `BACKUP_PASSPHRASE` refuses the backup.
2. `git status --porcelain` after a backup shows nothing — the dump is ignored.
3. Confirm the workflow is green on GitHub with a non-zero test count in the API, DB and web
   sections — satisfied by the three automatic runs on PR #32 before the owner switched it to
   `workflow_dispatch`. A CI run that collects zero tests is a failure, not a pass.

**Validation.** `python3 scripts/check-docs.py` → no findings. `pre-commit run --all-files` → all
hooks pass. The CI run on the PR → green, all five jobs.

**Acceptance.** The workflow proven green on this PR (before the switch to manual-only); a
backup produced and its path recorded in the PR;
`implementation-plan.md`'s stale cap line fixed; CLAUDE.md's index row flagged to the owner;
`progress.md` records M2 opened and the entry criterion satisfied.

---

### Task 2 — Documents, import batches and layout profiles

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-documents-and-layouts`
**Base and PR target:** `develop`
**Migration:** **V12** — `db/migrations/V12__documents_and_import.sql`
**Depends on:** Task 1.

**Outcome.** The `0008` substrate exists with RLS, and a layout profile can be saved and listed
through the API. No parsing yet.

**Files.**
- Create `db/migrations/V12__documents_and_import.sql`.
- Create `db/tests/0010_documents_and_layouts.sql`.
- Create `api/oneledger/routes/layouts.py`, `api/tests/test_layouts.py`.
- Modify `api/oneledger/main.py` (register the router).
- Modify `docs/superpowers/schema.md` (a `## V12 (applied)` section replacing the `0008`
  documents/batches/layouts subsections, and the V10 deferred-FK note corrected per §7.8) and
  `docs/design/data-model.html` (§12 wording and §17's migration list) — **same commit as the
  migration**.
- Modify `docs/design/api-contracts.html` §2 (two route rows).
- Regenerate `web/types/api/`.

**Interfaces produced** (consumed by Tasks 3, 5, 6, 7, 8):

```python
# api/oneledger/routes/layouts.py — the wire shape, camelCase
class LayoutColumn(BaseModel):
    field: str          # booked_date | value_date | description | reference
                        # | amount | debit | credit | running_balance
    xMin: float
    xMax: float
    label: str | None

class LayoutProfile(BaseModel):          # GET /layouts item, POST /layouts response
    id: UUID
    name: str
    version: int
    institutionId: UUID | None
    fingerprints: list[str]
    headerRowPattern: str | None
    columns: list[LayoutColumn]
    dateFormat: str
    signConvention: str                  # debit_credit_columns | signed | cr_suffix | balance_delta
    invertsSign: bool
    chronologicalOrder: str              # ascending | descending
    rowStartRule: str                    # has_date_and_amount | has_date
    continuationRule: str | None         # no_date_and_no_amount
    skipLinePatterns: list[str]
    openingBalancePattern: str | None
    closingBalancePattern: str | None
    hasRunningBalance: bool
    quirks: list[str]
    metadataPatterns: dict[str, str] | None
    timesUsed: int
    lastUsedAt: datetime | None

class LayoutCreate(BaseModel):           # POST /layouts body — LayoutProfile minus
    ...                                  # id/version/timesUsed/lastUsedAt
```

**Contracts.**
- The `schema.md` `0008` DDL is the table shape, plus §5.1's additions.
- `POST /layouts` with a `name` that already exists inserts `max(version) + 1` for that name and
  returns the new row; existing rows are never mutated (ADR-0012: "rows already imported keep
  pointing at the version that produced them"). `GET /layouts` returns the highest version per
  name by default and every version when `?allVersions=true`.
- Validation the database cannot express, refused with 422: every `xMin < xMax`; bands
  non-overlapping and ordered; exactly one `booked_date` column;
  `signConvention='balance_delta'` requires a `running_balance` column and forbids `amount`,
  `debit` and `credit` (its amounts derive from consecutive balances); every other convention
  requires at least one of `amount`, or both `debit` and `credit`;
  `signConvention='debit_credit_columns'` implies debit and credit columns
  and no `amount` column; `quirks` ⊆ `{filter_script:arabic, continuation:no_date_and_no_amount}`;
  every regex in `fingerprints`, `skipLinePatterns`, the balance patterns **and every
  `metadataPatterns` value** compiles — a profile is executed as regexes at parse time, so an
  uncompilable pattern must die at save, not at extraction; each reserved metadata key
  (`account_number`, `period_start`, `period_end` — §4.4) has exactly one capture group; a
  `dateFormat` with a two-digit year requires both period captures (§4.4's windowing rule).
- The `has_a_balance_anchor` CHECK is the database's floor: a profile with neither a closing
  pattern nor a running balance is rejected at insert, not warned about.
- Institution picks must be shared seeds (`household_id IS NULL`) or this household's — the
  pattern `routes/accounts.py` already uses for its institution predicate.

**Invariants / security.** `require_owner()` on both routes. RLS on all three tables. Every query
carries `household_id`. No regex from the request is ever interpolated into SQL. No bank name in
any test, fixture or default.

**Required test behaviours (failing first).**
- *DB (`0010`)*: RLS is enabled on `document`, `import_batch` and `statement_layout` and another
  household's rows are invisible; a second `document` with the same `(household_id, sha256)` is
  rejected by the unique index; a `statement_layout` with neither `closing_balance_pattern` nor
  `has_running_balance` is rejected by `has_a_balance_anchor`; `(household_id, name, version)` is
  unique; a `chronological_order` outside the allowed pair is rejected; a `transaction` insert
  carrying an `import_batch_id`, `statement_layout_id` or `source_document_id` that matches no
  row is rejected by the new FKs, and one carrying real ids is accepted. Fixtures use `XIN`/`XAE`
  and `XA`/`XB`.
- *API*: an unauthenticated call to each route returns 401; a session for another household sees
  no layouts; a profile round-trips every field byte-identically through save and list; saving the
  same name twice yields versions 1 and 2 and leaves version 1 unchanged; each validation rule
  above returns 422 with a message naming the offending field; an uncompilable regex returns 422
  rather than 500.

**Validation.** The DB gate (`db-tests.sh` reports 10 tests passed). The API gate. The web gate
for the regenerated types.

**Acceptance.** `flyway migrate` applies V12 from empty; `schema.md` and `data-model.html` carry
V12 in the same commit; a layout profile can be created and re-read with no code change to add a
new one — the ADR-0012 property, proven at the API level before any UI exists.

---

### Task 3 — The extraction engine, quirks, and account identification

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-extraction-engine`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 2 (the profile shape).

**Outcome.** One generic engine that extracts rows from any PDF given a profile, proposes a
profile for an unseen one, and resolves which account a statement belongs to — with a synthetic
PDF generator so all of it is testable without a real statement.

**Files.**
- Create `api/oneledger/ingest/__init__.py`, `api/oneledger/ingest/words.py`,
  `api/oneledger/ingest/engine.py`.
- Create `api/tests/fixtures/__init__.py`, `api/tests/fixtures/synthetic_pdf.py`.
- Create `api/tests/test_ingest_words.py`, `api/tests/test_ingest_engine.py`.
- Modify `api/oneledger/routes/layouts.py` (add `POST /layouts/propose`),
  `api/tests/test_layouts.py`.
- Modify `api/oneledger/routes/accounts.py` and `api/tests/test_accounts.py` (account-number
  hashing on write).
- Modify `docs/design/api-contracts.html` §2 (one route row). Regenerate `web/types/api/`.

**Interfaces produced** (consumed by Tasks 4b, 4c, 5d, 8a):

```python
# api/oneledger/ingest/engine.py
@dataclass(frozen=True)
class RawRow:
    page_number: int
    row_number: int          # 1-based, within the document
    raw_text: str            # the whole physical line(s), verbatim
    fields: dict[str, str]   # profile field name -> the joined cell text

@dataclass(frozen=True)
class ExtractionResult:
    rows: list[RawRow]
    declared_opening: str | None     # verbatim capture-group text, not parsed
    declared_closing: str | None
    metadata: dict[str, str]         # from metadata_patterns; verbatim — NEVER contains
                                     # the reserved account_number key (see below)
    account_number_raw: str | None   # the reserved metadata_patterns key "account_number";
                                     # transient and sensitive: hashed and discarded by the
                                     # pipeline, excluded from metadata, storage, responses,
                                     # logs and errors
    page_count: int
    has_text: bool                   # False = scanned; extraction refused
    words_filtered: int              # dropped by filter_script:*, reported (I7)
    lines_unparsed: int              # neither row start nor continuation

def matches(profile: LayoutProfileRow, page_one_text: str, filename: str) -> float: ...
def extract(profile: LayoutProfileRow, pdf_path: Path, password: str | None) -> ExtractionResult: ...
def propose(pdf_path: Path, password: str | None) -> tuple[LayoutDraft, list[RawRow]]: ...
```

**Contracts.**
- Extraction follows §4.4 exactly, including the **midpoint** band rule and the join-by-`x0`
  rule. `words.py` owns pdfplumber; `engine.py` owns profile semantics and never imports
  pdfplumber, so row assembly is testable on synthetic word lists.
- `matches()` scores by fingerprint regex hits against page 1 text and returns a value in
  `[0, 1]`. A filename hint is a **tiebreaker only**, applied by the caller, never on its own — a
  filename is a claim, not evidence. Below a stated threshold the result is "no match", which the
  caller turns into "ask, and offer to map". The engine never guesses.
- `propose()` follows §4.5. It persists nothing and proposes no balance anchor.
- **The route has two modes.** Without `draft`, it auto-proposes (`propose()`). With an optional
  multipart `draft` field — a `LayoutCreate` JSON — it validates the draft with the same rules as
  `POST /layouts` and returns it echoed alongside the rows `extract()` produces under it. This is
  the mapper's re-post path (Task 8): an edited band previews as the parse it will actually
  produce, rather than regenerating the original proposal. Persists nothing in either mode.
  In this task the draft mode returns the draft and the raw extracted rows; **Task 5f extends it**
  with the normalized preview and the reconciliation verdict once `normalize`/`reconcile` exist —
  that extended response is what Task 8a's mapper renders.
- **Account resolution.** `POST /accounts` and `PATCH /accounts/{id}` gain an optional
  `accountNumber` field: the service derives `account_number_masked` (last four, the existing
  `_masked` helper's shape) and `account_number_hash =
  sha256(ACCOUNT_HASH_SALT + normalised digits)` and stores **only those two**. The full value
  never reaches the database, a log, or a response. `ACCOUNT_HASH_SALT` is already in
  `.env.example`; a missing or empty salt refuses the write with the variable named rather than
  hashing with an empty salt. **The statement-side source is the reserved `metadata_patterns`
  key `account_number`**: a profile that captures it feeds `ExtractionResult.account_number_raw`
  (one capture group, verbatim), which the pipeline normalizes (separators stripped), hashes
  with the same salted formula, uses for lookup and discards — it never enters `metadata`, a
  stored row, a response, a log or an error, and `propose()` never proposes the key (the owner
  maps it deliberately). A resolution helper takes that hash and returns the matching account or
  `None` — it never guesses, because a wrong guess corrupts two accounts at once. A profile with
  no such capture simply always asks (the account picker), which is honest, not broken.
- **The synthetic PDF generator** writes minimal, valid PDF bytes from a list of
  `(page, x, y, text, size)` placements using base-14 Helvetica, with no new dependency and no
  file committed — the caller gets bytes or a `tmp_path` file. It must be able to produce: a
  normal two-column-amount layout; a `CR`-suffix single-amount layout; a bilingual page with
  Arabic-range text interleaved; a wrapped description with no date and no amount on its
  continuation lines; a page with zero text objects (the scanned case); and a producer that emits
  no space characters between words. That last one is M3's layout, generated here because it is
  the case the midpoint/`x0` rules exist for and it is free to cover now.

**Invariants / security.** No bank name anywhere. Nothing from the PDF is interpolated into SQL.
The raw filename and the file bytes never reach a log. The full account number is never stored,
returned or logged (never-store list). `require_owner()` on `POST /layouts/propose`.

**Required test behaviours (failing first).**
- Words are grouped into the correct physical lines when two cells share a baseline within
  tolerance and split when they do not.
- A right-aligned amount that overhangs its band on the left lands in the amount column, not the
  description — the midpoint rule, tested directly.
- A space-less producer's run-together tokens are split correctly into cells by band, and the
  same page extracted with a deliberately shifted band produces different, wrong cells — proving
  the bands, not the text, are what the engine reads.
- `filter_script:arabic` removes only Arabic-range words, reports the count in `words_filtered`,
  and leaves the Latin text and every amount intact; without the quirk the same page produces
  interleaved rows.
- `continuation:no_date_and_no_amount` joins a three-physical-line transaction into one row whose
  description contains all three fragments in order and whose amount and date come from the first;
  with `row_start_rule='has_date'` and no continuation rule, the same page yields a different,
  documented row count.
- `matches()` returns a high score for the fingerprints of the profile that produced a page, near
  zero for a different page, and never returns a match on filename alone.
- `propose()` on an unseen synthetic PDF identifies the header row, proposes bands that extract
  every row correctly with no hand-editing (the roadmap's auto-proposal gate), infers the sign
  convention and the date format, and proposes no fingerprint containing a 4-digit-or-longer digit
  run.
- `propose()` refuses rather than guessing when two date formats both parse every sample.
- Posting the same file with an edited `draft` returns that draft's extraction, not a regenerated
  proposal — a deliberately shifted band changes the preview cells, and an invalid draft is 422
  under the same rules as `POST /layouts`.
- A PDF with zero text objects yields `has_text=False`, and `extract()` refuses with a message
  saying scanned PDFs are unsupported.
- `POST /accounts` with a full account number stores only mask and hash; the full number appears
  in no column, no response body and no log; the same number on two accounts in one household is
  rejected by the existing `idx_account_number_hash`; an empty `ACCOUNT_HASH_SALT` refuses the
  write.

**Validation.** The API gate. The web gate for the regenerated types.

**Acceptance.** A synthetic statement in a format the engine has never seen can be proposed,
extracted and round-tripped with **no code change** — ADR-0012's real acceptance test, proven at
the engine level here and again through the UI in Task 8.

---

### Task 4a — Staged rows and normalization

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-staged-rows-and-normalization`
**Base and PR target:** `develop`
**Migration:** **V13** — `db/migrations/V13__staged_transactions.sql`
**Depends on:** Tasks 2, 3.
**Delivered:** +779 production, +1,315 test.

**Outcome.** The staging table exists, and `RawRow`s become validated, signed `StagedTransaction`
values — dates, signs, proposed type, description cleanup and currency — as pure functions, with
no database and no network.

**Files.** `db/migrations/V13__staged_transactions.sql`, `db/tests/0011_staged_transactions.sql`,
`api/oneledger/schemas/staging.py`, `api/oneledger/ingest/normalize.py`, and their tests.

**Contracts.** The spec's §5.2 (DDL), §5.3 (statuses) and the dates, signs, type, description and
currency contracts. `StagedTransaction` per `api-contracts.html` §4.

**Invariants / security.** I2 (signs), I7, RLS on `staged_transaction`, and the reporting-pair
lock's new reach asserted rather than worked around.

**Acceptance.** Delivered. Dedupe and reconciliation, which this task originally also carried,
are Tasks 4b and 4c.

---

### Task 4b — Dedupe: fingerprint, occurrence index, near-duplicate

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t4b-dedupe`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 4a.
**Forecast:** ~180 production, ~320 test.

**Outcome.** A normalized row carries a stable transaction fingerprint, and a row whose
fingerprint already exists in `transaction` is marked a duplicate rather than silently imported
twice. Pure functions: no database, no network.

**Files.**
- Create `api/oneledger/ingest/dedupe.py`, `api/tests/test_ingest_dedupe.py`.

**Contracts.** Layer 2 and layer 3 of the spec's §4.7, exactly. Layer 1 — the document checksum —
belongs to Task 5c, which owns the blob. The occurrence-index disambiguator is counted **among
this statement's own rows only**, never offset by committed rows and never a raw row ordinal:
that is what lets an overlapping month's rows collide with their committed copies instead of
importing twice.

**Invariants / security.** I6 (the fingerprint travels with the row unchanged). `description_raw`
is never modified; `normalise_for_hash` reads it without writing it back. No identifier reaches a
log, an error or a fingerprint input beyond what §4.7 names.

**Required test behaviours (failing first).**
- The same `(date, amount, normalised description)` twice in one statement gets occurrence indices
  0 and 1; the same pair in a later overlapping statement gets 0 and 1 again, so the hashes
  collide with the committed copies. Offsetting by committed rows must fail this test.
- A mapped, non-empty reference column is used as the disambiguator instead of the index; an empty
  one falls back to the index.
- `normalise_for_hash` is case-, whitespace- and punctuation-insensitive, and two descriptions
  differing only in those respects hash identically.
- A row matching a committed fingerprint is marked `duplicate` and carries
  `duplicate_of_transaction_id`; a near-duplicate is flagged as a stored fact, not inferred at
  read time (I7).

**Validation.** The API gate.

**Acceptance.** Re-importing an overlapping month marks exactly the overlapping rows as
duplicates, proven on synthetic rows without a database.

---

### Task 4c — Reconciliation: the four verdicts

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t4c-reconcile`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 4a. Independent of Task 4b.
**Forecast:** ~260 production, ~470 test.

**Outcome.** A statement's rows and its declared balances produce exactly one verdict —
`matched`, `mismatched`, `running_balance_break` or `unavailable` — with the evidence a reviewer
needs to act on it. Pure functions: no database, no network.

**Files.**
- Create `api/oneledger/ingest/reconcile.py`, `api/tests/test_ingest_reconcile.py`.

**Contracts.** The spec's §4.6, exactly — including the **single frame** rule (`inverts_sign`
applied to both the rows and the declared balances before the identity is evaluated), the
**chronological** reversal for a descending statement, the terminal-balance-as-declared-closing
rule for a running-balance-only layout, and `first_divergent_row` existing **only** where the
layout has a running-balance column.

**Invariants / security.** I8 (the verdict blocks). **I7**: with a closing anchor alone, no
algorithm may name *the* divergent row — `first_divergent_row` is `None` and `candidate_rows`
lists rows whose amount equals the delta exactly, with the message saying row-level evidence is
unavailable. Inventing a divergent row here is the invariant breach this task exists to avoid.

**Required test behaviours (failing first).**
- A card layout with `inverts_sign`: declared previous 4,500.00 owed and a 310.00 purchase
  reconcile to a declared new balance of 4,810.00 in one frame. The test must fail if either side
  is inverted alone — a half-inverted frame is internally consistent and entirely wrong.
- A descending statement reconciles; the same rows walked in display order flag every row as a
  break. The regression fails without the reversal.
- Running-balance layout: `first_divergent_row` names the first departing row, in **document** row
  numbers regardless of evaluation order.
- Closing-anchor-only layout with a dropped row: `first_divergent_row` is `None`, `candidate_rows`
  names the rows whose amount equals the delta; with a corrupted amount, `candidate_rows` is empty.
- Running-balance-only layout: the chronologically last row's stated balance becomes the declared
  closing, so nothing downstream receives a null.
- No tolerance: two `Decimal`s that differ by 0.0001 do not match.

**Validation.** The API gate.

**Acceptance.** The roadmap's corruption gate is provable on synthetic rows: a statement missing
one row reports the delta and the candidate rows, and blocks.

---

### Task 4d — A balance states its own direction

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `fix/m2-balance-direction`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 4a and 4c, both merged.
**Forecast:** ~60 production, ~130 test — `normalize.py` +33, `reconcile.py` +27, their tests
+53 and +75. Measured, not estimated: the change was built and then split out of Task 4c.

**Outcome.** A balance carrying a `Dr`/`Cr` suffix is read with its direction rather than
stripped of it, everywhere a balance is parsed: a row's `running_balance`, the balances
`balance_delta` derives amounts from, and reconciliation's declared anchors. Raised by Codex on
[PR #42](https://github.com/sandjaie/OneLedger/pull/42) and split out of Task 4c, which the pair
would have carried past the 1,000-line hard stop.

**Files.**
- `api/oneledger/ingest/normalize.py`, `api/tests/test_ingest_normalize.py`.
- `api/oneledger/ingest/reconcile.py`, `api/tests/test_ingest_reconcile.py`.

**Contracts.** `statement-ingestion.html`'s sign-convention table decides the reading: `Dr`
negative, `Cr` positive. `parse_amount` keeps refusing the suffix, because on an *amount* it
means something only under `cr_suffix`. A second parse reads it for balances and returns the
value beside `direction_is_stated`, the pair `SignedAmount` already carries; `NormalizedRow`
gains that flag next to `running_balance`. Reconciliation frames a balance only where its
direction was **not** stated.

**Invariants / security.** I2. A suffix read under the wrong convention, or honoured and then
inverted again, flips a sign in silence — reversing the statement instead of reframing it. The
flag travels with the value so no consumer has to infer it.

**Required test behaviours (failing first).**
- `parse_balance` reads `100.00 Dr` as `-100.0000` and `1,234.56 Cr` as `1234.5600`, both with
  the direction stated; a bare `100.00` is positive and unstated; non-numeric text is `None`.
- An account crossing into overdraft — `Cr` then `Dr` — reaches `NormalizedRow` signed and
  labelled, and its `balance_delta` amounts are right across the crossing. Fails today by twice
  the overdrawn balance.
- Under `inverts_sign`, a suffixed declared anchor and a suffixed row balance are not inverted a
  second time; an unsuffixed one still is. Each direction fails without its half.
- A running-balance-only layout with `Cr`-suffixed balances reconciles `matched`; it reports a
  false break without the fix.

**Validation.** The API gate.

**Acceptance.** The documented running-balance-only source — reverse-chronological, balance
suffixed `Cr` — reconciles rather than blocking, and no card balance is inverted twice.

### Task 5a — Money helpers extracted to `money.py`

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `refactor/m2-t5a-money-helpers`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** nothing. May land at any time.
**Forecast:** ~130 production, ~40 test.

**Outcome.** The freeze helpers live in one module both the transactions route and the import
pipeline can import, with behaviour unchanged.

**Files.**
- Create `api/oneledger/money.py` — `_money`, `_out`, `_rate_leg`, `_frozen`, `CENT`, `RATE`,
  `MONEY`, **moved** from `api/oneledger/routes/transactions.py`.
- Create `api/tests/test_money.py`.
- Modify `api/oneledger/routes/transactions.py` — import from `money.py`, nothing else.

**Contracts.** A pure move. No signature, rounding, quantization or error message changes. If a
change looks necessary, it is a separate task.

**Invariants / security.** I5 (frozen amounts derive from the quantized rate) is unchanged, and
the existing `api/tests/test_transactions.py` is the proof of that — it must pass untouched.

**Required test behaviours (failing first).**
- `test_transactions.py` passes with no edit. An edit to it is the signal that this stopped being
  a move.
- Direct tests for the moved helpers: quantization to `NUMERIC(20,4)`, half-away-from-zero
  rounding matching Postgres, and the rate-leg selection.

**Validation.** The API gate.

**Acceptance.** `git diff` shows the helpers deleted from `transactions.py` and added to
`money.py` with identical bodies; no behavioural test changed.

---

### Task 5b — FX fetch-on-missing

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t5b-fx-fetch-on-missing`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 5a.
**Forecast:** ~150 production, ~260 test.

**Outcome.** The rate a staged row needs can be fetched when it is missing, by the same code path
the backfill route already uses.

**Files.**
- Modify `api/oneledger/routes/fx.py` — extract the backfill body into a callable the route and
  the pipeline both use; the route's behaviour is unchanged.
- Modify `api/tests/test_fx*.py` as the extraction requires — name the files in the PR.

**Contracts.** The spec's §4.8. A rate is fetched for a date only when no row exists for it. A
carried-forward rate is stored **as a labelled fact**, never as though it were observed.

**Invariants / security.** **I7**: `fx_rate_is_carried` is a stored column, set at write time, not
a comparison made at read time. I5: the frozen amounts derive from the quantized stored rate.

**Required test behaviours (failing first).**
- The backfill route behaves identically after the extraction — its existing tests pass unedited.
- A missing date fetches; a present date does not.
- A weekend or holiday with no published rate carries the previous rate forward and marks it
  carried; the test asserts the flag, not just the value.
- A fetch failure surfaces as a stated error, not a silently skipped row.

**Validation.** The API gate.

**Acceptance.** The pipeline can obtain a rate for any date in a statement's period without the
owner running a backfill first.

---

### Task 5c — Document storage

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t5c-document-storage`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 4a.
**Forecast:** ~160 production, ~290 test.

**Outcome.** An uploaded statement is checksummed, stored under a name that leaks nothing, and
recognised on a second upload.

**Files.**
- Create `api/oneledger/ingest/storage.py`, `api/tests/test_ingest_storage.py`.

**Contracts.** The spec's §5.6 and layer 1 of §4.7: `sha256` of the file bytes; the path
`<DOCUMENTS_DIR>/<household_id>/<sha256>.pdf`; an identical file rejected with the earlier
import's date and batch id named.

**Invariants / security.** **The uploaded filename is never the stored filename.** Real statement
filenames carry card numbers — the stored name is the checksum, and the original name never
reaches a path, a log line or an error message. Path traversal, absolute paths and symlinks in the
supplied name are refused. `DOCUMENTS_DIR` missing refuses the write naming the variable.

**Required test behaviours (failing first).**
- A filename containing a card number is stored as `<sha256>.pdf` and the original never appears
  in the path, the response, the log or the raised error.
- `../`, an absolute path and a symlinked target are each refused.
- The same bytes uploaded twice are recognised on checksum, with the earlier batch named.
- The same bytes for a different household are a different document.

**Validation.** The API gate.

**Acceptance.** The roadmap's dedupe gate at the document layer: the same file twice imports once.

---

### Task 5d — The parse pipeline

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t5d-parse-pipeline`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 4b, 4c, 5b, 5c.
**Forecast:** ~260 production, ~470 test.

**Outcome.** One module runs identify → extract → normalize → dedupe → reconcile → FX → stage, and
it is the only module in `ingest/` that holds a database connection.

**Files.**
- Create `api/oneledger/ingest/pipeline.py`, `api/tests/test_ingest_pipeline.py`.

**Contracts.** The spec's §4.1 (only `pipeline.py` touches the database), §4.3 (dry run then
stage), §5.4 (the staging transaction: blob first, then `document` + `import_batch` + rows in one
transaction) and §5.5 (what a blocked batch leaves behind — nothing persisted). **Reconciliation
runs before FX enrichment**, as `statement-ingestion.html` §1 and `architecture.html` §5 now both
document. Identification order: content fingerprint, then filename hint as a tiebreaker only.

**Invariants / security.** I8 (a blocked batch stages nothing), I5, I6. Account resolution is by
`account_number_hash`; the raw number is transient and never stored, logged or returned.

**Required test behaviours (failing first).**
- A blocked batch persists **nothing** — no blob, no `document` row, no `import_batch`. Assert the
  tables are empty afterwards, not merely that an error was raised.
- Staging is one transaction: an induced failure after the blob write leaves no database row.
- The order is observable: with FX unreachable, a batch that cannot reconcile still blocks, and no
  rate row is written.
- A filename hint alone never selects a profile; a fingerprint match does.

**Validation.** The API gate, the DB gate.

**Acceptance.** A synthetic PDF parses end to end into staged rows with a verdict, through one
function call, with no route involved.

---

### Task 5e — `POST /parse`

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t5e-parse-route`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 5d.
**Forecast:** ~200 production, ~380 test.

**Outcome.** The web app can post a PDF and receive either a staged batch or a blocked verdict it
can render.

**Files.**
- Create `api/oneledger/routes/ingest.py`, `api/tests/test_parse.py`.
- Modify `api/oneledger/main.py` (register the router).
- Modify `docs/design/api-contracts.html` §2 and §4. Regenerate `web/types/api/`.

**Contracts.** The spec's §4.2 and §4.3: multipart PDF plus a `stage` flag; **200 whether or not
the batch is blocked** — a blocked batch is a result, not an error; `409` with
`password_required` for an encrypted file.

**Invariants / security.** `require_owner()` on the route. Money is a string on the wire.
No identifier, no original filename and no raw account number in any response or error body.

**Required test behaviours (failing first).**
- Unauthenticated returns 401; another household's batch is not visible.
- A blocked batch returns 200 with the verdict, and stages nothing.
- An encrypted PDF returns 409 `password_required`; a scanned PDF with no text layer refuses with
  a stated reason rather than returning zero rows as success.
- Amounts are strings in the response; no JSON number appears on a money field.

**Validation.** The API gate, the web gate for regenerated types.

**Acceptance.** Roadmap M2's dedupe, overlap, corruption and FX gates are provable through the
route.

---

### Task 5f — The layout draft preview completes

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t5f-layout-draft-preview`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 5d. Independent of Task 5e.
**Forecast:** ~110 production, ~220 test.

**Outcome.** `POST /layouts/propose`'s draft mode returns the normalized preview and the
reconciliation verdict the mapper needs, not just raw rows.

**Files.**
- Modify `api/oneledger/routes/layouts.py`, `api/tests/test_layouts.py`.
- Modify `docs/design/api-contracts.html` §2. Regenerate `web/types/api/`.

**Contracts.** The spec's §4.3 and §4.5. Draft mode still **persists nothing**. An edited band
previews as the parse it would actually produce, rather than regenerating the original proposal.

**Invariants / security.** `require_owner()`. Persists nothing. No identifier in the preview
payload — the rule Task 3's review rounds established.

**Required test behaviours (failing first).**
- A draft returns normalized rows and a verdict; the original proposal is not regenerated.
- Nothing is persisted in either mode — assert the tables afterwards.
- A draft that cannot reconcile still returns 200 with the verdict.

**Validation.** The API gate, the web gate for regenerated types.

**Acceptance.** The mapper (Task 8a) has the exact payload it renders.

---

### Task 6a — V14: the commit and verification functions

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t6a-import-commit-migration`
**Base and PR target:** `develop`
**Migration:** **V14** — `db/migrations/V14__import_commit.sql`
**Depends on:** Task 5e.
**Forecast:** ~180 production, ~280 test.

**Outcome.** The database can turn a staged batch into ledger rows in one all-or-nothing
statement, and can refuse to do so when the batch does not reconcile.

**Files.**
- Create `db/migrations/V14__import_commit.sql`, `db/tests/0012_import_commit.sql`.
- Modify `docs/superpowers/schema.md` (a one-line note: V14 carries functions only) and
  `docs/design/data-model.html` §17's migration list — **same commit as the migration**.

**Contracts.** The spec's §4.9. `assert_account_reconciles(p_account_id, p_declared_closing,
p_period_end)` is called by the commit, and `layout_version` and `dedupe_hash` pass through
unchanged (I6).

**Invariants / security.** Every `SECURITY DEFINER` function calls `assert_is_owner()` **first**.
I1/I5: committed money is immutable, enforced by V10's existing trigger. Forward-only: V14 is
never edited once applied.

**Required test behaviours (failing first).**
- `assert_is_owner()` is called first: a non-owner session is refused before any row is read.
- A batch that does not reconcile is refused by `assert_account_reconciles`.
- A failure part-way through commits nothing — count `transaction` rows afterwards.
- `dedupe_hash` and `layout_version` on the committed row equal the staged row's.

**Validation.** The DB gate (12 tests passed).

**Acceptance.** `flyway migrate` applies V14 from empty; `schema.md` and `data-model.html` carry it
in the same commit.

---

### Task 6b — The review payload

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t6b-review-payload`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 6a.
**Forecast:** ~150 production, ~270 test.

**Outcome.** `GET /import/{batch_id}` returns everything the review screen renders: the staged
rows, their statuses, the verdict and the diagnostics.

**Files.**
- Modify `api/oneledger/routes/ingest.py`, `api/tests/test_import_review.py` (new).
- Modify `docs/design/api-contracts.html` §2. Regenerate `web/types/api/`.

**Contracts.** The spec's §4.2 and §5.3 (the status vocabulary). Diagnostics per
`statement-ingestion.html` §12: extracted rows beside the computed totals, and the counts of what
was filtered or left unparsed (I7).

**Invariants / security.** `require_owner()`. Money as strings. Another household's batch is not
readable. No raw account number or original filename in the payload.

**Required test behaviours (failing first).**
- Unauthenticated 401; another household's batch 404, not 403 — existence is not disclosed.
- `words_filtered` and `lines_unparsed` are reported, not hidden (I7).
- **A blocked parse leaves no batch for this route to return** (spec §5.5): the id from a blocked
  `POST /parse` response 404s here. The blocked verdict lives in the parse response and is
  rendered by Task 7b and Task 9b — this route serves only batches that actually staged.

**Validation.** The API gate, the web gate for regenerated types.

**Acceptance.** The review screen (Task 9a) can be built against this payload with no further
route change.

---

### Task 6c — Row decisions

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t6c-row-decisions`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 6a. Independent of 6b and 6d.
**Forecast:** ~170 production, ~300 test.

**Outcome.** `PATCH /import/{batch_id}/rows` accepts, corrects or excludes staged rows before the
commit, including the override that accepts a row marked duplicate.

**Files.**
- Modify `api/oneledger/routes/ingest.py`, `api/tests/test_row_decisions.py` (new).
- Modify `docs/design/api-contracts.html` §2. Regenerate `web/types/api/`.

**Contracts.** The spec's §4.7: **accepting a `duplicate` row is a confirmed-distinct override.**
V10's unique `(household_id, account_id, dedupe_hash)` index means the colliding hash cannot commit
unchanged, so the endpoint recomputes the row's hash with the occurrence index advanced to the next
free index among committed collisions — deterministic and stable, not a random suffix.

**Invariants / security.** `require_owner()`. V13's `accepted_row_is_committable` check: an
accepted row always carries a `proposed_txn_type`. A correction never edits `description_raw`.

**Required test behaviours (failing first).**
- Accepting a duplicate recomputes the hash to the next free index; two overrides in a row produce
  distinct, deterministic hashes, and a repeat of the same override is idempotent.
- Accepting a row with no `proposed_txn_type` is refused by the database check, not by the route
  only.
- An excluded row is not committed; a corrected row keeps its `description_raw`.

**Validation.** The API gate, the DB gate.

**Acceptance.** A genuinely distinct transaction that collides with a committed one can be
imported, and the same transaction twice still cannot.

---

### Task 6d — The atomic commit

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t6d-atomic-commit-route`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 6a.
**Forecast:** ~180 production, ~340 test.

**Outcome.** `POST /import/{batch_id}/commit` turns accepted staged rows into ledger rows, or
changes
nothing at all.

**Files.**
- Modify `api/oneledger/routes/ingest.py`, `api/tests/test_import_commit.py` (new).
- Modify `docs/design/api-contracts.html` §2. Regenerate `web/types/api/`.

**Contracts.** The spec's §4.9. The route calls V14's function; the atomicity lives in the
database, not in Python.

**Invariants / security.** `require_owner()`. I1/I5: committed money is immutable. I8: a batch
that does not reconcile cannot commit, even with every row accepted.

**Required test behaviours (failing first).**
- **An induced failure mid-import leaves zero rows** — the roadmap's own gate. Count `transaction`
  rows before and after.
- Committing twice is refused the second time; the batch's status prevents it.
- A batch with an unreconciled verdict is refused.
- Balances are recomputed after the commit.

**Validation.** The API gate, the DB gate.

**Acceptance.** The roadmap's atomicity gate passes: an induced failure mid-import leaves zero
rows.

---

### Task 7a — The `/import` shell

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t7a-import-screen-shell`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 5e.
**Forecast:** ~240 production, ~240 test.

**Outcome.** `/import` exists, is reachable from the nav, and accepts multiple dropped files.

**Files.**
- Create `web/app/import/page.tsx`, `web/app/import/import-screen.tsx`,
  `web/app/import/__tests__/import-screen.test.tsx`.
- Modify `web/app/workspace.tsx` and `web/app/__tests__/workspace.test.tsx` (the `"import"`
  section and nav entry).
- Modify `web/app/globals.css` as the screen needs.

**Contracts.** `ui-information-architecture.html` §2 wins over the mockup on disagreement.
TypeScript formats money and performs no arithmetic on it.

**Invariants / security.** No money arithmetic in TypeScript. No secret behind `NEXT_PUBLIC_`.
The screen renders what the API returns; it is never the authorization boundary.

**Required test behaviours (failing first).**
- Dropping several files lists them all, each independently.
- A non-PDF is rejected in the UI with a stated reason.
- The nav entry appears and routes; `workspace.test.tsx` covers the new section.

**Validation.** The web gate.

**Acceptance.** Multi-file drop works and nothing is uploaded until the owner acts.

---

### Task 7b — Per-file status and Stage batch

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t7b-import-file-status`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 7a.
**Forecast:** ~200 production, ~220 test.

**Outcome.** Each dropped file reports its own outcome, and the batch is staged only when the
owner says so.

**Files.**
- Modify `web/app/import/import-screen.tsx` and its test.

**Contracts.** One `POST /parse` per file. A per-file failure never fails the others. A visible
summary precedes staging; nothing is uploaded without a click.

**Invariants / security.** Money formatted, never computed. A blocked file shows its verdict, not
a generic error.

**Required test behaviours (failing first).**
- One file failing leaves the others' statuses intact.
- An encrypted file reports that it needs a password and is skipped; **M2 offers no prompt and no
  retry** (§10 — M3 owns the prompt, and M2 must not create a place to put one).
- Nothing is posted until Stage batch is clicked.
- A blocked file's verdict text reaches the screen.

**Validation.** The web gate.

**Acceptance.** The roadmap's `/import` requirement: multi-file drop, per-file status, nothing
staged without an explicit action.

---

### Task 8a — The mapper: header row, bands, live preview

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t8a-layout-mapper-preview`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 5f and 7a.
**Forecast:** ~260 production, ~260 test.

**Outcome.** An unrecognised statement's proposed layout is visible: the matched header row, the
proposed column bands, and a live preview of the rows they produce.

**Files.**
- Create `web/app/import/layout-mapper.tsx`,
  `web/app/import/__tests__/layout-mapper.test.tsx`.

**Contracts.** `docs/product/mockups/LayoutMapping.dc.html` is the artboard contract — "HEADER ROW
FOUND" with the matched words, "PROPOSED COLUMNS" with each field's `x` range, "PREVIEW · N ROWS"
with date, description, amount and balance, and the reconciliation line beneath it.
`ui-information-architecture.html` wins on disagreement.

**Invariants / security.** Money formatted from strings, never parsed into a number. The preview
renders the API's normalized rows; it does not re-derive them.

**Required test behaviours (failing first).**
- The proposal renders its bands, its header row and its preview rows.
- The reconciliation verdict is shown, including the blocked case.
- No `parseFloat`/`Number()` on an amount — the static check must cover the new file.

**Validation.** The web gate.

**Acceptance.** A proposed layout can be read and understood before anything is saved.

---

### Task 8b — The mapper: drag, re-post, save

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t8b-layout-mapper-editing`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 8a.
**Forecast:** ~240 production, ~250 test.

**Outcome.** A band can be adjusted, the effect seen, and the corrected profile saved.

**Files.**
- Modify `web/app/import/layout-mapper.tsx` and its test.
- Modify `web/app/import/import-screen.tsx` (the handoff) and its test.

**Contracts.** An edited band re-posts as a **draft** (Task 5f) and previews the parse it would
actually produce. Saving calls `POST /layouts`; the saved profile is what a later import matches.

**Invariants / security.** Nothing is persisted until save. No identifier from the statement is
rendered into a label, a title attribute or a saved profile name.

**Required test behaviours (failing first).**
- Dragging a band re-posts the draft and re-renders the preview from the response, not locally.
- Save persists; cancel persists nothing.
- A draft that cannot reconcile can still be saved, with the verdict shown — the owner decides.

**Validation.** The web gate.

**Acceptance.** Roadmap M2's first two gates: a layout can be mapped through the UI, saved, and
matched on the next import.

---

### Task 9a — The review screen

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t9a-review-screen`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 6b, 6c, 6d.
**Forecast:** ~300 production, ~300 test.

**Outcome.** A staged batch is reviewed row by row and committed from the app.

**Files.**
- Create `web/app/import/review-screen.tsx`,
  `web/app/import/__tests__/review-screen.test.tsx`.
- Modify `web/app/import/import-screen.tsx` (route to review after staging) and its test.

**Contracts.** `Review.dc.html` and `ui-information-architecture.html` §3, the latter winning on
disagreement. **Nothing imports without a click** — no row is pre-accepted in M2.

**Invariants / security.** Money formatted, never computed. A duplicate row is excluded by
default and accepting it is a deliberate act with its consequence stated.

**Required test behaviours (failing first).**
- No row is accepted by default; committing with nothing accepted imports nothing.
- A duplicate row is visibly marked and excluded until overridden.
- A batch with no accepted rows commits nothing. (A *blocked* batch never reaches this screen —
  it never staged; §5.5.)
- Amounts render as formatted strings; no arithmetic in the component.

**Validation.** The web gate.

**Acceptance.** A batch can be reviewed and committed end to end from the app.

---

### Task 9b — The diagnostics panel

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t9b-diagnostics-panel`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 7b. Independent of Task 9a.
**Forecast:** ~200 production, ~200 test.

**Outcome.** When a parse blocks, the owner can see why, in the statement's own numbers — on the
import screen, because a blocked batch never staged and has no review screen (§5.5).

**Files.**
- Create `web/app/import/diagnostics-panel.tsx`,
  `web/app/import/__tests__/diagnostics-panel.test.tsx`.
- Modify `web/app/import/import-screen.tsx` (the panel renders the `POST /parse` response for a
  blocked file) and its test.

**Contracts.** `statement-ingestion.html` §12: extracted rows beside the computed totals, the
declared and computed closing balances, the delta, the divergent row or the candidate rows, and
the counts of what was filtered or left unparsed.

**Invariants / security.** **I7 rendered honestly**: where no divergent row can be named, the
panel says row-level evidence is unavailable and lists the candidates. It never presents a guess
as a finding. Carried-forward rates are labelled as such.

**Required test behaviours (failing first).**
- A running-balance layout shows the divergent row; a closing-anchor-only layout shows the
  candidates and the "evidence unavailable" wording instead.
- `words_filtered` and `lines_unparsed` are shown, not hidden.
- A carried-forward rate is labelled where it affects a displayed amount.

**Validation.** The web gate.

**Acceptance.** A blocked batch is explicable from the screen without reading a log.

---

### Task 9c — The end-to-end gate and M2 acceptance

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-t9c-import-acceptance`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 9a, 9b.
**Forecast:** ~60 production, ~280 test.

**Outcome.** The whole importer is proven in one automated run, and M2's acceptance is recorded.

**Files.**
- Create `api/tests/test_import_e2e.py`.
- Modify `docs/superpowers/progress.md` (M2 complete, with the owner-run results).

**Contracts.** Synthetic statements only — no real statement enters this repository, on any
branch, ever.

**Invariants / security.** The run exercises I2, I5, I6, I7 and I8 together. The owner-run
acceptance against real statements happens outside the repository and is recorded as a result,
never as a fixture.

**Required test behaviours (failing first).**
- A synthetic statement parses, stages, reviews and commits, and the ledger holds exactly the
  accepted rows.
- An overlapping second statement imports only the new rows.
- A corrupted statement blocks and commits nothing.
- A second commit of the same batch is refused.

**Validation.** The full local hook suite — the web gate, the API gate and the DB gate.

**Acceptance.** Every item in §9 checked, with the owner-run result against real statements
recorded in `progress.md`.

---

## 9. Milestone done-when checklist

Mapped one-to-one to roadmap M2's "Done when" list. Each is checked in the PR named.

| Roadmap gate | Proven by | Where |
|---|---|---|
| A layout can be mapped through the UI, saved and reused with no code change | Mapping UI save + reuse; API-level round trip | Tasks 2, 8 |
| Auto-proposal identifies the header row and proposes usable bands without hand-editing | `propose()` extracts every row correctly from an unseen synthetic layout; the mapper renders it | Tasks 3, 8 |
| All 38 card statements import end to end with no manual correction of dates or amounts | **Owner-run**, recorded in `progress.md` | Task 9 |
| Reconciliation passes for every one | **Owner-run**, recorded in `progress.md` | Task 9 |
| Re-importing the same file imports zero rows and names the earlier import | Layer-1 checksum test; end-to-end test; owner-run on a real file | Tasks 5, 9 |
| An overlapping statement imports only the new rows | Layer-2 occurrence-index test; owner-run | Tasks 4, 5, 9 |
| A deliberately corrupted amount blocks the batch and names the divergent row | `mismatched` verdict + zero staged rows (I8); the row is named where running-balance evidence exists, else the exact delta and candidates — the honest form, §7.10 | Tasks 4, 5 |
| The commit is atomic — an induced failure mid-import leaves zero rows | `db/tests/0012` induced-failure case | Task 6 |
| A backup has run and been restored into a scratch database | `scripts/backup-db.sh`; **owner-run** restore | Tasks 1, 9 |
| Deferred in from Phase 0: CI (lint, type-check, test, migrations-from-empty) | `.github/workflows/ci.yml`, proven green on PR #32, then kept manual-only by owner decision (§3.1); the local hook suite is the enforced gate | Task 1 |
| Deferred in from Phase 0: `scripts/backup-db.sh` | The script, run | Task 1 |

Roadmap M2's **Build** list maps as: generic engine → Task 3; mapping UI → Task 8; field-name
synonyms → Task 3; pdfplumber by x-position → Task 3; one layout end to end → Tasks 8, 9; the two
quirk handlers → Task 3; normalization → Task 4; dedupe → Tasks 4, 5; FX with fetch-on-missing →
Task 5; reconciliation blocking → Tasks 4, 5; original on the documents volume and `document` /
`import_batch` / `staged_transaction` written → Tasks 2, 4, 5; review screen → Tasks 6, 9; atomic
commit with post-commit verification → Task 6; the `/import` screen → Task 7.

---

## 10. Explicitly out of scope

Named so nobody builds them opportunistically.

- **The remaining three layouts** and their fixtures, password-protected PDFs, the scanned-PDF
  refusal UX, and multi-line handling beyond the one continuation rule — **M3**. (Task 3's
  synthetic generator covers the space-less producer because it is what the band rules exist for
  and costs nothing; the layout itself is still M3's to map.)
- **The classification cascade**, seed rules, merchant normalization, rule CRUD, confidence
  scoring and learn-from-correction — **M4**. M2 stages every row `unclassified` at `0.000`.
- **Transfer matching**, scoring, confirmation and FX cost — **M5**. `staged_transaction`'s
  `proposed_transfer_*` columns are created and left null.
- **`card_statement`** records, utilisation and due dates — **M6**.
- **The document library**, `GET /documents/{id}/original`, receipt attachment and owner export —
  **M10**.
- **The reporting static checks** (`testing-and-data-quality.html` §2) — M5 owns the base-table
  check; see §7.6.
- **The `{"error": {"code": ...}}` envelope** across the whole API surface — see §7.5.
- **Settings › Data Quality** (`GET /doctor`) — it reports on imported data and belongs after
  there is some.

---

## 11. Self-review

- **Roadmap coverage**: every Build bullet and every Done-when item in the roadmap's M2 section is
  mapped in §9. The two deferred Phase-0 items are Task 1.
- **Placeholders**: none. Every task names exact files, exact contracts, exact test behaviours and
  exact validation commands with expected output.
- **Type consistency**: `LayoutProfile`/`LayoutColumn` (Task 2) are consumed by
  `propose()` (Task 3) and the mapper (Task 8) under the same field names;
  `RawRow`/`ExtractionResult` (Task 3) are consumed by `normalize`/`reconcile` (Task 4);
  `Verdict` (Task 4) is serialised as `ReconciliationResult` (Task 5) and reused unchanged in
  `BatchReview` (Task 6) and the review screen (Task 9); `StagedTransaction` (Task 4) is the
  staging insert's shape (Task 5) and `StagedRow` (Task 6) is its wire projection. Statuses use
  one vocabulary throughout: `matched | mismatched | running_balance_break | unavailable` for
  reconciliation, `new | duplicate | near_duplicate | needs_review | accepted | rejected` for
  staged rows.
