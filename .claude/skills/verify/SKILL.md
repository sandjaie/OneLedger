---
name: verify
description: Run the OneLedger gate — docs and real-data checks, secrets, api lint/types/tests, migrations from empty plus the SQL invariant tests, web types/tests/bundle scan. Use before opening a PR, as the stop condition for /goal and /loop, and whenever asked whether the tree is green.
---

# verify

Run each section in order and stop at the first failure. Report every section as `ok` or `FAIL`
with **the number of checks that actually ran**, then one verdict line.

1. `python3 scripts/check-docs.py` — links, ADR link labels, HTML, no statement tracked by git
2. `pre-commit run --all-files` — secrets, formatting, and every commit-stage hook
3. `cd api && uv run ruff check . && uv run mypy . && uv run pytest -q`
4. Migrations from empty, against a **scratch** database and never the working one:
   `docker compose down -v && docker compose up -d db`, then `docker compose run --rm flyway
   migrate`, then every `db/seeds/*.sql`, then every `db/tests/*.sql` with `-v ON_ERROR_STOP=1`.
   (`flyway clean` is disabled by default since v10 and is not the reset to reach for — dropping
   the volume is both genuinely empty and incapable of touching a real database.)
5. `cd web && npx tsc --noEmit && npx vitest run && npm run build`, then grep `.next/static/` for
   `SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://`

## What green means

Every section passed **and collected a non-zero number of tests**. A suite that collects nothing
and exits 0 is a failure, not a pass — it is the state a loop will otherwise sit in for hours
believing it is fine.

A section whose directory does not exist yet is **skipped**, and must be reported as skipped.
Never report it as passing. `api/` arrives in Phase 0 PR 4, `web/` in PR 6.

## What is never allowed to make it green

Editing a test, loosening an assertion, deleting a case, or weakening an invariant. If a check is
genuinely wrong, that is a conversation and possibly a superseding ADR. Report red and stop.
