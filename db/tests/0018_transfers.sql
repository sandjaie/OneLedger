-- db/tests/0018_transfers.sql
-- V20: `transfer` per schema.md `0010`, and the two functions that are the only way a leg is
-- linked or released.
--
-- Invariants 3 and 4 are asserted here rather than inferred from `v_transaction_reportable`'s
-- text. A view that excludes a `transfer_id` is not the same guarantee as a confirmation path
-- that reliably sets one: the view has been right since V10 and nothing until now could put a
-- row behind it.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT to_regclass('public.transfer') IS NOT NULL, 'transfer must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'transfer'),
    'RLS must be enabled on transfer';
  ASSERT (SELECT count(*) FROM pg_indexes
           WHERE tablename = 'transfer'
             AND indexname IN ('idx_transfer_source', 'idx_transfer_dest')) = 2,
    'both partial unique indexes must exist';
  ASSERT EXISTS (SELECT FROM pg_constraint
                  WHERE conname = 'fk_txn_transfer' AND conrelid = 'transaction'::regclass),
    'transaction.transfer_id must gain its foreign key with this table';
  -- Both are SECURITY DEFINER, and both name pg_temp explicitly and last. V15 exists because
  -- a definer without pg_temp in its search_path reads the caller's temp schema first.
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE proname IN ('confirm_transfer', 'unlink_transfer')
             AND prosecdef
             AND 'search_path=public, pg_temp' = ANY(proconfig)) = 2,
    'both functions must be SECURITY DEFINER with search_path = public, pg_temp';
  ASSERT (SELECT atttypid::regtype::text FROM pg_attribute
           WHERE attrelid = 'staged_transaction'::regclass
             AND attname = 'proposed_transfer_kind') = 'transfer_kind',
    'proposed_transfer_kind must be the enum now that the enum exists';
  RAISE NOTICE 'PASS 0018 structure';
END $$;

-- ---------------------------------------------------------------------------------------
-- V13's seven values all still load, now that the CHECK has become the type. A conversion
-- that silently narrowed the vocabulary would only be found by an import months later.
-- ---------------------------------------------------------------------------------------
DO $$
DECLARE kind TEXT;
BEGIN
  FOREACH kind IN ARRAY ARRAY['internal_same_currency','remittance','card_payment',
                              'loan_payment','investment_funding','cash_withdrawal','other']
  LOOP
    PERFORM kind::transfer_kind;
  END LOOP;
  ASSERT (SELECT count(*) FROM pg_enum WHERE enumtypid = 'transfer_kind'::regtype) = 7,
    'transfer_kind must hold exactly the seven values V13 checked for';
  RAISE NOTICE 'PASS 0018 the staged column keeps every value it accepted as text';
END $$;

-- ---------------------------------------------------------------------------------------
-- The fixture every behavioural block below builds on: one household, two accounts, and a
-- caller who is its owner. Invented figures throughout.
-- ---------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION pg_temp.t0018_fixture()
  RETURNS TABLE (h UUID, src UUID, dst UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
DECLARE p UUID; u UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('transfers', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'self', true) RETURNING id INTO p;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h, p, 'transfers@example.test', 'owner') RETURNING id INTO u;
  PERFORM set_config('app.user_id', u::TEXT, true);
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h, p, 'Transfers source', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO src;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h, p, 'Transfers destination', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO dst;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0018_txn(
  p_household UUID, p_account UUID, p_amount NUMERIC, p_currency CHAR(3), p_type txn_type)
  RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE v_id UUID;
BEGIN
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
  VALUES (p_household, p_account, DATE '2026-03-02', 'transfer leg', p_amount, p_currency,
          DATE '2026-03-02', 1, 1, p_amount, p_amount, p_type, 'manual',
          encode(gen_random_bytes(32), 'hex'))
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- ---------------------------------------------------------------------------------------
-- Invariant 3, as testing-and-data-quality.html §1 states it: confirming a remittance drops
-- spending by exactly the source amount, and unlinking restores it exactly.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; leg_out UUID; leg_in UUID; t UUID; before NUMERIC; during NUMERIC; after NUMERIC;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  leg_out := pg_temp.t0018_txn(f.h, f.src, -4200.0000, f.base, 'expense');
  leg_in  := pg_temp.t0018_txn(f.h, f.dst,  4200.0000, f.base, 'income');

  SELECT COALESCE(-sum(amount_base), 0) INTO before FROM v_transaction_reportable
   WHERE household_id = f.h AND amount_base < 0;
  ASSERT before = 4200.0000, 'the unlinked leg must be spending, got ' || before;

  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, f.dst, leg_out, leg_in, 4200.0000, f.base, 4200.0000, f.base,
          DATE '2026-03-02', 'remittance', 'user_confirmed') RETURNING id INTO t;

  PERFORM confirm_transfer(t);
  SELECT COALESCE(-sum(amount_base), 0) INTO during FROM v_transaction_reportable
   WHERE household_id = f.h AND amount_base < 0;
  ASSERT during = 0, 'I3: a confirmed transfer is not spending, got ' || during;
  ASSERT (SELECT status FROM transfer WHERE id = t) = 'confirmed', 'the transfer must be confirmed';
  ASSERT (SELECT txn_type FROM transaction WHERE id = leg_out) = 'transfer_out',
    'the source leg must be transfer_out';
  ASSERT (SELECT txn_type FROM transaction WHERE id = leg_in) = 'transfer_in',
    'the destination leg must be transfer_in';

  PERFORM unlink_transfer(t);
  SELECT COALESCE(-sum(amount_base), 0) INTO after FROM v_transaction_reportable
   WHERE household_id = f.h AND amount_base < 0;
  ASSERT after = before,
    format('unlinking must restore spending exactly: %s before, %s after', before, after);
  RAISE NOTICE 'PASS 0018 I3 a confirmed transfer leaves every spending figure';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Invariant 4: the expense happened at purchase. A card payment confirmed as a transfer must
-- not be counted a second time.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; card UUID; purchase UUID; payment UUID; settle UUID; t UUID; spend NUMERIC;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date, credit_limit)
  SELECT f.h, p.id, 'Transfers card', 'credit_card', f.base, 'XB', 0, '2026-01-01', 50000.0000
    FROM person p WHERE p.household_id = f.h RETURNING id INTO card;

  purchase := pg_temp.t0018_txn(f.h, card, -310.0000, f.base, 'expense');
  payment  := pg_temp.t0018_txn(f.h, f.src, -3900.0000, f.base, 'expense');
  settle   := pg_temp.t0018_txn(f.h, card,  3900.0000, f.base, 'income');

  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, card, payment, settle, 3900.0000, f.base, 3900.0000, f.base,
          DATE '2026-03-02', 'card_payment', 'user_confirmed') RETURNING id INTO t;
  PERFORM confirm_transfer(t);

  SELECT COALESCE(-sum(amount_base), 0) INTO spend FROM v_transaction_reportable
   WHERE household_id = f.h AND amount_base < 0;
  ASSERT spend = 310.0000,
    format('I4: the purchase is the spending and the payment is not; expected 310, got %s', spend);
  RAISE NOTICE 'PASS 0018 I4 paying a card is not spending twice';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- A transaction is a leg of at most one confirmed transfer — and proposing it in a second
-- candidate is allowed, because the matcher is expected to.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; third UUID; leg_out UUID; leg_in UUID; other_in UUID; t1 UUID; t2 UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  SELECT f.h, p.id, 'Transfers third', 'savings', f.base, 'XB', 0, '2026-01-01'
    FROM person p WHERE p.household_id = f.h RETURNING id INTO third;

  leg_out  := pg_temp.t0018_txn(f.h, f.src, -900.0000, f.base, 'expense');
  leg_in   := pg_temp.t0018_txn(f.h, f.dst,  900.0000, f.base, 'income');
  other_in := pg_temp.t0018_txn(f.h, third,  900.0000, f.base, 'income');

  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, f.dst, leg_out, leg_in, 900.0000, f.base, 900.0000, f.base,
          DATE '2026-03-02', 'internal_same_currency', 'auto') RETURNING id INTO t1;
  -- The same debit, proposed against a different credit. Two candidates, no error.
  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, third, leg_out, other_in, 900.0000, f.base, 900.0000, f.base,
          DATE '2026-03-02', 'internal_same_currency', 'auto') RETURNING id INTO t2;

  PERFORM confirm_transfer(t1);
  BEGIN
    PERFORM confirm_transfer(t2);
    RAISE EXCEPTION 'a transaction must not be a leg of two confirmed transfers';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%idx_transfer_source%',
      'the refusal must name idx_transfer_source, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0018 one confirmed transfer per leg, many candidates';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Test 8 of `transfers-and-reconciliation.html` §8, which the partial indexes alone do not
-- satisfy: one transaction as the *source* of one confirmed transfer and the *destination*
-- of another. Each index is satisfied independently — `idx_transfer_source` sees the row once
-- and `idx_transfer_dest` sees it once — and `transaction.transfer_id` can hold only one of
-- the two, so without `assert_legs_unclaimed()` the second confirmation leaves the first
-- transfer pointing at a leg that no longer names it back.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; third UUID; middle UUID; credit UUID; debit UUID; t1 UUID; t2 UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  SELECT f.h, p.id, 'Transfers third', 'savings', f.base, 'XB', 0, '2026-01-01'
    FROM person p WHERE p.household_id = f.h RETURNING id INTO third;

  -- `middle` is a credit in the destination account: the arrival of transfer 1, and — were
  -- this allowed — the departure of transfer 2. A manual link is what produces this shape.
  debit  := pg_temp.t0018_txn(f.h, f.src,  -600.0000, f.base, 'expense');
  middle := pg_temp.t0018_txn(f.h, f.dst,   600.0000, f.base, 'income');
  credit := pg_temp.t0018_txn(f.h, third,   600.0000, f.base, 'income');

  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, f.dst, debit, middle, 600.0000, f.base, 600.0000, f.base,
          DATE '2026-03-02', 'internal_same_currency', 'auto') RETURNING id INTO t1;
  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.dst, third, middle, credit, 600.0000, f.base, 600.0000, f.base,
          DATE '2026-03-03', 'internal_same_currency', 'user_created') RETURNING id INTO t2;

  PERFORM confirm_transfer(t1);
  ASSERT (SELECT transfer_id FROM transaction WHERE id = middle) = t1,
    'the arrival must name the transfer it arrived on';

  BEGIN
    PERFORM confirm_transfer(t2);
    -- The trigger is DEFERRABLE INITIALLY DEFERRED, so it is queued rather than run: it
    -- fires at commit, and this block never commits. Forcing it here is what a commit would
    -- do, and is the only way a rolled-back test can see the refusal at all.
    SET CONSTRAINTS ALL IMMEDIATE;
    RAISE EXCEPTION 'a transaction must not be the source of one transfer and the '
                    'destination of another';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%already a leg of confirmed transfer%',
      'assert_legs_unclaimed must be what refuses it, got ' || refusal;
  END;
  -- The trigger fires on any UPDATE to a confirmed transfer, so it must not refuse a
  -- transfer over the legs that name it. Editing a note is the ordinary case.
  UPDATE transfer SET notes = 'the owner wrote something' WHERE id = t1;
  SET CONSTRAINTS ALL IMMEDIATE;

  -- And candidates may still overlap a confirmed leg freely: the matcher goes on proposing
  -- alternatives, and confirmation is what resolves to one
  -- (`transfers-and-reconciliation.html` §8). Refusing this would make a second run of the
  -- matcher an error.
  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.dst, third, middle, credit, 600.0000, f.base, 600.0000, f.base,
          DATE '2026-03-04', 'internal_same_currency', 'auto');
  SET CONSTRAINTS ALL IMMEDIATE;
  ASSERT (SELECT count(*) FROM transfer WHERE household_id = f.h AND status = 'candidate') = 2,
    'a claimed leg may still appear in candidates';

  RAISE NOTICE 'PASS 0018 a leg claimed in one role cannot be claimed in the other';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The authorization boundary. The definer bypasses RLS, so these predicates are the
-- isolation rather than a second opinion on one.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; other_h UUID; other_p UUID; other_a UUID; stranger UUID; mine UUID;
        t UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  INSERT INTO household (name, base_currency, secondary_currency)
  SELECT 'transfers other', f.base, min(code) FROM currency WHERE code > f.base
  RETURNING id INTO other_h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (other_h, 'them', true)
  RETURNING id INTO other_p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (other_h, other_p, 'Theirs', 'savings', f.base, 'XB', 0, '2026-01-01') RETURNING id INTO other_a;

  mine     := pg_temp.t0018_txn(f.h, f.src, -50.0000, f.base, 'expense');
  stranger := pg_temp.t0018_txn(other_h, other_a, 50.0000, f.base, 'income');

  -- A transfer in my household naming their transaction as its destination leg.
  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, f.dst, mine, stranger, 50.0000, f.base, 50.0000, f.base,
          DATE '2026-03-02', 'other', 'user_created') RETURNING id INTO t;

  BEGIN
    PERFORM confirm_transfer(t);
    RAISE EXCEPTION 'a leg from another household must be refused';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
  ASSERT (SELECT transfer_id FROM transaction WHERE id = stranger) IS NULL,
    'the refused call must have written nothing';
  ASSERT (SELECT transfer_id FROM transaction WHERE id = mine) IS NULL,
    'nor to the leg that was legitimately mine';

  -- And the same again with the transfer naming *their account* as its destination, so the
  -- account predicate agrees and only the household predicate is left to refuse it. Nothing
  -- stops a transfer row from naming a foreign account — `destination_account_id` references
  -- `account(id)` and no more — so this is the case where dropping the household check would
  -- link a leg across two households, and the block above would not have noticed.
  UPDATE transfer SET destination_account_id = other_a WHERE id = t;
  BEGIN
    PERFORM confirm_transfer(t);
    RAISE EXCEPTION 'a leg in another household''s account must be refused';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
  ASSERT (SELECT transfer_id FROM transaction WHERE id = stranger) IS NULL,
    'their transaction must stay unlinked even when the transfer names their account';
  UPDATE transfer SET destination_account_id = f.dst WHERE id = t;

  -- And a caller who is nobody.
  PERFORM set_config('app.user_id', '', true);
  BEGIN
    PERFORM confirm_transfer(t);
    RAISE EXCEPTION 'a caller who is not the owner must be refused';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', 'assert_is_owner must answer first, got ' || refusal;
  END;
  BEGIN
    PERFORM unlink_transfer(t);
    RAISE EXCEPTION 'unlinking is behind the same boundary';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
  RAISE NOTICE 'PASS 0018 both functions are the boundary, not a convenience over one';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Unlinking restores each leg's type from its own sign, leaves the transfer rejected so the
-- matcher does not re-propose it, and never touches the money (I1, I5).
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; leg_out UUID; leg_in UUID; t UUID; money_before RECORD; money_after RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  leg_out := pg_temp.t0018_txn(f.h, f.src, -75.5000, f.base, 'expense');
  leg_in  := pg_temp.t0018_txn(f.h, f.dst,  75.5000, f.base, 'income');
  INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                        source_transaction_id, destination_transaction_id,
                        source_amount, source_currency, destination_amount,
                        destination_currency, transfer_date, transfer_kind, match_method)
  VALUES (f.h, f.src, f.dst, leg_out, leg_in, 75.5000, f.base, 75.5000, f.base,
          DATE '2026-03-02', 'internal_same_currency', 'auto') RETURNING id INTO t;

  SELECT amount, currency, amount_base, amount_secondary, fx_rate_to_base, booked_date
    INTO money_before FROM transaction WHERE id = leg_out;

  PERFORM confirm_transfer(t);
  PERFORM unlink_transfer(t);

  ASSERT (SELECT status FROM transfer WHERE id = t) = 'rejected',
    'unlinking rejects the pair rather than returning it to the candidate list';
  ASSERT (SELECT count(*) FROM transaction
           WHERE id IN (leg_out, leg_in) AND transfer_id IS NOT NULL) = 0,
    'both legs must be released';
  ASSERT (SELECT txn_type FROM transaction WHERE id = leg_out) = 'expense',
    'a negative leg comes back as an expense, from its sign';
  ASSERT (SELECT txn_type FROM transaction WHERE id = leg_in) = 'income',
    'a positive leg comes back as income, from its sign';

  SELECT amount, currency, amount_base, amount_secondary, fx_rate_to_base, booked_date
    INTO money_after FROM transaction WHERE id = leg_out;
  ASSERT money_before = money_after,
    'neither function may touch the money — the immutability trigger would have said so';
  RAISE NOTICE 'PASS 0018 unlinking reverses the link and nothing else';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The table's own refusals: a transfer between one account, and a non-positive magnitude.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0018_fixture();
  BEGIN
    INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                          source_amount, source_currency, destination_amount,
                          destination_currency, transfer_date, transfer_kind, match_method)
    VALUES (f.h, f.src, f.src, 10.0000, f.base, 10.0000, f.base,
            DATE '2026-03-02', 'other', 'auto');
    RAISE EXCEPTION 'a transfer to the same account must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%different_accounts%', 'expected different_accounts, got ' || refusal;
  END;

  BEGIN
    INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                          source_amount, source_currency, destination_amount,
                          destination_currency, transfer_date, transfer_kind, match_method)
    VALUES (f.h, f.src, f.dst, -10.0000, f.base, 10.0000, f.base,
            DATE '2026-03-02', 'other', 'auto');
    RAISE EXCEPTION 'a transfer carries magnitudes, never a sign';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%amounts_positive%', 'expected amounts_positive, got ' || refusal;
  END;

  BEGIN
    UPDATE transfer SET status = 'confirmed' WHERE household_id = f.h;
    INSERT INTO transfer (household_id, source_account_id, destination_account_id,
                          source_amount, source_currency, destination_amount,
                          destination_currency, transfer_date, transfer_kind, match_method,
                          status)
    VALUES (f.h, f.src, f.dst, 10.0000, f.base, 10.0000, f.base,
            DATE '2026-03-02', 'other', 'auto', 'confirmed');
    RAISE EXCEPTION 'a confirmed transfer without legs must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%confirmed_has_legs%', 'expected confirmed_has_legs, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0018 the table refuses what it says it refuses';
END $$;
ROLLBACK;
