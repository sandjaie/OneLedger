#!/usr/bin/env bash
# The database the live test suite is allowed to truncate, and how to reach it.
#
#   eval "$(scripts/test-db.sh up)"     # start it, migrate it, seed it, export the DSN
#   eval "$(scripts/test-db.sh env)"    # just export the DSN for a database already up
#   scripts/test-db.sh down             # stop it and drop its volume
#
# `api/tests/*_live.py` reads ONELEDGER_TEST_DSN and nothing else. The working credentials in
# .env cannot reach those tests however the environment is arranged, which is the whole point:
# they empty transaction, import_batch, document, statement_layout and classification_rule on
# every fixture, and pointed at the working database that is not a test run.
#
# Each checkout — a worktree under .claude/worktrees/ included — is its own compose project, so
# it gets its own db-test, its own port and its own volume. On 10 September 2026 a worktree's
# `up` could not bind the main checkout's port and printed nothing, so the live tests skipped
# and looked green; and its `down` removed the main checkout's volume by name.
set -euo pipefail
cd "$(dirname "$0")/.."

# `eval "$(scripts/test-db.sh up)"` discards this script's exit status — eval of nothing
# succeeds — so a failure also prints `false`, and the eval fails with it.
trap 'status=$?; [ "$status" -eq 0 ] || echo false; exit "$status"' EXIT
fail() {
  echo "test-db.sh: $*" >&2
  exit 1
}

# The compose project, normalised as compose normalises it: lower case, [a-z0-9_-], no leading
# `-` or `_`. The volume compose creates for `pgdata_test` carries it as a prefix.
# ponytail: ignores a COMPOSE_PROJECT_NAME set inside .env; read it from there if one ever is.
project="$(printf %s "${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}" |
  tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | sed 's/^[-_]*//')"

# The host port of this project's db-test, when it is running.
running_port() {
  local mapped
  mapped="$(docker compose --profile test port db-test 5432 2>/dev/null)" && [ -n "$mapped" ] &&
    echo "${mapped##*:}"
}

# 55434 when nothing holds it, otherwise any port the OS says is free.
free_port() {
  python3 -c '
import socket
s = socket.socket()
try:
    s.bind(("127.0.0.1", 55434))
except OSError:
    s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])'
}

# Both, from the same container. The fixtures read ONELEDGER_TEST_DSN; the routes they
# exercise connect through `oneledger.db.connect`, which reads POSTGRES_*. Exporting only the
# first would truncate one database and test against another, so `live_database.py` refuses
# to run when the two disagree.
emit_env() {
  cat <<EOF
export ONELEDGER_TEST_DSN='$dsn'
export POSTGRES_USER=oneledger
export POSTGRES_PASSWORD=oneledger
export POSTGRES_DB=oneledger_test
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=$port
EOF
}

case "${1:-up}" in
  up)
    # Exported, because every compose call below must see it: one that saw the default would
    # recreate db-test on a port another checkout already holds.
    export TEST_DB_PORT="${TEST_DB_PORT:-$(running_port || free_port)}"
    port="$TEST_DB_PORT"
    dsn="postgresql://oneledger:oneledger@127.0.0.1:${port}/oneledger_test"
    docker compose --profile test up -d db-test >&2 ||
      fail "db-test did not start on 127.0.0.1:$port; compose says why above. If that port is taken, set TEST_DB_PORT to a free one."
    # `up -d` returns when the container starts, not when Postgres accepts connections.
    ready=
    for _ in $(seq 1 30); do
      if psql "$dsn" -c 'SELECT 1' >/dev/null 2>&1; then ready=1 && break; fi
      sleep 1
    done
    [ -n "$ready" ] || fail "db-test never accepted a connection on 127.0.0.1:$port."
    docker compose --profile test run --rm flyway-test migrate >&2
    for f in db/seeds/*.sql; do psql "$dsn" -v ON_ERROR_STOP=1 -f "$f" >/dev/null; done
    # The live fixtures need a household with a person to attach an account to, and they skip
    # rather than fail without one. Seeded here so the suite runs rather than reports green
    # having checked nothing.
    psql "$dsn" -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
DO $$
DECLARE h UUID; p UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  IF EXISTS (SELECT FROM person WHERE is_self) THEN RETURN; END IF;
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('test household', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'self', true)
  RETURNING id INTO p;
  INSERT INTO app_user (household_id, person_id, email, role)
  VALUES (h, p, 'test-owner@example.invalid', 'owner');
END $$;
SQL
    emit_env
    ;;
  env)
    port="${TEST_DB_PORT:-$(running_port || echo 55434)}"
    dsn="postgresql://oneledger:oneledger@127.0.0.1:${port}/oneledger_test"
    emit_env
    ;;
  down)
    docker compose --profile test rm -sfv db-test >&2
    # This project's volume and no other: `oneledger_pgdata_test` is the main checkout's.
    docker volume rm -f "${project}_pgdata_test" >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: scripts/test-db.sh [up|env|down]" >&2
    exit 2
    ;;
esac
