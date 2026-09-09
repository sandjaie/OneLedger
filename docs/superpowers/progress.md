# Implementation progress

The single maintained implementation-status file: what is done, what is next, and where the
evidence for each sits. **It is tables.** The reasoning does not live here — a decision taken
while building a task belongs to the milestone's spec, dated run evidence to `reports/`, and
scope and sequence to the roadmap and the plans. Keeping this file to status is what lets it be
read at a glance and updated at a task boundary without an argument about what to write.

Updated: 9 September 2026.

## Where the work stands

Delivery follows the [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) — six
phases, one term; the plan maps the old phase/milestone names. Sections further down record
delivered work under its old numbering and are not renamed.

| Phase | Status | Plan |
| --- | --- | --- |
| 0 — foundations and manual ledger (old Phase 0, M1a, M1b, M2 tasks 1–5c) | Delivered; live Google identity gates still open | [phase plan](plans/2026-09-09-phase-delivery-plan.md) |
| 1 — the complete application | **Active** — the current delivery | [phase plan](plans/2026-09-09-phase-delivery-plan.md) |
| 2 — trust and depth | Not started | planned when it starts |
| 3 — planning and resilience | Not started | planned when it starts |
| 4 — analysis | Not started | planned when it starts |
| 5 — intelligence | Not started | planned when it starts |

Each phase ships as branches and PRs into `develop` under the cycle plan → implement → local
review → merge ([git-workflow](../git-workflow.md)); any path to `main` needs a separate owner
request.

## Phase 1 — the complete application

| Slice | Branches | Status | Evidence |
| --- | --- | --- | --- |
| 1.1 — statements land in the ledger | `fix/p1-1-card-layout-fitness` · `feature/p1-1a-parse-pipeline` · `feature/p1-1a-parse-route` · `feature/p1-1b-commit-functions` · `feature/p1-1b-review-routes` · `feature/p1-1c-import-screen` · `feature/p1-1b-duplicate-override` · `feature/p1-1d-review-screen` | In progress — branch 8 of 8 | slice 1.1 branch table below |
| 1.2 — all four bank layouts | `feature/p1-2a-engine-hardening` · `feature/p1-2b-remaining-layouts` | Not started | — |
| 1.3 — classification and transfers | `feature/p1-3a-classification` · `feature/p1-3b-transfers` | Not started | — |
| 1.4 — cards, net worth and trends | `feature/p1-4a-cards` · `feature/p1-4b-dashboard` | Not started | — |
| 1.5 — investments | `feature/p1-5a-investment-schema-api` · `feature/p1-5b-nav-and-imports` · `feature/p1-5c-portfolio-screen` | Not started | — |
| 1.6 — salary and a thin forecast | `feature/p1-6a-payslips` · `feature/p1-6b-commitments-forecast` | Not started | — |
| 1.7 — the layout mapper and documents | `feature/p1-7a-layout-mapper` · `feature/p1-7b-documents-diagnostics` | Not started | — |

## Phase 1 · slice 1.1 — statements land in the ledger

`/oneledger-plan-review` ran on the phase delivery plan on 9 September 2026, before the first
dispatch, and split the slice into five branches; the owner took the two decisions it raised.
Branch 1a then split again in delivery — written as one branch it passed the diff budget's hard
stop, and 1b split on the same forecast — so the slice runs to seven. The decisions themselves
are contract and live in the [M2 spec §8](specs/2026-09-07-m2-statement-importer.md).

| Branch | Delivers | Status | Evidence |
| --- | --- | --- | --- |
| `fix/p1-1-card-layout-fitness` | `inverts_sign` reframes whatever stated no direction, so a card's bare anchors reach the ledger's frame; the card profile as data | Merged | [#51](https://github.com/sandjaie/OneLedger/pull/51) |
| `feature/p1-1a-parse-pipeline` | `ingest/pipeline.py`: identification, the staging transaction, the second-staging refusal | Merged | [#52](https://github.com/sandjaie/OneLedger/pull/52) |
| `feature/p1-1a-parse-route` | `POST /parse` and its wire contract | Merged | [#53](https://github.com/sandjaie/OneLedger/pull/53) |
| `feature/p1-1b-commit-functions` | V14's `commit_import_batch` and `assert_account_reconciles`, and `db/tests/0012` | Merged | [#54](https://github.com/sandjaie/OneLedger/pull/54) |
| `fix/security-definer-search-path` | V15: every `SECURITY DEFINER` function names `pg_temp` last, so `current_household_id()` cannot be answered by a caller's temporary table; `db/tests/0013` | Merged | [#55](https://github.com/sandjaie/OneLedger/pull/55) |
| `feature/p1-1b-review-routes` | `GET /import/{batch}`, `PATCH /import/{batch}/rows`, `POST /import/{batch}/commit` | Implemented; reviewed | `api/tests/test_import_review_live.py` (22 cases) |
| `feature/p1-1c-import-screen` | `/import`: picker, per-file status, parse summary, diagnostics, stage | Implemented; reviewed | `web/app/import/__tests__/import-screen.test.tsx` (14 cases) |
| `feature/p1-1b-duplicate-override` | Accepting a row marked `duplicate` as a confirmed-distinct override: the dedupe hash is recomputed with the occurrence index advanced (M2 spec §4.7). Added in delivery — the slice covers M2 6a–6d and 6c was not in the review-routes branch | Implemented; reviewed | `api/tests/test_import_review_live.py` (32 cases) |
| `feature/p1-1d-review-screen` | grouped review, row decisions, commit | Implemented; reviewed | `web/app/import/__tests__/review-screen.test.tsx` (14 cases) |

## Raised and not yet actioned

| What | Why it is not folded into the branch that found it |
| --- | --- |
| `.claude/skills/oneledger-plan-review/SKILL.md` states the pre-#50 diff budget (400/800) rather than the authoritative 800/1,600 in `git-workflow.md`. | Tooling, not a slice. A `chore/` branch. |
| `import_batch` records a period but not whether it was derived, so `GET /import/{batch}` reports `isDerived: null` rather than guessing (Invariant I7). | Recording it is a column, and a schema change does not belong in a route branch. Its own migration, with `schema.md` and `data-model.html` in the same commit. |
| `CLAUDE.md` says a static check fails the build on a currency, institution or country name used as an identifier, and the M2 plan's Task 8a expects one covering `parseFloat`/`Number()` on money in `web/`. **No such check exists anywhere in the tree.** | It spans every file, not this branch's. A `chore/` branch, with the hygiene sweep at the phase boundary. |

## Where the rest is written down

| What | Where |
| --- | --- |
| Decisions and deviations taken while building M2 | [M2 spec §8](specs/2026-09-07-m2-statement-importer.md) |
| Dated gate and test-run evidence for M2 | [M2 delivery evidence](reports/2026-09-08-m2-delivery-evidence.md) |
| Dated Phase 0 verification and dependency evidence | [Phase 0 verification](reports/2026-09-06-phase-0-verification.md) |
| Slice 1.1 state, decisions and layout field notes | [slice 1.1 handoff](reports/2026-09-09-slice-1.1-handoff.md) |
| Scope, sequence and acceptance criteria | [product roadmap](../product/roadmap.html) |
| Task blocks, branches and forecasts | the milestone's plan in `plans/` |

## Phase 1 · M2 — the statement importer

Entry criterion satisfied: the FX backfill reaches the 01 Jan 2024 anchor (ADR-0011), verified
2026-09-07 against the live compose database — `fx_rate` spans `2024-01-01` to `2026-09-06`,
1,960 rows across 2 pairs, complete daily coverage.

| M2 task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 1 — CI, backup, and the plan | `chore/m2-ci-and-backup` | Complete | [PR #32](https://github.com/sandjaie/OneLedger/pull/32), merged as `37d52cc` after five Codex review rounds |
| 2 — documents, import batches and layout profiles (V12) | `feature/m2-documents-and-layouts` | Complete | `db/tests/0010_documents_and_layouts.sql`; `api/tests/test_layouts.py` |
| 3 — extraction engine, quirks, account identification | `feature/m2-extraction-engine` | Complete | [PR #34](https://github.com/sandjaie/OneLedger/pull/34), merged as `30e6783` after eight Codex review rounds; branch deleted |
| 4a — staged rows and normalization (V13) | `feature/m2-staged-rows-and-normalization` | Complete | [PR #35](https://github.com/sandjaie/OneLedger/pull/35), merged; `db/tests/0011_staged_transactions.sql`; `api/tests/test_ingest_normalize.py` |
| 4b — dedupe: fingerprint, occurrence index | `feature/m2-t4b-dedupe` | Complete | [PR #38](https://github.com/sandjaie/OneLedger/pull/38), merged after one Codex round; `api/tests/test_ingest_dedupe.py` (37 tests); branch deleted |
| 4c — reconciliation: the four verdicts | `feature/m2-t4c-reconcile` | Complete | [PR #42](https://github.com/sandjaie/OneLedger/pull/42), merged after one Codex round and five local review rounds; `api/tests/test_ingest_reconcile.py` (36 tests); branch deleted |
| 4d — a balance states its own direction | `fix/m2-balance-direction` | Complete | [PR #45](https://github.com/sandjaie/OneLedger/pull/45), merged after one Codex round and five local rounds; split out of 4c on the hard stop; branch deleted |
| 5a — money helpers extracted to `money.py` | `refactor/m2-t5a-money-helpers` | Complete | [PR #47](https://github.com/sandjaie/OneLedger/pull/47), merged as `28ff07d`; branch deleted |
| 5b — FX fetch-on-missing | `feature/m2-t5b-fx-fetch-on-missing` | Complete | [PR #48](https://github.com/sandjaie/OneLedger/pull/48), merged as `1f1aa18`; `api/tests/test_fx_range.py`; branch deleted |
| 5c — document storage | `feature/m2-t5c-document-storage` | Complete | [PR #49](https://github.com/sandjaie/OneLedger/pull/49), merged as `953faf6`; branch deleted |

Tasks 5d–9c were re-planned before dispatch into Phase 1 slices 1.1–1.7 of the
[phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) (9 September 2026); their scope
ships there, none of it dropped.

## Phase 1 · M1b — manual transactions and taxonomy

| M1b task | Status | Evidence |
| --- | --- | --- |
| 12 — categories, merchants, seed taxonomy (V9) | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `db/tests/0008_categories.sql` |
| 13 — transaction ledger and reporting views (V10) | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `db/tests/0009_transactions.sql` |
| 14 — manual transaction API, B3.5 lock | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `api/tests/test_transactions*.py` |
| 15 — transactions screen, category management | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); component tests |

## Phase 1 · M1a — trusted financial position

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

## Phase 0 — foundations

Implementation is delivered to `main` as [PR #20](https://github.com/sandjaie/OneLedger/pull/20).
Live Google identity verification is the one thing still open.

| Completion-plan task | Status | Evidence |
| --- | --- | --- |
| 1 — web scaffold and API client | Complete | PR #8; timeout follow-up #14 |
| 2 — Google OIDC and revocable sessions | Implementation complete | PRs #9–#11 and #16; live identity gate below |
| 3 — exact money, configured shell and FX inspector | Complete | PRs #12, #13 and #15 |
| 4 — phase exit verification | Partially complete | Automated/synthetic checks and review passed; live identity gate pending |

### Delivered work

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

PR #14 merged before #13 as a prerequisite; original plan PR ordinals are not GitHub PR numbers.
The Phase 0 "59 tests" API evidence predates the import `SyntaxError` fixed by Phase 1 Task 1.

### Verified gates

| Gate | Verified |
| --- | --- |
| API — 59 tests, lint, strict type checks | Yes |
| Web — 33 tests, both TypeScript compilers, lint, generated API contract drift | Yes |
| Dependencies — strict installation, zero reported npm audit vulnerabilities | Yes |
| Database — five migrations, reference seeds and five SQL suites from empty; RLS negative control | Yes |
| Public FX reference history — 980 days, 1,960 rows, complete peg pairs, zero invariant violations | Yes |
| Production images, cached offline startup, synthetic-session browser checks | Yes |
| Local and current-head Cloud review of each delivery; final whole-phase review | Yes |

These describe the verified integration, not a new run whenever this file is edited.

### Remaining Phase 0 exit gates

| Gate | Status |
| --- | --- |
| Configure the Google client credentials and real permitted email privately, matching the seeded owner | Open |
| Verify real permitted-account sign-in and protected application access | Open |
| Verify rejection of a real second Google account, and account-choice recovery | Open |
| Record those live checks here, then close Phase 0 | Open |

The last configuration check found missing Google client credentials and a placeholder permitted
email; no credential value belongs in this repository. Synthetic protocol, session and browser
tests do not substitute for a live Google identity check.

## Later phases

| Phase | Status |
| --- | --- |
| Phase 1 — trusted financial position | Active (this file) |
| Phase 2A — next month | Not started |
| Phase 2B — resilience | Not started |
| Phase 2C — deeper analysis | Not started |
| V2 | Not started |

Scope and sequencing remain in the [master plan](plans/implementation-plan.md).
