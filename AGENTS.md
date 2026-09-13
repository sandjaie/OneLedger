# Repository instructions

Read [CLAUDE.md](CLAUDE.md) for the shared repository rules and current planning references.
They apply to Codex and all implementation/review agents.
Branching, integration and releases follow [docs/git-workflow.md](docs/git-workflow.md);
no other document defines the branch model. `develop` is the integration branch; a milestone is
planned once and delivered as several task-sized branches, each its own PR, the plan landing
with the first.

**The delivery cycle and its gates are specified in
[docs/development-cycle.md](docs/development-cycle.md). Read it before working, and run its gates
without being asked:** `/oneledger-plan-review` before a task is dispatched from a plan,
focused TDD then `/oneledger-code-review` **before the branch is pushed at all** — the order is
implement with focused checks, comprehensive review, consolidated fix, one final full `/verify`,
narrow confirmation, first push, PR — and `/oneledger-code-hygiene` at a milestone boundary.
Nothing reaches `origin` before the confirmation is clean. A documentation-only PR — a plan, a
spec, an ADR, `progress.md` — merges on owner review alone.

**Those three skills produce findings another agent can patch; they do not modify code** unless
the owner explicitly commands fix mode. The report goes back to the agent that produced the work,
which applies its complete finding set as one consolidated repair. Confirmation reviews only that
repair range; if it reveals a new architectural or security boundary, stop for a consolidated
design/threat review instead of continuing a patch loop. Plan tasks toward 500 added production
lines / 1,000 with tests; the formal warning remains 800 / 1,600 and the push hook refuses at
1,200 / 2,400, so a task too large is split before it is built, not after.

Use the active repository checkout for sequential development. Resolve its root with
`git rev-parse --show-toplevel`; do not substitute a path from another machine or create Git worktrees.
Before implementation starts, the plan must state the exact workspace, branch and base/PR target.
One task branch and PR normally use one implementation session. Only one implementation agent
works at a time; read-only specialist reviewers may run in parallel, and the orchestrator owns
branch switching and PRs. Use the branch's same `db-test` server for every focused and final
database check through `scripts/verify.sh`; the final gate tears it down once.
Use [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) and report actual
verification results, including any failures or checks not run. See the full workflow in CLAUDE.md.

Maintain implementation status only in [docs/superpowers/progress.md](docs/superpowers/progress.md).
Keep index/README/roadmap/plan pages free of mirrored progress summaries. Dated reports are
historical evidence; private Superpowers notes are recovery aids, not additional published trackers.
