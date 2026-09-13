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
later. The budget is **owned by [docs/git-workflow.md](../../../docs/git-workflow.md)** and
enforced at push by `scripts/check-diff-size.sh`; the table below restates it for reading, and
that file wins if the two ever disagree:

| | Dispatch target | Warning | Hard stop |
|---|---:|---:|---:|
| Production lines added | 500 | 800 | 1,200 |
| Production + test lines added | 1,000 | 1,600 | 2,400 |

Documentation, configuration and generated files do not count. Past the hard stop,
`ONELEDGER_SIZE_OVERRIDE="reason"` allows the push and the same reason goes in the PR's Notes —
an indivisible migration is the case it exists for.

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

The consequence, stated plainly: **a large module plus its tests spends the whole budget.**
`accounts.py` and its test are 1,682 counted lines; `layouts.py` and its test are 1,758. Both are
over. A small module leaves room for a second — `categories.py` and its test are around 600 — so
the question is never "how many files" but "how many lines, measured against the list above".

A task that says "the extraction engine, quirks, and account identification" is still three or
four tasks. A task that adds two endpoints and a screen is two. Slice below the module wherever
the forecast says so: one endpoint, one behaviour cluster, one migration — each with its own
branch.

For every task, write the forecast into the finding: files, estimated production lines, estimated
test lines, verdict and review headroom. The 500/1,000 dispatch target is deliberately 60–70% of
the warning threshold so security and correctness repairs do not force a late split. A task
forecast above that target is **split before dispatch**, and the split is named — sub-tasks in
dependency order, each with its own `<type>/<name>` branch off `develop`. Crossing the formal
800/1,600 warning because the estimate was wrong is not permission to keep expanding scope.

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
- **A financial write without its operation matrix.** Every new SQL function, write route,
  replay, correction or import commit names tests for owner/member/foreign-household behavior,
  direct-DML refusal and RLS, stale expected state and idempotency, precision before hashing or
  deduplication, correction leaves/history, lock order/concurrency and atomic rollback. A task
  that cannot cover that matrix inside the dispatch target splits at the operation boundary.

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
