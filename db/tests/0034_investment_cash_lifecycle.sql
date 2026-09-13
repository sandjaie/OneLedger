-- V37: owner-authorized cash lifecycle investment events.
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE operation_definition TEXT;
BEGIN
  SELECT pg_get_functiondef(
    'append_investment_transaction(uuid,uuid,date,investment_event_kind,numeric,numeric,numeric,numeric,numeric,numeric,character,date,numeric,numeric,numeric,numeric,investment_event_source,text,text,text,text,uuid,text)'::regprocedure
  ) INTO operation_definition;
  ASSERT operation_definition LIKE '%cash_distribution%'
     AND operation_definition LIKE '%interest%'
     AND operation_definition LIKE '%reinvested_distribution%'
     AND operation_definition LIKE '%charge%'
     AND operation_definition LIKE '%withheld_tax%',
    'one sanctioned append operation must admit every cash lifecycle kind';
  RAISE NOTICE 'PASS 0034 cash lifecycle kinds share the existing append boundary';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0034_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, p1 UUID, p2 UUID,
    account_1 UUID, account_2 UUID, security_1 UUID, security_2 UUID,
    base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('cash lifecycle household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('cash lifecycle household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'cash lifecycle owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'cash lifecycle owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'cash-lifecycle-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'cash-lifecycle-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic cash lifecycle account one', 'brokerage', base, 'XZ', 0,
    '2026-01-01'
  ) RETURNING id INTO account_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h2, p2, 'Synthetic cash lifecycle account two', 'brokerage', base, 'XY', 0,
    '2026-01-01'
  ) RETURNING id INTO account_2;

  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h1, 'equity', 'Synthetic cash lifecycle security one', base, 'XZ', 'equity',
    'CSH1', 'SYNX'
  ) RETURNING id INTO security_1;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h2, 'equity', 'Synthetic cash lifecycle security two', base, 'XY', 'equity',
    'CSH2', 'SYNX'
  ) RETURNING id INTO security_2;
  RETURN NEXT;
END $$;

-- Every cash lifecycle kind preserves its evidenced facts and only reinvestment changes units/lots.
BEGIN;
DO $$
DECLARE
  f RECORD; purchase_id UUID; distribution_id UUID; interest_id UUID;
  reinvestment_id UUID; charge_id UUID; tax_id UUID; v_holding_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0034_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    10.00000000, 2.000000, 20.0000, 0.0000, 0.0000, -20.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -20.0000, -20.0000,
    'statement', 'cash_lifecycle_v1', 'cash-seed', NULL, NULL, NULL, NULL
  );
  distribution_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-02', 'cash_distribution',
    NULL, NULL, 5.0000, 0.5000, 0.5000, 4.0000, f.base,
    '2026-02-02', 1.0000000000, 1.0000000000, 4.0000, 4.0000,
    'statement', 'cash_lifecycle_v1', 'cash-distribution', NULL, NULL, NULL, NULL
  );
  interest_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-03', 'interest',
    NULL, NULL, 2.0000, 0.0000, 0.0000, 2.0000, f.base,
    '2026-02-03', 1.0000000000, 1.0000000000, 2.0000, 2.0000,
    'statement', 'cash_lifecycle_v1', 'cash-interest', NULL, NULL, NULL, NULL
  );
  reinvestment_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-04', 'reinvested_distribution',
    1.50000000, 2.000000, 3.0000, 0.0000, 0.0000, 0.0000, f.base,
    NULL, NULL, NULL, NULL, NULL,
    'statement', 'cash_lifecycle_v1', 'cash-reinvestment', NULL, NULL, NULL, NULL
  );
  charge_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-05', 'charge',
    NULL, NULL, NULL, 1.2500, 0.0000, -1.2500, f.base,
    '2026-02-05', 1.0000000000, 1.0000000000, -1.2500, -1.2500,
    'statement', 'cash_lifecycle_v1', 'cash-charge', NULL, NULL, NULL, NULL
  );
  tax_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-06', 'withheld_tax',
    NULL, NULL, NULL, 0.0000, 0.7500, -0.7500, f.base,
    '2026-02-06', 1.0000000000, 1.0000000000, -0.7500, -0.7500,
    'statement', 'cash_lifecycle_v1', 'cash-tax', NULL, NULL, NULL, NULL
  );
  SELECT transaction.holding_id INTO v_holding_id
    FROM investment_transaction transaction WHERE transaction.id = purchase_id;

  ASSERT (SELECT current_units = 11.50000000 FROM holding WHERE id = v_holding_id),
    'only the acquisition and reinvestment change holding units';
  ASSERT investment_units_from_events(v_holding_id) = 11.50000000,
    'cash lifecycle unit effects remain rebuildable';
  ASSERT (SELECT count(*) = 2 FROM investment_unit_effect effect
           WHERE effect.holding_id = v_holding_id),
    'cash-only events must not manufacture unit effects';
  ASSERT (SELECT signed_units = 1.50000000 FROM investment_unit_effect
           WHERE investment_transaction_id = reinvestment_id),
    'a reinvestment creates its exact positive unit effect';
  ASSERT (SELECT acquired_units = 1.50000000 AND cost_basis_native = 3.0000
                  AND currency = f.base
            FROM acquisition_lot WHERE origin_transaction_id = reinvestment_id),
    'a reinvestment creates an acquisition-origin lot with evidenced gross basis';
  ASSERT (SELECT count(*) = 2 FROM acquisition_lot lot
           WHERE lot.holding_id = v_holding_id),
    'cash-only events must not manufacture acquisition lots';
  ASSERT (SELECT net_amount = 0 AND fx_rate_date IS NULL
                  AND fx_rate_to_base IS NULL AND fx_rate_to_secondary IS NULL
                  AND net_amount_base IS NULL AND net_amount_secondary IS NULL
            FROM investment_transaction WHERE id = reinvestment_id),
    'reinvestment must not manufacture an external cash flow';
  ASSERT (SELECT units IS NULL AND price IS NULL AND gross_amount = 5.0000
                  AND charges = 0.5000 AND withheld_tax = 0.5000 AND net_amount = 4.0000
            FROM investment_transaction WHERE id = distribution_id),
    'cash distribution keeps its exact frozen cash facts';
  ASSERT (SELECT units IS NULL AND price IS NULL AND gross_amount = 2.0000
            FROM investment_transaction WHERE id = interest_id),
    'interest keeps its exact frozen cash facts';
  ASSERT (SELECT units IS NULL AND price IS NULL AND gross_amount IS NULL
                  AND charges = 1.2500 AND net_amount = -1.2500
            FROM investment_transaction WHERE id = charge_id),
    'a standalone charge keeps its exact negative cash flow';
  ASSERT (SELECT units IS NULL AND price IS NULL AND gross_amount IS NULL
                  AND withheld_tax = 0.7500 AND net_amount = -0.7500
            FROM investment_transaction WHERE id = tax_id),
    'standalone withheld tax keeps its exact negative cash flow';
  RAISE NOTICE 'PASS 0034 exact cash lifecycle facts, effects and origin lots';
END $$;
ROLLBACK;

-- Existing disposal kinds remain admitted after the operation is replaced.
BEGIN;
DO $$
DECLARE f RECORD; holding_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0034_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    10.00000000, 2.000000, 20.0000, 0.0000, 0.0000, -20.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -20.0000, -20.0000,
    'statement', 'cash_disposal_v1', 'disposal-seed', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-02', 'sale',
    -1.00000000, 3.000000, 3.0000, 0.0000, 0.0000, 3.0000, f.base,
    '2026-02-02', 1.0000000000, 1.0000000000, 3.0000, 3.0000,
    'statement', 'cash_disposal_v1', 'disposal-sale', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-03', 'redemption',
    -1.00000000, 3.000000, 3.0000, 0.0000, 0.0000, 3.0000, f.base,
    '2026-02-03', 1.0000000000, 1.0000000000, 3.0000, 3.0000,
    'statement', 'cash_disposal_v1', 'disposal-redemption', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-04', 'maturity',
    -1.00000000, 3.000000, 3.0000, 0.0000, 0.0000, 3.0000, f.base,
    '2026-02-04', 1.0000000000, 1.0000000000, 3.0000, 3.0000,
    'statement', 'cash_disposal_v1', 'disposal-maturity', NULL, NULL, NULL, NULL
  );
  SELECT id INTO holding_id FROM holding
   WHERE account_id = f.account_1 AND security_id = f.security_1;
  ASSERT (SELECT current_units = 7.00000000 FROM holding WHERE id = holding_id),
    'sale, redemption and maturity retain their signed effects';
  ASSERT (SELECT count(*) = 4 FROM investment_transaction),
    'replacing the operation must retain all previously admitted kinds';
  RAISE NOTICE 'PASS 0034 existing acquisition and disposal behavior is preserved';
END $$;
ROLLBACK;

-- Invalid cash shapes and excess precision are refused without partial projections.
BEGIN;
DO $$
DECLARE f RECORD; before_events BIGINT; before_effects BIGINT; before_lots BIGINT;
        before_units NUMERIC; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0034_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    2.00000000, 2.000000, 4.0000, 0.0000, 0.0000, -4.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -4.0000, -4.0000,
    'statement', 'cash_refusal_v1', 'refusal-seed', NULL, NULL, NULL, NULL
  );
  SELECT count(*) INTO before_events FROM investment_transaction;
  SELECT count(*) INTO before_effects FROM investment_unit_effect;
  SELECT count(*) INTO before_lots FROM acquisition_lot;
  SELECT current_units INTO before_units FROM holding;

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'cash_distribution',
      1.00000000, NULL, 2.0000, 0.0000, 0.0000, 2.0000, f.base,
      '2026-02-02', 1.0000000000, 1.0000000000, 2.0000, 2.0000,
      'statement', 'cash_refusal_v1', 'bad-distribution', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a cash distribution accepted units';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_kind_fields_agree', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'interest',
      NULL, NULL, 2.0000, 1.0000, 1.0000, 0.0000, f.base,
      NULL, NULL, NULL, NULL, NULL,
      'statement', 'cash_refusal_v1', 'bad-interest', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'interest accepted a non-positive external cash flow';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_kind_fields_agree', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'reinvested_distribution',
      -1.00000000, 2.000000, 2.0000, 0.0000, 0.0000, 0.0000, f.base,
      NULL, NULL, NULL, NULL, NULL,
      'statement', 'cash_refusal_v1', 'bad-reinvestment', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a reinvestment accepted negative units';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_kind_fields_agree', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'charge',
      NULL, NULL, 1.0000, 1.0000, 0.0000, -1.0000, f.base,
      '2026-02-02', 1.0000000000, 1.0000000000, -1.0000, -1.0000,
      'statement', 'cash_refusal_v1', 'bad-charge', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a standalone charge accepted gross proceeds';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_kind_fields_agree', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'withheld_tax',
      NULL, NULL, NULL, 1.0000, 1.0000, -1.0000, f.base,
      '2026-02-02', 1.0000000000, 1.0000000000, -1.0000, -1.0000,
      'statement', 'cash_refusal_v1', 'bad-tax', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'withheld tax accepted a charge amount';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_kind_fields_agree', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'reinvested_distribution',
      1.000000001, 2.000000, 2.0000, 0.0000, 0.0000, 0.0000, f.base,
      NULL, NULL, NULL, NULL, NULL,
      'statement', 'cash_refusal_v1', 'bad-unit-scale', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a reinvestment silently rounded excess unit precision';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%units exceeds scale 8%', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'cash_distribution',
      NULL, NULL, 2.00001, 0.0000, 0.0000, 2.0000, f.base,
      '2026-02-02', 1.0000000000, 1.0000000000, 2.0000, 2.0000,
      'statement', 'cash_refusal_v1', 'bad-money-scale', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a cash event silently rounded excess money precision';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%money exceeds scale 4%', refusal;
  END;

  ASSERT (SELECT count(*) = before_events FROM investment_transaction),
    'cash shape and precision refusals leave the event set unchanged';
  ASSERT (SELECT count(*) = before_effects FROM investment_unit_effect),
    'cash shape and precision refusals leave unit effects unchanged';
  ASSERT (SELECT count(*) = before_lots FROM acquisition_lot),
    'cash shape and precision refusals leave lots unchanged';
  ASSERT (SELECT current_units = before_units FROM holding),
    'cash shape and precision refusals leave holding units unchanged';
  RAISE NOTICE 'PASS 0034 cash matrix, direction and precision fail atomically';
END $$;
ROLLBACK;

-- Cash lifecycle dedupe is idempotent, conflicts are atomic and corrections append.
BEGIN;
DO $$
DECLARE f RECORD; original_id UUID; replay_id UUID; correction_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0034_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  original_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'cash_distribution',
    NULL, NULL, 4.0000, 0.0000, 0.0000, 4.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, 4.0000, 4.0000,
    'statement', 'cash_dedupe_v1', 'cash-original', NULL, NULL, NULL, NULL
  );
  replay_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'cash_distribution',
    NULL, NULL, 4.0000, 0.0000, 0.0000, 4.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, 4.0000, 4.0000,
    'statement', 'cash_dedupe_v1', 'cash-original', NULL, NULL, NULL, NULL
  );
  ASSERT replay_id = original_id, 'identical cash source evidence returns its retained event';
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-01', 'cash_distribution',
      NULL, NULL, 5.0000, 0.0000, 0.0000, 5.0000, f.base,
      '2026-02-01', 1.0000000000, 1.0000000000, 5.0000, 5.0000,
      'statement', 'cash_dedupe_v1', 'cash-original', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'conflicting cash facts reused one source identity';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'conflicting investment event uses an existing dedupe identity', refusal;
  END;
  BEGIN
    UPDATE investment_transaction SET gross_amount = 5.0000 WHERE id = original_id;
    RAISE EXCEPTION 'an imported cash fact was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment transactions are immutable%', refusal;
  END;
  correction_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'cash_distribution',
    NULL, NULL, 5.0000, 0.0000, 0.0000, 5.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, 5.0000, 5.0000,
    'owner_entered', 'cash_correction_v1', 'cash-correction', NULL, NULL,
    original_id, 'owner supplied the replacement cash fact'
  );
  ASSERT (SELECT count(*) = 2 FROM investment_transaction),
    'a cash correction appends while retaining its immutable original';
  ASSERT (SELECT corrects_transaction_id = original_id
            FROM investment_transaction WHERE id = correction_id),
    'the cash correction identifies its original';
  RAISE NOTICE 'PASS 0034 cash dedupe, immutability and append-only correction';
END $$;
ROLLBACK;

-- Owner authorization and household-scoped targets fail before any projection is created.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0034_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM append_investment_transaction(
      f.account_2, f.security_2, '2026-02-01', 'cash_distribution',
      NULL, NULL, 1.0000, 0.0000, 0.0000, 1.0000, f.base,
      '2026-02-01', 1.0000000000, 1.0000000000, 1.0000, 1.0000,
      'statement', 'cash_scope_v1', 'foreign-account', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a cash event used another household account';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_2, '2026-02-01', 'cash_distribution',
      NULL, NULL, 1.0000, 0.0000, 0.0000, 1.0000, f.base,
      '2026-02-01', 1.0000000000, 1.0000000000, 1.0000, 1.0000,
      'statement', 'cash_scope_v1', 'foreign-security', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a cash event used another household security';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM holding),
    'household-scope refusals must not leave a holding';
  ASSERT NOT EXISTS (SELECT FROM investment_transaction),
    'household-scope refusals must not leave an event';
  RAISE NOTICE 'PASS 0034 owner authorization and household scope fail atomically';
END $$;
ROLLBACK;
