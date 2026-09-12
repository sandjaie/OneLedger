# Implementation progress

The single maintained implementation-status tracker. It records current state, the next action,
open delivery gates and evidence links. Scope and sequence live in the roadmap and plans; design
reasoning lives in specifications and ADRs; completed run narratives and deviations live in dated
reports.

Updated: 12 September 2026.

## Current direction

| Item | Current state | Evidence |
| --- | --- | --- |
| Current phase | **Phase 1 — daily-use financial core** | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) |
| Next slice | **1.5 — investments**; product direction approved, design and planning not started | [ADR-0005](../adr/0005-transaction-level-investment-lots.html) |
| Next action | Design the investment boundary, obtain owner approval, then write and review its specification and execution plan | [development cycle](../development-cycle.md) |
| Implementation state | No slice 1.5 implementation branch has been started | — |

## Phase status

| Phase | Status | Plan |
| --- | --- | --- |
| 0 — foundations and manual ledger | Delivered; live Google identity gates remain open | [historical evidence](reports/2026-09-12-progress-history.md) |
| 1 — daily-use financial core | **Active** | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) |
| 2 — verified history and data quality | Not started | Planned when the phase starts |
| 3 — planning and resilience | Not started | Planned when the phase starts |
| 4 — analysis and optimisation | Not started | Planned when the phase starts |
| 5 — optional private intelligence | Not started | Planned when the phase starts |

Each phase ships as task-sized branches and pull requests into `develop`. Anything targeting
`main` requires a separate owner request.

## Local operation

| Capability | Status | Evidence |
| --- | --- | --- |
| Development stack | Working ledger retained for development and owner acceptance | [development cycle](../development-cycle.md) |
| Isolated local-production stack | Delivered; production-built services, private secrets, database and persistent volumes remain separate from development | [ADR-0018](../adr/0018-local-owner-sessions.html) · [ADR-0019](../adr/0019-separate-local-production-stack.html) |
| Isolated verification database | Delivered; live tests refuse the working ledger and use a per-run migrated database | [development cycle](../development-cycle.md) |
| Local delivery gates | Delivered; commit checks are fast and whole-project verification remains enforced before push | [development cycle](../development-cycle.md) |

## Phase 1 slices

| Slice | Status | Accepted capability | Evidence |
| --- | --- | --- | --- |
| 1.1 — statements land in the ledger | **Accepted, 9 Sep 2026** | Card statements parse, reconcile, stage, review and commit atomically; duplicate content imports no rows | [acceptance handoff](reports/2026-09-09-slice-1.1-handoff.md) |
| 1.2 — all four bank layouts | **Accepted, 11 Sep 2026** | All supported bank and card layouts, including encrypted statements, reconcile without staging unsupported files | [delivery history](reports/2026-09-12-progress-history.md) |
| 1.3 — classification and transfers | **Accepted, 11 Sep 2026** | Deterministic classification, owner decisions and confirmed transfers make reportable spending trustworthy | [delivery history](reports/2026-09-12-progress-history.md) · [ADR-0020](../adr/0020-assisted-ledger-decisions.html) |
| 1.4 — cards, net worth and trends | **Accepted, 11 Sep 2026** | Card dues, signed net worth, monthly flows and trends are verified in both reporting currencies | [delivery history](reports/2026-09-12-progress-history.md) |
| 1.5 — investments | **Next — design not started** | Transaction-level holdings, prices, returns, allocation and funding links | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) · [ADR-0005](../adr/0005-transaction-level-investment-lots.html) |
| 1.6 — salary and a thin forecast | Not started | Payslip income, confirmed commitments and an assumption-labelled 30/60/90-day projection | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) |
| 1.7 — layout mapper and documents | Not started | Self-service layout mapping, document library, provenance links and diagnostics | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) |
| 1.8 — the import asks | **Accepted, 12 Sep 2026** | Statements propose or reuse their PDF-derived account; owner decisions resolve unclassified rows; identifiers stay masked except for deliberate reveal | [identity spec](specs/2026-09-11-local-statement-identities.md) · [ADR-0021](../adr/0021-local-owner-financial-identifiers.html) |

The slices retain their published identifiers even though 1.8 was deliberately delivered before
1.5–1.7. Execution order is a product decision, not an instruction to renumber accepted history.

## Open delivery gates and deferred findings

| Item | State | Expected home | Evidence |
| --- | --- | --- | --- |
| Real permitted-account Google sign-in and protected access | Open owner-only gate | Phase 0 closeout | [historical evidence](reports/2026-09-12-progress-history.md) |
| Real second Google account rejection and chooser recovery | Open owner-only gate | Phase 0 closeout | [historical evidence](reports/2026-09-12-progress-history.md) |
| Approved phase names are not yet aligned across the master phase plan and product roadmap | Documentation update pending | Roadmap governance | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) · [product roadmap](../product/roadmap.html) |
| Review screen does not display whether an import period was derived | Deferred | Small `web/` delivery | [historical note](reports/2026-09-12-progress-history.md) |
| One encrypted layout can append a harmless repeated-header word to narration | Deferred | Slice 1.7 layout mapper | [historical note](reports/2026-09-12-progress-history.md) |
| Import screen does not yet surface card metadata-read failures | Deferred | Focused import-screen follow-up | [historical note](reports/2026-09-12-progress-history.md) |
| Documents volume has no independent backup copy | Open operations gap | Focused `scripts/` delivery | [historical incident note](reports/2026-09-12-progress-history.md) |
| Database restore drill with current row-count and totals evidence | Deferred | Phase 2 | [historical note](reports/2026-09-12-progress-history.md) |
| Transfer fees have no source-provided capture path | Deferred until a source exists | Later transfer-cost scope | [historical note](reports/2026-09-12-progress-history.md) |

## Historical evidence index

| Evidence | Location |
| --- | --- |
| Full pre-cleanup tracker snapshot, task tables, deviations, acceptance runs, recovery notes and deferred findings | [12 September delivery history](reports/2026-09-12-progress-history.md) |
| Phase 0 verification and dependency evidence | [Phase 0 verification](reports/2026-09-06-phase-0-verification.md) |
| Statement-importer gate and test evidence | [M2 delivery evidence](reports/2026-09-08-m2-delivery-evidence.md) |
| Slice 1.1 state, decisions and layout notes | [slice 1.1 handoff](reports/2026-09-09-slice-1.1-handoff.md) |
| Product scope, sequencing and acceptance criteria | [product roadmap](../product/roadmap.html) |
| Current phase sequence and slice outcomes | [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) |
