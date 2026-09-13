-- V40: lot lineage read model, unit-event order guard and active-event capacity.
-- All names, identifiers and amounts below are synthetic. No operation yet creates lineage rows;
-- fixtures below create them only under the append guard, as the plan requires.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT EXISTS (
    SELECT FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'acquisition_lot'
       AND column_name = 'predecessor_lot_id' AND is_nullable = 'YES'
  ), 'acquisition_lot needs a nullable predecessor_lot_id';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'acquisition_lot'::regclass
       AND conname = 'fk_acquisition_lot_predecessor_scope'
       AND pg_get_constraintdef(oid) LIKE
         'FOREIGN KEY (household_id, predecessor_lot_id)%'
  ), 'a lot predecessor needs a household-scoped foreign key spanning any holding';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'acquisition_lot'::regclass
       AND conname = 'uq_acquisition_lot_household_id' AND contype = 'u'
  ), 'a household-scoped self reference needs (household_id, id) uniqueness';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'acquisition_lot'::regclass
       AND conname = 'uq_acquisition_lot_origin_predecessor' AND contype = 'u'
  ), 'one successor per predecessor per lineage event must be unique';
  ASSERT EXISTS (
    SELECT FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_transaction'
       AND column_name = 'counterpart_transaction_id' AND is_nullable = 'YES'
  ), 'investment_transaction needs a nullable counterpart_transaction_id';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_transaction'::regclass
       AND conname = 'fk_investment_transaction_counterpart_scope'
  ), 'a counterpart needs a household-scoped foreign key';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_transaction'::regclass
       AND conname = 'investment_event_counterpart_agrees' AND contype = 'c'
  ), 'counterpart presence must agree with paired *_in kinds';
  ASSERT EXISTS (
    SELECT FROM pg_indexes
     WHERE tablename = 'investment_transaction'
       AND indexname = 'idx_investment_event_counterpart'
  ), 'a counterpart reference must be unique';
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid IN (
             'investment_lot_lineage_available(uuid,date)'::regprocedure,
             'investment_open_lots(uuid,date)'::regprocedure)) = 2,
    'the lineage read model functions must exist';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'investment_transaction'::regclass
       AND tgname = 'trg_investment_transaction_order_guard'
  ), 'a unit-event order guard must exist on investment_transaction';
  RAISE NOTICE 'PASS 0037 structure and read-model functions exist';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0037_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    account_1 UUID, account_2 UUID, security_1 UUID, security_2 UUID,
    base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('lineage household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('lineage household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'lineage owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'lineage member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other lineage owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'lineage-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'lineage-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-lineage-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h1, p1, 'Synthetic lineage account one', 'brokerage', base, 'XZ', 0, '2025-01-01')
  RETURNING id INTO account_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h1, p1, 'Synthetic lineage account two', 'brokerage', base, 'XZ', 0, '2025-01-01')
  RETURNING id INTO account_2;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h1, 'equity', 'Synthetic lineage security one', base, 'XZ', 'equity')
  RETURNING id INTO security_1;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h1, 'equity', 'Synthetic lineage security two', base, 'XZ', 'equity')
  RETURNING id INTO security_2;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0037_event(
  p_account UUID, p_security UUID, p_date DATE, p_kind investment_event_kind,
  p_units NUMERIC, p_gross NUMERIC, p_charges NUMERIC, p_tax NUMERIC,
  p_net NUMERIC, p_currency CHAR(3), p_key TEXT
) RETURNS UUID LANGUAGE sql AS $$
  SELECT append_investment_transaction(
    p_account, p_security, p_date, p_kind, p_units,
    CASE WHEN p_units IS NULL THEN NULL ELSE p_gross / abs(p_units) END,
    p_gross, p_charges, p_tax, p_net, p_currency, p_date, 1, 1, p_net, p_net,
    'statement', 'lineage_v1', p_key, NULL, NULL, NULL, NULL)
$$;

-- Fixtures create a split/platform-transfer row only by setting the append guard directly,
-- since no owner operation creates lineage rows until Task 15.2/15.4.
CREATE OR REPLACE FUNCTION pg_temp.t0037_split(
  p_household UUID, p_account UUID, p_holding UUID, p_security UUID,
  p_date DATE, p_ratio NUMERIC, p_key TEXT,
  p_corrects UUID DEFAULT NULL, p_reason TEXT DEFAULT NULL
) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE result UUID; event_currency CHAR(3);
BEGIN
  SELECT currency INTO event_currency FROM security WHERE id = p_security;
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  -- The exact unit-effect value plays no role in these lineage fixtures; any nonzero value
  -- satisfies Task 15.2's investment_event_kind_fields_agree shape for a split row.
  INSERT INTO investment_transaction (
    household_id, account_id, holding_id, security_id, event_date, event_kind,
    units, unit_ratio, net_amount, currency, source_kind, source_contract,
    dedupe_version, dedupe_fingerprint, corrects_transaction_id, correction_reason
  ) VALUES (
    p_household, p_account, p_holding, p_security, p_date, 'split',
    1, p_ratio, 0, event_currency,
    CASE WHEN p_corrects IS NULL THEN 'statement' ELSE 'owner_entered' END::investment_event_source,
    'lineage_v1', 'v1', rpad(md5(p_key), 64, '0'), p_corrects, p_reason
  ) RETURNING id INTO result;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
  RETURN result;
END $$;

-- A successor lot fixture: keeps the predecessor's acquisition date, per the basis rule.
CREATE OR REPLACE FUNCTION pg_temp.t0037_successor(
  p_household UUID, p_holding UUID, p_origin UUID, p_predecessor UUID,
  p_units NUMERIC, p_basis NUMERIC
) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE result UUID; predecessor_date DATE; predecessor_currency CHAR(3);
BEGIN
  SELECT acquisition_date, currency INTO predecessor_date, predecessor_currency
    FROM acquisition_lot WHERE id = p_predecessor;
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO acquisition_lot (
    household_id, holding_id, origin_transaction_id, acquisition_date,
    acquired_units, cost_basis_native, currency, predecessor_lot_id
  ) VALUES (
    p_household, p_holding, p_origin, predecessor_date, p_units, p_basis,
    predecessor_currency, p_predecessor
  ) RETURNING id INTO result;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
  RETURN result;
END $$;

-- Open lots exclude a lot once an active split consumes it, follow the tip across a further
-- as-of date, and restore the predecessor once the consuming split is itself corrected.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID;
  split_id UUID; successor_id UUID; corrected_split_id UUID; second_successor_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;

  ASSERT (SELECT count(*) FROM investment_open_lots(holding_id, '2026-02-15')) = 1
     AND (SELECT open_units = 10 AND open_basis_native = 100.0000
            FROM investment_open_lots(holding_id, '2026-02-15')),
    'an unsplit lot is fully open before any lineage event';

  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-03-01', 2, 'lineage-split-one');
  successor_id := pg_temp.t0037_successor(f.h1, holding_id, split_id, lot_id, 20, 100.0000);

  ASSERT investment_lot_lineage_available(holding_id, '2026-03-01'),
    'a split with a successor lot keeps lineage available';
  ASSERT (SELECT acquisition_lot_id = successor_id AND open_units = 20
                 AND open_basis_native = 100.0000
            FROM investment_open_lots(holding_id, '2026-03-01')),
    'an active split excludes its consumed predecessor and reports its successor';
  ASSERT NOT EXISTS (
    SELECT FROM investment_open_lots(holding_id, '2026-03-01') WHERE acquisition_lot_id = lot_id
  ), 'a lot consumed by an active split is not open as of the split date';
  ASSERT (SELECT acquisition_lot_id = lot_id AND open_units = 10 AND open_basis_native = 100.0000
            FROM investment_open_lots(holding_id, '2026-02-15')),
    'an as-of date before the split still returns the predecessor once the split row exists';

  corrected_split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-04-01', 3, 'lineage-split-two', split_id, 'replace ratio');
  ASSERT (SELECT acquisition_lot_id = lot_id AND open_units = 10
                 AND open_basis_native = 100.0000
            FROM investment_open_lots(holding_id, '2026-03-15')),
    'capacity is restored once the consuming split is corrected';

  second_successor_id := pg_temp.t0037_successor(
    f.h1, holding_id, corrected_split_id, lot_id, 30, 100.0000);
  ASSERT (SELECT acquisition_lot_id = second_successor_id AND open_units = 30
                 AND open_basis_native = 100.0000
            FROM investment_open_lots(holding_id, '2026-04-01')),
    'the replacement split becomes the active consumer from its own date';
  RAISE NOTICE 'PASS 0037 open lots exclude consumed lots and restore capacity after correction';
END $$;
ROLLBACK;

-- A split without a successor withholds open lots and disposal allocation from its date, but
-- not before it; existing non-lineage allocation is unaffected.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID; split_id UUID;
  early_sale_id UUID; late_sale_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-incomplete-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;
  early_sale_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-02-01',
    'sale', -3, 9, 0, 0, 9, f.base, 'lineage-incomplete-early-sale');
  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-03-01', 2, 'lineage-incomplete-split');
  late_sale_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-03-02',
    'sale', -2, 6, 0, 0, 6, f.base, 'lineage-incomplete-late-sale');

  ASSERT investment_lot_lineage_available(holding_id, '2026-02-15'),
    'lineage is available before an incomplete split';
  ASSERT NOT investment_lot_lineage_available(holding_id, '2026-03-01'),
    'an active split with no successor lot leaves lineage unavailable';
  ASSERT (SELECT count(*) FROM investment_open_lots(holding_id, '2026-01-15')) = 1,
    'open lots are visible before the incomplete split';
  ASSERT (SELECT count(*) FROM investment_open_lots(holding_id, '2026-03-02')) = 0,
    'open lots withhold entirely once lineage is unavailable';

  PERFORM allocate_investment_lots(early_sale_id, ARRAY[lot_id], ARRAY[3.00000000]);
  ASSERT (SELECT count(*) FROM investment_lot_allocation
           WHERE disposal_transaction_id = early_sale_id) = 1,
    'a disposal dated before the incomplete split allocates normally';

  BEGIN
    PERFORM allocate_investment_lots(late_sale_id, ARRAY[lot_id], ARRAY[2.00000000]);
    RAISE EXCEPTION 'a disposal dated on or after an unavailable-lineage split was allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_allocation_requires_available_lineage', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_lot_allocation WHERE disposal_transaction_id = late_sale_id
  ), 'the withheld-lineage refusal is atomic';
  RAISE NOTICE 'PASS 0037 withheld lineage refuses open lots and disposal allocation';
END $$;
ROLLBACK;

-- A successor lot cannot fund a disposal dated before the lineage event that created it, even
-- when the disposal itself predates the fixture's later-inserted split.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID;
  early_sale_id UUID; split_id UUID; successor_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-precedes-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;
  early_sale_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-10',
    'sale', -3, 9, 0, 0, 9, f.base, 'lineage-precedes-sale');
  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-01-20', 2, 'lineage-precedes-split');
  successor_id := pg_temp.t0037_successor(f.h1, holding_id, split_id, lot_id, 20, 100.0000);

  BEGIN
    PERFORM allocate_investment_lots(early_sale_id, ARRAY[successor_id], ARRAY[3.00000000]);
    RAISE EXCEPTION 'a successor lot funded a disposal before its creating event';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_succeeds_creating_event', refusal;
  END;
  RAISE NOTICE 'PASS 0037 a successor lot cannot fund a disposal before its creating event';
END $$;
ROLLBACK;

-- An allocation from a disposal dated after the as-of date does not reduce open units.
BEGIN;
DO $$
DECLARE f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID; disposal_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-as-of-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;
  disposal_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-20',
    'sale', -3, 9, 0, 0, 9, f.base, 'lineage-as-of-sale');
  PERFORM allocate_investment_lots(disposal_id, ARRAY[lot_id], ARRAY[3.00000000]);

  ASSERT (SELECT open_units = 10 FROM investment_open_lots(holding_id, '2026-01-10')),
    'a disposal dated after the as-of date does not reduce open units';
  ASSERT (SELECT open_units = 7 FROM investment_open_lots(holding_id, '2026-01-20')),
    'a disposal dated on or before the as-of date reduces open units';
  RAISE NOTICE 'PASS 0037 open units exclude an allocation from a disposal dated after as-of';
END $$;
ROLLBACK;

-- Open basis rounds from an exact wide intermediate product, not PostgreSQL's truncated
-- division-scale heuristic: basis 5.0000 times open 99998.99999999 over acquired 99999.99999999
-- is 4.99994999949999..., which rounds to 4.9999, not the naively truncated 5.0000.
BEGIN;
DO $$
DECLARE f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID; disposal_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase', 99999.99999999, 0.000050, 5, 0, 0, -5,
    f.base, '2026-01-01', 1, 1, -5, -5, 'statement', 'lineage_v1', 'lineage-rounding-purchase',
    NULL, NULL, NULL, NULL
  );
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;
  disposal_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-20',
    'sale', -1, 1, 0, 0, 1, f.base, 'lineage-rounding-sale');
  PERFORM allocate_investment_lots(disposal_id, ARRAY[lot_id], ARRAY[1.00000000]);

  ASSERT (SELECT open_units = 99998.99999999 AND open_basis_native = 4.9999
            FROM investment_open_lots(holding_id, '2026-01-20')),
    'open basis rounds from an exact product, not a truncated intermediate division';
  RAISE NOTICE 'PASS 0037 open basis rounds exactly rather than truncating first';
END $$;
ROLLBACK;

-- Allocation refuses a predecessor consumed by an active lineage event, exactly like the read
-- model; capacity is restored, and proven through a real allocation, once the split is corrected.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID; other_purchase_id UUID;
  other_lot_id UUID; split_id UUID; successor_id UUID; disposal_id UUID;
  corrected_split_id UUID; other_successor_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-consumed-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;
  other_purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 5, 50, 0, 0, -50, f.base, 'lineage-consumed-other-purchase');
  SELECT lot.id INTO other_lot_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = other_purchase_id;

  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-02-01', 2, 'lineage-consumed-split');
  successor_id := pg_temp.t0037_successor(f.h1, holding_id, split_id, lot_id, 20, 100.0000);
  disposal_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-03-01',
    'sale', -3, 9, 0, 0, 9, f.base, 'lineage-consumed-sale');

  BEGIN
    PERFORM allocate_investment_lots(disposal_id, ARRAY[lot_id], ARRAY[3.00000000]);
    RAISE EXCEPTION 'a predecessor consumed by an active split funded a disposal';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_consumed_by_lineage', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_lot_allocation WHERE disposal_transaction_id = disposal_id
  ), 'the consumed-predecessor refusal is atomic';

  -- Correct the split so it no longer consumes the original lot at all; it keeps lineage
  -- available by consuming the second lot instead, proving capacity is genuinely restored rather
  -- than merely reported unavailable.
  corrected_split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-02-01', 2, 'lineage-consumed-split-correction', split_id, 'consume the other lot');
  other_successor_id := pg_temp.t0037_successor(
    f.h1, holding_id, corrected_split_id, other_lot_id, 10, 50.0000);

  ASSERT investment_lot_lineage_available(holding_id, '2026-03-01'),
    'the replacement split keeps lineage available via its own successor';
  PERFORM allocate_investment_lots(disposal_id, ARRAY[lot_id], ARRAY[3.00000000]);
  ASSERT (SELECT count(*) FROM investment_lot_allocation
           WHERE disposal_transaction_id = disposal_id AND acquisition_lot_id = lot_id) = 1,
    'capacity is restored after the corrected consumer, proven through a real allocation';
  RAISE NOTICE 'PASS 0037 allocation refuses a consumed predecessor and restores after correction';
END $$;
ROLLBACK;

-- The unit-event order guard: backdated and correction refusals, same-day and cash-only
-- exemptions, self-correction of the lineage event, and dedupe replay ahead of the guard.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; split_id UUID; target_id UUID;
  same_day_id UUID; cash_id UUID; corrected_split_id UUID; replay_id UUID; refusal TEXT;
  sale_target_id UUID; cash_correction_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-order-purchase');
  target_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-10',
    'purchase', 5, 50, 0, 0, -50, f.base, 'lineage-order-target');
  sale_target_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-11',
    'sale', -1, 10, 0, 0, 10, f.base, 'lineage-order-sale-target');
  SELECT event.holding_id INTO holding_id FROM investment_transaction event WHERE event.id = purchase_id;
  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-03-01', 2, 'lineage-order-split');

  BEGIN
    PERFORM pg_temp.t0037_event(f.account_1, f.security_1, '2026-02-01',
      'purchase', 1, 10, 0, 0, -10, f.base, 'lineage-order-backdated');
    RAISE EXCEPTION 'a backdated unit-affecting event was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_unit_event_order', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE source_event_id = 'lineage-order-backdated'
  ), 'the backdated refusal writes nothing';

  same_day_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-03-01',
    'purchase', 1, 10, 0, 0, -10, f.base, 'lineage-order-same-day');
  ASSERT same_day_id IS NOT NULL, 'a same-day row committed after the split must follow it';

  cash_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-02-01',
    'charge', NULL, NULL, 5, 0, -5, f.base, 'lineage-order-cash');
  ASSERT cash_id IS NOT NULL, 'a cash-only kind is exempt from the unit-event order guard';

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-01-11', 'charge', NULL, NULL, NULL, 5, 0, -5, f.base,
      '2026-01-11', 1, 1, -5, -5, 'owner_entered', 'lineage_v1', NULL, 'v2',
      rpad(md5('lineage-order-sale-target-correction'), 64, '0'), sale_target_id,
      'reclassify as a charge'
    );
    RAISE EXCEPTION 'a cash-only correction of a unit-bearing original bypassed the guard';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_unit_event_order', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE corrects_transaction_id = sale_target_id
  ), 'the cash-only-correction-of-a-unit-bearing-original refusal writes nothing';

  cash_correction_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'charge', NULL, NULL, NULL, 6, 0, -6, f.base,
    '2026-02-01', 1, 1, -6, -6, 'owner_entered', 'lineage_v1', NULL, 'v2',
    rpad(md5('lineage-order-cash-correction'), 64, '0'), cash_id, 'restate the charge amount'
  );
  ASSERT cash_correction_id IS NOT NULL,
    'a cash-only correction of a cash-only original remains exempt before the split';

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-01-10', 'purchase', 5, 10, 50, 0, 0, -50, f.base,
      '2026-01-10', 1, 1, -50, -50, 'owner_entered', 'lineage_v1', NULL, 'v1',
      rpad(md5('lineage-order-target-correction'), 64, '0'), target_id, 'restate units'
    );
    RAISE EXCEPTION 'a correction of an event an active lineage event already fixed was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_unit_event_order', refusal;
  END;

  corrected_split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-03-01', 3, 'lineage-order-split-correction', split_id, 'restate ratio');
  ASSERT corrected_split_id IS NOT NULL,
    'the order guard exempts a split correcting itself from its own fixed date';

  replay_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-order-purchase');
  ASSERT replay_id = purchase_id,
    'an exact dedupe replay returns before the order guard regardless of later lineage';
  RAISE NOTICE 'PASS 0037 unit-event order guard: backdated, same-day, cash-only and replay';
END $$;
ROLLBACK;

-- Rule 1 must also exclude the event a correction targets: an earlier-dated replacement of a
-- lineage event is accepted while it is the latest active lineage event, but the same replacement
-- of an event that is no longer latest is refused.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; split_id UUID;
  earlier_correction_id UUID; later_split_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-earlier-purchase');
  SELECT event.holding_id INTO holding_id FROM investment_transaction event WHERE event.id = purchase_id;
  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-03-01', 2, 'lineage-earlier-split');

  earlier_correction_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-02-01', 2, 'lineage-earlier-split-correction', split_id, 'restate an earlier date');
  ASSERT earlier_correction_id IS NOT NULL,
    'an earlier-dated replacement is accepted with no intervening active lineage event';

  later_split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-04-01', 3, 'lineage-earlier-second-split');
  BEGIN
    PERFORM pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
      '2026-01-15', 2, 'lineage-earlier-split-correction-two', earlier_correction_id,
      'restate an even earlier date');
    RAISE EXCEPTION 'an earlier-dated correction crossed another active lineage event';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_unit_event_order', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE corrects_transaction_id = earlier_correction_id
  ), 'the crossed-lineage-event refusal writes nothing';
  RAISE NOTICE 'PASS 0037 order guard excludes only the corrected event from its own comparison';
END $$;
ROLLBACK;

-- guard_correction_unfunded_event refuses correcting a split once its successor is allocated to
-- a disposal, refuses correcting the plain acquisition that active split consumed, and refuses
-- correcting a split once its successor is itself consumed by a later active split.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID; split_id UUID; successor_id UUID;
  disposal_id UUID; second_holding_id UUID; second_purchase_id UUID; second_lot_id UUID;
  first_split_id UUID; first_successor_id UUID; second_split_id UUID; second_successor_id UUID;
  refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);

  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-guard-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;
  split_id := pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
    '2026-02-01', 2, 'lineage-guard-split');
  successor_id := pg_temp.t0037_successor(f.h1, holding_id, split_id, lot_id, 20, 100.0000);
  disposal_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-03-01',
    'sale', -4, 12, 0, 0, 12, f.base, 'lineage-guard-disposal');
  PERFORM allocate_investment_lots(disposal_id, ARRAY[successor_id], ARRAY[4.00000000]);

  BEGIN
    PERFORM pg_temp.t0037_split(f.h1, f.account_1, holding_id, f.security_1,
      '2026-02-01', 2, 'lineage-guard-split-correction', split_id, 'replace after use');
    RAISE EXCEPTION 'a split was corrected after its successor was allocated';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_correction_requires_unfunded_event', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE corrects_transaction_id = split_id
  ), 'the allocated-successor correction refusal writes nothing';

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-01-01', 'purchase', 10, 10, 100, 0, 0, -100, f.base,
      '2026-01-01', 1, 1, -100, -100, 'owner_entered', 'lineage_v1', NULL, 'v2',
      rpad(md5('lineage-guard-purchase-correction'), 64, '0'), purchase_id, 'restate the purchase'
    );
    RAISE EXCEPTION 'a purchase was corrected after an active split consumed its lot';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_correction_requires_unfunded_event', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE corrects_transaction_id = purchase_id
  ), 'the consumed-acquisition correction refusal writes nothing';

  second_purchase_id := pg_temp.t0037_event(f.account_2, f.security_2, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-guard-second-purchase');
  SELECT lot.id, lot.holding_id INTO second_lot_id, second_holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = second_purchase_id;
  first_split_id := pg_temp.t0037_split(f.h1, f.account_2, second_holding_id, f.security_2,
    '2026-02-01', 2, 'lineage-guard-first-split');
  first_successor_id := pg_temp.t0037_successor(
    f.h1, second_holding_id, first_split_id, second_lot_id, 20, 100.0000);
  second_split_id := pg_temp.t0037_split(f.h1, f.account_2, second_holding_id, f.security_2,
    '2026-03-01', 2, 'lineage-guard-second-split');
  second_successor_id := pg_temp.t0037_successor(
    f.h1, second_holding_id, second_split_id, first_successor_id, 40, 100.0000);

  BEGIN
    PERFORM pg_temp.t0037_split(f.h1, f.account_2, second_holding_id, f.security_2,
      '2026-02-01', 2, 'lineage-guard-first-split-correction', first_split_id,
      'replace once chained');
    RAISE EXCEPTION 'a split was corrected after its successor was chained into a later split';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_correction_requires_unfunded_event', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE corrects_transaction_id = first_split_id
  ), 'the chained-successor correction refusal writes nothing';
  RAISE NOTICE 'PASS 0037 correction refuses once lineage consumption is allocated or chained';
END $$;
ROLLBACK;

-- Direct DML on the new columns fails closed exactly like every other investment history row.
BEGIN;
DO $$
DECLARE
  f RECORD; holding_id UUID; purchase_id UUID; lot_id UUID;
  other_household_id UUID; other_person_id UUID; other_owner_id UUID;
  other_account_id UUID; other_security_id UUID; foreign_purchase_id UUID; foreign_lot_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-dml-purchase');
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;

  BEGIN
    INSERT INTO acquisition_lot (
      household_id, holding_id, origin_transaction_id, acquisition_date,
      acquired_units, cost_basis_native, currency, predecessor_lot_id
    ) VALUES (f.h1, holding_id, purchase_id, '2026-01-01', 5, 50, f.base, lot_id);
    RAISE EXCEPTION 'a direct successor-lot insert bypassed the append boundary';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;

  BEGIN
    UPDATE acquisition_lot SET predecessor_lot_id = lot_id WHERE id = lot_id;
    RAISE EXCEPTION 'a retained lot predecessor was updated directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;
  BEGIN
    DELETE FROM acquisition_lot WHERE id = lot_id;
    RAISE EXCEPTION 'a retained lot was deleted directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;
  BEGIN
    UPDATE investment_transaction SET counterpart_transaction_id = purchase_id
     WHERE id = purchase_id;
    RAISE EXCEPTION 'a retained event counterpart was updated directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;
  BEGIN
    DELETE FROM investment_transaction WHERE id = purchase_id;
    RAISE EXCEPTION 'a retained event was deleted directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;

  BEGIN
    PERFORM set_config('oneledger.investment_event_append', 'on', true);
    INSERT INTO investment_transaction (
      household_id, account_id, holding_id, security_id, event_date, event_kind,
      units, price, gross_amount, net_amount, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, net_amount_base, net_amount_secondary,
      source_kind, source_contract, source_event_id, counterpart_transaction_id
    ) VALUES (
      f.h1, f.account_1, holding_id, f.security_1, '2026-01-15', 'purchase',
      1, 10, 10, -10, f.base, '2026-01-15', 1, 1, -10, -10,
      'statement', 'lineage_v1', 'lineage-dml-counterpart', purchase_id
    );
    PERFORM set_config('oneledger.investment_event_append', 'off', true);
    RAISE EXCEPTION 'a non-paired kind stored a counterpart reference';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);

  BEGIN
    PERFORM set_config('oneledger.investment_event_append', 'on', true);
    INSERT INTO investment_transaction (
      household_id, account_id, holding_id, security_id, event_date, event_kind,
      units, price, gross_amount, net_amount, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, net_amount_base, net_amount_secondary,
      source_kind, source_contract, source_event_id
    ) VALUES (
      f.h1, f.account_1, holding_id, f.security_1, '2026-01-16', 'platform_transfer_in',
      1, NULL, NULL, 0, f.base, NULL, NULL, NULL, NULL, NULL,
      'statement', 'lineage_v1', 'lineage-dml-paired-without-counterpart'
    );
    PERFORM set_config('oneledger.investment_event_append', 'off', true);
    RAISE EXCEPTION 'a paired *_in kind without a counterpart was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('lineage foreign household', f.base, f.secondary) RETURNING id INTO other_household_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (other_household_id, 'lineage foreign owner', true) RETURNING id INTO other_person_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), other_household_id, other_person_id,
          'lineage-foreign-owner@example.invalid', 'owner') RETURNING id INTO other_owner_id;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (other_household_id, other_person_id, 'Synthetic foreign lineage account',
            'brokerage', f.base, 'XZ', 0, '2025-01-01') RETURNING id INTO other_account_id;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (other_household_id, 'equity', 'Synthetic foreign lineage security', f.base, 'XZ', 'equity')
  RETURNING id INTO other_security_id;
  PERFORM set_config('app.user_id', other_owner_id::TEXT, true);
  foreign_purchase_id := pg_temp.t0037_event(other_account_id, other_security_id, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-dml-foreign-purchase');
  SELECT lot.id INTO foreign_lot_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = foreign_purchase_id;
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM set_config('oneledger.investment_event_append', 'on', true);
    INSERT INTO acquisition_lot (
      household_id, holding_id, origin_transaction_id, acquisition_date,
      acquired_units, cost_basis_native, currency, predecessor_lot_id
    ) VALUES (
      f.h1, holding_id, purchase_id, '2026-01-01', 5, 50, f.base, foreign_lot_id
    );
    PERFORM set_config('oneledger.investment_event_append', 'off', true);
    RAISE EXCEPTION 'a predecessor from another household was accepted';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
  RAISE NOTICE 'PASS 0037 direct DML on lineage columns and lots fails closed';
END $$;
ROLLBACK;

-- A predecessor is household-scoped, not holding-scoped: a platform transfer's successor lives in
-- the destination holding, and the source holding's open lots exclude it from the transfer date.
BEGIN;
DO $$
DECLARE
  f RECORD; source_holding_id UUID; destination_holding_id UUID;
  source_purchase_id UUID; destination_purchase_id UUID; source_lot_id UUID;
  transfer_out_id UUID; successor_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  source_purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-transfer-source-purchase');
  SELECT lot.id, lot.holding_id INTO source_lot_id, source_holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = source_purchase_id;
  destination_purchase_id := pg_temp.t0037_event(f.account_2, f.security_2, '2026-01-01',
    'purchase', 1, 10, 0, 0, -10, f.base, 'lineage-transfer-destination-purchase');
  SELECT event.holding_id INTO destination_holding_id
    FROM investment_transaction event WHERE event.id = destination_purchase_id;
  ASSERT source_holding_id <> destination_holding_id,
    'the source and destination fixtures must be different holdings';

  -- platform_transfer_out has no owner-facing append path until Task 15.4; the fixture inserts it
  -- directly under the append guard, exactly like the split fixture above.
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO investment_transaction (
    household_id, account_id, holding_id, security_id, event_date, event_kind,
    units, net_amount, currency, source_kind, source_contract, source_event_id
  ) VALUES (
    f.h1, f.account_1, source_holding_id, f.security_1, '2026-02-01', 'platform_transfer_out',
    -10, 0, f.base, 'statement', 'lineage_v1', 'lineage-transfer-out'
  ) RETURNING id INTO transfer_out_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);

  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO acquisition_lot (
    household_id, holding_id, origin_transaction_id, acquisition_date,
    acquired_units, cost_basis_native, currency, predecessor_lot_id
  ) VALUES (
    f.h1, destination_holding_id, transfer_out_id, '2026-01-01', 10, 100.0000,
    f.base, source_lot_id
  ) RETURNING id INTO successor_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);

  ASSERT (SELECT count(*) FROM investment_open_lots(source_holding_id, '2026-02-01')) = 0,
    'a fully transferred lot leaves the source holding''s open lots at the transfer date';
  ASSERT (SELECT open_units = 10 AND open_basis_native = 100.0000
            FROM investment_open_lots(destination_holding_id, '2026-02-01')
           WHERE acquisition_lot_id = successor_id),
    'the destination holding reports the cross-holding successor as its own open lot';
  ASSERT (SELECT count(*) FROM investment_open_lots(destination_holding_id, '2026-02-01')) = 2,
    'the destination holding also keeps its own pre-existing open lot';
  RAISE NOTICE 'PASS 0037 a cross-holding successor moves open lots to its destination holding';
END $$;
ROLLBACK;

-- The read model is owner- and household-scoped, matching every other investment relation: the
-- owning household's owner sees its own lots, a member has no owner-level read access, and
-- another household's session sees nothing for the same holding identity.
BEGIN;
DO $$
DECLARE f RECORD; holding_id UUID; purchase_id UUID; visible INTEGER; available BOOLEAN;
BEGIN
  SELECT * INTO f FROM pg_temp.t0037_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := pg_temp.t0037_event(f.account_1, f.security_1, '2026-01-01',
    'purchase', 10, 100, 0, 0, -100, f.base, 'lineage-rls-purchase');
  SELECT event.holding_id INTO holding_id FROM investment_transaction event WHERE event.id = purchase_id;

  SET LOCAL ROLE authenticated;
  SELECT count(*) INTO visible FROM investment_open_lots(holding_id, '2026-02-01');
  ASSERT visible = 1, 'the owning household reads its own open lots';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  SELECT count(*) INTO visible FROM investment_open_lots(holding_id, '2026-02-01');
  ASSERT visible = 0, 'a member has no owner-level read access to open lots';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  SELECT count(*) INTO visible FROM investment_open_lots(holding_id, '2026-02-01');
  ASSERT visible = 0, 'another household reads no open lots for a foreign holding';
  SELECT investment_lot_lineage_available(holding_id, '2026-02-01') INTO available;
  ASSERT available, 'lineage availability is vacuously true for an unseen holding';
  RESET ROLE;
  RAISE NOTICE 'PASS 0037 open lots and lineage availability are household-scoped';
END $$;
ROLLBACK;
