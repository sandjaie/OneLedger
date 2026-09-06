# Repository instructions

Read [CLAUDE.md](CLAUDE.md) for the shared repository rules and current planning references.
They apply to Codex and all implementation/review agents.

Use the current repository checkout for sequential development. Do not create Git worktrees.
Before dispatch, the task plan or spec must state the exact workspace, branch and base/PR target.
Only one implementation agent works at a time; the orchestrator owns branch switching and PRs.
Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) and report actual
verification results, including any failures or checks not run. See the full workflow in CLAUDE.md.
