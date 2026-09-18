# Repository instructions

Read [CLAUDE.md](CLAUDE.md) before working. It routes each rule to its authoritative document.

[docs/git-workflow.md](docs/git-workflow.md) alone owns branches and releases.
[docs/development-cycle.md](docs/development-cycle.md) alone owns delivery gates. In brief:

> phase plan reviewed once → implement with major tests → local commit → one read-only review →
> one repair → scope-appropriate verifier once → first push → PR

Use the active checkout; do not create worktrees. One implementation agent writes at a time.
Review agents report findings and do not edit unless the owner explicitly requests fix mode. The
orchestrator owns branch switching, pushes, PRs and merges.

Each planned task declares its exact workspace, branch, base and PR target and fits the diff budget
in `docs/git-workflow.md`. Every financial write operation meets the floor in
`docs/development-cycle.md`.

Run focused tests while implementing. Reuse the branch's one `db-test` server for every database
check. After review and repair, run `scripts/verify.sh` on a clean commit, or
`scripts/verify-web.sh` for a web-only branch. There is no pre-push hook.

Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) and report only checks
actually run. Maintain live implementation status solely in
[docs/superpowers/progress.md](docs/superpowers/progress.md).
