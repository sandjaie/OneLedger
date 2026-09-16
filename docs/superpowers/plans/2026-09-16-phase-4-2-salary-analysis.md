# Phase 4.2 implementation plan — salary analysis

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Deliver the [Phase 4.2 specification](../specs/2026-09-16-phase-4-2-salary-analysis.md):
compensation, cash received, variable pay and increments per currency and year, reconciled to active
payslips and linked credits, with gaps and unlinked evidence shown rather than filled.

**Architecture:** Task 1 adds earning rules (V60), a pure analysis module and the read-only analysis
and earning-rule routes. Task 2 adds the analysis to the Salary screen.

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; one forward-only migration
(V60), no new dependency. This phase is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` after the previous one merges and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings. TypeScript formats only; `scripts/check-identifiers.py`
  refuses `Number`/`parseFloat` on money and any direct read of the `transaction` table.
- Every route uses `require_owner` and an explicit `owner.household_id` filter. Every GET opens
  `SET TRANSACTION READ ONLY` and writes nothing. Every write takes the household lock first.
- **Active payslip:** no payslip has `supersedes_payslip_id` equal to its id, the definition
  `routes/payslips.py` already uses.
- Payslip figures are per currency; nothing converts or adds across currencies.
- Labels are normalized exactly as `routes/payslips.py::_normalize_label` does, so a rule matches
  the stored `payslip_line.label`.
- No institution, employer or country name is an identifier (ADR-0012); components are the generic
  words `fixed`, `variable` and `reimbursement`.
- Tests cover major behaviour: money, authorization, household isolation, database writes, and one
  web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money, auth or
  migration findings, `scripts/verify.sh` once, push, PR, merge.

## Branch table

| Task | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | earning rules, salary analysis API | `feature/p4-2-t1-salary-analysis-api` | 800 | 750 | Phase 4.1 |
| 2 | salary analysis on the Salary screen | `feature/p4-2-t2-salary-analysis-screen` | 650 | 300 | 1 |

## Phase 4.2 Task 1 — earning rules and the salary analysis API

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-2-t1-salary-analysis-api` / `develop` / `develop`. **Depends:** Phase 4.1 Task 7 merged.
**Files:** create `db/migrations/V60__payslip_earning_rules.sql`,
`db/tests/0057_payslip_earning_rules.sql`, `api/oneledger/salary_analysis.py`,
`api/oneledger/routes/salary.py`, `api/tests/test_salary_analysis.py`,
`api/tests/test_salary_analysis_live.py`; update `api/oneledger/main.py`; generated types;
`docs/design/api-contracts.html`; `docs/superpowers/schema.md`;
`docs/superpowers/project-register.md`; progress. **Contract:**

- **V60.** `payslip_earning_rule`: `household_id UUID NOT NULL REFERENCES household(id)`, `label
  TEXT NOT NULL` with the same bound as `payslip_line.label`, `component TEXT NOT NULL` checked to
  `fixed`, `variable` or `reimbursement`, `created_at`, `updated_at`, `PRIMARY KEY (household_id,
  label)`. RLS as V44's `payslip_deduction_rule`; written directly by the route like V44. The db
  test proves the component check, the key and RLS.
- **Pure module `api/oneledger/salary_analysis.py`** (no database). Input: active payslips (id, pay
  month, currency, gross, deductions, net, earning lines, cash-received status and amount), the
  household's earning rules. Output: the response below. Rules:
  - **Month:** within a currency, the active payslips sharing a `pay_month` form one payslip month;
    its fixed, variable, reimbursement, compensation, deductions, net and cash received are the sums
    over those payslips. Inference counts months, not payslips; gaps, increments, years'
    `payslipMonths`/`receivedMonths` operate on months (a month is received when every payslip in it
    is received). The `payslips` array still lists each payslip.
  - **Inference** per currency and label over all active payslips: `fixed` when the label is in at
    least 3 payslip months and in at least `3/4` of the payslip months from its first to its last
    month (compare as integers: `4 × present ≥ 3 × span_months`); else `variable`. A rule
    overrides; `source` is `rule` or `inferred`.
  - **Per payslip** (and summed per month): `fixed`, `variable`, `reimbursement` sum their lines;
    `compensation = fixed + variable`. Assert `compensation + reimbursement = gross` and `gross −
    deductions = net` (both already DB invariants); a violation raises rather than reporting.
  - **Gaps** per currency: every calendar month strictly between two consecutive payslip months.
  - **Years** per currency and calendar year: sums of compensation, fixed, variable, reimbursement,
    deductions, net and cash received (eligible links only); `payslipMonths`, `gapMonths`,
    `receivedMonths`; `variableShare = variable / compensation × 100` quantized to 2 places
    half-even, null when compensation is zero.
  - **Increments** per currency: walk payslip months in order keeping `baseline` (initially the
    first month). For month `c` with `c.fixed ≠ baseline.fixed`: when a next month `n` exists and
    `n.fixed = baseline.fixed`, skip `c` (a one-off; baseline unchanged). Otherwise report
    `effectiveMonth = c`, `previousFixed = baseline.fixed`, `newFixed`, `change`, `changePercent` (2
    places half-even, null when previous is zero), `confirmed` (a next month exists), and
    `acrossGap` (a gap month lies between `baseline` and `c`), then set `baseline = c`. A month
    whose fixed pay equals the baseline's moves the baseline to it.
  - All sums `Decimal`, returned as strings at the payslip's scale; percentages as strings.
- **`GET /salary/analysis`** reads active payslips and their earning lines, the rules, and each
  payslip's receipt status: `unlinked` only when `deposit_transaction_id IS NULL`; `received` when
  the link is eligible as `routes/payslips.py` defines it (the credit is still reportable income of
  equal currency and amount); otherwise `ineligible`, including a link whose credit no longer
  appears in `v_transaction_posting`. Response: `{ currencies: [{ currency, years: [...], payslips:
  [{ payslipId, payMonth, compensation, fixed, variable, reimbursement, deductions, netPay,
  cashReceived, receiptStatus (received | unlinked | ineligible), lines: [{ lineNo, label, amount,
  component, source }] }], gaps: [month], increments: [...], variablePay: [{ payslipId, payMonth,
  label, amount }], labels: [{ label, component, source, monthCount, firstMonth, lastMonth,
  latestAmount }] }], salaryCreditsWithoutPayslip: [{ transactionId, bookedDate, amount, currency,
  accountName }] }`. Payslips newest month first; years newest first; increments and variable pay
  newest first; labels by label. Currencies by code.
- **Salary credits without a payslip:** rows of `v_transaction_reportable` for the household with
  `txn_type = 'income'` whose effective category (through `categories_sql.EFFECTIVE_CATEGORY`) is
  the Salary category found by `routes/payslips.py::_salary_category_id` or a child of it (`c.id =
  salary_id OR c.parent_id = salary_id`), excluding any `payslip.deposit_transaction_id` and any
  `payslip_line_posting.income_transaction_id`. Newest first, capped at 200.
- **`GET /salary/earning-rules`**, **`PUT /salary/earning-rules/{label:path}`** `{ component }` and
  **`DELETE /salary/earning-rules/{label:path}`** follow `routes/payslips.py`'s deduction-rule
  routes for label normalization (`_normalize_label_param`, 422 on a bad path label) and the upsert
  shape, but take the household lock first; PUT upserts and replays idempotently; DELETE returns 404
  when it removes no row. A rule may
  name a label no payslip carries yet.
- **Project register:** close "Employer-contribution and reimbursement payslip lines" for
  reimbursement (reimbursement is now a component) and keep employer contributions open; record that
  "Keep a linked payslip credit eligible when its ledger row is reclassified" stays read-time and is
  now surfaced as `ineligible` in the analysis.

**Tests:** db — V60 shape, component check, key, RLS. Pure — inference at the 3-payslip and
three-quarter boundaries; rule override both ways; reimbursement split; yearly sums and variable
share; a gap; an increment, a reversed one-off, an unconfirmed latest change, one across a gap, and
a payslip after a one-off compared with the right predecessor; a change the next month changes again
is confirmed; a separate bonus payslip in the same month adds no month, no increment and no
inference count; zero previous fixed pay. Live — a superseded payslip excluded; cash received for an
eligible link and none for unlinked and a credit reclassified to ineligible (status `ineligible`,
not `unlinked`); salary credits without a payslip excluding a linked credit and a V44 income leg;
rule PUT replay, DELETE 404, label normalization; another household's payslips, rules and credits
never appear and its rule is invisible; a GET writes nothing. **Commands:** `eval
"$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them; `(cd api && uv run pytest -q
tests/test_salary_analysis.py tests/test_salary_analysis_live.py tests/test_payslips_live.py)` then
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`; shared types command; `(cd
web && npm run typecheck)`; `scripts/verify.sh`. **Acceptance:** every salary figure the screen
needs is an exact string that reconciles to active payslips and linked credits, with every gap and
unlinked credit listed.

## Phase 4.2 Task 2 — salary analysis on the Salary screen

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-2-t2-salary-analysis-screen` / `develop` / `develop`. **Depends:** Task 1. **Files:**
create `web/app/salary/salary-analysis.tsx` and its test; update
`web/app/salary/payslips-screen.tsx` (or the section that hosts it), `web/app/globals.css`;
progress. **Contract:**

- The Salary screen shows **Analysis** above the existing payslip sections, loaded from
  `GET /salary/analysis`, and reloads it after a payslip is entered, corrected, imported or linked.
- Per currency (a currency switch when there is more than one):
  - **Years**: compensation, fixed, variable with its share, reimbursement, deductions, net pay and
    cash received, with payslip months, gap months and received months beside them;
  - **Months**: each payslip month with the same figures and its receipt status, and each gap month
    as its own row reading "No payslip";
  - **Increments**: month, previous and new fixed pay, change and percentage, marked **Unconfirmed**
    or **After a gap** where the server says so;
  - **Variable pay**: month, label and amount;
  - **Earning labels**: label, component (with **Inferred** where not the owner's), month count,
    first and last month and latest amount; a component choice saves a rule and **Use inference**
    removes it, then the analysis reloads.
- **Salary credits without a payslip**: date, account, amount, linking to the ledger row.
- Loading, empty ("No payslips yet"), error and retry states. Every amount and percentage is a
  server string; the browser computes nothing.

**Tests:** the years, months with a gap row, increments with both markers, variable pay and credits
without a payslip render from server strings; a component change PUTs the rule and reloads; **Use
inference** DELETEs it. **Commands:** `(cd web && npx vitest run && npm run typecheck && npm run
lint)`; `scripts/verify.sh`. **Acceptance:** the owner reads each year's pay, what was fixed and
variable, what reached the bank, their increments and every missing payslip or unlinked credit on
one screen, and corrects a label's component in place.
