#!/usr/bin/env bash
# Seeds the owner's app_user row against the household and person created by V7. The household
# name, the owner's display name and tax residency, and the reporting pair are application data
# and are edited in Settings > Household, not here.
#
# ALLOWED_USER_EMAIL stays in .env: it is the authorization pin, and Flyway cannot read it.
# Deliberately refuses a second owner — the app has no concept of one. An explicit repair mode
# changes only the sole existing owner's email when the configured authorization pin was wrong.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh
: "${ALLOWED_USER_EMAIL:?ALLOWED_USER_EMAIL is unset — copy .env.example to .env}"
: "${DATABASE_URL:=$(scripts/dsn.sh)}"

case "${1:-}" in
  "") mode=seed ;;
  --repair-owner-email) mode=repair ;;
  *)
    echo "usage: scripts/seed-household.sh [--repair-owner-email]" >&2
    exit 2
    ;;
esac

if [ "$mode" = repair ]; then
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v email="$ALLOWED_USER_EMAIL" <<'SQL'
BEGIN;
LOCK TABLE app_user IN SHARE ROW EXCLUSIVE MODE;
DO $$
DECLARE owner_count INTEGER;
BEGIN
  SELECT count(*) INTO owner_count FROM app_user WHERE role = 'owner';
  IF owner_count <> 1 THEN
    RAISE EXCEPTION 'owner email repair requires exactly one existing owner';
  END IF;
END $$;
UPDATE app_user SET email = :'email' WHERE role = 'owner';
COMMIT;
SQL
  psql "$DATABASE_URL" -c "SELECT email, role FROM app_user;"
  exit 0
fi

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v email="$ALLOWED_USER_EMAIL" <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM person WHERE is_self) THEN
    RAISE EXCEPTION 'no household yet — run: docker compose run --rm flyway migrate';
  END IF;
  IF EXISTS (SELECT FROM app_user WHERE role = 'owner') THEN
    RAISE EXCEPTION 'an owner already exists — this script runs once per database';
  END IF;
END $$;

INSERT INTO app_user (id, household_id, person_id, email, role)
SELECT gen_random_uuid(), p.household_id, p.id, :'email', 'owner'
  FROM person p WHERE p.is_self LIMIT 1;
SQL
psql "$DATABASE_URL" -c "SELECT email, role FROM app_user;"
