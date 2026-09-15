-- V51: goals and resource reservations. A reservation earmarks an amount of an asset account, or
-- a whole account, for a goal; it is immutable once inserted except a single release
-- (`preserve_resource_reservation`), and a goal is editable in place until it closes
-- (`preserve_closed_goal`). Both tables are plain owner-scoped tables with no SECURITY DEFINER
-- write path, the same convention as `commitment` (V43) and `action_item`/`action_mark` (V49).
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'goal') = 1,
    'the goal table must exist';
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'resource_reservation') = 1,
    'the resource_reservation table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'goal'::regclass),
    'goal must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'resource_reservation'::regclass),
    'resource_reservation must have row level security enabled';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid = 'preserve_resource_reservation()'::regprocedure) = 1,
    'the reservation immutability guard function must exist';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid = 'preserve_closed_goal()'::regprocedure) = 1,
    'the closed-goal guard function must exist';
  RAISE NOTICE 'PASS 0048 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0048_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    a1 UUID, a2 UUID, a_foreign UUID,
    g1 UUID, g_foreign UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('goals household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('goals household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'goals owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'goals member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other goals owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'goals-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'goals-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-goals-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic goals account', 'savings', base, 'XZ', 10000, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic second goals account', 'savings', base, 'XZ', 5000, '2026-01-01')
  RETURNING id INTO a2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic goals account', 'savings', base, 'XZ', 10000, '2026-01-01')
  RETURNING id INTO a_foreign;
  INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                     target_date, priority)
  VALUES (h1, gen_random_uuid(), 'Synthetic goal', 6000.0000, base, '2027-01-01', 1)
  RETURNING id INTO g1;
  INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                     target_date, priority)
  VALUES (h2, gen_random_uuid(), 'Other synthetic goal', 6000.0000, base, '2027-01-01', 1)
  RETURNING id INTO g_foreign;
  RETURN NEXT;
END $$;

-- goal shape: name, target, priority, planned monthly, closure agreement, idempotency.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; gid UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0048_fixture();

  BEGIN
    INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                       target_date, priority)
    VALUES (f.h1, gen_random_uuid(), '', 1000.0000, f.base, '2027-01-01', 1);
    RAISE EXCEPTION 'an empty name must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%goal_name_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                       target_date, priority)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic zero goal', 0, f.base, '2027-01-01', 1);
    RAISE EXCEPTION 'a zero target must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%goal_target_is_positive%', refusal;
  END;
  BEGIN
    INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                       target_date, priority)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic bad priority', 1000.0000, f.base, '2027-01-01', 6);
    RAISE EXCEPTION 'a priority outside 1-5 must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%goal_priority_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                       target_date, priority, planned_monthly)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic negative planned', 1000.0000, f.base,
            '2027-01-01', 1, -1);
    RAISE EXCEPTION 'a negative planned monthly must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%goal_planned_is_nonnegative%', refusal;
  END;
  BEGIN
    INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                       target_date, priority, state, closed_at)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic bad closure', 1000.0000, f.base, '2027-01-01', 1,
            'active', now());
    RAISE EXCEPTION 'an active goal with closed_at set must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%goal_closure_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                       target_date, priority, state)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic unclosed achieved', 1000.0000, f.base,
            '2027-01-01', 1, 'achieved');
    RAISE EXCEPTION 'an achieved goal without closed_at must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%goal_closure_agrees%', refusal;
  END;

  INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                     target_date, priority)
  VALUES (f.h1, gen_random_uuid(), 'Synthetic valid goal', 1000.0000, f.base, '2027-01-01', 1)
  RETURNING id INTO gid;
  ASSERT gid IS NOT NULL, 'a valid goal must be accepted';

  RAISE NOTICE 'PASS 0048 goal shape';
END $$;
ROLLBACK;

-- resource_reservation shape: purpose agreement, positive amount and rate, release agreement,
-- reason bounds, and the cross-household goal/account scoping foreign keys.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; rid UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0048_fixture();

  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'goal', NULL, f.a1, 'amount', 1000.0000, f.base, current_date,
            f.base, 1);
    RAISE EXCEPTION 'a goal reservation without a goal_id must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation_purpose_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'emergency', f.g1, f.a1, 'amount', 1000.0000, f.base,
            current_date, f.base, 1);
    RAISE EXCEPTION 'an emergency reservation with a goal_id must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation_purpose_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 0, f.base, current_date,
            f.base, 1);
    RAISE EXCEPTION 'a zero native amount must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation_amount_is_positive%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 1000.0000, f.base, current_date,
            f.base, 0);
    RAISE EXCEPTION 'a zero rate must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation_rate_is_positive%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation, release_reason)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 1000.0000, f.base, current_date,
            f.base, 1, 'no release yet');
    RAISE EXCEPTION 'a release reason without released_at must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation_release_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation, released_at, release_reason)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 1000.0000, f.base, current_date,
            f.base, 1, now(), repeat('x', 201));
    RAISE EXCEPTION 'a release reason over 200 characters must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation_reason_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g_foreign, f.a1, 'amount', 1000.0000, f.base,
            current_date, f.base, 1);
    RAISE EXCEPTION 'a cross-household goal reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_reservation_goal_scope%', refusal;
  END;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a_foreign, 'amount', 1000.0000, f.base,
            current_date, f.base, 1);
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_reservation_account_scope%', refusal;
  END;

  INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                     kind, native_amount, currency, reserved_on, rate_currency,
                                     rate_at_reservation)
  VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 1000.0000, f.base, current_date,
          f.base, 1)
  RETURNING id INTO rid;
  ASSERT rid IS NOT NULL, 'a valid reservation must be accepted';

  RAISE NOTICE 'PASS 0048 resource_reservation shape';
END $$;
ROLLBACK;

-- Exactly one active whole-account reservation per account; an amount reservation is a separate
-- row the index does not see (the app, not the database, enforces full exclusivity).
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; r1 UUID; r2 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0048_fixture();

  INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                     kind, native_amount, currency, reserved_on, rate_currency,
                                     rate_at_reservation)
  VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'whole_account', 10000.0000, f.base,
          current_date, f.base, 1)
  RETURNING id INTO r1;
  BEGIN
    INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                       kind, native_amount, currency, reserved_on, rate_currency,
                                       rate_at_reservation)
    VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'whole_account', 10000.0000, f.base,
            current_date, f.base, 1);
    RAISE EXCEPTION 'a second active whole-account reservation on the same account must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_reservation_whole_account%', refusal;
  END;

  UPDATE resource_reservation SET released_at = now() WHERE id = r1;
  INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                     kind, native_amount, currency, reserved_on, rate_currency,
                                     rate_at_reservation)
  VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'whole_account', 10000.0000, f.base,
          current_date, f.base, 1)
  RETURNING id INTO r2;
  ASSERT r2 IS NOT NULL, 'releasing the first reservation must free the account for a new one';

  RAISE NOTICE 'PASS 0048 whole-account uniqueness';
END $$;
ROLLBACK;

-- preserve_resource_reservation: every UPDATE refused except one release; DELETE always refused.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; rid UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0048_fixture();
  INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                     kind, native_amount, currency, reserved_on, rate_currency,
                                     rate_at_reservation)
  VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 1000.0000, f.base, current_date,
          f.base, 1)
  RETURNING id INTO rid;

  BEGIN
    UPDATE resource_reservation SET native_amount = 2000.0000 WHERE id = rid;
    RAISE EXCEPTION 'changing a field other than release must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation can only be released once%', refusal;
  END;
  BEGIN
    DELETE FROM resource_reservation WHERE id = rid;
    RAISE EXCEPTION 'a delete must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%reservation cannot be deleted%', refusal;
  END;

  UPDATE resource_reservation SET released_at = now(), release_reason = 'no longer needed'
   WHERE id = rid;
  ASSERT (SELECT released_at FROM resource_reservation WHERE id = rid) IS NOT NULL,
    'the one release must succeed';

  BEGIN
    UPDATE resource_reservation SET release_reason = 'changed my mind' WHERE id = rid;
    RAISE EXCEPTION 'a released reservation must be immutable';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%released reservation is immutable%', refusal;
  END;
  BEGIN
    UPDATE resource_reservation SET released_at = NULL WHERE id = rid;
    RAISE EXCEPTION 'un-releasing a reservation must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%released reservation is immutable%', refusal;
  END;

  RAISE NOTICE 'PASS 0048 preserve_resource_reservation';
END $$;
ROLLBACK;

-- preserve_closed_goal: an active goal is edited in place; a closed goal refuses every update.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; gid UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0048_fixture();
  INSERT INTO goal (household_id, idempotency_key, name, target_amount, target_currency,
                     target_date, priority)
  VALUES (f.h1, gen_random_uuid(), 'Synthetic closeable goal', 1000.0000, f.base, '2027-01-01', 1)
  RETURNING id INTO gid;

  UPDATE goal SET name = 'Synthetic renamed goal' WHERE id = gid;
  ASSERT (SELECT name FROM goal WHERE id = gid) = 'Synthetic renamed goal',
    'an active goal must be editable in place';

  UPDATE goal SET state = 'achieved', closed_at = now() WHERE id = gid;
  BEGIN
    UPDATE goal SET name = 'Synthetic renamed after close' WHERE id = gid;
    RAISE EXCEPTION 'a closed goal must refuse every further update';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%closed goal is immutable%', refusal;
  END;

  RAISE NOTICE 'PASS 0048 preserve_closed_goal';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS on both new tables.
BEGIN;
DO $$
DECLARE f RECORD; rid UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0048_fixture();
  INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                     kind, native_amount, currency, reserved_on, rate_currency,
                                     rate_at_reservation)
  VALUES (f.h1, gen_random_uuid(), 'goal', f.g1, f.a1, 'amount', 1000.0000, f.base, current_date,
          f.base, 1)
  RETURNING id INTO rid;
  INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id, account_id,
                                     kind, native_amount, currency, reserved_on, rate_currency,
                                     rate_at_reservation)
  VALUES (f.h2, gen_random_uuid(), 'goal', f.g_foreign, f.a_foreign, 'amount', 1000.0000, f.base,
          current_date, f.base, 1);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM goal) = 1, 'owner sees only its own household goal';
  ASSERT (SELECT count(*) FROM resource_reservation) = 1,
    'owner sees only its own household reservation';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM goal) = 0, 'a household member reads no goal';
  ASSERT (SELECT count(*) FROM resource_reservation) = 0, 'a household member reads no reservation';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM goal) = 1, 'the other household sees only its own goal';
  ASSERT (SELECT count(*) FROM goal WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s goal';
  ASSERT (SELECT count(*) FROM resource_reservation) = 1,
    'the other household sees only its own reservation';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM goal) = 0, 'anonymous goal reads are empty';
  ASSERT (SELECT count(*) FROM resource_reservation) = 0, 'anonymous reservation reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0048 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
