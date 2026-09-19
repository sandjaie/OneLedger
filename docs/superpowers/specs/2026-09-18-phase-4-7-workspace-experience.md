# Phase 4.7 — owner workspace experience

Revision: 19 September 2026 — owner-selected editorial workspace refinement.

## Authority and outcome

This is the single design specification for OneLedger's workspace experience. It
consolidates the earlier [workspace refresh](2026-09-17-workspace-experience-refresh-design.md)
and [decision-first workspace](2026-09-17-decision-first-workspace-design.md) designs. Those
documents and their dispatched plans remain historical evidence; this specification owns the
current visual and interaction decisions.

Tasks 4.7.1–4.7.13 delivered a functional Nova/shadcn baseline. After inspecting that baseline in
the running application, the owner asked for a more distinctive, useful and coherent experience
and selected the **Quiet Editorial Ledger** direction on 19 September 2026. The delivered controls,
navigation contracts, financial semantics and accessibility work remain the foundation; the
refinement changes hierarchy, composition, typography and information density across every page.
It does not reopen already-proven financial behavior.

The owner should experience one coherent, visually confident financial operating desk across
every existing sidebar destination. In the first viewport, each screen makes it easy to answer:
what is true, what needs attention, and what can I do next? Repeated records are scannable;
details, provenance, and less-used actions remain easy to find without creating walls of text.
The checked-in mockups are references, not pixel or topology requirements. The owner will assess
the finished experience after the planned screen branches and cross-screen audit are complete.

This is Phase 4.7, pulled forward after Phase 4.3 at the owner's request. Existing Phase 4.4–4.6
identifiers retain their meanings. Phase 4.7 neither changes their financial scope nor makes an
uncompleted optional outcome a prerequisite for this UI work.

## Visual direction

Keep the owner's `b2YOAa` shadcn preset as the component foundation: Nova style, semantic tokens,
Lucide icons and its accessible interaction primitives. The selected product identity is **Quiet
Editorial Ledger**: warm parchment and off-white canvases, deep forest and ink, muted sage,
mineral blue, ochre and restrained coral. A refined editorial serif may be used for page titles
and one dominant financial figure per screen; controls, body copy, dense records and tables use a
highly legible modern sans-serif. Financial columns use tabular figures and align for comparison.
Icons clarify actions or status; they never replace labels.

The interface is calm rather than sparse. Fine dividers, surface tint and typography establish
hierarchy before borders or elevation. Avoid a wall of interchangeable white cards, nested cards,
decorative gradients, glass effects, heavy shadows, motivational artwork and lifestyle slogans.
Illustration never occupies space that can answer a financial question. A supporting KPI, exact
record list or useful action takes that space instead.

The signature is an **owner decision band**: one clear focus area that frames a real status, an
evidence-backed visualization when useful, and its next action. It earns prominence through task
priority, not a fabricated score or giant number. Nova Cards, Items, Tabs, and data display
components give repeated content a stronger visual rhythm, but not every paragraph becomes the
same box. Long explanations are shortened or put in a named disclosure beside the fact they
explain. Typography, spacing, contrast, and clear interaction states carry the identity.

Keep semantic colors separate from data colors. Green remains the principal action/success cue;
attention, danger, information, and neutral states have their own accessible tokens. Extend the
preset's chart palette to 10–12 distinguishable categorical series colors spanning cool and warm
hues. A category keeps its color across Overview, Spending, Budgets, and related detail views;
chart order and category identity must not silently recolor it. When a view has more categories
than distinguishable colors, group the remainder as labelled Other or use a neutral treatment.
Legends, labels, values, and (where useful) line styles carry meaning even without color. Never
use danger red merely as the next arbitrary category color.

Every interactive element has distinct rest, hover, focus, selected, expanded, disabled, loading,
success, and error treatment where applicable. Motion is brief and tied to an action; reduced
motion removes nonessential transitions. The design is evaluated on real screens, not on a
standalone component gallery.

## Application topology

Every authenticated destination uses the same ordered grammar. A page may omit a layer when no
truthful content exists, but it does not reorder the remaining layers merely to create novelty:

1. Persistent grouped navigation and compact owner/reporting-currency controls.
2. Page identity: eyebrow, title, at most one useful sentence and at most one primary action.
3. Primary truth: the most important financial position or workflow state on the page.
4. Decision band: the next real action, warning or review item, shown only when one exists.
5. Supporting evidence: a small set of sourced KPIs, comparisons or charts.
6. Work area: compact records, tables, timelines, forms or queues designed for scanning.
7. Progressive detail: provenance, methodology, maintenance and infrequent operations.

The topology is shared, not cloned. A ledger uses a dense table, a review queue uses staged rows,
and Settings uses sectioned forms; they still align to the same page grid, type scale, spacing,
color semantics, focus treatment and responsive shell. The desktop navigation remains visible
while the page scrolls. At narrow widths it becomes a dismissible sheet and the page retains its
title, primary state and next action without horizontal page scrolling.

Five representative screens establish the reusable patterns before the remaining screens adopt
them: Overview for the editorial dashboard, Transactions for dense data, Plans for a mixed
planning workspace, statement review for staged decisions, and Settings for forms and
administration. The implementation must still inspect and verify every destination and detail
route; an archetype is not permission to leave a route on the old composition.

## Page families

| Family | Destinations | Shared emphasis |
| --- | --- | --- |
| Financial position | Overview, Accounts, Wealth | Position, composition, coverage and change over time |
| Activity | Transactions, Spending, Subscriptions, Recurring | Comparison, filters, upcoming activity and direct action |
| Planning | Investments, Salary, Plans, Coverage | Evidence quality, progress, forecasts and review work |
| Intake and review | Import, Import investments, Classify, Documents | Stages, provenance, validation, recovery and batch decisions |
| Administration | Settings | Focused configuration, health, rules and advanced maintenance |
| Detail workflows | Account valuation, holding, subscription and import-review routes | Context summary, evidence timeline and safe correction |

## Shared structure and content rules

- Preserve the persistent grouped desktop rail, compact dismissible mobile navigation, skip
  link, and top bar's reporting-currency and owner controls. The active destination remains
  obvious while the page scrolls. The page grid aligns headings, filters, records, and actions.
- A page starts with a title, at most one useful purpose sentence, and one context-specific
  primary action. Show a factual status or next-action strip only when a real warning or useful
  next step exists. Do not make loading look like an empty result.
- Give one focus surface to the next decision or noteworthy state. Put repeated items in compact
  rows with a clear status, key facts, and a review affordance. Keep infrequent operations in a
  labelled secondary disclosure. Convert the shell, controls, navigation, data surfaces,
  feedback, and overlays to the shadcn design system; preserve the behavior that Task 1's
  `RowActions` and `SectionNav` currently guarantee.
- Use plain, action-specific language. Preserve all financial caveats, confidence, coverage,
  provenance, confirmation, and recovery information, but place each beside the affected fact
  or behind an explicit explanation. Do not invent dates, totals, valuations, prices, returns,
  cost basis, or reassuring clean states.
- Wide data may scroll in a named region. The page itself does not scroll horizontally at 360px,
  588px, or desktop widths. Empty and error states give a real next step when one exists.

## Screen outcomes

### Overview

Overview establishes the editorial dashboard pattern. Lead with net worth in the primary and
secondary reporting currencies and an evidenced twelve-month trend. Follow with the open-action
band, this month's cash flow, account composition, recent activity and **Upcoming commitments —
next 30 days**. That commitments KPI shows the exact total due, count, next due date and a link to
review; it replaces promotional artwork and appears only when its source is available. Keep
assets, liabilities, liquid net worth, savings rate, year-to-date values, cards due and coverage
warnings reachable without giving every value equal visual weight.

### Accounts

Lead with the household position split into assets, liabilities and liquid accounts, followed by
the account requiring attention or a truthful all-clear absence. Group open accounts by their
financial role and present each as a compact comparison row with name, type, currency, signed
balance, valuation state and last evidence date. Add account is the primary action. Editing,
closing, reopening, identifier reveal and valuation maintenance are contextual actions rather
than repeated full-size buttons. Closed accounts and methodology are progressively disclosed.

### Wealth

Lead with the selected month-end net worth and the history's completeness. Show the month-end
trend, then a selected-month position table and an attribution waterfall for contributions,
withdrawals, market movement, FX and residual where those values exist. Missing or incomplete
months remain gaps, never zero. Refresh is prominent only when history is stale. Methodology and
coverage explanations sit beside the affected period, not above the whole experience.

### Transactions

Use the dense-ledger archetype. Keep one compact filter row and a contained semantic table with
aligned dates, account, description, category, amount and status. The primary action records a
transaction; transfer linking, classification and row correction remain contextual. A decision
band surfaces the most urgent unresolved classification or transfer work. Date-range inflow,
outflow and net KPIs may appear only when the server supplies exact strings and plotting values;
the client does no money arithmetic. Filters remain reflected in the URL and usable by keyboard.

### Spending

Lead with total spending for the selected range and the comparison or budget state that is
actually supported. Keep date controls compact. Show bucket and category evidence with stable
colors, exact-value tables and links back to the filtered ledger. Budget progress, refunds and
negative net values remain visually distinct. Recurring payments and subscriptions link to their
own workspaces instead of becoming duplicated narrative sections.

### Subscriptions

Lead with the next renewal, followed by monthly equivalent, annual spend and current count. Use a
timeline or compact cadence view for upcoming renewals and a scannable Current, Upcoming and Past
table/list with name, category when evidenced, cadence, exact amount, next or last date, status
and payment source. Search, sort and filters stay compact; lifecycle actions use a contextual
menu. Do not add a spending chart unless stored charge history answers a meaningful comparison.

### Recurring

Lead with the next dated non-subscription payment or a truthful absence. Present Active and Ended
series as compact rows with cadence, expected amount, next or last date and evidence status.
Rename, cadence correction, cancellation, resumption and deletion remain available without
turning every row into a form. Subscription records never reappear here.

### Investments

Lead with portfolio value while clearly separating valued, unvalued and closed positions. The
decision band prioritizes open investment reviews or stale/missing valuation evidence. Follow
with return versus benchmark, allocation, holdings and valuation confidence, using exact tables
and explicit gaps. Recording an event and importing evidence remain the two primary entry paths.
Do not imply value, cost basis, gain or return where evidence is missing.

### Salary

Lead with the latest evidenced pay period and earned-versus-received status. Show year and month
history, increments and variable pay only where the authenticated response supports them. Keep
payslips as compact records with deposit-link status and deductions. Unmatched credits and
unclassified deductions form the decision band. Entry, PDF import, correction and deduction-rule
maintenance remain separate, labelled workflows rather than one long page of forms.

### Plans

Use the mixed-workspace archetype. Lead with the nearest overdue or upcoming commitment and an
evidenced cash forecast. Keep Needs attention, monthly review, emergency reserve, goals,
commitments and register as stable sections reached through local navigation. Confirmed and
projected amounts are visually and textually distinct. Editing or settling a commitment happens
in a contextual panel with exact confirmation and recovery behavior.

### Coverage

Lead with the count and age of missing or partial periods. Keep the account-by-month grid as the
core evidence surface, with a clear legend, sticky labels and accessible cell names. Selecting a
cell opens the reason, acknowledgement state and next available action without losing the grid.
Do not chart coverage as a decorative percentage.

### Statement import and review

Statement import uses an explicit Upload, Identify, Parse, Review and Commit journey. The first
screen focuses on the drop target, accepted evidence and current file progress. A batch review
starts with exact row totals and blocking decisions, then groups rows by status with compact
selection and batch actions. Evidence, confidence and reconciliation stay beside each affected
row. Commit and cancel remain visually distinct, exact and recoverable where the existing
contract allows it.

### Investment import

Use the same staged journey while preserving investment-specific group resolution, coverage
limits and commit semantics. The current stage and unresolved groups are always visible. Account
setup, parsing, review, commit and cancellation are not combined into one undifferentiated form.

### Classify

Lead with the review-queue count and the highest-confidence actionable group. Each group shows
the proposed category, evidence, affected-row count, confidence and scope before Apply. Keep
reversible recent changes nearby. Merchant knowledge and rule maintenance remain linked but do
not compete with the current classification decision.

### Documents

Lead with documents needing a next action, then show the library as readable compact records with
type, account, period, import state and provenance. Type and Account filters remain visible;
advanced filters can collapse at narrow widths. Description repair and maintenance actions are
contextual. Documents does not gain a decorative chart.

### Settings

Open `/settings` on General, with editable household fields and one Save action visible before
diagnostics. Use General, Data health, Categories, Rules and Exchange rates as one-at-a-time
sections. Keep all sections mounted so changing sections cannot discard an unfinished form.
Selection is reflected in the URL; direct hash links, Back/Forward, keyboard use and focus on the
requested heading work. A genuine data-health finding may appear above the selector and link to
its full report; loading or unavailable data never claims health. Rules analytics and FX
maintenance use compact evidence tables rather than dashboard decoration.

### Detail and public routes

Account valuation, holding and subscription detail routes start with a compact context header,
key facts and the current evidence state. Timelines and exact records precede edit controls;
correction, lifecycle and destructive actions live in clearly labelled panels or dialogs.
Investment entry retains instrument resolution, acquisition, price and confirmation stages.
Login and Unauthorized use the same typography, color and control system in a minimal public
shell without exposing workspace navigation or private state.

## Charts and trends

Use shadcn Chart, composed with Recharts, for supported comparisons and trends. Required chart
families are the existing Spending category/bucket comparison and Wealth month-end history, plus
Overview's twelve-month income/spending/net trend, Plans' confirmed-versus-projected cash
forecast, Investments' monthly return/benchmark and allocation views, and Salary's
earned-versus-received history where its authenticated response establishes both series.
Subscription price history or other charts may be added only if their existing evidence supports
a meaningful question. Documents, Classify, and Settings do not gain decorative graphs.

Every chart has a title that states the measure and period, an accessible keyboard layer, a
legend when more than one series appears, explicit missing/incomplete segments, and a nearby
table or list of exact values. Tooltips show the server's exact display strings; a chart never
turns missing data into zero or connects a line across an unknown period. Color is not the only
way to distinguish a series. At 360px the chart fits its region, offers a readable fallback,
and never causes page-wide scrolling. Respect reduced motion.

Financial amounts, rates, gains, and return inputs remain `NUMERIC`/`Decimal` on the server and
strings on the wire. Recharts may receive only bounded, server-derived integer plotting
coordinates or nonfinancial counts for geometry; it must not parse a money/return string into a
JavaScript float or perform financial arithmetic. The exact source string remains authoritative
for labels, tooltips, and tables. A missing display coordinate means a visible gap. If an
existing endpoint lacks such coordinates or a needed evidenced series, add a separately tested
read contract with owner authorization and household isolation; never infer or fabricate it in
the client.

## Supporting data contracts

The owner authorizes read-side API work needed to present truthful information in the selected
experience. Prefer extending an existing page response when the fact has the same authorization,
freshness and lifecycle; use a focused endpoint when it does not. New fields and endpoints must:

- require the owner and scope every query to the owner's household;
- derive money and rates with PostgreSQL `NUMERIC` and Python `Decimal`;
- return exact display values as strings and separate bounded plotting coordinates when needed;
- state `asOf`, currency, coverage and missing-data semantics;
- preserve unknown or unsupported values as null or an explicit unavailable state;
- test owner success, member refusal, foreign-household isolation and exact round trips.

Overview's upcoming-commitments KPI may reuse the existing commitment/action sources, but its
total must be calculated on the server for the next 30 calendar days in one stated reporting
currency. Mixed-currency or missing-rate cases must return an explicit incomplete state rather
than a silently partial total. Presentation work does not authorize new financial writes,
predictions, fabricated categories or advisory scores.

## App-wide shadcn/ui conversion

The target is consistent use of the Nova system on every existing screen and detail route.
Replace general-purpose custom buttons, fields, selects, dialogs, navigation controls, tables,
badges, alerts, loading and empty states, menus, and chart wrappers with appropriate shadcn
source components. Domain-specific record compositions and the exact financial formatting and
workflow logic remain OneLedger code. "Everything" means the visible UI system is coherent, not
that every semantic HTML element must be wrapped by a library component.

The web project uses Tailwind CSS 4 and the checked-in Nova/shadcn configuration. Extend the
installed source components deliberately; do not run a wholesale overwrite of application
files. Use semantic tokens for the editorial palette and component states, keep the project's
actual aliases and npm runner, and remove superseded global CSS as each screen family adopts the
refined composition. Client-side controls keep their proper client boundary. Keyboard behavior,
focus return, labels, validation and narrow-screen layout are tested in their consuming
workflows, not assumed from the library.

## Safety, verification, and owner acceptance

This phase changes presentation and, only where charts require it, adds read-only evidenced
plotting data. It adds no financial write, schema calculation shortcut, or authorization
exception. Screens continue using Workspace's authenticated request wrapper. Exact monetary
strings remain display-only in TypeScript. Any new chart endpoint or response field needs an
explicit contract, owner authorization, household isolation, Decimal-only computation, and
tests before its chart consumes it. Do not change underlying financial meanings.

Each screen branch runs affected component tests, web lint/types, and live keyboard and viewport
checks. A web-only branch uses `scripts/verify-web.sh`; a branch with API/chart-contract work
uses `scripts/verify.sh` and the same task's test database. Inspect every
sidebar destination and relevant detail route at 360px, 588px, and desktop in the cross-screen
audit. Use synthetic fixtures; never commit real ledger screenshots, values, filenames, or full
identifiers. The owner checks the finished application after the plan is implemented.

Acceptance means the owner can identify state and next action in the first viewport, navigate
without losing the rail or unsaved Settings edits, scan dense records, reach all existing actions
and evidence, and understand empty/error/loading states. Every sidebar destination and detail
route visibly belongs to the Quiet Editorial Ledger system; no route retains the former wall of
equal cards or uses decorative artwork in place of financial information. Useful, truthful
charts keep exact-value alternatives and stable category identity. Interactions are visible,
responsive and keyboard-operable, and every existing financial meaning is preserved. Automated
verification and merge do not close the refinement: final closure requires owner visual and UX
acceptance in the development stack.
