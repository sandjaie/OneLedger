-- V45: coverage_gap_acknowledgement is an owner assumption, not an evidenced fact — no
-- SECURITY DEFINER write path, just household-scoped RLS and an account-scoped composite
-- foreign key when the subject is an account. All names, identifiers and amounts below are
-- synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'coverage_gap_acknowledgement') = 1,
    'the coverage_gap_acknowledgement table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'coverage_gap_acknowledgement'::regclass),
    'coverage_gap_acknowledgement must have row level security enabled';
  RAISE NOTICE 'PASS 0042 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0042_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID, a1 UUID, a2 UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('coverage household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('coverage household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'coverage owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'coverage member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other coverage owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'coverage-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'coverage-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-coverage-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic coverage account', 'savings', base, 'XZ', 0, '2024-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic coverage account', 'savings', base, 'XZ', 0, '2024-01-01')
  RETURNING id INTO a2;
  RETURN NEXT;
END $$;

-- A cross-household account reference is refused by the composite foreign key.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0042_fixture();
  BEGIN
    INSERT INTO coverage_gap_acknowledgement (household_id, subject, account_id, month, reason)
    VALUES (f.h1, 'account', f.a2, DATE '2024-01-01', 'not_issued');
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_coverage_gap_account_scope%', refusal;
  END;
  RAISE NOTICE 'PASS 0042 cross-household account foreign key refused';
END $$;
ROLLBACK;

-- The subject/account CHECK: an account subject needs an account, a payslips subject must not
-- carry one.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0042_fixture();
  BEGIN
    INSERT INTO coverage_gap_acknowledgement (household_id, subject, account_id, month, reason)
    VALUES (f.h1, 'account', NULL, DATE '2024-01-01', 'not_issued');
    RAISE EXCEPTION 'an account subject without an account must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%coverage_gap_subject_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO coverage_gap_acknowledgement (household_id, subject, account_id, month, reason)
    VALUES (f.h1, 'payslips', f.a1, DATE '2024-01-01', 'not_issued');
    RAISE EXCEPTION 'a payslips subject with an account must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%coverage_gap_subject_agrees%', refusal;
  END;
  RAISE NOTICE 'PASS 0042 subject/account CHECK';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS: only the owning household's authenticated owner
-- reads or writes its own coverage_gap_acknowledgement rows.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0042_fixture();
  INSERT INTO coverage_gap_acknowledgement (household_id, subject, account_id, month, reason)
  VALUES (f.h1, 'account', f.a1, DATE '2024-01-01', 'not_issued');
  INSERT INTO coverage_gap_acknowledgement (household_id, subject, account_id, month, reason)
  VALUES (f.h2, 'account', f.a2, DATE '2024-01-01', 'unobtainable');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM coverage_gap_acknowledgement) = 1,
    'owner sees only its own household acknowledgement';
  ASSERT (SELECT reason FROM coverage_gap_acknowledgement WHERE household_id = f.h1)
    = 'not_issued', 'owner reads its own acknowledgement row';
  RESET ROLE;

  -- current_household_id() resolves only an 'owner' row (V5): a member's app.user_id never
  -- names a household at all, so household_isolation excludes every row for a member exactly
  -- as it does for a stranger.
  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM coverage_gap_acknowledgement) = 0,
    'a household member reads no acknowledgement';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM coverage_gap_acknowledgement) = 1,
    'the other household sees only its own row';
  ASSERT (SELECT reason FROM coverage_gap_acknowledgement WHERE household_id = f.h1) IS NULL,
    'the other household cannot read the first household''s acknowledgement';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM coverage_gap_acknowledgement) = 0,
    'anonymous acknowledgement reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0042 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
