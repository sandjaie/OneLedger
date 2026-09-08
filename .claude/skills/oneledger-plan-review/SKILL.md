---
name: oneledger-plan-review
description: Audit of a OneLedger implementation plan or spec before any task is dispatched — task size against the diff budget, execution-map shape, contracts and invariants stated, no embedded implementations, and agreement with the ADRs and design documents. It produces a revised task and branch table for another agent to apply, and does not modify the plan or any code. Run it unprompted whenever a plan or spec has been written or changed and a task is about to be dispatched from it, and whenever asked to review, size or split a plan.
---

# oneledger-plan-review

Superpowers writes the plan; this reads it before anyone builds from it. Splitting a task on paper
costs a paragraph. Discovering at review time that the task was 3,380 lines costs eight review
rounds and a day — which is what M2 task 3 cost.

**Produce findings another agent can patch. Do not modify the plan or any code** unless the user
explicitly commands fix mode. The report carries a revised task and branch table; the planning
agent or orchestrator applies it and re-runs this gate. Work begins when no blocking finding is
open.

It is gate 2 of [docs/development-cycle.md](../../../docs/development-cycle.md); its trigger is a
plan or spec having just been written or changed, with a task about to be dispatched from it.
Reviewing after dispatch is too late — by then a split costs an implementation, not a paragraph.

## 1. Size every task against the budget

This is the check that matters most, because it is the only one that is cheap now and expensive
later. The budget, enforced at push by `scripts/check-diff-size.sh`:

| | Budget | Hard stop |
|---|---|---|
| Production lines added | 400 | 500 |
| Production + test lines added | 800 | 1000 |

Documentation, configuration and generated files do not count.

**Forecast each task by naming its files and sizing them against what this repository already
has.** These are the real numbers; use them, not optimism:

| Thing | Actual size in this tree |
|---|---|
| A route module with real validation | `currencies.py` 33 · `household.py` 140 · `fx.py` 183 · `categories.py` 226 · `transactions.py` 612 · `layouts.py` 622 · `accounts.py` 673 |
| Its test file | 1.5–2× the module: `test_transactions.py` 673 · `test_ingest_engine.py` 918 · `test_accounts.py` 1009 · `test_layouts.py` 1136 |
| A migration | `V3` 86 · `V7` 96 · `V12` 173 · `V9` 274 · `V10` 473 |
| Its SQL invariant test | `0010` 184 · `0008` 196 · `0009` 617 |
| A web screen | `categories-manager.tsx` 200 · `workspace.tsx` 437 · `transactions-screen.tsx` 607 · `accounts-screen.tsx` 785 |
| Its component test | 0.6–1× the screen: 377 · 433 · 474 |

The consequence, stated plainly: **one module plus its tests already spends the whole budget.** A
task that says "the extraction engine, quirks, and account identification" is four tasks. A task
that adds two endpoints and a screen is three. Slice below the module: one endpoint, one
behaviour cluster, one migration — each with its own branch.

For every task, write the forecast into the finding: files, estimated production lines, estimated
test lines, verdict. Any task over 400/800 is **split**, and the split is named — sub-tasks in
dependency order, each with its own `<type>/<name>` branch off `develop`.

A migration that genuinely cannot be divided is the one legitimate exception, and it rides with
the smallest possible surface around it — the migration, its invariant test, nothing else.

## 2. Check the shape

Per `CLAUDE.md`, a plan is an execution map, not a pre-written patch. Each task states:

outcome · affected files and modules · dependencies · interfaces and contracts · invariants ·
security requirements · required test **behaviours** · validation commands · acceptance criteria ·
`Workspace`, `Branch`, `Base and PR target`.

Findings to raise:

- **Embedded implementations.** Code belongs in a plan only as (1) an exact cross-task interface,
  (2) a schema or API contract, (3) a non-obvious invariant or algorithm prose cannot pin down, or
  (4) a tiny deterministic change. A function body with logic in it is a finding. Phase 1's plan
  was 63% code by line; M2's was 11%; the target is "only the four cases".
- **Design rationale living in the plan.** Architecture decisions, table semantics and storage
  contracts belong in `docs/superpowers/specs/`, `docs/design/` or an ADR. M2's plan carried ~480
  lines of them and had no spec file at all. Say where each section should move.
- **Test implementations.** The plan names behaviours that must hold; it does not write the tests.
- **Missing branch declarations.** A task without `Branch` and `Base and PR target` before
  dispatch is a task the implementer will invent a branch for.
- **Budget on the document itself.** A task block over ~40 lines, or a plan over ~400, is
  carrying something that belongs elsewhere.
- **Status tables.** Progress lives only in `docs/superpowers/progress.md`. A plan with
  completion checkboxes is a second tracker.

## 3. Check it against the documents in force

Read what the plan claims against what the repository actually says: the ADRs
(`docs/adr/index.html`), `docs/design/`, `docs/operations/`, `docs/superpowers/schema.md`, and the
current tree. A plan that contradicts a design document is a bug in one of them — **name which
one and say how it is resolved**, rather than leaving the implementer to discover it mid-task and
guess.

Confirm the plan respects the standing rules it will otherwise break by reflex: money as
`Decimal`/`NUMERIC(20,4)`/string, no currency or institution name as an identifier, reports
reading `v_transaction_reportable`, `require_owner()` on financial routes, forward-only
migrations, never-store list, no LLM calls in Phase 1 or 2 (ADR-0003).

## 4. Report

Produce:

1. **A verdict** — dispatch as written, or dispatch after these splits.
2. **A revised task table** — number, title, branch, forecast production/test lines, dependencies,
   in dependency order.
3. **Findings**, each naming the section and what to do about it.

Hand it to the orchestrator, which edits the plan and updates
`docs/superpowers/progress.md` before dispatching the first task.
