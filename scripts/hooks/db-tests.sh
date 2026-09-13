#!/usr/bin/env bash
# Every db/tests/*.sql asserts an invariant, and the final verification runner executes them
# against a real test server before the first push — an invariant proven only in CI is too late.
#
# The database is a throwaway one, created on the TEST server for this run, migrated from empty
# with this branch's own db/migrations, seeded, tested and dropped. Never the working ledger,
# and never the shared test database either:
#
#   - The working ledger is what `scripts/dsn.sh` resolves to by default, and this hook used to
#     test whatever that was. The tests roll back, but a hook that aims at the ledger unless told
#     otherwise is the mistake that emptied it twice on 10 September 2026.
#   - The shared test database sits at whatever version was last migrated into it, so a branch
#     adding a migration would be tested against the schema before it; and migrating it from a
#     branch whose migration is still changing is how Flyway's checksum refusal is earned.
#
# From empty on every final database gate therefore proves two things at once: the migrations apply
# in order on a clean database, and every invariant holds on the result. psql applies the files in
# version order, which is Flyway's effect for forward-only SQL.
set -euo pipefail
cd "$(dirname "$0")/../.."

shopt -s nullglob
tests=(db/tests/*.sql)
upgrade_tests=(db/tests/*_upgrade.sh)
[ ${#tests[@]} -gt 0 ] || { echo "no db tests yet — skipping"; exit 0; }

if [ -z "${ONELEDGER_TEST_DSN:-}" ]; then
  eval "$(scripts/test-db.sh env)" >/dev/null
fi
server="${ONELEDGER_TEST_DSN%/*}"

if ! psql "$server/postgres" -c 'SELECT 1' >/dev/null 2>&1; then
  echo "FAIL: ${#tests[@]} invariant test(s) exist but the test database server is unreachable."
  echo "      Start it and verify again:  eval \"\$(scripts/test-db.sh up)\""
  echo "      (It is never the working ledger: this hook does not fall back to it.)"
  exit 1
fi

name="push_$$"
trap 'psql "$server/postgres" -q -c "DROP DATABASE IF EXISTS $name WITH (FORCE)" >/dev/null 2>&1' EXIT
psql "$server/postgres" -q -c "CREATE DATABASE $name" >/dev/null
db="$server/$name"

migrations=0
for f in $(ls db/migrations/V*.sql | sort -t V -k2 -n); do
  if ! psql "$db" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>"/tmp/db-tests-$$.err"; then
    echo "FAIL: $(basename "$f") does not apply on a database migrated from empty"
    grep -m3 ERROR "/tmp/db-tests-$$.err"; rm -f "/tmp/db-tests-$$.err"; exit 1
  fi
  migrations=$((migrations + 1))
done
rm -f "/tmp/db-tests-$$.err"
for f in db/seeds/*.sql; do psql "$db" -q -v ON_ERROR_STOP=1 -f "$f" >/dev/null; done

for t in "${tests[@]}"; do
  echo "  $t"
  psql "$db" -v ON_ERROR_STOP=1 -f "$t" >/dev/null
done
for t in "${upgrade_tests[@]}"; do
  echo "  $t"
  bash "$t" "$server"
done
echo "OK: $migrations migration(s) applied from empty; ${#tests[@]} invariant test(s) passed"
