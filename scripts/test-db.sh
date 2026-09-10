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
set -euo pipefail
cd "$(dirname "$0")/.."

port="${TEST_DB_PORT:-55434}"
dsn="postgresql://oneledger:oneledger@127.0.0.1:${port}/oneledger_test"

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
    docker compose --profile test up -d db-test >&2
    # `up -d` returns when the container starts, not when Postgres accepts connections.
    for _ in $(seq 1 30); do
      if psql "$dsn" -c 'SELECT 1' >/dev/null 2>&1; then break; fi
      sleep 1
    done
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
    emit_env
    ;;
  down)
    docker compose --profile test rm -sfv db-test >&2
    docker volume rm -f oneledger_pgdata_test >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: scripts/test-db.sh [up|env|down]" >&2
    exit 2
    ;;
esac
