-- V46: month_end_position is a recomputable cache of wealth-history evidence — no SECURITY
-- DEFINER write path, just household-scoped RLS and an account-scoped composite foreign key,
-- same convention as coverage_gap_acknowledgement (0042). All names, identifiers and amounts
-- below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'month_end_position') = 1,
    'the month_end_position table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'month_end_position'::regclass),
    'month_end_position must have row level security enabled';
  RAISE NOTICE 'PASS 0043 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0043_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID, a1 UUID, a2 UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('wealth history household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('wealth history household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'wealth history owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'wealth history member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other wealth history owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'wealth-history-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'wealth-history-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-wealth-history-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic wealth history account', 'savings', base, 'XZ', 0, '2024-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic wealth history account', 'savings', base, 'XZ', 0, '2024-01-01')
  RETURNING id INTO a2;
  RETURN NEXT;
END $$;

-- A cross-household account reference is refused by the composite foreign key.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0043_fixture();
  BEGIN
    INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode,
                                      currency, status)
    VALUES (f.h1, DATE '2024-01-31', f.a2, 'ledger_balance', f.base, 'before_opening');
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_month_end_position_account%', refusal;
  END;
  RAISE NOTICE 'PASS 0043 cross-household account foreign key refused';
END $$;
ROLLBACK;

-- month_end_position_values_agree: an 'available' row needs all three values; any other status
-- must carry neither reporting value.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0043_fixture();
  BEGIN
    INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode,
                                      currency, native_value, value_base, value_secondary, status)
    VALUES (f.h1, DATE '2024-01-31', f.a1, 'ledger_balance', f.base, 100, NULL, NULL, 'available');
    RAISE EXCEPTION 'an available row missing a reporting value must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%month_end_position_values_agree%', refusal;
  END;
  BEGIN
    INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode,
                                      currency, value_base, value_secondary, status)
    VALUES (f.h1, DATE '2024-01-31', f.a1, 'ledger_balance', f.base, 100, 200, 'missing_rate');
    RAISE EXCEPTION 'a non-available row carrying a reporting value must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%month_end_position_values_agree%', refusal;
  END;
  -- The honest shapes both succeed: available with all three values, and every other status
  -- with neither reporting value (native_value may still be known, e.g. missing_rate).
  INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode, currency,
                                    native_value, value_base, value_secondary, status)
  VALUES (f.h1, DATE '2024-01-31', f.a1, 'ledger_balance', f.base, 100, 100, 200, 'available');
  INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode, currency,
                                    native_value, status)
  VALUES (f.h1, DATE '2024-02-29', f.a1, 'ledger_balance', f.base, 100, 'missing_rate');
  RAISE NOTICE 'PASS 0043 values-agree CHECK';
END $$;
ROLLBACK;

-- uq_month_end_position's NULLS NOT DISTINCT: two account-level rows (holding_id NULL) for the
-- same household, month and account collide even though NULL is never ordinarily equal to NULL.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0043_fixture();
  INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode, currency,
                                    status)
  VALUES (f.h1, DATE '2024-01-31', f.a1, 'ledger_balance', f.base, 'before_opening');
  BEGIN
    INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode, currency,
                                      status)
    VALUES (f.h1, DATE '2024-01-31', f.a1, 'ledger_balance', f.base, 'before_opening');
    RAISE EXCEPTION 'a second account-level row for the same household, month and account must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_month_end_position%', refusal;
  END;
  RAISE NOTICE 'PASS 0043 NULL-holding uniqueness';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS: only the owning household's authenticated owner
-- reads or writes its own month_end_position rows.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0043_fixture();
  INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode, currency,
                                    status)
  VALUES (f.h1, DATE '2024-01-31', f.a1, 'ledger_balance', f.base, 'before_opening');
  INSERT INTO month_end_position (household_id, month_end, account_id, valuation_mode, currency,
                                    status)
  VALUES (f.h2, DATE '2024-01-31', f.a2, 'ledger_balance', f.base, 'before_opening');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM month_end_position) = 1,
    'owner sees only its own household position rows';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM month_end_position) = 0,
    'a household member reads no position row';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM month_end_position) = 1,
    'the other household sees only its own row';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM month_end_position) = 0,
    'anonymous position reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0043 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
