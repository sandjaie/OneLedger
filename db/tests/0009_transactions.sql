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

  -- I7 as a shape: no look-ahead rates, and carried means exactly "from an earlier date".
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_sav, '2024-02-12', 'UNFLAGGED CARRY', -10.0000, base,
              '2024-02-09', 1, 20, -10.0000, -200.0000, 'expense', 'manual', 'cr1');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_sav, '2024-02-12', 'FUTURE RATE', -10.0000, base,
              '2024-02-15', 1, 20, -10.0000, -200.0000, 'expense', 'manual', 'cr2');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- Frozen amounts reproduce their stored rate on every row, whoever the writer is.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_sav, '2024-02-12', 'MADE UP FROZEN', -10.0000, base,
              '2024-02-12', 1, 20, -999.0000, -200.0000, 'expense', 'manual', 'mf1');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

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

  -- A split child inherits its parent's facts (data-model, Splits): another account, date or
  -- rate would report the movement somewhere it never happened.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             parent_transaction_id)
      VALUES (h, a_sav, '2024-02-06', 'SUPERMARKET', -100.0000, base,
              '2024-02-06', 1, 20, -100.0000, -2000.0000, 'expense', 'manual', 'd8x1', t_parent);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a child in another account must be rejected';
    END IF;
  END;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             parent_transaction_id)
      VALUES (h, a_card, '2024-02-07', 'SUPERMARKET', -100.0000, base,
              '2024-02-06', 1, 20, -100.0000, -2000.0000, 'expense', 'manual', 'd8x2', t_parent);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a child on another date must be rejected';
    END IF;
  END;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             parent_transaction_id)
      VALUES (h, a_card, '2024-02-06', 'SUPERMARKET', -100.0000, base,
              '2024-02-06', 2, 20, -200.0000, -2000.0000, 'expense', 'manual', 'd8x3', t_parent);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a child with different frozen rates must be rejected';
    END IF;
  END;

  -- A child's inherited columns are revalidated on every update, not only reparenting.
  BEGIN
    UPDATE transaction SET account_id = a_sav WHERE id = t_child;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'moving a split child to another account must be rejected';
    END IF;
  END;

  -- A parent's inherited facts are fixed while children exist.
  BEGIN
    UPDATE transaction SET account_id = a_sav WHERE id = t_parent;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'moving a split parent with children must be rejected';
    END IF;
  END;

  -- Reparenting validates the losing parent too: a move that satisfies its new parent but
  -- leaves the old parent's children short must be rejected.
  DECLARE p3 UUID; p4 UUID; c3b UUID;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_card, '2024-02-08', 'SPLIT THREE', -200.0000, base,
              '2024-02-08', 1, 20, -200.0000, -4000.0000, 'expense', 'manual', 'p3')
      RETURNING id INTO p3;
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_card, '2024-02-08', 'SPLIT FOUR', -50.0000, base,
              '2024-02-08', 1, 20, -50.0000, -1000.0000, 'expense', 'manual', 'p4')
      RETURNING id INTO p4;
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             parent_transaction_id)
      VALUES (h, a_card, '2024-02-08', 'SPLIT THREE', -150.0000, base,
              '2024-02-08', 1, 20, -150.0000, -3000.0000, 'expense', 'manual', 'p3a', p3),
             (h, a_card, '2024-02-08', 'SPLIT THREE', -50.0000, base,
              '2024-02-08', 1, 20, -50.0000, -1000.0000, 'expense', 'manual', 'p3b', p3);
    SELECT id INTO c3b FROM transaction WHERE dedupe_hash = 'p3b';
    BEGIN
      UPDATE transaction SET parent_transaction_id = p4 WHERE id = c3b;
      RAISE EXCEPTION 'unreachable';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM = 'unreachable' THEN
        RAISE EXCEPTION 'a reparent that leaves the old parent short must be rejected';
      END IF;
    END;
  END;

  -- Report-affecting divergence on a child is refused: an excluded child would drop part of
  -- a movement the balance still holds in full.
  BEGIN
    UPDATE transaction SET is_excluded = true WHERE id = t_child;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'excluding one split child must be rejected';
    END IF;
  END;

  -- Topology changes rebalance: an adopted posting stops counting (its parent carries the
  -- movement); a released child counts again.
  DECLARE q1 UUID; p4 UUID; before NUMERIC(20,4);
  BEGIN
    SELECT id INTO p4 FROM transaction WHERE dedupe_hash = 'p4';
    before := (SELECT current_balance FROM account WHERE id = a_card);
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_card, '2024-02-08', 'SPLIT FOUR', -50.0000, base,
              '2024-02-08', 1, 20, -50.0000, -1000.0000, 'expense', 'manual', 'q1')
      RETURNING id INTO q1;
    ASSERT (SELECT current_balance FROM account WHERE id = a_card) = before - 50.0000,
      'a new posting counts';
    UPDATE transaction SET parent_transaction_id = p4 WHERE id = q1;
    ASSERT (SELECT current_balance FROM account WHERE id = a_card) = before,
      'an adopted posting stops counting — its parent already carries the movement';
    UPDATE transaction SET parent_transaction_id = NULL WHERE id = q1;
    ASSERT (SELECT current_balance FROM account WHERE id = a_card) = before - 50.0000,
      'a released child counts again';
  END;

  -- Each leaf derives its frozen amounts from its own amount and rates: totals that balance
  -- while one child hoards the converted value are rejected per row.
  DECLARE p6 UUID;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_card, '2024-02-11', 'SPLIT SIX', -60.0000, base,
              '2024-02-11', 20, 1, -1200.0000, -60.0000, 'expense', 'manual', 'p6')
      RETURNING id INTO p6;
    BEGIN
      INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                               currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                               amount_base, amount_secondary, txn_type, source, dedupe_hash,
                               parent_transaction_id)
        VALUES (h, a_card, '2024-02-11', 'SPLIT SIX', -30.0000, base,
                '2024-02-11', 20, 1, -1200.0000, -30.0000, 'expense', 'manual', 'p6a', p6),
               (h, a_card, '2024-02-11', 'SPLIT SIX', -30.0000, base,
                '2024-02-11', 20, 1, 0.0000, -30.0000, 'expense', 'manual', 'p6b', p6);
      RAISE EXCEPTION 'unreachable';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
  END;

  -- The frozen sums balance too: each row may carry a cent of allocated residual, so the
  -- aggregate check is what stops residues accumulating into a divergent converted total.
  DECLARE p5 UUID;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_card, '2024-02-09', 'SPLIT FIVE', -0.0002, base,
              '2024-02-09', 3, 1, -0.0006, -0.0002, 'expense', 'manual', 'p5')
      RETURNING id INTO p5;
    BEGIN
      -- Each child stays within its one-cent row tolerance, but the residues line up the
      -- same way and the total drifts: -0.0008 against the parent's -0.0006.
      INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                               currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                               amount_base, amount_secondary, txn_type, source, dedupe_hash,
                               parent_transaction_id)
        VALUES (h, a_card, '2024-02-09', 'SPLIT FIVE', -0.0001, base,
                '2024-02-09', 3, 1, -0.0004, -0.0001, 'expense', 'manual', 'p5a', p5),
               (h, a_card, '2024-02-09', 'SPLIT FIVE', -0.0001, base,
                '2024-02-09', 3, 1, -0.0004, -0.0001, 'expense', 'manual', 'p5b', p5);
      RAISE EXCEPTION 'unreachable';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM = 'unreachable' THEN
        RAISE EXCEPTION 'children whose frozen sums diverge from the parent must be rejected';
      END IF;
    END;
  END;

  -- A transaction is denominated in its account's currency.
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash)
      VALUES (h, a_sav, '2024-02-10', 'WRONG DENOMINATION', -5.0000, secondary,
              '2024-02-10', 1, 20, -5.0000, -100.0000, 'expense', 'manual', 'wd1');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a foreign-denominated transaction must be rejected';
    END IF;
  END;

  -- A transaction's household owns its account: the balance trigger moves that account's
  -- money, and a cross-household row would move another tenant's.
  DECLARE h2 UUID; p2 UUID; a2 UUID;
  BEGIN
    INSERT INTO household (name, base_currency, secondary_currency)
    VALUES ('other household', base, secondary) RETURNING id INTO h2;
    INSERT INTO person (household_id, display_name, is_self) VALUES (h2,'self',true)
      RETURNING id INTO p2;
    INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                         country, opening_balance, opening_balance_date)
      VALUES (h2,p2,'Foreign Savings','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a2;
    BEGIN
      INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                               currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                               amount_base, amount_secondary, txn_type, source, dedupe_hash)
        VALUES (h, a2, '2024-02-10', 'CROSS HOUSEHOLD', -5.0000, base,
                '2024-02-10', 1, 20, -5.0000, -100.0000, 'expense', 'manual', 'xh1');
      RAISE EXCEPTION 'unreachable';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM = 'unreachable' THEN
        RAISE EXCEPTION 'a cross-household transaction must be rejected';
      END IF;
    END;

    -- Nor a pre-anchor posting: the opening balance already holds those movements.
    BEGIN
      INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                               currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                               amount_base, amount_secondary, txn_type, source, dedupe_hash)
        VALUES (h, a_sav, '2023-12-15', 'PRE ANCHOR', -5.0000, base,
                '2023-12-15', 1, 20, -5.0000, -100.0000, 'expense', 'manual', 'pa1');
      RAISE EXCEPTION 'unreachable';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM = 'unreachable' THEN
        RAISE EXCEPTION 'a pre-anchor posting must be rejected';
      END IF;
    END;

    -- Nor another household's merchant.
    DECLARE foreign_merchant UUID;
    BEGIN
      INSERT INTO merchant (household_id, canonical_name) VALUES (h2, 'Their Shop')
        RETURNING id INTO foreign_merchant;
      BEGIN
        UPDATE transaction SET merchant_id = foreign_merchant WHERE id = t_lone;
        RAISE EXCEPTION 'unreachable';
      EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = 'unreachable' THEN
          RAISE EXCEPTION 'a cross-household merchant must be rejected';
        END IF;
      END;
    END;

    -- Nor may a row wear another household's category.
    DECLARE foreign_cat UUID;
    BEGIN
      INSERT INTO category (household_id, name, slug, kind, bucket)
        VALUES (h2, 'Their Category', 'their-cat', 'expense', 'want') RETURNING id INTO foreign_cat;
      BEGIN
        UPDATE transaction SET category_id = foreign_cat WHERE id = t_lone;
        RAISE EXCEPTION 'unreachable';
      EXCEPTION WHEN raise_exception THEN
        IF SQLERRM = 'unreachable' THEN
          RAISE EXCEPTION 'a cross-household category must be rejected';
        END IF;
      END;
    END;
  END;

  -- Nothing is its own container.
  BEGIN
    UPDATE transaction SET parent_transaction_id = t_lone WHERE id = t_lone;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a transaction must not be its own split parent';
    END IF;
  END;

  -- A transaction belongs to its account: reassignment is a delete and a recreate.
  BEGIN
    UPDATE transaction SET account_id = a_sav WHERE id = t_lone;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'reassigning an unsplit transaction''s account must be rejected';
    END IF;
  END;

  -- Deleting the latest posting rewinds the as-of to a date the ledger still supports (I7).
  DELETE FROM transaction WHERE dedupe_hash = 'd6';
  ASSERT (SELECT current_balance FROM account WHERE id = a_sav) = 95000.0000,
    'the deleted posting leaves the balance';
  ASSERT (SELECT current_balance_as_of FROM account WHERE id = a_sav)
           = '2024-02-04'::timestamptz,
    'the as-of rewinds to the latest remaining posting';

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
