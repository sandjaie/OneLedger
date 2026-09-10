-- db/tests/0019_card_statements.sql
-- V21: `card_statement` per schema.md `0011` — one cycle per card per period end, only on a
-- credit card of the same household and in its currency, household-isolated, with the foreign
-- key schema.md `0018` reserved on `transaction.card_statement_id`.
--
-- The sign block is the one ADR-0010 asks for per card surface: a card the owner owes on stores
-- a NEGATIVE closing balance beside a POSITIVE amount due, and the two cannot be swapped without
-- the table refusing it. A sign error is internally consistent and therefore invisible; this is
-- where it is made visible. Every figure is invented.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT to_regclass('public.card_statement') IS NOT NULL, 'card_statement must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'card_statement'),
    'RLS must be enabled on card_statement';
  ASSERT (SELECT indisunique FROM pg_index
           WHERE indexrelid = 'idx_card_stmt_period'::regclass),
    'idx_card_stmt_period must be unique: one cycle per card per period end';
  ASSERT EXISTS (SELECT FROM pg_constraint
                  WHERE conname = 'fk_txn_card_stmt' AND conrelid = 'transaction'::regclass),
    'transaction.card_statement_id must gain the foreign key schema.md 0018 reserves';
  RAISE NOTICE 'PASS 0019 structure';
END $$;

-- ---------------------------------------------------------------------------------------
-- The fixture: one household, a card, a savings account. Invented throughout.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION pg_temp.t0019_fixture()
  RETURNS TABLE (h UUID, p UUID, card UUID, savings UUID, base CHAR(3), other CHAR(3))
  LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO other FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('card cycles', base, other) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'self', true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date, credit_limit)
  VALUES (h, p, 'Cycle card', 'credit_card', base, 'XB', 0, '2026-01-01', 20000.0000)
  RETURNING id INTO card;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h, p, 'Cycle savings', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO savings;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0019_cycle(
  p_household UUID, p_account UUID, p_currency CHAR(3), p_end DATE,
  p_closing NUMERIC, p_due NUMERIC,
  p_statement DATE DEFAULT NULL, p_due_date DATE DEFAULT NULL, p_start DATE DEFAULT NULL)
  RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE v_id UUID;
BEGIN
  INSERT INTO card_statement (household_id, account_id, period_start, period_end, statement_date,
                              payment_due_date, currency, opening_balance, closing_balance,
                              statement_balance)
  VALUES (p_household, p_account, COALESCE(p_start, p_end - 30), p_end,
          COALESCE(p_statement, p_end), COALESCE(p_due_date, p_end + 20),
          p_currency, 0, p_closing, p_due)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- ---------------------------------------------------------------------------------------
-- ADR-0010, per card surface: owed is negative, due is positive, and the swap is refused.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; c UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0019_fixture();

  c := pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-28', -4500.0000, 4500.0000);
  ASSERT (SELECT closing_balance FROM card_statement WHERE id = c) = -4500.0000,
    'a card the owner owes on stores a negative closing balance';
  ASSERT (SELECT statement_balance FROM card_statement WHERE id = c) = 4500.0000,
    'and a positive amount due beside it';

  -- In credit: the owner overpaid, so the balance is theirs and nothing is due.
  PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-03-31', 120.0000, 0);

  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-04-30', 4500.0000, 4500.0000);
    RAISE EXCEPTION 'the amount due stored as a positive closing balance must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%owed_is_negative%', 'expected owed_is_negative, got ' || refusal;
  END;

  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-05-31', -4500.0000, -4500.0000);
    RAISE EXCEPTION 'an amount due is never negative';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%statement_balance_is_what_was_due%',
      'expected statement_balance_is_what_was_due, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0019 owed is negative, due is positive, and the two are not confused';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- One cycle per card per period end.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; second_card UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0019_fixture();
  PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-28', -100.0000, 100.0000);
  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-28', -200.0000, 200.0000);
    RAISE EXCEPTION 'a second cycle with the same period end must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%idx_card_stmt_period%', 'expected idx_card_stmt_period, got ' || refusal;
  END;

  -- The same period end on a different card is a different cycle.
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date, credit_limit)
  VALUES (f.h, f.p, 'Second card', 'credit_card', f.base, 'XB', 0, '2026-01-01', 5000.0000)
  RETURNING id INTO second_card;
  PERFORM pg_temp.t0019_cycle(f.h, second_card, f.base, '2026-02-28', -50.0000, 50.0000);
  RAISE NOTICE 'PASS 0019 one cycle per card per period end';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The two CHECKs schema.md states, each refusing its inverted case.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0019_fixture();
  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-01', -1.0000, 1.0000,
                                p_start => '2026-02-28');
    RAISE EXCEPTION 'a period ending before it starts must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%period_valid%', 'expected period_valid, got ' || refusal;
  END;
  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-28', -1.0000, 1.0000,
                                p_statement => '2026-03-02', p_due_date => '2026-03-01');
    RAISE EXCEPTION 'a payment due before the statement was issued must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%due_after_statement%', 'expected due_after_statement, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0019 period_valid and due_after_statement refuse their inverted cases';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- A cycle only on a credit card, of this household, in its currency. The foreign key says
-- the account exists and nothing more.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; theirs UUID; their_person UUID; their_card UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0019_fixture();

  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.savings, f.base, '2026-02-28', -1.0000, 1.0000);
    RAISE EXCEPTION 'a cycle on a savings account must be refused';
  EXCEPTION WHEN raise_exception THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%belongs on a credit_card account%', 'got ' || refusal;
  END;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('card cycles other', f.base, f.other) RETURNING id INTO theirs;
  INSERT INTO person (household_id, display_name, is_self) VALUES (theirs, 'them', true)
  RETURNING id INTO their_person;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date, credit_limit)
  VALUES (theirs, their_person, 'Their card', 'credit_card', f.base, 'XB', 0, '2026-01-01', 1.0000)
  RETURNING id INTO their_card;
  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, their_card, f.base, '2026-02-28', -1.0000, 1.0000);
    RAISE EXCEPTION 'a cycle on another household''s card must be refused';
  EXCEPTION WHEN raise_exception THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%does not own account%', 'got ' || refusal;
  END;

  BEGIN
    PERFORM pg_temp.t0019_cycle(f.h, f.card, f.other, '2026-02-28', -1.0000, 1.0000);
    RAISE EXCEPTION 'a cycle in a currency other than the card''s must be refused';
  EXCEPTION WHEN raise_exception THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%must match the account%', 'got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0019 a cycle belongs on a card of this household, in its currency';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The foreign key schema.md 0018 reserved: a transaction names a cycle that exists.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; c UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0019_fixture();
  c := pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-28', -310.0000, 310.0000);

  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash,
                           card_statement_id)
  VALUES (f.h, f.card, '2026-02-10', 'billed in this cycle', -310.0000, f.base,
          '2026-02-10', 1, 1, -310.0000, -310.0000, 'expense', 'manual',
          encode(gen_random_bytes(32), 'hex'), c);

  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             card_statement_id)
    VALUES (f.h, f.card, '2026-02-11', 'billed in no cycle', -10.0000, f.base,
            '2026-02-11', 1, 1, -10.0000, -10.0000, 'expense', 'manual',
            encode(gen_random_bytes(32), 'hex'), gen_random_uuid());
    RAISE EXCEPTION 'a transaction naming a cycle that does not exist must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_txn_card_stmt%', 'expected fk_txn_card_stmt, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0019 a transaction names a cycle that exists';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Isolation, as 0017 proves it: bound as one household's owner under the restricted role,
-- another household's cycles are not there.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; theirs UUID; their_person UUID; their_card UUID; u UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0019_fixture();
  PERFORM pg_temp.t0019_cycle(f.h, f.card, f.base, '2026-02-28', -1.0000, 1.0000);

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('card cycles theirs', f.base, f.other) RETURNING id INTO theirs;
  INSERT INTO person (household_id, display_name, is_self) VALUES (theirs, 'them', true)
  RETURNING id INTO their_person;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date, credit_limit)
  VALUES (theirs, their_person, 'Their card', 'credit_card', f.base, 'XB', 0, '2026-01-01', 1.0000)
  RETURNING id INTO their_card;
  PERFORM pg_temp.t0019_cycle(theirs, their_card, f.base, '2026-02-28', -2.0000, 2.0000);

  u := gen_random_uuid();
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (u, f.h, f.p, 'cycles-' || u || '@example.invalid', 'owner');
  PERFORM set_config('app.user_id', u::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM card_statement) = 1, 'a household reads only its own cycles';
  ASSERT (SELECT household_id FROM card_statement) = f.h, 'and it reads the right one';
  RESET ROLE;
  RAISE NOTICE 'PASS 0019 a household reads only its own cycles';
END $$;
ROLLBACK;
