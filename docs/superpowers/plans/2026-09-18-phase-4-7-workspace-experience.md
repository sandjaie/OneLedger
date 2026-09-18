# Phase 4.7 owner workspace experience implementation plan

**Goal:** Convert the existing owner workspace to one coherent Nova/shadcn visual system,
improve decisions and interactions on every screen, and add useful, evidenced charts. Invite
owner acceptance only after the cross-screen audit.

**Design:**
[Phase 4.7 owner workspace experience](../specs/2026-09-18-phase-4-7-workspace-experience.md).
The owner's `b2YOAa` preset (Nova, green/neutral, Inter, Lucide) is the starting template, not a
pixel specification. Integrate its source components and tokens by controlled merge into the
existing Next.js app. Task 4.7.1's page grammar is transitional; retain its behavior while
replacing its styling and general-purpose controls. This plan supersedes the remaining-task
instructions in the [earlier decision-first plan](2026-09-17-decision-first-workspace.md).
Use that plan's endpoint, call-order, action, and screen-state inventories where not overridden
here. The [earlier refresh plan](2026-09-17-workspace-experience-refresh.md) is historical.

**Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS 4, shadcn/ui source components,
Recharts through shadcn Chart, existing authenticated Python API, PostgreSQL `NUMERIC` and
Python `Decimal`.

**Workspace for every task:** `/Users/sandjaieravi/dev/OneLedger`, active checkout only;
no worktree and one implementation writer.

**Base and PR target for every task:** current `develop` after the immediately preceding task
has merged; target `develop`. Check the clean tree and `origin/develop`, fast-forward `develop`,
then create the exact branch below. The orchestrator owns branches, pushes, PRs, and merges.
These 4.7.x IDs are work packages within one Phase 4.7 outcome, not new roadmap phases.

## Contracts for every work package

- Convert the assigned screens and detail routes to Nova/shadcn for general-purpose controls,
  surfaces, navigation, feedback, overlays, and data presentation. Keep domain-specific record
  compositions, semantic HTML, authenticated `Workspace.request`, exact financial strings,
  existing URL/endpoint/request order, and owner actions. Do not change a financial meaning to
  satisfy a visual layout.
- Preserve every loading, unknown, empty, error, correction, confirmation, provenance, coverage,
  and recovery path. Destructive-dialog cancel sends no request and returns focus. The desktop
  rail stays visible while scrolling; mobile navigation is dismissible and keyboard-operable.
- Financial values stay `NUMERIC`/`Decimal` server-side and strings on the wire. The browser
  formats but never parses them into floats or recalculates money, prices, rates, gains, or
  returns. New charts use bounded server-derived integer plotting coordinates or nonfinancial
  counts, exact strings for labels and tables, and null gaps for incomplete evidence. Any new
  API read field/route needs owner authorization, household isolation, Decimal tests, and the
  full verifier. Do not add a financial write or invented history.
- Establish semantic action/status colors separately from 10–12 categorical chart colors.
  Keep a category's mapping stable across screens, even when chart order changes. Use labels,
  values, and line styles as well as color; group overflow explicitly. A chart must answer a
  real comparison or trend question and have an accessible exact-value fallback.
- Use synthetic tests and structural live observations only. Never put real figures, document
  names, identifiers, or screenshots in code, fixtures, commits, logs, or agent messages.
  Check desktop, 588px, and 360px, including focus, reduced motion, and no page-wide overflow.
- Run affected tests while implementing. Keep one task `db-test` server for all database checks
  if an API contract changes. Commit locally, obtain one read-only review, consolidate repair,
  then run `scripts/verify-web.sh` on the clean intended head for web-only work or
  `scripts/verify.sh` if API/database paths changed. An unchanged exact-head receipt precedes
  first push and PR. Observe the branch diff budget in `docs/git-workflow.md`; split an
  oversized coherent remainder into the next numbered package rather than weakening checks.

## 4.7.1 — Shared page grammar (implemented; PR pending)

**Branch:** `feature/ui-page-grammar`. **Base and PR target:** `develop` → `develop`.

The branch supplies the grouped rail, page grammar, `SectionNav`, `RowActions`, and initial
Coverage/Wealth/Spending adoption. It was reviewed, repaired, web-verified, and pushed. The
current canonical spec and plan update rides on this branch. Open and merge its PR before
4.7.2. Do not redo its behavior; its CSS and custom general-purpose controls may be replaced
by the Nova conversion.

## 4.7.2 — Nova foundation, shell, and component contracts

**Branch:** `feature/p4-7-nova-foundation`. **Dependency:** 4.7.1 merged.

**Files/modules:** `web/components.json`, `web/package.json` and lockfile, `web/app/globals.css`,
`web/app/workspace.tsx`, shared UI components and tests. Inspect the `b2YOAa` preset output
before merging configuration, CSS, dependencies, and source; do not overwrite app files.
Establish Nova typography, surface, spacing, radius, semantic colors, 10–12 category/chart
tokens, stable category-color selection, Chart wrapper, and responsive chart/table frame.
Convert shell navigation, menus, buttons, status/empty/loading surfaces, and Task 1's
`SectionNav`/`RowActions` behavior to appropriate shadcn primitives. Remove superseded shared
CSS only after consuming screens render correctly.

**Proof:** Shell/navigation tests cover sticky rail, active item, deep link, mobile dismissal,
focus return, skip link, and stable loading frame. Component tests cover deterministic category
color, overflow grouping, chart gaps, exact-value fallback, and reduced motion. Inspect the
generated source and bundle; run affected web tests and live checks at three widths. No chart
is populated with fabricated data in this foundation package.

## 4.7.3 — Settings architecture

**Branch:** `feature/p4-7-settings`. **Dependency:** 4.7.2 merged.

**Files/modules:** `web/app/settings/`, Settings content currently in `web/app/workspace.tsx`,
related sections, CSS, and tests. Move the screen into a bounded module. Use Nova fields,
grouped controls, section navigation, alerts, and disclosures for General, Data health,
Categories, Rules, and Exchange rates. General opens first with one Save. Keep sections mounted
while only one long section is visible; hash/deep links, Back/Forward, heading focus, and
unsaved drafts survive. A loading/unavailable health response never reads clean. Preserve
validation, reporting-pair, FX actions, and incoming links. This screen needs no decorative
chart.

**Proof:** Settings/Workspace tests for links, navigation, focus, draft, validation, and true
versus unknown health; live edit/error/narrow-width states. Keep the earlier Task 2 endpoint
and request contracts.

## 4.7.4 — Subscriptions and Recurring

**Branch:** `feature/p4-7-recurring`. **Dependency:** 4.7.3 merged.

**Files/modules:** `web/app/subscriptions/`, `web/app/recurring/`, detail routes, screen CSS,
and tests. Use Nova record items/cards, status badges, tabs/filters, and AlertDialog where a
destructive action needs confirmation. Lead with the next real dated item or truthful absence;
make Current/Upcoming/Past and Active/Ended scannable. Preserve all payment source,
provenance, correction, lifecycle, and detail actions. An ended item never reads Active.
Keep exact existing confirmation text and request bodies. Add price-history visualization
only if an authenticated evidenced series already exists; otherwise omit it.

**Proof:** Affected Subscriptions/Recurring/Transactions tests, including dialog copy,
keyboard cancel/focus return, no POST on cancel, and unchanged endpoints; live dense, ended,
empty, and error states at three widths.

## 4.7.5 — Documents and Classify

**Branch:** `feature/p4-7-documents-classify`. **Dependency:** 4.7.4 merged.

**Files/modules:** `web/app/documents/`, `web/app/review/`, related CSS and tests. Present
document type, account, period, import state, and next action before provenance. Keep Type and
Account filters visible; collapse advanced filters only when needed. Preserve Original,
Transactions, Review, and Re-read conditions. Classify retains evidence, confidence,
reversibility, and recovery in the common decision/record hierarchy. Humanize enums. Convert
controls and feedback to Nova; do not add decorative charts.

**Proof:** Affected Documents/Classify tests for exact query order and actions; live dense,
empty, unavailable, and narrow states. Keep the earlier Task 4 API contract.

## 4.7.6 — Investments and investment import

**Branch:** `feature/p4-7-investments`. **Dependency:** 4.7.5 merged.

**Files/modules:** portfolio, investment analysis/import screens and detail routes, relevant
API read response if plotting fields are absent, related tests and CSS. Separate holdings,
valuation confidence, review work, and analysis. When no holdings exist, show the two real
entry routes. Use Nova charts for evidenced monthly return versus benchmark and allocation;
show exclusions, methodology, exact-value tables, and gaps for unsupported/unvalued periods.
Keep import review and commit steps explicit. No implied valuation or return.

**Proof:** Investment screen tests for valued/unvalued/empty and import-review paths; chart
tests for benchmark absence, gaps, allocation overflow, and exact values. If the API changes,
test owner/household refusal and Decimal-to-coordinate derivation with the one test server,
then use the full verifier; otherwise use the web verifier.

## 4.7.7 — Overview and ledger destinations

**Branch:** `feature/p4-7-ledger`. **Dependency:** 4.7.6 merged.

**Files/modules:** Overview, Accounts, Transactions, Import and relevant detail routes,
tests/CSS, plus read-only chart response fields if necessary. Preserve strong existing
summaries, filters, imports, and reconciliation. Add Overview's evidenced twelve-month
income/spending/net trend with reporting-currency context, explicit unknown months, and its
exact-value table. Make the first true state and action clear without hiding dense ledger
data or changing fetch order.

**Proof:** Affected screen tests for source series, chart gaps and exact values, request order,
import review, and actions; live populated/empty/error and narrow states. API read-contract
changes trigger authorization/household/Decimal tests and the full verifier.

## 4.7.8 — Spending and Plans

**Branch:** `feature/p4-7-spending-plans`. **Dependency:** 4.7.7 merged.

**Files/modules:** `web/app/spending/`, `web/app/plans/`, relevant tests/CSS and read-only
plotting fields if required. Convert Spending's evidenced category/bucket comparison to the
Nova chart system with stable category colors and exact figures; keep refunds/negative net
distinguishable. Plans gains a confirmed-versus-projected cash-forecast chart by the supported
currency/account context, without mixing currencies or presenting projection as fact. Preserve
budgets, goals, filters, forecast, uncertainty, confirmations, and existing links.

**Proof:** Affected tests for category identity/order, refund signs, mixed-currency refusal,
missing forecast points, series labels, exact table, and existing actions. Live positive,
empty, incomplete, and 360px states; API changes use the full verifier and one test server.

## 4.7.9 — Salary, Coverage, and Wealth

**Branch:** `feature/p4-7-salary-wealth`. **Dependency:** 4.7.8 merged.

**Files/modules:** `web/app/salary/`, `web/app/coverage/`, `web/app/wealth/`, relevant detail
routes, tests/CSS, and read-only plotting fields if required. Convert Wealth's month-end net
worth history to Nova Chart with incomplete months as gaps. Salary gains earned-versus-received
history only where its authenticated response establishes both series, with exact values and
unmatched credits clearly separated. Coverage uses decision-first evidence/status surfaces,
not a decorative graph. Preserve salary, coverage, and wealth workflows and caveats.

**Proof:** Affected tests for reconciliation, incomplete months, gaps, series labels, exact
tables, and links/actions; live positive/empty/incomplete states at three widths. API changes
use the full verifier and one test server.

## 4.7.10 — Cross-screen visual and UX audit

**Branch:** `fix/p4-7-workspace-audit`. **Dependency:** 4.7.1–4.7.9 merged.

**Files/modules:** only screens, shared UI/CSS, and tests implicated by evidenced findings.
Inspect every sidebar destination and relevant detail route at desktop, 588px, and 360px for
first-viewport clarity, Nova consistency, chart usefulness, category-color stability,
rail/menu persistence, keyboard/focus, reduced motion, loading/empty/error recovery, overflow,
and exact-value alternatives. Record structural observations only, never real ledger contents.
Repair confirmed defects in one coherent branch; no new financial feature or fabricated chart.

**Proof:** Affected tests and the scope-appropriate exact-head verifier after review/repair.
Push/PR to `develop`. After merge, invite owner assessment in the running application;
engineering verification does not claim owner visual acceptance.
