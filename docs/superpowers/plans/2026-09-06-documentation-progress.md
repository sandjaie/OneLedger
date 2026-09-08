# Documentation progress ownership

**Workspace:** resolve the active checkout with `git rev-parse --show-toplevel` before dispatch.
For this local task it resolves to `/Users/sandjaieravi/dev/OneLedger`; do not use that path on
another host.
**Branch:** `docs/centralize-implementation-progress`.
**Base and PR target:** `feature/phase-0-foundations`.

## Requirements and steps

1. Maintain implementation status only in `docs/superpowers/progress.md`. Preserve completed
   deliveries, remaining verification gates and references when consolidating existing summaries.
2. Remove live execution summaries, PR lists and milestone status from the index, README,
   roadmap, agent instructions and planning documents. Keep product scope, sequence and acceptance
   criteria in those documents, with neutral links to the canonical tracker where useful.
3. Keep plans as execution instructions and dated verification reports as historical evidence.
   Superpowers may retain private recovery notes; it must not publish parallel status summaries.
4. Record this documentation ownership rule in the shared agent instructions and PR template.
5. Check links, HTML, whitespace and required hooks. Create a template-based PR, address Cloud
   feedback on the same branch, and merge after clean current-head review.

No application behavior, accepted ADR body or historical verification result changes. Current
completion state and follow-up work belong in the canonical progress file, not this plan.
