# Git workflow

This file is the single authority for branches, commits, PRs and releases.
[development-cycle.md](development-cycle.md) owns the delivery gates.

## Branch model

| Branch | Created from | Merges into | Purpose |
|---|---|---|---|
| `main` | — | — | Released code only; receives `release/*` merges. |
| `develop` | `main` | `main` through `release/*` | Standing integration branch and target for task PRs. |
| `<type>/<name>` | `develop` | `develop` | One task-sized feature, fix, chore or documentation change. |
| `release/<version>` | `develop` | `main` | Changelog, release notes, tag and release candidate. |

Allowed task prefixes are `feature/`, `fix/`, `chore/` and `docs/`. Use the active checkout only;
do not create worktrees. The orchestrator creates and switches branches, pushes, opens PRs and
merges. One implementation writer works at a time.

Before dispatch, every task plan records the exact `Workspace`, `Branch`, and `Base and PR target`.
Start each task branch from current `develop`. A phase may use several task branches; its approved
plan lands with the first.

## Task flow

Follow [development-cycle.md](development-cycle.md): implement with TDD and affected tests, commit
locally, review, repair and confirm if needed, then run `scripts/verify.sh` once. The first push is
allowed only for that unchanged verified head.

Open one PR per branch to `develop` using `.github/PULL_REQUEST_TEMPLATE.md`. Record actual evidence
and any approved deviation from the plan. The orchestrator merges when required gates are green,
then deletes the branch. A required check marked `Not run` and anything targeting `main` require an
owner decision.

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

Commit hooks stay fast and staged-file-scoped. Pre-push checks only the exact-head verification
receipt and diff size; it does not rerun application or database suites. Do not bypass either hook.

## Diff budget

Plan each branch toward 500 added production lines and 1,000 production plus test lines. The
warning threshold is 800 / 1,600; `scripts/check-diff-size.sh` refuses a push above 1,200 / 2,400.
Documentation, configuration, lockfiles and generated code do not count.

Split work before implementation when it exceeds the planning target. For a genuinely indivisible
change, such as one migration, the owner may set `ONELEDGER_SIZE_OVERRIDE="reason"`; record the
same reason in PR Notes.

## Releases

On owner request:

1. Create `release/<version>` from `develop`.
2. Add the changelog entry and release notes.
3. Merge the release branch to `main`.
4. Tag the merge commit as `v<version>` and publish the GitHub release from that tag.

Nothing commits directly to `main`.
