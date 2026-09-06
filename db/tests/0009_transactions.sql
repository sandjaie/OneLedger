-- db/tests/0009_transactions.sql
-- The transaction ledger: immutable originals (I1), frozen translations (I5), provenance (I6),
-- labelled derivations (I7 columns), and the split-safe reporting surface — a split parent is a
-- container, not a posting, so spending is never double-counted. Fixtures use whatever
-- currencies this deployment seeded and the ISO user-assigned country range (ADR-0012).
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'transaction'),
    'RLS must be enabled on transaction';
  ASSERT (SELECT 'security_invoker=true' = ANY(reloptions)
            FROM pg_class WHERE relname = 'v_transaction_posting'),
    'v_transaction_posting must run as the invoker, or the view owner bypasses RLS';
  ASSERT (SELECT 'security_invoker=true' = ANY(reloptions)
            FROM pg_class WHERE relname = 'v_transaction_reportable'),
    'v_transaction_reportable must run as the invoker, or the view owner bypasses RLS';
  RAISE NOTICE 'PASS 0009 structure';
END $$;

DO $$
DECLARE h UUID; p UUID; a_sav UUID; a_card UUID;
        base CHAR(3); secondary CHAR(3);
        t_parent UUID; t_child UUID; t_lone UUID;
BEGIN
  SET CONSTRAINTS ALL IMMEDIATE;  -- surface the deferred split-sum trigger per statement

  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('txn test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Ledger Savings','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a_sav;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date, credit_limit)
    VALUES (h,p,'Ledger Card','credit_card',base,'XB',0,'2024-01-01',20000) RETURNING id INTO a_card;

  -- I2: signed rows sum with no CASE, both classes, exact to 4 dp.
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h, a_sav, '2024-02-01', 'SALARY CREDIT', 100000.0000, base,
            '2024-02-01', 1, 20, 100000.0000, 2000000.0000, 'income', 'manual', 'd1');
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h, a_card, '2024-02-02', 'COFFEE', -310.5000, base,
            '2024-02-02', 1, 20, -310.5000, -6210.0000, 'expense', 'manual', 'd2')
    RETURNING id INTO t_lone;
  ASSERT (SELECT sum(amount) FROM transaction WHERE household_id = h) = 99689.5000,
    'signed rows sum exactly, asset and liability alike';

  -- I7: the carried-forward label survives as stored.
  ASSERT (SELECT count(*) FROM transaction WHERE household_id = h AND fx_rate_is_carried) = 0,
    'nothing so far claims a carried rate';

  -- I1/I5: originals and frozen translations are immutable.
  BEGIN
    UPDATE transaction SET amount = -300.0000 WHERE id = t_lone;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN RAISE EXCEPTION 'amount must be immutable (I1)'; END IF;
  END;
  BEGIN
    UPDATE transaction SET currency = secondary WHERE id = t_lone;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN RAISE EXCEPTION 'currency must be immutable (I1)'; END IF;
  END;
  BEGIN
    UPDATE transaction SET amount_base = -1.0000 WHERE id = t_lone;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN RAISE EXCEPTION 'frozen amounts are never recomputed (I5)'; END IF;
  END;
  -- A category edit is not a money edit and stays allowed.
  UPDATE transaction SET category_id =
    (SELECT id FROM category WHERE household_id IS NULL AND parent_id IS NULL LIMIT 1),
    classified_by = 'user_manual'
   WHERE id = t_lone;

  -- Frozen amounts are required at insert.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_sav, '2024-02-03', 'NO FROZEN BASE', -5.0000, base,
              '2024-02-03', 1, 20, -100.0000, 'expense', 'manual', 'd3');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN not_null_violation THEN NULL;
  END;

  -- I6: an imported row must carry its document.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_sav, '2024-02-03', 'IMPORT NO DOC', -5.0000, base,
              '2024-02-03', 1, 20, -5.0000, -100.0000, 'expense', 'statement_import', 'd4');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- Dedupe is unique per household and account.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_card, '2024-02-02', 'COFFEE AGAIN', -310.5000, base,
              '2024-02-02', 1, 20, -310.5000, -6210.0000, 'expense', 'manual', 'd2');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;

  -- The reportable view excludes what is not consumption: transfer legs, excluded rows,
  -- balance-sheet movements.
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash,
                           transfer_id)
    VALUES (h, a_sav, '2024-02-04', 'TT REMITTANCE', -5000.0000, base,
            '2024-02-04', 1, 20, -5000.0000, -100000.0000, 'transfer_out', 'manual', 'd5',
            gen_random_uuid());
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash,
                           is_excluded)
    VALUES (h, a_sav, '2024-02-05', 'EXCLUDED ROW', -7.0000, base,
            '2024-02-05', 1, 20, -7.0000, -140.0000, 'expense', 'manual', 'd6', true);
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h, a_sav, '2024-01-01', 'OPENING', 0.0000, base,
            '2024-01-01', 1, 20, 0.0000, 0.0000, 'adjustment', 'manual', 'd7');
  ASSERT NOT EXISTS (
    SELECT FROM v_transaction_reportable
     WHERE household_id = h AND dedupe_hash IN ('d5','d6','d7')),
    'transfer legs, excluded rows and adjustments never report (I3 groundwork)';
  ASSERT EXISTS (SELECT FROM v_transaction_posting WHERE household_id = h AND dedupe_hash = 'd5'),
    'a transfer leg is still a posting — the ledger shows it, reports do not';

  -- Splits: the parent is a container. Children land in one statement so the deferred
  -- sum check sees the complete split.
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h, a_card, '2024-02-06', 'SUPERMARKET', -8000.0000, base,
            '2024-02-06', 1, 20, -8000.0000, -160000.0000, 'expense', 'manual', 'd8')
    RETURNING id INTO t_parent;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash,
                           parent_transaction_id)
    VALUES (h, a_card, '2024-02-06', 'SUPERMARKET', -6500.0000, base,
            '2024-02-06', 1, 20, -6500.0000, -130000.0000, 'expense', 'manual', 'd8c1', t_parent),
           (h, a_card, '2024-02-06', 'SUPERMARKET', -1500.0000, base,
            '2024-02-06', 1, 20, -1500.0000, -30000.0000, 'expense', 'manual', 'd8c2', t_parent);
  SELECT id INTO t_child FROM transaction WHERE dedupe_hash = 'd8c1';
  ASSERT NOT EXISTS (SELECT FROM v_transaction_posting WHERE id = t_parent),
    'a row with children is a container, not a posting';
  ASSERT (SELECT count(*) FROM v_transaction_reportable
           WHERE household_id = h AND parent_transaction_id = t_parent) = 2,
    'the children carry the reporting';
  ASSERT (SELECT sum(amount) FROM v_transaction_reportable
           WHERE household_id = h AND account_id = a_card) = -8310.5000,
    'a split changes categories, never the reported total';

  -- The materialised balance equation holds to 4 dp on both classes, split-neutrally:
  -- savings 0 + 100000 - 5000 - 7 + 0; card 0 - 310.5 - 8000 (children move nothing).
  ASSERT (SELECT current_balance FROM account WHERE id = a_sav) = 94993.0000,
    'the asset balance is opening + the sum of its posting rows';
  ASSERT (SELECT current_balance FROM account WHERE id = a_card) = -8310.5000,
    'the liability balance is opening + the sum of its posting rows, and a split moves nothing';
  ASSERT (SELECT current_balance_as_of FROM account WHERE id = a_sav)
           = '2024-02-05'::timestamptz,
    'the balance as-of advances to the latest booked date (I7)';

  -- Children must sum to the parent, at statement end.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             parent_transaction_id)
      VALUES (h, a_card, '2024-02-06', 'SUPERMARKET', -100.0000, base,
              '2024-02-06', 1, 20, -100.0000, -2000.0000, 'expense', 'manual', 'd8c3', t_parent);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'children that do not sum to the parent must be rejected';
    END IF;
  END;
  BEGIN
    DELETE FROM transaction WHERE dedupe_hash = 'd8c2';
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'removing one child of a split must be rejected';
    END IF;
  END;

  -- Splits are one level deep.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             parent_transaction_id)
      VALUES (h, a_card, '2024-02-06', 'SUPERMARKET', -6500.0000, base,
              '2024-02-06', 1, 20, -6500.0000, -130000.0000, 'expense', 'manual', 'd8g', t_child);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN RAISE EXCEPTION 'a split child cannot itself be split'; END IF;
  END;

  RAISE NOTICE 'PASS 0009';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;

-- Isolation: another session with no bound user sees no ledger at all.
BEGIN;
  DO $$
  DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3);
  BEGIN
    SELECT min(code) INTO base FROM currency;
    SELECT min(code) INTO secondary FROM currency WHERE code > base;
    INSERT INTO household (name, base_currency, secondary_currency)
    VALUES ('txn rls fixture', base, secondary) RETURNING id INTO h;
    INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;
    INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                         country, opening_balance, opening_balance_date)
      VALUES (h,p,'RLS Savings','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a;
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a, '2024-02-01', 'RLS FIXTURE', -1.0000, base,
              '2024-02-01', 1, 20, -1.0000, -20.0000, 'expense', 'manual', 'rls1');
  END $$;

  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM transaction) >= 1,
      'the fixture must exist before the isolation check';
  END $$;

  SET LOCAL ROLE anon;
  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM transaction) = 0, 'anon must not read the ledger';
    ASSERT (SELECT count(*) FROM v_transaction_reportable) = 0,
      'the reporting view must not leak across households';
    RAISE NOTICE 'PASS 0009 isolation';
  END $$;
ROLLBACK;
