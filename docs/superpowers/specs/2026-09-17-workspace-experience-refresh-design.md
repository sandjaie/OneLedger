# Workspace experience refresh

## Outcome

OneLedger's owner workspace should feel like one product across every sidebar destination. The
owner should always be able to find navigation, see the next useful action, scan a record before
reading its details, and use dense screens at a narrow viewport without page-wide horizontal
scrolling. This is a presentation and interaction improvement; financial meanings, authorization,
and source-of-truth calculations do not change.

## Design direction

Keep the established ink, paper, mint, and IBM Plex visual language from Overview and the checked-in
product mock. Use a slightly wider ledger rail with grouped destinations, clear active state, and
a compact menu on narrow screens. The rail stays visible while the desktop page scrolls; the
mobile menu stays available from the toolbar and closes after navigation. The toolbar remains the
place for reporting currency and owner controls.

Across screens, use one page-header rhythm, one surface treatment for major sections, one concise
label/value treatment for record metadata, one prominent next action per context, and restrained
hover/focus affordances. Long prose becomes a short plain-language introduction with optional
explanation where lossless disclosure is needed. Avoid decorative icons or charts that do not
encode real data. Preserve exact strings for money and all existing warnings about incomplete data.

## Screen families

- Subscriptions and Recurring: scan-friendly record cards with label, amount/cadence, status,
  next or last date, and local actions. Keep detailed provenance reachable without making every
  list row a paragraph. Keep subscription lifecycle and correction controls unchanged.
- Documents and Classify: clearly separated filter/review areas, contained scroll for dense
  tables, visible record-level actions, and reasons or recovery text next to the relevant item.
- Investments and Import investments: distinguish portfolio summary, holdings, price confidence,
  import setup, and review steps. No implied value for unvalued holdings.
- Spending, Salary, Plans, Coverage, Wealth, and Settings: group tasks into consistent panels;
  give tables and charts their own bounded scrolling; make anchor and action controls recognizable.
- Overview, Accounts, Transactions, and Import: retain their stronger existing structure while
  inheriting the corrected shell and responsive rules.

## Interaction and accessibility

Navigation uses labelled landmarks and current-page state. The narrow-screen menu exposes its
expanded state, can be dismissed, and never traps focus. Interactive surfaces have visible hover
and keyboard focus, real buttons or links, and adequate touch targets. Errors and empty states
give a next step when one exists. The page itself should fit 360px, 588px, and desktop viewports;
wide tables may scroll inside a labelled or semantically clear container. Reduced-motion users
receive no nonessential animation.

## Scope and verification

Prefer frontend-only changes. Backend contracts change only if a specific UI goal cannot be met
with existing data; such a change must retain all owner, RLS, precision, and invariant tests.
Verify affected web tests, lint, typecheck, build, then inspect every sidebar destination in the
running app at desktop and narrow widths. Use structural observations only; do not capture or
commit real financial screenshots or values.
