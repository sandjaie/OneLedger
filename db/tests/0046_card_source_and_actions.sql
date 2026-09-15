-- V49: a commitment may be planned from a card statement's due amount (source_card_statement_id,
-- one open plan per statement), and the two plain owner-scoped tables `routes/actions.py` reads
-- and writes to gather the Needs attention list: `action_item` (owner-entered follow-ups) and
-- `action_mark` (a snooze or, on a card key only, a dismissal). All names, identifiers and
-- amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'action_item') = 1,
    'the action_item table must exist';
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'action_mark') = 1,
    'the action_mark table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'action_item'::regclass),
    'action_item must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'action_mark'::regclass),
    'action_mark must have row level security enabled';
  RAISE NOTICE 'PASS 0046 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0046_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    a1 UUID, cc1 UUID, a2 UUID, cc2 UUID, cs1 UUID, cs_foreign UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('actions household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('actions household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'actions owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'actions member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other actions owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'actions-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'actions-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-actions-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic actions paying account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date, credit_limit)
  VALUES (h1, p1, 'Synthetic actions card', 'credit_card', base, 'XZ', 0, '2026-01-01', 5000)
  RETURNING id INTO cc1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic actions account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date, credit_limit)
  VALUES (h2, p2, 'Other synthetic actions card', 'credit_card', base, 'XZ', 0, '2026-01-01', 5000)
  RETURNING id INTO cc2;
  INSERT INTO card_statement (household_id, account_id, period_start, period_end, statement_date,
                               payment_due_date, currency, opening_balance, closing_balance,
                               statement_balance)
  VALUES (h1, cc1, '2026-01-01', '2026-01-31', '2026-02-01', '2026-02-20', base, 0, -400, 400)
  RETURNING id INTO cs1;
  INSERT INTO card_statement (household_id, account_id, period_start, period_end, statement_date,
                               payment_due_date, currency, opening_balance, closing_balance,
                               statement_balance)
  VALUES (h2, cc2, '2026-01-01', '2026-01-31', '2026-02-01', '2026-02-20', base, 0, 0, 0)
  RETURNING id INTO cs_foreign;
  RETURN NEXT;
END $$;

-- The card-source check, the cross-household statement reference, and the one-open-plan index
-- (an archived plan frees the statement for a new one).
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; c1 UUID; c2 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0046_fixture();

  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_card_statement_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic card payment', -400.0000, f.base, 'monthly',
            '2026-02-20', f.cs1);
    RAISE EXCEPTION 'a recurring card-sourced commitment must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%commitment_card_source_is_one_payment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_card_statement_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic positive card payment', 400.0000, f.base,
            'once', '2026-02-20', f.cs1);
    RAISE EXCEPTION 'a positive card-sourced commitment amount must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%commitment_card_source_is_one_payment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_card_statement_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic foreign card source', -400.0000, f.base,
            'once', '2026-02-20', f.cs_foreign);
    RAISE EXCEPTION 'a cross-household card statement reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_commitment_card_statement_scope%', refusal;
  END;

  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, source_card_statement_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic card payment plan', -400.0000, f.base, 'once',
          '2026-02-20', f.cs1)
  RETURNING id INTO c1;
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_card_statement_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic second plan for the same statement',
            -400.0000, f.base, 'once', '2026-02-20', f.cs1);
    RAISE EXCEPTION 'a second open plan for the same statement must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_commitment_open_card_statement%', refusal;
  END;

  UPDATE commitment SET archived_at = now() WHERE id = c1;
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, source_card_statement_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic replan after archiving', -400.0000, f.base,
          'once', '2026-02-20', f.cs1)
  RETURNING id INTO c2;
  ASSERT c2 IS NOT NULL, 'an archived plan must free its statement for a new one';

  RAISE NOTICE 'PASS 0046 card-source check and one-open-plan index';
END $$;
ROLLBACK;

-- action_item shape: title bounds, resolution/dismissal agreement, idempotency, and the
-- cross-household account reference.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; item_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0046_fixture();

  BEGIN
    INSERT INTO action_item (household_id, idempotency_key, title) VALUES (f.h1, gen_random_uuid(), '');
    RAISE EXCEPTION 'an empty title must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_item_title_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO action_item (household_id, idempotency_key, title, state, resolved_at)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic follow-up', 'completed', NULL);
    RAISE EXCEPTION 'a completed follow-up without resolved_at must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_item_resolution_agrees%', refusal;
  END;
  BEGIN
    INSERT INTO action_item (household_id, idempotency_key, title, state, dismiss_reason, resolved_at)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic follow-up', 'dismissed', NULL, now());
    RAISE EXCEPTION 'a dismissal without a reason must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_item_dismissal_has_reason%', refusal;
  END;
  BEGIN
    INSERT INTO action_item (household_id, idempotency_key, title, account_id)
    VALUES (f.h1, gen_random_uuid(), 'Synthetic follow-up', f.a2);
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_action_item_account_scope%', refusal;
  END;

  INSERT INTO action_item (household_id, idempotency_key, title, account_id)
  VALUES (f.h1, gen_random_uuid(), 'Synthetic follow-up', f.a1) RETURNING id INTO item_id;
  ASSERT item_id IS NOT NULL, 'a valid follow-up must be accepted';

  RAISE NOTICE 'PASS 0046 action_item shape';
END $$;
ROLLBACK;

-- action_mark shape: the key pattern, exactly one of a snooze or a dismissal, dismissal reserved
-- for a card key, and the reason bound.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0046_fixture();

  BEGIN
    INSERT INTO action_mark (household_id, action_key, snoozed_until)
    VALUES (f.h1, 'bogus-key', '2026-03-01');
    RAISE EXCEPTION 'a malformed action key must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_mark_key_shape%', refusal;
  END;
  BEGIN
    INSERT INTO action_mark (household_id, action_key, snoozed_until, dismiss_reason)
    VALUES (f.h1, 'card.' || f.cs1::text, '2026-03-01', 'both set');
    RAISE EXCEPTION 'both a snooze and a dismissal on one mark must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_mark_is_one_mark%', refusal;
  END;
  BEGIN
    INSERT INTO action_mark (household_id, action_key, dismiss_reason)
    VALUES (f.h1, 'follow-up.' || f.a1::text, 'not a card');
    RAISE EXCEPTION 'a dismissal on a non-card key must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_mark_dismissal_is_card%', refusal;
  END;
  BEGIN
    INSERT INTO action_mark (household_id, action_key, dismiss_reason)
    VALUES (f.h1, 'card.' || f.cs1::text, repeat('x', 201));
    RAISE EXCEPTION 'a reason over 200 characters must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%action_mark_reason_is_bounded%', refusal;
  END;

  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'follow-up.' || f.a1::text, '2026-03-01');
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'commitment.' || f.a1::text || '.2026-03-01', '2026-03-05');
  INSERT INTO action_mark (household_id, action_key, dismiss_reason)
  VALUES (f.h1, 'card.' || f.cs1::text, 'already paid another way');
  ASSERT (SELECT count(*) FROM action_mark WHERE household_id = f.h1) = 3,
    'a valid mark for each of the three key shapes must be accepted';

  RAISE NOTICE 'PASS 0046 action_mark shape';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS on both new tables.
BEGIN;
DO $$
DECLARE f RECORD; item1 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0046_fixture();
  INSERT INTO action_item (household_id, idempotency_key, title)
  VALUES (f.h1, gen_random_uuid(), 'Synthetic isolation follow-up') RETURNING id INTO item1;
  INSERT INTO action_item (household_id, idempotency_key, title)
  VALUES (f.h2, gen_random_uuid(), 'Other synthetic isolation follow-up');
  INSERT INTO action_mark (household_id, action_key, snoozed_until)
  VALUES (f.h1, 'follow-up.' || item1::text, '2026-03-01');
  INSERT INTO action_mark (household_id, action_key, dismiss_reason)
  VALUES (f.h2, 'card.' || f.cs_foreign::text, 'other household reason');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM action_item) = 1, 'owner sees only its own household follow-up';
  ASSERT (SELECT count(*) FROM action_mark) = 1, 'owner sees only its own household mark';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM action_item) = 0, 'a household member reads no follow-up';
  ASSERT (SELECT count(*) FROM action_mark) = 0, 'a household member reads no mark';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM action_item) = 1, 'the other household sees only its own follow-up';
  ASSERT (SELECT count(*) FROM action_item WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s follow-up';
  ASSERT (SELECT count(*) FROM action_mark) = 1, 'the other household sees only its own mark';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM action_item) = 0, 'anonymous follow-up reads are empty';
  ASSERT (SELECT count(*) FROM action_mark) = 0, 'anonymous mark reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0046 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
