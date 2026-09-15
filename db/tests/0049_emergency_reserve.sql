-- V52: the emergency reserve settings, eligible accounts, and the widened action_mark key shape.
-- Neither new table has a SECURITY DEFINER write path, the same convention as `goal` and
-- `resource_reservation` (V51). All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'emergency_reserve') = 1,
    'the emergency_reserve table must exist';
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'emergency_resource_account') = 1,
    'the emergency_resource_account table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'emergency_reserve'::regclass),
    'emergency_reserve must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'emergency_resource_account'::regclass),
    'emergency_resource_account must have row level security enabled';
  RAISE NOTICE 'PASS 0049 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0049_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    a1 UUID, a_foreign UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('emergency household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('emergency household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'emergency owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'emergency member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other emergency owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'emergency-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'emergency-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-emergency-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic emergency account', 'savings', base, 'XZ', 10000, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic emergency account', 'savings', base, 'XZ', 10000, '2026-01-01')
  RETURNING id INTO a_foreign;
  RETURN NEXT;
END $$;

-- emergency_reserve shape: target bounds, reference bounds, bucket subset, manual agreement.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0049_fixture();

  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method)
    VALUES (f.h1, 0, 'reference_period');
    RAISE EXCEPTION 'a zero target must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_target_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method)
    VALUES (f.h1, 60.1, 'reference_period');
    RAISE EXCEPTION 'a target over 60 months must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_target_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, reference_months)
    VALUES (f.h1, 6, 'reference_period', 0);
    RAISE EXCEPTION 'a zero reference_months must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_reference_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, reference_months)
    VALUES (f.h1, 6, 'reference_period', 25);
    RAISE EXCEPTION 'a reference_months over 24 must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_reference_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, buckets)
    VALUES (f.h1, 6, 'reference_period', '{}');
    RAISE EXCEPTION 'an empty bucket list must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_buckets_are_spending%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, buckets)
    VALUES (f.h1, 6, 'reference_period', '{need,income}');
    RAISE EXCEPTION 'a non-spending bucket (income) must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_buckets_are_spending%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, buckets)
    VALUES (f.h1, 6, 'reference_period', '{need,ignore}');
    RAISE EXCEPTION 'a non-spending bucket (ignore) must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_buckets_are_spending%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, manual_monthly_base)
    VALUES (f.h1, 6, 'reference_period', 1000.0000);
    RAISE EXCEPTION 'a reference_period row with a manual base must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_manual_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method)
    VALUES (f.h1, 6, 'manual');
    RAISE EXCEPTION 'a manual row without a manual base must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_manual_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_reserve (household_id, target_months, baseline_method, manual_monthly_base)
    VALUES (f.h1, 6, 'manual', 0);
    RAISE EXCEPTION 'a zero manual base must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_manual_agrees%', refusal;
  END;

  INSERT INTO emergency_reserve (household_id, target_months, baseline_method, reference_months,
                                  buckets)
  VALUES (f.h1, 6, 'reference_period', 6, '{need,debt_service}');
  ASSERT (SELECT count(*) FROM emergency_reserve WHERE household_id = f.h1) = 1,
    'a valid reference-period row must be accepted';

  RAISE NOTICE 'PASS 0049 emergency_reserve shape';
END $$;
ROLLBACK;

-- emergency_resource_account shape: access_days bounds, note bounds, cross-household account scope.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0049_fixture();

  BEGIN
    INSERT INTO emergency_resource_account (household_id, account_id, access_days)
    VALUES (f.h1, f.a1, -1);
    RAISE EXCEPTION 'a negative access_days must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_access_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_resource_account (household_id, account_id, access_days)
    VALUES (f.h1, f.a1, 366);
    RAISE EXCEPTION 'an access_days over 365 must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_access_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_resource_account (household_id, account_id, restriction_note)
    VALUES (f.h1, f.a1, repeat('x', 201));
    RAISE EXCEPTION 'a restriction note over 200 characters must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%emergency_note_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO emergency_resource_account (household_id, account_id)
    VALUES (f.h1, f.a_foreign);
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_emergency_account_scope%', refusal;
  END;

  INSERT INTO emergency_resource_account (household_id, account_id, access_days, restriction_note)
  VALUES (f.h1, f.a1, 3, 'Synthetic notice period');
  ASSERT (SELECT count(*) FROM emergency_resource_account WHERE household_id = f.h1) = 1,
    'a valid eligible account must be accepted';

  RAISE NOTICE 'PASS 0049 emergency_resource_account shape';
END $$;
ROLLBACK;

-- The widened action_mark_key_shape: every documented key form is accepted, a malformed one is
-- refused. follow-up/card/commitment forms are unchanged from V49; goal and reserve are this
-- phase's Task 1/2 keys; record.* is Phase 3.4's, parsed here but with no items yet.
BEGIN;
DO $$
DECLARE f RECORD; id1 UUID := gen_random_uuid(); refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0049_fixture();

  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'follow-up.' || id1, current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'card.' || id1, current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'commitment.' || id1 || '.2027-01-01', current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'goal.' || id1, current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'reserve.' || id1, current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'record.' || id1 || '.review', current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'record.' || id1 || '.renewal.2027-01-01', current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'record.' || id1 || '.maturity.2027-01-01', current_date + 1);
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'record.' || id1 || '.deadline.2027-01-01', current_date + 1);
  ASSERT (SELECT count(*) FROM action_mark WHERE household_id = f.h1) = 9,
    'every documented key form must be accepted';

  BEGIN
    INSERT INTO action_mark (household_id, action_key, snoozed_until)
    VALUES (f.h1, 'record.' || id1 || '.bogus', current_date + 1);
    RAISE EXCEPTION 'an unrecognised record sub-kind must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_mark_key_shape%', refusal;
  END;
  BEGIN
    INSERT INTO action_mark (household_id, action_key, snoozed_until)
    VALUES (f.h1, 'reserve.not-a-uuid', current_date + 1);
    RAISE EXCEPTION 'a malformed reserve key must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_mark_key_shape%', refusal;
  END;

  RAISE NOTICE 'PASS 0049 widened action_mark_key_shape';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS on both new tables.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0049_fixture();
  INSERT INTO emergency_reserve (household_id, target_months, baseline_method)
  VALUES (f.h1, 6, 'reference_period');
  INSERT INTO emergency_reserve (household_id, target_months, baseline_method)
  VALUES (f.h2, 6, 'reference_period');
  INSERT INTO emergency_resource_account (household_id, account_id)
  VALUES (f.h1, f.a1);
  INSERT INTO emergency_resource_account (household_id, account_id)
  VALUES (f.h2, f.a_foreign);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM emergency_reserve) = 1, 'owner sees only its own reserve settings';
  ASSERT (SELECT count(*) FROM emergency_resource_account) = 1,
    'owner sees only its own eligible accounts';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM emergency_reserve) = 0, 'a household member reads no reserve settings';
  ASSERT (SELECT count(*) FROM emergency_resource_account) = 0,
    'a household member reads no eligible account';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM emergency_reserve) = 1, 'the other household sees only its own row';
  ASSERT (SELECT count(*) FROM emergency_reserve WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s reserve settings';
  ASSERT (SELECT count(*) FROM emergency_resource_account) = 1,
    'the other household sees only its own eligible account';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM emergency_reserve) = 0, 'anonymous reserve reads are empty';
  ASSERT (SELECT count(*) FROM emergency_resource_account) = 0,
    'anonymous eligible-account reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0049 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
