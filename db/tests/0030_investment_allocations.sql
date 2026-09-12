-- V33: owner-authorized cash and disposal-lot allocations replace the legacy cash link.
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE relation_name TEXT;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'investment_cash_allocation', 'investment_lot_allocation'
  ] LOOP
    ASSERT to_regclass('public.' || relation_name) IS NOT NULL,
      relation_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = relation_name),
      'RLS must be enabled on ' || relation_name;
  END LOOP;
  ASSERT NOT EXISTS (
    SELECT FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'transaction'
       AND column_name = 'investment_transaction_id'
  ), 'the legacy one-to-one investment cash link must be removed';
  ASSERT EXISTS (
    SELECT FROM pg_enum item
    JOIN pg_type kind ON kind.oid = item.enumtypid
     WHERE kind.typname = 'txn_type' AND item.enumlabel = 'investment_fee'
  ), 'investment_fee must be a ledger transaction type';
  ASSERT (
    SELECT array_agg(item.enumlabel::TEXT ORDER BY item.enumsortorder)
      FROM pg_enum item JOIN pg_type kind ON kind.oid = item.enumtypid
     WHERE kind.typname = 'investment_cash_allocation_role'
  ) = ARRAY['funding', 'proceeds', 'cash_distribution', 'fee'],
    'cash allocation roles must be the fixed four-value vocabulary';
  ASSERT investment_cash_type_for_role('funding') = 'investment_buy'
     AND investment_cash_type_for_role('proceeds') = 'investment_sell'
     AND investment_cash_type_for_role('cash_distribution') = 'investment_income'
     AND investment_cash_type_for_role('fee') = 'investment_fee',
    'each role must map to its one report-excluded transaction type';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_cash_allocation'::regclass
       AND conname = 'fk_investment_cash_allocation_cash_scope'
  ), 'cash allocations need a household-leading ledger foreign key';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_lot_allocation'::regclass
       AND conname = 'fk_investment_lot_allocation_lot_scope'
  ), 'lot allocations need a household-and-holding-leading lot foreign key';
  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 4
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_cash_allocation'
       AND column_name = 'allocated_amount'
  ), 'cash allocation money must be NUMERIC(20,4)';
  ASSERT (
    SELECT numeric_precision = 28 AND numeric_scale = 8
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_lot_allocation'
       AND column_name = 'allocated_units'
  ), 'lot allocation units must be NUMERIC(28,8)';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid IN (
             'confirm_investment_cash_allocations(uuid[],uuid[],investment_cash_allocation_role[],numeric[])'::regprocedure,
             'unlink_investment_cash_allocations(uuid)'::regprocedure,
             'allocate_investment_lots(uuid,uuid[],numeric[])'::regprocedure)
             AND prosecdef AND 'search_path=public, pg_temp' = ANY(proconfig)) = 3,
    'all allocation operations must be SECURITY DEFINER with a safe search path';
  ASSERT classification_state_is_valid(jsonb_build_object(
    'txn_type', 'investment_fee', 'category_id', NULL, 'merchant_id', NULL,
    'payment_method', NULL, 'classified_by', 'user_manual',
    'classification_rule_id', NULL, 'classification_confidence', NULL,
    'is_reviewed', true, 'country', NULL, 'tags', NULL, 'is_excluded', false
  )), 'classification snapshots must accept investment_fee';
  ASSERT (SELECT 'security_invoker=true' = ANY(reloptions)
            FROM pg_class WHERE relname = 'v_transaction_reportable'),
    'the recreated reportable view must retain invoker security';
  RAISE NOTICE 'PASS 0030 structure, exact scales, operations and legacy removal';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0030_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, p1 UUID, p2 UUID,
    investment_1 UUID, investment_2 UUID, cash_1 UUID, cash_2 UUID,
    cash_other_currency UUID, security_1 UUID, security_1b UUID, security_2 UUID,
    base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('allocation household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('allocation household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'allocation owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'allocation owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'allocation-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'allocation-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES
    (h1, p1, 'Synthetic allocation investment one', 'brokerage', base, 'XZ', 0, '2026-01-01')
    RETURNING id INTO investment_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES
    (h2, p2, 'Synthetic allocation investment two', 'brokerage', base, 'XY', 0, '2026-01-01')
    RETURNING id INTO investment_2;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES
    (h1, p1, 'Synthetic cash one', 'savings', base, 'XZ', 0, '2026-01-01')
    RETURNING id INTO cash_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES
    (h1, p1, 'Synthetic cash two', 'savings', base, 'XZ', 0, '2026-01-01')
    RETURNING id INTO cash_2;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES
    (h1, p1, 'Synthetic alternate cash', 'savings', secondary, 'XZ', 0, '2026-01-01')
    RETURNING id INTO cash_other_currency;

  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (h1, 'equity', 'Synthetic allocation security one', base, 'XZ', 'equity',
            'ALC1', 'SYNX') RETURNING id INTO security_1;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (h1, 'equity', 'Synthetic allocation security two', base, 'XZ', 'equity',
            'ALC2', 'SYNX') RETURNING id INTO security_1b;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (h2, 'equity', 'Synthetic allocation security three', base, 'XY', 'equity',
            'ALC3', 'SYNX') RETURNING id INTO security_2;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0030_cash(
  p_household UUID, p_account UUID, p_amount NUMERIC, p_currency CHAR(3),
  p_type txn_type, p_key TEXT
) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE result UUID;
BEGIN
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
    amount_secondary, txn_type, source, dedupe_hash
  ) VALUES (
    p_household, p_account, '2026-03-01', 'SYNTHETIC ALLOCATION CASH', p_amount,
    p_currency, '2026-03-01', 1.0000000000, 1.0000000000, p_amount,
    p_amount, p_type, 'manual', p_key
  ) RETURNING id INTO result;
  RETURN result;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0030_event(
  p_account UUID, p_security UUID, p_date DATE, p_kind investment_event_kind,
  p_units NUMERIC, p_gross NUMERIC, p_charges NUMERIC, p_tax NUMERIC,
  p_net NUMERIC, p_currency CHAR(3), p_key TEXT
) RETURNS UUID LANGUAGE sql AS $$
  SELECT append_investment_transaction(
    p_account, p_security, p_date, p_kind, p_units,
    CASE WHEN p_units IS NULL THEN NULL ELSE p_gross / abs(p_units) END,
    p_gross, p_charges, p_tax, p_net, p_currency, p_date, 1, 1, p_net, p_net,
    'owner_entered', 'allocation_v1', p_key, NULL, NULL, NULL, NULL)
$$;

-- Cash-only event append APIs arrive with the later lifecycle task. This test fixture inserts
-- their already-validated immutable shape so Task 4 can prove allocation behavior now.
CREATE OR REPLACE FUNCTION pg_temp.t0030_cash_event(
  p_origin UUID, p_date DATE, p_kind investment_event_kind, p_gross NUMERIC,
  p_charges NUMERIC, p_tax NUMERIC, p_net NUMERIC, p_key TEXT
) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE origin investment_transaction%ROWTYPE; result UUID;
BEGIN
  SELECT * INTO STRICT origin FROM investment_transaction WHERE id = p_origin;
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO investment_transaction (
    household_id, account_id, holding_id, security_id, event_date, event_kind,
    gross_amount, charges, withheld_tax, net_amount, currency, fx_rate_date,
    fx_rate_to_base, fx_rate_to_secondary, net_amount_base, net_amount_secondary,
    source_kind, source_contract, source_event_id
  ) VALUES (
    origin.household_id, origin.account_id, origin.holding_id, origin.security_id, p_date, p_kind,
    p_gross, p_charges, p_tax, p_net, origin.currency, p_date, 1, 1, p_net, p_net,
    'owner_entered', 'allocation_v1', p_key
  ) RETURNING id INTO result;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
  RETURN result;
END $$;

-- One event may use several cash leaves. Confirmation removes their ordinary flow, and unlink
-- restores each leaf's exact previous type without touching immutable money.
BEGIN;
DO $$
DECLARE f RECORD; event_id UUID; cash_a UUID; cash_b UUID;
        spend_before NUMERIC; spend_during NUMERIC; spend_after NUMERIC;
BEGIN
  SELECT * INTO f FROM pg_temp.t0030_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  event_id := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'allocation_v1', 'cash-many-event', NULL, NULL, NULL, NULL
  );
  cash_a := pg_temp.t0030_cash(f.h1, f.cash_1, -40.0000, f.base, 'expense', 'cash-many-a');
  cash_b := pg_temp.t0030_cash(f.h1, f.cash_2, -60.0000, f.base, 'fee', 'cash-many-b');
  SELECT -sum(amount_base) INTO spend_before FROM v_transaction_reportable
   WHERE id IN (cash_a, cash_b);

  PERFORM confirm_investment_cash_allocations(
    ARRAY[event_id, event_id], ARRAY[cash_a, cash_b],
    ARRAY['funding', 'funding']::investment_cash_allocation_role[],
    ARRAY[40.0000, 60.0000]
  );
  SELECT COALESCE(-sum(amount_base), 0) INTO spend_during FROM v_transaction_reportable
   WHERE id IN (cash_a, cash_b);
  ASSERT spend_before = 100.0000 AND spend_during = 0,
    'confirmed purchase funding must leave ordinary spending exactly once';
  ASSERT (SELECT count(*) = 2 FROM investment_cash_allocation
           WHERE investment_transaction_id = event_id),
    'one investment event may be funded by several cash leaves';
  ASSERT (SELECT txn_type = 'investment_buy' FROM transaction WHERE id = cash_a),
    'funding maps the cash leaf to investment_buy';
  ASSERT (SELECT prior_txn_type = 'fee' FROM investment_cash_allocation
           WHERE cash_transaction_id = cash_b),
    'each allocation retains the exact prior ledger type';

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  BEGIN
    PERFORM unlink_investment_cash_allocations(cash_b);
    RAISE EXCEPTION 'another household unlinked a cash allocation';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  ASSERT EXISTS (
    SELECT FROM investment_cash_allocation WHERE cash_transaction_id = cash_b
  ), 'a refused unlink must preserve the allocation';
  PERFORM set_config('app.user_id', f.u1::TEXT, true);

  PERFORM unlink_investment_cash_allocations(cash_b);
  SELECT COALESCE(-sum(amount_base), 0) INTO spend_after FROM v_transaction_reportable
   WHERE id IN (cash_a, cash_b);
  ASSERT (SELECT txn_type = 'fee' FROM transaction WHERE id = cash_b),
    'unlink restores the exact prior type rather than inferring from sign';
  ASSERT (SELECT txn_type = 'investment_buy' FROM transaction WHERE id = cash_a),
    'unlinking one fully allocated leaf does not disturb another';
  ASSERT spend_after = 60.0000,
    'only the unlinked fee leaf returns to ordinary reporting';
  RAISE NOTICE 'PASS 0030 many-to-many cash allocation and exact unlink restoration';
END $$;
ROLLBACK;

-- Every cash role confirms/unlinks symmetrically; one complete same-role leaf may fund many events.
BEGIN;
DO $$
DECLARE f RECORD; purchase UUID; sale UUID; distribution UUID; fee_a UUID; fee_b UUID;
        sale_cash UUID; distribution_cash UUID; fee_cash UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0030_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase := pg_temp.t0030_event(f.investment_1, f.security_1, '2026-03-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'role-origin');
  sale := pg_temp.t0030_event(f.investment_1, f.security_1, '2026-03-02',
    'sale', -2, 12, 0, 0, 12, f.base, 'role-sale');
  distribution := pg_temp.t0030_cash_event(purchase, '2026-03-03',
    'cash_distribution', 3, 0, 0, 3, 'role-distribution');
  fee_a := pg_temp.t0030_cash_event(purchase, '2026-03-04',
    'charge', NULL, 1, 0, -1, 'role-fee-a');
  fee_b := pg_temp.t0030_cash_event(purchase, '2026-03-05',
    'charge', NULL, 2, 0, -2, 'role-fee-b');
  sale_cash := pg_temp.t0030_cash(f.h1, f.cash_1, 12, f.base, 'income', 'role-cash-sale');
  distribution_cash := pg_temp.t0030_cash(f.h1, f.cash_1, 3, f.base, 'refund',
    'role-cash-distribution');
  fee_cash := pg_temp.t0030_cash(f.h1, f.cash_1, -3, f.base, 'fee', 'role-cash-fee');

  PERFORM confirm_investment_cash_allocations(ARRAY[sale], ARRAY[sale_cash],
    ARRAY['proceeds']::investment_cash_allocation_role[], ARRAY[12]);
  PERFORM confirm_investment_cash_allocations(ARRAY[distribution], ARRAY[distribution_cash],
    ARRAY['cash_distribution']::investment_cash_allocation_role[], ARRAY[3]);
  PERFORM confirm_investment_cash_allocations(ARRAY[fee_a, fee_b], ARRAY[fee_cash, fee_cash],
    ARRAY['fee', 'fee']::investment_cash_allocation_role[], ARRAY[1, 2]);
  ASSERT (SELECT count(*) = 4
      AND count(*) FILTER (WHERE role = 'proceeds' AND cash.txn_type = 'investment_sell') = 1
      AND count(*) FILTER (WHERE role = 'cash_distribution'
                           AND cash.txn_type = 'investment_income') = 1
      AND count(*) FILTER (WHERE role = 'fee' AND cash.txn_type = 'investment_fee') = 2
    FROM investment_cash_allocation JOIN transaction cash ON cash.id = cash_transaction_id
    WHERE cash.id IN (sale_cash, distribution_cash, fee_cash)),
    'every non-funding role maps to its report-excluded type';
  ASSERT NOT EXISTS (SELECT FROM v_transaction_reportable
                      WHERE id IN (sale_cash, distribution_cash, fee_cash)),
    'every allocated investment role is outside ordinary reporting';
  ASSERT (SELECT count(*) = 2 FROM investment_cash_allocation WHERE cash_transaction_id = fee_cash),
    'one cash leaf may allocate to several investment events';
  PERFORM unlink_investment_cash_allocations(cash)
    FROM unnest(ARRAY[sale_cash, distribution_cash, fee_cash]) cash;
  ASSERT (SELECT array_agg(txn_type::TEXT ORDER BY txn_type::TEXT) FROM transaction WHERE id IN
          (sale_cash, distribution_cash, fee_cash)) = ARRAY['fee', 'income', 'refund'],
    'unlink restores every role leaf to its exact prior type';
  RAISE NOTICE 'PASS 0030 all cash roles and one leaf to many events';
END $$;
ROLLBACK;

-- Confirmation is atomic: every cash leaf is full, each event stays within its cash side, roles
-- cannot mix, currencies and signs agree, and the caller owns every referenced row.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; sale_id UUID; foreign_event UUID;
        cash_full UUID; cash_small UUID; cash_foreign UUID; cash_wrong_sign UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0030_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'allocation_v1', 'cash-refusal-buy', NULL, NULL, NULL, NULL
  );
  sale_id := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-02', 'sale',
    -2.00000000, 10.000000, 20.0000, 0.0000, 0.0000, 20.0000, f.base,
    '2026-03-02', 1.0000000000, 1.0000000000, 20.0000, 20.0000,
    'owner_entered', 'allocation_v1', 'cash-refusal-sale', NULL, NULL, NULL, NULL
  );
  cash_full := pg_temp.t0030_cash(f.h1, f.cash_1, -100.0000, f.base, 'expense', 'cash-refusal-full');
  cash_small := pg_temp.t0030_cash(f.h1, f.cash_2, -60.0000, f.base, 'expense', 'cash-refusal-small');
  cash_foreign := pg_temp.t0030_cash(
    f.h1, f.cash_other_currency, -100.0000, f.secondary, 'expense', 'cash-refusal-currency');
  cash_wrong_sign := pg_temp.t0030_cash(
    f.h1, f.cash_2, 100.0000, f.base, 'income', 'cash-refusal-sign');

  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[purchase_id], ARRAY[cash_full], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[99.0000]);
    RAISE EXCEPTION 'a partial cash leaf was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_cash_leaf_is_fully_allocated', refusal;
  END;
  PERFORM confirm_investment_cash_allocations(
    ARRAY[purchase_id], ARRAY[cash_small], ARRAY['funding']::investment_cash_allocation_role[],
    ARRAY[60.0000]);
  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[purchase_id], ARRAY[cash_full], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[100.0000]);
    RAISE EXCEPTION 'an investment event was over-allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_cash_is_not_overallocated', refusal;
  END;
  ASSERT (SELECT count(*) = 1 FROM investment_cash_allocation),
    'the rejected second confirmation must leave the first allocation intact';
  ASSERT (SELECT txn_type = 'expense' FROM transaction WHERE id = cash_full),
    'an atomic refusal cannot reclassify its cash row';

  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[purchase_id, sale_id], ARRAY[cash_full, cash_full],
      ARRAY['funding', 'proceeds']::investment_cash_allocation_role[],
      ARRAY[80.0000, 20.0000]);
    RAISE EXCEPTION 'mixed roles were accepted on one cash row';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_cash_leaf_has_one_role', refusal;
  END;
  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[purchase_id], ARRAY[cash_foreign], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[100.0000]);
    RAISE EXCEPTION 'a direct cross-currency allocation was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_cash_currency_matches_event', refusal;
  END;
  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[purchase_id], ARRAY[cash_wrong_sign],
      ARRAY['funding']::investment_cash_allocation_role[], ARRAY[100.0000]);
    RAISE EXCEPTION 'funding accepted a positive cash row';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_cash_role_matches_event', refusal;
  END;
  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[purchase_id], ARRAY[cash_full], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[100.00001]);
    RAISE EXCEPTION 'cash allocation precision was silently rounded';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%cash allocation money exceeds scale 4%', refusal;
  END;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  foreign_event := append_investment_transaction(
    f.investment_2, f.security_2, '2026-03-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
    'owner_entered', 'allocation_v1', 'cash-foreign-event', NULL, NULL, NULL, NULL
  );
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[foreign_event], ARRAY[cash_full], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[100.0000]);
    RAISE EXCEPTION 'a foreign investment event was allocated';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  RAISE NOTICE 'PASS 0030 cash allocation refusals are exact and atomic';
END $$;
ROLLBACK;

-- A split parent is never cash evidence; an exactly allocated split child is a leaf and may be
-- reclassified without weakening the one-level or exact-sum split invariants.
BEGIN;
GRANT UPDATE (txn_type) ON transaction TO authenticated;
GRANT EXECUTE ON FUNCTION investment_cash_type_for_role(investment_cash_allocation_role)
  TO authenticated;
DO $$
DECLARE f RECORD; event_id UUID; parent_id UUID; child_investment UUID; child_ordinary UUID;
        allocated_event UUID; allocated_leaf UUID; refusal TEXT; reported NUMERIC;
BEGIN
  SET CONSTRAINTS ALL IMMEDIATE;
  SELECT * INTO f FROM pg_temp.t0030_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  event_id := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-01', 'purchase',
    4.00000000, 10.000000, 40.0000, 0.0000, 0.0000, -40.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, -40.0000, -40.0000,
    'owner_entered', 'allocation_v1', 'split-event', NULL, NULL, NULL, NULL
  );
  parent_id := pg_temp.t0030_cash(f.h1, f.cash_1, -100.0000, f.base, 'expense', 'split-parent');
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
    amount_secondary, txn_type, source, dedupe_hash, parent_transaction_id
  ) VALUES
    (f.h1, f.cash_1, '2026-03-01', 'SYNTHETIC ALLOCATION CASH', -40.0000, f.base,
     '2026-03-01', 1, 1, -40.0000, -40.0000, 'expense', 'manual', 'split-child-a', parent_id),
    (f.h1, f.cash_1, '2026-03-01', 'SYNTHETIC ALLOCATION CASH', -60.0000, f.base,
     '2026-03-01', 1, 1, -60.0000, -60.0000, 'expense', 'manual', 'split-child-b', parent_id);
  SELECT id INTO child_investment FROM transaction WHERE dedupe_hash = 'split-child-a';
  SELECT id INTO child_ordinary FROM transaction WHERE dedupe_hash = 'split-child-b';

  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[event_id], ARRAY[parent_id], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[100.0000]);
    RAISE EXCEPTION 'a split parent was accepted as cash evidence';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_cash_transaction_is_leaf', refusal;
  END;
  PERFORM confirm_investment_cash_allocations(
    ARRAY[event_id], ARRAY[child_investment],
    ARRAY['funding']::investment_cash_allocation_role[], ARRAY[40.0000]);
  SET CONSTRAINTS ALL IMMEDIATE;
  SELECT -sum(amount_base) INTO reported FROM v_transaction_reportable
   WHERE id IN (parent_id, child_investment, child_ordinary);
  ASSERT reported = 60.0000,
    'the ordinary split child reports while the investment child does not';
  ASSERT (SELECT txn_type = 'investment_buy' FROM transaction WHERE id = child_investment),
    'an allocated split child carries the investment classification';
  ASSERT (SELECT txn_type = 'expense' FROM transaction WHERE id = child_ordinary),
    'the sibling keeps its ordinary classification';
  BEGIN
    SET LOCAL ROLE authenticated;
    PERFORM set_config('oneledger.investment_cash_allocation_write', 'on', true);
    UPDATE transaction SET txn_type = 'investment_buy' WHERE id = child_ordinary;
    RESET ROLE;
  EXCEPTION WHEN raise_exception THEN NULL;
  END;
  ASSERT (SELECT txn_type = 'expense' FROM transaction WHERE id = child_ordinary),
    'a caller-set flag cannot reclassify an unallocated split child';
  SELECT -sum(amount_base) INTO reported FROM v_transaction_reportable
   WHERE id IN (parent_id, child_investment, child_ordinary);
  ASSERT reported = 60.0000, 'a spoofed split-child change cannot alter reportable flow';

  allocated_event := pg_temp.t0030_event(f.investment_1, f.security_1, '2026-03-02',
    'purchase', 2, 20, 0, 0, -20, f.base, 'allocated-parent-event');
  allocated_leaf := pg_temp.t0030_cash(f.h1, f.cash_2, -20, f.base, 'expense',
    'allocated-parent-cash');
  PERFORM confirm_investment_cash_allocations(ARRAY[allocated_event], ARRAY[allocated_leaf],
    ARRAY['funding']::investment_cash_allocation_role[], ARRAY[20]);
  BEGIN
    INSERT INTO transaction (
      household_id, account_id, booked_date, description_raw, amount, currency,
      fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
      amount_secondary, txn_type, source, dedupe_hash, parent_transaction_id
    ) VALUES
      (f.h1, f.cash_2, '2026-03-01', 'SYNTHETIC ALLOCATION CASH', -8, f.base,
       '2026-03-01', 1, 1, -8, -8, 'investment_buy', 'manual', 'stranded-child-a', allocated_leaf),
      (f.h1, f.cash_2, '2026-03-01', 'SYNTHETIC ALLOCATION CASH', -12, f.base,
       '2026-03-01', 1, 1, -12, -12, 'investment_buy', 'manual', 'stranded-child-b', allocated_leaf);
    RAISE EXCEPTION 'an allocated leaf became a split parent';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_cash_transaction_must_remain_leaf', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM transaction WHERE parent_transaction_id = allocated_leaf),
    'refusing the split leaves no child behind';
  PERFORM unlink_investment_cash_allocations(allocated_leaf);
  ASSERT (SELECT txn_type = 'expense' FROM transaction WHERE id = allocated_leaf),
    'the still-leaf cash row remains unlinkable';
  RAISE NOTICE 'PASS 0030 split parents refuse and allocated split leaves remain exact';
END $$;
ROLLBACK;

-- Lot allocation records only evidenced units against a disposal and its own holding's origins.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; later_purchase UUID; other_purchase UUID; foreign_purchase UUID;
        sale_one UUID; sale_two UUID; foreign_lot UUID;
        lot_id UUID; later_lot UUID; other_lot UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0030_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.investment_1, f.security_1, '2026-02-01', 'purchase',
    6.00000000, 5.000000, 30.0000, 0.0000, 0.0000, -30.0000, f.base,
    '2026-02-01', 1, 1, -30.0000, -30.0000,
    'owner_entered', 'allocation_v1', 'lot-origin-one', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.investment_1, f.security_1, '2026-02-02', 'purchase',
    4.00000000, 5.000000, 20.0000, 0.0000, 0.0000, -20.0000, f.base,
    '2026-02-02', 1, 1, -20.0000, -20.0000,
    'owner_entered', 'allocation_v1', 'lot-origin-one-b', NULL, NULL, NULL, NULL
  );
  other_purchase := append_investment_transaction(
    f.investment_1, f.security_1b, '2026-02-01', 'purchase',
    3.00000000, 5.000000, 15.0000, 0.0000, 0.0000, -15.0000, f.base,
    '2026-02-01', 1, 1, -15.0000, -15.0000,
    'owner_entered', 'allocation_v1', 'lot-origin-two', NULL, NULL, NULL, NULL
  );
  sale_one := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-01', 'sale',
    -4.00000000, 6.000000, 24.0000, 0.0000, 0.0000, 24.0000, f.base,
    '2026-03-01', 1, 1, 24.0000, 24.0000,
    'owner_entered', 'allocation_v1', 'lot-sale-one', NULL, NULL, NULL, NULL
  );
  sale_two := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-02', 'sale',
    -4.00000000, 6.000000, 24.0000, 0.0000, 0.0000, 24.0000, f.base,
    '2026-03-02', 1, 1, 24.0000, 24.0000,
    'owner_entered', 'allocation_v1', 'lot-sale-two', NULL, NULL, NULL, NULL
  );
  later_purchase := pg_temp.t0030_event(f.investment_1, f.security_1, '2026-03-03',
    'purchase', 1, 5, 0, 0, -5, f.base, 'lot-origin-later');
  SELECT id INTO lot_id FROM acquisition_lot WHERE origin_transaction_id = purchase_id;
  SELECT id INTO later_lot FROM acquisition_lot WHERE origin_transaction_id = later_purchase;
  SELECT id INTO other_lot FROM acquisition_lot WHERE origin_transaction_id = other_purchase;

  PERFORM allocate_investment_lots(sale_one, ARRAY[lot_id], ARRAY[4.00000000]);
  ASSERT EXISTS (
    SELECT FROM investment_lot_allocation allocation
    JOIN acquisition_lot lot ON lot.id = allocation.acquisition_lot_id
     WHERE allocation.disposal_transaction_id = sale_one
       AND allocation.allocated_units = 4.00000000
       AND lot.origin_transaction_id = purchase_id
  ), 'a lot allocation retains its acquisition origin';

  BEGIN
    PERFORM allocate_investment_lots(sale_two, ARRAY[later_lot], ARRAY[1.00000000]);
    RAISE EXCEPTION 'a post-disposal acquisition lot was allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_precedes_disposal', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM investment_lot_allocation WHERE acquisition_lot_id = later_lot),
    'the temporal refusal is atomic';

  BEGIN
    PERFORM allocate_investment_lots(sale_two, ARRAY[other_lot], ARRAY[1.00000000]);
    RAISE EXCEPTION 'a lot from another holding was allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_matches_disposal_holding', refusal;
  END;
  BEGIN
    PERFORM allocate_investment_lots(purchase_id, ARRAY[lot_id], ARRAY[1.00000000]);
    RAISE EXCEPTION 'an acquisition was used as a disposal';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_allocation_uses_disposal', refusal;
  END;
  BEGIN
    PERFORM allocate_investment_lots(sale_two, ARRAY[lot_id], ARRAY[4.000000001]);
    RAISE EXCEPTION 'lot allocation precision was silently rounded';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%lot allocation units exceeds scale 8%', refusal;
  END;
  BEGIN
    PERFORM allocate_investment_lots(sale_two, ARRAY[lot_id], ARRAY[5.00000000]);
    RAISE EXCEPTION 'a disposal was over-allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_disposal_units_are_not_overallocated', refusal;
  END;
  BEGIN
    PERFORM allocate_investment_lots(sale_two, ARRAY[lot_id], ARRAY[3.00000000]);
    RAISE EXCEPTION 'an acquisition lot was over-allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_units_are_not_overallocated', refusal;
  END;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  foreign_purchase := append_investment_transaction(
    f.investment_2, f.security_2, '2026-02-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-02-01', 1, 1, -5.0000, -5.0000,
    'owner_entered', 'allocation_v1', 'lot-foreign-origin', NULL, NULL, NULL, NULL
  );
  SELECT id INTO foreign_lot FROM acquisition_lot
   WHERE origin_transaction_id = foreign_purchase;
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM allocate_investment_lots(sale_two, ARRAY[foreign_lot], ARRAY[1.00000000]);
    RAISE EXCEPTION 'another household acquisition lot was allocated';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  RAISE NOTICE 'PASS 0030 lot origins, holdings, precision and disposal capacity';
END $$;
ROLLBACK;

-- Allocation rows cannot bypass the operations, and household RLS hides all foreign facts.
BEGIN;
GRANT UPDATE (txn_type) ON transaction TO authenticated;
DO $$
DECLARE f RECORD; event_one UUID; event_two UUID; cash_one UUID; cash_two UUID;
        holding_one UUID; lot_one UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0030_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  event_one := append_investment_transaction(
    f.investment_1, f.security_1, '2026-03-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-03-01', 1, 1, -5.0000, -5.0000,
    'owner_entered', 'allocation_v1', 'rls-event-one', NULL, NULL, NULL, NULL
  );
  cash_one := pg_temp.t0030_cash(f.h1, f.cash_1, -5.0000, f.base, 'expense', 'rls-cash-one');
  PERFORM confirm_investment_cash_allocations(
    ARRAY[event_one], ARRAY[cash_one], ARRAY['funding']::investment_cash_allocation_role[],
    ARRAY[5.0000]);
  SELECT holding_id INTO holding_one FROM investment_transaction WHERE id = event_one;
  SELECT id INTO lot_one FROM acquisition_lot WHERE origin_transaction_id = event_one;

  BEGIN
    INSERT INTO investment_cash_allocation (
      household_id, investment_transaction_id, cash_transaction_id, role,
      allocated_amount, prior_txn_type, allocated_by
    ) VALUES (f.h1, event_one, cash_one, 'funding', 1.0000, 'expense', f.u1);
    RAISE EXCEPTION 'direct cash-allocation DML bypassed the operation';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%must use confirm_investment_cash_allocations%', refusal;
  END;
  BEGIN
    UPDATE transaction SET txn_type = 'expense' WHERE id = cash_one;
    RAISE EXCEPTION 'direct DML changed an allocated cash classification';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification changes require the allocation operations%', refusal;
  END;
  BEGIN
    PERFORM set_config('oneledger.investment_cash_allocation_write', 'on', true);
    SET LOCAL ROLE authenticated;
    UPDATE transaction SET txn_type = 'expense' WHERE id = cash_one;
    RESET ROLE;
    RAISE EXCEPTION 'an authenticated caller spoofed the internal allocation flag';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification changes require the allocation operations%', refusal;
  END;
  BEGIN
    INSERT INTO investment_lot_allocation (
      household_id, holding_id, disposal_transaction_id, acquisition_lot_id,
      allocated_units, allocated_by
    ) VALUES (f.h1, holding_one, event_one, lot_one, 1.00000000, f.u1);
    RAISE EXCEPTION 'direct lot-allocation DML bypassed the operation';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%must use allocate_investment_lots%', refusal;
  END;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  event_two := append_investment_transaction(
    f.investment_2, f.security_2, '2026-03-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-03-01', 1, 1, -5.0000, -5.0000,
    'owner_entered', 'allocation_v1', 'rls-event-two', NULL, NULL, NULL, NULL
  );
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (f.h2, f.p2, 'Synthetic foreign cash', 'savings', f.base, 'XY', 0, '2026-01-01')
  RETURNING id INTO cash_two;
  cash_two := pg_temp.t0030_cash(f.h2, cash_two, -5.0000, f.base, 'expense', 'rls-cash-two');
  PERFORM confirm_investment_cash_allocations(
    ARRAY[event_two], ARRAY[cash_two], ARRAY['funding']::investment_cash_allocation_role[],
    ARRAY[5.0000]);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM investment_cash_allocation) = 1,
    'an owner sees only household cash allocations';
  ASSERT (SELECT count(*) FROM investment_lot_allocation) = 0,
    'an owner sees no foreign lot allocations';
  RESET ROLE;
  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM investment_cash_allocation) = 0,
    'anonymous readers see no cash allocations';
  ASSERT (SELECT count(*) FROM investment_lot_allocation) = 0,
    'anonymous readers see no lot allocations';
  RESET ROLE;
  RAISE NOTICE 'PASS 0030 direct DML fails closed and allocation RLS isolates households';
END $$;
ROLLBACK;
