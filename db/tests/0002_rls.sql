-- db/tests/0002_rls.sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'person'),
    'RLS must be enabled on person';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'app_user'),
    'RLS must be enabled on app_user';
  RAISE NOTICE 'PASS 0002 structure';
END $$;

-- Isolation. Two things this test MUST get right, both easy to get wrong:
--
--   1. Rows must exist first. Asserting count = 0 against an empty table passes whether or
--      not a policy exists, which is a test that proves nothing.
--   2. SET LOCAL ROLE only lasts for the enclosing transaction. psql runs in autocommit, so
--      a bare SET LOCAL is discarded immediately and the assertions below would run as the
--      table owner -- who bypasses RLS anyway. The explicit BEGIN is load-bearing.
BEGIN;
  -- Seed a household that WOULD be visible if the policy were missing or permissive.
  WITH pair AS (
    SELECT (SELECT min(code) FROM currency) AS base,
           (SELECT min(code) FROM currency
             WHERE code > (SELECT min(code) FROM currency)) AS secondary
  )
  INSERT INTO household (id, name, base_currency, secondary_currency)
  SELECT '11111111-1111-1111-1111-111111111111', 'rls fixture', base, secondary FROM pair;
  INSERT INTO person (id, household_id, display_name) VALUES
    ('22222222-2222-2222-2222-222222222222',
     '11111111-1111-1111-1111-111111111111', 'rls fixture');
  INSERT INTO app_user (id, household_id, person_id, email) VALUES
    ('33333333-3333-3333-3333-333333333333',
     '11111111-1111-1111-1111-111111111111',
     '22222222-2222-2222-2222-222222222222', 'fixture@example.test');

  -- Prove the fixture is actually there, as owner.
  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM person) >= 1, 'fixture must exist before the isolation check';
  END $$;

  SET LOCAL ROLE anon;
  DO $$
  BEGIN
    -- app.user_id is unset here, so current_household_id() is NULL, so every predicate is
    -- NULL -> no rows. If any of these returns > 0, a policy is missing or permissive.
    ASSERT (SELECT count(*) FROM household) = 0, 'anon must not read household';
    ASSERT (SELECT count(*) FROM person)    = 0, 'anon must not read person';
    ASSERT (SELECT count(*) FROM app_user)  = 0, 'anon must not read app_user';
    -- But the shared seed set must remain readable, or M1 has no institution picker.
    ASSERT (SELECT count(*) FROM institution WHERE household_id IS NULL) > 0,
      'system seed institutions must stay visible';
    RAISE NOTICE 'PASS 0002 isolation';
  END $$;
  RESET ROLE;
ROLLBACK;   -- the fixture is a test artefact and must not survive
