# Design QA — grouped classification review

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

final result: passed
