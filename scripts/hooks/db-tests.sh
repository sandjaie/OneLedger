#!/usr/bin/env bash
# Every db/tests/*.sql asserts an invariant. They run on push, against a real
# database, because an invariant proven only in CI is proven too late.
set -euo pipefail

shopt -s nullglob
tests=(db/tests/*.sql)
[ ${#tests[@]} -gt 0 ] || { echo "no db tests yet — skipping"; exit 0; }

# One source of truth for the password: scripts/dsn.sh assembles it from .env.
: "${DATABASE_URL:=$(scripts/dsn.sh)}"

if ! psql "$DATABASE_URL" -c 'SELECT 1' >/dev/null 2>&1; then
  echo "FAIL: ${#tests[@]} invariant test(s) exist but the database is unreachable."
  echo "      Start it and push again:  docker compose up -d db"
  exit 1
fi

for t in "${tests[@]}"; do
  echo "  $t"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$t" >/dev/null
done
echo "OK: ${#tests[@]} invariant test(s) passed"
