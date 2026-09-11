# Design QA — product mock UI

- Focused Review source: `docs/product/mockups/Review.dc.html`, captured from the
  selected in-app browser at 479 × 585 CSS pixels.
- Shared application source: `docs/product/mockups/OneLedger.dc.html`; its
  Overview, Accounts, Transactions, Import and other supported-screen artboards
  supplied the persistent navigation rail and common workspace-shell reference.
- Source screenshot: in-app browser capture retained in the task evidence; it is
  not persisted in the repository.
- Implementation: `http://localhost:3000/review`, captured in the same selected
  browser and viewport with the local owner session.
- Implementation screenshot: in-app browser capture retained in the task
  evidence; it is not persisted because the rendered queue contains private
  statement descriptions.
- Comparison method: the source and implementation captures were emitted
  together in one visual comparison input at the same viewport.
- Scope: the classification workflow adapts the Review artboard's grouped queue,
  while the shared shell follows the OneLedger artboards' compact dark rail,
  serif headings and muted ledger palette. The Review surface adds reason labels,
  count pills, bordered cards and restrained controls. Import reconciliation and
  transfer matching remain in their existing dedicated workflows.
- Primary interactions checked: authenticated route, Review navigation state,
  loading-to-queue transition, category controls, disabled actions before a
  choice, and horizontal overflow at the artboard's narrow inspection viewport.
- Automated interaction coverage: preview scope, apply, conditional undo,
  reusable answers, one-row answers, incomplete scopes, conflicts, retry and
  empty state.
- Console/runtime check: the Next.js development server reported no compile or
  runtime error while the route loaded; browser-console extraction is not
  available in the selected in-app browser controls.

## Comparison history

1. The first full-view comparison found a P1 shell mismatch: the application
   stacked navigation above the queue, hiding the core Review content at the
   inspection viewport.
2. The shell was changed to the `OneLedger.dc.html` artboards' persistent
   132-pixel dark rail, compact active state, top-right currency control and
   horizontally preserved canvas.
3. The post-fix comparison showed the Review heading, first grouped card and
   controls beside the rail at the same viewport. No P0, P1 or P2 visual
   difference remained within this task's adapted classification-review scope.

Focused-region comparison was not needed: the narrow full-view capture rendered
the rail, heading, reason label, card typography, facts, category control and
action row large enough to judge.

## Overview — Screen set 01

- Primary source: `docs/product/mockups/OneLedger.dc.html`, whose published copy
  was byte-checked against the checked-in file before capture.
- Source screenshot: selected in-app browser capture at 1269 × 713 CSS pixels;
  retained in task evidence and not persisted in the repository.
- Implementation: `http://localhost:3000/`, captured in the selected in-app
  browser at 1280 × 720 CSS pixels after the dashboard completed loading.
- Implementation screenshot: retained in task evidence and not persisted because
  it contains private household figures.
- Comparison method: source and implementation full-view captures were inspected
  together. The product mock is primary; `Review.dc.html` remains supplemental
  for the grouped review screen only.
- Matched structure: fixed 132-pixel dark rail, pale neutral canvas, white sticky
  toolbar, compact reporting-currency toggle, owner badge, large serif net-worth
  hero, four position cards, current-month strip and lower reporting panels.
- Truth-preserving differences: the implementation shows the second reporting
  currency and carried-rate labels instead of an invented net-worth sparkline;
  it omits mock-only investment/property breakdowns, global search and a month
  selector until their data or interaction contracts exist. The twelve-month
  data stays in its semantic table and card obligations remain conditional.
- Interaction and accessibility check: currency selection remains functional,
  the owner session can sign out, navigation state is exposed, headings and
  regions name the financial surfaces, tables retain headers and visible focus
  treatment remains in the shared stylesheet.
- Automated coverage: selected-currency headline, both-currency position facts,
  position summary, month and trend currency switching, carried/missing rates,
  card dues, empty ledger, service failure and owner-toolbar identity.
- Runtime check: the screen completed its loading transition in the selected
  browser and the Next.js development server reported no compile or runtime
  error.

The comparison found no P0, P1 or P2 mismatch within the delivered Overview
contract. Unsupported mock capabilities remain absent rather than decorative or
fed with invented financial facts.

final result: passed
