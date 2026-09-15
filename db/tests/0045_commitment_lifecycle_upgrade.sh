#!/usr/bin/env bash
# Rehearse V48's backfill on a database migrated only through V47, so `commitment.next_due_date`
# is computed by the default next-due rule (spec "Occurrence rules") against real, already-stored
# commitment rows rather than rows inserted after the column exists. Every commitment here sits on
# an account whose `current_balance_as_of` is a fixed past date, so the expected `next_due_date`
# does not depend on which day this script happens to run.
set -euo pipefail
cd "$(dirname "$0")/../.."

server=${1:?database server DSN is required}
name="commitment_lifecycle_upgrade_$$"
trap 'psql "$server/postgres" -q -c "DROP DATABASE IF EXISTS $name WITH (FORCE)" >/dev/null 2>&1' EXIT
psql "$server/postgres" -q -c "CREATE DATABASE $name" >/dev/null
db="$server/$name"

for migration in $(ls db/migrations/V*.sql | sort -t V -k2 -n); do
  [[ $migration == db/migrations/V48__* ]] && break
  psql "$db" -q -1 -v ON_ERROR_STOP=1 -f "$migration" >/dev/null
done
for seed in db/seeds/*.sql; do psql "$db" -q -v ON_ERROR_STOP=1 -f "$seed" >/dev/null; done

psql "$db" -q -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
DO $$
DECLARE
  h UUID; p UUID; base CHAR(3); secondary CHAR(3);
  a_once UUID; a_monthly UUID; a_annual UUID; a_ended UUID;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('synthetic commitment lifecycle upgrade', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'synthetic owner', true)
  RETURNING id INTO p;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date, current_balance, current_balance_as_of)
  VALUES (h, p, 'synthetic once account', 'savings', base, 'XZ', 0, '2015-01-01', 0,
    '2020-06-15') RETURNING id INTO a_once;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date, current_balance, current_balance_as_of)
  VALUES (h, p, 'synthetic month-end monthly account', 'savings', base, 'XZ', 0, '2015-01-01', 0,
    '2020-06-15') RETURNING id INTO a_monthly;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date, current_balance, current_balance_as_of)
  VALUES (h, p, 'synthetic 29 february annual account', 'savings', base, 'XZ', 0, '2015-01-01', 0,
    '2023-01-01') RETURNING id INTO a_annual;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date, current_balance, current_balance_as_of)
  VALUES (h, p, 'synthetic ended account', 'savings', base, 'XZ', 0, '2015-01-01', 0,
    '2020-06-15') RETURNING id INTO a_ended;

  -- once: always defaults to first_date, whatever the account's balance date.
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date)
  VALUES (h, gen_random_uuid(), a_once, 'synthetic once', -1000.0000, base, 'once', '2025-03-10');

  -- month-end monthly: first_date clamps to the last day of every month; the account's balance
  -- date (2020-06-15) is strictly before the next scheduled month-end (2020-06-30).
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date)
  VALUES (h, gen_random_uuid(), a_monthly, 'synthetic month-end monthly', -2000.0000, base,
          'monthly', '2019-01-31');

  -- 29 February annual: the schedule clamps to 28 February outside a leap year. The account's
  -- balance date (2023-01-01) is strictly before the 2023 occurrence (2023-02-28, clamped from
  -- the 2016 leap-year first date).
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date)
  VALUES (h, gen_random_uuid(), a_annual, 'synthetic 29 february annual', -3000.0000, base,
          'annual', '2016-02-29');

  -- ended: every occurrence on or after the account's balance date falls after end_date, so no
  -- occurrence remains and next_due_date backfills to NULL.
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, end_date)
  VALUES (h, gen_random_uuid(), a_ended, 'synthetic ended', -4000.0000, base, 'monthly',
          '2019-01-31', '2019-06-30');
END $$;
SQL

psql "$db" -q -1 -v ON_ERROR_STOP=1 -f db/migrations/V48__commitment_lifecycle.sql >/dev/null

psql "$db" -q -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
DO $$
BEGIN
  ASSERT (SELECT next_due_date FROM commitment WHERE label = 'synthetic once') = '2025-03-10',
    'a once commitment backfills to its first date';
  ASSERT (SELECT next_due_date FROM commitment WHERE label = 'synthetic month-end monthly')
           = '2020-06-30',
    'a month-end monthly commitment backfills to the first month-end after the balance date';
  ASSERT (SELECT next_due_date FROM commitment WHERE label = 'synthetic 29 february annual')
           = '2023-02-28',
    'a 29 February annual commitment backfills clamped to 28 February outside a leap year';
  ASSERT (SELECT next_due_date FROM commitment WHERE label = 'synthetic ended') IS NULL,
    'an ended commitment with no occurrence left backfills to NULL';
  RAISE NOTICE 'PASS 0045 upgrade: V47-to-V48 backfill matches the default next-due rule';
END $$;
SQL
