# Product mock UI implementation plan

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Base and PR target:** `develop`
**Primary reference:** `docs/product/mockups/OneLedger.dc.html`
**Supplemental review reference:** `docs/product/mockups/Review.dc.html`

This plan applies the checked-in product mock to the screens whose Phase 1 data contracts already
exist. It does not start investments, loans or any other undelivered slice. Where example figures
or controls in the artboard require data OneLedger does not yet expose, the existing truthful
surface remains; no value is invented and no money arithmetic moves into TypeScript.

## Delivery map

| Task | Branch | Outcome |
| --- | --- | --- |
| 1 — Overview and shared frame | `feature/product-mock-overview` | The persistent rail, toolbar, typography, colour system and Overview composition match Screen set 01 while preserving the dashboard contract. This plan lands with the task. |
| 2 — Accounts | `feature/product-mock-accounts` | Accounts use the mock's compact ledger rows, grouped by class then country as required by the UI architecture. |
| 3 — Transactions | `feature/product-mock-transactions` | The ledger, filters, transfer treatment and category editing adopt the mock's dense transaction layout. |
| 4 — Statement import | `feature/product-mock-import` | Upload, parse feedback and staging controls adopt the Statement import artboard without weakening reconciliation or password handling. |

Each task starts from the then-current `develop`, stays within the repository diff budget, follows
test-first implementation, runs `/verify`, receives one focused local review, and opens its own PR.
The Overview task owns shared-frame changes; later tasks only extend the screen-specific language.

## Task 1 — Overview and shared frame

Add failing component tests for the glanceable hierarchy: one selected-currency net-worth
headline, the other reporting currency as context, truthful position summaries, the current month,
the twelve-month table and conditional card obligations. Keep all values sourced from
`GET /dashboard` and formatted from decimal strings. Then reshape the application frame and
Overview markup, and add responsive CSS derived from `OneLedger.dc.html`.

Design QA compares the checked-in mock and the local application at the same desktop viewport.
Because the local application contains private financial data, screenshots remain ephemeral; only
the source path, viewport, comparison result and structural findings are written to `design-qa.md`.

## Tasks 2–4

For each screen, capture its artboard before editing, add a failing structural interaction test,
implement only existing capabilities, run the targeted suite, and compare the finished local screen
with the reference at matching viewport and state. Controls visible in the implementation must work;
mock-only controls are omitted until their supporting product slice exists.

## Acceptance

- Navigation, focus treatment, contrast and semantic tables remain accessible.
- Both reporting currencies and every monetary value retain the existing formatting and data source.
- Loading, empty, partial-data and error states remain explicit.
- No real financial value, institution, country, statement image or screenshot enters Git.
- `design-qa.md` records `passed` only after side-by-side visual comparison; otherwise it records
  `blocked` and the exact remaining issue.
