-- V31: immutable investment events, rebuildable holdings and acquisition-origin lots.
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE relation_name TEXT;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'holding', 'investment_transaction', 'investment_unit_effect', 'acquisition_lot'
  ] LOOP
    ASSERT to_regclass('public.' || relation_name) IS NOT NULL,
      relation_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = relation_name),
      'RLS must be enabled on ' || relation_name;
  END LOOP;

  ASSERT (
    SELECT numeric_precision = 28 AND numeric_scale = 8
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'holding'
       AND column_name = 'current_units'
  ), 'holding units must be NUMERIC(28,8)';
  ASSERT (
    SELECT numeric_precision = 28 AND numeric_scale = 8
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_transaction'
       AND column_name = 'units'
  ), 'event units must be NUMERIC(28,8)';
  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 6
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_transaction'
       AND column_name = 'price'
  ), 'event prices must be NUMERIC(20,6)';
  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 4
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_transaction'
       AND column_name = 'net_amount'
  ), 'event money must be NUMERIC(20,4)';
  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 10
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_transaction'
       AND column_name = 'fx_rate_to_base'
  ), 'event FX rates must be NUMERIC(20,10)';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_transaction'::regclass
       AND conname = 'investment_event_kind_fields_agree'
  ), 'the event-kind field matrix must be a database constraint';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_transaction'::regclass
       AND conname = 'fk_investment_transaction_holding_scope'
  ), 'events need a household-leading holding foreign key';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'acquisition_lot'::regclass
       AND conname = 'fk_acquisition_lot_origin_scope'
  ), 'lots need a household-leading origin-event foreign key';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'valuation_snapshot'::regclass
       AND conname = 'valuation_snapshot_has_one_target'
  ), 'valuations must target exactly one account or holding';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'investment_transaction'::regclass
       AND tgname = 'trg_investment_transaction_uses_append'
       AND NOT tgisinternal
  ), 'event inserts must use the owner-authorized append boundary';
  RAISE NOTICE 'PASS 0028 structure, RLS, exact scales and scoped keys';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0028_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, p1 UUID, p2 UUID,
    account_1 UUID, account_2 UUID,
    security_1 UUID, security_2 UUID,
    base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('event household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('event household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'event owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'event owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'event-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'event-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic investment account one', 'brokerage', base, 'XZ', 0,
    '2026-01-01'
  ) RETURNING id INTO account_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h2, p2, 'Synthetic investment account two', 'brokerage', base, 'XY', 0,
    '2026-01-01'
  ) RETURNING id INTO account_2;

  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h1, 'equity', 'Synthetic event security one', base, 'XZ', 'equity',
    'EVT1', 'SYNX'
  ) RETURNING id INTO security_1;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h2, 'equity', 'Synthetic event security two', base, 'XY', 'equity',
    'EVT2', 'SYNX'
  ) RETURNING id INTO security_2;
  RETURN NEXT;
END $$;

-- The first direct projection write in a fresh session must fail closed.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  ASSERT current_setting('oneledger.investment_event_append', true) IS NULL,
    'fresh-session guard test requires an unset append flag';
  BEGIN
    INSERT INTO holding (
      household_id, account_id, security_id, current_units, opened_on, closed_on
    ) VALUES (
      f.h1, f.account_1, f.security_1, 0.00000000, '2026-02-01', '2026-02-01'
    );
    RAISE EXCEPTION 'a missing append flag allowed a direct holding write';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%holding projection is derived%', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM holding),
    'the refused fresh-session write must leave no holding';
  RAISE NOTICE 'PASS 0028 fresh-session projection writes fail closed';
END $$;
ROLLBACK;

-- Owner-authorized acquisitions create one event, one unit effect and one origin lot.
BEGIN;
DO $$
DECLARE f RECORD; event_id UUID; holding_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-01', 'purchase',
      4.00000000, 5.000000, 20.0000, 1.0000, 0.0000, -21.0000, f.base,
      '2026-02-01', 2.0000000000, 3.0000000000, -42.0000, -63.0000,
      'owner_entered', 'owner_entry_v1', 'event-001', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an unauthenticated investment event was accepted';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  event_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    4.00000000, 5.000000, 20.0000, 1.0000, 0.0000, -21.0000, f.base,
    '2026-02-01', 2.0000000000, 3.0000000000, -42.0000, -63.0000,
    'owner_entered', 'owner_entry_v1', 'event-001', NULL, NULL, NULL, NULL
  );
  ASSERT append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    4.00000000, 5.000000, 20.0000, 1.0000, 0.0000, -21.0000, f.base,
    '2026-02-01', 2.0000000000, 3.0000000000, -42.0000, -63.0000,
    'owner_entered', 'owner_entry_v1', 'event-001', NULL, NULL, NULL, NULL
  ) = event_id, 'an identical source event replay must return the retained event';
  SELECT event.holding_id INTO holding_id
    FROM investment_transaction event WHERE event.id = event_id;

  ASSERT (SELECT current_units = 4.00000000 FROM holding WHERE id = holding_id),
    'the holding projection must equal its committed unit effects';
  ASSERT investment_units_from_events(holding_id) = 4.00000000,
    'the materialized units must be rebuildable from immutable effects';
  ASSERT (SELECT count(*) = 1 FROM investment_unit_effect
           WHERE investment_transaction_id = event_id AND signed_units = 4.00000000),
    'an acquisition must create one exact positive unit effect';
  ASSERT (SELECT count(*) = 1 FROM acquisition_lot
           WHERE origin_transaction_id = event_id
             AND acquired_units = 4.00000000
             AND cost_basis_native = 21.0000),
    'an acquisition must create one origin lot with evidenced cost';
  ASSERT (SELECT opened_on = '2026-02-01' AND closed_on IS NULL
           FROM holding WHERE id = holding_id),
    'a positive holding remains open from its first effective event';
  RAISE NOTICE 'PASS 0028 owner-authorized acquisition creates effect, lot and projection';
END $$;
ROLLBACK;

-- Redemptions use negative units, close at zero and preserve the acquisition history.
BEGIN;
DO $$
DECLARE f RECORD; purchase_id UUID; redemption_id UUID; holding_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  purchase_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    4.00000000, 5.000000, 20.0000, 0.0000, 0.0000, -20.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -20.0000, -20.0000,
    'statement', 'statement_v1', 'event-002', NULL, NULL, NULL, NULL
  );
  SELECT event.holding_id INTO holding_id
    FROM investment_transaction event WHERE event.id = purchase_id;
  redemption_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-03-01', 'redemption',
    -4.00000000, 6.000000, 24.0000, 1.0000, 1.0000, 22.0000, f.base,
    '2026-03-01', 1.0000000000, 1.0000000000, 22.0000, 22.0000,
    'statement', 'statement_v1', 'event-003', NULL, NULL, NULL, NULL
  );

  ASSERT (SELECT current_units = 0.00000000 AND closed_on = '2026-03-01'
           FROM holding WHERE id = holding_id),
    'an exact redemption closes the retained holding at zero units';
  ASSERT investment_units_from_events(holding_id) = 0.00000000,
    'closed holding units remain reconstructable';
  ASSERT (SELECT count(*) = 1 FROM acquisition_lot
           WHERE origin_transaction_id = purchase_id),
    'closing a holding must not delete its acquisition-origin lot';
  ASSERT (SELECT count(*) = 1 FROM investment_unit_effect
           WHERE investment_transaction_id = redemption_id
             AND signed_units = -4.00000000),
    'a redemption must retain its exact negative unit effect';
  RAISE NOTICE 'PASS 0028 redemption closes units and preserves history';
END $$;
ROLLBACK;

-- The event-kind matrix, precision boundary and projection guard fail atomically.
BEGIN;
DO $$
DECLARE f RECORD; holding_id UUID; before_events BIGINT; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
    'statement', 'statement_v1', 'event-004', NULL, NULL, NULL, NULL
  );
  SELECT id INTO holding_id FROM holding
   WHERE account_id = f.account_1 AND security_id = f.security_1;
  SELECT count(*) INTO before_events FROM investment_transaction;

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'purchase',
      -1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-02', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
      'statement', 'statement_v1', 'event-bad-direction', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a purchase accepted negative units';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_kind_fields_agree', refusal;
  END;

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'purchase',
      1.000000001, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-02', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
      'statement', 'statement_v1', 'event-bad-scale', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an event silently rounded excess unit precision';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%units exceeds scale 8%', refusal;
  END;

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-02', 'adjustment',
      NULL, NULL, NULL, 0.0000, 0.0000, NULL, NULL,
      NULL, NULL, NULL, NULL, NULL,
      'statement', 'statement_v1', 'event-unsupported', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a meaning-free adjustment event was accepted';
  EXCEPTION WHEN invalid_text_representation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%adjustment%', refusal;
  END;

  BEGIN
    UPDATE holding SET current_units = 99.00000000 WHERE id = holding_id;
    RAISE EXCEPTION 'the holding projection was edited directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%holding projection is derived%', refusal;
  END;

  BEGIN
    INSERT INTO investment_transaction (
      household_id, account_id, holding_id, security_id, event_date, event_kind,
      units, price, gross_amount, charges, withheld_tax, net_amount, currency,
      fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
      net_amount_base, net_amount_secondary,
      source_kind, source_contract, source_event_id
    )
    SELECT household_id, account_id, investment_transaction.holding_id, security_id,
           event_date, event_kind, units, price, gross_amount, charges, withheld_tax,
           net_amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
           net_amount_base, net_amount_secondary,
           source_kind, source_contract, 'event-direct-bypass'
      FROM investment_transaction LIMIT 1;
    RAISE EXCEPTION 'an event bypassed the owner-authorized append operation';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%must use append_investment_transaction%', refusal;
  END;

  ASSERT (SELECT count(*) = before_events FROM investment_transaction),
    'every rejected append must leave the event set unchanged';
  ASSERT (SELECT current_units = 1.00000000 FROM holding WHERE id = holding_id),
    'every rejected append must leave the holding projection unchanged';
  RAISE NOTICE 'PASS 0028 direction, precision and unsupported changes fail atomically';
END $$;
ROLLBACK;

-- Immutable facts are corrected by an owner-entered replacement, never edited or deleted.
BEGIN;
DO $$
DECLARE f RECORD; original_id UUID; correction_id UUID; holding_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  original_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
    'statement', 'statement_v1', 'event-005', NULL, NULL, NULL, NULL
  );
  SELECT event.holding_id INTO holding_id
    FROM investment_transaction event WHERE event.id = original_id;

  BEGIN
    UPDATE investment_transaction SET units = 3.00000000 WHERE id = original_id;
    RAISE EXCEPTION 'an imported event was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment transactions are immutable%', refusal;
  END;
  BEGIN
    DELETE FROM investment_transaction WHERE id = original_id;
    RAISE EXCEPTION 'an imported event was deleted';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment transactions are immutable%', refusal;
  END;

  correction_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    3.00000000, 5.000000, 15.0000, 0.0000, 0.0000, -15.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -15.0000, -15.0000,
    'owner_entered', 'owner_correction_v1', 'event-005-c1', NULL, NULL,
    original_id, 'owner supplied the exact replacement fact'
  );
  ASSERT (SELECT count(*) = 2 FROM investment_transaction),
    'a correction must append and retain its original';
  ASSERT (SELECT corrects_transaction_id = original_id
           FROM investment_transaction WHERE id = correction_id),
    'the replacement must identify the fact it corrects';
  ASSERT investment_units_from_events(holding_id) = 3.00000000,
    'rebuilds must use the latest replacement rather than double-counting the original';
  ASSERT (SELECT current_units = 3.00000000 FROM holding WHERE id = holding_id),
    'the holding projection must refresh after an append-only correction';
  ASSERT (SELECT count(*) = 2 FROM acquisition_lot),
    'both original and correction lots remain as immutable history';
  RAISE NOTICE 'PASS 0028 append-only correction preserves originals and rebuilds units';
END $$;
ROLLBACK;

-- Dedupe returns an identical event and refuses conflicting evidence without partial rows.
BEGIN;
DO $$
DECLARE f RECORD; first_id UUID; replay_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  first_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
    'statement', 'statement_v1', NULL, 'typed_facts_v1', repeat('a', 64), NULL, NULL
  );
  replay_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
    'statement', 'statement_v1', NULL, 'typed_facts_v1', repeat('a', 64), NULL, NULL
  );
  ASSERT replay_id = first_id, 'an identical normalized event must be idempotent';
  ASSERT (SELECT count(*) = 1 FROM investment_transaction),
    'an idempotent replay must not duplicate the event';
  ASSERT (SELECT count(*) = 1 FROM acquisition_lot),
    'an idempotent replay must not duplicate the lot';

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-01', 'purchase',
      2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
      '2026-02-01', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
      'statement', 'statement_v1', NULL, 'typed_facts_v1', repeat('a', 64), NULL, NULL
    );
    RAISE EXCEPTION 'conflicting facts reused one dedupe identity';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'conflicting investment event uses an existing dedupe identity', refusal;
  END;
  ASSERT (SELECT count(*) = 1 FROM investment_transaction),
    'a dedupe conflict must leave the original event set intact';
  RAISE NOTICE 'PASS 0028 dedupe is idempotent and conflicts fail atomically';
END $$;
ROLLBACK;

-- Household scope is enforced before creating a holding, event, effect or lot.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM append_investment_transaction(
      f.account_2, f.security_2, '2026-02-01', 'purchase',
      1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
      'statement', 'statement_v1', 'event-cross-account', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an event used another household account';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_2, '2026-02-01', 'purchase',
      1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
      'statement', 'statement_v1', 'event-cross-security', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an event used another household security';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  ASSERT (SELECT count(*) = 0 FROM holding),
    'cross-household refusals must not leave a holding';
  ASSERT (SELECT count(*) = 0 FROM investment_transaction),
    'cross-household refusals must not leave an event';
  RAISE NOTICE 'PASS 0028 cross-household references fail before any write';
END $$;
ROLLBACK;

-- Parent edits cannot relabel or strand retained immutable investment facts.
BEGIN;
DO $$
DECLARE f RECORD; event_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  event_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
    'statement', 'statement_v1', 'event-parent-guard', NULL, NULL, NULL, NULL
  );

  BEGIN
    UPDATE security SET currency = f.secondary WHERE id = f.security_1;
    RAISE EXCEPTION 'a security edit relabelled retained investment facts';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'security_currency_retains_investment_facts', refusal;
  END;
  ASSERT (SELECT rtrim(currency) = rtrim(f.base) FROM security WHERE id = f.security_1),
    'the rejected security currency edit is atomic';

  BEGIN
    UPDATE account SET opened_on = '2026-03-01' WHERE id = f.account_1;
    RAISE EXCEPTION 'an account open date stranded a retained investment event';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_lifetime_retains_investment_events', refusal;
  END;
  BEGIN
    UPDATE account SET closed_on = '2026-01-31' WHERE id = f.account_1;
    RAISE EXCEPTION 'an account close date stranded a retained investment event';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_lifetime_retains_investment_events', refusal;
  END;
  BEGIN
    UPDATE account SET opening_balance_date = '2026-03-01' WHERE id = f.account_1;
    RAISE EXCEPTION 'an account anchor stranded a retained investment event';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_anchor_retains_investment_events', refusal;
  END;
  BEGIN
    PERFORM transition_account_valuation_mode(
      f.account_1, 'ledger_balance', '2026-01-15');
    RAISE EXCEPTION 'a valuation transition stranded a retained investment event';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_valuation_transition_retains_investment_events', refusal;
  END;
  ASSERT EXISTS (
    SELECT FROM investment_transaction event
    JOIN account_valuation_period period ON period.account_id = event.account_id
     WHERE event.id = event_id AND period.valuation_mode = 'holdings'
       AND event.event_date >= period.effective_from AND period.effective_to IS NULL
  ), 'rejected parent edits leave the event inside its holdings period';
  RAISE NOTICE 'PASS 0028 parent edits preserve retained investment facts';
END $$;
ROLLBACK;

-- Every currently appendable kind and exact input scale is enforced at the operation boundary.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    10.00000000, 5.000000, 50.0000, 0.0000, 0.0000, -50.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -50.0000, -50.0000,
    'statement', 'matrix_v1', 'matrix-purchase', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-02', 'recurring_purchase',
    2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
    '2026-02-02', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
    'statement', 'matrix_v1', 'matrix-recurring', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-03', 'sale',
    -3.00000000, 6.000000, 18.0000, 1.0000, 1.0000, 16.0000, f.base,
    '2026-02-03', 1.0000000000, 1.0000000000, 16.0000, 16.0000,
    'statement', 'matrix_v1', 'matrix-sale', NULL, NULL, NULL, NULL
  );
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-04', 'maturity',
    -1.00000000, 6.000000, 6.0000, 0.0000, 0.0000, 6.0000, f.base,
    '2026-02-04', 1.0000000000, 1.0000000000, 6.0000, 6.0000,
    'statement', 'matrix_v1', 'matrix-maturity', NULL, NULL, NULL, NULL
  );
  ASSERT (SELECT current_units = 8.00000000 FROM holding),
    'all appendable acquisition and disposal kinds use their signed unit effects';
  ASSERT (SELECT count(*) = 2 FROM acquisition_lot),
    'both appendable acquisition kinds create origin lots';

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-05', 'cash_distribution',
      NULL, NULL, 1.0000, 0.0000, 0.0000, 1.0000, f.base,
      '2026-02-05', 1.0000000000, 1.0000000000, 1.0000, 1.0000,
      'statement', 'matrix_v1', 'matrix-deferred', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'a deferred lifecycle kind was appended prematurely';
  EXCEPTION WHEN feature_not_supported THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%requires a later lifecycle operation%', refusal;
  END;

  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-05', 'purchase',
      1.00000000, 5.0000001, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-05', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
      'statement', 'matrix_v1', 'matrix-price-scale', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an event silently rounded excess price precision';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%price exceeds scale 6%', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-05', 'purchase',
      1.00000000, 5.000000, 5.00001, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-05', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
      'statement', 'matrix_v1', 'matrix-money-scale', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an event silently rounded excess money precision';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%money exceeds scale 4%', refusal;
  END;
  BEGIN
    PERFORM append_investment_transaction(
      f.account_1, f.security_1, '2026-02-05', 'purchase',
      1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
      '2026-02-05', 1.00000000001, 1.0000000000, -5.0000, -5.0000,
      'statement', 'matrix_v1', 'matrix-fx-scale', NULL, NULL, NULL, NULL
    );
    RAISE EXCEPTION 'an event silently rounded excess FX precision';
  EXCEPTION WHEN numeric_value_out_of_range THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%FX rate exceeds scale 10%', refusal;
  END;
  RAISE NOTICE 'PASS 0028 appendable kinds and all exact scales';
END $$;
ROLLBACK;

-- Holding valuations use the alternative target and immutable child facts remain append-only.
BEGIN;
DO $$
DECLARE f RECORD; event_id UUID; v_holding_id UUID; lot_id UUID; effect_id UUID;
        refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  event_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
    'statement', 'holding_value_v1', 'holding-value-event', NULL, NULL, NULL, NULL
  );
  SELECT event.holding_id INTO v_holding_id
    FROM investment_transaction event WHERE event.id = event_id;
  SELECT id INTO lot_id FROM acquisition_lot WHERE origin_transaction_id = event_id;
  SELECT id INTO effect_id FROM investment_unit_effect
   WHERE investment_transaction_id = event_id;

  INSERT INTO valuation_snapshot (
    household_id, holding_id, as_of_date, value_native, currency, fx_rate_date,
    fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
    method, confidence
  ) VALUES (
    f.h1, v_holding_id, '2026-02-28', 10.0000, f.base, '2026-02-28',
    1.0000000000, 1.0000000000, 10.0000, 10.0000, 'owner_entered', 'medium'
  );
  ASSERT EXISTS (
    SELECT FROM valuation_snapshot snapshot
     WHERE snapshot.holding_id = v_holding_id AND snapshot.account_id IS NULL
  ), 'a holding valuation has exactly the holding target';

  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, holding_id, as_of_date, value_native, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
      method, confidence
    ) VALUES (
      f.h1, v_holding_id, '2026-03-01', 10.0000, f.secondary, '2026-03-01',
      1.0000000000, 1.0000000000, 10.0000, 10.0000, 'owner_entered', 'medium'
    );
    RAISE EXCEPTION 'a holding valuation accepted the wrong currency';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'valuation_currency_matches_target', refusal;
  END;
  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, account_id, holding_id, as_of_date, value_native, currency,
      fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, value_base,
      value_secondary, method, confidence
    ) VALUES (
      f.h1, f.account_1, v_holding_id, '2026-03-01', 10.0000, f.base,
      '2026-03-01', 1.0000000000, 1.0000000000, 10.0000, 10.0000,
      'owner_entered', 'medium'
    );
    RAISE EXCEPTION 'a valuation accepted both account and holding targets';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'valuation_snapshot_has_one_target', refusal;
  END;

  BEGIN
    UPDATE investment_unit_effect SET signed_units = 3.00000000 WHERE id = effect_id;
    RAISE EXCEPTION 'an immutable unit effect was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment_unit_effect is immutable%', refusal;
  END;
  BEGIN
    DELETE FROM acquisition_lot WHERE id = lot_id;
    RAISE EXCEPTION 'an immutable acquisition lot was deleted';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%acquisition_lot is immutable%', refusal;
  END;
  RAISE NOTICE 'PASS 0028 holding valuation target and immutable child history';
END $$;
ROLLBACK;

-- Owners read only their own event history; anonymous readers see none.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0028_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  PERFORM append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -5.0000, -5.0000,
    'statement', 'statement_v1', 'event-006', NULL, NULL, NULL, NULL
  );
  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  PERFORM append_investment_transaction(
    f.account_2, f.security_2, '2026-02-01', 'purchase',
    2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000, f.base,
    '2026-02-01', 1.0000000000, 1.0000000000, -10.0000, -10.0000,
    'statement', 'statement_v1', 'event-007', NULL, NULL, NULL, NULL
  );

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM holding) = 1, 'owner sees only own holdings';
  ASSERT (SELECT count(*) FROM investment_transaction) = 1,
    'owner sees only own investment events';
  ASSERT (SELECT count(*) FROM investment_unit_effect) = 1,
    'owner sees only own unit effects';
  ASSERT (SELECT count(*) FROM acquisition_lot) = 1,
    'owner sees only own acquisition lots';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM holding) = 0, 'anonymous holding reads are empty';
  ASSERT (SELECT count(*) FROM investment_transaction) = 0,
    'anonymous event reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0028 authenticated investment-event RLS';
END $$;
ROLLBACK;
