-- V41: holding-level unit actions (split, bonus, rights, switch, systematic transfer, platform
-- transfer). All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_proc
           WHERE oid IN (
             'append_investment_split(uuid,uuid,date,numeric,investment_event_source,text,text,text,text,uuid,text)'::regprocedure,
             'append_investment_unit_transfer(investment_event_kind,date,uuid,uuid,numeric,numeric,numeric,uuid,uuid,numeric,numeric,numeric,character,investment_event_source,text,text,text,text,text,text,text,uuid,text)'::regprocedure,
             'append_investment_event_core(uuid,uuid,date,investment_event_kind,numeric,numeric,numeric,numeric,numeric,numeric,character,date,numeric,numeric,numeric,numeric,investment_event_source,text,text,text,text,uuid,text,numeric,uuid)'::regprocedure
           )) = 3,
    'the split, unit-transfer and revoked core operations must exist';
  ASSERT NOT has_function_privilege(
    'authenticated',
    'append_investment_event_core(uuid,uuid,date,investment_event_kind,numeric,numeric,numeric,numeric,numeric,numeric,character,date,numeric,numeric,numeric,numeric,investment_event_source,text,text,text,text,uuid,text,numeric,uuid)'::regprocedure,
    'EXECUTE'
  ), 'the core append operation must be revoked from authenticated';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'investment_lot_allocation'::regclass
       AND tgname = 'trg_investment_lot_allocation_requires_cash_disposal'
  ), 'a corporate-action lot-allocation guard trigger must exist';
  RAISE NOTICE 'PASS 0038 structure and revoked core operation exist';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0038_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    account_1 UUID, account_2 UUID, security_1 UUID, security_2 UUID, security_foreign UUID,
    base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('unit action household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('unit action household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'unit action owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'unit action member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other unit action owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'unit-action-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'unit-action-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-unit-action-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h1, p1, 'Synthetic unit action account one', 'brokerage', base, 'XZ', 0, '2025-01-01')
  RETURNING id INTO account_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h1, p1, 'Synthetic unit action account two', 'brokerage', base, 'XZ', 0, '2025-01-01')
  RETURNING id INTO account_2;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h1, 'equity', 'Synthetic unit action security one', base, 'XZ', 'equity')
  RETURNING id INTO security_1;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h1, 'equity', 'Synthetic unit action security two', base, 'XZ', 'equity')
  RETURNING id INTO security_2;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h2, 'equity', 'Synthetic unit action foreign security', base, 'XZ', 'equity')
  RETURNING id INTO security_foreign;
  RETURN NEXT;
END $$;

-- A bonus creates a zero-cost lot; rights are a funded acquisition lot. Both admit through the
-- widened append_investment_transaction boundary and add to the holding total.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; bonus_id UUID; rights_id UUID; holding_id UUID;
  bonus_lot RECORD; rights_lot RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-purchase', NULL, NULL, NULL, NULL);
  SELECT holding.id INTO holding_id FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = purchase_id;

  bonus_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'bonus',
    2.00000000, NULL, NULL, 0.0000, 0.0000, 0.0000, f.base,
    NULL, NULL, NULL, NULL, NULL,
    'owner_entered', 'unit_action_v1', 'ua-bonus', NULL, NULL, NULL, NULL);
  SELECT * INTO bonus_lot FROM acquisition_lot WHERE origin_transaction_id = bonus_id;
  ASSERT bonus_lot.acquired_units = 2 AND bonus_lot.cost_basis_native = 0,
    'a bonus creates a zero-cost lot';

  rights_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-15', 'rights',
    5.00000000, 8.000000, 40.0000, 1.0000, 0.0000, -41.0000, f.base,
    '2026-02-15', 1.0000000000, 1.0000000000, -41.0000, -41.0000,
    'owner_entered', 'unit_action_v1', 'ua-rights', NULL, NULL, NULL, NULL);
  SELECT * INTO rights_lot FROM acquisition_lot WHERE origin_transaction_id = rights_id;
  ASSERT rights_lot.acquired_units = 5 AND rights_lot.cost_basis_native = 41.0000,
    'rights are a funded acquisition lot';

  ASSERT (SELECT current_units FROM holding WHERE id = holding_id) = 17,
    'bonus and rights units add to the holding total';
  RAISE NOTICE 'PASS 0038 bonus is a zero-cost lot and rights are a funded acquisition';
END $$;
ROLLBACK;

-- An exact forward split doubles holding units, writes the delta as its own unit effect and
-- creates no lot; a reverse split (ratio < 1) reduces units by the same rule.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; split_id UUID; reverse_id UUID; holding_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-split-purchase', NULL, NULL, NULL, NULL);
  SELECT holding.id INTO holding_id FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = purchase_id;

  split_id := append_investment_split(
    f.account_1, f.security_1, '2026-02-01', 2,
    'owner_entered', 'unit_action_split_v1', 'ua-split-one', NULL, NULL, NULL, NULL);
  ASSERT (SELECT units FROM investment_transaction WHERE id = split_id) = 10,
    'a 2-for-1 split on 10 units writes a +10 unit effect';
  ASSERT (SELECT current_units FROM holding WHERE id = holding_id) = 20,
    'the holding doubles after a 2-for-1 split';
  ASSERT NOT EXISTS (SELECT FROM acquisition_lot WHERE origin_transaction_id = split_id),
    'a split creates no lot';

  reverse_id := append_investment_split(
    f.account_1, f.security_1, '2026-03-01', 0.5,
    'owner_entered', 'unit_action_split_v1', 'ua-split-reverse', NULL, NULL, NULL, NULL);
  ASSERT (SELECT units FROM investment_transaction WHERE id = reverse_id) = -10,
    'a 1-for-2 reverse split on 20 units writes a -10 unit effect';
  ASSERT (SELECT current_units FROM holding WHERE id = holding_id) = 10,
    'the holding halves after a 1-for-2 reverse split';
  RAISE NOTICE 'PASS 0038 exact forward and reverse splits change units exactly and create no lot';
END $$;
ROLLBACK;

-- An inexact split refuses atomically at scale 8.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    1.00000000, 10.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
    'owner_entered', 'unit_action_v1', 'ua-inexact-purchase', NULL, NULL, NULL, NULL);
  BEGIN
    PERFORM append_investment_split(
      f.account_1, f.security_1, '2026-02-01', 1.3333333333,
      'owner_entered', 'unit_action_split_v1', 'ua-split-inexact', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'an inexact split unit effect was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_split_units_are_exact', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE source_event_id = 'ua-split-inexact'
  ), 'an inexact split refusal is atomic';
  RAISE NOTICE 'PASS 0038 an inexact split refuses atomically';
END $$;
ROLLBACK;

-- A split must be the holding's latest unit-affecting event.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; second_purchase_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-latest-purchase-one', NULL, NULL, NULL, NULL);
  second_purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-03-01', 'purchase',
    5.00000000, 10.000000, 50.0000, 0.0000, 0.0000, -50.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, -50.0000, -50.0000,
    'owner_entered', 'unit_action_v1', 'ua-latest-purchase-two', NULL, NULL, NULL, NULL);
  BEGIN
    PERFORM append_investment_split(
      f.account_1, f.security_1, '2026-02-01', 2,
      'owner_entered', 'unit_action_split_v1', 'ua-split-backdated', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'a split predated a later active purchase';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_split_is_latest', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE source_event_id = 'ua-split-backdated'
  ), 'the split-is-latest refusal is atomic';
  RAISE NOTICE 'PASS 0038 a split must be the holding''s latest unit-affecting event';
END $$;
ROLLBACK;

-- A split correction excludes its own prior effect from the recomputed baseline, and an exact
-- replay of either the original or the correction returns its retained id.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; split_id UUID; replay_id UUID; correction_id UUID;
  holding_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-correct-purchase', NULL, NULL, NULL, NULL);
  SELECT holding.id INTO holding_id FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = purchase_id;

  split_id := append_investment_split(
    f.account_1, f.security_1, '2026-02-01', 2,
    'owner_entered', 'unit_action_split_v1', 'ua-correct-split-one', NULL, NULL, NULL, NULL);
  ASSERT (SELECT current_units FROM holding WHERE id = holding_id) = 20,
    'the original split doubles the holding';

  replay_id := append_investment_split(
    f.account_1, f.security_1, '2026-02-01', 2,
    'owner_entered', 'unit_action_split_v1', 'ua-correct-split-one', NULL, NULL, NULL, NULL);
  ASSERT replay_id = split_id, 'an exact split replay returns its retained id';
  ASSERT (SELECT count(*) FROM investment_transaction WHERE event_kind = 'split') = 1,
    'an exact split replay writes no new row';

  correction_id := append_investment_split(
    f.account_1, f.security_1, '2026-04-01', 3,
    'owner_entered', 'unit_action_split_v1', 'ua-correct-split-two', NULL, NULL,
    split_id, 'replace the recorded ratio');
  ASSERT (SELECT units FROM investment_transaction WHERE id = correction_id) = 20,
    'the correction recomputes from the baseline excluding the corrected split (10 x (3-1))';
  ASSERT (SELECT current_units FROM holding WHERE id = holding_id) = 30,
    'the corrected split retires the original and applies its own effect';
  RAISE NOTICE 'PASS 0038 a split correction excludes its own prior effect; replay is idempotent';
END $$;
ROLLBACK;

-- A conflicting split dedupe identity refuses atomically.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; split_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-conflict-purchase', NULL, NULL, NULL, NULL);
  split_id := append_investment_split(
    f.account_1, f.security_1, '2026-02-01', 2,
    'owner_entered', 'unit_action_split_v1', 'ua-conflict-split', NULL, NULL, NULL, NULL);
  BEGIN
    PERFORM append_investment_split(
      f.account_1, f.security_1, '2026-02-01', 3,
      'owner_entered', 'unit_action_split_v1', 'ua-conflict-split', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'conflicting split facts reused one source identity';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'conflicting investment event uses an existing dedupe identity', refusal;
  END;
  ASSERT (SELECT count(*) FROM investment_transaction WHERE event_kind = 'split') = 1,
    'a conflicting split dedupe identity writes nothing new';
  RAISE NOTICE 'PASS 0038 a conflicting split dedupe identity refuses atomically';
END $$;
ROLLBACK;

-- A switch pair moves equal gross value between two securities in one account: the out leg
-- disposes with no lot, the in leg is a funded lot, and a mismatched pair refuses atomically.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; out_id UUID; in_id UUID; out_holding UUID; in_holding UUID;
  in_lot RECORD; pair RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-switch-purchase', NULL, NULL, NULL, NULL);
  SELECT holding.id INTO out_holding FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = purchase_id;

  SELECT * INTO pair FROM append_investment_unit_transfer(
    'switch_out', '2026-02-01', f.account_1, f.security_1, -4, 10.000000, 40.0000,
    f.account_1, f.security_2, 8, 5.000000, 40.0000,
    f.base, 'owner_entered', 'unit_action_switch_v1',
    'ua-switch-out', NULL, NULL, 'ua-switch-in', NULL, NULL, NULL, NULL);
  out_id := pair.out_event_id;
  in_id := pair.in_event_id;

  ASSERT (SELECT current_units FROM holding WHERE id = out_holding) = 6,
    'the switch out leg reduces the source holding';
  ASSERT NOT EXISTS (SELECT FROM acquisition_lot WHERE origin_transaction_id = out_id),
    'a switch out leg creates no lot';
  SELECT holding.id INTO in_holding FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = in_id;
  ASSERT (SELECT current_units FROM holding WHERE id = in_holding) = 8,
    'the switch in leg opens the destination holding';
  SELECT * INTO in_lot FROM acquisition_lot WHERE origin_transaction_id = in_id;
  ASSERT in_lot.acquired_units = 8 AND in_lot.cost_basis_native = 40.0000,
    'a switch in leg is a funded acquisition lot at its own gross value';
  ASSERT (SELECT counterpart_transaction_id FROM investment_transaction WHERE id = in_id)
    = out_id, 'the in leg names its out leg as counterpart';

  BEGIN
    PERFORM append_investment_unit_transfer(
      'switch_out', '2026-03-01', f.account_1, f.security_1, -1, 10.000000, 10.0000,
      f.account_1, f.security_2, 1, 5.000000, 5.0000,
      f.base, 'owner_entered', 'unit_action_switch_v1',
      'ua-switch-mismatch-out', NULL, NULL, 'ua-switch-mismatch-in', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'a switch with unequal gross value was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_unit_transfer_pair_agrees', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE source_event_id LIKE 'ua-switch-mismatch%'
  ), 'a mismatched switch pair refuses atomically';
  RAISE NOTICE 'PASS 0038 a switch pair moves equal gross value; a mismatched pair refuses';
END $$;
ROLLBACK;

-- A platform transfer pair moves opposite units of one security between two accounts, creating
-- no lot on either leg; a mismatched pair refuses atomically.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; out_id UUID; in_id UUID; out_holding UUID; in_holding UUID;
  pair RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-platform-purchase', NULL, NULL, NULL, NULL);
  SELECT holding.id INTO out_holding FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = purchase_id;

  SELECT * INTO pair FROM append_investment_unit_transfer(
    'platform_transfer_out', '2026-02-01', f.account_1, f.security_1, -6, NULL, NULL,
    f.account_2, f.security_1, 6, NULL, NULL,
    f.base, 'owner_entered', 'unit_action_platform_transfer_v1',
    'ua-platform-out', NULL, NULL, 'ua-platform-in', NULL, NULL, NULL, NULL);
  out_id := pair.out_event_id;
  in_id := pair.in_event_id;

  ASSERT (SELECT current_units FROM holding WHERE id = out_holding) = 4,
    'the platform transfer out leg reduces the source holding';
  SELECT holding.id INTO in_holding FROM investment_transaction event
    JOIN holding ON holding.id = event.holding_id WHERE event.id = in_id;
  ASSERT (SELECT current_units FROM holding WHERE id = in_holding) = 6,
    'the platform transfer in leg opens the destination holding at the same security';
  ASSERT NOT EXISTS (
    SELECT FROM acquisition_lot WHERE origin_transaction_id IN (out_id, in_id)
  ), 'a platform transfer creates no lot on either leg';

  BEGIN
    PERFORM append_investment_unit_transfer(
      'platform_transfer_out', '2026-03-01', f.account_1, f.security_1, -2, NULL, NULL,
      f.account_2, f.security_1, 3, NULL, NULL,
      f.base, 'owner_entered', 'unit_action_platform_transfer_v1',
      'ua-platform-mismatch-out', NULL, NULL, 'ua-platform-mismatch-in',
      NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'a platform transfer with unequal units was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_unit_transfer_pair_agrees', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE source_event_id LIKE 'ua-platform-mismatch%'
  ), 'a mismatched platform transfer pair refuses atomically';
  RAISE NOTICE 'PASS 0038 a platform transfer pair moves opposite units and creates no lot';
END $$;
ROLLBACK;

-- An exact pair replay returns both retained ids; a conflicting replay refuses atomically; a
-- correction replaces both legs together.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; pair RECORD; out_id UUID; in_id UUID;
  replay_out UUID; replay_in UUID; correction RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-replay-purchase', NULL, NULL, NULL, NULL);

  SELECT * INTO pair FROM append_investment_unit_transfer(
    'switch_out', '2026-02-01', f.account_1, f.security_1, -4, 10.000000, 40.0000,
    f.account_1, f.security_2, 8, 5.000000, 40.0000,
    f.base, 'owner_entered', 'unit_action_switch_v1',
    'ua-replay-out', NULL, NULL, 'ua-replay-in', NULL, NULL, NULL, NULL);
  out_id := pair.out_event_id;
  in_id := pair.in_event_id;

  SELECT * INTO pair FROM append_investment_unit_transfer(
    'switch_out', '2026-02-01', f.account_1, f.security_1, -4, 10.000000, 40.0000,
    f.account_1, f.security_2, 8, 5.000000, 40.0000,
    f.base, 'owner_entered', 'unit_action_switch_v1',
    'ua-replay-out', NULL, NULL, 'ua-replay-in', NULL, NULL, NULL, NULL);
  replay_out := pair.out_event_id;
  replay_in := pair.in_event_id;
  ASSERT replay_out = out_id AND replay_in = in_id,
    'an exact pair replay returns both retained ids';
  ASSERT (SELECT count(*) FROM investment_transaction WHERE event_kind IN (
    'switch_out', 'switch_in')) = 2, 'an exact pair replay writes no new rows';

  BEGIN
    PERFORM append_investment_unit_transfer(
      'switch_out', '2026-02-01', f.account_1, f.security_1, -5, 10.000000, 50.0000,
      f.account_1, f.security_2, 10, 5.000000, 50.0000,
      f.base, 'owner_entered', 'unit_action_switch_v1',
      'ua-replay-out', NULL, NULL, 'ua-replay-in', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'conflicting pair facts reused one source identity';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'conflicting investment event uses an existing dedupe identity', refusal;
  END;

  SELECT * INTO correction FROM append_investment_unit_transfer(
    'switch_out', '2026-02-01', f.account_1, f.security_1, -4, 10.000000, 40.0000,
    f.account_1, f.security_2, 8, 5.000000, 40.0000,
    f.base, 'owner_entered', 'unit_action_switch_correction_v1',
    'ua-replay-correction-out', NULL, NULL, 'ua-replay-correction-in', NULL, NULL,
    out_id, 'replace both legs');
  ASSERT (SELECT corrects_transaction_id FROM investment_transaction
           WHERE id = correction.out_event_id) = out_id,
    'the correction out leg identifies the original out leg';
  ASSERT (SELECT corrects_transaction_id FROM investment_transaction
           WHERE id = correction.in_event_id) = in_id,
    'a correction replaces both legs together';
  RAISE NOTICE 'PASS 0038 an exact pair replay is idempotent; a correction replaces both legs';
END $$;
ROLLBACK;

-- The V41 trigger refuses lot allocation for a switch/platform leg itself, and for any disposal
-- dated on or after an active corporate-action leg in its holding. allocate_investment_lots_v33's
-- own lineage check (extended to these kinds too) would refuse the ordinary entry point first, so
-- this exercises the trigger directly the way a future or alternate write path would reach it.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; lot_id UUID; holding_id UUID; pair RECORD;
  out_id UUID; late_sale_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-guard-purchase', NULL, NULL, NULL, NULL);
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id
    FROM acquisition_lot lot WHERE lot.origin_transaction_id = purchase_id;

  SELECT * INTO pair FROM append_investment_unit_transfer(
    'switch_out', '2026-02-01', f.account_1, f.security_1, -4, 10.000000, 40.0000,
    f.account_1, f.security_2, 8, 5.000000, 40.0000,
    f.base, 'owner_entered', 'unit_action_switch_v1',
    'ua-guard-out', NULL, NULL, 'ua-guard-in', NULL, NULL, NULL, NULL);
  out_id := pair.out_event_id;

  BEGIN
    PERFORM set_config('oneledger.investment_lot_allocation_write', 'on', true);
    INSERT INTO investment_lot_allocation (
      household_id, holding_id, disposal_transaction_id, acquisition_lot_id,
      allocated_units, allocated_by
    ) VALUES (f.h1, holding_id, out_id, lot_id, 4.00000000, f.u1);
    RAISE EXCEPTION 'a switch out leg accepted direct lot allocation';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_allocation_requires_cash_disposal', refusal;
  END;
  PERFORM set_config('oneledger.investment_lot_allocation_write', 'off', true);

  late_sale_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-03-01', 'sale',
    -2.00000000, 10.000000, 20.0000, 0.0000, 0.0000, 20.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, 20.0000, 20.0000,
    'owner_entered', 'unit_action_v1', 'ua-guard-late-sale', NULL, NULL, NULL, NULL);
  BEGIN
    PERFORM set_config('oneledger.investment_lot_allocation_write', 'on', true);
    INSERT INTO investment_lot_allocation (
      household_id, holding_id, disposal_transaction_id, acquisition_lot_id,
      allocated_units, allocated_by
    ) VALUES (f.h1, holding_id, late_sale_id, lot_id, 2.00000000, f.u1);
    RAISE EXCEPTION 'a disposal dated after an active switch leg accepted direct lot allocation';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_allocation_requires_cash_disposal', refusal;
  END;
  PERFORM set_config('oneledger.investment_lot_allocation_write', 'off', true);

  ASSERT NOT EXISTS (SELECT FROM investment_lot_allocation),
    'both allocation refusals are atomic';

  BEGIN
    PERFORM allocate_investment_lots(out_id, ARRAY[lot_id], ARRAY[4.00000000]);
    RAISE EXCEPTION 'the ordinary allocation entry point accepted a switch out leg';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_lot_allocation_requires_available_lineage', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM investment_lot_allocation),
    'the ordinary entry point refusal is also atomic';
  RAISE NOTICE 'PASS 0038 lot allocation is refused for a corporate-action leg and its aftermath';
END $$;
ROLLBACK;

-- Authorization and household scope fail before any write: a member cannot split, and a
-- cross-household account or security is indistinguishable from a missing one.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-scope-purchase', NULL, NULL, NULL, NULL);

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  BEGIN
    PERFORM append_investment_split(
      f.account_1, f.security_1, '2026-02-01', 2,
      'owner_entered', 'unit_action_split_v1', 'ua-scope-member-split', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'a household member split a holding';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  BEGIN
    PERFORM append_investment_split(
      f.account_1, f.security_1, '2026-02-01', 2,
      'owner_entered', 'unit_action_split_v1', 'ua-scope-foreign-split', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'a foreign household split another household''s holding';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;

  ASSERT (SELECT count(*) FROM investment_transaction WHERE event_kind = 'split') = 0,
    'no split was written by an unauthorized attempt';
  RAISE NOTICE 'PASS 0038 a member and a foreign household cannot split a holding';
END $$;
ROLLBACK;

-- A pair whose in leg names a foreign-household security fails after the out leg would have
-- been written, and the whole pair rolls back atomically (Postgres statement-level atomicity).
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0038_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-01-01', 'purchase',
    10.00000000, 10.000000, 100.0000, 0.0000, 0.0000, -100.0000, f.base,
    '2026-01-01', 1.0000000000, 1.0000000000, -100.0000, -100.0000,
    'owner_entered', 'unit_action_v1', 'ua-cross-purchase', NULL, NULL, NULL, NULL);

  BEGIN
    PERFORM append_investment_unit_transfer(
      'switch_out', '2026-02-01', f.account_1, f.security_1, -4, 10.000000, 40.0000,
      f.account_1, f.security_foreign, 8, 5.000000, 40.0000,
      f.base, 'owner_entered', 'unit_action_switch_v1',
      'ua-cross-out', NULL, NULL, 'ua-cross-in', NULL, NULL, NULL, NULL);
    RAISE EXCEPTION 'a switch in leg used a foreign household security';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_transaction WHERE source_event_id = 'ua-cross-out'
  ), 'the already-written out leg rolls back when the in leg is refused';
  ASSERT (SELECT current_units FROM holding
           WHERE household_id = f.h1 AND account_id = f.account_1
             AND security_id = f.security_1) = 10,
    'the source holding is unaffected by the rolled-back pair';
  RAISE NOTICE 'PASS 0038 a pair whose in leg is refused rolls back the out leg atomically';
END $$;
ROLLBACK;
