#!/usr/bin/env bash
# Seeds household, person and app_user from .env. Deliberately NOT idempotent — a second run
# creates a second household, which the app has no concept of. Run it once per database.
#
# It lives here rather than with OIDC because Task 8's reporting_config() reads the household's
# reporting pair and raises without a row: PR 5 would otherwise depend on PR 7.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/load-env.sh
: "${DATABASE_URL:=$(scripts/dsn.sh)}"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -v household="$HOUSEHOLD_NAME"    -v person="$OWNER_DISPLAY_NAME" \
  -v residency="$OWNER_TAX_RESIDENCY" -v email="$ALLOWED_USER_EMAIL" \
  -v base="$BASE_CURRENCY"          -v secondary="$SECONDARY_CURRENCY" <<'SQL'
WITH h AS (
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES (:'household', :'base', :'secondary') RETURNING id
), p AS (
  INSERT INTO person (household_id, display_name, is_self, tax_residency)
  SELECT id, :'person', true, :'residency' FROM h RETURNING id, household_id
)
INSERT INTO app_user (id, household_id, person_id, email, role)
SELECT gen_random_uuid(), p.household_id, p.id, :'email', 'owner' FROM p;
SQL
psql "$DATABASE_URL" -c "SELECT email, role FROM app_user;"
