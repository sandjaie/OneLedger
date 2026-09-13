# Implementation progress

The single maintained implementation-status tracker. It records the canonical project position,
the next action, active delivery gates, and links to evidence. Product outcomes live in the
[roadmap](../product/roadmap.html), delivery order in the
[master implementation plan](../product/master-implementation-plan.html), and deferred or
unapproved information in the [project register](project-register.md).

Updated: 13 September 2026.

## Current position

| Item | Current state | Evidence |
| --- | --- | --- |
| Current outcome | **Phase 1.6 — investments** | [master plan](../product/master-implementation-plan.html#phase-1-delivery) |
| Outcome state | **Implementation active** — design, owner approval and plan review are complete | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md) |
| Completed documentation action | PR #164 merged at `e501536` and established this tracker structure | [documentation design](specs/2026-09-12-project-documentation-architecture-design.md) |
| Next product action | Controller review and delivery of Task 9 — catalogue, purchase, price and holding-entry web flow | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-9--catalogue-purchase-price-and-holding-entry-web-flow) |
| Current implementation task | Task 9 adds an owner-only investment entry workspace that resolves catalogue evidence, confirms exact acquisition facts, optionally records a dated manual price and reloads the resulting acquisition-only holding; local implementation verification is complete and controller review remains | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-9--catalogue-purchase-price-and-holding-entry-web-flow) |

## Canonical phase status

| Phase | Status | Evidence or next reference |
| --- | --- | --- |
| 0.1 — local runtime and repository safeguards | **Accepted** | [Phase 0 evidence](reports/2026-09-06-phase-0-verification.md) |
| 0.2 — household data and authorization boundary | **Accepted**; two owner-operated identity gates remain open | [Phase 0 evidence](reports/2026-09-06-phase-0-verification.md) |
| 0.3 — FX and reporting foundation | **Accepted** | [Phase 0 evidence](reports/2026-09-06-phase-0-verification.md) |
| 0.4 — accounts and manual ledger | **Accepted** | [delivery history](reports/2026-09-12-progress-history.md) |
| 0.5 — import and document foundation | **Accepted** | [import evidence](reports/2026-09-08-m2-delivery-evidence.md) |
| 1.1 — statements land in the ledger | **Accepted, 9 Sep 2026** | [acceptance handoff](reports/2026-09-09-slice-1.1-handoff.md) |
| 1.2 — all supported statement layouts | **Accepted, 11 Sep 2026** | [delivery history](reports/2026-09-12-progress-history.md) |
| 1.3 — classification and transfers | **Accepted, 11 Sep 2026** | [delivery history](reports/2026-09-12-progress-history.md) · [ADR-0020](../adr/0020-assisted-ledger-decisions.html) |
| 1.4 — cards, net worth, and trends | **Accepted, 11 Sep 2026** | [delivery history](reports/2026-09-12-progress-history.md) |
| 1.5 — statement-guided onboarding and owner decisions | **Accepted, 12 Sep 2026** | [identity specification](specs/2026-09-11-local-statement-identities.md) · [ADR-0021](../adr/0021-local-owner-financial-identifiers.html) |
| 1.6 — investments | **In progress** | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md) |
| 1.7 — salary and a thin forecast | **Planned** | [master plan](../product/master-implementation-plan.html#phase-1-delivery) |
| 1.8 — layout mapper and documents | **Planned** | [master plan](../product/master-implementation-plan.html#phase-1-delivery) |
| 2.1 — complete historical coverage | **Planned** | [master plan](../product/master-implementation-plan.html#phase-2-delivery) |
| 2.2 — wealth history and attribution | **Planned** | [master plan](../product/master-implementation-plan.html#phase-2-delivery) |
| 2.3 — classification learning and review depth | **Planned** | [master plan](../product/master-implementation-plan.html#phase-2-delivery) |
| 2.4 — data quality and recovery | **Planned** | [master plan](../product/master-implementation-plan.html#phase-2-delivery) |
| 3.1 — commitments and actions | **Planned** | [master plan](../product/master-implementation-plan.html#phase-3-delivery) |
| 3.2 — cash forecast and monthly review | **Planned** | [master plan](../product/master-implementation-plan.html#phase-3-delivery) |
| 3.3 — goals and emergency reserve | **Planned** | [master plan](../product/master-implementation-plan.html#phase-3-delivery) |
| 3.4 — protection and administration | **Planned** | [master plan](../product/master-implementation-plan.html#phase-3-delivery) |
| 4.1 — budgets and recurring discovery | **Planned** | [master plan](../product/master-implementation-plan.html#phase-4-delivery) |
| 4.2 — salary analysis | **Planned** | [master plan](../product/master-implementation-plan.html#phase-4-delivery) |
| 4.3 — investment analysis | **Planned** | [master plan](../product/master-implementation-plan.html#phase-4-delivery) |
| 4.4 — FX and remittance analysis | **Planned** | [master plan](../product/master-implementation-plan.html#phase-4-delivery) |
| 4.5 — optional email ingestion | **Planned** | [master plan](../product/master-implementation-plan.html#phase-4-delivery) |
| 4.6 — conditional loan support | **Conditional** — no real loan has been confirmed | [master plan](../product/master-implementation-plan.html#phase-4-delivery) |
| 5.1 — privacy, safety, and runtime decision | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.2 — Ask My Finances | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.3 — classification fallback | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.4 — financial insights | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.5 — scenario simulator | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.6 — tax reporting | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.7 — portfolio rebalancing | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |
| 5.8 — automated imports | **Planned** | [master plan](../product/master-implementation-plan.html#phase-5-delivery) |

Each phase is delivered through reviewable task branches into `develop`. Work targeting `main`
requires a separate owner request.

## Active delivery gates

| Gate | State | Evidence or completion path |
| --- | --- | --- |
| Real permitted-account Google sign-in and protected access | Owner-operated evidence open; does not block Phase 1.6 design | [historical evidence](reports/2026-09-12-progress-history.md) |
| Real second-account rejection and chooser recovery | Owner-operated evidence open; does not block Phase 1.6 design | [historical evidence](reports/2026-09-12-progress-history.md) |
| Phase 1.6 Task 1 | **Complete via PR #165** after full `/verify`, all ten local-review findings resolved, confirmation approval and green pre-push gates; 1,351 API tests ran with live database paths enabled | [PR #165](https://github.com/sandjaie/OneLedger/pull/165) |
| Phase 1.6 Task 2 | **Complete via PR #166** — immutable events, rebuildable holdings and acquisition-origin lots are on `develop` | [PR #166](https://github.com/sandjaie/OneLedger/pull/166) |
| Phase 1.6 Task 3 | **Complete via PR #167** — V32 and its invariant suite passed full `/verify`; all four local-review findings were resolved and confirmation review found no remaining issues | [PR #167](https://github.com/sandjaie/OneLedger/pull/167) |
| Phase 1.6 Task 4 | **Complete via PR #169** — V33 enforces owner-authorized many-to-many cash links, exact unlink restoration, disposal-lot allocation and legacy-column retirement | [PR #169](https://github.com/sandjaie/OneLedger/pull/169) |
| Phase 1.6 Task 5 | **Complete via PR #170** — catalogue resolution, private investment identifiers and append-only manual price observations are on `develop` | [PR #170](https://github.com/sandjaie/OneLedger/pull/170) |
| Phase 1.6 Task 6 | **Complete via PR #171** — exact owner-entered acquisitions and idempotent append-only corrections are on `develop` | [PR #171](https://github.com/sandjaie/OneLedger/pull/171) |
| Phase 1.6 Task 7 | **Complete via PR #172** — purchase funding proposals, correction-safe atomic confirmation, final-debit selection and exact unlink restoration are on `develop` | [PR #172](https://github.com/sandjaie/OneLedger/pull/172) |
| Phase 1.6 Task 8 | **Complete via PR #173** — acquisition-only positions, typed price/FX coverage and exclusive ledger/holdings/snapshot dashboard valuation are on `develop` | [PR #173](https://github.com/sandjaie/OneLedger/pull/173) |
| Phase 1.6 Task 9 | **Complete via PR #174** — catalogue resolution, explicit acquisition confirmation, optional dated price entry and resulting holding display are on `develop` | [PR #174](https://github.com/sandjaie/OneLedger/pull/174) |
| Phase 1.6 Task 10 | **Implementation ready for controller review** — exact cash candidates, many-to-many funding proposals, explicit confirmation, stale-state refusal, transfer-path explanation and reviewed unlink are implemented locally; no push or PR yet | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-10--funding-confirmation-web-flow) |

Longer-horizon findings and decisions are maintained in the
[project register](project-register.md), not in this live-gate table.

## Local operation

| Capability | State | Evidence |
| --- | --- | --- |
| Development stack | Working ledger retained for development and owner acceptance | [development cycle](../development-cycle.md) |
| Isolated local-production stack | Delivered with separate services, secrets, database, documents, ports, and persistent volumes | [ADR-0018](../adr/0018-local-owner-sessions.html) · [ADR-0019](../adr/0019-separate-local-production-stack.html) |
| Isolated verification database | Delivered; live tests refuse the working ledger and use a per-run migrated database | [development cycle](../development-cycle.md) |
| Local delivery gates | Delivered; fast commit checks and whole-project verification remain enforced | [development cycle](../development-cycle.md) |

## Historical evidence index

| Evidence | Location |
| --- | --- |
| Full pre-structure tracker snapshot, delivery narratives, deviations, and deferred findings | [12 September delivery history](reports/2026-09-12-progress-history.md) |
| Foundations verification and dependency evidence | [Phase 0 verification](reports/2026-09-06-phase-0-verification.md) |
| Statement-importer gate and test evidence | [import delivery evidence](reports/2026-09-08-m2-delivery-evidence.md) |
| Phase 1.1 state, decisions, and layout notes | [Phase 1.1 handoff](reports/2026-09-09-slice-1.1-handoff.md) |
| Product outcomes and acceptance criteria | [product roadmap](../product/roadmap.html) |
| Canonical sequence and dependencies | [master implementation plan](../product/master-implementation-plan.html) |
