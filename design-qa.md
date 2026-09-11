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

## Accounts — Screen set 01

- Primary source: the Accounts artboard in
  `docs/product/mockups/OneLedger.dc.html`, captured from the same published,
  byte-checked source as Overview.
- Source and implementation captures: full-width selected in-app browser views
  retained in task evidence and not persisted because the implementation shows
  private balances and masked account identifiers.
- Comparison method: the source and `http://localhost:3000/accounts` captures
  were emitted together in one visual comparison input after the account data
  completed loading.
- Matched structure: compact serif page title, muted sync summary, monospaced
  section labels and totals, three-column responsive card grid, pale bordered
  cards, institution marks, native balance hierarchy, masks and update dates.
- Contract-preserving differences: the implementation groups first by asset or
  liability and then country, as product UI §6 requires over the raw artboard.
  It shows only supported ledger accounts, not invented investment or loan
  products, and keeps owner actions plus unacknowledged-opening evidence visible
  within each card.
- Interaction and accessibility check: semantic tables retain named columns and
  rows while CSS presents their rows as cards; add, edit, acknowledge,
  close/reopen and empty-inventory flows remain reachable and covered.
- Runtime check: the authenticated route loaded the real inventory without a
  compile or runtime error. No clipped balance, metadata, group total or action
  was visible in the full-width comparison.

The final comparison found no P0, P1 or P2 mismatch within the supported
Accounts contract.

## Transactions — Screen set 01

- Primary source: the Transactions artboard in
  `docs/product/mockups/OneLedger.dc.html`; `Review.dc.html` was not used as the
  visual source for this screen.
- Source and implementation captures: selected in-app browser views retained in
  task evidence and not persisted because the implementation contains private
  transaction descriptions and household figures.
- Comparison method: the source and `http://localhost:3000/transactions`
  captures were emitted together in one visual comparison input after the real
  ledger completed loading.
- Matched structure: compact serif title and ledger summary, shallow filter
  controls, restrained transfer action, and a dense five-column ledger with
  muted metadata and right-aligned amounts.
- Contract-preserving differences: filters supported by the real ledger remain
  available, with less-common controls behind More filters. Manual entry is a
  working disclosure rather than a permanent first-viewport form. The table
  retains inline category editing, notes, provenance, transfer linking, frozen
  FX evidence and pagination instead of replacing those capabilities with the
  mock's static rows.
- Interaction and accessibility check: the manual-entry toggle, primary and
  advanced filters, transfer discovery, semantic ledger table, inline category
  controls and pagination all remain reachable. The focused transaction-screen
  suite covers the disclosure state and existing ledger workflows.
- Runtime check: the authenticated route loaded the real ledger without a
  compile or runtime error; the first rows remained visible in the full-width
  comparison without clipping the date, account or amount columns.

The final comparison found no P0, P1 or P2 mismatch within the supported
Transactions contract.

## Statement import — Screen set 01

- Primary source: the Statement import artboard in
  `docs/product/mockups/OneLedger.dc.html`; its Documents navigation item opens
  this screen. `Review.dc.html` remains supplemental to the separate Review
  route only.
- Source and implementation captures: selected in-app browser views retained in
  task evidence. No statement, statement image, filename or household figure
  was persisted in the repository.
- Comparison method: the source and `http://localhost:3000/import` captures were
  emitted together after applying the same desktop viewport override, which was
  reset immediately after the comparison.
- Matched structure: compact serif title, provenance-first summary, large dashed
  upload workspace, centred statement guidance, restrained format label and
  bordered file-result cards with progress and status treatment.
- Contract-preserving differences: the real screen advertises PDF only because
  that is the implemented import contract; mock-only CSV and XLSX labels remain
  absent. The account override and Check statements action remain in the upload
  workspace because they drive the existing guarded parse flow. No processed
  count or example statement is invented for the empty state.
- Interaction and accessibility check: the file picker and PDF drop target feed
  the same multi-file candidate path; account detection or override, per-file
  password, parse verdict, account proposal, staging and review link remain
  functional and covered. A rejected file stays attached to its own result card.
- Runtime check: the authenticated empty state loaded without a browser-console
  error. Candidate-card states were exercised with synthetic component fixtures
  rather than uploading a private statement during visual QA.

The final comparison found no P0, P1 or P2 mismatch within the delivered PDF
statement-import contract.

final result: passed
