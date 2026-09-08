# Git workflow

The single reference for branching, integration and release. `CLAUDE.md`, `AGENTS.md`, plans
and specs point here instead of restating the model. When another document disagrees with this
file about a branch, a base, a merge or a release, this file wins and the other document gets
fixed — do not fork the model into a second place.

The one division: **the review gates belong to
[docs/development-cycle.md](development-cycle.md)** — when each runs, who runs it, what it
produces, and that it only ever reports. That file wins on a gate, this one on a branch.

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

**[docs/development-cycle.md](development-cycle.md) is where that cycle is specified** — each
gate, when an agent must run it unprompted, and what it produces. It is not restated here. This
file says only where the branches go:

1. The milestone's dated execution-map plan goes in `docs/superpowers/plans/` and lands with the
   milestone's first branch. It is reviewed — `/oneledger-plan-review` — before any task is
   dispatched, because that is where a task too large for one branch gets split.
2. Each task — or a small coherent group of tasks — gets its own branch `<type>/<name>` from
   `develop`, implemented TDD in the plan's order. The plan declares `Workspace`, `Branch` and
   `Base and PR target` per task before implementation starts.
3. Before the push: `/verify`, then `/oneledger-code-review`. Nothing reaches `origin` with a
   blocking finding open, and the push hooks enforce the diff budget below.
4. **One PR to `develop` per branch** from `.github/PULL_REQUEST_TEMPLATE.md`, with honest
   verification evidence. A milestone is a sequence of such PRs, not one large one.
5. Codex reviews; findings are verified against the tree and triaged. **Blocking** — a correctness
   bug, a security or never-store violation, an invariant breach, a contract or schema mismatch,
   missing test evidence — is patched on the same branch and pushed for re-review. **Non-blocking**
   — hardening beyond the task, style, naming, a suggested extra case, a refactor of code the PR
   did not touch — is answered in the thread and recorded in the PR's Notes. It is not patched
   here: each one widens the diff and buys another round.
6. **Merge when no blocking finding is open** and Codex has given its thumbs-up; then the branch
   is deleted and the next task begins. A **documentation-only PR** — a plan, a spec, an ADR,
   `progress.md` — merges on owner review alone; prose review has no convergence criterion and
   the milestone plan is not worth a day of it.
7. At the milestone boundary, `/oneledger-code-hygiene` sweeps what accumulated between tasks. Its
   findings ship as their own `chore/` branch, never folded into a feature PR.
8. **Release.** Branch `release/<version>` from `develop`. On that branch: write the changelog
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
