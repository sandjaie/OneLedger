# Repository instructions

Read [CLAUDE.md](CLAUDE.md) before working. It routes each rule to its authoritative document.

[docs/git-workflow.md](docs/git-workflow.md) alone owns branches and releases.
[docs/development-cycle.md](docs/development-cycle.md) alone owns delivery gates. In brief:

> plan review once → TDD with affected tests → local commit → comprehensive read-only review →
> consolidated repair → confirmation if repaired → `scripts/verify.sh` once → first push → PR

Use the active checkout; do not create worktrees. One implementation agent writes at a time.
Review agents report findings and do not edit unless the owner explicitly requests fix mode. The
orchestrator owns branch switching, pushes, PRs and merges.

Each planned task declares its exact workspace, branch, base and PR target. Plan toward 500 added
production lines and 1,000 with tests; split before dispatch when needed. Every financial write
operation covers the matrix in `docs/development-cycle.md`.

Run focused tests while implementing. Reuse the branch's one `db-test` server for every database
check. After review and any confirmation, run the no-argument `scripts/verify.sh` on a clean commit.
Pre-push then checks only that exact-head receipt and diff size.

Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) and report only checks
actually run. Maintain live implementation status solely in
[docs/superpowers/progress.md](docs/superpowers/progress.md).
