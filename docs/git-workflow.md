# Git workflow

This file is the single authority for branches, commits, PRs and releases.
[development-cycle.md](development-cycle.md) owns the delivery gates.

## Branch model

| Branch | Created from | Merges into | Purpose |
|---|---|---|---|
| `main` | — | — | Released code only; receives `release/*` merges. |
| `develop` | `main` | `main` through `release/*` | Standing integration branch and target for task PRs. |
| `<type>/<name>` | `develop` | `develop` | One coherent outcome — a feature, fix, chore or documentation change — grouping related plan tasks up to the diff budget. |
| `release/<version>` | `develop` | `main` | Changelog, release notes, tag and release candidate. |

Allowed task prefixes are `feature/`, `fix/`, `chore/` and `docs/`. Use the active checkout only;
do not create worktrees. The orchestrator creates and switches branches, pushes, opens PRs and
merges. One implementation writer works at a time.

Before dispatch, every task plan records the exact `Workspace`, `Branch`, and `Base and PR target`.
Start each task branch from current `develop`. A phase may use several task branches; its approved
plan lands with the first. Record the current planned task's implementation status, splits and
re-baselines in that task's PR, not in a later feature PR. Do not open a documentation-only PR for
routine status updates.

## Task flow

Follow [development-cycle.md](development-cycle.md): implement with tests for major behavior, commit
locally, review once, repair once, then run the scope-appropriate verifier once. The first push is
allowed only for that unchanged verified head.

Open one PR per branch to `develop` using `.github/PULL_REQUEST_TEMPLATE.md`. Record actual evidence
and any approved deviation from the plan. Each planned task PR must update `docs/superpowers/progress.md`:
mark its implementation complete, name the exact next task (or state that none is planned), and
remove any stale "in progress" claim for the completed task. Use wording that is true while the PR
is open and after it merges; do not claim a merge or owner acceptance in advance. A missing or
misleading tracker update blocks the merge. Do not make a post-merge status-only commit merely to
add the PR number; that evidence can be added with the next task PR. The orchestrator merges when
required gates are green, then deletes the branch. A required check marked `Not run` and anything
targeting `main` require an owner decision.

## Pull request titles

Use a phase/slice prefix when the work belongs to one:

```text
[P1.3] feat(api): a remittance and its arrival link are recorded together
```

Repeat the slice prefix when one planned slice needs multiple PRs; the subject distinguishes them.
Use `[P1]` for phase-wide work and no phase prefix for repository-wide workflow or tooling work.
Do not rename historical merged PRs.

## Commits

Use `type(scope): a lowercase sentence stating what is now true`.

- Types: `feat`, `fix`, `docs`, `ci`, `chore`, `refactor`, `test`.
- Common scopes: `db`, `api`, `web`, `ci`, `docs`, `plan`.
- Keep the subject declarative. Use the body for the problem and why the change is correct.

Commit hooks stay fast and staged-file-scoped. There is no pre-push hook; run the final verifier
before pushing and do not bypass the commit hook.

## Diff budget

Plan each branch within 1,500 added production lines and 3,000 production plus test lines, grouping
related plan tasks until a branch approaches that budget. `scripts/check-diff-size.sh` warns above
the budget and fails verification above 2,000 / 4,000. Documentation, configuration, lockfiles and
generated code do not count.

Above the hard stop, move the unfinished remainder to the next branch before pushing. For a
genuinely indivisible change, such as one migration, the owner may set
`ONELEDGER_SIZE_OVERRIDE="reason"`; record the same reason in PR Notes.

## Releases

On owner request:

1. Create `release/<version>` from `develop`.
2. Add the changelog entry and release notes.
3. Merge the release branch to `main`.
4. Tag the merge commit as `v<version>` and publish the GitHub release from that tag.

Nothing commits directly to `main`.
