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
| 1.1 — statements land in the ledger | `fix/p1-1-card-layout-fitness` · `feature/p1-1a-parse-pipeline` · `feature/p1-1a-parse-route` · `feature/p1-1b-commit-functions` · `feature/p1-1b-review-routes` · `feature/p1-1c-import-screen` · `feature/p1-1b-duplicate-override` · `feature/p1-1d-review-screen` | **Accepted** — the run was made on 9 September 2026; all 38 card statements were handled unaided | [acceptance run](#phase-1--slice-11--the-acceptance-run) |
| 1.2 — all four bank layouts | `feature/p1-2a-encrypted-statements` · `feature/p1-2b-batch-cancellation` · `feature/p1-2c1-layout-two` · `feature/p1-2c2-layout-three` · `feature/p1-2c3-layout-four` | **Accepted** — the run was made on 10 September 2026; 67 of the 71 gathered files imported and every account reconciles across its period | [acceptance run](#phase-1--slice-12--the-acceptance-run) |
| 1.3 — classification and transfers | `feature/p1-3a-classification` · `feature/p1-3b-transfers` | Not started | — |
| 1.4 — cards, net worth and trends | `feature/p1-4a-cards` · `feature/p1-4b-dashboard` | Not started | — |
| 1.5 — investments | `feature/p1-5a-investment-schema-api` · `feature/p1-5b-nav-and-imports` · `feature/p1-5c-portfolio-screen` | Not started | — |
| 1.6 — salary and a thin forecast | `feature/p1-6a-payslips` · `feature/p1-6b-commitments-forecast` | Not started | — |
| 1.7 — the layout mapper and documents | `feature/p1-7a-layout-mapper` · `feature/p1-7b-documents-diagnostics` | Not started | — |

## Phase 1 · slice 1.2 — all four bank layouts

| Task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 2a — a password reaches the parse | `feature/p1-2a-encrypted-statements` | Delivered | [#71](https://github.com/sandjaie/OneLedger/pull/71) |
| 2b — a staged batch can be discarded | `feature/p1-2b-batch-cancellation` | Delivered | [#72](https://github.com/sandjaie/OneLedger/pull/72) |
| 2c1–2c3 — the three remaining layouts | `feature/p1-2c1-layout-two` · `feature/p1-2c2-layout-three` · `feature/p1-2c3-layout-four` | Not started — local tasks | [slice 1.2 plan](plans/2026-09-10-slice-1-2-all-four-layouts.md) |

The slice's survey found three of its four stated engine capabilities already delivered with M2
and exercised by slice 1.1; the plan records that and the five-branch split that followed. What
remained was the browser having no way to supply a password, and a staged batch having no way to
be abandoned. Both are merged.

`2c1`–`2c3` cannot run in a remote checkout: they need the statements, and `pdf-statements/` is
git-ignored and exists on the owner's machine only. Each carries that bank's profile, a redacted
fixture and a golden-output test; the last also carries the 71-file acceptance run.

## Phase 1 · slice 1.1 — statements land in the ledger

`/oneledger-plan-review` ran on the phase delivery plan on 9 September 2026, before the first
dispatch, and split the slice into five branches; the owner took the two decisions it raised.
Delivery split it three times more: branch 1a passed the diff budget's hard stop written as one
branch, 1b split on the same forecast, and the confirmed-distinct override (M2 6c, which the
slice covers) was found missing while building the review screen. With the `pg_temp` repair the
review gate raised on #54 — taken as its own fix branch because every remaining branch builds on
that boundary — the slice ran to nine. The decisions themselves are contract and live in the
[M2 spec §8](specs/2026-09-07-m2-statement-importer.md).

| Branch | Delivers | Status | Evidence |
| --- | --- | --- | --- |
| `fix/p1-1-card-layout-fitness` | `inverts_sign` reframes whatever stated no direction, so a card's bare anchors reach the ledger's frame; the card profile as data | Merged | [#51](https://github.com/sandjaie/OneLedger/pull/51) |
| `feature/p1-1a-parse-pipeline` | `ingest/pipeline.py`: identification, the staging transaction, the second-staging refusal | Merged | [#52](https://github.com/sandjaie/OneLedger/pull/52) |
| `feature/p1-1a-parse-route` | `POST /parse` and its wire contract | Merged | [#53](https://github.com/sandjaie/OneLedger/pull/53) |
| `feature/p1-1b-commit-functions` | V14's `commit_import_batch` and `assert_account_reconciles`, and `db/tests/0012` | Merged | [#54](https://github.com/sandjaie/OneLedger/pull/54) |
| `fix/security-definer-search-path` | V15: every `SECURITY DEFINER` function names `pg_temp` last, so `current_household_id()` cannot be answered by a caller's temporary table; `db/tests/0013` | Merged | [#55](https://github.com/sandjaie/OneLedger/pull/55) |
| `feature/p1-1b-review-routes` | `GET /import/{batch}`, `PATCH /import/{batch}/rows`, `POST /import/{batch}/commit` | Merged | [#56](https://github.com/sandjaie/OneLedger/pull/56) |
| `feature/p1-1c-import-screen` | `/import`: picker, per-file status, parse summary, stage | Merged | [#57](https://github.com/sandjaie/OneLedger/pull/57) |
| `feature/p1-1b-duplicate-override` | Accepting a row marked `duplicate` as a confirmed-distinct override: the dedupe hash is recomputed with the occurrence index advanced (M2 spec §4.7). Added in delivery — the slice covers M2 6a–6d and 6c was not in the review-routes branch | Merged | [#58](https://github.com/sandjaie/OneLedger/pull/58) |
| `feature/p1-1d-review-screen` | grouped review, row decisions, commit | Merged | [#59](https://github.com/sandjaie/OneLedger/pull/59) |

## Phase 1 · slice 1.1 — the acceptance run

Run locally on 9 September 2026 against the 38 card statements in the git-ignored
`pdf-statements/`. No figure, filename or row from any of them appears in this repository.

| Question the run asked | Answer |
| --- | --- |
| Do all 38 import end-to-end with no manual correction? | Yes. 35 are distinct statements and every one imported: layout identified, period derived, reconciliation `matched`, no row failing to normalize, batch committed. The other 3 are byte-identical re-downloads of files already in the set, and each was refused. |
| Do duplicates name the earlier import? | Yes, both kinds. The same file is refused by checksum, naming the date it first arrived and the batch that imported it. The same transactions in a different file stage as `duplicate`, each row naming the transaction it collides with. |
| Was anything corrected by hand? | No layout was edited, no row was hand-fixed, and no `UPDATE` was written to make a batch reconcile. |

The corpus covers two card accounts — 15 monthly statements on one, 20 on the other — across
June 2025 to August 2026 and December 2024 to August 2026 respectively. 1,974 transactions
committed across 35 batches, every batch `completed` and `matched`.

**Flyway.** `flyway migrate` ran for the first time against this tree and applied V14–V17 onto a
ledger standing at V13, validating all seventeen checksums first. A second `migrate` was a no-op.
Migrating from empty on a scratch stack, seeds and all fifteen SQL suites are green. No checksum
problem, no finding.

**Account setup is not statement correction, and one part of it is worth stating.** A card
statement prints its installment plans under each plan's *original purchase date*, so a statement
legitimately carries rows dated before its own period. `commit_import_batch` refuses a row dated
before the account's `opening_balance_date`, and `assert_account_reconciles` needs
`opening_balance` to be the balance carried into the earliest statement held. The two do not refer
to the same instant: the anchor is dated at or before the oldest installment row, while its value
is the balance at the first statement's period start. That is the only sound setting for an
account whose history starts mid-life, and it is what the Accounts screen asks for.

| Finding | Fix |
| --- | --- |
| `db/tests/0005` asserted one active owner session **across the whole table**, so the push hook refused every push on every branch once the owner had signed in | [#65](https://github.com/sandjaie/OneLedger/pull/65) |
| `apiFetch` handed the whole `{"detail": …}` body to `ApiError.detail`, so all nine screens that display it printed the JSON envelope instead of the sentence written for the owner | [#66](https://github.com/sandjaie/OneLedger/pull/66) |
| The review screen formatted a row's frozen `amountBase` with the **account's** currency, printing a base-currency figure under the card's symbol and grouping | [#67](https://github.com/sandjaie/OneLedger/pull/67) |

Each was invisible to the existing suite for the same reason: a fixture that agreed with itself
where reality does not — an empty session table, a bare-text error body, a household reporting in
the currency its card is denominated in.

## Phase 1 · slice 1.2 — the acceptance run

Run locally on 10 September 2026 against all 71 statements in the git-ignored
`pdf-statements/`, across four producers and seven accounts. No figure, filename or row from
any of them appears in this repository.

| Question the run asked | Answer |
| --- | --- |
| Do all 71 gathered files import? | **67 of 71.** The four that did not are all refused by design, and each is named below. 4,184 transactions across 67 batches, every one `completed` and `matched`. |
| Does every account reconcile across its period? | Yes, all seven. Every commit runs `assert_account_reconciles`, so a batch that reached `completed` proved it. |
| Does an encrypted statement import without its password touching disk? | Yes. Six documents are recorded `is_password_protected`; the stored blob is the encrypted original, and the password appears in no filename, no storage path, no description, no reference and no log line. |

| Layout | Files | Imported | Rows |
| --- | --- | --- | --- |
| Card (slice 1.1) | 38 | 35 | 1,974 |
| Current account | 21 | 21 | 307 |
| Passbook | 6 | 5 | 1,292 |
| Ledger (encrypted) | 6 | 6 | 611 |

**The four that did not import, and why each is right.** Three are byte-identical re-downloads
of card statements already in the set, refused by checksum, each naming the earlier import — the
slice 1.1 criterion, unchanged. The fourth is a passbook statement whose *own text layer* is
missing the integer part of one balance figure: the characters are literally spaces, confirmed
against the page's character layer, so no band and no tolerance can recover them. Invariant I8
refuses a statement it cannot verify rather than walking past the row, and that account is
anchored at its earliest importable statement instead.

**Period provenance is now split, as it should be.** 32 of the 67 batches record a period the
statement stated and 35 one derived from the rows — the card layout deliberately derives, because
its installment block prints purchase dates outside the period; the three account layouts state.

| Finding | Fix |
| --- | --- |
| `continuation_rule` could not tell wrapped narration from a closing legal block: 246 prose lines reached 6 of 1,532 rows, and every statement still reconciled | [#76](https://github.com/sandjaie/OneLedger/pull/76) — `blank_date_and_money_columns`, M2 spec §7 item 13 |
| The period gate failed rows a statement books after its period and values inside it, dropping money-bearing rows from the end where the remainder still reconciled | [#77](https://github.com/sandjaie/OneLedger/pull/77) — §7 item 14 |
| `commit_import_batch` verified as of `period_end`, which excludes those same rows while the declared closing includes them | [#77](https://github.com/sandjaie/OneLedger/pull/77) — V18, §7 item 15 |

The first two were invisible to every other check: the statements reconciled `matched` either
way. That is what a run against real files buys.

## Raised and not yet actioned

The four items slice 1.1 raised during delivery were shipped as their own branches:
[#61](https://github.com/sandjaie/OneLedger/pull/61) the identifier and money static check,
[#62](https://github.com/sandjaie/OneLedger/pull/62) `source_row_number NOT NULL`,
[#63](https://github.com/sandjaie/OneLedger/pull/63) the review skills' stale diff budget, and
[#64](https://github.com/sandjaie/OneLedger/pull/64)'s `period_is_derived`. The three the
acceptance run raised were shipped the same way, above. The fourth — a staged batch that could
not be cancelled through the app, so an abandoned one blocked its period for good — was slice
1.2's task 2b, and is delivered.

These are what the run raised and did not action — each in code the fixes it produced do not
touch, recorded here rather than folded in past a diff budget.

| Raised | What it is | Where it belongs |
| --- | --- | --- |
| The review screen renders no period | `import_batch.period_is_derived` reaches the payload and stops there; the import screen does show it. | A small `web/` branch |
| A repeated header line reaches the last row of each page on the encrypted layout | The producer runs whole lines together, so the account holder's locality is a single word centred in the narration column — no band separates it from real narration, and the only pattern that would match it is the owner's own address. 47 of 611 rows gain that one word; no figure, no balance. | A `web/` or layout-mapper branch, once 1.7 can edit a profile |
| `scripts/seed-household.sh` cannot correct the owner it seeded | It refuses a second run once an owner exists, which is right, but an owner seeded before `.env` was filled in carries the example address and no supported path fixes it. Hit during this run's setup. | A `scripts/` branch, or Settings › Household once that screen owns the field |
| Cancelling a batch frees its period but not its file | `POST /import/{batch}/cancel` reaches the state `_refuse_second_staging` treats as finished, so the account and period are re-stageable. `idx_document_checksum` is a separate guard: the same bytes are still refused on re-upload, naming a batch that imported nothing. Changing that means layer 1 reusing a document row rather than refusing it, which is its own decision. | A slice 1.7 branch, with the documents screen that would surface the stored file |

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
