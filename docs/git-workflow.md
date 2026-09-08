# Git workflow

The single reference for branching, integration and release. `CLAUDE.md`, `AGENTS.md`, plans
and specs point here instead of restating the model. When another document disagrees with this
file, this file wins and the other document gets fixed — do not fork the model into a second
place.

## Branch model

| Branch | Created from | Merges into | Purpose |
|---|---|---|---|
| `main` | — | — | Released code only. Receives merges from `release/*`, nothing else. |
| `develop` | `main` | `main`, via `release/*` | The standing integration branch. Every feature PR targets it. |
| `<type>/<name>` | `develop` | `develop` | One task-sized slice of a milestone (or a standalone fix), delivered as one PR. A milestone spans several such branches; its plan lands with the first. |
| `release/<version>` | `develop` | `main` | Cut for a release; carries the changelog and release notes; produces the tag. |

`<type>` is one of `feature/`, `fix/`, `chore/`, `docs/` — the complete set of branch-name
prefixes. `<name>` names the work the branch carries: `feature/m1b-manual-transactions`,
`fix/auth-database-errors`.

## Flow — the delivery cycle

Every delivery follows one cycle: **plan → review the plan → implement → review locally → PR →
patch the blocking findings → merge.** The review that costs least is the earliest one; a finding
that reaches GitHub has already cost a round trip.

1. **Plan the milestone.** The dated execution-map plan (per the CLAUDE.md "plans are execution
   maps" rule) goes in `docs/superpowers/plans/` and lands with the milestone's first branch.
2. **Review the plan before dispatching any of it** with `/oneledger-plan-review`: every task
   forecast against the diff budget below and split where it does not fit, contracts and
   invariants stated, no embedded implementations, no contradiction with the ADRs and design
   documents. Splitting a task on paper costs a paragraph; discovering the same thing after
   implementation costs a day of review rounds.
3. **Implement task by task.** Each task — or a small coherent group of tasks — gets its own
   branch `<type>/<name>` from `develop`, implemented TDD in the plan's order. The plan declares
   `Workspace`, `Branch` and `Base and PR target` per task before implementation starts.
4. **Review locally, before the push.** `/verify` for the gate, then `/oneledger-code-review` for
   the reading pass. Nothing is pushed with a blocking finding open. The push hooks enforce the
   diff budget; a branch past the hard stop is split, not reviewed.
5. **Open one PR to `develop` per branch** from `.github/PULL_REQUEST_TEMPLATE.md`, with honest
   verification evidence. A milestone is a sequence of such PRs, not one large one.
6. **Review and patch, triaged.** Codex reviews on GitHub. Findings are verified against the tree
   first, then split: **blocking** findings — a correctness bug, a security or never-store
   violation, an invariant breach, a contract or schema mismatch, missing test evidence — are
   patched on the same branch and pushed for re-review. **Non-blocking** findings — hardening
   beyond the task, style, naming, a suggested extra case, a refactor of code the PR did not
   touch — are answered in the thread and recorded in the PR's Notes, and become their own issue
   if they matter. They are not patched here: each one widens the diff and buys another round.
7. **Merge when no blocking finding is open** and Codex has given its thumbs-up; then the branch
   is deleted and the next task begins. A **documentation-only PR** — a plan, a spec, an ADR,
   `progress.md` — merges on owner review alone; prose review has no convergence criterion and
   the milestone plan is not worth a day of it.
8. **At the milestone boundary, sweep.** `/oneledger-code-hygiene` finds what accumulated between
   tasks — dead code, duplication, migration cruft, drifted documentation. Its findings ship as
   their own `chore/` branch, never folded into a feature PR.
9. **Release.** Branch `release/<version>` from `develop`. On that branch: write the changelog
   entry and the release notes for what shipped since the last release. On owner request, merge
   to `main`, tag the merge commit (`git tag -a v<version>`) and publish the GitHub release
   from that tag with those notes.

## Rules

- Nothing commits straight to `main`; it moves only through `release/*` on owner request.
  `develop` moves by reviewed PR (owner-directed integrations excepted).
- One task-sized slice (or one coherent fix) per branch and PR, inside the **diff budget**:
  **400 lines of added production code, 800 added production + test lines**, with a hard stop at
  500 / 1000 that refuses the push. Documentation, configuration and generated files do not count.
  `scripts/check-diff-size.sh` measures the branch against `develop` and runs on push; it prints
  the largest files so the split is obvious. Past the hard stop, `ONELEDGER_SIZE_OVERRIDE="reason"`
  allows the push and the same reason goes in the PR's Notes — an indivisible migration is the
  case it exists for.
- The budget is not bureaucracy. A branch is re-read in full on every review round, so review
  effort grows faster than the diff: M2 task 3 reached +3,380 lines and took eight Codex rounds
  and fifteen fix commits. Four branches of 800 would have converged in a fraction of that.
- Every plan declares its exact `Workspace`, `Branch` and `Base and PR target` before
  implementation starts, using values consistent with this file.
- Delete branches after merge; the PR record is the history.
