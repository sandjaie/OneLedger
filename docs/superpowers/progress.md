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
| Current outcome | **Phase 2.3 — classification learning and review depth** | [master plan](../product/master-implementation-plan.html#phase-2-delivery) |
| Outcome state | **Implementation active** — Phases 2.2–2.4 planned together and reviewed once on 14 Sep 2026 | [Phase 2.3 plan](plans/2026-09-14-phase-2-3-classification-learning.md) |
| Next product action | Deliver Phase 2.4 — data quality and recovery | [Phase 2.4 plan](plans/2026-09-14-phase-2-4-data-quality-recovery.md) |
| Current implementation task | Phase 2.2 Tasks 1 and 2 delivered (`feature/p2-2a-month-end-positions`, `feature/p2-2b-attribution`); Phase 2.3 Task 1 delivered (`feature/p2-3-classification-learning`); Phase 2.4 is next | [Phase 2.4 plan](plans/2026-09-14-phase-2-4-data-quality-recovery.md) |

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
| 1.6 — investments | **Implementation complete; owner acceptance open** | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md) |
| 1.7 — salary and a thin forecast | **Implementation complete; owner acceptance open** | [Phase 1.7 specification](specs/2026-09-14-phase-1-7-salary-forecast.md) · [Phase 1.7 plan](plans/2026-09-14-phase-1-7-salary-forecast.md) |
| 1.8 — layout mapper and documents | **Implementation complete; owner acceptance open** | [Phase 1.8 specification](specs/2026-09-14-phase-1-8-layout-mapper-documents.md) · [Phase 1.8 plan](plans/2026-09-14-phase-1-8-layout-mapper-documents.md) |
| 2.1 — complete historical coverage | **Implementation complete; owner acceptance open** | [Phase 2.1 specification](specs/2026-09-14-phase-2-1-history-coverage.md) · [Phase 2.1 plan](plans/2026-09-14-phase-2-1-history-coverage.md) |
| 2.2 — wealth history and attribution | **Implementation complete; owner acceptance open** | [Phase 2.2 specification](specs/2026-09-14-phase-2-2-wealth-history-attribution.md) · [Phase 2.2 plan](plans/2026-09-14-phase-2-2-wealth-history-attribution.md) |
| 2.3 — classification learning and review depth | **Implementation complete; owner acceptance open** | [Phase 2.3 specification](specs/2026-09-14-phase-2-3-classification-learning.md) · [Phase 2.3 plan](plans/2026-09-14-phase-2-3-classification-learning.md) |
| 2.4 — data quality and recovery | **Planned** | [Phase 2.4 plan](plans/2026-09-14-phase-2-4-data-quality-recovery.md) |
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

Completed task rows are not kept here; each delivered task is its merged PR (#165–#207), and
the pre-structure narrative lives in the [12 September delivery history](reports/2026-09-12-progress-history.md).

| Gate | State | Evidence or completion path |
| --- | --- | --- |
| Real permitted-account Google sign-in and protected access | Owner-operated evidence open; does not block Phase 2 planning | [historical evidence](reports/2026-09-12-progress-history.md) |
| Real second-account rejection and chooser recovery | Owner-operated evidence open; does not block Phase 2 planning | [historical evidence](reports/2026-09-12-progress-history.md) |
| Phase 1.6 owner acceptance | **Open** — delivered via PRs #165–#201; run the real detailed and summary statements through the app and record structural pass/fail counts | [Phase 1.6 plan](plans/2026-09-12-phase-1-6-investments.md#task-20--portfolio-web-and-phase-16-acceptance) |
| Phase 1.7 owner acceptance | **Open** — delivered via PRs #202–#204; deviation from Task 3 on `chore/phase-1-hygiene`: unticking a deduction line also removes its label's saved rule, so it is not re-posted on a later link; run real payslips through the application and record only structural pass/fail counts | [Phase 1.7 specification](specs/2026-09-14-phase-1-7-salary-forecast.md#acceptance) |
| Phase 1.8 owner acceptance | **Open** — delivered via PRs #205–#206; criteria 1–3 are exercised by the automated suites; criterion 4 is owner-operated: remap the encrypted layout whose narration keeps a repeated header word, then re-read descriptions for its completed imports, recording only structural counts (rows compared, descriptions changed, rows queued) and never real narration text | [Phase 1.8 plan](plans/2026-09-14-phase-1-8-layout-mapper-documents.md#acceptance) |
| Phase 2.1 owner acceptance | **Open** — delivered via PR #207; after importing all available real statements, record the structural counts per coverage status only | [Phase 2.1 specification](specs/2026-09-14-phase-2-1-history-coverage.md#acceptance) |
| Phase 2.2 owner acceptance | **Open** — delivered via PRs #209–#210; after real imports, refresh wealth history and record structural counts of complete months and months with a non-zero residual only | [Phase 2.2 specification](specs/2026-09-14-phase-2-2-wealth-history-attribution.md#acceptance) |
| Phase 2.3 owner acceptance | **Open** — delivered on `feature/p2-3-classification-learning`; applying a suggestion to past rows uses the previewed `/classify?ruleId=` pass (unclassified rows only, no undo) instead of chunked classification runs | [Phase 2.3 specification](specs/2026-09-14-phase-2-3-classification-learning.md#acceptance) |

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
