-- V53: the register — kind shapes, text and amount bounds, the account scope key, archived
-- preservation and household isolation. register_document's own link isolation is Task 2's
-- 0051 (it carries no rows until then); this file only checks the table exists with RLS enabled,
-- matching V53's own structural contract. All names, identifiers and amounts are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'register_record') = 1,
    'the register_record table must exist';
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'register_document') = 1,
    'the register_document table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'register_record'::regclass),
    'register_record must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'register_document'::regclass),
    'register_document must have row level security enabled';
  RAISE NOTICE 'PASS 0050 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0050_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    a1 UUID, a_fd UUID, a_foreign UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('register household one', base, (SELECT max(code) FROM currency)) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('register household two', base, (SELECT max(code) FROM currency)) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'register owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'register member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other register owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'register-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'register-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-register-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic register account', 'savings', base, 'XZ', 1000, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic register deposit', 'fixed_deposit', base, 'XZ', 5000, '2026-01-01')
  RETURNING id INTO a_fd;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic register account', 'savings', base, 'XZ', 1000, '2026-01-01')
  RETURNING id INTO a_foreign;
  RETURN NEXT;
END $$;

-- Kind shapes: one valid row per kind, and the forced-null/forced-not-null field each violates.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0050_fixture();

  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
  VALUES (f.h1, gen_random_uuid(), 'insurance_policy', 'Synthetic policy', '2027-01-01');
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date, account_id)
    VALUES (f.h1, gen_random_uuid(), 'insurance_policy', 'Synthetic policy', '2027-01-01', f.a1);
    RAISE EXCEPTION 'a policy with an account_id must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_kind_shape%', refusal;
  END;
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title)
    VALUES (f.h1, gen_random_uuid(), 'insurance_policy', 'Synthetic policy without a renewal');
    RAISE EXCEPTION 'a policy without a key_date must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_kind_shape%', refusal;
  END;

  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date, account_id)
  VALUES (f.h1, gen_random_uuid(), 'deposit', 'Synthetic deposit', '2027-01-01', f.a_fd);
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
    VALUES (f.h1, gen_random_uuid(), 'deposit', 'Synthetic deposit without an account', '2027-01-01');
    RAISE EXCEPTION 'a deposit without an account_id must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_kind_shape%', refusal;
  END;

  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date, counterparty,
                                amount, currency)
  VALUES (f.h1, gen_random_uuid(), 'claim', 'Synthetic claim', '2027-01-01',
          'Synthetic insurer', 1000, f.base);
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date, amount,
                                  currency)
    VALUES (f.h1, gen_random_uuid(), 'claim', 'Synthetic claim without a counterparty',
            '2027-01-01', 1000, f.base);
    RAISE EXCEPTION 'a claim without a counterparty must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_kind_shape%', refusal;
  END;

  INSERT INTO register_record (household_id, idempotency_key, kind, title, account_id,
                                nominee_recorded)
  VALUES (f.h1, gen_random_uuid(), 'nominee', 'Synthetic nominee', f.a1, true);
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, account_id)
    VALUES (f.h1, gen_random_uuid(), 'nominee', 'Synthetic nominee unset', f.a1);
    RAISE EXCEPTION 'a nominee without nominee_recorded must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_kind_shape%', refusal;
  END;

  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
  VALUES (f.h1, gen_random_uuid(), 'administration', 'Synthetic deadline', '2027-01-01');
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date, amount,
                                  currency)
    VALUES (f.h1, gen_random_uuid(), 'administration', 'Synthetic deadline with an amount',
            '2027-01-01', 100, f.base);
    RAISE EXCEPTION 'an administration record with an amount must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_kind_shape%', refusal;
  END;

  ASSERT (SELECT count(*) FROM register_record WHERE household_id = f.h1) = 5,
    'exactly the five valid rows must be accepted';

  RAISE NOTICE 'PASS 0050 kind shapes';
END $$;
ROLLBACK;

-- Text and amount bounds.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0050_fixture();

  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
    VALUES (f.h1, gen_random_uuid(), 'administration', repeat('x', 121), '2027-01-01');
    RAISE EXCEPTION 'a title over 120 characters must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_texts_are_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date,
                                  counterparty, amount, currency)
    VALUES (f.h1, gen_random_uuid(), 'claim', 'Synthetic claim', '2027-01-01', 'Insurer', -1,
            f.base);
    RAISE EXCEPTION 'a non-positive amount must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_amounts_are_valid%', refusal;
  END;
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date,
                                  counterparty, amount)
    VALUES (f.h1, gen_random_uuid(), 'claim', 'Synthetic claim', '2027-01-01', 'Insurer', 100);
    RAISE EXCEPTION 'an amount without a currency must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_amounts_are_valid%', refusal;
  END;
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date,
                                  premium_amount, currency)
    VALUES (f.h1, gen_random_uuid(), 'insurance_policy', 'Synthetic policy', '2027-01-01', 100,
            f.base);
    RAISE EXCEPTION 'a premium without a cadence must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_amounts_are_valid%', refusal;
  END;
  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date,
                                  premium_amount, premium_cadence, currency)
    VALUES (f.h1, gen_random_uuid(), 'insurance_policy', 'Synthetic policy', '2027-01-01', 100,
            'once', f.base);
    RAISE EXCEPTION 'a once-off premium cadence must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register_amounts_are_valid%', refusal;
  END;

  RAISE NOTICE 'PASS 0050 text and amount bounds';
END $$;
ROLLBACK;

-- The account scope key: a foreign household's account is refused.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0050_fixture();

  BEGIN
    INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date, account_id)
    VALUES (f.h1, gen_random_uuid(), 'deposit', 'Synthetic deposit', '2027-01-01', f.a_foreign);
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_register_account_scope%', refusal;
  END;

  RAISE NOTICE 'PASS 0050 account scope key';
END $$;
ROLLBACK;

-- Archiving is terminal: an archived row refuses every UPDATE and DELETE.
BEGIN;
DO $$
DECLARE f RECORD; rid UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0050_fixture();

  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
  VALUES (f.h1, gen_random_uuid(), 'administration', 'Synthetic deadline', '2027-01-01')
  RETURNING id INTO rid;
  UPDATE register_record SET archived_at = now() WHERE household_id = f.h1 AND id = rid;

  BEGIN
    UPDATE register_record SET title = 'Renamed after archiving'
     WHERE household_id = f.h1 AND id = rid;
    RAISE EXCEPTION 'an update to an archived record must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%archived register record is immutable%', refusal;
  END;
  BEGIN
    DELETE FROM register_record WHERE household_id = f.h1 AND id = rid;
    RAISE EXCEPTION 'a delete must always be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%register record cannot be deleted%', refusal;
  END;

  RAISE NOTICE 'PASS 0050 archived preservation';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS on register_record.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0050_fixture();
  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
  VALUES (f.h1, gen_random_uuid(), 'administration', 'Synthetic deadline', '2027-01-01');
  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
  VALUES (f.h2, gen_random_uuid(), 'administration', 'Other synthetic deadline', '2027-01-01');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM register_record) = 1, 'owner sees only its own records';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM register_record) = 0, 'a household member reads no record';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM register_record) = 1, 'the other household sees only its own row';
  ASSERT (SELECT count(*) FROM register_record WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s records';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM register_record) = 0, 'anonymous register reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0050 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
