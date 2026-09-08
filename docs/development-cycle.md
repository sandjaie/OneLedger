# Development cycle

The single ordered reference for how work moves from a plan to a merge, and for the gate that
stands at each step. [docs/git-workflow.md](git-workflow.md) owns branches, integration and
releases; this file owns **when a gate runs, who runs it, and what it produces**. When the two
disagree about a branch, git-workflow.md wins; when they disagree about a gate, this file wins.

Every agent working in this repository — planning, implementing, reviewing or orchestrating —
runs the gates below **without being asked**. A gate is not a favour to the owner; it is the step
that stops a finding from costing a review round.

## The review skills report

`/oneledger-plan-review`, `/oneledger-code-review` and `/oneledger-code-hygiene` **produce
findings another agent can patch. They do not modify code** unless the owner explicitly commands
fix mode. A reviewer that fixes what it finds hides the finding from whoever needed to learn it,
and reports on a tree that no longer matches the one it reviewed.

**The report goes back to the agent that produced the work** — the planning agent for a plan, the
implementing agent for a diff — which applies the changes on the branch and re-runs the gate. The
orchestrator carries the report between them and records any deviation in
`docs/superpowers/progress.md`.

| Skill | Reads | Produces | Never |
|---|---|---|---|
| `/oneledger-plan-review` | a plan or spec, the ADRs and design documents, the current tree | findings, a size forecast per task, a revised task and branch table | edits the plan, dispatches a task |
| `/oneledger-code-review` | the branch diff against `develop`, the tree around it | findings triaged blocking / non-blocking, with file and line | edits code, fixes a finding, pushes, opens a PR |
| `/oneledger-code-hygiene` | the whole tree at a milestone boundary | findings grouped mechanical / judgement, and a proposed `chore/` branch | deletes anything, edits a migration, touches a test |

`/verify` is the other kind of gate: it runs the suite and reports green or red. It also changes
nothing.

## The cycle

### 1. Plan the milestone

Superpowers writes the plan into `docs/superpowers/plans/`, execution-map shaped per `CLAUDE.md`.
The Superpowers skill is not modified; the gate reads what it produces.

### 2. Gate — `/oneledger-plan-review`, before any task is dispatched

**Trigger, and it is an obligation, not a suggestion: a plan or spec under `docs/superpowers/` has
just been written or changed, and a task is about to be dispatched from it.** Do not dispatch
first and review later — after dispatch, a split costs an implementation.

It returns findings, a per-task forecast against the diff budget, and a revised task and branch
table. The **planning agent or orchestrator** applies them to the plan. Re-run the gate on the
revised plan. Dispatch begins when no blocking finding is open.

### 3. Implement the task

The orchestrator switches to the task's declared branch; one implementation agent works at a
time; TDD in the plan's order. A deviation from the plan is legitimate where repository reality
requires it, and is recorded in the PR and `progress.md` — never by editing the plan after
dispatch.

### 4. Gate — `/verify`, on a tree the implementer believes is finished

Red is not reviewed. A failing test is a finding that already found itself.

### 5. Gate — `/oneledger-code-review`, before the push

**Trigger: implementation is finished, or a push is about to happen, or a PR is about to be
opened. Any of the three.** There is no path to `origin` that skips this gate — not a small fix,
not a one-line follow-up, not a patch answering a Codex finding.

It returns findings ranked CRITICAL to LOW, with file and line. The **implementing agent** fixes
**every one of them** on the branch, re-runs `/verify`, and re-runs this gate on the new diff — a
fix has its own surface. If a second pass finds new problems inside the first pass's fixes, stop
and raise it with the owner: that is a design problem, not a patch problem.

Everything is fixed here because here it is free. A finding left open does not stay quiet — Codex
raises it once the PR exists, and then the same fix costs a push, a re-review and a round. The
one exception is **scope, not severity**: a finding in code this task does not touch is recorded
in the PR's Notes and raised as its own task, because patching it widens the branch past the task
it carries.

### 6. Push — the hooks are the enforcement

`pre-push` runs pytest, vitest, the SQL invariant tests, the generated-type check and
`scripts/check-diff-size.sh`. The size budget is 400 added production lines and 800 added
production + test lines, refusing at 500 / 1000. A branch past the hard stop is **split**, not
reviewed and not argued with; the `ONELEDGER_SIZE_OVERRIDE` reason exists for a migration that
cannot be divided, and it goes in the PR.

A skill can be skipped by an agent in a hurry; a hook cannot. That is why the budget is a hook and
why `--no-verify` is not an option.

### 7. Open the PR, then triage the Codex findings

One PR per branch, from `.github/PULL_REQUEST_TEMPLATE.md`, carrying this cycle's evidence
honestly — including checks that did not run, and why.

Codex reviews every PR that carries code; a documentation-only one merges on owner review
alone (step 8). Its findings are verified against the tree, then triaged — and here, unlike
step 5, triage is real, because every patch costs another round: blocking findings
are patched on the branch (and step 5 runs again before that patch is pushed); non-blocking ones
are answered and recorded. A Codex round that finds only what `/oneledger-code-review` already
raised means the gate worked; a Codex round finding something the gate missed is a bug in the
gate — add the check to the skill in the same PR that fixes the code.

### 8. Merge, then sweep at the milestone boundary

Merge when no blocking finding is open and Codex has approved; delete the branch. A
documentation-only PR merges on owner review alone.

At the end of a milestone, run `/oneledger-code-hygiene`. Its findings ship as their own `chore/`
branch, sized to the same budget, and go through this cycle from step 5. They are never folded
into a feature PR.

## What a gate never does

- Reports. It does not fix, commit, push or merge unless the owner commands fix mode.
- Never approves. Approval is the owner's, and Codex's.
- Never silences its own finding to reach a clean report. A gate exists to say the inconvenient
  thing early, while it is still cheap.
- Never weakens a test, an assertion or an invariant to make a finding go away. If an invariant is
  genuinely wrong, that is a superseding ADR.
