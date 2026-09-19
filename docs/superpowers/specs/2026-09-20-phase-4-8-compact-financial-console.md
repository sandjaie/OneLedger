# Phase 4.8 — compact financial console

Revision: 20 September 2026 — owner-approved visual direction.

## Authority and supersession

This specification owns the authenticated OneLedger workspace experience after Phase 4.7.14.
The owner inspected the delivered Quiet Editorial Ledger foundation and rejected its warm beige,
serif-led, low-density direction. Phase 4.7.1–4.7.14 remain historical implementation evidence,
but did not receive owner visual/UX acceptance. Task 4.7.15 was stopped; Tasks 4.7.16–4.7.21 must
not start. Their replacement work belongs to Phase 4.8.

The owner approved the four-screen
[Compact Financial Console reference](../../product/mockups/CompactFinancialConsole.html) on
20 September 2026. That checked-in HTML uses synthetic values and is the durable visual reference
for Overview, Accounts, Transactions and Plans. It is not production code and must not be imported
into the application.

This specification and the approved HTML are a joint contract:

- this specification owns behavior, financial meaning, data availability, accessibility,
  responsive rules and the application-wide screen outcomes;
- the HTML owns the selected visual language, topology, relative density, hierarchy, color,
  typography and alignment of its four representative screens;
- production may adapt the reference for real content, loading, empty, error and narrow-screen
  states, but must not replace the approved direction with another aesthetic or component layout;
- a visible deviation that is not required by real data, accessibility or responsive reflow must
  be identified before implementation and approved by the owner. A PR note after the fact is not
  approval.

The implementation target is the running application, not a loose interpretation of a mockup.
Matching the approved topology is part of correctness.

## Product outcome

OneLedger becomes a compact financial operating console: white working surfaces, cool-gray
structure, a restrained blue interaction accent, modern sans-serif typography, persistent grouped
navigation, dense evidence tables and small decision-oriented modules. Each screen answers three
questions quickly: what is true, what needs attention, and what can the owner do next?

The application must feel like one product across every authenticated route. It must not contain
parallel visual systems where the first pages use the new console and later pages retain beige
editorial cards, oversized copy, promotional surfaces or legacy form layouts.

## Non-goals

- This phase does not change financial meaning merely to fill a chart or KPI.
- It does not invent prices, valuations, transactions, cost basis, gains, returns, dates, coverage
  or reassuring clean states.
- It does not move financial arithmetic into TypeScript. Money, prices, rates, gains and return
  inputs remain PostgreSQL `NUMERIC`, Python `Decimal` and strings on the wire.
- It does not add financial recommendations, trade suggestions or rebalancing advice.
- It does not require every page to use three columns. Domain work remains a ledger, timeline,
  queue, form or staged flow as appropriate.
- It does not reproduce the reference application's brand, labels or workflows. The screenshot
  supplied by the owner established layout and density only; OneLedger keeps its own identity and
  financial contracts.

## Approved visual system

### Color

The authenticated light theme uses these reference tokens. Semantic dark-theme equivalents may
be added only if they preserve the same hierarchy; light theme is the acceptance baseline.

| Purpose | Reference value | Rule |
| --- | --- | --- |
| Application and module surface | `#FFFFFF` | The financial workspace is white, not beige or tinted parchment |
| Outer/background wash | `#EEF1F5` | Used outside the app frame or for restrained neutral separation |
| Soft row/control surface | `#F6F8FB` | Hover, inactive structure and quiet secondary areas |
| Active navigation surface | `#EEF5FF` | Blue-tinted active state only |
| Primary text | `#161B26` | Titles, values and primary labels |
| Secondary text | `#697386` | Supporting copy, axes and timestamps |
| Hairline | `#E5E9F0` | Dividers, table rules and module boundaries |
| Strong hairline/input | `#D4DBE5` | Controls and the outer app frame |
| Primary interaction | `#1261C9` | Main action, links, focus and primary chart series |
| Primary hover | `#084FAE` | Filled-action hover/pressed state |
| Success | `#13835F` | Positive or complete state only |
| Attention | `#B66B0A` | Review-needed state only |
| Danger | `#C43D4D` | Error, destructive or negative exception only |
| Secondary series | `#4BA4CA`, `#7656C9` | Persistent data identity, never decoration |

Category colors continue to use the existing stable semantic palette where a category identity is
already stored. Semantic state color and chart-series color remain separate. Text and icons must
not depend on color alone.

### Typography

- Use Inter when locally available, followed by the system sans-serif stack. Do not use a display
  serif in the authenticated workspace.
- Desktop page titles are compact, approximately 28–30 px, semibold and tightly tracked. They are
  not billboard headings.
- Module headings are approximately 15–16 px and semibold. Body text is 13–14 px. Supporting
  labels are 11–12 px with sufficient contrast.
- Use only the weight needed to distinguish hierarchy. Avoid decorative italics, all-caps prose
  and oversized financial numbers.
- Financial values and aligned numeric columns use tabular figures.

### Geometry and density

- Desktop sidebar reference width: 218 px. Top bar reference height: 70 px. Desktop page padding:
  28–30 px. These may change at breakpoints, not arbitrarily per screen.
- Controls use compact 36–39 px desktop heights, 7–8 px radii and one-pixel borders. Touch targets
  expand at coarse-pointer and narrow breakpoints without inflating desktop density.
- Module groups use whitespace and hairline separators before boxes. A real alert, bounded form,
  menu, dialog or interactive panel may use a restrained surface; ordinary information does not
  become a grid of rounded cards.
- Shadow is reserved for the outer preview/application frame, overlays and menus. The production
  content area does not apply shadows to every module.
- A desktop dashboard may use three evidence columns. Tables and record lists use compact rows and
  horizontal rules without vertical cell grids.

### Explicit removals

The implementation removes or replaces these Phase 4.7 treatments throughout the application:

- pale beige/parchment page backgrounds;
- forest-led interaction colors and the editorial serif hierarchy;
- large introductory copy blocks, eyebrows and motivational language;
- promotional or lifestyle artwork in financial working space;
- giant hero cards and repeated rounded KPI cards;
- low-density sections where exact rows, a chart or an action would communicate more clearly;
- page-specific visual systems that make later sidebar destinations appear unfinished.

## Application shell and topology

### Desktop

The authenticated shell has a persistent white sidebar separated by a hairline, a white top bar,
and a white content workspace. The sidebar stays visible while content scrolls. Navigation is
grouped by the current OneLedger information architecture and uses Lucide icons plus text labels.
The active destination uses the blue-tinted selection treatment. Settings stays at the lower
workspace boundary.

The top bar contains a compact search entry, workspace/reporting context and the existing safe
owner utilities. Reporting-currency controls remain reachable without dominating the page. Search
is a real control, not decorative chrome; its contract is defined under Backend read contracts.

Every page then uses this ordered grammar:

1. compact title, at most one useful sentence and page-level actions aligned on the same row;
2. optional local filter/action row;
3. one unboxed three-column metric strip when exact values materially orient the page;
4. one restrained attention row when a real unresolved item exists;
5. dense domain work: modules, charts, tables, timelines, queues or forms;
6. progressive detail and infrequent maintenance actions.

A layer is omitted when unsupported; empty space is not filled with invented metrics.

### Responsive shell

- At wide desktop, modules align to the reference grid and the sidebar remains persistent.
- At 1024 px, three-column modules may become two columns and page actions may wrap without
  covering the title or content.
- At 588 px and below, navigation becomes a compact sheet or bottom/top destination switcher,
  page actions stack, metrics become one column and tables scroll only inside a named region.
- At 360 px, the document has no page-wide horizontal overflow. Essential status, amount and the
  primary action remain reachable without hover.
- The active route, accessible name and focus return behavior remain correct across navigation
  modes.

## Interaction rules

- One primary filled action is allowed per page-level action group. Secondary actions use bordered
  controls; low-frequency row actions use one labelled contextual menu.
- Hover, focus-visible, selected, expanded, disabled, loading, success and error states are
  visually distinct. Reduced motion removes nonessential transitions.
- Menus, sheets, popovers, dialogs, tabs, tables and form controls use the checked-in shadcn
  primitives when they supply the correct accessible behavior. OneLedger compositions own the
  domain layout and must style those primitives to match the approved reference exactly.
- Closing a dialog, sheet or popover returns focus to the control that opened it. Row operations
  do not cause layout jumps.
- Loading preserves page identity and table/module geometry. Loading never appears as zero, empty,
  complete or all clear.
- Empty and error states name what is absent or unavailable and offer a truthful next action when
  one exists.
- Long names wrap or truncate with an accessible full name without displacing aligned amounts or
  actions.

## Reusable production compositions

The implementation may refine names after inspecting the existing components, but it must produce
one shared system rather than screen-local replicas:

- `AppShell`: persistent sidebar, responsive navigation and top utility bar;
- `ConsolePageHeader`: compact title, one-line context and actions;
- `MetricStrip`: exact server strings in one unboxed divided row;
- `AttentionRow`: real unresolved state plus one next action;
- `ModuleGrid` and `Module`: unboxed, hairline-separated evidence areas;
- `DataToolbar`: search, filters, sort, date and view controls;
- `DataTableRegion`: named horizontal overflow boundary, compact table and row menu;
- `StatusLabel`: icon/text/semantic state pairing;
- `EvidenceChart`: server-coordinate chart wrapper with axes, missing-data gaps and accessible
  summary;
- `SectionNav`: local navigation that preserves mounted form state where required;
- `ContextPanel`: dialog/sheet for row actions, evidence and safe correction.

These compositions may wrap shadcn primitives. They do not hide financial semantics, accept raw
numbers for browser arithmetic or force every domain into a card.

## Representative screen contracts

### Overview

Overview follows the approved reference most closely. It contains:

- a compact title and period control;
- an unboxed strip for net position, liquid balance and known commitments over the dashboard's
  inclusive `asOf` through `asOf + 30 days` window;
- first-row modules for position composition, twelve-month net-position history and cash flow;
- second-row modules for Needs attention, upcoming commitments and recent activity;
- exact links into Accounts, Wealth, Plans and Transactions;
- gaps or unavailable labels whenever evidence is incomplete.

The former promotional panel does not return. Its space is financial evidence or a fourth useful
KPI when the page width and data support one.

### Accounts

Accounts follows the approved reference table layout:

- assets, liabilities and evidence freshness orient the page;
- one attention row appears only when an account has an unacknowledged opening balance, a missing
  required snapshot or an explicit typed coverage problem already returned by a domain response;
- open accounts are grouped by role and rendered in a compact table with name, institution/type,
  role, currency, evidence date/status, signed balance and one contextual action menu;
- Add account is primary; evidence import is secondary;
- closed accounts are progressively disclosed but retain edit, reopen and history access;
- valuation detail shows context facts, stored evidence in explicit newest-first chronology and
  maintenance actions. Focus returns to the originating row action.

The displayed role is a deterministic presentation grouping derived from `accountType`; it is not
a new stored financial fact:

- `everyday`: `cash`, `current`, `salary`;
- `reserve`: `savings`, `fixed_deposit`, `recurring_deposit`;
- `investment`: `brokerage`, `mutual_fund_folio`, `retirement`, `precious_metal`, `crypto`;
- `property`: `property`;
- `credit`: `credit_card`, `payable`;
- `loan`: `personal_loan`, `home_loan`, `auto_loan`, `education_loan`, `gold_loan`, `loan_other`;
- `other`: `other_asset`, `receivable`.

Within a group, order by account name and stable ID. Evidence status is also deterministic:
`needs_confirmation` when `openingBalanceConfirmedAt` is null;
`missing_snapshot` when `valuationMode` is `snapshot` and `latestValuationDate` is null;
`coverage_problem` only when a typed existing coverage response names the account; otherwise
`current`. The table always shows the actual evidence date when present. Phase 4.8 does not invent
a universal age threshold or call an account stale merely because a date is old.

The stopped 4.7.15 stash may be inspected after this specification is dispatched. Only proven
behavior and accessibility tests are eligible for selective recovery; its editorial composition,
styles, copy and stale tracker changes are not.

### Transactions

Transactions uses the approved dense ledger:

- page actions remain compact;
- one toolbar exposes text, period, account and category filters with advanced filters in a
  disclosure;
- the metric strip shows server-supplied reportable income, reportable spending and unresolved
  review count for the active filter, or is omitted until that exact summary is available;
- the table keeps date, description, account, category, evidence/status and signed amount aligned;
- imported evidence, transfer linking, split state and classification remain reachable without
  expanding every row;
- filters remain represented in the URL, keyboard usable and resilient to Back/Forward.

### Plans

Plans follows the approved mixed workspace:

- the page opens on the highest-priority real decision and a 30/60/90-day cash forecast;
- available-after-commitments, reserve status, goals and upcoming commitments use existing server
  strings and coordinates;
- Needs attention, Monthly review, Forecast, Emergency reserve, Goals, Commitments and Register
  remain stable locally navigable sections;
- the default viewport favors decisions and evidence rather than rendering every long explanation;
- settlement, editing and recovery use contextual panels without removing existing confirmation.

## Remaining screen outcomes

### Wealth

Use the Accounts/position family. Lead with the selected complete month, completeness and refresh
state, then a compact history chart, selected-month position table and attribution evidence.
Incomplete months are gaps, never zero. The stopped 4.7.15 behavior for selected-month summaries,
refresh feedback and stored methodology may be recovered only after verifying it against this
composition.

### Spending

Use the Transactions/activity family. Lead with exact selected-range spending and comparison,
then stable-color bucket/category charts and an exact table linking into the ledger. Budget state,
refunds and negative net values stay semantically distinct. Date and export controls remain in one
compact toolbar.

### Subscriptions and Recurring

Use compact summary strips, renewal/cadence timelines and scannable lifecycle tables. The next
renewal or next recurring payment leads when present. Current, upcoming and past/ended states are
visibly distinct. Rename, category, cadence, lifecycle and deletion actions live in contextual
menus or panels; existing confirmation and recovery remain intact. Subscription series do not
duplicate in Recurring.

### Investments

Lead with portfolio value and clearly separate valued, unvalued and closed positions. Show review
items, return/benchmark evidence, allocation, holdings and valuation confidence only where the
server supplies complete values. Recording an event and importing evidence remain the primary
entry paths. Holdings, lots, funding chains and corrections use dense tables and contextual
panels, preserving closed history and never fabricating value or cost basis.

### Salary

Lead with the latest evidenced pay period and earned-versus-received state. Use compact history,
increment and variable-pay modules only where authenticated responses support them. Payslips,
deposit-link status, deductions and rules use tables or focused panels; unmatched credits and
unclassified deductions form the attention row.

### Coverage

Keep the account-by-month grid as the primary evidence surface, with a compact summary, sticky
labels, a clear legend and accessible cell names. Selecting a cell opens its reason,
acknowledgement and next action without losing the grid. Do not replace coverage evidence with a
decorative score.

### Statement import and investment import

Use a visible staged journey: Upload, Identify/Resolve, Parse, Review and Commit. The current stage,
blocking decisions, exact row/group counts and provenance remain visible. Batch decisions use
compact rows and a stable action area. Commit and cancel remain exact and visually distinct.

### Classify

Lead with queue count and the highest-confidence actionable group. Compact rows show proposed
category, evidence, affected count, confidence and scope before Apply. Recent reversible changes
stay nearby. Merchant knowledge and rule maintenance remain linked secondary workspaces.

### Documents

Lead with documents requiring action, then a compact library table with type, account, period,
import state and provenance. Type and account filters remain visible. Description repair and
maintenance actions are contextual. Documents gains no decorative chart.

### Settings

Open on General with the editable household fields and one Save action visible. General, Data
health, Categories, Rules and Exchange rates use the shared section navigation and compact
tables/forms. All sections remain mounted where switching must preserve an unfinished form. A real
health finding may appear above navigation; unavailable data never claims health.

### Detail and public routes

Account valuation, holding, subscription and import-review routes use a compact context header,
key facts, evidence timeline/table and focussed correction panels. Public/auth routes adopt the
same sans typography, blue accent and neutral surfaces without exposing authenticated navigation.

## Backend read contracts

Phase 4.8 may add read contracts needed to render the approved experience. Every read is
owner-authorized, household-scoped and read-only. A read that returns financial values also returns
exact strings, server-computed ratios/coordinates for visual marks, explicit currency and `asOf`,
and reasons for missing or partial evidence.

### Dashboard position composition

Extend `DashboardResponse` with `positionComposition`. It groups included active accounts by this
stable server-owned order:

1. `cash`: `cash`, `current`, `savings`, `salary`, `fixed_deposit`, `recurring_deposit`;
2. `investments`: `brokerage`, `mutual_fund_folio`, `crypto`, `precious_metal`;
3. `property`: `property`;
4. `retirement`: `retirement`;
5. `other`: every other included asset type;
6. `liabilities`: every included liability.

Each non-empty row returns `key`, `label`, signed `valueBase`, signed `valueSecondary`,
`sharePercentBase`, `sharePercentSecondary`, `chartCoordinateBase` and
`chartCoordinateSecondary`. A share uses the absolute row value divided by the sum of absolute
included row values, so a liability is visible without cancelling an asset. `sharePercent*` is a
human-readable percent string quantized to four decimal places with `ROUND_HALF_EVEN` (for example,
`"12.5000"`). `chartCoordinate*` is the same ratio scaled to the inclusive integer range 0–10,000
and rounded half-even; it is always non-negative, while `value*` retains the liability's sign. If
the absolute denominator is zero, every share is `"0.0000"` and every coordinate is `0`.

When any included account lacks a reporting value, the affected currency's values, shares and
coordinates are null for the whole composition and a `missing` reason list identifies affected
account IDs and currency codes. The client never derives a share or silently drops an account.

### Filtered transaction summary

Extend `TransactionsResponse` with `summary`. Every existing query filter except `sort`,
`direction`, `limit` and `offset` applies before both summary queries. The same typed predicate is
used against the base ledger and `v_transaction_reportable`; a filter that selects only
non-reportable transaction types truthfully produces zero reportable money while the visible
ledger total may remain non-zero. The existing top-level `total` remains the filtered base-ledger
result count before pagination. `summary` returns:

- `reportableIncomeBase` / `reportableIncomeSecondary`;
- `reportableSpendingBase` / `reportableSpendingSecondary`;
- `reportableNetBase` / `reportableNetSecondary`;
- `unreviewedCount`, `uncategorizedCount` and `attentionCount`;
- `moneyScope: "reportable"` and `attentionScope: "ledger"`.

The financial summary reads `v_transaction_reportable` and reuses the established Dashboard and
Spending rule exactly: income is `sum(amount_*)` where `txn_type = 'income'`; spending is
`-sum(amount_*)` where `txn_type <> 'income'`; and net is income minus spending. A refund therefore
reduces spending rather than becoming income, and reportable spending may be negative when refunds
exceed charges. Transfers, card payments, investment legs and duplicate/non-reportable rows remain
excluded by the view. The attention fields count filtered
base-ledger rows whose current review/classification state requires action. Specifically,
`unreviewedCount` counts `is_reviewed = false`, `uncategorizedCount` counts
`classified_by = 'unclassified'`, and `attentionCount` counts their distinct union. The summary
names `moneyScope: "reportable"` and `attentionScope: "ledger"`; the UI labels the monetary values
as reportable and never implies they sum every visible ledger row. All values are PostgreSQL/Python
decimal strings; TypeScript formats only.

### Workspace search

Add `GET /search?q=&limit=` for the top-bar control. Query length is 2–120 characters; limit is
1–10 per result kind. The first Phase 4.8 contract searches only safe authenticated fields already
visible in the corresponding screens:

- accounts by name, institution name and masked identifier;
- transactions by screened clean/raw description and merchant name;
- documents by safe document type and linked account name;
- holdings by safe security and account names;
- plans by goal, commitment and register title.

Results return only `kind`, stable resource ID, primary label, safe secondary label, route and
optional date/status. They never return complete identifiers, filenames, storage paths, statement
content or another household's data. Result ordering is exact-prefix, then word-prefix, then
contains, followed by the domain's stable date/name ordering. The endpoint uses the same owner
session and explicit household predicates as the domain routes. The top bar shows grouped results,
supports keyboard selection and announces result count. A query shorter than two trimmed
characters performs no request and clears any earlier result list.

### Existing chart and planning contracts

Reuse these existing server-produced values rather than adding browser calculations:

- `DashboardResponse.flowEvidence` for cash-flow coordinates;
- `WealthHistoryResponse.months[].chartCoordinate` and `chartRatio` for net-position history;
- forecast `chartBalanceCoordinate` and `chartConfirmedCoordinate` for Plans;
- goal `percent` for goal progress;
- spending response chart ratios for bucket/category charts;
- investment analysis coordinates and exact return strings for Investments.

Plans may continue to compose its existing endpoints. A composite read may be added later only if
measured latency justifies it; visual redesign alone does not authorize duplicate financial logic.

## Backend safety and tests

Each new or extended route must prove:

- owner success and member refusal;
- foreign-household exclusion, including search and account/transaction filters;
- read-only transaction behavior;
- exact Decimal strings, deterministic rounding and explicit missing-data behavior;
- stable ordering, bounded limits and no partial confident total;
- no serialization of complete identifiers, filenames, storage paths, credentials or statement
  content;
- generated OpenAPI and TypeScript types match the final contract.

No new financial write is planned. If implementation discovers that a write must change, it stops
and receives a separate approved contract satisfying the financial write floor.

## Accessibility, privacy and responsive acceptance

- One visible H1 per page; headings follow a logical order.
- All icon-only controls have accessible names. Status never relies on color alone.
- Sidebar, local navigation, filters, menus, tables, dialogs and staged flows are keyboard usable
  with visible focus.
- Charts have accessible summaries, labelled axes where applicable and explicit missing-data gaps.
- The page has no horizontal overflow at 360, 588, 1024 and 1440 px. A wide table may scroll only
  in its labelled table region.
- Ordinary responses and logs preserve existing redaction. Synthetic visual fixtures contain no
  real names, values, filenames, narrations or identifiers.
- Loading, empty, error, partial and populated states are inspected on every page family.

## Fidelity verification and owner acceptance

Automated tests and a merged PR do not close Phase 4.8. Each task verifies its affected behavior;
the final task performs the application-wide fidelity audit.

For Overview, Accounts, Transactions and Plans, compare the running application with the approved
HTML at 1440 and 1024 px. The audit checks shell dimensions, white/blue palette, sans typography,
title/action alignment, unboxed metric strip, module/table density, hairlines, contextual menus and
absence of beige/editorial remnants. At 588 and 360 px it checks the specified responsive reflow.
Temporary local captures may support review, but screenshots containing financial data are never
committed.

Every sidebar destination and affected detail route is then inspected for the same shell, tokens,
interaction states and information hierarchy. A route that retains the rejected visual system
blocks completion even if its tests pass.

The owner performs the final visual/UX inspection in the development stack. Only explicit owner
acceptance may mark Phase 4.8 accepted in `docs/superpowers/progress.md`.

## Delivery boundaries

- Use the active checkout only; no worktree.
- One implementation writer works at a time. The orchestrator owns branches, pushes, PRs and
  merges; reviewers are read-only.
- The stopped 4.7.15 stash remains untouched until the relevant task selectively ports a proven
  behavior or test. It is never popped wholesale.
- Each task branch includes its truthful progress update before final verification and PR.
- Run focused tests during implementation, one read-only review, one consolidated repair and one
  exact-head verifier. Use `scripts/verify-web.sh` only when the branch changes no API, database or
  generated contract; otherwise use `scripts/verify.sh` with the task's named affected tests.
- Phase 4.8 is not complete until every planned task is merged, the cross-screen fidelity audit is
  green and the owner records visual/UX acceptance.
