---
name: verify
description: Run OneLedger's tiered verification runner — focused checks during TDD, a database-only
final gate when the reviewed diff cannot affect application surfaces, or the full exact-head gate
before the confirmation review and first push. Use before opening a PR, as the stop condition for
/goal and /loop, and whenever asked whether the tree is green.
---

# verify

Use `scripts/verify.sh`; do not reconstruct this orchestration across ad hoc shells. The runner
prints the mode and starting Git head, prints each stage's result, stops on the first failure, and
refuses a result if `HEAD` changed while it ran.

## Focused mode — implementation feedback

Wrap the smallest command that proves the current RED/GREEN step:

```bash
scripts/verify.sh focused -- <affected-test-command>
scripts/verify.sh focused --database -- <affected-database-test-command>
```

The database form starts this checkout's one `db-test` server only if it is absent. It deliberately
leaves the server running so every later focused test and the final gate reuse it. Focused green is
required before code review, but it is not the final branch verdict.

## Database mode — narrow final gate

Use this only when the plan and comprehensive review establish that a database-only diff cannot
affect API or web behavior:

```bash
scripts/verify.sh database
```

It migrates a fresh database from empty, runs every SQL invariant and upgrade rehearsal through
`scripts/hooks/db-tests.sh`, checks the exact head, and tears down the branch's test server once.

## Full mode — normal final gate

After the comprehensive review and consolidated repair, run once:

```bash
scripts/verify.sh full
```

The runner executes, in order:

1. `python3 scripts/check-docs.py`.
2. `pre-commit run --all-files` and
   `pre-commit run markdownlint --all-files --hook-stage pre-push`; mutating fixers are skipped in
   this gate and their non-mutating checks run in the component stages.
3. `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` and
   `uv run pytest -q` under `api/`.
4. `scripts/hooks/db-tests.sh` on the same test server.
5. generated API type checks, both TypeScript compilers, `npx vitest run`, the production build,
   and the server-secret bundle scan under `web/`.

The final mode reuses a running server or, if no database check was needed during implementation,
starts it with `eval "$(scripts/test-db.sh up)"`. A fresh shell can re-export that same server with
`eval "$(scripts/test-db.sh env)"`; it must never start a second verification server. The runner
tears it down with `scripts/test-db.sh down` exactly once, on success or failure.

Do not run the full mode before review and then again after fixes. Do not repeat it on an unchanged
head. If a real repair changes the head after full verification, its evidence is stale and the
runner must run once on the new head.

## What green means

For a normal branch, `scripts/verify.sh full` passed on the exact head awaiting confirmation and
each application suite collected a non-zero number of tests. A suite that collects nothing and
exits 0 is a failure, not a pass. For a database-only branch, the reviewed scope and a passing
`database` mode must both be recorded.

## What is never allowed to make it green

Editing a test, loosening an assertion, deleting a case, or weakening an invariant. If a check is
genuinely wrong, that is a conversation and possibly a superseding ADR. Report red and stop.
