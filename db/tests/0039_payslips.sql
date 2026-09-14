-- V42: payslips, entered or imported, immutable except their deposit link. All names,
-- identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid IN (
             'record_payslip(uuid,uuid,uuid,date,character,numeric,numeric,numeric,payslip_line_kind[],text[],numeric[])'::regprocedure,
             'assert_payslip_totals_agree()'::regprocedure,
             'preserve_payslip()'::regprocedure,
             'preserve_payslip_line()'::regprocedure,
             'assert_payslip_deposit()'::regprocedure
           )) = 5,
    'record_payslip and its trigger functions must exist';
  ASSERT has_function_privilege(
    'authenticated',
    'record_payslip(uuid,uuid,uuid,date,character,numeric,numeric,numeric,payslip_line_kind[],text[],numeric[])'::regprocedure,
    'EXECUTE'
  ), 'record_payslip must be granted to authenticated';
  ASSERT NOT has_function_privilege(
    'public',
    'record_payslip(uuid,uuid,uuid,date,character,numeric,numeric,numeric,payslip_line_kind[],text[],numeric[])'::regprocedure,
    'EXECUTE'
  ), 'record_payslip must be revoked from PUBLIC';
  RAISE NOTICE 'PASS 0039 structure and grants';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0039_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('payslip household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('payslip household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'payslip owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'payslip member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other payslip owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'payslip-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'payslip-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-payslip-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  RETURN NEXT;
END $$;

-- An owner entry records exactly, an identical replay returns the same row, and a reused key
-- with different arguments conflicts.
BEGIN;
DO $$
DECLARE f RECORD; first_id UUID; replay_id UUID; created BOOLEAN; key UUID := gen_random_uuid();
BEGIN
  SELECT * INTO f FROM pg_temp.t0039_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);

  SELECT recorded_payslip_id, r.created INTO first_id, created FROM record_payslip(
    key, NULL, NULL, '2026-01-01', f.base, 5000.0000, 1000.0000, 4000.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
    ARRAY[5000.0000, 1000.0000]) r;
  ASSERT first_id IS NOT NULL AND created, 'a reconciling entry is recorded as new';
  ASSERT (SELECT count(*) FROM payslip_line WHERE payslip_id = first_id) = 2,
    'both lines are stored';

  SELECT recorded_payslip_id, r.created INTO replay_id, created FROM record_payslip(
    key, NULL, NULL, '2026-01-01', f.base, 5000.0000, 1000.0000, 4000.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
    ARRAY[5000.0000, 1000.0000]) r;
  ASSERT replay_id = first_id AND NOT created, 'an identical replay returns the original row';

  BEGIN
    PERFORM r.recorded_payslip_id FROM record_payslip(
      key, NULL, NULL, '2026-02-01', f.base, 6000.0000, 1000.0000, 5000.0000,
      ARRAY['earning']::payslip_line_kind[], ARRAY['Basic'], ARRAY[6000.0000]) r;
    RAISE EXCEPTION 'a conflicting reused key must have been refused';
  EXCEPTION WHEN unique_violation THEN
    ASSERT SQLERRM LIKE '%idempotency%', 'the refusal names the idempotency conflict';
  END;
  RAISE NOTICE 'PASS 0039 owner entry, replay and conflicting key';
END $$;
ROLLBACK;

-- A member cannot record a payslip; a foreign owner cannot correct another household's payslip.
BEGIN;
DO $$
DECLARE f RECORD; owner_payslip_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0039_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SELECT recorded_payslip_id INTO owner_payslip_id FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', f.base, 5000.0000, 1000.0000, 4000.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
    ARRAY[5000.0000, 1000.0000]) r;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  BEGIN
    PERFORM r.recorded_payslip_id FROM record_payslip(
      gen_random_uuid(), NULL, NULL, '2026-02-01', f.base, 5000.0000, 1000.0000, 4000.0000,
      ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
      ARRAY[5000.0000, 1000.0000]) r;
    RAISE EXCEPTION 'a member must have been refused';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  BEGIN
    PERFORM r.recorded_payslip_id FROM record_payslip(
      gen_random_uuid(), owner_payslip_id, NULL, '2026-01-01', f.base,
      5100.0000, 1000.0000, 4100.0000,
      ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
      ARRAY[5100.0000, 1000.0000]) r;
    RAISE EXCEPTION 'a foreign household must not correct this payslip';
  EXCEPTION WHEN no_data_found THEN NULL;
  END;
  RAISE NOTICE 'PASS 0039 member and foreign-household refusal';
END $$;
ROLLBACK;

-- Lines that do not sum to the stated totals are refused when the deferred constraint is
-- checked, and nothing from that call is left committed.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0039_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM r.recorded_payslip_id FROM record_payslip(
      gen_random_uuid(), NULL, NULL, '2026-01-01', f.base, 5000.0000, 1000.0000, 4000.0000,
      ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
      ARRAY[4900.0000, 1000.0000]) r;
    RAISE EXCEPTION 'mismatched lines must have been refused';
  EXCEPTION WHEN check_violation THEN
    ASSERT SQLERRM LIKE '%sum to its stated totals%', 'the refusal names the totals mismatch';
  END;
  ASSERT (SELECT count(*) FROM payslip WHERE household_id = f.h1) = 0,
    'a refused payslip leaves no header';
  ASSERT (SELECT count(*) FROM payslip_line WHERE household_id = f.h1) = 0,
    'a refused payslip leaves no lines';
  RAISE NOTICE 'PASS 0039 totals mismatch refused with nothing stored';
END $$;
ROLLBACK;

-- A payslip's facts and a payslip line are immutable; only the deposit link may change.
BEGIN;
DO $$
DECLARE f RECORD; target_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0039_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SELECT recorded_payslip_id INTO target_id FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', f.base, 5000.0000, 1000.0000, 4000.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
    ARRAY[5000.0000, 1000.0000]) r;

  BEGIN
    UPDATE payslip SET gross_earnings = 6000.0000 WHERE id = target_id;
    RAISE EXCEPTION 'a fact update must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    DELETE FROM payslip WHERE id = target_id;
    RAISE EXCEPTION 'a payslip delete must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    UPDATE payslip_line SET amount = 1.0000 WHERE payslip_id = target_id;
    RAISE EXCEPTION 'a line update must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    DELETE FROM payslip_line WHERE payslip_id = target_id;
    RAISE EXCEPTION 'a line delete must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  RAISE NOTICE 'PASS 0039 payslip facts and lines are immutable';
END $$;
ROLLBACK;

-- Authenticated reads see only the household's own payslips and lines; anon sees none.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0039_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  PERFORM r.recorded_payslip_id FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', f.base, 5000.0000, 1000.0000, 4000.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Tax'],
    ARRAY[5000.0000, 1000.0000]) r;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  PERFORM r.recorded_payslip_id FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', f.base, 2000.0000, 0.0000, 2000.0000,
    ARRAY['earning']::payslip_line_kind[], ARRAY['Basic'], ARRAY[2000.0000]) r;

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip) = 1, 'owner sees only its own household payslip';
  ASSERT (SELECT count(*) FROM payslip_line) = 2, 'owner sees only its own household lines';
  RESET ROLE;

  -- current_household_id() resolves only an 'owner' row (V5): a member's app.user_id never
  -- names a household at all, so household_isolation excludes every row for a member exactly
  -- as it does for a stranger — this is the same single-owner boundary record_payslip's
  -- assert_is_owner() enforces on writes, now checked on reads.
  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip) = 0, 'a household member reads no payslip';
  ASSERT (SELECT count(*) FROM payslip_line) = 0, 'a household member reads no payslip line';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM payslip) = 0, 'anonymous payslip reads are empty';
  ASSERT (SELECT count(*) FROM payslip_line) = 0, 'anonymous line reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0039 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
