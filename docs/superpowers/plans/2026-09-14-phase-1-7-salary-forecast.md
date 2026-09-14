# Phase 1.7 salary and thin forecast implementation plan

> Execute sequentially in the active checkout with `superpowers:executing-plans`. Track completion
> only in `../progress.md`.

**Goal:** Exact payslips, entered or imported from a supported text PDF, linked to one ledger
credit; and commitments that move an assumption-labelled 30/60/90-day per-account forecast.

**Architecture:** `record_payslip` owns every payslip write, from entry or import; triggers keep
payslips immutable except the deposit link. A content-selected adapter turns pay-advice PDF pages
into exact monthly drafts. Commitments are single-row assumptions; the forecast is a pure `Decimal`
calculation over account balances and commitments, computed on read in a read-only transaction.

**Stack:** Existing PostgreSQL/Flyway, Python/FastAPI, pdfplumber words and Next.js/React; no new
dependency. See the [Phase 1.7 specification](../specs/2026-09-14-phase-1-7-salary-forecast.md).

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees. Each
  branch starts from current `develop` and opens one PR to `develop` titled `[P1.7] …`. The
  specification and this plan land with Task 1. Task 2 starts after Task 1 merges.
- Task 1 depends on merged Phase 1.6 implementation (PRs #194–#201). Run `/oneledger-plan-review`
  once before implementation and apply its blocking findings.
- Money is `NUMERIC(20,4)`, `Decimal` and strings; TypeScript formats with `formatMoney` only.
- Routes use `require_owner` and filter `household_id`; new tables carry RLS and household-scoped
  composite foreign keys. Labels are bounded by length only.
- Ledger reads use `v_transaction_reportable` or `v_transaction_posting`, never base `transaction`
  (`scripts/check-identifiers.py` rule 3). No employer, institution, country or currency name is an
  identifier.
- Fixtures are synthetic. Real payslips stay git-ignored and are never read by an agent, copied into
  a fixture or quoted in a log, error, commit or message.
- New live fixtures create synthetic households and `TRUNCATE` their new tables at teardown, because
  other suites delete accounts and truncate `transaction`. Import tests set `DOCUMENTS_DIR` to
  `tmp_path`.
- Tests cover major behavior only: reconciliation refusal, import idempotency, the multi-month
  file, link eligibility, forecast math and owner/household isolation, plus the financial write
  floor in `docs/development-cycle.md`.
- Start one server with `eval "$(scripts/test-db.sh up)"`; fresh shells use
  `eval "$(scripts/test-db.sh env)"`. After one review and one repair, run `scripts/verify.sh` once
  on the committed head; it tears the server down.
- Shared commands: docs `python3 scripts/check-docs.py`; static `python3 scripts/check-identifiers.py`;
  Python `cd api && uv run ruff check . && uv run mypy .`; types
  `cd web && npm run api:types && npm run api:types:check`.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | payslips, PDF import and the net-pay link | `feature/p1-7a-payslips` | 1,450 | 1,050 | Phase 1.6 merged |
| 2 | commitments and the thin forecast | `feature/p1-7b-commitments-forecast` | 900 | 750 | 1 |

## Task 1 — payslips, PDF import and the net-pay link

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p1-7a-payslips` /
`develop` / `develop`.
**Depends:** Phase 1.6 implementation merged. **Forecast:** 1,300–1,500 production, 850–1,100 tests.
**Files:** create `db/migrations/V42__payslips.sql`, `db/tests/0039_payslips.sql`,
`api/oneledger/payslip_pdf.py`, `api/oneledger/routes/payslips.py`,
`api/tests/fixtures/payslip_pdf.py`, `api/tests/test_payslip_pdf.py`,
`api/tests/test_payslips_live.py`, `web/app/salary/page.tsx`, `web/app/salary/payslips-screen.tsx`,
`web/app/salary/__tests__/payslips-screen.test.tsx`; update `api/oneledger/main.py`,
`web/app/workspace.tsx`, `web/app/__tests__/workspace.test.tsx`, generated `web/types/api/`;
`docs/superpowers/schema.md`, `docs/design/data-model.html`, `docs/design/api-contracts.html`,
`docs/design/statement-ingestion.html`, `docs/operations/security-and-privacy.html`,
`docs/operations/testing-and-data-quality.html` and `docs/superpowers/progress.md`.
**Contract:**

- V42 is the specification's payslip DDL, `record_payslip`, `payslip_totals_agree`,
  `preserve_payslip`, `preserve_payslip_line`, `assert_payslip_deposit` and RLS, exactly.
- `payslip_pdf.py` implements the specification's import rules 1–7 as pure functions over
  `Sequence[Page]` returning frozen dataclasses; the route applies the `unknown_currency` check
  against the currency table after extraction. One layout needs no adapter registry; add one only
  when a second layout exists.
- `routes/payslips.py` serves the seven payslip routes. Entry, correction and import call
  `record_payslip` (which defers and re-applies its own totals constraint per call), and map
  constraint names and SQLSTATEs to the specification's statuses. Import reads the upload through
  `ingest.uploads.uploaded_pages`, re-extracts on commit, derives each month's `uuid5` key, records
  each month in its own savepoint, and stores the file and `document` row as
  `routes/investment_imports.py::_document` does when at least one month reconciles.
- Link and unlink lock the household row, then the payslip `FOR UPDATE`; a transaction absent from
  the household's `v_transaction_posting` is 404; only `deposit_transaction_id` is written.
  `deposit.eligible` is computed from `v_transaction_reportable` on read.
- The Salary screen follows the specification and `web/app/investments/entry-screen.tsx`'s injected
  `request` and `createIdempotencyKey`; month input is `<input type="month">`. The workspace adds the
  `salary` section, nav link and its `/currencies` load.

**Tests:**

- SQL: owner, member, foreign and anon RLS on both tables; member refused by `record_payslip`;
  totals mismatch refused at constraint time; fact and line updates and deletes refused.
- `test_payslip_pdf.py` over `api/tests/fixtures/payslip_pdf.py` (built on `synthetic_pdf.py`): a
  three-month file with side-by-side payment and deduction lines, a detail page that is ignored and
  one non-reconciling month extracts exact drafts and the fixed reason; a non-matching PDF is
  unsupported.
- Live: entry 201, identical replay 200 and conflicting key 409; excess precision 422 and exact
  round trip; a non-reconciling entry stores no rows; foreign payslip or credit 404; import preview
  writes nothing; commit imports only reconciling months, with at least two reconciling months
  committed in one request; the same file again returns
  `already_imported` with no new rows; a manually entered month returns `already_recorded`; linking
  refuses a wrong amount, currency, transfer leg, superseded payslip and a credit linked elsewhere;
  candidate window edges (first day of the month, 10th of the next, 11th excluded); `eligible: false`
  after the credit becomes a transfer leg; dashboard flows unchanged by linking. Corrections: 201,
  identical replay 200, conflicting key 409; the original's facts and lines unchanged and
  `supersededByPayslipId` set; a second correction of the same payslip 409; correcting a linked
  payslip 409; linking an already-linked payslip to a different credit 409.
- Web: typed strings posted with one key across a retry; import preview then confirmation; link
  only after explicit confirmation.

**RED/GREEN:** `eval "$(scripts/test-db.sh up)"`; `scripts/hooks/db-tests.sh`;
`(cd api && uv run pytest -q tests/test_payslip_pdf.py tests/test_payslips_live.py
tests/test_dashboard_flows_live.py)`; shared types command;
`cd web && npx vitest run app/salary/__tests__/payslips-screen.test.tsx
app/__tests__/workspace.test.tsx && npm run typecheck`; shared static command; `scripts/verify.sh`.
**Acceptance:** from `/salary`, a synthetic payslip stores exactly or stores nothing; a synthetic
multi-month PDF imports its reconciling months once however often it is imported; net pay links to
one eligible credit and unlinks with income, spending and net worth unchanged; a correction
supersedes without editing the original.

## Task 2 — commitments and the thin forecast

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-7b-commitments-forecast` / `develop` / `develop`.
**Depends:** Task 1. **Forecast:** 800–950 production, 600–850 tests.
**Files:** create `db/migrations/V43__commitments.sql`, `db/tests/0040_commitments.sql`,
`api/oneledger/forecast.py`, `api/oneledger/routes/plans.py`, `api/tests/test_forecast.py`,
`api/tests/test_plans_live.py`, `web/app/plans/page.tsx`, `web/app/plans/plans-screen.tsx`,
`web/app/plans/__tests__/plans-screen.test.tsx`; update `api/oneledger/main.py`,
`api/oneledger/routes/dashboard.py`, `web/app/workspace.tsx`, `web/app/__tests__/workspace.test.tsx`,
generated `web/types/api/`; schema, data-model, API-contract, testing, IA (Plans) and progress
documents.
**Contract:**

- V43 is the specification's commitment DDL and RLS, exactly.
- `forecast.py` exposes pure `occurrence_dates(cadence: str, first: date, end: date | None,
  after: date, through: date) -> list[date]` and `project_balances(start: Decimal,
  occurrences: Sequence[tuple[date, Decimal]], as_of: date) -> list[tuple[int, date, Decimal]]`
  implementing forecast rules 4–6.
- `routes/plans.py` serves the four commitment routes and `GET /forecast`. Writes take the household
  lock; create replays by key with field equality; update locks the row and refuses archived rows;
  archive sets `archived_at` once. Forecast opens its transaction with `SET TRANSACTION READ ONLY`
  before any other statement (it calls no SECURITY DEFINER function, so owner scoping by explicit
  household filters suffices) and applies rules 1–7. Extract the active valuation-period selection
  from `dashboard.py::_net_worth` into a shared
  SQL constant used by both, with no dashboard behavior change.
- The Plans screen follows the specification; the workspace replaces the disabled Plans button with
  a `plans` link and loads `/accounts` and `/currencies` for it.

**Tests:**

- SQL: owner, member, foreign and anon RLS; cross-household account foreign key refused.
- Unit: 31st to 30th/28th/29th, annual 29 February, inclusive end date, once, exclusive balance
  date and inclusive day 90, signed amounts and exact 4-place sums.
- Live: create 201, replay 200, conflicting key 409, foreign account or commitment 404, currency
  mismatch 422, archived update 409; an amount beyond 4 decimals or 16 integer digits 422 with no
  row; the signed amount string round-trips exactly through create, update and list; forecast start
  balance and date, `dueByAsOf`, holdings and
  snapshot accounts `not_ledger_valued`, `balance_after_as_of`, closed-account and mismatch
  commitments, archived ignored, another household's commitments invisible, and row counts, balances
  and `GET /dashboard` unchanged by `GET /forecast`.
- Web: points, dates, reasons and assumptions rendered from server strings; a commitment write
  refetches the forecast.

**RED/GREEN:** `eval "$(scripts/test-db.sh up)"`; `scripts/hooks/db-tests.sh`;
`(cd api && uv run pytest -q tests/test_forecast.py tests/test_plans_live.py
tests/test_dashboard_live.py)`; shared types command;
`cd web && npx vitest run app/plans/__tests__/plans-screen.test.tsx
app/__tests__/workspace.test.tsx && npm run typecheck`; shared static command; `scripts/verify.sh`.
**Acceptance:** from `/plans`, a synthetic commitment added, edited or archived moves its account's
30/60/90-day balances exactly; exclusions, dates and assumptions are visible; the forecast writes
nothing; the specification's acceptance passes.

## Stop conditions

- Stop rather than infer a payslip fact, import a month that does not reconcile, link an inexact
  credit, or project an account whose valuation, balance date or currency cannot support it.
- Stop before push when a required check is red or not run, review blocks, or size exceeds the hard
  stop; move an unfinished remainder to the next branch and name it in `../progress.md`.
- Never read, copy or describe a real payslip outside the running application.
