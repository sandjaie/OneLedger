# Repository instructions

Read [CLAUDE.md](CLAUDE.md) for the shared repository rules and current planning references.
They apply to Codex and all implementation/review agents.
Branching, integration and releases follow [docs/git-workflow.md](docs/git-workflow.md);
no other document defines the branch model. `develop` is the integration branch; a milestone is
planned once and delivered as several task-sized branches, each its own PR, the plan landing
with the first. The delivery cycle per PR: plan and implement → Codex review → patch the
findings → Codex thumbs-up → merge.

Use the active repository checkout for sequential development. Resolve its root with
`git rev-parse --show-toplevel`; do not substitute a path from another machine or create Git worktrees.
Before implementation starts, the plan must state the exact workspace, branch and base/PR target.
Only one implementation agent works at a time; the orchestrator owns branch switching and PRs.
Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) and report actual
verification results, including any failures or checks not run. See the full workflow in CLAUDE.md.

Maintain implementation status only in [docs/superpowers/progress.md](docs/superpowers/progress.md).
Keep index/README/roadmap/plan pages free of mirrored progress summaries. Dated reports are
historical evidence; private Superpowers notes are recovery aids, not additional published trackers.
