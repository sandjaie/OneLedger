# Phase 4.7 Quiet Editorial Ledger Implementation Plan

> **Superseded on 20 September 2026 after Task 4.7.14.** Task 4.7.15 must not
> resume, and Tasks 4.7.16–4.7.21 must not start. Phase 4.8 owns the replacement UI direction.
> Delivered Phase 4.7 work remains historical implementation evidence; Phase 4.7 did not receive
> owner visual/UX acceptance.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the owner-selected Quiet Editorial Ledger system and decision-first hierarchy to
every OneLedger page while preserving financial behavior and adding only the evidenced read data
the experience requires.

**Architecture:** Establish the editorial tokens, persistent shell and reusable page grammar once,
then convert the application in coherent page families. Each family keeps its domain workflow and
exact financial strings while sharing page identity, primary truth, decision band, evidence and
work-area primitives. One owner-scoped dashboard read extension supplies the Overview commitments
KPI; no other backend work is planned unless a task proves an approved view lacks required data.

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, checked-in Nova/shadcn source
components, Lucide icons, Recharts through shadcn Chart, FastAPI, Pydantic, PostgreSQL `NUMERIC`,
Python `Decimal`, Vitest and pytest.

**Spec:**
[Phase 4.7 owner workspace experience](../specs/2026-09-18-phase-4-7-workspace-experience.md)

## Global constraints

- Workspace for every task: `/Users/sandjaieravi/dev/OneLedger`, active checkout only; no worktree.
- Base and PR target for every task: current `develop`; create the named branch only after the
  preceding task is merged and `develop` is fast-forwarded.
- One implementation writer at a time. Reviewers are read-only. The orchestrator owns branch
  switching, pushes, PRs and merges.
- Preserve every existing owner operation, URL, financial meaning, exact confirmation and recovery
  path. Presentation changes do not authorize new financial writes.
- Money, prices, rates, gains and returns remain PostgreSQL `NUMERIC`, Python `Decimal` and strings
  on the wire. TypeScript does no financial arithmetic.
- New or extended reads require owner authorization, household isolation, explicit currency,
  `asOf`, coverage and missing-data semantics. Unsupported or incomplete values remain visible as
  unavailable; never infer or fabricate them.
- Use semantic design tokens and checked-in shadcn source components. Do not run a wholesale preset
  overwrite, introduce raw one-off colors, or recreate an installed component with custom markup.
- Do not commit real financial values, names, identifiers, filenames, statement content or
  screenshots. UI tests and visual audit fixtures use synthetic data only.
- Every page uses the shared topology but keeps the correct domain work area: tables remain tables,
  review queues remain staged decisions and settings remain forms.
- Run focused tests while implementing. After the local commit, run one read-only review, one
  consolidated repair, and the scope-appropriate verifier once on the final clean head. Use
  `scripts/verify-web.sh` for web/docs-only branches and `scripts/verify.sh` when API code changes.
- Each task PR updates `docs/superpowers/progress.md` with its truthful completed scope and exact
  next task. Automated verification and merge never claim owner visual acceptance.

## Shared implementation contracts

Create focused OneLedger compositions under `web/app/ui/`; they may compose shadcn primitives but
must not hide domain semantics:

```ts
type PageHeaderProps = {
  eyebrow: string;
  title: string;
  description?: string;
  primaryAction?: React.ReactNode;
};

type DecisionBandProps = {
  tone: "neutral" | "attention" | "danger" | "success";
  title: string;
  summary: string;
  action?: React.ReactNode;
  children?: React.ReactNode;
};

type MetricDatum = {
  label: string;
  value: string;
  detail?: string;
};

type MetricStripProps = {
  label: string;
  items: readonly MetricDatum[];
};
```

`PageHeader` owns the visible H1. `DecisionBand` is rendered only for a real status or next action.
`MetricStrip` presents exact server strings and never calculates them. Domain tables, timelines,
forms and record rows remain in their screen modules.

## Review focus

1. A long account, merchant, security or document label must wrap or truncate with an accessible
   name without pushing actions or amounts outside the page at 360px.
2. A page with missing, stale or mixed-currency evidence must state that limitation and must not
   show a confident total, connected chart segment or reassuring empty state.
3. Keyboard navigation must reach the persistent rail, local tabs, row menus, dialogs and the next
   logical control after a dialog closes; visible focus must survive every new composition.
4. Loading, empty, error and populated states must keep the same page identity and must not change
   loading into a false zero or false all-clear.
5. Large record sets and wide tables must remain usable in their named scroll region without page-
   wide horizontal overflow at 360px, 588px, 1024px and 1440px.

---

## 4.7.14 — Editorial foundation, shell and Overview

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-foundation`

**Base and PR target:** current `develop` after this plan is merged; PR to `develop`.

**Outcome:** Establish the final tokens and shared topology, keep the grouped rail visible while
desktop content scrolls, and make Overview the first complete Quiet Editorial Ledger screen.
Replace decorative space with the sourced Upcoming commitments KPI.

**Files:**

- Modify: `web/app/globals.css`
- Modify: `web/app/layout.tsx`
- Modify: `web/app/workspace.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/avatar.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/dropdown-menu.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/scroll-area.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/sidebar.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/table.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/tabs.tsx`
- Create with the shadcn CLI when absent: `web/components/ui/tooltip.tsx`
- Modify if the CLI adds dependencies: `web/package.json`
- Modify if dependencies change: `web/package-lock.json`
- Create: `web/app/ui/page-header.tsx`
- Create: `web/app/ui/decision-band.tsx`
- Create: `web/app/ui/metric-strip.tsx`
- Create: `web/app/ui/__tests__/page-grammar.test.tsx`
- Modify: `web/app/__tests__/workspace.test.tsx`
- Modify: `web/app/overview/overview-screen.tsx`
- Modify: `web/app/overview/__tests__/overview-screen.test.tsx`
- Modify: `api/oneledger/routes/dashboard.py`
- Modify: `api/tests/test_dashboard_live.py`
- Modify: `api/tests/test_dashboard_flows_live.py`
- Regenerate: `web/openapi.json`
- Regenerate: `web/types/api/types.gen.ts`
- Regenerate: `web/types/api/index.ts`
- Modify: `docs/superpowers/progress.md`

**Interfaces:**

- Produces the shared `PageHeader`, `DecisionBand` and `MetricStrip` contracts above.
- Extends `DashboardResponse` with `upcomingCommitments: UpcomingCommitmentsSummary`:

```python
class UpcomingCommitment(BaseModel):
    commitmentId: UUID
    label: str
    dueDate: date
    nativeAmount: str
    nativeCurrency: str
    remainingBase: str | None
    remainingSecondary: str | None

class UpcomingCommitmentsSummary(BaseModel):
    asOf: date
    through: date
    count: int
    totalBase: str | None
    totalSecondary: str | None
    missingBaseCurrencies: list[str]
    missingSecondaryCurrencies: list[str]
    next: UpcomingCommitment | None
    items: list[UpcomingCommitment]
```

The interval is inclusive from `asOf` through `asOf + 30 days`. Include only open, non-archived
occurrences and use their remaining unpaid amount. Each reporting-currency total is null when any
included amount lacks the required rate; never return a partial total. Order items by due date,
then label and stable identifier.

- [ ] Add live API cases for no commitments, an inclusive day-30 occurrence, overdue exclusion,
  archived/closed exclusion, partly paid remaining amount, exact decimal conversion, mixed-
  currency missing-rate null totals, foreign-household exclusion and member refusal.
- [ ] Run the named dashboard tests and confirm the new assertions fail because
  `upcomingCommitments` is absent.
- [ ] Implement the query and Pydantic response using the existing commitment-progress and
  translation helpers; keep the read side-effect free.
- [ ] Regenerate the OpenAPI client and run the dashboard API tests until green.
- [ ] Run `npx shadcn@latest docs avatar dropdown-menu scroll-area sidebar table tabs tooltip`,
  inspect the current Base UI APIs, and add only the absent listed components with
  `npx shadcn@latest add`; review every generated source file before composing it.
- [ ] Add component tests for the shared page grammar, sticky desktop rail, labelled mobile sheet,
  long-label containment, decision-band omission and keyboard-visible actions.
- [ ] Replace the shell and Overview composition with the editorial hierarchy: net worth and
  twelve-month trend, decision band, cash flow, account composition, recent activity and upcoming
  commitments. Preserve coverage warnings, cards due and exact-value trend table.
- [ ] Test Overview empty, loading, error, complete, incomplete-rate and no-commitment states at the
  component boundary; assert financial strings are rendered unchanged.
- [ ] Run focused API and web tests, update progress to 4.7.14 implemented with 4.7.15 next, commit,
  complete review/repair, run `scripts/verify.sh` once, then push and open the PR.

**Acceptance:** Overview matches the selected editorial reference without decorative artwork;
the rail remains available during long-page scrolling; an incomplete commitment conversion cannot
look complete; all former Overview links and evidence remain reachable.

## 4.7.15 — Financial position: Accounts and Wealth

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-position`

**Base and PR target:** current `develop` after 4.7.14; PR to `develop`.

**Outcome:** Apply the position archetype to Accounts and Wealth, including their valuation and
month-detail workflows.

**Files:**

- Modify: `web/app/accounts/accounts-screen.tsx`
- Modify: `web/app/accounts/valuation-panel.tsx`
- Modify: `web/app/accounts/__tests__/accounts-screen.test.tsx`
- Modify: `web/app/accounts/__tests__/valuation-panel.test.tsx`
- Modify: `web/app/wealth/wealth-screen.tsx`
- Modify: `web/app/wealth/__tests__/wealth-screen.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Consumes 4.7.14's page grammar. Produces no API change; existing account groups,
valuation rows, wealth history, positions and attribution remain authoritative.

- [ ] Add failing component assertions for the page header, primary position, evidence state,
  compact account rows, contextual row actions, incomplete-month gap and named table scroll region.
- [ ] Recompose Accounts into position totals, attention state, role-grouped open accounts and a
  disclosed closed-account section. Keep add/edit/close/reopen/reveal/valuation behavior unchanged.
- [ ] Recompose the valuation panel into context facts, evidence timeline, add-preview-confirm flow
  and valuation-mode maintenance with focus return.
- [ ] Recompose Wealth into selected month-end position, completeness, trend, positions and
  attribution. Preserve exact values, missing periods, refresh and month selection.
- [ ] Exercise long names and 360px/588px overflow in component tests, run affected tests, update
  progress to 4.7.15 implemented with 4.7.16 next, commit, review/repair, run
  `scripts/verify-web.sh` once, then push and open the PR.

**Acceptance:** Accounts compare cleanly without repeated buttons; Wealth communicates what is
known and missing before methodology; every account and month action still works.

## 4.7.16 — Activity: Transactions, Spending, Subscriptions and Recurring

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-activity`

**Base and PR target:** current `develop` after 4.7.15; PR to `develop`.

**Outcome:** Establish the dense activity grammar for ledger rows, comparison charts, renewal
timelines and recurring-series management.

**Files:**

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
- Modify: `web/app/subscriptions/subscriptions-screen.tsx`
- Modify: `web/app/subscriptions/subscription-detail.tsx`
- Modify: `web/app/subscriptions/__tests__/subscriptions-screen.test.tsx`
- Modify: `web/app/subscriptions/__tests__/subscription-detail.test.tsx`
- Modify: `web/app/recurring/recurring-screen.tsx`
- Modify: `web/app/recurring/__tests__/recurring-screen.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Consumes the shared page grammar and existing exact server responses. Produces a
reusable dense-row and contained-table styling contract in `globals.css`; no new financial
calculation is introduced.

- [ ] Add failing tests for compact filter disclosure, URL-backed filters, named table scrolling,
  row-menu keyboard access, long descriptions, refunds, missing chart coordinates, ended series
  and subscription/recurring separation.
- [ ] Recompose Transactions around its ledger table and unresolved-work decision band. Preserve
  record, edit, pagination, classification and transfer behavior.
- [ ] Recompose Spending around selected-range truth, budget state, bucket/category evidence and
  exact tables. Keep stable category colors and links to filtered Transactions.
- [ ] Recompose Subscriptions around next renewal, sourced totals, cadence timeline and compact
  Current/Upcoming/Past records; convert repeated management buttons to labelled row menus.
- [ ] Recompose subscription detail into facts, billing timeline, price history, source evidence
  and one management panel; retain exact lifecycle confirmations.
- [ ] Recompose Recurring around next payment and Active/Ended records while preserving rename,
  cadence, cancel, resume and delete flows.
- [ ] Run affected tests, update progress to 4.7.16 implemented with 4.7.17 next, commit,
  review/repair, run `scripts/verify-web.sh` once, then push and open the PR.

**Acceptance:** Dense activity is faster to scan, no table causes page-wide overflow, charts keep
exact alternatives and no subscription appears in Recurring.

## 4.7.17 — Investments and investment workflows

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-investments`

**Base and PR target:** current `develop` after 4.7.16; PR to `develop`.

**Outcome:** Give portfolio status, evidence quality, analysis, holdings, events, lots and import
their own clear layers without changing investment semantics.

**Files:**

- Modify: `web/app/investments/portfolio-screen.tsx`
- Modify: `web/app/investments/investment-analysis.tsx`
- Modify: `web/app/investments/allocation-evidence-chart.tsx`
- Modify: `web/app/investments/review-queue.tsx`
- Modify: `web/app/investments/holding-screen.tsx`
- Modify: `web/app/investments/entry-screen.tsx`
- Modify: `web/app/investments/investment-event-form.tsx`
- Modify: `web/app/investments/event-correction-form.tsx`
- Modify: `web/app/investments/funding-panel.tsx`
- Modify: `web/app/investments/lot-allocation-panel.tsx`
- Modify: `web/app/investments/security-form.tsx`
- Modify: `web/app/investments/wealth-refresh-notice.tsx`
- Modify: `web/app/investments/import/investment-import-screen.tsx`
- Modify: the corresponding files under `web/app/investments/**/__tests__/`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Consumes existing portfolio, analysis, review, holding, event, funding, lot and
import responses unchanged. Exact financial values stay server strings.

- [ ] Add failing tests for valued/unvalued/closed separation, missing benchmark, stale valuation,
  open-review priority, allocation overflow, long security names, event paging and import-stage
  persistence.
- [ ] Recompose portfolio summary, decision band, returns/allocation evidence, confidence and
  holdings. Preserve both real entry routes and explicit empty state.
- [ ] Recompose holding detail around key facts and evidence timeline, with event correction,
  funding, prices and lot allocation in contextual panels.
- [ ] Recompose manual entry as instrument-resolution, acquisition, optional dated-price and
  confirmation stages without weakening fail-closed cases.
- [ ] Recompose investment import as a persistent staged journey with unresolved groups first and
  commit/cancel consequences explicit.
- [ ] Run affected tests, update progress to 4.7.17 implemented with 4.7.18 next, commit,
  review/repair, run `scripts/verify-web.sh` once, then push and open the PR.

**Acceptance:** The first viewport separates value, evidence and review work; unsupported value,
cost basis, gain or return never appears inferred; all event and import actions survive.

## 4.7.18 — Planning: Salary, Plans and Coverage

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-planning`

**Base and PR target:** current `develop` after 4.7.17; PR to `develop`.

**Outcome:** Apply the mixed planning archetype to compensation evidence, commitments, forecast,
goals, reserve, monthly review, register and history coverage.

**Files:**

- Modify: `web/app/salary/payslips-screen.tsx`
- Modify: `web/app/salary/salary-analysis.tsx`
- Modify: `web/app/salary/__tests__/payslips-screen.test.tsx`
- Modify: `web/app/salary/__tests__/salary-analysis.test.tsx`
- Modify: `web/app/plans/plans-screen.tsx`
- Modify: `web/app/plans/*.tsx`
- Modify: `web/app/plans/__tests__/*.test.tsx`
- Modify: `web/app/coverage/coverage-screen.tsx`
- Modify: `web/app/coverage/__tests__/coverage-screen.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Consumes existing salary, forecast, action, review, reserve, goal, commitment,
register and coverage responses. Confirmed and projected values remain distinct.

- [ ] Add failing tests for latest-pay evidence, unmatched credits, unclassified deductions,
  confirmed/projected distinction, no-open-commitment state, overdue priority, coverage-grid
  keyboard access and long labels at 360px.
- [ ] Recompose Salary into latest evidence, decision band, analysis and compact payslip records;
  separate manual entry, PDF import and rule maintenance into labelled workflows.
- [ ] Recompose Plans into nearest commitment, forecast and stable local sections for actions,
  monthly review, reserve, goals, commitments and register. Keep section links and unsaved state.
- [ ] Recompose Coverage around missing/partial status and its account-by-month evidence grid;
  preserve acknowledgements and cell detail without inventing a percentage score.
- [ ] Run affected tests, update progress to 4.7.18 implemented with 4.7.19 next, commit,
  review/repair, run `scripts/verify-web.sh` once, then push and open the PR.

**Acceptance:** Planning pages foreground the next real task, long forms no longer dominate the
first viewport, and projections can never be mistaken for facts.

## 4.7.19 — Intake and review: Statements, Classify and Documents

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-intake`

**Base and PR target:** current `develop` after 4.7.18; PR to `develop`.

**Outcome:** Make ingestion and review flows visibly staged, recoverable and evidence-led while
keeping destructive boundaries explicit.

**Files:**

- Modify: `web/app/import/import-screen.tsx`
- Modify: `web/app/import/account-proposal.tsx`
- Modify: `web/app/import/layout-mapper.tsx`
- Modify: `web/app/import/review-screen.tsx`
- Modify: `web/app/import/**/__tests__/*.test.tsx`
- Modify: `web/app/review/classification-review-screen.tsx`
- Modify: `web/app/review/__tests__/classification-review-screen.test.tsx`
- Modify: `web/app/documents/documents-screen.tsx`
- Modify: `web/app/documents/description-repair.tsx`
- Modify: `web/app/documents/__tests__/*.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Consumes existing parse, batch review, classification and document contracts.
Produces a shared visual stepper convention in domain CSS; it does not merge the distinct import
state machines.

- [ ] Add failing tests for stage identity, blocked commit, grouped row decisions, selection state,
  destructive cancel confirmation, confidence/evidence placement, reversible classification,
  document-filter disclosure and long private-safe display labels.
- [ ] Recompose statement import around the Upload–Identify–Parse–Review–Commit journey and current
  file progress; retain account proposal and layout mapping as explicit substeps.
- [ ] Recompose batch review around exact totals, blocking decisions, grouped rows and sticky batch
  actions without hiding row provenance.
- [ ] Recompose Classify around queue state, one featured actionable group, evidence/scope and
  recent reversible changes.
- [ ] Recompose Documents around records needing action, compact library rows and visible primary
  filters; keep repair and maintenance contextual.
- [ ] Run affected tests, update progress to 4.7.19 implemented with 4.7.20 next, commit,
  review/repair, run `scripts/verify-web.sh` once, then push and open the PR.

**Acceptance:** The owner always knows the current stage, what blocks progress and what commit or
cancel will do; no raw enum or private identifier becomes presentation copy.

## 4.7.20 — Settings, public states and remaining route consistency

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/p4-7-editorial-administration`

**Base and PR target:** current `develop` after 4.7.19; PR to `develop`.

**Outcome:** Complete the editorial system in Settings, login, unauthorized, loading, empty and
error states, and remove superseded page-level styling left by earlier conversions.

**Files:**

- Modify: `web/app/settings/settings-screen.tsx`
- Modify: `web/app/settings/household-form.tsx`
- Modify: `web/app/settings/data-quality.tsx`
- Modify: `web/app/settings/categories-manager.tsx`
- Modify: `web/app/settings/rules-manager.tsx`
- Modify: `web/app/settings/merchant-knowledge.tsx`
- Modify: `web/app/settings/__tests__/*.test.tsx`
- Modify: `web/app/login/page.tsx`
- Modify: `web/app/unauthorized/page.tsx`
- Modify: `web/app/__tests__/public-pages.test.tsx`
- Modify: `web/app/globals.css`
- Modify: `docs/superpowers/progress.md`

**Interfaces:** Consumes the shared page grammar and current settings section-navigation contract.
Produces no API change.

- [ ] Add failing tests for General-first rendering, hash/Back/Forward navigation, preserved
  unsaved form state, focus movement, real data-health findings, unavailable-health honesty,
  section keyboard use and public-shell privacy.
- [ ] Recompose Settings into one-at-a-time General, Data health, Categories, Rules and Exchange
  rates workspaces; keep sections mounted and one Save action for household fields.
- [ ] Put diagnostics, rule analytics, starter packs and FX maintenance behind clear local context
  rather than presenting them as equal introductory cards.
- [ ] Bring Login and Unauthorized into the same type, color and control system without rendering
  private navigation, household state or owner information.
- [ ] Remove only CSS selectors proven unused after all screen-family conversions; keep shared
  semantic tokens and responsive contracts centralized.
- [ ] Run affected tests and the full web test suite, update progress to 4.7.20 implemented with
  4.7.21 next, commit, review/repair, run `scripts/verify-web.sh` once, then push and open the PR.

**Acceptance:** Settings opens with a clear editable purpose, switching sections never loses work,
and every public or exceptional state visibly belongs to OneLedger without leaking private state.

## 4.7.21 — Cross-screen visual, interaction and acceptance audit

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `fix/p4-7-editorial-audit`

**Base and PR target:** current `develop` after 4.7.20; PR to `develop`.

**Outcome:** Inspect every destination and detail route as one product, fix blocking inconsistencies
once, and hand the verified development stack to the owner for final visual and UX acceptance.

**Files:**

- Modify only the web modules and tests required by findings from this audit.
- Modify: `docs/superpowers/progress.md`
- Create: `docs/superpowers/reports/2026-09-<day>-phase-4-7-editorial-audit.md`

**Interfaces:** No new product interface is planned. Any discovered need for financial or
authorization behavior is out of scope for an audit fix and must return to a planned API task.

- [ ] Build a temporary untracked synthetic fixture harness using the screens' typed request
  contracts. Never point the audit browser at the working ledger and never commit its screenshots.
- [ ] Inspect Overview, Accounts, Transactions, Spending, Subscriptions, Recurring, Investments,
  Salary, Plans, Wealth, statement import, investment import, Classify, Documents, Coverage and
  Settings plus account valuation, holding, subscription and import-review detail states.
- [ ] For representative populated, empty, loading and error states, inspect 360px, 588px, 1024px
  and 1440px for persistent navigation, first-viewport hierarchy, long labels, table containment,
  keyboard path, focus return, selected state, contrast and reduced motion.
- [ ] Record one deduplicated finding set in the dated report. Fix CRITICAL/HIGH findings and small
  related MEDIUM findings in one consolidated repair; record nonblocking remainder in PR Notes.
- [ ] Run affected tests once after repair. Update progress to state that 4.7.14–4.7.21
  implementation is complete and owner acceptance is open; do not claim acceptance.
- [ ] Commit the repaired head, run `scripts/verify-web.sh` once, push unchanged, open the PR and
  merge only after its required checks and review are green.
- [ ] Start the development stack on the merged `develop` head and ask the owner to inspect the
  complete application. Only a later owner confirmation may mark Phase 4.7 accepted.

**Acceptance:** Every route uses the shared topology and editorial system, every existing action
remains reachable, no supported viewport has page-wide overflow, and the exact verified build is
ready for owner visual/UX acceptance.
