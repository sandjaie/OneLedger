# M2 Implementation Plan — the statement importer, one layout end to end

> **For agentic workers:** this plan follows the CLAUDE.md rule **"Plans are execution maps"**.
> It carries outcomes, contracts, invariants, required test behaviours and acceptance criteria —
> not pre-written patches. The implementer inspects the repository before deciding the exact
> implementation, and may deviate from an intended detail when the contracts and acceptance
> criteria stay satisfied, recording the deviation in the PR and in
> `docs/superpowers/progress.md` — never by editing this plan mid-task. Task state lives only in
> `docs/superpowers/progress.md`.
>
> **M2 ships as nine task-sized PRs**, not one. Each task below declares its own `Workspace`,
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

| # | Branch | Carries | Migration |
|---|---|---|---|
| 1 | `chore/m2-ci-and-backup` | This plan; CI workflow; `scripts/backup-db.sh`; stale-cap doc fix | — |
| 2 | `feature/m2-documents-and-layouts` | `document`, `import_batch`, `statement_layout`; layouts API | **V12** |
| 3 | `feature/m2-extraction-engine` | Word/band extraction, quirks, `propose`; account hashing | — |
| 4 | `feature/m2-staged-rows-and-normalization` | `staged_transaction`; normalize, dedupe, reconcile | **V13** |
| 5 | `feature/m2-parse-and-stage` | FX fetch-on-missing, storage, `POST /parse` | — |
| 6 | `feature/m2-review-and-commit-api` | Review payload, row decisions, atomic commit | **V14** |
| 7 | `feature/m2-import-screen` | `/import`: multi-file drop, per-file status, Stage batch | — |
| 8 | `feature/m2-layout-mapping-ui` | The mapping UI: bands, drag, live preview, save | — |
| 9 | `feature/m2-review-screen-and-acceptance` | Review screen, diagnostics panel, end-to-end gate | — |

Tasks are strictly ordered: 2 → 3 → 4 → 5 → 6 → {7, 8} → 9. Task 1 goes first so the operational
pieces (the proven CI workflow, kept manual-only by owner decision — §3.1 — and the restorable
backup) exist before any importer table does; the enforced per-PR gate is the local hook suite. Tasks 7 and 8 are independent of each other and may be delivered in either order,
but both after 5 (they consume `POST /parse` and `POST /layouts/propose`).

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

## 4. Architecture decisions

Grounded in the current tree, which the implementer should read before starting each task.

### 4.1 New module layout

```
api/oneledger/
  money.py                    # Task 5: freeze helpers extracted from routes/transactions.py
  ingest/
    __init__.py
    words.py                  # Task 3: pdfplumber words -> banded rows; scanned detection
    engine.py                 # Task 3: matches() / extract() / propose(); quirk handlers
    normalize.py              # Task 4: dates, signs, currency, description cleanup
    dedupe.py                 # Task 4: fingerprint + near-duplicate
    reconcile.py              # Task 4: the four verdicts
    storage.py                # Task 5: blob write, filename masking
    pipeline.py               # Task 5: orchestration + the staging transaction
  schemas/
    __init__.py
    staging.py                # Task 4: StagedTransaction (api-contracts.html §4 names this path)
  routes/
    layouts.py                # Tasks 2 and 3
    ingest.py                 # Tasks 5 and 6
```

`api/oneledger/ingest/` **may not import from `api/oneledger/routes/`** and may not open a
database connection except in `pipeline.py`. Extraction, normalization, dedupe and reconciliation
are pure functions over values, unit-testable with no database (`testing-and-data-quality.html`
§2, "Parsers may not import from lib/queries").

Web:

```
web/app/import/
  page.tsx                    # Task 7
  import-screen.tsx           # Task 7
  layout-mapper.tsx           # Task 8
  review-screen.tsx           # Task 9
  diagnostics-panel.tsx       # Task 9
  __tests__/*.test.tsx
```

`web/app/workspace.tsx` gains an `"import"` section: the `Props["section"]` union,
`sectionFromPath()` and the nav — a `next/link` entry labelled **Import** pointing at the
`/import` route, placed between Accounts and Transactions, following the existing `Link` entries
exactly. The disabled "Plans" nav button stays as it is.

### 4.2 Route surface added by M2

All under `require_owner()`, all money as decimal strings, all field names **camelCase** on the
wire (matching `routes/transactions.py` and `routes/accounts.py`).

| Route | Task | Does |
|---|---|---|
| `GET /layouts` | 2 | The household's saved layout profiles, newest version per name |
| `POST /layouts` | 2 | Saves a profile. Re-saving an existing `name` creates `version + 1` |
| `POST /layouts/propose` | 3 | Multipart PDF → a draft profile: header row, column bands, sample rows. With an optional `draft` field, previews that edited draft against the file instead of auto-proposing. Persists nothing in either mode |
| `POST /parse` | 5 | Multipart PDF + `stage` flag. Identify, extract, normalize, dedupe, reconcile; stage only when `stage=true` and the verdict allows it |
| `GET /import/{batch_id}` | 6 | The batch, its verdict and its staged rows grouped for review |
| `PATCH /import/{batch_id}/rows` | 6 | Bulk row decisions: accept, reject, categorize |
| `POST /import/{batch_id}/commit` | 6 | One database transaction: staged accepted rows become `transaction` rows, then the batch re-verifies and rolls back whole on mismatch |

`GET /documents/{id}/original` is **out of scope** — the document library is M10. The review
screen shows `description_raw`, page and row number, which is what M2's traceability gate needs.

### 4.3 The parse contract: dry run then stage

`POST /parse` takes the file, an optional `layoutId`, an optional `accountId`, and a boolean
`stage` (default **false**).

- `stage=false` writes **nothing** and returns the full summary: identification result,
  reconciliation verdict, row counts by proposed status, and the first 50 normalized rows for
  preview. This is the roadmap's "nothing written until Stage batch".
- `stage=true` performs the staging transaction described in §5.4 and returns the same summary
  plus `batchId`.

The file crosses localhost twice. That is acceptable at statement sizes and avoids a
server-side scratch store with its own lifetime, ownership and cleanup problems; revisit only if
measured latency makes it unacceptable.

### 4.4 The extraction engine

One engine, driven entirely by a profile row. `matches()`, `extract()`, `propose()` per
`statement-ingestion.html` §4. Extraction rules:

1. `page.extract_words()` gives words with `x0`, `x1`, `top`, `bottom`, `text`. Group words into
   **physical lines** by `top` within a tolerance (the profile's default; propose from the modal
   line height). Never sort by reading order across columns.
2. Assign each word to the column whose `[x_min, x_max)` band contains the word's **horizontal
   midpoint** `(x0 + x1) / 2`. Midpoint, not `x0`: a right-aligned amount that overhangs its band
   on the left otherwise lands in the description column. A word whose midpoint falls in no band
   is dropped from `fields` but stays in `raw_text`.
3. Within a column, join words by ascending `x0` with a single space. **This is what makes the
   space-less producer work** (M3's layout D) and it costs nothing here.
4. A line matching any `skip_line_patterns` entry is dropped before row assembly.
5. Row assembly follows `row_start_rule`. `has_date_and_amount`: a line starts a row when its
   date column parses under `date_format` **and** its amount column parses as a number.
   `has_date`: the date alone starts a row. Any other line, when `continuation_rule` is
   `no_date_and_no_amount`, appends its description column to the previous row's description
   with a single space; with no continuation rule it is dropped and counted as unparsed.
6. `raw_text` is the whole physical line, kept verbatim, for the diagnostics panel.

**Quirk `filter_script:arabic`.** Before line grouping, drop any word whose text contains a
character in `U+0600–U+06FF`, `U+0750–U+077F`, `U+08A0–U+08FF`, `U+FB50–U+FDFF` or
`U+FE70–U+FEFF`. Dropped words are counted and the count is reported in the parse summary
(Invariant 7 — a filter that silently removes content is indistinguishable from a parser bug).

**Quirk `continuation:no_date_and_no_amount`.** As rule 5 above. Recorded as a profile flag so
that the vocabulary, not the layout, is what grew.

The quirk vocabulary is **exactly these two** in M2. A third is a considered addition with the
owner's agreement, not a reflex (ADR-0012).

**Reserved metadata keys.** `metadata_patterns` is a generic capture map, with three reserved
keys the pipeline understands: `account_number` (sensitive and transient — Task 3),
`period_start` and `period_end` (the statement period, parsed under the profile's
`date_format`). When the period keys are captured they are authoritative: they gate row dates
(a parsed date outside the period fails its row) and window two-digit years. When absent, the
period is **derived** from the parsed row dates and labelled derived (I7) in the parse summary
and on `import_batch`. Because windowing two-digit years against a derived period is circular,
**a profile whose `date_format` has a two-digit year must capture both period keys — enforced
at save (422)**. Every reserved capture takes exactly one capture group, enforced at save.

**Scanned detection.** A page whose `extract_words()` returns zero words is a scanned page.
`extract()` reports `has_text=false` and the parse is refused with a message saying scanned PDFs
are not supported, never garbage rows. The full M3 refusal UX is out of scope here.

### 4.5 `propose()` — auto-proposal

Given the pages of an unrecognised PDF:

1. Find the **header row**: the physical line whose words match the most entries in a synonym
   table, requiring at least a date synonym and one amount synonym. The synonym table is
   **module-level data in `engine.py`**, grouped by field, case-insensitive, whitespace- and
   punctuation-insensitive: `booked_date` ← date, tran date, transaction date, txn date, value
   date, posting date; `description` ← description, narration, particulars, details, remarks;
   `reference` ← ref, ref no, reference, chq no, cheque no, chq./ref.no.; `amount` ← amount;
   `debit` ← debit, withdrawal, withdrawal amt, dr, debits; `credit` ← credit, deposit, deposit
   amt, cr, credits; `running_balance` ← balance, closing balance, running balance. **No entry
   in this table is a bank name.**
2. Propose a band per matched header word: `x_min` is the midpoint between this word's `x0` and
   the previous word's `x1`; `x_max` the midpoint between this word's `x1` and the next word's
   `x0`. The first band extends to the page's left text margin, the last to the right margin.
3. Infer `sign_convention`: `debit_credit_columns` when both a debit and a credit header matched;
   otherwise `cr_suffix` when any sampled amount cell ends in `CR`/`DR` (case-insensitive);
   otherwise `signed`.
4. Infer `date_format` by trying `dd/MM/yyyy`, `dd-MM-yyyy`, `dd MMM yyyy`, `dd/MM/yy`,
   `yyyy-MM-dd` against the sampled date cells and keeping the one that parses every sample
   unambiguously; propose nothing rather than guess when two formats both parse every sample.
5. Infer `chronological_order` from the sampled dates.
6. Propose `fingerprints` as up to three distinctive lines from page 1 that are not the header
   row. The digit screen runs on a **normalized** copy of the line — every separator that
   formats an identifier (whitespace, `-`, `–`, `.`, `/`) removed between digits first — and
   rejects any line whose normalized form contains a digit run of length ≥ 4. `123-456-789`
   normalizes to a 9-digit run and is rejected; checking each raw 3-digit group independently
   would wave it through, and a formatted full account number must never become a stored
   fingerprint (the never-store list is absolute).
7. Return the draft **plus the first 20 rows extracted with it**, so the UI can show the parse
   live. `propose()` persists nothing and never invents a balance anchor: the draft's balance
   patterns are empty and the owner supplies them, which the `has_a_balance_anchor` CHECK then
   enforces at save.

### 4.6 Reconciliation (Invariant I8)

`reconcile.py` returns exactly one verdict, computed on **native signed ledger amounts** —
after `inverts_sign` has been applied to both the rows *and* the declared balances, so the
identity holds in one frame. All balance arithmetic runs in **chronological order**: when the
profile's `chronological_order` is `descending`, the rows are reversed first, because the
running-balance identity `balanceₙ = balanceₙ₋₁ + amountₙ` reads the *previous chronological*
row, and applying it in display order on a descending statement flags every valid row as a
break. Reported row numbers always reference document row numbers regardless of the
evaluation order:

```
opening_ledger + Σ(amount_ledger) == closing_ledger
```

For a card layout with `inverts_sign = true`: a declared "previous balance 4,500.00" (owed) is
`-4500.0000` in the ledger, a purchase presented as `310.00` is `-310.0000`, a declared "new
balance 4,810.00" is `-4810.0000`, and `-4500 + -310 = -4810` reconciles. Getting this frame
wrong produces a batch that is internally consistent and entirely wrong, which is why it is
stated here rather than left to the implementer.

Verdicts:

- `matched` — the identity holds exactly (both sides are `Decimal`; there is no tolerance).
  For a running-balance-only layout (no closing pattern), the **chronologically last row's
  stated running balance is the terminal balance**: every row-to-row step must hold, and that
  terminal value plays the declared-closing role everywhere downstream — it is what staging
  records on `import_batch` and what the commit verifies against (§4.9), so the verification
  never receives a null.
- `mismatched` — it does not. Carries `declared_closing`, `computed_closing`, `delta`,
  `first_divergent_row` and `candidate_rows`. `first_divergent_row` is the first row at which
  the running total departs from the stated running balance — it exists **only when the layout
  has a running-balance column**; with a closing anchor alone no algorithm can honestly name
  *the* divergent row, because any single row could absorb the delta (I7 forbids inventing one).
  In that case `first_divergent_row` is `None` and `candidate_rows` lists the rows whose amount
  equals the delta exactly (the dropped-row case; empty for a corrupted amount), and the
  message says row-level evidence is unavailable. **Blocks either way.**
- `running_balance_break` — the layout has `has_running_balance` and no declared closing;
  `balanceₙ = balanceₙ₋₁ + amountₙ` fails at a row. Carries that row number, the previous
  balance, the amount and both the expected and stated balances. **Blocks.**
- `unavailable` — the layout's configured balance evidence did not extract. **Blocks in M2.**
  The `has_a_balance_anchor` CHECK means every saved layout declares a closing pattern or a
  running-balance column, so `unavailable` at parse time is never "this statement has no
  balances" — it is a shifted band, a changed label or a parse regression, and letting it
  through would commit rows that bypassed reconciliation entirely. The message says the
  configured evidence failed to extract and points at remapping the layout. A genuinely
  anchorless statement source, if one ever exists, is a separate explicit case for the
  milestone that meets it — not this value. (The **draft-preview** in the mapper is the one
  place `unavailable` renders without blocking anything: an auto-proposed draft has no anchors
  yet, and nothing is being staged.)

**Reconciliation runs immediately after normalization, before FX enrichment.** The documented
stage order in `statement-ingestion.html` §1 puts it after FX; running the cheap, purely local,
blocking check first means a doomed batch costs no network round trip and no rate rows. The
staged verdict recorded on `import_batch` is identical either way. Record this ordering in the
Task 4/5 PRs.

### 4.7 Dedupe

Three layers, per `statement-ingestion.html` §6.

- **Layer 1 — document checksum.** `sha256` of the file bytes, uniquely indexed on
  `(household_id, sha256)`. Checked before parsing; an identical file is rejected with the date
  of the earlier import and its batch id named. Free, and catches the common case.
- **Layer 2 — transaction fingerprint.**
  `sha256("|".join([account_id, booked_date.isoformat(), f"{amount:.4f}", normalise_for_hash(description_raw), disambiguator]))`
  where `normalise_for_hash` uppercases, collapses whitespace and strips punctuation, and
  `disambiguator` is `reference_raw` when the profile maps a reference column and the value is
  non-empty, **otherwise the occurrence index within the day, within this document** — the nth
  identical `(date, amount, normalised description)` among this statement's own rows, counted
  from 0 in statement order. Never counted against rows already committed: a statement is a
  stable, self-contained ordering of its day, so the same transaction carries the same index in a
  Jul–Aug and an Aug–Sep statement and the hashes collide — which is exactly how the overlap is
  recognised. An offset by committed rows would hand the overlapping row index 1 while the
  committed copy holds index 0, and August would import twice. And never a raw row ordinal within
  the document either — an ordinal differs between two statements covering the same day even when
  the day's identical rows are the same.
  A row whose hash already exists in `transaction` is staged with `status='duplicate'` and
  `duplicate_of_transaction_id` set, and is excluded from import by default. **Accepting a
  duplicate is a confirmed-distinct override**: V10's unique
  `(household_id, account_id, dedupe_hash)` index means the colliding hash cannot commit
  unchanged, so the accept endpoint recomputes and stores the row's hash with the occurrence
  index advanced to the next free index among committed collisions — deterministic, stable, and
  it keeps future overlap detection intact (a later statement carrying both occurrences hashes
  them 0 and 1 and recognises both as duplicates). The unique index remains the final guard;
  a commit-time collision still fails loudly rather than silently skipping.
- **Layer 3 — near-duplicate warning.** Same account, same amount, `booked_date` within ±3 days,
  description token-set similarity ≥ 0.85 (a pure-Python token-set ratio over the normalised
  descriptions; no new dependency). Staged as `status='near_duplicate'`. **Warns, never blocks** —
  it catches a genuinely double-charged subscription as readily as a re-import, and only the owner
  can tell those apart.

Manual rows created by M1b use their own occurrence-index formula and are not expected to collide
with imported rows; layer 3 is what surfaces that overlap. Do not change the M1b formula.

### 4.8 FX enrichment with fetch-on-missing

Today `routes/transactions.py::_rate_leg` refuses a save when a rate is missing, and
`POST /fx/backfill` is the only writer of `fx_rate`. M2 adds fetch-on-missing without a second
implementation of either:

1. Extract `POST /fx/backfill`'s body — `reporting_config` → `terminal_anchor` → `fetch_range` →
   `to_rows` → `upsert_rates` — into a plain function in `api/oneledger/routes/fx.py` (or a new
   `api/oneledger/fxrange.py` if the import graph reads better), called by both the route and the
   pipeline. The route's future-date and ordering guards stay on the route.
2. **Only on the `stage=true` path.** A dry run (`stage=false`) skips the FX step entirely — no
   fetch, no upsert, no `fx_rate` row: the preview shows native amounts only, and "a dry run
   writes nothing" includes reference data. When staging, the pipeline computes the batch's
   distinct booked dates, checks which lack a stored rate for the pair it needs, and fetches
   **one contiguous range** covering the gaps before the staging transaction opens.
3. If the fetch fails or leaves a date uncovered, **the import is refused** with the uncovered
   dates named. Never a fallback to `1.0`, never today's rate, never an interpolation
   (`multi-currency-and-fx.html` §3).
4. A carried-forward rate stamps `fx_rate_is_carried = true` and the earlier `fx_rate_date`
   (Invariant I7 — and the `carried_rate_dates` CHECK on `transaction` enforces it at commit).

### 4.9 The commit

`POST /import/{batch}/commit` calls a single `SECURITY DEFINER` Postgres function
`commit_import_batch(p_batch_id UUID)` created in **V14** (api-contracts.html §3: "if a failure
halfway through would leave the ledger inconsistent, it belongs in one function in the database").
The function, in order:

1. `PERFORM assert_is_owner();`
2. Locks the household row `FOR UPDATE` — it is a writer of frozen amounts (spec B3.5).
3. Refuses unless the batch belongs to the current household and its `reconciliation_status` is
   `matched` — the only status a staged batch can carry in M2, since every other verdict blocks
   staging (§4.6, §5.5); the function still checks rather than assumes.
4. `INSERT INTO transaction (...) SELECT ... FROM staged_transaction WHERE import_batch_id = $1
   AND status = 'accepted'`, carrying `source='statement_import'`, `source_document_id`,
   `source_page_number`, `source_row_number`, `import_batch_id`, `statement_layout_id`,
   `layout_version` and `dedupe_hash` through unchanged (Invariant I6), and `txn_type` from the
   staged row's `proposed_txn_type` — non-null on every accepted row by V13's CHECK (§5.2).
5. Calls `assert_account_reconciles(p_account_id, p_declared_closing, p_period_end)` — also
   created in V14, also `SECURITY DEFINER`, also calling `assert_is_owner()` first — which
   **`RAISE`s immediately when either argument is null** (a null comparison must never silently
   pass as verified), then computes the account's balance **as of the statement close** in
   Postgres (the account's opening balance plus the sum of its posted transactions with
   `booked_date <= p_period_end`) and `RAISE`s on any difference from the declared closing.
   `p_declared_closing` is the batch's recorded closing target: the closing-pattern capture
   when the layout declares one, else the terminal running balance §4.6 defines — staged on
   `import_batch`, so the value always exists for a batch that reached staging. Not the maintained
   `current_balance`: the multi-file screen imposes no chronological commit order, and a valid
   older statement committed after a newer one would compare its closing against a balance that
   already contains the newer postings and wrongly roll back. This is the post-commit
   verification, inside the same transaction, so a raise rolls the whole batch back.
6. Updates `import_batch` to `status='completed'` with `rows_imported` and `completed_at`.

M2's commit inserts `transaction` rows only. `transfer` (M5) and `card_statement` (M6) inserts
named in `statement-ingestion.html` §11 belong to those milestones.

---

## 5. Table and storage contracts

### 5.1 V12 — `document`, `import_batch`, `statement_layout`

`schema.md` `0008` is the shape, verbatim, with these M2 additions and one correction:

- All three tables get `ENABLE ROW LEVEL SECURITY` and a `household_isolation` policy
  (`schema.md` `0008` shows the policy for `statement_layout` only; `document` and `import_batch`
  need it too — `schema.md` carries no policies by design, so this is an addition, not a
  disagreement).
- `document.storage_bucket` is the constant `'documents'` and `storage_path` is relative to
  `DOCUMENTS_DIR` (already in `.env.example`, already a compose volume).
- `import_batch.reconciliation_status` stays `TEXT` with a `CHECK` restricting it to
  `matched | mismatched | running_balance_break | unavailable`. The design doc's `switch` uses
  `reconciled`; the column comment in `schema.md` uses `matched`. **`matched` wins** — it is the
  column's documented value set. Task 4 records the alignment.
- `statement_layout` keeps `has_a_balance_anchor`. `UNIQUE (household_id, name, version)` makes
  version bumping a plain insert.
- `document.original_filename` is written **masked** — see §5.6.
- **V12 completes the deferred foreign keys whose targets it creates** (the `0018` rule: an FK
  clause lands with its target table, not earlier and not later): `ALTER TABLE transaction` adds
  the constraints for `source_document_id → document`, `receipt_document_id → document`,
  `import_batch_id → import_batch` and `statement_layout_id → statement_layout`. All four columns
  are entirely NULL today, so validation is trivial. `classification_rule_id` stays a bare UUID —
  its target arrives with M4, which adds only that FK. `schema.md`'s V10 note currently says "the
  M4 migration that completes the target set adds their foreign keys"; Task 2 corrects that
  sentence to the per-target rule in the same commit (§7.8).

### 5.2 V13 — `staged_transaction`

`statement-ingestion.html` §9 is the shape. It is not currently in `schema.md`, so Task 4 adds it
as a new subsection under the `V13 (applied)` heading in the same commit. M2 deltas:

- `proposed_transfer_peer_txn_id`, `duplicate_of_transaction_id` and `near_duplicate_of_id`
  reference `transaction(id)`, which exists — declare them as real foreign keys.
- `proposed_transfer_kind` is created as `TEXT` with a `CHECK` (the `transfer_kind` enum arrives
  with M5) and `classification_rule_id` as a bare `UUID` (the table arrives with M4) — the same
  deferred-reference pattern V10 already uses and documents.
- A `CHECK`: `status <> 'accepted' OR proposed_txn_type IS NOT NULL` — an accepted row always
  carries a committable type, because `transaction.txn_type` is `NOT NULL` and a row that can be
  accepted but never committed is a dead end the database refuses to represent.
- `ENABLE ROW LEVEL SECURITY` + `household_isolation`.
- **Consequence to test, not to work around:** `reporting_pair_is_locked()` (V7) discovers every
  table carrying both `amount_base` and `household_id`. Creating `staged_transaction` therefore
  makes a *staged, uncommitted* batch lock the household's reporting pair. That is correct — those
  rows are already frozen against the current pair — and Task 4's DB test asserts it, so nobody
  later reads it as an accident.

### 5.3 Statuses

`staged_transaction.status ∈ {new, duplicate, near_duplicate, needs_review, accepted, rejected}`.
M2 stages `new` (clean), `duplicate` (layer 2 hit) or `near_duplicate` (layer 3 hit); `needs_review`
is set by M4's classifier. Review moves rows to `accepted` or `rejected`. Only `accepted` commits.
Every staged row carries a `proposed_txn_type` (sign-derived at normalization, overridable in
review — Task 4's Type contract), so acceptance always leaves a row that can commit into
`transaction.txn_type NOT NULL`.

### 5.4 The staging transaction

Per api-contracts.html §4: the service writes the original into the documents volume **first**,
then writes `document`, `import_batch` and every staged row **inside one database transaction**.
Original first, because a staged batch referencing a blob that failed to write is worse than an
orphaned file. The blob is written to a **uniquely named temporary file** in the same directory
and atomically renamed to its final `<sha256>.pdf` path before the transaction opens, so a
partial write never sits at the final name (two identical concurrent uploads renaming to the
same path is harmless — the bytes are identical by construction of the name). On any failure the
database transaction rolls back and cleanup removes **only the request's own uniquely named
temporary file — never the final-path blob**. A committed-rows ownership check cannot close the
race: a concurrent identical upload's `document` row may be inserted but not yet committed, so
under MVCC the failing request would see no owner and delete a blob the winner is about to
reference. A final-path orphan is the safe residue instead — it is inert, content-addressed
(identical bytes by construction), and self-healing: the next identical upload renames over it,
and `GET /doctor` may report unreferenced blobs when the document library arrives (M10).

### 5.5 What a blocked batch leaves behind

**Nothing.** A reconciliation mismatch, a running-balance break, an `unavailable` verdict
(configured evidence that failed to extract — §4.6), an unidentified layout, an
unresolved account, a missing FX rate or a parse failure leaves the database **unchanged** —
no `document`, no `import_batch`, no staged row (api-contracts.html §4's error table; Invariant
I8's "stages zero rows"). The verdict travels in the `POST /parse` response body so the
diagnostics panel can render it.

This deviates from `data-model.html` §12's sentence "a blocked import records why it blocked".
The reason is functional: `document.sha256` is uniquely indexed per household, so a persisted
blocked document would make its own checksum reject the retry after the owner re-maps the layout —
the exact recovery path ADR-0012 exists to provide. **Task 4 corrects that sentence in
`data-model.html`** to say the batch records its verdict for batches that stage, and records the
correction in its PR. Do not work around it in code.

### 5.6 Filenames, logs and the never-store list

`pdf-statements/` holds real statements **with card numbers in the filenames**. Two consequences:

1. `document.original_filename` is stored **masked**: the mask detects identifier-length digit
   sequences on a **normalized** view — separators that format an identifier (whitespace, `-`,
   `–`, `.`, `_`, `/`) are ignored between digit groups — and replaces each whole formatted
   sequence totalling 7 or more digits with `…` plus its last four digits, before the value
   reaches the database. `statement-1234-5678-9012-3456.pdf` masks to `statement-…3456.pdf`;
   checking only contiguous runs would store that full card number. The unmasked filename exists
   only as a local variable during the request.
2. **No log line, error message, exception detail or HTTP response may contain the raw filename,
   the file bytes, a password, or any digit sequence of 7 or more digits — contiguous or
   separator-formatted — from the document.** Parse errors name page and row numbers and the
   *masked* filename.

Task 5 owns both, and Task 5's tests assert them.

---

## 6. Migration ownership

| Migration | Task | Contents |
|---|---|---|
| `db/migrations/V12__documents_and_import.sql` | 2 | `document_type`, `parse_status`, `sign_convention` types; `document`, `import_batch`, `statement_layout`; indexes; RLS; the four deferred FKs on `transaction` (§5.1) |
| `db/migrations/V13__staged_transactions.sql` | 4 | `staged_transaction` + indexes + RLS |
| `db/migrations/V14__import_commit.sql` | 6 | `assert_account_reconciles()`, `commit_import_batch()` |

`db/tests/` ordinals: **0010** (Task 2), **0011** (Task 4), **0012** (Task 6). The `db/tests`
sequence is independent of `schema.md` section numbers — 0008 and 0009 are already taken by
categories and transactions.

`schema.md` gains `## V12 (applied)` and `## V13 (applied)` sections replacing its unapplied
`0008` reference section; after M2 its unapplied sequence begins at `0009`
(classification rules). `data-model.html` §17's migration list is updated to match.

**No migration is ever edited after it is applied.** If V12 is wrong after Task 2 merges, V15
fixes it forward.

---

## 7. Contradictions between the documents and the tree

Named here because CLAUDE.md requires the disagreement to be stated and fixed at its source, not
worked around.

1. **The PR line cap.** `docs/git-workflow.md` and `CLAUDE.md` say "no hard line cap — keep each
   PR reviewable at one sitting". `docs/superpowers/plans/implementation-plan.md` line 53 still
   says "at most 500 lines of implementation and test code per PR", and
   `docs/superpowers/phase-0-pull-requests.md` §"The 500-line cap" still states the cap.
   **`docs/git-workflow.md` is the authority.** Task 1 fixes `implementation-plan.md` line 53 to
   point at the git-workflow rule. `phase-0-pull-requests.md` is a Phase 0 historical document
   and is left alone; **CLAUDE.md's index row still describes it as "branch/review workflow and
   the 500-line cap" — that is the owner's file, so Task 1 flags it in the PR rather than
   editing it.**
2. **`pdfplumber` is not a new dependency.** `api/pyproject.toml` already declares
   `pdfplumber>=0.11.10` and `api/uv.lock` pins `0.11.10`; nothing imports it yet. M2 adds no
   dependency at all. Any planning note describing pdfplumber as a new addition is out of date.
3. **`reconciled` versus `matched`.** `statement-ingestion.html` §8's TypeScript sketch returns
   `reconciled`; `schema.md` `0008` documents the column's values as `matched|mismatched|unavailable`.
   Resolved in §5.1: the column value set wins, extended with `running_balance_break`, which §8
   describes but the column comment omits.
4. **A blocked import's durability.** `api-contracts.html` §4 says the database is unchanged;
   `data-model.html` §12 says a blocked import records why it blocked. Resolved in §5.5 in favour
   of api-contracts, with `data-model.html` corrected by Task 4.
5. **The error envelope.** `api-contracts.html` §6 specifies
   `{"error": {"code": ..., "message": ..., "detail": {...}}}`; every implemented route returns
   FastAPI's `{"detail": "..."}`. M2 does **not** introduce a second error shape for a handful of
   routes. Blocking conditions are not HTTP errors at all — `POST /parse` returns **200** with a
   `reconciliation` object carrying `status`, `declaredClosing`, `computedClosing`, `delta` and
   `firstDivergentRow`, which is what the diagnostics panel needs and what a `detail` string could
   never carry. Genuine client errors keep the established `detail` string. Task 5 records this;
   converging the whole surface on §6's envelope is a separate, repo-wide change and is out of
   M2's scope.
6. **The static checks do not exist.** `testing-and-data-quality.html` §2 and CLAUDE.md describe
   build-failing static checks (reports never read the base table; no `amount × rate`; no
   `parseFloat`/`Number()` on money; no currency/institution/country identifier). None is in the
   tree — there is no such script and no such ESLint rule. Roadmap **M5** owns the reporting
   check; M2 does not build any of them. Recorded here so the gap is known rather than assumed
   covered. M2's own compliance is enforced by review and by the tests each task declares.
7. **`FxRateResponse` uses snake_case** (`carried_from`) while every later route uses camelCase.
   M2's new models use camelCase; the existing response is not renamed here (it would break the
   Settings FX inspector for no M2 benefit).
8. **Who adds V10's deferred foreign keys.** `schema.md`'s V10 note says "the M4 migration that
   completes the target set adds their foreign keys" — all five at once, in M4. The `0018` rule
   is per target: an FK clause lands with its target table. Resolved per target (§5.1): V12 adds
   the four FKs whose targets it creates, M4 adds `classification_rule`'s. Waiting for M4 would
   leave M2's own commit path writing `import_batch_id`, `statement_layout_id` and
   `source_document_id` into `transaction` for two milestones with no referential check. Task 2
   corrects the `schema.md` sentence in the migration's commit.
9. **The dedupe index in `statement-ingestion.html` §6.** The specification's own worked example
   establishes that any statement covering a day carries the same set for that day — the
   within-document property — yet one sentence then said the index is computed against the union
   of committed rows and earlier batch rows. Under the union rule a committed overlap shifts
   every incoming index and the recognition defeats itself. Corrected at source in Task 1's PR:
   the index counts within the current document only; the split-boundary edge stages as a
   visible `duplicate` for review instead of silently doubling a month.
10. **The roadmap's "names the divergent row" gate.** With a closing-anchor-only layout (no
    running-balance column) there is no per-row balance evidence, so no algorithm can honestly
    name *the* divergent row — any single row could absorb the delta. §4.6 defines the honest
    diagnostic: the row is named when running-balance evidence exists; otherwise the verdict
    carries the exact delta and the candidate rows whose amount equals it (the dropped-row
    case), and says that row-level evidence is unavailable. The roadmap gate is met in that
    form; inventing a row number would violate I7.

---

## 8. Tasks

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

**Interfaces produced** (consumed by Tasks 4, 5, 8):

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
  In this task the draft mode returns the draft and the raw extracted rows; **Task 5 extends it**
  with the normalized preview and the reconciliation verdict once `normalize`/`reconcile` exist —
  that extended response is what Task 8's mapper renders.
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

### Task 4 — Staged rows, normalization, dedupe and reconciliation

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-staged-rows-and-normalization`
**Base and PR target:** `develop`
**Migration:** **V13** — `db/migrations/V13__staged_transactions.sql`
**Depends on:** Tasks 2, 3.

**Outcome.** The staging table exists, and `RawRow`s become validated, signed, deduplicated,
reconciled `StagedTransaction` values — all as pure functions, no database, no network.

**Files.**
- Create `db/migrations/V13__staged_transactions.sql`, `db/tests/0011_staged_transactions.sql`.
- Create `api/oneledger/schemas/__init__.py`, `api/oneledger/schemas/staging.py`.
- Create `api/oneledger/ingest/normalize.py`, `dedupe.py`, `reconcile.py`.
- Create `api/tests/test_ingest_normalize.py`, `test_ingest_dedupe.py`, `test_ingest_reconcile.py`.
- Modify `docs/superpowers/schema.md` (a `## V13 (applied)` section) and
  `docs/design/data-model.html` (§12's blocked-import sentence per §5.5, §17's migration list) —
  **same commit as the migration**.

**Interfaces produced** (consumed by Tasks 5, 6):

```python
# api/oneledger/schemas/staging.py — api-contracts.html §4 is the shape; keep its two
# model_validators (translations agree with the stated rate; original amount and currency
# travel together) and add the M2 fields the staging insert needs.
class StagedTransaction(BaseModel): ...          # per api-contracts.html §4

# api/oneledger/ingest/reconcile.py
@dataclass(frozen=True)
class Verdict:
    status: Literal["matched", "mismatched", "running_balance_break", "unavailable"]
    declared_opening: Decimal | None
    declared_closing: Decimal | None
    computed_closing: Decimal | None
    delta: Decimal | None
    first_divergent_row: int | None   # only with running-balance evidence (§4.6)
    candidate_rows: list[int]         # rows whose amount equals the delta, else empty
    message: str            # owner-facing, names the numbers and the row or the delta
```

**Contracts.**
- **Dates.** The profile's `date_format` is used explicitly; no inference at parse time. The
  statement period comes from the reserved `period_start`/`period_end` captures when the profile
  maps them (authoritative — a parsed date outside the period fails the row, and two-digit years
  window against it, never a fixed pivot); otherwise the period derives from the parsed row
  dates and is labelled derived (§4.4 — and a two-digit `date_format` cannot reach this branch,
  save-time validation requires the captures).
- **Signs (I2).** All four conventions per `statement-ingestion.html` §5, then `inverts_sign`
  applied last. A card purchase presented as a positive debit stores **negative**; a card payment
  stores positive. Assert this explicitly in tests — a sign error is internally consistent and
  therefore invisible.
- **Type.** `proposed_txn_type` is set deterministically at normalization: `expense` for a
  negative ledger amount, `income` for a positive one. `transaction.txn_type` is `NOT NULL`, so
  a row with no type could be accepted but never committed. The sign-derived default is a
  labelled mechanical rule shown in review and overridable there before acceptance — I7 bars
  silent invention, and this is neither silent nor invented. Refund, transfer and settlement
  types are M4/M5/M6 refinements.
- **Description.** `description_raw` is **never modified** — it is the audit record.
  `description_clean` is derived by an ordered, inspectable list of removals (terminal and
  reference numbers, trailing dates, POS/ATM prefixes, repeated whitespace, trailing country and
  currency codes), preserving case. No cleverness; the owner must be able to see exactly what was
  stripped, so the applied rules are returned alongside the value.
- **Currency.** The account's currency, unless the layout maps a foreign amount, in which case
  `original_amount`/`original_currency` carry it and `amount` stays the account's. The bank's own
  conversion is authoritative; the ledger never second-guesses it with a market rate.
- **Dedupe** exactly per §4.7, including the **within-document** occurrence-index disambiguator —
  the index is counted among this statement's own rows only, never offset by committed rows.
- **Reconciliation** exactly per §4.6, including the single-frame rule for `inverts_sign`.
- `staged_transaction` DDL per §5.2.

**Invariants / security.** I2 (signs), I7 (`fx_rate_is_carried` and the near-duplicate flag are
stored facts, not inferences at read time), I8 (the verdict is computed here and blocks in
Task 5). RLS on `staged_transaction`. `reporting_pair_is_locked()`'s new reach is asserted, not
worked around (§5.2).

**Required test behaviours (failing first).**
- *DB (`0011`)*: RLS enabled and isolating; the status CHECK rejects an unknown status; setting
  `status='accepted'` with a null `proposed_txn_type` is rejected by the V13 CHECK; a staged
  row cascades away with its `import_batch`; **with one staged row present,
  `reporting_pair_is_locked()` returns true and `update_household_configuration` refuses a pair
  change** — and with none it returns false.
- *Dates*: `03/09/2026` under `dd/MM/yyyy` is 3 September; the same string under `MM/dd/yyyy` is 9
  March; a date outside the statement period fails the row with the period named; a two-digit year
  windows into the statement's period.
- *Signs*: each of the four conventions produces the documented sign; `inverts_sign=true` flips a
  card purchase to negative and a payment to positive; a `CR` suffix is credit and a bare amount
  debit.
- *Description*: `description_raw` is byte-identical to the extracted cell; a `POS 4821 … AE 030926
  REF 887262819` sample cleans to the merchant name with the applied rules listed.
- *Dedupe*: the same transaction appearing in a Jul–Aug and an Aug–Sep statement hashes
  identically (the within-document occurrence index, not the ordinal — construct the case with
  different ordinals and assert equality, including with the Jul–Aug copy already committed);
  two genuinely distinct same-day identical transactions in one statement hash differently
  (indexes 0 and 1); a second same-day identical transaction appearing only in the later
  overlapping statement takes index 1, collides with nothing committed, and imports;
  a row with a reference uses the reference; a near-duplicate at ±3 days and 0.86 similarity is
  flagged and **not** blocked.
- *Reconciliation*: a correct card statement reconciles exactly; on a **running-balance layout**
  a corrupted amount yields `mismatched` naming `first_divergent_row`; on a
  **closing-anchor-only layout** one dropped row yields `mismatched` with the delta equal to the
  dropped amount, `first_divergent_row=None` and the twin amount's rows in `candidate_rows`,
  while a corrupted amount yields empty candidates and a message saying row-level evidence is
  unavailable (§4.6 — never an invented row); a running-balance layout with a
  broken row yields `running_balance_break` naming that row, the expected and the stated balance;
  a **descending** statement's running balances reconcile only under chronological-order
  evaluation — the same fixture mis-evaluated in display order flags breaks (§4.6's order rule,
  asserted directly); a running-balance-only layout's verdict carries the chronologically-last
  stated balance as its terminal declared closing;
  a draft or layout whose configured evidence produced no values yields `unavailable` — a
  blocking verdict in M2 (§4.6), non-blocking only in the mapper's draft preview; an
  `inverts_sign` statement
  reconciles in the ledger frame and **fails** if the declared balances are not inverted with the
  rows.
- *Property (hypothesis)*: for any generated row set, `opening + Σ(amounts) == computed_closing`
  exactly; and `StagedTransaction`'s validator rejects any `amount_base` inconsistent with its
  stated rate.

**Validation.** The DB gate (11 tests passed). The API gate.

**Acceptance.** V13 applies from empty; `schema.md` and `data-model.html` updated in the same
commit including the §5.5 correction; every normalization, dedupe and reconciliation behaviour
above is proven without a database and without the network.

---

### Task 5 — FX fetch-on-missing, storage, and `POST /parse`

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-parse-and-stage`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 2, 3, 4.

**Outcome.** A PDF posted to the service is identified, extracted, normalized, deduplicated,
reconciled and — only if the owner asked to stage and the verdict allows — written as a document,
a batch and staged rows, in one database transaction, with FX frozen and any missing rate fetched.

**Files.**
- Create `api/oneledger/money.py` (the freeze helpers, **moved** from
  `api/oneledger/routes/transactions.py`: `_money`, `_out`, `_rate_leg`, `_frozen`, `CENT`,
  `RATE`, `MONEY`), `api/oneledger/ingest/storage.py`, `api/oneledger/ingest/pipeline.py`,
  `api/oneledger/routes/ingest.py`.
- Create `api/tests/test_ingest_storage.py`, `api/tests/test_parse.py`.
- Modify `api/oneledger/routes/transactions.py` (import from `money.py`; behaviour unchanged),
  `api/oneledger/routes/fx.py` (extract the backfill body into a callable both the route and the
  pipeline use), `api/oneledger/routes/layouts.py` and `api/tests/test_layouts.py` (the draft
  mode gains its normalized preview and reconciliation verdict — see Contracts),
  `api/oneledger/main.py`, `api/tests/test_transactions.py`,
  `api/tests/test_fx*.py` as the refactor requires.
- Modify `docs/design/api-contracts.html` §2/§4. Regenerate `web/types/api/`.

**Interfaces produced** (consumed by Tasks 6, 7, 8, 9):

```python
# POST /parse response — 200 whether or not the batch is blocked
class ParseSummary(BaseModel):
    batchId: UUID | None              # null on a dry run or a blocked batch
    documentId: UUID | None
    layoutId: UUID | None
    layoutVersion: int | None
    layoutCandidates: list[LayoutCandidate]   # id, name, score — when identification is ambiguous
    accountId: UUID | None
    periodStart: date | None
    periodEnd: date | None
    pageCount: int
    rowsExtracted: int
    rowsNew: int
    rowsDuplicate: int
    rowsNearDuplicate: int
    wordsFiltered: int
    linesUnparsed: int
    reconciliation: ReconciliationResult
    preview: list[PreviewRow]         # first 50: page, row, bookedDate, descriptionRaw,
                                      # amount, runningBalance, computedBalance, isDivergent
    blockedReason: str | None         # owner-facing; null when stageable

class ReconciliationResult(BaseModel):
    status: str                       # matched | mismatched | running_balance_break | unavailable
    declaredOpening: str | None
    declaredClosing: str | None
    computedClosing: str | None
    delta: str | None
    firstDivergentRow: int | None     # only with running-balance evidence (§4.6)
    candidateRows: list[int]
    message: str
```

**Contracts.**
- `POST /parse` is `multipart/form-data`: `file`, optional `layoutId`, optional `accountId`,
  optional `password`, boolean `stage` (default false). Per §4.3.
- **Identification order**: content fingerprint (`matches()`), then filename hint as a tiebreaker
  only, then ask. When no profile scores above the threshold, the response carries
  `layoutCandidates` with scores and `blockedReason` explaining that the layout must be chosen or
  mapped; nothing is written.
- **Account resolution**: by `account_number_hash` from the profile's reserved `account_number`
  metadata capture (Task 3's transient contract — normalized, hashed, discarded). When the
  profile captures nothing or the hash matches no account, the response says so and the caller
  must supply `accountId`; the service never guesses.
- **FX** per §4.8. Fetching happens **after** reconciliation passes and **before** the staging
  transaction opens, so no network call happens inside an open transaction. The prefetch reads
  the reporting pair **optimistically**; the staging transaction, after acquiring the household
  lock, **re-reads the pair and revalidates**. If the pair changed since the prefetch (possible
  only before the first frozen write pins it), the transaction aborts cleanly, the pipeline
  refetches for the new pair, and retries once — a second change refuses the import naming the
  cause. Frozen amounts are always derived under the pair read inside the lock, never the
  prefetch's.
- **Staging** per §5.4 and §5.5: blob first, then `document` + `import_batch` + rows in one
  transaction, taking the household row lock **before** reading the reporting pair (spec B3.5).
  A blocked verdict writes nothing at all.
- **Storage** per §5.6: path `<DOCUMENTS_DIR>/<household_id>/<sha256>.pdf`, `storage_bucket =
  'documents'`, `storage_path` relative, `original_filename` masked, mode `0600`, written to a
  temporary name and renamed so a partial file is never mistaken for a stored original.
- `password` is a local variable for the duration of the parse. It is never written to disk, the
  database, a log or a response, and never returned to the client.
- The `money.py` move is a **pure move**: no behaviour change, and `test_transactions.py` must
  pass unchanged apart from import paths. Two implementations of the freeze would drift, and a
  drifted freeze is a wrong number that looks right.
- **The draft preview completes here.** `POST /layouts/propose`'s draft mode (Task 3) now also
  returns the normalized preview rows and a `reconciliation: ReconciliationResult` computed
  under the draft's sign convention, `inverts_sign` and balance patterns. Reconciliation is a
  within-statement identity on native amounts — no account, no FX — so an unsaved mapping
  previews its true verdict. Auto-propose mode reports `unavailable` (a fresh draft proposes no
  balance anchor). Still persists nothing in either mode.

**Invariants / security.** I5 (frozen once, derived from the quantized rate), I6 (every staged row
carries document, page and row), I7 (carried rates labelled; `wordsFiltered` and `linesUnparsed`
reported), I8 (a blocking verdict stages zero rows). `require_owner()`. B3.5 household lock. The
never-store rules of §5.6, tested.

**Required test behaviours (failing first).**
- A synthetic statement parses dry-run and writes **nothing** — assert zero rows in `document`,
  `import_batch` and `staged_transaction` afterwards, and the `fx_rate` row count unchanged even
  when the statement's dates have no stored rate (the dry run fetches nothing).
- The same file with `stage=true` writes exactly one document, one batch and N staged rows, and
  the blob is byte-identical to the upload.
- Re-posting the identical file is rejected before parsing, naming the earlier import's date and
  batch id, and writes nothing.
- An overlapping statement stages only the non-overlapping rows; the overlapping ones stage as
  `duplicate` and are excluded from import by default.
- A deliberately corrupted amount yields `mismatched` and **stages zero rows** (Invariant I8) —
  assert the three tables are empty afterwards. It names `firstDivergentRow` when the layout
  carries a running balance; on a closing-anchor-only layout it reports the exact delta with
  row-level evidence unavailable (§4.6).
- A booked date with no stored FX rate triggers exactly one fetch covering the gap (the source is
  stubbed; **no test reaches the network**), then stages successfully; when the stub fails, the
  import is refused with the uncovered dates named and nothing is written, and no row falls back
  to `1.0` or today's rate.
- A weekend booked date stages with `fx_rate_is_carried = true` and an earlier `fx_rate_date`
  (I7).
- A reporting-pair change racing a staging batch serializes correctly in both interleavings
  (B3.5), matching the shape of the existing `test_transactions_live.py` proof — and the
  assertions cover the outcome, not just the contention: after a pair change that lands between
  the FX prefetch and the household lock, the staged rows' frozen `amount_base` /
  `amount_secondary` are derived under the **final** pair with rates that cover it (the
  locked-revalidation retry), never under the stale prefetch pair.
- An unidentified layout returns candidates and scores and writes nothing.
- An unresolved account returns the account-picker state and writes nothing.
- A filename containing a 16-digit run is stored masked, and so is one carrying the same number
  formatted in hyphenated groups of four (`…-1234-5678-9012-3456.pdf` → `…3456`); a parse
  failure's message contains neither the raw filename nor any 7-or-more-digit sequence,
  contiguous or formatted; the password never appears in any response, log record or stored row.
- A scanned PDF is refused with the unsupported message and writes nothing.
- The draft-mode propose response carries the normalized preview and the draft's own
  reconciliation verdict: a correct draft yields `matched`, a deliberately shifted amount band
  yields a blocking verdict, and nothing is written in either case.
- A profile capturing `period_start`/`period_end` reports that period; one without reports the
  derived period labelled derived (§4.4), on the parse summary and on the staged batch.
- An `unavailable` verdict (configured evidence failing to extract) blocks staging and writes
  nothing (§4.6).

**Validation.** The API gate, the DB gate, the web gate for regenerated types.

**Acceptance.** Roadmap M2's dedupe, overlap, corruption and FX gates are provable end to end at
the API level, on synthetic statements, offline.

---

### Task 6 — Review payload, row decisions, and the atomic commit

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-review-and-commit-api`
**Base and PR target:** `develop`
**Migration:** **V14** — `db/migrations/V14__import_commit.sql`
**Depends on:** Task 5.

**Outcome.** A staged batch can be reviewed, decided and committed — as one all-or-nothing
database transaction that verifies the resulting balance and rolls back whole on any mismatch.

**Files.**
- Create `db/migrations/V14__import_commit.sql`, `db/tests/0012_import_commit.sql`.
- Modify `api/oneledger/routes/ingest.py` (three routes), `api/tests/test_import_commit.py` (new).
- Modify `docs/superpowers/schema.md` (note V14 carries functions only — `schema.md` carries no
  functions by design, so this is a one-line note, not a DDL section) and
  `docs/design/data-model.html` §17's migration list.
- Modify `docs/design/api-contracts.html` §2. Regenerate `web/types/api/`.

**Interfaces produced** (consumed by Tasks 7, 9):

```python
class StagedRow(BaseModel):
    id: UUID
    pageNumber: int | None
    rowNumber: int | None
    bookedDate: date
    valueDate: date | None
    descriptionRaw: str
    descriptionClean: str | None
    referenceRaw: str | None
    amount: str
    currency: str
    amountBase: str
    amountSecondary: str
    fxRateDate: date
    fxRateIsCarried: bool
    status: str
    proposedTxnType: str
    categoryId: UUID | None
    duplicateOfTransactionId: UUID | None
    nearDuplicateOfId: UUID | None
    rawRow: dict[str, str]
    userNote: str | None

class ReviewGroups(BaseModel):
    blocked: list[StagedRow]        # empty in M2 — a blocked batch never stages
    transfers: list[StagedRow]      # empty in M2 — transfer matching is M5
    unclassified: list[StagedRow]
    lowConfidence: list[StagedRow]  # empty in M2 — classification is M4
    ready: list[StagedRow]
    duplicates: list[StagedRow]

class BatchReview(BaseModel):
    batchId: UUID
    accountId: UUID
    accountName: str
    periodStart: date | None
    periodEnd: date | None
    status: str
    reconciliation: ReconciliationResult
    groups: ReviewGroups
    totals: BatchTotals             # rowsExtracted/new/duplicate/accepted/rejected, sum of
                                    # accepted amounts as a decimal string, computed in Postgres

class RowDecisions(BaseModel):      # PATCH /import/{id}/rows
    ids: list[UUID]
    status: str | None              # accepted | rejected
    txnType: str | None             # overrides the sign-derived proposed type
    categoryId: UUID | None
    clearCategory: bool = False     # explicit, so "omitted" never means "clear"
```

**Contracts.**
- `commit_import_batch()` and `assert_account_reconciles()` per §4.9, both `SECURITY DEFINER`,
  both calling `assert_is_owner()` first, both `SET search_path = public`, reached only after
  `scope_to_owner(conn, owner.user_id)`.
- Commit inserts **only** `status='accepted'` rows. Nothing is accepted implicitly: staging sets
  `new`/`duplicate`/`near_duplicate`, and only an explicit decision moves a row to `accepted`.
  "Nothing imports without a click" is a property of the data, not of the UI.
- Commit is refused (409) for any `reconciliation_status` other than `matched` — in M2 a staged
  batch can only be `matched` (every other verdict blocks staging), and the check holds anyway.
- Committing an already-completed batch is refused (409), not silently repeated.
- A commit that raises leaves **zero** `transaction` rows and the batch not `completed` — the
  induced-failure gate.
- Every committed row carries `source='statement_import'`, its `source_document_id`,
  `source_page_number`, `source_row_number`, `import_batch_id`, `statement_layout_id`,
  `layout_version` and its `dedupe_hash` (I6), and the `import_has_document` CHECK on
  `transaction` is what makes forgetting impossible.
- `PATCH /import/{id}/rows` applies one decision to a set of ids in one statement — the review
  screen is bulk-first. A category must be available to the household (reuse
  `routes/transactions.py::_assert_category_allowed`; do not write a second predicate).
  `txnType` must be a `txn_type` enum member M2 can produce or the owner can reasonably assign
  (422 otherwise); an omitted `txnType` leaves the sign-derived proposal in place.
- **Accepting a `duplicate` row is the confirmed-distinct override** (§4.7): the endpoint
  recomputes the row's `dedupe_hash` with the occurrence index advanced to the next free index
  among committed collisions, stores it on the staged row, then sets `accepted` — otherwise
  V10's unique `(household_id, account_id, dedupe_hash)` index makes the commit fail. The index
  stays the final guard.
- `GET /import/{id}` computes its totals **in Postgres**, not by summing in Python.

**Invariants / security.** I1/I5 (committed money is immutable — the V10 trigger already enforces
it and the commit must not attempt an update path around it), I6 (provenance), I8 (commit
re-verifies and rolls back), B3.5 (the household lock). `require_owner()` on all three routes.
Household predicate on every statement, including inside the functions.

**Required test behaviours (failing first).**
- *DB (`0012`)*: `commit_import_batch` raises when `app.user_id` is unset (`assert_is_owner`
  first); a batch belonging to another household is refused; committing a `mismatched` batch is
  refused; a clean batch commits and `account.current_balance` equals `opening + Σ(amount)` to
  4 dp; **a valid older statement committed after a newer one succeeds** — the as-of-close
  comparison, not `current_balance`, is what `assert_account_reconciles` reads; **an induced
  failure mid-commit (a declared closing deliberately off by one unit) leaves zero `transaction`
  rows and the batch not completed**; a second commit of a completed batch is refused;
  `assert_account_reconciles` raises on a null declared closing or null period end rather than
  passing vacuously; a **running-balance-only** batch commits with its terminal stated balance
  as the verification target (§4.6/§4.9).
- *API*: an unticked (`new`) row never imports; only `accepted` rows import; commit is 409 while
  blocked; the review payload groups rows most-attention-first with the counts the UI renders;
  bulk accept of 200 ids is one statement; a category not available to the household is 422;
  `clearCategory` clears while an omitted `categoryId` does not; every committed row's
  `source_document_id`, page and row trace back to the staged row it came from; every committed
  row's `txn_type` equals its staged row's final `proposed_txn_type`, and a review override of
  the type is what commits; accepting a `duplicate` row recomputes its hash and both rows commit
  side by side, after which re-parsing a statement carrying both occurrences stages both as
  duplicates (the §4.7 override preserves future overlap detection).

**Validation.** The DB gate (12 tests passed), the API gate, the web gate for regenerated types.

**Acceptance.** The roadmap's "the commit is atomic — an induced failure mid-import leaves zero
rows" gate is proven in `db/tests/0012` and at the API level.

---

### Task 7 — The `/import` screen

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-import-screen`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 5.

**Outcome.** Multi-file drop with independent per-file status, a visible summary before anything
is written, and a Stage batch action that writes.

**Files.**
- Create `web/app/import/page.tsx`, `web/app/import/import-screen.tsx`,
  `web/app/import/__tests__/import-screen.test.tsx`.
- Modify `web/app/workspace.tsx` (the `"import"` section and nav) and
  `web/app/__tests__/workspace.test.tsx`.
- Modify `web/app/globals.css` as the screen needs.

**Mockups.** `docs/product/mockups/OneLedger.dc.html` (statement-import artboard);
`docs/product/ui-information-architecture.html` §2 wins on disagreement.

**Contracts.**
- Multi-file drop and file picker. **Each file is parsed independently** — one bad statement
  blocks only itself (`statement-ingestion.html` §12). Per-file status: pending, parsing,
  reconciled, blocked, duplicate, needs-layout, needs-account, staged, with the file's own message.
- Every file is first parsed with `stage=false`. Nothing is written until the owner presses
  **Stage batch** on that file, which re-posts it with `stage=true`. Blocked files have no Stage
  action at all.
- A file with no matching layout offers the candidate list and a **Map a new layout** action that
  hands off to Task 8's mapper. A file with an unresolved account offers an account picker built
  from `/accounts`.
- Money is rendered with `formatMoney(value, currencyRow)` from the decimal string. **No
  arithmetic, no `Number()`, no `parseFloat` on any money field** — totals come from the API.
- Consume **generated types only** from `@/types/api`; no hand-written response type.
- 401 → `/login` and 403 → `/unauthorized`, using the existing `authDestination` pattern rather
  than a second one.
- Fully keyboard-operable, AA contrast, `aria-live` on per-file status (WCAG AA is a stated PRD §6
  requirement, not a nicety).

**Required test behaviours (failing first).**
- Dropping three files issues three independent parse requests and renders three status rows.
- A blocked file renders its reconciliation message and offers **no** Stage action, while its
  siblings still stage.
- Nothing is posted with `stage=true` until Stage batch is pressed — assert on the request bodies.
- A duplicate file renders the earlier import's date.
- A file needing a layout renders the candidate list and the map action.
- Money renders exactly the string the API sent, formatted by the currency row.
- The empty state teaches the first action.
- Keyboard-only: every control is reachable and operable by Tab/Enter/Space.

**Validation.** The web gate.

**Acceptance.** The roadmap's `/import` requirement — multi-file drop, per-file status, nothing
written until Stage batch — demonstrable against a live stack.

---

### Task 8 — The layout mapping UI

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-layout-mapping-ui`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Task 5 (Task 7 may land before or after).

**Outcome.** An unrecognised statement can be mapped and saved through the UI, with the parse
shown live and the reconciliation line proving the mapping is right before it is saved.

**Files.**
- Create `web/app/import/layout-mapper.tsx`,
  `web/app/import/__tests__/layout-mapper.test.tsx`.
- Modify `web/app/import/import-screen.tsx` (the handoff) and its test.

**Mockup.** `docs/product/mockups/LayoutMapping.dc.html` — the artboard is the contract:
"HEADER ROW FOUND" with the matched header words; "PROPOSED COLUMNS" listing each field with its
`x` range and a drag hint; "PREVIEW · N ROWS" with date, description, amount and balance; and the
reconciliation line beneath it. `ui-information-architecture.html` wins on disagreement.

**Contracts.**
- On **Map a new layout**, the file is posted to `POST /layouts/propose`; the response's draft and
  sample rows populate the mapper.
- Band edges are draggable and each column is relabellable from the field list. **Every edit
  re-posts the file with the edited `draft` field to `POST /layouts/propose`** (Task 3's second
  mode) for a fresh preview, so the parse shown is always the parse that will happen — the
  preview is never computed in TypeScript.
- The reconciliation line is rendered from the draft-mode propose response's verdict (Task 5's
  extended contract), never recomputed in the browser.
  Saving is permitted with a failing verdict (the owner may be mapping a period with no anchors),
  but the line states the verdict plainly; staging still refuses a blocking verdict.
- The owner supplies the profile name, the balance patterns and the quirks; **the UI offers
  exactly the two quirks in the vocabulary** and no free-text quirk field.
- Save posts `POST /layouts`; a name that already exists is presented as "this saves version N+1",
  not as an error.
- No bank name is prefilled, suggested or defaulted anywhere in this UI.

**Required test behaviours (failing first).**
- The proposal renders the header row, one band per proposed column with its x range, and the
  sample rows.
- Dragging a band edge re-posts and re-renders the preview from the **new** response, not a
  locally recomputed one — assert on the request and on the rendered values.
- Relabelling a column changes the field sent on save.
- The reconciliation line renders `matched` and `mismatched` distinctly, and shows the delta and
  the divergent row for a mismatch.
- Saving posts every profile field and, on success, the newly saved layout is selectable for the
  file that triggered the mapping.
- Only the two vocabulary quirks are offered.
- Keyboard-only band adjustment works (arrow keys on a focused edge), because drag alone is not
  an accessible control.

**Validation.** The web gate.

**Acceptance.** Roadmap M2's first two gates: a layout can be mapped through the UI, saved and
reused **with no code change**; auto-proposal identifies the header row and proposes usable bands
without hand-editing.

---

### Task 9 — The review screen, the diagnostics panel, and M2 acceptance

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/m2-review-screen-and-acceptance`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** Tasks 6, 7, 8.

**Outcome.** The staged batch is reviewed and committed from the app, a blocked batch is
diagnosable at a glance, and the whole milestone is proven end to end on synthetic statements.

**Files.**
- Create `web/app/import/review-screen.tsx`, `web/app/import/diagnostics-panel.tsx`,
  `web/app/import/__tests__/review-screen.test.tsx`,
  `web/app/import/__tests__/diagnostics-panel.test.tsx`.
- Create `api/tests/test_import_e2e.py`.
- Modify `web/app/import/import-screen.tsx` (route to review after staging) and its test.
- Modify `docs/superpowers/progress.md` (M2 complete, with the owner-run results).

**Mockups.** `docs/product/mockups/Review.dc.html`;
`docs/product/ui-information-architecture.html` §3 wins on disagreement.

**Contracts.**
- Groups **most-attention-first**, exactly as IA §3: Blocked, Transfers, Unclassified, Low
  confidence, Ready (collapsed, with a count), Duplicates (collapsed, with a count). In M2 the
  Transfers and Low-confidence groups are empty and are **not rendered** rather than shown as
  empty shells; the review screen is built so M4 and M5 fill them without restructuring it.
- The **reconciliation banner sits at the top**: green states the identity with its three numbers;
  red blocks import entirely and opens the diagnostics panel.
- Bulk actions over per-row clicks: select-all-ready, bulk categorize, bulk accept, bulk reject.
  Running totals update from the API's totals, live, so the shape of the month is visible before
  committing. Target: 200 rows reviewed in under 10 minutes.
- **Nothing imports without a click.** No row is pre-accepted in M2.
- Each row shows `description_raw` and its page and row number (the M2 form of traceability; the
  document link is M10).
- **Diagnostics panel** (`statement-ingestion.html` §12): extracted rows beside the computed
  running balance, rendered from the parse response's preview — the divergent row highlighted
  when `firstDivergentRow` exists, and otherwise the delta stated with any `candidateRows`
  called out and a plain line that row-level evidence is unavailable (§4.6). It is the first
  thing to open when a layout changes.
- Fully keyboard-operable via Tab, Enter and Space. **No vim-style bindings** — IA §3 is explicit
  that keyboard operability here is an accessibility requirement, not a power-user feature.

**Required test behaviours (failing first).**
- *Web*: groups render most-attention-first with correct counts, Ready and Duplicates collapsed;
  a blocked batch renders the red banner, opens diagnostics and offers no Import action; bulk
  accept sends one PATCH with every selected id; an unticked row is never sent as accepted;
  running totals render exactly the API's strings; the diagnostics panel highlights the divergent
  row; every control is reachable by keyboard.
- *API end-to-end (`test_import_e2e.py`)*, on a synthetic statement, offline: propose a layout →
  save it → parse dry-run → stage → review → accept → commit → assert the committed
  `transaction` rows match the statement's dates and amounts exactly, that
  `account.current_balance` equals the declared closing, and that every row traces to document,
  page and row. Then re-run the same file and assert zero new rows with the earlier import named.
  **This test fails if the engine or a saved profile regresses** — it is the golden-fixture role,
  played by a generated fixture because no PDF may be committed.

**Validation.** The web gate, the API gate, the DB gate — the full local hook suite, which is
the enforced gate (§3.1); a manual CI dispatch is optional corroboration.

**Owner-run acceptance** (recorded in `docs/superpowers/progress.md`, not automated — §3.1):
all 38 real card statements import end to end with reconciliation passing on each; a re-import
imports zero rows and names the earlier import; an overlapping statement imports only new rows;
a backup restored into a scratch database.

**Acceptance.** Every item in §9 checked, with CI-run items evidenced by a green run and
owner-run items evidenced by dated entries in the progress file.

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
