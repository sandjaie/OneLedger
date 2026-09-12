#!/usr/bin/env bash
# Rehearse the real V32-to-V33 refusal with a populated legacy cash link.
set -euo pipefail
cd "$(dirname "$0")/../.."

server=${1:?database server DSN is required}
name="allocation_upgrade_$$"
error_file=$(mktemp)
trap 'psql "$server/postgres" -q -c "DROP DATABASE IF EXISTS $name WITH (FORCE)" >/dev/null 2>&1; rm -f "$error_file"' EXIT
psql "$server/postgres" -q -c "CREATE DATABASE $name" >/dev/null
db="$server/$name"

for migration in $(ls db/migrations/V*.sql | sort -t V -k2 -n); do
  [[ $migration == db/migrations/V33__* ]] && break
  psql "$db" -q -1 -v ON_ERROR_STOP=1 -f "$migration" >/dev/null
done
for seed in db/seeds/*.sql; do psql "$db" -q -v ON_ERROR_STOP=1 -f "$seed" >/dev/null; done

psql "$db" -q -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
DO $$
DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('synthetic allocation upgrade', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'synthetic owner', true)
  RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date) VALUES (h, p, 'synthetic cash', 'savings', base, 'XZ',
    0, '2026-01-01') RETURNING id INTO a;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
    currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, source, dedupe_hash, investment_transaction_id) VALUES (h, a, '2026-03-01',
    'SYNTHETIC UPGRADE CASH', -1, base, '2026-03-01', 1, 1, -1, -1, 'expense', 'manual',
    'allocation-upgrade', gen_random_uuid());
END $$;
SQL

if psql "$db" -q -1 -v ON_ERROR_STOP=1 -f db/migrations/V33__investment_allocations.sql \
    >/dev/null 2>"$error_file"; then
  echo "FAIL: V33 accepted a populated legacy investment cash link"
  exit 1
fi
grep -q "legacy investment cash links must be cleared" "$error_file"
psql "$db" -q -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
DO $$
BEGIN
  ASSERT EXISTS (SELECT FROM information_schema.columns WHERE table_schema = 'public'
    AND table_name = 'transaction' AND column_name = 'investment_transaction_id');
  ASSERT (SELECT count(*) = 1 FROM transaction WHERE investment_transaction_id IS NOT NULL);
  ASSERT to_regclass('public.investment_cash_allocation') IS NULL;
  ASSERT to_regprocedure('public.assert_legacy_investment_cash_links_empty(bigint)') IS NULL;
  RAISE NOTICE 'PASS 0030 real populated V32-to-V33 refusal preserves V32';
END $$;
SQL
