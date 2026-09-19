# Phase 4.8 Compact Financial Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild every OneLedger route as the owner-approved compact white, cool-gray and blue
financial console while preserving financial semantics and adding only the exact read contracts
needed by the approved experience.

**Architecture:** Establish the final authenticated shell, search and shared console compositions
once, then convert coherent screen families in sequential branches. Overview, Transactions and
search receive narrow owner-scoped backend reads; every other family reuses existing exact strings,
server ratios and chart coordinates. The checked-in four-screen HTML is the visual contract and the
final branch audits every route against it before owner acceptance.

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, checked-in Nova/shadcn source
components, Lucide icons, Recharts through shadcn Chart, FastAPI, Pydantic, PostgreSQL `NUMERIC`,
Python `Decimal`, Vitest and pytest.

**Spec:**
[Phase 4.8 compact financial console](../specs/2026-09-20-phase-4-8-compact-financial-console.md)

**Visual reference:**
[Compact Financial Console](../../product/mockups/CompactFinancialConsole.html)

## Global constraints

- Workspace for every task: `/Users/sandjaieravi/dev/OneLedger`, active checkout only; no worktree.
- PR target for every task: `develop`. Task 4.8.1 starts from
  `bb997e0662e570a520f10c94b35e7156d1a8502c`; each later branch starts from current `develop`
  only after the preceding task is merged and `develop` is fast-forwarded.
- One implementation writer works at a time. The orchestrator owns branch switches, pushes, PRs
  and merges; review agents are read-only.
- Before Task 4.8.1 implementation, run one read-only phase plan review over this plan and the
  approved specification, repair blocking findings once, and do not repeat the plan review.
- Match the checked-in HTML's shell, topology, density, color, typography and alignment. Real data,
  accessibility and responsive reflow may change content length, not the visual direction.
- Use checked-in shadcn primitives for accessible behavior and shared OneLedger compositions for
  domain layout. Do not run a preset overwrite or add another CSS override layer.
- Money, prices, rates, gains and returns remain PostgreSQL `NUMERIC`, Python `Decimal` and strings
  on the wire. TypeScript formats only and never derives a financial total, ratio or chart point.
- Every new read begins read-only, requires the owner, includes explicit household scoping and
  returns missing/partial evidence honestly. No new financial write is planned.
- Do not commit real financial values, names, identifiers, filenames, statement content or
  screenshots. Fixtures and visual checks use synthetic data.
- The stopped 4.7.15 work is the immutable stash object
  `9be2a6d57c0545bf7d5c084aac90feb874b6415c` (`WIP on
  feature/p4-7-editorial-position: bb997e0 Merge pull request #266 from
  sandjaie/feature/p4-7-editorial-foundation`). Inspect or apply individual hunks by object ID only
  when a task names reusable behavior; never pop or apply the stash wholesale.
- Write new tests only for major financial behavior, authorization/household isolation, new backend
  contracts and critical accessibility interactions. Styling, ordinary markup, copy and obvious
  composition receive no dedicated test. Preserve and run affected existing tests.
- During implementation run only focused affected tests. Each branch then receives one read-only
  review, one consolidated repair, and one final verifier run on the clean final commit. Do not run
  the full verifier before review or rerun it on an unchanged head.
- Use `scripts/verify.sh` with the task's named affected API/database tests when API, database or
  generated contracts change; otherwise use `scripts/verify-web.sh`. Each task PR updates
  `docs/superpowers/progress.md` truthfully and names the exact next task before final verification.
- Automated checks and merged PRs do not equal visual acceptance. Only the owner may mark Phase 4.8
  accepted after Task 4.8.13's running-application review.

## Shared delivery sequence for every task

Each numbered task is one branch and one independently reviewable outcome:

- [ ] Create or switch to the task's exact branch from the declared base; confirm a clean status.
- [ ] When the task introduces a named major financial, authorization, privacy, backend-contract or
  critical accessibility behavior, add the specified test and run it to confirm RED. When it is a
  composition-only change, add no styling test and proceed with the affected existing suite.
- [ ] Implement the task without changing unrelated financial behavior.
- [ ] Run the focused command until the affected tests pass; do not run the final verifier here.
- [ ] For a UI task, run one focused visual pass in the development stack at 1440, 1024, 588 and
  360 px before review, covering only the changed routes and their loading/empty/error/partial
  states. Tasks 4.8.2, 4.8.3, 4.8.4 and 4.8.9 compare their archetype directly with the checked-in
  HTML. Record findings, repair them in the implementation, and do not defer first fidelity review
  to Task 4.8.13.
- [ ] Update `docs/superpowers/progress.md`, inspect the diff and diff size, then make one coherent
  local commit using the task's declared commit subject.
- [ ] Run one read-only code review over the task diff. Apply blocking findings and small related
  medium findings in one repair, then rerun only affected tests and commit the repair if needed.
- [ ] Run the task's declared verifier once on the clean final head.
- [ ] Push that unchanged head, open the PR to `develop` with actual evidence, merge after gates are
  green, delete the branch, fast-forward `develop`, and only then start the next task.

## Review focus

1. Missing, partial or stale evidence must remain unavailable or visibly incomplete; it must never
   become zero, an all-clear state or a connected chart segment. Tasks 4.8.2, 4.8.3 and 4.8.6 own
   the critical tests.
2. A long account, merchant, security, document or plan label must not displace an amount or action
   or create page-wide overflow at 360, 588, 1024 or 1440 px. Tasks 4.8.3, 4.8.4, 4.8.10 and the
   final audit own this check.
3. Closing a menu, sheet or dialog must return focus to its opener, including a row action in a
   horizontally scrollable table. Tasks 4.8.1, 4.8.3, 4.8.7 and 4.8.9 own the critical tests.
4. Search/filter responses that arrive out of order, URL Back/Forward and a cleared short query
   must not show stale results. Tasks 4.8.1 and 4.8.4 own the critical tests.
5. Loading, empty, error, partial and populated states must retain the same page identity and must
   not use the rejected beige/editorial composition. Each family branch inspects these states and
   Task 4.8.13 performs the cross-application check.

---

## Task 4.8.1 — Console foundation, authenticated shell and workspace search

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8a-compact-console-foundation` (already created)

**Base and PR target:** `develop` at
`bb997e0662e570a520f10c94b35e7156d1a8502c`; PR to `develop`.

**Outcome:** Land the approved spec, plan and durable HTML reference; replace the editorial shell
with the exact compact console shell; create the shared Phase 4.8 compositions; and make the top-bar
search a real owner-scoped interaction while leaving existing screen bodies behaviorally intact.

**Files:**

- Add: `docs/superpowers/specs/2026-09-20-phase-4-8-compact-financial-console.md`
- Add: `docs/superpowers/plans/2026-09-20-phase-4-8-compact-financial-console.md`
- Add: `docs/product/mockups/CompactFinancialConsole.html`
- Modify: `docs/product/master-implementation-plan.html`
- Modify: `docs/product/roadmap.html`
- Modify: `docs/superpowers/specs/2026-09-18-phase-4-7-workspace-experience.md`
- Modify: `docs/superpowers/plans/2026-09-19-phase-4-7-editorial-refinement.md`
- Modify: `docs/superpowers/progress.md`
- Add: `api/oneledger/routes/search.py`
- Modify: `api/oneledger/main.py`
- Add: `api/tests/test_search_live.py`
- Modify: `docs/design/api-contracts.html` — add the complete `GET /search` contract, safe fields,
  ranking, limits, owner/household boundary and forbidden response data
- Regenerate: `web/openapi.json`
- Regenerate: `web/types/api/types.gen.ts`
- Regenerate: `web/types/api/index.ts`
- Modify: `web/app/globals.css`
- Modify: `web/app/workspace.tsx`
- Add: `web/app/ui/app-shell.tsx`
- Add: `web/app/ui/console-page-header.tsx`
- Add: `web/app/ui/attention-row.tsx`
- Add: `web/app/ui/module-grid.tsx`
- Add: `web/app/ui/data-toolbar.tsx`
- Add: `web/app/ui/data-table-region.tsx`
- Add: `web/app/ui/status-label.tsx`
- Add: `web/app/ui/workspace-search.tsx`
- Add: `web/app/ui/__tests__/workspace-search.test.tsx`
- Modify: `web/app/__tests__/workspace.test.tsx`
- Modify: `web/app/ui/__tests__/page-grammar.test.tsx`

**Interfaces:**

```python
class SearchResult(BaseModel):
    kind: Literal["account", "transaction", "document", "holding", "goal", "commitment", "register"]
    resourceId: UUID
    primaryLabel: str
    secondaryLabel: str | None
    route: str
    date: date | None
    status: str | None

class SearchResponse(BaseModel):
    results: list[SearchResult]
```

`GET /search?q=&limit=` trims `q`, accepts 2–120 characters and a per-kind limit of 1–10, begins
with `SET TRANSACTION READ ONLY`, scopes to the owner and applies an explicit household predicate in
every domain query. It searches only the safe fields approved by the spec and returns fixed kind
order with exact-prefix, word-prefix and contains ranking. It never selects complete identifiers,
normalized identifiers, filenames, paths or document content.

```ts
type ConsolePageHeaderProps = {
  title: string;
  description?: string;
  actions?: React.ReactNode;
};

type AttentionRowProps = {
  tone: "attention" | "danger" | "neutral" | "success";
  title: string;
  detail?: string;
  action?: React.ReactNode;
};
```

`AppShell` owns grouped navigation, responsive navigation, top utilities and the content boundary.
`WorkspaceSearch` owns debounced request state, grouped results, keyboard selection, Escape/clear,
stale-response suppression and route navigation. Queries shorter than two trimmed characters issue
no request and clear results.

**Major tests:**

- owner success across all seven result kinds; member refusal; another household absent;
- per-kind limit and deterministic ranking; masked identifiers searchable without serializing a
  complete identifier; transaction labels pass the existing response redactor;
- the GET changes no rows and its JSON contains no filename/path/content/private identifier keys;
- search keyboard selection, Escape and focus return; a stale response cannot replace a newer one;
- the persistent desktop rail, responsive sheet, skip link, owner/logout/reporting controls and
  active route retain their existing behavior under `AppShell`.

**Focused command:**

```bash
cd api && uv run pytest -q tests/test_search_live.py
cd ../web && npm test -- app/ui/__tests__/workspace-search.test.tsx app/__tests__/workspace.test.tsx app/ui/__tests__/page-grammar.test.tsx
```

**Commit:** `feat(web): establish the compact financial console shell`

**Final verifier:** `scripts/verify.sh --api-test tests/test_search_live.py --api-test tests/test_local_gate_contract.py`

**Progress next task:** 4.8.2 — Overview console and position composition.

---

## Task 4.8.2 — Overview console and dashboard position composition

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8b-overview-console`

**Base and PR target:** current `develop` after Task 4.8.1 merges; PR to `develop`.

**Outcome:** Make Overview match the approved HTML and add one exact server-owned position
composition without creating a second valuation model.

**Files:**

- Modify: `api/oneledger/routes/dashboard.py`
- Modify: `api/tests/test_dashboard_flow_evidence.py`
- Modify: `api/tests/test_dashboard_live.py`
- Modify: `api/tests/test_dashboard_flows_live.py`
- Modify: `docs/design/api-contracts.html` — extend `GET /dashboard` with the exact
  `positionComposition` fields, buckets, Decimal rounding, missing-value and sign semantics
- Regenerate: `web/openapi.json`
- Regenerate: `web/types/api/types.gen.ts`
- Regenerate: `web/types/api/index.ts`
- Modify: `web/app/overview/overview-screen.tsx`
- Modify: `web/app/overview/__tests__/overview-screen.test.tsx`
- Modify: `web/app/ui/evidence-chart.tsx`
- Modify: `web/app/ui/__tests__/evidence-chart.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:**

```python
class PositionCompositionMissing(BaseModel):
    accountId: UUID
    currency: str

class PositionCompositionRow(BaseModel):
    key: Literal["cash", "investments", "property", "retirement", "other", "liabilities"]
    label: str
    valueBase: str | None
    valueSecondary: str | None
    sharePercentBase: str | None
    sharePercentSecondary: str | None
    chartCoordinateBase: int | None
    chartCoordinateSecondary: int | None

class PositionComposition(BaseModel):
    rows: list[PositionCompositionRow]
    missing: list[PositionCompositionMissing]
```

Add `DashboardResponse.positionComposition`. Extend the existing resolved `_net_worth()` account
facts with `account_type`; reuse its ledger/holdings/snapshot resolution. Shares divide each
bucket's absolute value by the sum of absolute bucket values, return four-place half-even percent
strings and 0–10,000 half-even coordinates. Liabilities retain signed value strings but use
non-negative shares. A zero denominator returns zero shares; a missing included value nulls the
affected currency for every row and lists the missing account/currency.

**Major tests:**

- exact account-type buckets, holdings not double-counted, liability sign and non-negative share;
- four-place half-even rounding, coordinate bounds, zero denominator and deterministic order;
- missing one reporting currency nulls that currency's whole composition but not the other;
- owner/member and foreign-household cases remain correct and the route is read-only;
- Overview shows the approved metric strip and six modules, exact server strings, next-30-day
  commitment label, gaps for incomplete history and no promotional/editorial panel.

**Focused command:**

```bash
cd api && uv run pytest -q tests/test_dashboard_flow_evidence.py tests/test_dashboard_live.py tests/test_dashboard_flows_live.py
cd ../web && npm test -- app/overview/__tests__/overview-screen.test.tsx app/ui/__tests__/evidence-chart.test.tsx
```

**Commit:** `feat(web): make overview a compact financial console`

**Final verifier:** `scripts/verify.sh`

**Progress next task:** 4.8.3 — Accounts and Wealth position console.

---

## Task 4.8.3 — Accounts, valuation and Wealth position console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8c-position-console`

**Base and PR target:** current `develop` after Task 4.8.2 merges; PR to `develop`.

**Outcome:** Apply the approved position-family design to Accounts, valuation and Wealth using
existing exact reads, with contextual actions and preserved incomplete history.

**Files:**

- Modify: `web/app/accounts/accounts-screen.tsx`
- Modify: `web/app/accounts/valuation-panel.tsx`
- Modify: `web/app/accounts/__tests__/accounts-screen.test.tsx`
- Modify: `web/app/accounts/__tests__/valuation-panel.test.tsx`
- Modify: `web/app/wealth/wealth-screen.tsx`
- Modify: `web/app/wealth/__tests__/wealth-screen.test.tsx`
- Modify: `web/app/ui/row-actions.tsx`
- Modify: `web/app/ui/__tests__/row-actions.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Derive the spec's seven display roles from `AccountRow.accountType`; do not store a
new role. Evidence status is `needs_confirmation`, `missing_snapshot`, an existing typed coverage
problem, or `current`; do not invent an age threshold. Use `/dashboard.netWorth` for reporting-pair
assets/liabilities/liquid values when available and retain exact native grouped totals as evidence.

Inspect stash object `9be2a6d57c0545bf7d5c084aac90feb874b6415c` and manually reproduce only
the contextual menu, focus return, closed-account
disclosure, valuation context/newest-first evidence, selected-month Wealth summary and named table
overflow behavior. Do not apply its CSS, editorial components, copy or progress changes.

**Major tests:**

- long labels, deterministic role grouping, native/reporting values not mixed, liability signs,
  unacknowledged opening and missing snapshot attention states;
- row menu and valuation panel return focus to the originating action; masked reveal privacy and
  mutation lock behavior remain unchanged;
- valuation dry-run/confirm identity and newest-first chronology remain exact;
- Wealth incomplete months remain gaps, stale responses cannot replace the selection, refresh
  feedback is announced and no unavailable value becomes zero.

**Focused command:**

```bash
npm --prefix web test -- app/accounts/__tests__/accounts-screen.test.tsx app/accounts/__tests__/valuation-panel.test.tsx app/wealth/__tests__/wealth-screen.test.tsx app/ui/__tests__/row-actions.test.tsx
```

**Commit:** `feat(web): align position screens with the compact console`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.4 — Transactions and Spending activity console.

---

## Task 4.8.4 — Transactions and Spending activity console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8d-activity-console`

**Base and PR target:** current `develop` after Task 4.8.3 merges; PR to `develop`.

**Outcome:** Deliver the dense URL-filtered ledger and compact Spending evidence view, backed by
one exact filtered transaction summary.

**Files:**

- Modify: `api/oneledger/routes/transactions.py`
- Modify: `api/tests/test_transactions.py`
- Add: `api/tests/test_transaction_summary_live.py`
- Modify: `docs/design/api-contracts.html` — extend `GET /transactions` with every summary field,
  filter scope, reportable-versus-ledger meaning and refund/net semantics
- Regenerate: `web/openapi.json`
- Regenerate: `web/types/api/types.gen.ts`
- Regenerate: `web/types/api/index.ts`
- Modify: `web/app/workspace.tsx`
- Modify: `web/app/transactions/transactions-screen.tsx`
- Modify: `web/app/transactions/classify-prompt.tsx`
- Modify: `web/app/transactions/transfer-panel.tsx`
- Modify: `web/app/transactions/__tests__/transactions-screen.test.tsx`
- Modify: `web/app/transactions/__tests__/transfer-panel.test.tsx`
- Modify: `web/app/spending/spending-screen.tsx`
- Modify: `web/app/spending/spending-evidence-chart.tsx`
- Modify: `web/app/spending/budget-panel.tsx`
- Modify: `web/app/spending/__tests__/spending-screen.test.tsx`
- Modify: `web/app/spending/__tests__/spending-evidence-chart.test.tsx`
- Modify: `web/app/spending/__tests__/budget-panel.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:**

```python
class TransactionSummary(BaseModel):
    reportableIncomeBase: str
    reportableIncomeSecondary: str
    reportableSpendingBase: str
    reportableSpendingSecondary: str
    reportableNetBase: str
    reportableNetSecondary: str
    unreviewedCount: int
    uncategorizedCount: int
    attentionCount: int
    moneyScope: Literal["reportable"]
    attentionScope: Literal["ledger"]
```

Add `TransactionsResponse.summary`. Build one typed filter predicate used by the base-ledger count,
base-ledger attention query and `v_transaction_reportable` financial query; keep sort, direction,
limit and offset outside it. Reuse Dashboard/Spending semantics exactly: income is the sum of
`txn_type='income'`, spending is the negative sum of other reportable types, net is income minus
spending, and refunds reduce spending. `attentionCount` is the distinct union of unreviewed and
unclassified ledger rows. Begin the GET read-only and retain explicit household scoping.

**Major tests:**

- every existing filter affects list, total and summary; pagination/sort do not alter summary;
- transfers/card/investment/duplicate rows remain visible when selected but never enter reportable
  money; refunds reduce spending and may make it negative;
- exact string serialization, attention union without double-counting, owner/member and household
  isolation, unchanged rows after GET;
- URL Back/Forward, stale filter response suppression and keyboard-operable compact toolbar;
- Spending continues to use server totals/ratios, stable categories and exact refund/budget states.

**Focused command:**

```bash
cd api && uv run pytest -q tests/test_transactions.py tests/test_transaction_summary_live.py tests/test_spending_live.py
cd ../web && npm test -- app/transactions/__tests__/transactions-screen.test.tsx app/transactions/__tests__/transfer-panel.test.tsx app/spending/__tests__/spending-screen.test.tsx app/spending/__tests__/spending-evidence-chart.test.tsx app/spending/__tests__/budget-panel.test.tsx
```

**Commit:** `feat(web): deliver the compact activity console`

**Final verifier:** `scripts/verify.sh`

**Progress next task:** 4.8.5 — Subscriptions and Recurring console.

---

## Task 4.8.5 — Subscriptions and Recurring console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8e-recurring-console`

**Base and PR target:** current `develop` after Task 4.8.4 merges; PR to `develop`.

**Outcome:** Replace text-heavy recurring surfaces with compact renewal/cadence evidence, dense
lifecycle tables and contextual actions without changing lifecycle behavior.

**Files:**

- Modify: `web/app/subscriptions/subscriptions-screen.tsx`
- Modify: `web/app/subscriptions/subscription-detail.tsx`
- Modify: `web/app/subscriptions/__tests__/subscriptions-screen.test.tsx`
- Modify: `web/app/subscriptions/__tests__/subscription-detail.test.tsx`
- Modify: `web/app/recurring/recurring-screen.tsx`
- Modify: `web/app/recurring/__tests__/recurring-screen.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Reuse `/recurring`, `/recurring/summary`, `/recurring/by-account`,
`/recurring/{id}` and `/payment-channels`. The list leads with exact next renewal and server monthly/
annual strings; detail leads with context facts, periods, charges and price history. Subscription
series never duplicate in Recurring. Existing `RowActions` and `ConfirmAction` own lifecycle work.

**Major tests:** Preserve current refresh, rename, category, cadence, channel, cancel, resume and
remove behavior. Add a test only if needed to prove subscription exclusion from Recurring or focus
return from a new contextual panel; do not add tests for visual arrangement.

**Focused command:**

```bash
npm --prefix web test -- app/subscriptions/__tests__/subscriptions-screen.test.tsx app/subscriptions/__tests__/subscription-detail.test.tsx app/recurring/__tests__/recurring-screen.test.tsx
```

**Commit:** `feat(web): compact subscription and recurring workflows`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.6 — Investments portfolio console.

---

## Task 4.8.6 — Investments portfolio console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8f-investment-console`

**Base and PR target:** current `develop` after Task 4.8.5 merges; PR to `develop`.

**Outcome:** Apply the compact evidence hierarchy to the portfolio, analysis, allocation and review
queue while preserving explicit valued, unvalued, stale and closed states.

**Files:**

- Modify: `web/app/investments/portfolio-screen.tsx`
- Modify: `web/app/investments/investment-analysis.tsx`
- Modify: `web/app/investments/allocation-evidence-chart.tsx`
- Modify: `web/app/investments/review-queue.tsx`
- Modify: `web/app/investments/__tests__/portfolio-screen.test.tsx`
- Modify: `web/app/investments/__tests__/investment-analysis.test.tsx`
- Modify: `web/app/investments/__tests__/allocation-evidence-chart.test.tsx`
- Modify: `web/app/investments/__tests__/review-queue.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Reuse portfolio summary, positions, analysis and review endpoints exactly. Exact
values, gains, XIRR and allocation coordinates remain server supplied. Keep Record event and Import
evidence as the two primary entry paths. Remove the current duplicate XIRR metric as a related
presentation defect.

**Major tests:** Preserve unavailable-value and return reasons, valued/unvalued/stale counts,
closed history, due-only refresh and stable allocation identity. Pin missing evidence as a gap and
assert XIRR appears once; do not test ordinary CSS.

**Focused command:**

```bash
npm --prefix web test -- app/investments/__tests__/portfolio-screen.test.tsx app/investments/__tests__/investment-analysis.test.tsx app/investments/__tests__/allocation-evidence-chart.test.tsx app/investments/__tests__/review-queue.test.tsx
```

**Commit:** `feat(web): make investments an evidence-first console`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.7 — Holding and investment workflow console.

---

## Task 4.8.7 — Holding and investment workflow console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8g-holding-workflows`

**Base and PR target:** current `develop` after Task 4.8.6 merges; PR to `develop`.

**Outcome:** Bring holding detail, event entry, correction, funding, lot allocation and security
entry into the same compact context-panel and dense-table system.

**Files:**

- Modify: `web/app/investments/holding-screen.tsx`
- Modify: `web/app/investments/entry-screen.tsx`
- Modify: `web/app/investments/security-form.tsx`
- Modify: `web/app/investments/investment-event-form.tsx`
- Modify: `web/app/investments/event-correction-form.tsx`
- Modify: `web/app/investments/funding-panel.tsx`
- Modify: `web/app/investments/lot-allocation-panel.tsx`
- Modify only affected tests under: `web/app/investments/__tests__/`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Do not change investment write bodies or response contracts. The holding page uses a
compact context header, key facts, events, prices, lots and cash links; nested writers use
`ContextPanel`-style presentation while preserving their current review/confirm/idempotency flows.

**Major tests:** Existing tests remain the authority for event pagination, exact price recording,
correction field matrices, funded-event refusal, funding link/unlink, lot allocation, confirmation
and 404/foreign indistinguishability. Add only the critical focus-return test for the new contextual
panel and rerun a nested component's test only if that component changed.

**Focused command:**

```bash
npm --prefix web test -- app/investments/__tests__/holding-screen.test.tsx app/investments/__tests__/entry-screen.test.tsx app/investments/__tests__/investment-event-form.test.tsx app/investments/__tests__/event-correction-form.test.tsx app/investments/__tests__/funding-panel.test.tsx
```

Run the lot-allocation or security-form test file too only when its component changed.

**Commit:** `feat(web): unify holding and investment workflows`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.8 — Salary console.

---

## Task 4.8.8 — Salary console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8h-salary-console`

**Base and PR target:** current `develop` after Task 4.8.7 merges; PR to `develop`.

**Outcome:** Lead Salary with the latest evidenced pay period and earned-versus-received state,
then present analysis, payslips, deposits, deductions and rules as compact evidence/work panels.

**Files:**

- Modify: `web/app/salary/payslips-screen.tsx`
- Modify: `web/app/salary/salary-analysis.tsx`
- Modify: `web/app/salary/__tests__/payslips-screen.test.tsx`
- Modify: `web/app/salary/__tests__/salary-analysis.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Reuse `/payslips` and `/salary/analysis`; values and chart coordinates remain server
supplied. Unmatched credits and unclassified deductions create attention, while record/import,
correction, deposit link/unlink and deduction-rule maintenance retain their existing operations.

**Major tests:** Preserve idempotency, correction, link/unlink and deduction-spending/rule behavior;
keep incomplete pay periods unavailable. Add no styling test.

**Focused command:**

```bash
npm --prefix web test -- app/salary/__tests__/payslips-screen.test.tsx app/salary/__tests__/salary-analysis.test.tsx
```

**Commit:** `feat(web): turn salary into a compact evidence console`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.9 — Plans decision console.

---

## Task 4.8.9 — Plans decision console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8i-plans-console`

**Base and PR target:** current `develop` after Task 4.8.8 merges; PR to `develop`.

**Outcome:** Make Plans match the approved reference: attention and forecast first, then compact
stable sections for monthly review, reserve, goals, commitments and register.

**Files:**

- Modify: `web/app/plans/plans-screen.tsx`
- Modify: `web/app/plans/needs-attention.tsx`
- Modify: `web/app/plans/cash-forecast.tsx`
- Modify: `web/app/plans/forecast-evidence-chart.tsx`
- Modify: `web/app/plans/monthly-review.tsx`
- Modify: `web/app/plans/emergency-reserve.tsx`
- Modify: `web/app/plans/goals.tsx`
- Modify: `web/app/plans/register.tsx`
- Modify: `web/app/plans/settlement-panel.tsx`
- Modify only affected tests under: `web/app/plans/__tests__/`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Reuse existing actions, forecast, monthly review, emergency reserve, goals,
commitments and register endpoints. Preserve mounted section state, URL/local navigation, exact
server strings and forecast coordinates. Settlement/editing stays in contextual panels with
existing confirmation and focus return.

**Major tests:** Run every existing Plans test because the page composes all seven sections. Add a
new test only if the reordering risks draft loss or focus return not already covered. Check diff
size before review; if it approaches the hard stop, finish the coherent attention/forecast and
planning sections and record the exact remainder in this PR rather than bypassing the limit.

**Focused command:**

```bash
npm --prefix web test -- app/plans/__tests__
```

**Commit:** `feat(web): make plans a decision-first console`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.10 — Coverage, Documents and Classify evidence console.

---

## Task 4.8.10 — Coverage, Documents and Classify evidence console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8j-evidence-review-console`

**Base and PR target:** current `develop` after Task 4.8.9 merges; PR to `develop`.

**Outcome:** Apply the compact evidence/review family to the coverage grid, document library and
classification queue without inventing aggregates.

**Files:**

- Modify: `web/app/coverage/coverage-screen.tsx`
- Modify: `web/app/coverage/__tests__/coverage-screen.test.tsx`
- Modify: `web/app/documents/documents-screen.tsx`
- Modify: `web/app/documents/description-repair.tsx`
- Modify: `web/app/documents/__tests__/documents-screen.test.tsx`
- Modify: `web/app/documents/__tests__/description-repair.test.tsx`
- Modify: `web/app/review/classification-review-screen.tsx`
- Modify: `web/app/review/__tests__/classification-review-screen.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Keep Coverage's account-by-month grid primary with accessible cell context;
Documents uses existing type/account/period/import/provenance fields; Classify leads with queue
count and highest-confidence actionable group. Exact evidence and reversible actions remain intact.

**Major tests:** Preserve coverage reasons/acknowledgement, accessible cell naming, document privacy
and repair, classification preview/apply/undo and stale state. Add only a long-label/named-overflow
test where existing coverage is absent.

**Focused command:**

```bash
npm --prefix web test -- app/coverage/__tests__/coverage-screen.test.tsx app/documents/__tests__/documents-screen.test.tsx app/documents/__tests__/description-repair.test.tsx app/review/__tests__/classification-review-screen.test.tsx
```

**Commit:** `feat(web): unify evidence and review workspaces`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.11 — Staged statement and investment import console.

---

## Task 4.8.11 — Staged statement and investment import console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8k-staged-import-console`

**Base and PR target:** current `develop` after Task 4.8.10 merges; PR to `develop`.

**Outcome:** Give statement and investment imports one consistent Upload → Identify/Resolve →
Parse → Review → Commit stage grammar while retaining their separate contracts and recovery.

**Files:**

- Modify: `web/app/import/import-screen.tsx`
- Modify: `web/app/import/account-proposal.tsx`
- Modify: `web/app/import/layout-mapper.tsx`
- Modify: `web/app/import/review-screen.tsx`
- Modify: `web/app/import/__tests__/import-screen.test.tsx`
- Modify: `web/app/import/__tests__/account-proposal.test.tsx`
- Modify: `web/app/import/__tests__/layout-mapper.test.tsx`
- Modify: `web/app/import/__tests__/review-screen.test.tsx`
- Modify: `web/app/investments/import/investment-import-screen.tsx`
- Modify: `web/app/investments/import/__tests__/investment-import-screen.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Preserve multi-file independence, password non-retention, PDF identity evidence,
identifier privacy, layout proposal/remap, no-default selection and commit/cancel confirmation.
Investment import retains blocked-group resolution and resume behavior. The shared stage component
is presentation only and does not merge state machines or request bodies.

**Major tests:** Run every affected existing import test. Add a new test only for a critical stage
navigation or focus behavior introduced by the shared presentation.

**Focused command:**

```bash
npm --prefix web test -- app/import/__tests__ app/investments/import/__tests__/investment-import-screen.test.tsx
```

**Commit:** `feat(web): present imports as staged console workflows`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.12 — Settings and public access console.

---

## Task 4.8.12 — Settings and public access console

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8l-settings-access-console`

**Base and PR target:** current `develop` after Task 4.8.11 merges; PR to `develop`.

**Outcome:** Finish the shared visual system in Settings and public/auth routes without leaking
authenticated chrome or losing unfinished form state.

**Files:**

- Modify: `web/app/settings/settings-screen.tsx`
- Modify: `web/app/settings/household-form.tsx`
- Modify: `web/app/settings/data-quality.tsx`
- Modify: `web/app/settings/categories-manager.tsx`
- Modify: `web/app/settings/rules-manager.tsx`
- Modify: `web/app/settings/merchant-knowledge.tsx`
- Modify only affected tests under: `web/app/settings/__tests__/`
- Modify: `web/app/login/page.tsx`
- Modify: `web/app/unauthorized/page.tsx`
- Modify: `web/app/__tests__/public-pages.test.tsx`
- Modify: `web/app/__tests__/workspace.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Settings opens General with one Save action. General, Data health, Categories, Rules
and Exchange rates retain mounted-state navigation and direct links. A genuine health finding may
surface; loading or unavailable never claims health. Public routes use the sans/blue/neutral identity
without authenticated navigation or service detail.

**Major tests:** Preserve unfinished household draft across section navigation, direct section
links/Back/Forward/focus, long-running FX/rules behavior, owner/logout boundary and public-page
absence of authenticated data. Do not test static styling.

**Focused command:**

```bash
npm --prefix web test -- app/settings/__tests__ app/__tests__/public-pages.test.tsx app/__tests__/workspace.test.tsx
```

**Commit:** `feat(web): finish the compact console across settings and access`

**Final verifier:** `scripts/verify-web.sh`

**Progress next task:** 4.8.13 — Application-wide fidelity audit and owner acceptance.

---

## Task 4.8.13 — Application-wide fidelity audit and owner acceptance

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-8m-console-fidelity`

**Base and PR target:** current `develop` after Task 4.8.12 merges; PR to `develop`.

**Outcome:** Remove cross-screen residuals, prove responsive/accessibility consistency, compare the
four archetypes to the approved HTML and obtain explicit owner visual/UX acceptance.

**Files:**

- Modify only residual shared or screen files proven by the audit
- Modify: `web/app/ui/__tests__/page-grammar.test.tsx` only for genuine shared regressions
- Modify: `web/app/__tests__/workspace.test.tsx` only for genuine shell regressions
- Modify: `web/app/globals.css` to consolidate/remove obsolete rules, not add another override layer
- Modify: `docs/superpowers/progress.md`

**Audit matrix:** Task 4.8.13 confirms and consolidates the per-family visual checks already
completed in Tasks 4.8.1–4.8.12; it is not the first fidelity pass for any route.

- Compare Overview, Accounts, Transactions and Plans with the checked-in HTML at 1440 and 1024 px:
  shell dimensions, white/blue palette, sans typography, title/action alignment, unboxed metric
  strip, module/table density, hairlines, contextual actions and no beige/editorial remnants.
- Inspect every sidebar destination plus valuation, holding, subscription detail, statement review,
  investment entry/import/resume and login/unauthorized routes at 1440, 1024, 588 and 360 px.
- Exercise loading, empty, error, partial and populated states with synthetic data. Check single H1,
  heading order, accessible icon names, keyboard use, focus return, status semantics and named table
  overflow. The document itself must not scroll horizontally.
- Use the approved project UI-automation browser, not the owner's personal browser. Temporary
  captures remain local and contain synthetic data; commit no screenshots.

**Tests and verification:** Do not add broad snapshot or styling tests. Add a shared regression test
only when the audit finds a reusable structural failure. First run the owner preview on the local
branch and consolidate all owner feedback into the implementation. Then run the affected focused
tests, commit, and conduct the one read-only branch review and one repair. Ask the owner to confirm
the resulting reviewed UI is unchanged in intent and accepted; confirmation adds no code. Record
that acceptance in `docs/superpowers/progress.md`, run the documentation checker for that tracker-only
change, commit it, and run the final verifier once. The branch receives no second review. If the owner
does not accept the reviewed UI, do not verify, push, change code or start another review. Stop for
an explicit owner process decision.

**Focused command:** determined only by residual files changed; never the full suite.

**Commit:** `fix(web): close the compact console fidelity audit`

**Final verifier:** `scripts/verify-web.sh` unless a residual API change is explicitly approved, in
which case use `scripts/verify.sh`.

**Progress next task:** None. After owner acceptance and merge, Phase 4.8 is accepted; return to the
next owner-selected roadmap outcome.
