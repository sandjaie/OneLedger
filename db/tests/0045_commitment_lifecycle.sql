-- V48: a commitment's lifecycle over exactly one open occurrence, resolved by linking a ledger
-- posting, marking done, or dismissing with a reason. `commitment_resolution` is a plain
-- household-scoped table — no SECURITY DEFINER write path — guarded by `assert_commitment_payment`
-- (payment eligibility) and `preserve_commitment_resolution` (immutable once inserted). All
-- names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'commitment_resolution') = 1,
    'the commitment_resolution table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'commitment_resolution'::regclass),
    'commitment_resolution must have row level security enabled';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid = 'assert_commitment_payment()'::regprocedure) = 1,
    'the payment eligibility guard function must exist';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid = 'preserve_commitment_resolution()'::regprocedure) = 1,
    'the immutability guard function must exist';
  RAISE NOTICE 'PASS 0045 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0045_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    a1 UUID, a1b UUID, a2 UUID, category1 UUID,
    c1 UUID, c2 UUID, c_foreign UUID,
    eligible_txn UUID, wrong_account_txn UUID, excluded_txn UUID, opening_txn UUID,
    sign_mismatch_txn UUID, currency_ok_txn UUID, transfer_txn UUID, parent_txn UUID,
    payslip_expense_txn UUID, foreign_txn UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE
  p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
  payslip1 UUID; deduction_line UUID; income_txn UUID; child1 UUID; child2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('commitment lifecycle household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('commitment lifecycle household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'lifecycle owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'lifecycle member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other lifecycle owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'lifecycle-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'lifecycle-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-lifecycle-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic lifecycle account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic second lifecycle account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a1b;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic lifecycle account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a2;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h1, 'Synthetic lifecycle spend', 'lifecycle-spend', 'expense', 'want')
  RETURNING id INTO category1;

  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date)
  VALUES (h1, gen_random_uuid(), a1, 'Synthetic rent', -500.0000, base, 'monthly',
          '2026-01-01', '2026-02-01')
  RETURNING id INTO c1;
  -- c2's currency is set directly to the household's secondary currency though its account is
  -- in base — the API's own create-time check would refuse this; the DB is exercised directly to
  -- isolate a currency-only eligibility refusal from an account refusal.
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date)
  VALUES (h1, gen_random_uuid(), a1, 'Synthetic mismatched currency', -300.0000, secondary,
          'once', '2026-01-01', '2026-01-01')
  RETURNING id INTO c2;
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date)
  VALUES (h2, gen_random_uuid(), a2, 'Other synthetic rent', -500.0000, base, 'monthly',
          '2026-01-01', '2026-02-01')
  RETURNING id INTO c_foreign;

  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC RENT PAYMENT', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'expense', 'manual', 'lc-eligible')
    RETURNING id INTO eligible_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1b, '2026-02-01', 'SYNTHETIC WRONG ACCOUNT', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'expense', 'manual', 'lc-wrong-account')
    RETURNING id INTO wrong_account_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash, is_excluded)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC EXCLUDED', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'expense', 'manual', 'lc-excluded', true)
    RETURNING id INTO excluded_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC OPENING', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'opening_balance', 'manual', 'lc-opening')
    RETURNING id INTO opening_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC WRONG SIGN', 500.0000, base,
            '2026-02-01', 1, 1, 500.0000, 500.0000, 'income', 'manual', 'lc-sign')
    RETURNING id INTO sign_mismatch_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-01-01', 'SYNTHETIC CURRENCY OK', -300.0000, base,
            '2026-01-01', 1, 1, -300.0000, -300.0000, 'expense', 'manual', 'lc-currency-ok')
    RETURNING id INTO currency_ok_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC TRANSFER LEG', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'transfer_out', 'manual', 'lc-transfer')
    RETURNING id INTO transfer_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h2, a2, '2026-02-01', 'SYNTHETIC FOREIGN', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'expense', 'manual', 'lc-foreign')
    RETURNING id INTO foreign_txn;

  -- A split parent is excluded from v_transaction_posting once it has children, so it can never
  -- be an eligible posting for a resolution.
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC SPLIT PARENT', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'expense', 'manual', 'lc-parent')
    RETURNING id INTO parent_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, category_id, source,
                           dedupe_hash, parent_transaction_id)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC SPLIT PARENT', -300.0000, base,
            '2026-02-01', 1, 1, -300.0000, -300.0000, 'expense', category1, 'manual',
            'lc-parent-c1', parent_txn)
    RETURNING id INTO child1;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, category_id, source,
                           dedupe_hash, parent_transaction_id)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC SPLIT PARENT', -200.0000, base,
            '2026-02-01', 1, 1, -200.0000, -200.0000, 'expense', category1, 'manual',
            'lc-parent-c2', parent_txn)
    RETURNING id INTO child2;

  -- A posting already committed as one leg of a payslip deduction pair is ineligible — it is
  -- released only by unlinking the payslip, which the resolution's own RESTRICT key would block.
  PERFORM set_config('app.user_id', u1::TEXT, true);
  SELECT r.recorded_payslip_id INTO payslip1 FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', base, 5000.0000, 500.0000, 4500.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Synthetic withheld'],
    ARRAY[5000.0000, 500.0000]) r;
  SELECT id INTO deduction_line FROM payslip_line
   WHERE household_id = h1 AND payslip_id = payslip1 AND kind = 'deduction';
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC PAYROLL INCOME', 500.0000, base,
            '2026-02-01', 1, 1, 500.0000, 500.0000, 'income', 'manual', 'lc-payroll-income')
    RETURNING id INTO income_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, category_id, source,
                           dedupe_hash)
    VALUES (h1, a1, '2026-02-01', 'SYNTHETIC PAYROLL EXPENSE', -500.0000, base,
            '2026-02-01', 1, 1, -500.0000, -500.0000, 'expense', category1, 'manual',
            'lc-payroll-expense')
    RETURNING id INTO payslip_expense_txn;
  INSERT INTO payslip_line_posting (
    household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
  VALUES (h1, deduction_line, income_txn, payslip_expense_txn);

  RETURN NEXT;
END $$;

-- The resolution shape: exactly the field combination each kind requires.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0045_fixture();
  -- An otherwise-eligible posting still trips the shape check when a payment carries a reason
  -- (reserved for a dismissal) — using an eligible transaction here isolates the shape refusal
  -- from the payment-eligibility trigger, which runs first and would otherwise mask it.
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id, reason)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.eligible_txn, 'not allowed');
    RAISE EXCEPTION 'a payment with a reason must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%commitment_resolution_shape%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'completed', true, f.eligible_txn);
    RAISE EXCEPTION 'a completed row with a transaction must be refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes)
    VALUES (f.h1, f.c1, '2026-02-01', 'dismissed', true);
    RAISE EXCEPTION 'a dismissal without a reason must be refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes, reason)
    VALUES (f.h1, f.c1, '2026-02-01', 'dismissed', false, 'too soon');
    RAISE EXCEPTION 'a dismissal must always close';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  RAISE NOTICE 'PASS 0045 resolution shape refused';
END $$;
ROLLBACK;

-- Cross-household commitment and transaction references are refused by the composite foreign
-- keys, before any trigger runs.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0045_fixture();
  BEGIN
    INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes)
    VALUES (f.h1, f.c_foreign, '2026-02-01', 'completed', true);
    RAISE EXCEPTION 'a cross-household commitment reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_commitment_resolution_commitment_scope%', refusal;
  END;
  -- A payment's own BEFORE trigger runs before the FK is checked and finds no matching
  -- household-scoped posting first — still refused, by the eligibility rule rather than the FK.
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.foreign_txn);
    RAISE EXCEPTION 'a cross-household transaction reference must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  RAISE NOTICE 'PASS 0045 cross-household references refused';
END $$;
ROLLBACK;

-- Every commitment_payment_is_eligible refusal, and a transfer leg accepted.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0045_fixture();

  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.wrong_account_txn);
    RAISE EXCEPTION 'a posting on a different account must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c2, '2026-01-01', 'payment', false, f.currency_ok_txn);
    RAISE EXCEPTION 'a currency mismatch must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.sign_mismatch_txn);
    RAISE EXCEPTION 'a signed-wrong posting must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.excluded_txn);
    RAISE EXCEPTION 'an excluded posting must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.opening_txn);
    RAISE EXCEPTION 'an opening balance posting must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.payslip_expense_txn);
    RAISE EXCEPTION 'a posting already paired with a payslip deduction must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.parent_txn);
    RAISE EXCEPTION 'a split parent (not a leaf posting) must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;

  -- A transfer leg is a real cash movement on the paying account and is eligible.
  INSERT INTO commitment_resolution (
    household_id, commitment_id, due_date, kind, closes, transaction_id)
  VALUES (f.h1, f.c1, '2026-02-01', 'payment', true, f.transfer_txn);
  ASSERT (SELECT count(*) FROM commitment_resolution
           WHERE household_id = f.h1 AND transaction_id = f.transfer_txn) = 1,
    'a transfer leg must be accepted as an eligible payment';

  RAISE NOTICE 'PASS 0045 eligibility refusals and a transfer leg accepted';
END $$;
ROLLBACK;

-- Closing and transaction uniqueness, and immutability once inserted.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; row_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0045_fixture();

  INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes)
  VALUES (f.h1, f.c1, '2026-02-01', 'completed', true)
  RETURNING id INTO row_id;
  BEGIN
    INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes, reason)
    VALUES (f.h1, f.c1, '2026-02-01', 'dismissed', true, 'duplicate closing attempt');
    RAISE EXCEPTION 'a second closing row for the same occurrence must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_commitment_resolution_closing%', refusal;
  END;

  INSERT INTO commitment_resolution (
    household_id, commitment_id, due_date, kind, closes, transaction_id)
  VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.eligible_txn);
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, f.c1, '2026-02-01', 'payment', false, f.eligible_txn);
    RAISE EXCEPTION 'the same posting must not settle twice';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_commitment_resolution_transaction%', refusal;
  END;

  BEGIN
    UPDATE commitment_resolution SET closes = false WHERE id = row_id;
    RAISE EXCEPTION 'a resolution must be immutable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  RAISE NOTICE 'PASS 0045 closing and transaction uniqueness, immutability';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS: only the owning household's authenticated owner
-- reads or writes its own resolution rows.
BEGIN;
DO $$
DECLARE f RECORD; r1 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0045_fixture();
  INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes)
  VALUES (f.h1, f.c1, '2026-02-01', 'completed', true)
  RETURNING id INTO r1;
  INSERT INTO commitment_resolution (household_id, commitment_id, due_date, kind, closes)
  VALUES (f.h2, f.c_foreign, '2026-02-01', 'completed', true);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM commitment_resolution) = 1,
    'owner sees only its own household resolution';
  ASSERT (SELECT id FROM commitment_resolution WHERE household_id = f.h1) = r1,
    'owner reads its own resolution row';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM commitment_resolution) = 0,
    'a household member reads no commitment resolution';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM commitment_resolution) = 1,
    'the other household sees only its own row';
  ASSERT (SELECT count(*) FROM commitment_resolution WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s resolution';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM commitment_resolution) = 0,
    'anonymous commitment resolution reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0045 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
