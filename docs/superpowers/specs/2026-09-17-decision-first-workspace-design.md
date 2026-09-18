# Decision-first owner workspace

## Outcome and authority

OneLedger is a private financial operating desk for its owner. Each screen should answer, in
order: what is true, what needs attention, and what can I do next? The checked-in mockups remain
references for product intent, not pixel or topology requirements. This design supersedes the
mock-fidelity direction of the earlier workspace refresh for future UI work; it does not change
financial contracts, calculations, authorization, or the source of any displayed fact.

The first pass covers the shared workspace and every existing sidebar destination. It gives
Settings, Subscriptions, Recurring, Documents, and Investments deeper information-architecture
work, then applies the same hierarchy to Overview, Accounts, Transactions, Import, Spending,
Classify, Salary, Plans, Coverage, Wealth, and investment import. Existing detail routes inherit
the shared system. No new route or backend endpoint is required by this design.

## Direction

Choose a **decision-first ledger** over a tile-heavy dashboard or an editorial report. A dashboard
would make unlike facts look equally important; a report would perpetuate the current wall of
copy. OneLedger instead has a quiet frame, one prominent focus area where a real decision exists,
and compact rows for repeatable records. The visual risk is to remove many card borders and
buttons, trusting spacing, alignment, type, and a small amount of color to carry hierarchy.

The core palette is cool canvas `#F1F5F3`, white surface `#FFFFFF`, deep ink `#172A24`, action
green `#176B52`, secondary slate `#52665D`, and attention amber `#9A5C1B`. Error/destructive
states retain the established danger red. IBM Plex Sans handles controls, labels, and most
headings; IBM Plex Serif is reserved for a single page title or long-form analysis. Numeric data
uses tabular figures and right alignment where columns compare. Monospace is for genuine codes,
not every small caption. Avoid repeated all-caps eyebrow labels and decorative gradients.

## Shared page grammar

- Keep the persistent grouped rail and compact mobile navigation. The top bar retains reporting
  currency and owner controls. Main content uses a consistent maximum reading width and aligns
  all page titles, filters, records, and actions to one grid.
- Each page begins with its title, at most one short purpose sentence when needed, and one
  context-specific primary action. A factual status/next-action strip appears only when the page
  has a real warning or a meaningful next step; no fabricated score or empty dashboard tile.
- Use one focus surface for the next decision or noteworthy state. Repeatable items become
  compact, readable rows with clear status, key facts, and an obvious way to review details.
  Secondary metadata and infrequent actions sit in an accessible disclosure. Avoid making every
  item the same card.
- Use shared treatments for filters, section headers, status chips, empty/error/loading states,
  row actions, and contained dense data. Controls have a visible primary/secondary/destructive
  hierarchy. A loading state keeps the frame stable and does not masquerade as an empty result.
- Explanatory copy should answer a concrete user question. Preserve safety and provenance text
  where needed, but put it next to the affected data or behind a clearly named explanation.

## Screen decisions

### Settings

`/settings` opens on **General**: household name, display name, tax residency, and the fixed
reporting pair. The first viewport should communicate what can be edited and give one Save
action, rather than lead with a diagnostic paragraph. A concise Data health summary remains
visible above the section navigation when genuine findings need attention; it links to the full
report and never claims a clean state while loading or unavailable.

Use a section selector for General, Data health, Categories, Rules (including merchant knowledge),
and Exchange rates. Only one long section is visible at a time, with its own title, purpose, and
controls. Preserve a stable deep-link contract: existing `/settings#merchant-knowledge-title`
opens Rules and focuses the target; links to data quality or missing rates select their matching
section. Direct loads, browser Back/Forward, and keyboard selection must work. A pending edit
must not be silently discarded on section change: either retain mounted form state or warn before
leaving. Keep every current server-side validation and disabled reporting-pair behavior.

The existing Classify link to `/settings#merchant-knowledge-title` must open Rules. Retarget the
investment-import Data quality link to `#data-quality-title` and the Wealth missing-rate link to
`#fx-title` so those paths select and focus the intended section. A plain `/settings` still opens
General. Section selection is reflected in the URL, not stored only in component memory.

### Subscriptions and Recurring

Subscriptions leads with the next real renewal from its subscription summary or a truthful empty
state. Recurring leads with the earliest dated non-subscription payment from its existing rows,
labels an overdue date accurately, or states truthfully when no next date is available. Show only
the most useful subscription totals; do not invent recurring totals.
Use distinct Current, Upcoming, and Past views for subscriptions and Active and Ended views for
recurring payments. A record row names the item, status, amount/cadence, and next or last date;
payment source and provenance are available on expansion or detail. A subscription links to its
existing detail route; a recurring payment opens its details in the row, without a new route or
fetch. Move infrequent correction, classification, rename, and lifecycle actions into a
labelled secondary action disclosure without changing their semantics or confirmation behavior.
Do not suggest an ended item is active merely because its status selector is visible.

### Documents and Classify

Documents should show type, account, period, import state, and the next relevant action in a
readable row, not a squeezed seven-column table. Filters remain visible and meaningful; advanced
filters may collapse on narrow screens. Document-specific provenance and maintenance actions
remain reachable without dominating every row. Classify uses the same decision/record hierarchy
for unresolved groups and retains evidence, confidence, and reversible review behavior.

### Investments and investment import

When holdings are absent, make the two real entry routes clear and place analysis limitations in
one concise explanation. When present, distinguish holdings, valuation confidence, review work,
and analysis. Detailed coverage exclusions and technical methodology remain available without
occupying the first screen. Never imply a market value, return, price, or cost basis where the
server has not established one. Import keeps its existing review and commit stages explicit.

### Remaining destinations

Overview and Accounts keep their useful summaries but inherit the shared hierarchy. Transactions
and Spending use compact filters and legible contained data; Salary, Plans, Coverage, and Wealth
surface the next real task before long methodology or history. Every page retains its existing
owner workflow, recovery path, and linkable action. No screen gains a decorative metric or chart
without a truthful server-backed source.

## Interaction, accessibility, and privacy

Use semantic headings, labelled navigation and regions, native controls where possible, and
keyboard-operable selectors/disclosures. Focus remains visible and moves predictably after
navigation, disclosure, and action. State is not conveyed by color alone. Respect reduced motion;
motion responds to an action rather than animating every card. Check 360px, 588px, and desktop:
the page does not scroll horizontally; genuinely wide data scrolls within a named region.

All displayed amounts and statuses come from existing authenticated responses. Do not perform
money arithmetic in TypeScript or introduce inferred financial facts. Do not persist real ledger
screenshots or values in repository artifacts, test fixtures, logs, or review messages. UI tests
use synthetic data. Backend work, if a concrete gap is discovered later, needs a separate
contract and the owner/RLS/Decimal test floor before implementation.

## Acceptance

An owner can identify the current state and next action in the first viewport of each primary
screen, reach all existing operations without hunting through prose, and navigate long Settings
areas without losing deep links or unsaved edits. Documents and recurring records remain legible
on desktop and mobile. Existing financial, import, and correction workflows keep their meanings.
Affected component tests and live viewport/keyboard checks pass; the branch then follows the
repository's local review and exact-head verification gates.
