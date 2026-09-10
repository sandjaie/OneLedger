# Development cycle

The single ordered reference for how work moves from a plan to a merge, and for the gate that
stands at each step. [docs/git-workflow.md](git-workflow.md) owns branches, integration and
releases; this file owns **when a gate runs, who runs it, and what it produces**. When the two
disagree about a branch, git-workflow.md wins; when they disagree about a gate, this file wins.

The cycle is deliberately light — owner decision, 8 September 2026: **plan → implement → local
review → merge.** External (Codex) review is disabled. What remains is the part that catches a
defect while it is still cheap: the suite, one local review of the diff, and hooks that cannot
be skipped.

## The review skills report

`/oneledger-plan-review`, `/oneledger-code-review` and `/oneledger-code-hygiene` **produce
findings another agent can patch. They do not modify code** unless the owner explicitly commands
fix mode. A reviewer that fixes what it finds hides the finding from whoever needed to learn it,
and reports on a tree that no longer matches the one it reviewed.

| Skill | Reads | Produces | Never |
|---|---|---|---|
| `/oneledger-plan-review` | a plan or spec, the ADRs and design documents, the current tree | findings, a size forecast per slice, a revised slice and branch table | edits the plan, dispatches a task |
| `/oneledger-code-review` | the branch diff against `develop`, the tree around it | findings triaged blocking / non-blocking, with file and line | edits code, fixes a finding, pushes, opens a PR |
| `/oneledger-code-hygiene` | the whole tree at a phase boundary | findings grouped mechanical / judgement, and a proposed `chore/` branch | deletes anything, edits a migration, touches a test |

`/verify` is the other kind of gate: it runs the suite and reports green or red. It also changes
nothing.

## The cycle

### 1. Plan the phase

The phase's dated execution-map plan goes in `docs/superpowers/plans/`, shaped per `CLAUDE.md`.
The master sequence is the current phase delivery plan; each later phase gets its own dated plan
written immediately before its implementation, against the tree at that time.

### 2. Gate — `/oneledger-plan-review`, once per new plan

**Trigger: a phase plan or spec has just been written, or materially changed, and nothing has
been dispatched from it yet.** One pass: apply the findings, then dispatch. It does not re-run
per slice, and it does not run at all for work an existing reviewed plan already covers.

### 3. Implement the slice

One implementation agent at a time, on the slice's declared branch, TDD in the plan's order. A
deviation from the plan is legitimate where repository reality requires it, and is recorded in
the PR and `progress.md` — never by editing the plan after dispatch.

**Nothing is weakened to reach green.** Not a loosened assertion, not a deleted case, not a
skipped test, not a softened invariant. A red suite is a finding that already found itself, and
an implementer who edits the test has removed the finding rather than the defect. If an
invariant is genuinely wrong, that is a superseding ADR. The same rule binds the gates, and for
the same reason — "What a gate never does", below.

### 4. Gate — `/verify`, on a tree the implementer believes is finished

Red is not reviewed. A failing test is a finding that already found itself.

### 5. Gate — `/oneledger-code-review`, one round before the first push

**Trigger: implementation is finished and `/verify` is green.** The order is:

> implement → `/verify` → review → fix the blocking findings → `/verify` → **first push** → PR

The reviewer reads the diff against this repository's own rules: identifier leakage, money
handling, the authorization boundary, reporting views, migrations, invariants, TDD evidence.
**Blocking findings** — correctness, security, an invariant or contract breach, missing test
evidence — are fixed on the branch before the push. **Non-blocking findings** — hardening,
style, scope-widening suggestions — are recorded in the PR's Notes and become their own later
task if they are worth one. One round is the default; re-review only what the fixes themselves
changed. If the fixes keep producing new findings, stop and raise it with the owner — that is a
design problem, not a patch problem.

Nothing reaches `origin` unreviewed. A documentation-only branch carries no code for this gate
to read: `scripts/check-docs.py` and the commit hooks are its gate, and the PR says so.

### 6. Push — the hooks are the enforcement

`pre-push` runs pytest, vitest, the SQL invariant tests, the generated-type check and
`scripts/check-diff-size.sh` (soft 800 / 1,600, hard stop 1,200 / 2,400; the
`ONELEDGER_SIZE_OVERRIDE` reason exists for what genuinely cannot be split, and it goes in the
PR). A skill can be skipped by an agent in a hurry; a hook cannot. `--no-verify` is not an
option.

### 7. Open the PR, then merge

One PR per branch, from `.github/PULL_REQUEST_TEMPLATE.md`, carrying this cycle's evidence
honestly — including checks that did not run, and why. **The orchestrator merges** once the
local gates are green and the template is honest, then deletes the branch. The tracker entry in
`docs/superpowers/progress.md` rides on the task branch, in the head that gets merged, written
to read correctly after the merge.

Two cases stay the owner's, and an agent that reaches one says so and stops:

- **A PR whose Verification table records a required check as `Not run`.** Merging it would
  cover a gap nothing looked at.
- **Anything targeting `main`.** That moves only through `release/*` on a separate owner
  request ([docs/git-workflow.md](git-workflow.md)).

### 8. Sweep at the phase boundary

At the end of a phase, run `/oneledger-code-hygiene`. Its findings ship as their own `chore/`
branch, sized to the same budget, through this cycle from step 3. They are never folded into a
feature PR.

## What a gate never does

- Reports. It does not fix, commit, push or merge unless the owner commands fix mode.
- Never silences its own finding to reach a clean report. A gate exists to say the inconvenient
  thing early, while it is still cheap.
- Never weakens a test, an assertion or an invariant to make a finding go away. If an invariant
  is genuinely wrong, that is a superseding ADR.
