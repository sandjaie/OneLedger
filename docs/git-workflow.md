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

Every delivery follows one cycle: **plan and implement → review → patch the findings → Codex
thumbs-up → merge.**

1. **Plan the milestone, then implement it task by task.** The milestone's dated execution-map
   plan (per the CLAUDE.md "plans are execution maps" rule) goes in `docs/superpowers/plans/`
   and lands with the milestone's first branch. Each task — or a small coherent group of
   tasks — gets its own branch `<type>/<name>` from `develop`, implemented TDD in the plan's
   order. The plan declares `Workspace`, `Branch` and `Base and PR target` per task before
   implementation starts.
2. **Open one PR to `develop` per branch** from `.github/PULL_REQUEST_TEMPLATE.md`, with honest
   verification evidence. Keep each PR reviewable at one sitting; a milestone is a sequence of
   such PRs, not one large one.
3. **Review and patch.** Codex reviews on GitHub; findings are verified against the tree,
   patched on the same branch, and pushed for re-review.
4. **Merge on Codex's thumbs-up.** The PR merges only after Codex approves; then the branch is
   deleted and the next milestone begins.
5. **Release.** Branch `release/<version>` from `develop`. On that branch: write the changelog
   entry and the release notes for what shipped since the last release. On owner request, merge
   to `main`, tag the merge commit (`git tag -a v<version>`) and publish the GitHub release
   from that tag with those notes.

## Rules

- Nothing commits straight to `main`; it moves only through `release/*` on owner request.
  `develop` moves by reviewed PR (owner-directed integrations excepted).
- One milestone (or one coherent fix) per branch and PR. Commits inside the PR stay small and
  task-shaped; there is no per-PR line cap — the milestone is the review unit.
- Every plan declares its exact `Workspace`, `Branch` and `Base and PR target` before
  implementation starts, using values consistent with this file.
- Delete branches after merge; the PR record is the history.
