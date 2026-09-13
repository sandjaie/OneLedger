---
name: verify
description: Run the OneLedger gate — docs and real-data checks, secrets, api lint/types/tests,
migrations from empty plus the SQL invariant tests, web types/tests/bundle scan. Use before opening
a PR, as the stop condition for /goal and /loop, and whenever asked whether the tree is green.
---

# verify

Run each section in order and stop at the first failure. Report every section as `ok` or `FAIL`
with **the number of checks that actually ran**, then one verdict line.

Start the delivery verification stage's one isolated test server before section 1:

```bash
eval "$(scripts/test-db.sh up)"
```

Keep that `db-test` server running for every database-dependent check below. Tool calls commonly
use fresh shells; in each later fresh shell, re-export the already-running server without starting
another one:

```bash
eval "$(scripts/test-db.sh env)"
```

On success, or immediately after the first failure, run `scripts/test-db.sh down` exactly once.
Never launch a separate scratch Compose stack for this gate.

1. `python3 scripts/check-docs.py` — links, ADR link labels, HTML, no statement tracked by git
2. Commit-stage hooks plus the Markdown check intentionally reserved for pre-push:

   ```bash
   pre-commit run --all-files
   pre-commit run markdownlint --all-files --hook-stage pre-push
   ```

   These cover secrets, formatting and every commit-stage hook without putting a network-resolved
   Markdown runner in the ordinary commit path.
3. Re-export the test environment if this command runs in a fresh shell, then run the API gate.
   The exported `ONELEDGER_TEST_DSN` and `POSTGRES_*` values make every live path execute against
   the same isolated server:

   ```bash
   eval "$(scripts/test-db.sh env)"
   (cd api && uv run ruff check . && uv run mypy . && uv run pytest -q)
   ```

4. Re-export the same running test server, then run the database invariant suite directly:

   ```bash
   eval "$(scripts/test-db.sh env)"
   scripts/hooks/db-tests.sh
   ```

   The hook creates its own throwaway database on that server, migrates it from empty, runs every
   SQL invariant suite and real upgrade rehearsal, then drops that database. Do not repeat this
   section on another server.

5. The web gate, then a scan of the bundle it produced:

   ```bash
   cd web
   npm run api:types:check && npm run typecheck && npm run typecheck:compat
   npx vitest run && npm run build
   grep -rE 'SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://' .next/static/
   ```

## What green means

Every section passed **and collected a non-zero number of tests**. A suite that collects nothing
and exits 0 is a failure, not a pass — it is the state a loop will otherwise sit in for hours
believing it is fine.

A section whose directory does not exist yet is **skipped**, and must be reported as skipped.
Never report it as passing. `api/` arrives in Phase 0 PR 4, `web/` in PR 6.

## What is never allowed to make it green

Editing a test, loosening an assertion, deleting a case, or weakening an invariant. If a check is
genuinely wrong, that is a conversation and possibly a superseding ADR. Report red and stop.
