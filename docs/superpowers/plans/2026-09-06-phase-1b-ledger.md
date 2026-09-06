# Phase 1b — plan index for the ledger milestones

Phase 1b is planned **one milestone at a time**. Each milestone gets its own dated plan,
written immediately before its implementation against the tree at that time — the rule the
[Phase 1 specification §7](../specs/2026-09-06-phase-1-trusted-financial-position.md) already
sets. Scope, entry criteria, contracts and acceptance for every milestone live in the
[Phase 1b specification](../specs/2026-09-06-phase-1b-ledger.md), which stays authoritative
throughout. This file is the map between them; it contains no dispatchable tasks, and progress
is recorded only in [the progress file](../progress.md).

## Sequence, task numbers and plan files

| Milestone | Tasks | Plan |
|---|---|---|
| M1b — manual transactions and taxonomy | 12–15 | [M1b plan](2026-09-06-m1b-manual-transactions.md) — dispatchable once M1a merges |
| M2 — importer, one layout end-to-end | 16–20 | [M2 plan](2026-09-06-m2-importer.md) — re-verified against the tree at dispatch |
| M3 — the remaining layouts | 21 | written at its turn |
| M4 — classification | 22–23 | written at its turn |
| M5 — transfers and household outflows | 24 | written at its turn |
| M9a — early verified overview | 25 | written at its turn |
| M6 — credit cards | 26 | written at its turn |
| M7 — investments | 27 | written at its turn |
| M10 — documents and owner export | 28 | written at its turn |
| M9b — full history and attribution | 29 | written at its turn |
| Phase exit | 30 | written at its turn |

Task numbers continue the Phase 1 plan's sequence and are reserved above. The intended
migration sequence runs from V9 in execution order; a plan written after the sequence shifts
takes the next unapplied version and records it. Milestones marked order-independent in the
spec still dispatch sequentially — one implementation agent in the single active checkout.

## Findings banked for the future plans

Verified findings from the PR #28 review rounds that belong to later milestones, kept so each
dated plan starts from them instead of rediscovering them:

- **M4:** region rule packs load idempotently whenever an account opens in a new country —
  never a one-time migration snapshot (classification §3). The pack loader is one service path
  callable from the migration and from account creation. M4's migration also constrains the
  deferred bare-UUID references created by M1b and M2 (`source_document_id`,
  `receipt_document_id`, `statement_layout_id`, both `classification_rule_id` columns, the
  import-batch references) once it completes their target set.
- **M5:** the `0010` transfer shape cannot hold a pending one-sided transfer or a confirmed
  external outflow — it requires a destination account, a destination amount and both
  confirmed legs — while transfers §3 and ADR-0007 require exactly those states. `schema.md`
  is the bug: reconcile `0010` before writing the migration, as a reviewed docs delivery. The
  static check that no reporting query reads the base table also lands in M5.
- **M5–M7:** each `schema.md` `0018` deferred foreign key is applied by the forward migration
  that creates its own target (transfers, card statements, investment transactions); the loan
  and recurring clauses stay deferred with their features.
- **M9b:** reconcile the unapplied `0015` attribution shape with F03 before its migration;
  prove the frozen-flow-to-closing-rate FX bridge with dated fixtures; the residual stays
  visible (spec §3.10).
- **M10:** vault deletion stays blocked while a document is referenced (the constraint ships
  in M2's V11); the owner export builds in an owner-only temporary location with deterministic
  cleanup proven for the success and the failure path.
- **Phase exit:** the seven roadmap gates (spec §6), verified and recorded in the progress
  file; integration toward `develop` needs the separate owner request.
