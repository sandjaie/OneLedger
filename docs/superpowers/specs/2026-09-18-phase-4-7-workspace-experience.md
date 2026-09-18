# Phase 4.7 — owner workspace experience

## Authority and outcome

This is the single design specification for OneLedger's workspace experience refresh. It
consolidates the earlier [workspace refresh](2026-09-17-workspace-experience-refresh-design.md)
and [decision-first workspace](2026-09-17-decision-first-workspace-design.md) designs. Those
documents and their dispatched plans remain historical evidence; this specification owns the
remaining visual and interaction decisions. Task 1's implemented page grammar, `SectionNav`, and
`RowActions` are a transitional baseline. The numbered shadcn conversion may replace their
presentation and interaction plumbing while preserving their navigation and action contracts.

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

Use the owner's `b2YOAa` shadcn preset as the base visual template: Nova style, green theme,
neutral base, Inter body type, Lucide icons, and its default radius. Integrate it by controlled
merge into the existing Next.js app, not by replacing application files wholesale. Preserve
OneLedger's financial semantics and brand recognition, but allow the preset's typography, surface,
spacing, and component system to supersede Task 1's transitional CSS. Amount columns use tabular
figures and align for comparison. Icons clarify actions or status; they never replace labels.

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

### Settings

Open `/settings` on General, with editable household fields and one Save action visible before
diagnostics. Use General, Data health, Categories, Rules, and Exchange rates as one-at-a-time
sections. Keep all sections mounted so changing sections cannot discard an unfinished form.
Selection is reflected in the URL; direct hash links, Back/Forward, keyboard use, and focus on
the requested heading work. A genuine data-health finding may appear above the selector and
links to its full report; loading or unavailable data never claims health. Existing validation,
fixed reporting-pair behavior, and links into Rules, Data health, and Exchange rates survive.

### Subscriptions and Recurring

Lead with the next real renewal or dated recurring payment, or a truthful absence of one. Show
Current, Upcoming, and Past subscriptions and Active and Ended recurring payments in compact
rows. A row gives name, status, amount/cadence, and next or last date before payment source and
provenance. Keep subscription detail routes and recurring inline detail, lifecycle controls,
corrections, and exact destructive confirmations. An ended record must never appear active
because a lifecycle selector says Active.

### Documents and Classify

Documents displays type, account, period, import state, and next relevant action as readable
records instead of a squeezed table. Type and Account filters remain visible; advanced filters
can collapse at narrow widths. Provenance and maintenance actions stay reachable. Classify uses
the same decision/record hierarchy while retaining evidence, confidence, reversible review,
and clear recovery paths. Raw enum identifiers are not owner-facing labels.

### Investments and investment import

Separate portfolio status, holdings, valuation confidence, review work, and analysis. When
holdings are absent, show the two real entry routes and one concise explanation of limitations.
Do not imply value for unvalued holdings. Import retains its explicit review and commit stages;
coverage exclusions and methodology remain available without occupying the first screen.

### Remaining destinations

Overview and Accounts keep their useful summaries and adopt the common hierarchy. Transactions
and Spending use compact filters and contained dense data. Salary, Plans, Coverage, and Wealth
surface the next real task ahead of long methodology or history. Existing detail routes inherit
the visual system. Every sidebar destination retains its current owner operation, linkable
action, and recovery path; no decorative metric or chart is added without a truthful source.

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

## App-wide shadcn/ui conversion

The target is consistent use of the Nova system on every existing screen and detail route.
Replace general-purpose custom buttons, fields, selects, dialogs, navigation controls, tables,
badges, alerts, loading and empty states, menus, and chart wrappers with appropriate shadcn
source components. Domain-specific record compositions and the exact financial formatting and
workflow logic remain OneLedger code. "Everything" means the visible UI system is coherent, not
that every semantic HTML element must be wrapped by a library component.

The web project already uses Tailwind CSS 4 but has no shadcn configuration. Use the owner's
preset as the reference; preview generated configuration, CSS, dependencies, and component files
before merging them into `web/`. Do not run a wholesale overwrite of application files. Commit
reviewed component source, use the project's actual aliases and npm runner, and remove superseded
global CSS after each screen family adopts Nova. Client-side controls keep their proper client
boundary. Keyboard behavior, focus return, labels, validation, and narrow-screen layout are
tested in their consuming workflows, not assumed from the library.

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
and evidence, and understand empty/error/loading states. The pages use the Nova system coherently
and show useful, truthful charts with a category palette and exact-value alternatives. They have
visible and responsive interactions and preserve every existing financial meaning.
