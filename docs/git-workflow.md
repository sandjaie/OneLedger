# Git workflow

The single reference for branching, integration and release. `CLAUDE.md`, `AGENTS.md`, plans
and specs point here instead of restating the model. When another document disagrees with this
file, this file wins and the other document gets fixed — do not fork the model into a second
place.

## Branch model

| Branch | Created from | Merges into | Purpose |
|---|---|---|---|
| `main` | — | — | Released code only. Receives merges from `release/*`, nothing else. |
| `develop` | `main` | `main`, via `release/*` | The standing integration branch. Completed phase branches merge here. |
| `feature/phase-<n>-<description>` | `develop` | `develop` | Phase integration branch, created when a phase plan starts execution. Every task PR of that phase targets it. |
| `<type>/task-<n>-<description>` | the phase branch | the phase branch | One task from the active phase plan, delivered as one PR (or a declared split). |
| `release/<version>` | `develop` | `main` | Cut for a release; carries the changelog and release notes; produces the tag. |

`<type>` is one of `feature/`, `fix/`, `chore/`, `docs/` — the complete set of branch-name
prefixes. `<n>` in a task branch is the task number from the active phase plan, so the branch
names the work it carries: `fix/task-4-auth-database-errors`,
`feature/task-9-account-inventory-api`.

## Flow

1. **Start a phase.** Branch `feature/phase-<n>-<description>` from `develop`. The phase plan
   and [docs/superpowers/progress.md](superpowers/progress.md) name it; that named branch is
   the `Base and PR target` for every task in the phase.
2. **Deliver a task.** Branch `<type>/task-<n>-<description>` from the phase branch. Open the
   PR against the phase branch from `.github/PULL_REQUEST_TEMPLATE.md`; it merges on Codex
   approval within the 500-line implementation-and-test cap. The PR-process rules themselves
   live in `CLAUDE.md`.
3. **Complete the phase.** When the phase's exit gates are verified and recorded in the
   progress file, open a PR from the phase branch to `develop`. This, like any integration
   toward `main`, happens only on a separate owner request.
4. **Release.** Branch `release/<version>` from `develop`. On that branch: write the changelog
   entry and the release notes for what shipped since the last release. On owner request, merge
   to `main`, tag the merge commit (`git tag -a v<version>`) and publish the GitHub release
   from that tag with those notes.

## Rules

- Nothing commits straight to `main` or `develop`; both move only by reviewed PR.
- Work outside any active phase branches from `develop` and targets `develop`, using the same
  four prefixes; it reaches `main` with the next release.
- Every task plan or spec declares its exact `Workspace`, `Branch` and `Base and PR target`
  before dispatch, using values consistent with this file.
- Delete branches after merge; the PR record is the history.
