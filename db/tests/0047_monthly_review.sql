-- V50: `monthly_review`, the owner's record that a calendar month's review is complete. All
-- names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'monthly_review') = 1,
    'the monthly_review table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'monthly_review'::regclass),
    'monthly_review must have row level security enabled';
  RAISE NOTICE 'PASS 0047 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0047_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('review household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('review household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'review owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'review member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other review owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'review-owner@example.invalid', 'owner') RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'review-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-review-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  RETURN NEXT;
END $$;

-- The first-day check.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0047_fixture();
  BEGIN
    INSERT INTO monthly_review (household_id, review_month) VALUES (f.h1, '2026-02-15');
    RAISE EXCEPTION 'a review_month that is not the first of its month must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%monthly_review_month_is_first_day%', refusal;
  END;

  INSERT INTO monthly_review (household_id, review_month) VALUES (f.h1, '2026-02-01');
  ASSERT (SELECT count(*) FROM monthly_review WHERE household_id = f.h1) = 1,
    'a first-of-month review_month must be accepted';

  RAISE NOTICE 'PASS 0047 first-day check';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0047_fixture();
  INSERT INTO monthly_review (household_id, review_month) VALUES (f.h1, '2026-02-01');
  INSERT INTO monthly_review (household_id, review_month) VALUES (f.h2, '2026-02-01');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM monthly_review) = 1, 'owner sees only its own household review';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM monthly_review) = 0, 'a household member reads no review';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM monthly_review) = 1, 'the other household sees only its own review';
  ASSERT (SELECT count(*) FROM monthly_review WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s review';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM monthly_review) = 0, 'anonymous review reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0047 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
