# Implementation progress

This is the single maintained implementation-status file. The orchestrator updates it while
executing Superpowers plans. Plans define the work; the roadmap defines scope and sequence;
dated reports retain verification evidence. Do not copy this status into other documentation.

Updated: 7 September 2026.

## Current phase

**Phase 1 — trusted financial position.**
M1a (the Phase 1 plan's Tasks 1–11) is on `develop` by owner request; the former phase branch
is deleted. `develop` is the standing integration branch, merging on Codex's thumbs-up
([docs/git-workflow.md](../git-workflow.md)). From M2 onward, by owner request, a milestone is
delivered as several task-shaped branches and PRs — not one PR per milestone — each carrying
its slice of plan and implementation through the same review cycle. Any path to `main` still
needs a separate owner request.

**M1b — manual transactions and taxonomy — is complete.**
Merged to `develop` as [PR #31](https://github.com/sandjaie/OneLedger/pull/31)
(merge commit `2e2f35f`) by the owner after thirty Codex review rounds; the branch is deleted.
Plan: [M1b manual transactions](plans/2026-09-06-m1b-manual-transactions.md), on the same
branch. Delivered there: V9 (category taxonomy, merchants, two-level trigger, seeded tree),
V10 (the transaction ledger, immutability and split-sum triggers, the posting and reportable
views, balance maintenance), the manual transaction API freezing both reporting amounts under
the B3.5 household lock with the live interleaving proof, the categories API, and the
transactions screen with Settings category management. Recorded deviations: the categories API
(the plan named no such surface), `db/tests/0008`/`0009` numbering (the test sequence is
independent of `schema.md`), and V10 gaining the account-balance trigger before publication.

| M1b task | Status | Evidence |
| --- | --- | --- |
| 12 — categories, merchants, seed taxonomy (V9) | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `db/tests/0008_categories.sql` |
| 13 — transaction ledger and reporting views (V10) | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `db/tests/0009_transactions.sql` |
| 14 — manual transaction API, B3.5 lock | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `api/tests/test_transactions*.py` |
| 15 — transactions screen, category management | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); component tests |

Phase 1 remains active: M1a and M1b are merged; roadmap M1 is delivered. **M2 — the statement
importer — is open, the current delivery**, shipped as nine task-sized PRs per
[the M2 plan](plans/2026-09-07-m2-statement-importer.md). Its entry criterion (the FX backfill
reaching the 01 Jan 2024 anchor, ADR-0011) is satisfied — verified 2026-09-07 against the live
compose database: `fx_rate` spans `2024-01-01` to `2026-09-06`, 1,960 rows across 2 pairs = 980
days per pair, complete daily coverage with no gaps. Phase 0's live Google identity gates remain
open below — synthetic checks do not substitute for them.

| M2 task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 1 — CI, backup, and the plan | `chore/m2-ci-and-backup` | Complete | [PR #32](https://github.com/sandjaie/OneLedger/pull/32), merged as `37d52cc` after five Codex review rounds |
| 2 — documents, import batches and layout profiles (V12) | `feature/m2-documents-and-layouts` | Complete | `db/tests/0010_documents_and_layouts.sql`; `api/tests/test_layouts.py` |
| 3 — extraction engine, quirks, account identification | `feature/m2-extraction-engine` | Complete | [PR #34](https://github.com/sandjaie/OneLedger/pull/34), merged as `30e6783` after eight Codex review rounds; branch deleted |
| 4a — staged rows and normalization (V13) | `feature/m2-staged-rows-and-normalization` | In review | [PR #35](https://github.com/sandjaie/OneLedger/pull/35); `db/tests/0011_staged_transactions.sql`; `api/tests/test_ingest_normalize.py` |
| 4b — dedupe and reconciliation | `feature/m2-dedupe-and-reconcile` | Not started | — |
| 5 — FX fetch-on-missing, storage, `POST /parse` | `feature/m2-parse-and-stage` | Not started | — |
| 6 — review payload, row decisions, atomic commit (V14) | `feature/m2-review-and-commit-api` | Not started | — |
| 7 — `/import` screen | `feature/m2-import-screen` | Not started | — |
| 8 — layout mapping UI | `feature/m2-layout-mapping-ui` | Not started | — |
| 9 — review screen, diagnostics, end-to-end gate | `feature/m2-review-screen-and-acceptance` | Not started | — |

Task 2 delivers V12 (`document`, `import_batch`, `statement_layout`, RLS on all three, and the
four foreign keys V12 completes on `transaction` per schema.md's per-target `0018` rule) and the
layouts API (`GET /layouts`, `POST /layouts`) behind `require_owner`, with every 422 validation
rule Task 2's contract names (band ordering, sign-convention/column agreement, the quirks
vocabulary, regex compilation, reserved metadata capture groups, the two-digit-year period-
capture requirement, and the institution predicate). No parsing exists yet — that is Task 3.

Task 3 is merged. [PR #34](https://github.com/sandjaie/OneLedger/pull/34) landed on `develop` as
`30e6783` after eight GitHub Codex review rounds fixing 17 privacy, proposal, header, grammar and
validation findings; the branch is deleted. The seven-round closeout gates passed on
2026-09-08: API `266 passed, 2 warnings` against the live Compose database with no skips;
web `97 passed` across 8 Vitest files with lint/typecheck clean; generated API types were unchanged;
and the documentation/whitespace checks passed. The eighth round was fixed in an environment with
no reachable database, so its API run is `261 passed, 10 skipped` — the ten skips are the
database-backed account and transaction tests, which no eighth-round change touches; the web gate,
the API type drift check, lint, strict mypy and the documentation checks all passed there. Original
RED evidence for the recovered first three implementation steps is unavailable and is not claimed.

Task 3 review clarifications: ambiguous numeric day/month dates are refused for both slash and
dash separators. Since the profile contract has no line-tolerance field, the PDF boundary infers
tolerance from modal character height for both automatic proposal and saved-profile extraction;
the fixed default is only a fallback when no reliable mode exists. Turning continuation off with
`has_date` leaves the transaction count unchanged and instead drops description fragments while
increasing `lines_unparsed`; this corrects the plan test's wording, not the binding row predicate.
Automatic proposals may be incomplete unsaved drafts (including two-digit dates before period
mapping); submitted drafts and saved profiles still enforce period captures. Raw diagnostics
retain original PDF characters, including whitespace and filtered script text. Public previews
redact identifiers in both modes, and account validation responses omit sensitive request input.

**Task 4 is split into two deliveries**, each its own branch and PR: 4a
(`feature/m2-staged-rows-and-normalization`) carries V13, `db/tests/0011`, the schema and
data-model corrections, `schemas/staging.py` and `ingest/normalize.py`; 4b
(`feature/m2-dedupe-and-reconcile`) carries `ingest/dedupe.py` and `ingest/reconcile.py`. The
plan's branch table names one branch for Task 4; the split is the recorded deviation, taken
because reconciliation and dedupe are the subtlest logic in M2 and PR #34 showed how slowly a
large surface converges. 4b depends on 4a's normalized row and on nothing else.

Task 4a deviations, recorded rather than worked around. The plan's date test pairs `dd/MM/yyyy`
with `MM/dd/yyyy`; Task 3's save-time validation has since restricted `dateFormat` to the
engine's supported set, so month-first cannot be declared at all and the no-inference rule is
proved in that stronger form. `original_amount` and `original_currency` join the mappable column
vocabulary, without which the currency contract's foreign amount could never be captured.
`api/tests/test_schemas_staging.py` is an added test file the plan does not list, because
`schemas/staging.py` is a new module. A statement whose dates never parse is refused whole rather
than row by row: without one parsed date there is no period to place the others in. And a Dr/Cr
suffix appearing under a convention that does not read one fails its row, because dropping it
silently is exactly the invisible sign error Invariant I2 exists to prevent.

Two review findings about the statement period were answered differently from the way they were
raised. A `dateFormat` carrying a two-digit year with no period capture now refuses the whole
statement: `LayoutCreate` already refuses to save that shape, but normalization is a pure
function over a profile-shaped value and does not lean on a route-layer validator, and a derived
period is made of the very dates it would have to correct — Python reads `99` as 1999 and the
derived period then agrees, so a century-early preview looks internally consistent. The second
finding asked that a captured period be read only in the profile's declared `dateFormat`; that
is **partially declined**, because a statement's header commonly writes its period differently
from its transaction lines and the rule would reject those layouts outright. Both bounds must
instead parse under one shared four-digit-year format, which blocks the mis-captured fragment
pairing with a genuine date — the risk the finding names — without the product cost.

**Owner decision, 2026-09-08 — statement text is stored as the statement wrote it.** Codex's
fifth review round found that a full account or card number appearing inside a transaction
description is kept verbatim in `description_raw` and `raw_row`, which Task 5 will insert into
`staged_transaction`. The owner ruled that this is correct for this product: the number is the
owner's own data, in a single-user system, and the ledger is where they read it, so it is stored
and shown rather than masked. Normalization therefore applies no identifier mask, and Task 5's
insert does not need one for these two fields.

This decision **contradicts `security-and-privacy.html` §1**, which introduces the never-store
list with "Absolute. No feature justifies an exception, and no future integration changes it"
and lists full card and bank account numbers. Nothing in the tree or the documents reconciles
that yet, and the repository's own rule is that an invariant which is genuinely wrong changes by
a superseding ADR rather than by an edit or by code quietly diverging. **An ADR is owed before
the documents and the code agree**, and until it exists this paragraph is the only record.

The ruling is read narrowly, pending that ADR. It covers identifiers the *statement itself*
prints in a transaction line. It does not touch: the account number the owner enters for an
account, which Task 3 stores as a mask plus a salted hash and never whole; the §5.6 rule keeping
document-derived digit sequences out of log lines, error messages and responses, which Task 4a
implements; the layout preview's redaction, delivered and reviewed in Task 3; or credentials —
CVV, PIN, OTP and passwords — which no part of this decision admits into storage.

Task 4a gate evidence, 2026-09-08, in an environment with no Docker and therefore no Flyway: the
migrations were applied from empty in version order with `psql` against a scratch PostgreSQL 16
cluster — 13 migrations, then the seeds, then `db/tests/*.sql`, 11 passed. Flyway's own checksum
and versioning path was not exercised and is not claimed. API `358 passed, 2 skipped` against
that live database, the two skips being the Phase 0 live-identity gates that remain open; Ruff,
Ruff format and strict mypy clean over 40 files; web `97 passed` across 8 Vitest files with
typecheck clean and generated API types unchanged; documentation and whitespace checks passed.
`gitleaks` is not installed in this environment and was not run.

Header recognition claims each word once, across all fields at once, longest label first: a
directional label such as `Withdrawal Amount` is one debit column, never a debit column beside an
amount column. The plan's synonym table is extended with those directional compounds (`withdrawal
amount`, `debit amount`, `deposit amount`, `credit amount`, their `amt` spellings, and
`transaction amount`) so the compound outranks the generic word it contains; the plan is not
edited after dispatch. An automatic proposal that would still hold an amount column beside debit
and credit is refused, because no saved layout may hold all three. Identifier screening treats any
non-alphanumeric run as a grouping separator in previews, captures and fingerprints, so a
non-breaking hyphen or an Arabic thousands separator groups an identifier exactly as a plain
hyphen does.

The preview privacy boundary preserves a validated date or monetary value only in its known
typed field. Raw diagnostics and description/reference text redact every identifier-length digit
run, including separator variants, even when it resembles a date or amount. A deliberately
captured account identifier overrides all field protections. This reduces detail in raw previews
and incorrectly mapped fields; header/band semantics are the accepted boundary for auto-proposed
financial cells. Internal extraction still retains the verbatim source text for transient parsing.

Review-fix verification on 2026-09-07: the covering account/layout/engine/word/fixture run passed
`135` tests against local PostgreSQL, with no skips and the same two upstream warnings. Targeted
Ruff, formatting and strict mypy passed over 13 files. Regenerated API types passed the drift
check and web typecheck; docs checked 125 documents and `git diff --check` passed. Full API/web
suites are reserved for the fix re-review boundary; no new full-suite result is claimed here.

| Phase 1 task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 1 — restore API package import | `fix/task-1-api-import-syntax` | Complete | [PR #23](https://github.com/sandjaie/OneLedger/pull/23) |
| 2 — currency-agnostic SQL fixtures | `fix/task-2-db-tests-currency-agnostic` | Complete | [PR #25](https://github.com/sandjaie/OneLedger/pull/25) |
| 3 — V6 opening-balance current_balance | `fix/task-3-account-opening-balance` | Complete | [PR #26](https://github.com/sandjaie/OneLedger/pull/26) (with Task 4) |
| 4 — distinct DB vs auth failures | `fix/task-3-account-opening-balance` | Complete | [PR #26](https://github.com/sandjaie/OneLedger/pull/26) (with Task 3) |
| 5 — Compose DB identity | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 6–8) |
| 6 — household configuration API | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 5, 7, 8) |
| 7 — Settings › Household UI | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 5, 6, 8) |
| 8 — account inventory schema | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 5–7) |
| 9 — account inventory API | `feature/task-9-11-account-inventory` | Complete | [PR #29](https://github.com/sandjaie/OneLedger/pull/29); follow-up `1d7aab5` |
| 10 — account inventory UI | `feature/task-9-11-account-inventory` | Complete | [PR #29](https://github.com/sandjaie/OneLedger/pull/29); follow-up `1d7aab5` |
| 11 — progress evidence and M1a/M1b split | `feature/task-9-11-account-inventory` | Complete | [PR #29](https://github.com/sandjaie/OneLedger/pull/29) |

Roadmap M1 is delivered as **M1a** (Tasks 8–10 in this plan) and **M1b** (its own dated plan).
Roadmap M1's *Done when* list is complete only when both are.

## Phase 0 (prior)

**Phase 0: implementation delivered to `main`; live Google identity verification still pending.**
`feature/phase-0-foundations` merged to `main` as [PR #20](https://github.com/sandjaie/OneLedger/pull/20).

| Completion-plan task | Status | Evidence |
| --- | --- | --- |
| 1 — web scaffold and API client | Complete | PR #8; timeout follow-up #14 |
| 2 — Google OIDC and revocable sessions | Implementation complete | PRs #9–#11 and #16; live identity gate below |
| 3 — exact money, configured shell and FX inspector | Complete | PRs #12, #13 and #15 |
| 4 — phase exit verification | Partially complete | Automated/synthetic checks and review passed; live identity gate pending |

## Delivered work

| GitHub PR | Completed scope | Original task numbers | Original PR ordinal |
|---|---|---|---|
| #3 | Compose definition, environment template, secret hygiene | 1 | 1 |
| #4 | V1–V2 reference data, identity, RLS | 2–3 | 2 |
| #5 | V3–V4 accounts and FX rates | 4–5 | 3 |
| #6 | FastAPI, `require_owner`, `resolve_owner`, `connect` | 6 | 4 |
| #7 | FX source, peg derivation, carry-forward, backfill; three review rounds reported | 7–8 | 5 |
| #8 | Reviewed Next.js foundation and cookie-aware typed API client | 9 | 6 |
| #9 | Revocable owner-session persistence and strict session verification | 10 partial | 7 partial |
| #10 | Protected logout, CSRF origin checks and household configuration | 10 partial | 7 partial |
| #11 | Google OIDC, state/nonce/PKCE and pinned subject binding | 10 | 7 partial |
| #12 | Exact money formatting and generated API types with drift enforcement | 11 partial | 7 partial |
| #14 | Bounded API requests and compatible caller cancellation | 9 follow-up | 6 follow-up |
| #13 | Authenticated shell, configured currency preference and recovery | 11 partial | 7 partial |
| #15 | Protected Settings FX inspector, exact rates and provenance | 11 | 7 partial |
| #16 | Fixed account chooser on sign-in retry | 10 follow-up | 7 follow-up |
| #17 | Explicit dual-compiler verification and current API boundary comments | Final review follow-up | Closeout |
| #18 | Sequential current-checkout workflow and generic PR template | Workflow | Documentation |
| #21 | Phase 1 plan, spec and git workflow | Phase 1 planning | Documentation |

PR #14 merged before #13 as a prerequisite. Original plan PR ordinals are not GitHub PR numbers.
The original Phase 0 Tasks 1–11 are implemented. The whole-phase review found no blocking defects;
both minor follow-ups were addressed in #17 and passed scoped and Cloud re-review.

The Phase 0 "59 tests" API evidence predates the import SyntaxError fixed by Phase 1 Task 1.

## Verified gates

- [x] API: 59 tests, lint and strict type checks.
- [x] Web: 33 tests, both TypeScript compilers, lint and generated API contract drift.
- [x] Dependencies: strict installation and zero reported npm audit vulnerabilities.
- [x] Database: all five migrations, reference seeds and five SQL suites from empty; RLS negative-control proof.
- [x] Public FX reference history: 980 days, 1,960 rows, complete peg pairs and zero invariant violations.
- [x] Production images, cached offline startup and synthetic-session browser checks, including exact
  FX rates/provenance, keyboard/mobile use, currency persistence and logout/replay behavior.
- [x] Local and current-head Cloud review of each implementation delivery; final whole-phase review.

Evidence: [dated verification and dependency report](reports/2026-09-06-phase-0-verification.md).
These results describe the verified integration, not a new test run whenever this file is edited.

## Remaining Phase 0 exit gates

- [ ] Privately configure the Google client credentials and real permitted email, matching the seeded owner.
- [ ] Verify real permitted-account sign-in and protected application access.
- [ ] Verify rejection of a real second Google account and successful account-choice recovery.
- [ ] Close Phase 0 only after those live checks are recorded here.

The last configuration check found missing Google client credentials and a placeholder permitted
email. The owner was asked to configure ignored `.env` privately; no credential values belong here.
Synthetic protocol/session/browser tests do not substitute for live Google identity checks.
Integration-to-main requires a separate owner request.

## Later phases

| Phase | Status |
| --- | --- |
| Phase 1 — trusted financial position | Active (this file) |
| Phase 2A — next month | Not started |
| Phase 2B — resilience | Not started |
| Phase 2C — deeper analysis | Not started |
| V2 | Not started |

Scope and sequencing remain in the [master plan](plans/implementation-plan.md) and
[product roadmap](../product/roadmap.html). Loan work remains conditional on the product decision;
this file tracks implementation, not scope decisions.
