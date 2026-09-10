---
name: verify
description: Run the OneLedger gate — docs and real-data checks, secrets, api lint/types/tests,
migrations from empty plus the SQL invariant tests, web types/tests/bundle scan. Use before opening
a PR, as the stop condition for /goal and /loop, and whenever asked whether the tree is green.
---

# verify

Run each section in order and stop at the first failure. Report every section as `ok` or `FAIL`
with **the number of checks that actually ran**, then one verdict line.

1. `python3 scripts/check-docs.py` — links, ADR link labels, HTML, no statement tracked by git
2. `pre-commit run --all-files` — secrets, formatting, and every commit-stage hook
3. `cd api && uv run ruff check . && uv run mypy . && uv run pytest -q`
4. Migrations from empty, against a **scratch stack that cannot reach the working one**:

   ```bash
   (
     set -euo pipefail
     export COMPOSE_PROJECT_NAME=oneledger-verify DB_PORT=55432
     # Pin the scratch password rather than inheriting .env's: a properly secured install
     # sets a real POSTGRES_PASSWORD, and this URL would then fail to authenticate against
     # the database compose just initialised with it.
     export POSTGRES_PASSWORD=oneledger POSTGRES_PORT=55432
     export DATABASE_URL="$(scripts/dsn.sh)"   # assembled from the parts, never restated

     # Runs on success and on failure alike. Without it a failing test — the case this gate
     # exists to catch — leaves the scratch stack up and port 55432 held.
     trap 'docker compose down -v >/dev/null 2>&1' EXIT

     docker compose down -v
     docker compose up -d db
     docker compose run --rm flyway migrate
     for f in db/seeds/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
     for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
   )
   ```

   **`COMPOSE_PROJECT_NAME` scopes the teardown, and it is not a tidiness flourish.** Without
   it these commands operate on the working stack: `down -v` stops its containers, and
   `up -d db` and `flyway migrate` would run against the ledger rather than a scratch copy.
   `DB_PORT` keeps the scratch database off 5432 so it can run beside a working stack.

   The working volumes themselves are now **external** (`docker-compose.yml`), so `down -v`
   can no longer remove `pgdata` or `documents` whatever project it runs in. That guard exists
   because the discipline above was not enough on its own: on 10 September 2026 a
   `docker compose --profile test down -v`, which looks scoped to the test stack and is not,
   removed both. Rely on the volumes being external; keep the project name anyway, because it
   is what stops a verification run from migrating the working database.

   (`flyway clean` is not the reset to reach for either: it has been disabled by default since
   Flyway 10, and a dropped scratch volume is a truer "from empty" anyway.)
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
