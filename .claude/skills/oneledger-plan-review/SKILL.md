---
name: oneledger-plan-review
description: Review a new or materially changed OneLedger plan before dispatch for task shape, contracts, security, dependencies and branch size. Report findings and splits without editing the plan.
---

# OneLedger plan review

Run once after a phase plan or specification is written or materially changed and before its first
task is dispatched. This skill is report-only unless the owner explicitly requests fix mode.

## Required task shape

Each task must state:

- outcome and acceptance criteria;
- dependencies and affected files or modules;
- interfaces, schema/API contracts and invariants;
- security requirements and required test behaviors;
- validation commands;
- exact `Workspace`, `Branch`, and `Base and PR target`.

A plan is an execution map, not an implementation. Allow code only for an exact interface/schema
contract, a non-obvious invariant, or a tiny deterministic change. Move durable rationale and table
semantics to a spec, design document or ADR. Keep live status solely in
`docs/superpowers/progress.md`.

## Size and split

Forecast each task against the existing modules and tests it changes:

| Added lines | Dispatch target | Warning | Hard stop |
|---|---:|---:|---:|
| Production | 500 | 800 | 1,200 |
| Production plus tests | 1,000 | 1,600 | 2,400 |

Documentation, configuration, generated files and lockfiles do not count. Split a forecast above
the dispatch target before implementation unless the change is genuinely indivisible. Give every
split its own branch from `develop`, dependency order and acceptance boundary. An owner-approved
hard-limit override needs a reason that will also appear in PR Notes.

## Contract review

Compare the plan with the current tree and the relevant ADR, design, operations and schema
documents. Name any contradiction and which owning document must change; do not leave the
implementer to resolve it implicitly.

For every financial write operation, require explicit test behavior for:

- owner, member and foreign-household access;
- direct-DML refusal, RLS and immutability;
- stale state and idempotent retry;
- precision before normalization, hashing or deduplication;
- correction leaves and preserved history;
- lock order and concurrent conflict paths;
- atomic rollback after late failure.

Confirm money uses Postgres `NUMERIC`, Python `Decimal` and wire strings; financial routes enforce
owner authorization and household isolation; identifiers remain privacy-safe; migrations are
forward-only; unsupported facts are never fabricated.

## Output

Return:

1. `DISPATCH AS WRITTEN` or `CHANGES REQUIRED`.
2. A task table with title, branch, production/test forecast and dependencies.
3. Consolidated findings naming the affected section, risk and required correction.

The planning agent applies findings. Re-run this gate only if that repair materially changes the
plan or specification.
