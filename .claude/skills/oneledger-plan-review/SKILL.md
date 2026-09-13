---
name: oneledger-plan-review
description: Review a new OneLedger phase specification and plan once before its first branch for contracts, safety, dependencies and branch size. Report blocking findings without editing the plan.
---

# OneLedger plan review

Run once for a new phase specification and plan, before its first branch. Do not run it for later
plan edits, splits, re-baselines or recorded deviations. This skill is report-only unless the owner
explicitly requests fix mode.

## Required task shape

Each branch task states:

- outcome and acceptance criteria;
- dependencies and affected files or modules;
- interfaces, schema/API contracts and invariants;
- security requirements and required test behaviors;
- validation commands;
- exact `Workspace`, `Branch`, and `Base and PR target`.

A plan is an execution map, not an implementation. Code appears only for an exact interface or
schema contract, a non-obvious invariant, or a tiny deterministic change. Live status stays solely
in `docs/superpowers/progress.md`.

## Size

Forecast each branch once against the diff budget in `docs/git-workflow.md`. Related work belongs
in one branch up to that budget; recommend merging thin tasks as readily as splitting large ones.
Documentation, configuration, generated files and lockfiles do not count.

## Contract review

Compare the plan with the current tree and the relevant ADR, design, operations and schema
documents; name any contradiction and the owning document that must change. Check the safety floor
and financial write floor in `docs/development-cycle.md`. Flag scope a single-owner personal
application does not need.

## Output

1. `DISPATCH` or `DISPATCH AFTER FIXES`.
2. A branch table with title, branch, production/test forecast and dependencies.
3. Blocking findings only — contract contradictions, safety-floor gaps, missing dependencies or a
   branch over the hard stop — each naming the section, risk and correction. At most five
   non-blocking suggestions may follow.

The planning agent applies blocking findings. There is no second plan review or confirmation.
