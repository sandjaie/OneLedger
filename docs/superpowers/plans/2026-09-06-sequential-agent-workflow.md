# Sequential agent workflow update

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` — the resolved local checkout for this task,
not a global path. On another host, resolve the active root with `git rev-parse --show-toplevel`
and record it here before dispatch. Do not create a Git worktree.
**Branch:** `docs/sequential-agent-workflow`.
**Base and PR target:** `feature/phase-0-foundations`.
**Scope:** repository agent instructions, planning/review workflow and the reusable PR template.

## Requirements

Run implementation agents sequentially in the current repository. Each task plan or spec must
declare its exact branch, base/PR target and workspace before it is passed to an agent. The
workspace is resolved from the active repository, never copied from another host's instructions. The
orchestrator controls branch switches and PR creation, and waits for implementation and review
work to finish before switching branches. Preserve unrelated local changes.

Use `.github/PULL_REQUEST_TEMPLATE.md` for every PR. Explain what changed and was implemented,
record actual tests and other checks with their results, and identify skipped checks or remaining
limits. Preserve the existing local/Cloud review and same-implementer fix workflow.

## Steps

- Update `CLAUDE.md` and a root `AGENTS.md` entry point with the owner's workflow preference.
- Update the master plan and Phase 0 workflow references, including the current completion plan.
- Replace the finance-specific PR template with reusable sections and evidence instructions.
- Run documentation and whitespace checks; no application behavior changes.

**Delivery gate:** required commit/push hooks, a PR using the revised template, and current-head
Codex review before integration merge. GitHub records the final review and merge status.

The phase exit requirements remain unchanged. This workflow update does not
authorize integration-to-main or removal of existing worktrees.
