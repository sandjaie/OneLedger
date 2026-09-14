-- V44: a deduction the owner marks as spending becomes a balanced income/expense pair
-- (spec "Deductions counted as spending"). Both new tables are plain household-scoped tables —
-- no SECURITY DEFINER write path, RLS as defence in depth, the same convention V43's
-- `commitment` table uses. All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public'
             AND tablename IN ('payslip_deduction_rule', 'payslip_line_posting')) = 2,
    'both V44 tables must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'payslip_deduction_rule'::regclass),
    'payslip_deduction_rule must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'payslip_line_posting'::regclass),
    'payslip_line_posting must have row level security enabled';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid = 'assert_payslip_line_posting_is_deduction()'::regprocedure) = 1,
    'the deduction-only guard function must exist';
  RAISE NOTICE 'PASS 0041 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0041_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    category1 UUID, category2 UUID,
    deduction_line1 UUID, earning_line1 UUID, deduction_line2 UUID,
    income_txn1 UUID, expense_txn1 UUID, income_txn2 UUID, expense_txn2 UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
        a1 UUID; a2 UUID; payslip1 UUID; payslip2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('spending household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('spending household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'spending owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'spending member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other spending owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'spending-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'spending-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-spending-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic spending account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic spending account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a2;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h1, 'Synthetic travel', 'spending-travel-one', 'expense', 'want')
  RETURNING id INTO category1;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h2, 'Synthetic travel', 'spending-travel-two', 'expense', 'want')
  RETURNING id INTO category2;

  -- record_payslip is SECURITY DEFINER but still identifies its household through
  -- app.user_id (assert_is_owner / current_household_id), same as every route caller.
  PERFORM set_config('app.user_id', u1::TEXT, true);
  SELECT r.recorded_payslip_id INTO payslip1 FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', base, 5000.0000, 400.0000, 4600.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Flights'],
    ARRAY[5000.0000, 400.0000]) r;
  PERFORM set_config('app.user_id', u2::TEXT, true);
  SELECT r.recorded_payslip_id INTO payslip2 FROM record_payslip(
    gen_random_uuid(), NULL, NULL, '2026-01-01', base, 5000.0000, 400.0000, 4600.0000,
    ARRAY['earning', 'deduction']::payslip_line_kind[], ARRAY['Basic', 'Flights'],
    ARRAY[5000.0000, 400.0000]) r;
  SELECT id INTO earning_line1 FROM payslip_line
   WHERE household_id = h1 AND payslip_id = payslip1 AND kind = 'earning';
  SELECT id INTO deduction_line1 FROM payslip_line
   WHERE household_id = h1 AND payslip_id = payslip1 AND kind = 'deduction';
  SELECT id INTO deduction_line2 FROM payslip_line
   WHERE household_id = h2 AND payslip_id = payslip2 AND kind = 'deduction';

  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, category_id, source, dedupe_hash
  ) VALUES (
    h1, a1, '2026-01-15', 'Payroll: Flights withheld', 400.0000, base,
    '2026-01-15', 1, 1, 400.0000, 400.0000, 'income', NULL, 'manual', repeat('a', 64)
  ) RETURNING id INTO income_txn1;
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, category_id, source, dedupe_hash
  ) VALUES (
    h1, a1, '2026-01-15', 'Payroll: Flights', -400.0000, base,
    '2026-01-15', 1, 1, -400.0000, -400.0000, 'expense', category1, 'manual', repeat('b', 64)
  ) RETURNING id INTO expense_txn1;
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, category_id, source, dedupe_hash
  ) VALUES (
    h2, a2, '2026-01-15', 'Payroll: Flights withheld', 400.0000, base,
    '2026-01-15', 1, 1, 400.0000, 400.0000, 'income', NULL, 'manual', repeat('c', 64)
  ) RETURNING id INTO income_txn2;
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, category_id, source, dedupe_hash
  ) VALUES (
    h2, a2, '2026-01-15', 'Payroll: Flights', -400.0000, base,
    '2026-01-15', 1, 1, -400.0000, -400.0000, 'expense', category2, 'manual', repeat('e', 64)
  ) RETURNING id INTO expense_txn2;
  RETURN NEXT;
END $$;

-- A rule must resolve to an active expense category, checked at the API layer, not here; the
-- schema's own job is the deduction-only guard and household scoping. A posting on an earning
-- line is refused by the trigger, whatever household it is in.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0041_fixture();
  BEGIN
    INSERT INTO payslip_line_posting (
      household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
    VALUES (f.h1, f.earning_line1, f.income_txn1, f.expense_txn1);
    RAISE EXCEPTION 'a posting on an earning line must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%deduction line%', refusal;
  END;
  RAISE NOTICE 'PASS 0041 earning line refused';
END $$;
ROLLBACK;

-- A cross-household line reference has no matching deduction line in this household at all, so
-- the deduction-only trigger (which runs before the foreign key is even checked) refuses it
-- first — the same path the earning-line case above takes. A cross-household transaction
-- reference, which the trigger does not look at, is refused by the composite foreign key.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0041_fixture();
  BEGIN
    INSERT INTO payslip_line_posting (
      household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
    VALUES (f.h1, f.deduction_line2, f.income_txn1, f.expense_txn1);
    RAISE EXCEPTION 'a cross-household line reference must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%deduction line%', refusal;
  END;
  BEGIN
    INSERT INTO payslip_line_posting (
      household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
    VALUES (f.h1, f.deduction_line1, f.income_txn2, f.expense_txn1);
    RAISE EXCEPTION 'a cross-household transaction reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_payslip_line_posting_income_scope%', refusal;
  END;
  RAISE NOTICE 'PASS 0041 cross-household references refused';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS on both tables: only the owning household's
-- authenticated owner reads or writes its own rule and posting rows.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0041_fixture();
  INSERT INTO payslip_deduction_rule (household_id, label, category_id)
  VALUES (f.h1, 'Flights', f.category1);
  INSERT INTO payslip_deduction_rule (household_id, label, category_id)
  VALUES (f.h2, 'Flights', f.category2);
  INSERT INTO payslip_line_posting (
    household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
  VALUES (f.h1, f.deduction_line1, f.income_txn1, f.expense_txn1);
  INSERT INTO payslip_line_posting (
    household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
  VALUES (f.h2, f.deduction_line2, f.income_txn2, f.expense_txn2);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip_deduction_rule) = 1,
    'owner sees only its own household rule';
  ASSERT (SELECT category_id FROM payslip_deduction_rule WHERE household_id = f.h1) = f.category1,
    'owner reads its own rule row';
  ASSERT (SELECT count(*) FROM payslip_line_posting) = 1,
    'owner sees only its own household posting';
  RESET ROLE;

  -- current_household_id() resolves only an 'owner' row (V5): a member reads neither table.
  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip_deduction_rule) = 0, 'a household member reads no rule';
  ASSERT (SELECT count(*) FROM payslip_line_posting) = 0, 'a household member reads no posting';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payslip_deduction_rule) = 1,
    'the other household sees only its own rule';
  ASSERT (SELECT count(*) FROM payslip_line_posting) = 1,
    'the other household sees only its own posting';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM payslip_deduction_rule) = 0, 'anonymous rule reads are empty';
  ASSERT (SELECT count(*) FROM payslip_line_posting) = 0, 'anonymous posting reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0041 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
