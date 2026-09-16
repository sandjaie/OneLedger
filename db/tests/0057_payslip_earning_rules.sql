-- V60: earning rules for salary analysis. All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'payslip_earning_rule') = 1,
    'payslip_earning_rule must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'payslip_earning_rule'::regclass),
    'payslip_earning_rule must have row level security enabled';
  RAISE NOTICE 'PASS 0057 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0057_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID)
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('earning rule household one', (SELECT min(code) FROM currency),
          (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency)))
  RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('earning rule household two', (SELECT min(code) FROM currency),
          (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency)))
  RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'earning rule owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'earning rule member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other earning rule owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'earning-rule-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'earning-rule-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-earning-rule-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  RETURN NEXT;
END $$;

-- The component check refuses anything outside the three named components.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0057_fixture();
  BEGIN
    INSERT INTO payslip_earning_rule (household_id, label, component)
    VALUES (f.h1, 'Basic', 'bonus');
    RAISE EXCEPTION 'an unknown component must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  INSERT INTO payslip_earning_rule (household_id, label, component) VALUES (f.h1, 'Basic', 'fixed');
  RAISE NOTICE 'PASS 0057 component check';
END $$;
ROLLBACK;

-- The primary key is (household_id, label): a second rule for the same label upserts (application
-- code uses ON CONFLICT), and the same label is independent per household.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0057_fixture();
  INSERT INTO payslip_earning_rule (household_id, label, component) VALUES (f.h1, 'Basic', 'fixed');
  BEGIN
    INSERT INTO payslip_earning_rule (household_id, label, component)
    VALUES (f.h1, 'Basic', 'variable');
    RAISE EXCEPTION 'a duplicate (household_id, label) insert must have been refused';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  INSERT INTO payslip_earning_rule (household_id, label, component)
  VALUES (f.h2, 'Basic', 'variable');
  ASSERT (SELECT component FROM payslip_earning_rule WHERE household_id = f.h1 AND label = 'Basic')
       = 'fixed', 'household one keeps its own rule for the shared label';
  ASSERT (SELECT component FROM payslip_earning_rule WHERE household_id = f.h2 AND label = 'Basic')
       = 'variable', 'household two keeps its own rule for the shared label';
  RAISE NOTICE 'PASS 0057 primary key scoped per household';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0057_fixture();
  INSERT INTO payslip_earning_rule (household_id, label, component) VALUES (f.h1, 'Basic', 'fixed');
  INSERT INTO payslip_earning_rule (household_id, label, component)
  VALUES (f.h2, 'Bonus', 'variable');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip_earning_rule) = 1, 'owner sees only its own household rule';
  ASSERT (SELECT component FROM payslip_earning_rule WHERE household_id = f.h1) = 'fixed',
    'owner reads its own rule row';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip_earning_rule) = 0, 'a household member reads no rule';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip_earning_rule) = 1,
    'the other household sees only its own rule';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM payslip_earning_rule) = 0, 'anonymous rule reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0057 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
