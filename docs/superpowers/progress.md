# Implementation progress

The single maintained implementation-status tracker. It records the canonical project position,
the next action, active delivery gates, and links to evidence. Product outcomes live in the
[roadmap](../product/roadmap.html), delivery order in the
[master implementation plan](../product/master-implementation-plan.html), and deferred or
unapproved information in the [project register](project-register.md).

Updated: 14 September 2026.

## Current position

| Item | Current state | Evidence |
| --- | --- | --- |
| Current outcome | **Phase 1.6 — investments** | [master plan](../product/master-implementation-plan.html#phase-1-delivery) |
| Outcome state | **Implementation active** — re-baselined to personal scale on 14 Sep 2026 with owner approval | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md) |
| Completed documentation action | PR #164 merged at `e501536` and established this tracker structure | [documentation design](specs/2026-09-12-project-documentation-architecture-design.md) |
| Next product action | Deliver Task 15.2 — holding-level unit actions | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-152--holding-level-unit-actions) |
| Current implementation task | Task 15.2 is delivered on `feature/p1-6o2-investment-unit-actions`: split, bonus, rights, switch, systematic transfer and platform transfer unit actions | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-152--holding-level-unit-actions) |

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
| Phase 1.6 Task 10 | **Complete via PR #175** — exact cash candidates, explicit many-to-many proposals/confirmation, truthful uncertain-outcome recovery and reviewed unlink are on `develop` | [PR #175](https://github.com/sandjaie/OneLedger/pull/175) |
| Phase 1.6 Task 11 | **Split before implementation** — dispatch reforecast exceeded the review budget; Tasks 11.1–11.3 now separate core extraction, evidence coverage and grouped unit actions/staged ratios | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-111--adapter-contract-and-core-detailed-extraction) |
| Phase 1.6 Task 11.1 | **Complete** — pure content selection, frozen versioned results, exact core event drafts, safe abstention and private evidence representations are on `develop` | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-111--adapter-contract-and-core-detailed-extraction) |
| Phase 1.6 Task 11.2 | **Complete** — exact income, fee and tax drafts plus end-position checkpoints, page-break coverage, precision refusal and hostile-evidence privacy are on `develop` | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-112--income-checkpoints-and-hostile-evidence) |
| Phase 1.6 Task 11.3 | **Complete** — exact evidence-grouped unit actions, group-local abstention and forward-only staged split ratios are on `develop` | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-113--grouped-unit-actions-and-staged-split-ratios) |
| Phase 1.6 Task 12 | **Split before implementation** — dispatch reforecast exceeded the review budget; Tasks 12.1–12.3 now separate pure preparation, atomic persistence/source-scoped dedupe and the owner API/cancellation surface | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-121--preparation-and-safe-preview-contract) |
| Phase 1.6 Task 12.1 | **Complete** — explicit UUID resolutions prepare exact immutable drafts and checkpoints, correlated legs share one atomic group, and previews remain bounded and privacy-safe | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-121--preparation-and-safe-preview-contract) |
| Phase 1.6 Task 12.2 | **Complete** — validated owner-scoped targets stage under one household transaction; retries, cross-document evidence overlap, group-local blockers and terminal cancellation retain provenance without changing financial facts | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-122--idempotent-atomic-persistence) |
| Phase 1.6 Task 12.3 | **Complete** — owner-authenticated write-free previews, explicit UUID resolution, atomic staging/replay and idempotent terminal cancellation are available through the application API | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-123--owner-api-and-cancellation-surface) |
| Phase 1.6 Task 13 | **Complete** — content-selected summary statements emit only exact units/cost/value checkpoints; owner-scoped reconciliation confirms or disputes committed positions without manufacturing events or terminal prices | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-13--summary-checkpoints) |
| Phase 1.6 Task 14 | **Complete across Tasks 14.1–14.3** — exact cash lifecycle, owner APIs and explicit allocation are on `develop` | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-141--cash-lifecycle-database-operation) |
| Phase 1.6 Task 14.1 | **Complete** — V37 admits exact cash distributions, interest, reinvested distributions, standalone charges and withheld tax through one atomic owner-authorized operation without manufacturing cash, units or lots | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-141--cash-lifecycle-database-operation) |
| Phase 1.6 Task 14.2 | **Complete** — owner-authenticated disposal and cash-lifecycle APIs retain exact frozen facts, count embedded tax once, append corrections and use V38 to resolve superseded incomplete-lot review without reopening history or exposing inferred basis or gain | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-142--disposal-and-income-owner-api) |
| Phase 1.6 Task 14.3 | **Complete** — all four V33 cash roles share explicit proposal/confirmation and exact unlink restoration; V39 adds database-owned immutable lot decisions, replay-safe dated proposals, active-leaf allocation/correction serialization, canonical evidenced-basis claims and complete-review resolution without guessing lot order | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-143--evidenced-lot-and-cash-allocation) |
| Phase 1.6 positions disposal fix | **Deviation from Task 8** — positions now value units from every active unit effect, so disposals and reinvestments change current value and net worth; gain is withheld once any non-purchase event is active | [PR #193](https://github.com/sandjaie/OneLedger/pull/193) |
| Phase 1.6 Task 15 | **Re-baselined 14 Sep 2026** — owner approved holding-level corporate actions; Task 15.1 (V40) is kept as the foundation; Tasks 15.2–24 are replaced by Tasks 15.2 and 16–20 | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-152--holding-level-unit-actions) |
| Phase 1.6 Task 15.1 | **Complete via PR #194** — V40 adds household-scoped lot lineage, the unit-event order guard, lineage-aware open lots, active-event lot capacity and consumed-lot refusal; review findings repaired and confirmed | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-151--lot-lineage-read-model-and-guards) |
| Phase 1.6 Task 15.2 | **Delivered on `feature/p1-6o2-investment-unit-actions`** — V41 adds owner-only `append_investment_split` (exact holding-level delta, no lot) and `append_investment_unit_transfer` (paired switch/systematic-transfer/platform-transfer legs, all-or-nothing); `append_investment_transaction` additionally admits `bonus` and `rights`; lot lineage and a new `investment_lot_allocation` trigger withhold basis and realised claims from an active split, switch, systematic transfer or platform transfer; positions and checkpoint reconciliation apply the same distinction | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-152--holding-level-unit-actions) |
| Phase 1.6 year-to-date summary | **Delivered on `feature/p1-6-year-to-date-summary`** — overview shows this year's income, spending, savings and investment contributions | [API contracts](../design/api-contracts.html) |

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
