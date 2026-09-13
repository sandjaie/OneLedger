-- V30: generic investment instruments and dated values.
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE relation_name TEXT;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'account_valuation_period', 'security', 'security_price_source',
    'price_snapshot', 'valuation_snapshot'
  ] LOOP
    ASSERT to_regclass('public.' || relation_name) IS NOT NULL,
      relation_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = relation_name),
      'RLS must be enabled on ' || relation_name;
  END LOOP;

  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'account_valuation_period'::regclass
       AND conname = 'account_valuation_periods_do_not_overlap' AND contype = 'x'
  ), 'account valuation periods need a transactional exclusion constraint';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'security_price_source'::regclass
       AND conname = 'security_price_source_periods_do_not_overlap' AND contype = 'x'
  ), 'source periods need a transactional exclusion constraint';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'security_price_source'::regclass
       AND conname = 'security_price_source_identity_is_temporally_unique' AND contype = 'x'
  ), 'exact source codes need temporal uniqueness';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'valuation_snapshot'::regclass
       AND conname = 'fk_valuation_snapshot_document_household'
       AND pg_get_constraintdef(oid) LIKE
         'FOREIGN KEY (household_id, source_document_id) REFERENCES document(household_id, id)%'
  ), 'valuation evidence needs a household-leading composite foreign key';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'valuation_snapshot'::regclass
       AND conname = 'valuation_fx_rate_is_not_future'
  ), 'valuation FX evidence cannot be dated after the valuation';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'account'::regclass
       AND tgname = 'trg_account_valuation_children_remain_valid'
       AND NOT tgisinternal
  ), 'account edits need a database-owned valuation-history guard';

  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 6
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'price_snapshot'
       AND column_name = 'price'
  ), 'prices must be NUMERIC(20,6)';
  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 4
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'valuation_snapshot'
       AND column_name = 'value_native'
  ), 'valuation money must be NUMERIC(20,4)';
  ASSERT (
    SELECT numeric_precision = 20 AND numeric_scale = 10
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'valuation_snapshot'
       AND column_name = 'fx_rate_to_base'
  ), 'valuation FX must be NUMERIC(20,10)';
  RAISE NOTICE 'PASS 0027 structure, RLS and exact scales';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0027_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, p1 UUID, p2 UUID,
    ledger_1 UUID, holdings_1 UUID, snapshot_1 UUID, holdings_2 UUID,
    document_1 UUID, document_2 UUID,
    shared_security UUID, private_security_1 UUID, private_security_2 UUID,
    shared_source UUID, private_source_1 UUID, private_source_2 UUID,
    base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('instrument household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('instrument household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'instrument owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'instrument owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'instrument-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'instrument-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic cash account', 'savings', base, 'XZ', 0, '2026-01-01'
  ) RETURNING id INTO ledger_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic investment account', 'brokerage', base, 'XZ', 0, '2026-01-01'
  ) RETURNING id INTO holdings_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic opaque asset', 'property', base, 'XZ', 0, '2026-01-01'
  ) RETURNING id INTO snapshot_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h2, p2, 'Synthetic investment account two', 'brokerage', base, 'XY', 0,
    '2026-01-01'
  ) RETURNING id INTO holdings_2;

  INSERT INTO document (
    household_id, document_type, original_filename, storage_bucket, storage_path,
    mime_type, byte_size, sha256
  ) VALUES (
    h1, 'investment_statement', 'synthetic-one.pdf', 'local', 'synthetic/one',
    'application/pdf', 1, repeat('1', 64)
  ) RETURNING id INTO document_1;
  INSERT INTO document (
    household_id, document_type, original_filename, storage_bucket, storage_path,
    mime_type, byte_size, sha256
  ) VALUES (
    h2, 'investment_statement', 'synthetic-two.pdf', 'local', 'synthetic/two',
    'application/pdf', 1, repeat('2', 64)
  ) RETURNING id INTO document_2;

  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, isin
  ) VALUES (
    NULL, 'mutual_fund', 'Synthetic shared fund', base, 'XZ', 'equity',
    'XZ0000000017'
  ) RETURNING id INTO shared_security;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h1, 'equity', 'Synthetic private security one', base, 'XZ', 'equity',
    'SYN1', 'SYNX'
  ) RETURNING id INTO private_security_1;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h2, 'equity', 'Synthetic private security two', base, 'XY', 'equity',
    'SYN2', 'SYNX'
  ) RETURNING id INTO private_security_2;

  INSERT INTO security_price_source (
    household_id, security_id, source_key, source_version, source_security_code,
    effective_from
  ) VALUES (
    NULL, shared_security, 'daily_nav_text_v1', '1', 'SYN-SHARED-1', '2026-01-01'
  ) RETURNING id INTO shared_source;
  INSERT INTO security_price_source (
    household_id, security_id, source_key, source_version, source_security_code,
    effective_from
  ) VALUES (
    h1, private_security_1, 'owner_price_v1', '1', 'SYN-PRIVATE-1', '2026-01-01'
  ) RETURNING id INTO private_source_1;
  INSERT INTO security_price_source (
    household_id, security_id, source_key, source_version, source_security_code,
    effective_from
  ) VALUES (
    h2, private_security_2, 'owner_price_v1', '1', 'SYN-PRIVATE-2', '2026-01-01'
  ) RETURNING id INTO private_source_2;
  RETURN NEXT;
END $$;

-- Account valuation chooses exactly one source and changes only at a dated boundary.
BEGIN;
DO $$
DECLARE f RECORD; liability UUID; historical_snapshot UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();
  ASSERT (
    SELECT valuation_mode = 'ledger_balance' AND effective_from = '2026-01-01'
      AND effective_to IS NULL
      FROM account_valuation_period WHERE account_id = f.ledger_1
  ),
    'cash accounts default to their ledger balance';
  ASSERT (
    SELECT valuation_mode = 'holdings' AND effective_from = '2026-01-01'
      AND effective_to IS NULL
      FROM account_valuation_period WHERE account_id = f.holdings_1
  ),
    'investment accounts default to structured holdings';
  ASSERT (
    SELECT valuation_mode = 'snapshot' AND effective_from = '2026-01-01'
      AND effective_to IS NULL
      FROM account_valuation_period WHERE account_id = f.snapshot_1
  ),
    'opaque asset accounts default to dated snapshots';

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date, credit_limit
  ) VALUES (
    f.h1, f.p1, 'Synthetic liability', 'credit_card', f.base, 'XZ', 0,
    '2026-01-01', 100.0000
  ) RETURNING id INTO liability;

  BEGIN
    PERFORM transition_account_valuation_mode(liability, 'snapshot', '2026-06-01');
    RAISE EXCEPTION 'an unauthenticated mode transition was accepted';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  BEGIN
    PERFORM transition_account_valuation_mode(liability, 'snapshot', '2026-06-01');
    RAISE EXCEPTION 'a liability accepted a non-ledger valuation mode';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%liability accounts require ledger_balance%', refusal;
  END;

  PERFORM transition_account_valuation_mode(f.snapshot_1, 'holdings', '2026-06-01');
  ASSERT (
    SELECT count(*) = 2
      FROM account_valuation_period WHERE account_id = f.snapshot_1
  ), 'a transition appends rather than overwrites valuation history';
  ASSERT EXISTS (
    SELECT FROM account_valuation_period
     WHERE account_id = f.snapshot_1 AND valuation_mode = 'snapshot'
       AND effective_from = '2026-01-01' AND effective_to = '2026-05-31'
  ), 'the old valuation mode remains authoritative before the boundary';
  ASSERT EXISTS (
    SELECT FROM account_valuation_period
     WHERE account_id = f.snapshot_1 AND valuation_mode = 'holdings'
       AND effective_from = '2026-06-01' AND effective_to IS NULL
  ), 'the new valuation mode begins exactly at the boundary';

  INSERT INTO valuation_snapshot (
    household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
    fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
    method, confidence
  ) VALUES (
    f.h1, f.snapshot_1, '2026-03-31', 10.0000, f.base, '2026-03-31',
    1.0000000000, 1.0000000000, 10.0000, 10.0000, 'owner_entered', 'medium'
  ) RETURNING id INTO historical_snapshot;
  ASSERT historical_snapshot IS NOT NULL,
    'a historical snapshot correction is allowed inside the prior snapshot period';

  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
      method, confidence
    ) VALUES (
      f.h1, f.snapshot_1, '2026-06-01', 10.0000, f.base, '2026-06-01',
      1.0000000000, 1.0000000000, 10.0000, 10.0000, 'owner_entered', 'medium'
    );
    RAISE EXCEPTION 'a snapshot was accepted after the account changed to holdings';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%valuation snapshots require snapshot mode%', refusal;
  END;

  BEGIN
    INSERT INTO account_valuation_period (
      household_id, account_id, valuation_mode, effective_from
    ) VALUES (f.h1, f.holdings_1, 'holdings', '2026-02-01');
    RAISE EXCEPTION 'overlapping account valuation periods were accepted';
  EXCEPTION WHEN exclusion_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_valuation_periods_do_not_overlap', refusal;
  END;
  RAISE NOTICE 'PASS 0027 account valuation history is exclusive, dated and authorized';
END $$;
ROLLBACK;

-- Ordinary account edits preserve the child history or fail atomically.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();

  UPDATE account SET opening_balance_date = '2025-12-01' WHERE id = f.ledger_1;
  ASSERT (
    SELECT effective_from = '2025-12-01'
      FROM account_valuation_period WHERE account_id = f.ledger_1
  ), 'moving an anchor earlier extends the initial valuation period';

  UPDATE account SET opening_balance_date = '2026-02-01' WHERE id = f.holdings_1;
  ASSERT (
    SELECT effective_from = '2026-02-01'
      FROM account_valuation_period WHERE account_id = f.holdings_1
  ), 'moving an unused anchor later moves the initial valuation period';

  BEGIN
    UPDATE account
       SET account_type = 'credit_card', credit_limit = 100.0000
     WHERE id = f.holdings_2;
    RAISE EXCEPTION 'an account edit stranded holdings mode on a liability';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_liability_uses_ledger_value', refusal;
  END;
  ASSERT (SELECT account_type = 'brokerage' FROM account WHERE id = f.holdings_2),
    'the rejected liability edit is atomic';

  INSERT INTO valuation_snapshot (
    household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
    fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
    method, confidence
  ) VALUES (
    f.h1, f.snapshot_1, '2026-03-31', 10.0000, f.base, '2026-03-31',
    1.0000000000, 1.0000000000, 10.0000, 10.0000, 'owner_entered', 'medium'
  );

  BEGIN
    UPDATE account SET currency = f.secondary WHERE id = f.snapshot_1;
    RAISE EXCEPTION 'an account edit relabelled retained valuation currency';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_currency_retains_valuations', refusal;
  END;
  ASSERT (SELECT rtrim(currency) = rtrim(f.base) FROM account WHERE id = f.snapshot_1),
    'the rejected currency edit is atomic';

  BEGIN
    UPDATE account SET opened_on = '2026-04-01' WHERE id = f.snapshot_1;
    RAISE EXCEPTION 'an account edit stranded a valuation before its lifetime';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_lifetime_retains_valuations', refusal;
  END;
  BEGIN
    UPDATE account SET closed_on = '2026-03-01' WHERE id = f.snapshot_1;
    RAISE EXCEPTION 'an account edit stranded a valuation after its lifetime';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_lifetime_retains_valuations', refusal;
  END;

  BEGIN
    UPDATE account SET opening_balance_date = '2026-04-01' WHERE id = f.snapshot_1;
    RAISE EXCEPTION 'an account edit stranded a valuation before its new anchor';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'account_anchor_retains_valuations', refusal;
  END;
  ASSERT (
    SELECT opening_balance_date = '2026-01-01'
      FROM account WHERE id = f.snapshot_1
  ), 'the rejected anchor edit is atomic';
  RAISE NOTICE 'PASS 0027 parent account edits preserve valuation history';
END $$;
ROLLBACK;

-- Investment source identifiers use the existing private typed boundary and safe mask shape.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();
  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) VALUES
    (f.h1, f.holdings_1, 'folio', '****A123', 'A123',
      'SYN A-123', 'SYNA123', 'owner_entered'),
    (f.h1, f.holdings_1, 'brokerage_account', '****B456', 'B456',
      'SYN B-456', 'SYNB456', 'owner_entered');

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix,
      full_value, normalized_value, provenance
    ) VALUES (
      f.h1, f.holdings_1, 'folio', '••••A123', 'A123',
      'SYN A-123', 'SYNA123', 'owner_entered'
    );
    RAISE EXCEPTION 'an investment identifier accepted a noncanonical mask';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment_identifier_shape_is_safe%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix,
      full_value, normalized_value, provenance
    ) VALUES (
      f.h1, f.holdings_1, 'folio', '****A123', 'A123',
      'SYN A-123', 'SYNÄ123', 'owner_entered'
    );
    RAISE EXCEPTION 'an investment identifier accepted a non-ASCII normalized value';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment_identifier_shape_is_safe%', refusal;
  END;
  RAISE NOTICE 'PASS 0027 private investment identifier kinds and masks';
END $$;
ROLLBACK;

-- Shared securities remain global reference facts; private ones cannot be re-homed or crossed.
BEGIN;
DO $$
DECLARE f RECORD; duplicate_security UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();
  BEGIN
    INSERT INTO security_price_source (
      household_id, security_id, source_key, source_version, source_security_code,
      effective_from
    ) VALUES (
      f.h1, f.private_security_2, 'daily_nav_text_v1', '1', 'WRONG-SCOPE',
      '2026-01-01'
    );
    RAISE EXCEPTION 'a source mapping crossed households';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%security price source is outside its scope%', refusal;
  END;

  BEGIN
    UPDATE security SET household_id = f.h2 WHERE id = f.private_security_1;
    RAISE EXCEPTION 'a security was re-homed after source mapping';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'security household is immutable', refusal;
  END;

  BEGIN
    INSERT INTO security_price_source (
      household_id, security_id, source_key, source_version, source_security_code,
      effective_from
    ) VALUES (
      f.h1, f.private_security_1, 'owner_price_v1', '2', 'OVERLAP', '2026-02-01'
    );
    RAISE EXCEPTION 'overlapping mappings were accepted for one source';
  EXCEPTION WHEN exclusion_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'security_price_source_periods_do_not_overlap', refusal;
  END;

  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    f.h2, 'equity', 'Synthetic duplicate-code target', f.base, 'XY', 'equity',
    'SYN3', 'SYNX'
  ) RETURNING id INTO duplicate_security;
  BEGIN
    INSERT INTO security_price_source (
      household_id, security_id, source_key, source_version, source_security_code,
      effective_from
    ) VALUES (
      f.h2, duplicate_security, 'owner_price_v1', '1', 'SYN-PRIVATE-1',
      '2026-02-01'
    );
    RAISE EXCEPTION 'one exact source identity resolved to two securities at once';
  EXCEPTION WHEN exclusion_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'security_price_source_identity_is_temporally_unique', refusal;
  END;

  UPDATE security_price_source
     SET is_active = false, effective_to = '2026-03-31', deactivated_at = now()
   WHERE id = f.private_source_1;
  INSERT INTO security_price_source (
    household_id, security_id, source_key, source_version, source_security_code,
    effective_from
  ) VALUES (
    f.h2, duplicate_security, 'owner_price_v1', '1', 'SYN-PRIVATE-1',
    '2026-04-01'
  );
  ASSERT EXISTS (
    SELECT FROM security_price_source
     WHERE security_id = duplicate_security AND effective_from = '2026-04-01'
  ), 'an exact public resolver identity can be reassigned after its old period closes';
  RAISE NOTICE 'PASS 0027 security and source scope, tenancy and periods';
END $$;
ROLLBACK;

-- Dated prices retain exact observations, including conflicts, but only one accepted value.
BEGIN;
DO $$
DECLARE f RECORD; accepted UUID; conflicting UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();
  INSERT INTO price_snapshot (
    household_id, security_id, source_mapping_id, price_date, price, currency,
    is_accepted, accepted_at
  ) VALUES (
    f.h1, f.private_security_1, f.private_source_1, '2026-03-31', 12.345678,
    f.base, true, clock_timestamp()
  ) RETURNING id INTO accepted;
  INSERT INTO price_snapshot (
    household_id, security_id, source_mapping_id, price_date, price, currency
  ) VALUES (
    f.h1, f.private_security_1, f.private_source_1, '2026-03-31', 12.345679, f.base
  ) RETURNING id INTO conflicting;
  ASSERT (SELECT count(*) FROM price_snapshot WHERE price_date = '2026-03-31') = 2,
    'conflicting exact observations must both remain';

  BEGIN
    INSERT INTO price_snapshot (
      household_id, security_id, source_mapping_id, price_date, price, currency,
      is_accepted, accepted_at
    ) VALUES (
      f.h1, f.private_security_1, f.private_source_1, '2026-03-31', 12.345680,
      f.base, true, clock_timestamp()
    );
    RAISE EXCEPTION 'a second accepted price was accepted for one security/date';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%idx_price_snapshot_one_accepted%', refusal;
  END;

  UPDATE price_snapshot
     SET is_accepted = false, accepted_at = NULL
   WHERE id = accepted;
  UPDATE price_snapshot
     SET is_accepted = true, accepted_at = clock_timestamp()
   WHERE id = conflicting;
  ASSERT (
    SELECT price = 12.345679 AND is_accepted
      FROM price_snapshot WHERE id = conflicting
  ), 'an explicit conflict decision can select another immutable observation';

  BEGIN
    INSERT INTO price_snapshot (
      household_id, security_id, source_mapping_id, price_date, price, currency
    ) VALUES (
      f.h1, f.private_security_2, f.private_source_2, '2026-03-31', 1.000000, f.base
    );
    RAISE EXCEPTION 'a price crossed household scope';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%price observation is outside its scope%', refusal;
  END;

  BEGIN
    UPDATE price_snapshot SET price = 13.000000 WHERE id = accepted;
    RAISE EXCEPTION 'a dated price observation was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%price_snapshot is immutable%', refusal;
  END;

  BEGIN
    UPDATE security_price_source
       SET is_active = false, effective_to = '2026-03-30', deactivated_at = now()
     WHERE id = f.private_source_1;
    RAISE EXCEPTION 'a source period was closed before a retained observation';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%security price source cannot end before an observation%', refusal;
  END;
  RAISE NOTICE 'PASS 0027 exact, scoped and immutable price observations';
END $$;
ROLLBACK;

-- Opaque valuations are exact, frozen, account-scoped snapshots and never mix with holdings.
BEGIN;
DO $$
DECLARE f RECORD; snapshot UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();
  INSERT INTO valuation_snapshot (
    household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
    fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
    method, confidence, source_document_id
  ) VALUES (
    f.h1, f.snapshot_1, '2026-03-31', 100.0000, f.base, '2026-03-31',
    2.0000000000, 3.0000000000, 200.0000, 300.0000,
    'statement', 'high', f.document_1
  ) RETURNING id INTO snapshot;

  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
      method, confidence
    ) VALUES (
      f.h1, f.snapshot_1, '2026-04-30', 10.0000, f.base, '2026-05-01',
      1.0000000000, 1.0000000000, 10.0000, 10.0000,
      'owner_entered', 'medium'
    );
    RAISE EXCEPTION 'a valuation accepted future-dated FX evidence';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'valuation_fx_rate_is_not_future', refusal;
  END;

  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
      method, confidence
    ) VALUES (
      f.h1, f.holdings_1, '2026-03-31', 10.0000, f.base, '2026-03-31',
      1.0000000000, 1.0000000000, 10.0000, 10.0000,
      'owner_entered', 'medium'
    );
    RAISE EXCEPTION 'a snapshot was added to a structured holding account';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%valuation snapshots require snapshot mode%', refusal;
  END;

  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
      method, confidence, source_document_id
    ) VALUES (
      f.h1, f.snapshot_1, '2026-04-30', 10.0000, f.base, '2026-04-30',
      2.0000000000, 3.0000000000, 19.9999, 30.0000,
      'statement', 'high', f.document_1
    );
    RAISE EXCEPTION 'a snapshot accepted a wrong frozen reporting value';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%valuation_base_is_frozen%', refusal;
  END;

  BEGIN
    INSERT INTO valuation_snapshot (
      household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
      fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
      method, confidence, source_document_id
    ) VALUES (
      f.h1, f.snapshot_1, '2026-04-30', 10.0000, f.base, '2026-04-30',
      2.0000000000, 3.0000000000, 20.0000, 30.0000,
      'statement', 'high', f.document_2
    );
    RAISE EXCEPTION 'a snapshot accepted another household source document';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'fk_valuation_snapshot_document_household', refusal;
  END;

  BEGIN
    UPDATE document SET household_id = f.h2 WHERE id = f.document_1;
    RAISE EXCEPTION 'a referenced valuation document was re-homed';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'fk_valuation_snapshot_document_household', refusal;
  END;

  BEGIN
    DELETE FROM valuation_snapshot WHERE id = snapshot;
    RAISE EXCEPTION 'a dated valuation was deleted';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%valuation_snapshot is immutable%', refusal;
  END;
  RAISE NOTICE 'PASS 0027 exact, scoped and immutable valuation snapshots';
END $$;
ROLLBACK;

-- Authenticated reads see shared and own investment facts, never another household; anon sees none.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0027_fixture();
  INSERT INTO price_snapshot (
    household_id, security_id, source_mapping_id, price_date, price, currency
  ) VALUES
    (NULL, f.shared_security, f.shared_source, '2026-03-31', 10.000000, f.base),
    (f.h1, f.private_security_1, f.private_source_1, '2026-03-31', 11.000000, f.base),
    (f.h2, f.private_security_2, f.private_source_2, '2026-03-31', 12.000000, f.base);
  INSERT INTO valuation_snapshot (
    household_id, account_id, as_of_date, value_native, currency, fx_rate_date,
    fx_rate_to_base, fx_rate_to_secondary, value_base, value_secondary,
    method, confidence
  ) VALUES (
    f.h1, f.snapshot_1, '2026-03-31', 10.0000, f.base, '2026-03-31',
    1.0000000000, 1.0000000000, 10.0000, 10.0000, 'owner_entered', 'medium'
  );

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM security) = 2,
    'owner sees shared and own securities only';
  ASSERT (SELECT count(*) FROM security_price_source) = 2,
    'owner sees shared and own source mappings only';
  ASSERT (SELECT count(*) FROM price_snapshot) = 2,
    'owner sees shared and own prices only';
  ASSERT (SELECT count(*) FROM account_valuation_period) = 3,
    'owner sees only household account valuation periods';
  ASSERT (SELECT count(*) FROM valuation_snapshot) = 1,
    'owner sees only household valuations';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM security) = 0, 'anonymous security reads are empty';
  ASSERT (SELECT count(*) FROM account_valuation_period) = 0,
    'anonymous account valuation period reads are empty';
  ASSERT (SELECT count(*) FROM price_snapshot) = 0, 'anonymous price reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0027 authenticated investment RLS';
END $$;
ROLLBACK;

-- A populated V29 upgrade must not reinterpret existing accounts as structured evidence.
BEGIN;
-- Later migrations may depend on V30 relations. Remove their objects first so this historical
-- upgrade test can reconstruct the exact pre-V30 shape it is responsible for proving.
DROP FUNCTION allocate_investment_lots(UUID, UUID[], NUMERIC[]);
DROP FUNCTION unlink_investment_cash_allocations(UUID);
DROP FUNCTION confirm_investment_cash_allocations(
  UUID[], UUID[], investment_cash_allocation_role[], NUMERIC[]);
DROP TABLE investment_lot_allocation_decision;
DROP TABLE investment_lot_allocation;
DROP TABLE investment_cash_allocation;
DROP FUNCTION guard_investment_lot_allocation_write();
DROP TRIGGER trg_allocated_cash_classification_uses_operation ON transaction;
DROP FUNCTION guard_allocated_cash_classification();
DROP FUNCTION guard_investment_cash_allocation_write();
DROP FUNCTION investment_cash_type_for_role(investment_cash_allocation_role);
DROP TYPE investment_cash_allocation_role;
DROP FUNCTION assert_legacy_investment_cash_links_empty(BIGINT);
DROP FUNCTION record_investment_evidence(
  UUID, UUID, UUID, UUID, UUID, SMALLINT, INTEGER,
  TEXT, TEXT, TEXT, TEXT, TEXT);
DROP FUNCTION resolve_investment_review_item(
  UUID, investment_review_resolution_kind, UUID);
DROP TABLE investment_review_item;
DROP TABLE investment_evidence;
DROP TABLE investment_position_checkpoint;
DROP TABLE investment_event_draft;
DROP TABLE investment_import_group;
DROP TABLE investment_import;
DROP FUNCTION apply_investment_review_block();
DROP FUNCTION guard_investment_group_transition();
DROP FUNCTION preserve_investment_review_item();
DROP FUNCTION guard_investment_evidence_insert();
DROP FUNCTION preserve_investment_evidence();
DROP FUNCTION preserve_investment_evidence_target();
DROP FUNCTION assert_investment_staging_security_scope();
DROP FUNCTION preserve_investment_import();
DROP FUNCTION assert_investment_import_document();
DROP TYPE investment_review_resolution_kind;
DROP TYPE investment_review_status;
DROP TYPE investment_review_reason;
DROP TYPE investment_checkpoint_status;
DROP TYPE investment_draft_status;
DROP TYPE investment_group_status;
DROP TYPE investment_import_status;
ALTER TABLE valuation_snapshot
  DROP CONSTRAINT fk_valuation_snapshot_holding_household;
DROP TRIGGER trg_account_investment_history_remains_valid ON account;
DROP TRIGGER trg_investment_transaction_projects ON investment_transaction;
DROP TRIGGER trg_investment_transaction_uses_append ON investment_transaction;
DROP TRIGGER trg_investment_transaction_is_immutable ON investment_transaction;
DROP TRIGGER trg_investment_unit_effect_uses_append ON investment_unit_effect;
DROP TRIGGER trg_investment_unit_effect_is_immutable ON investment_unit_effect;
DROP TRIGGER trg_acquisition_lot_uses_append ON acquisition_lot;
DROP TRIGGER trg_acquisition_lot_is_immutable ON acquisition_lot;
DROP TRIGGER trg_holding_write_is_derived ON holding;
DROP FUNCTION append_investment_transaction(
  UUID, UUID, DATE, investment_event_kind,
  NUMERIC, NUMERIC, NUMERIC, NUMERIC, NUMERIC, NUMERIC, CHAR,
  DATE, NUMERIC, NUMERIC, NUMERIC, NUMERIC,
  investment_event_source, TEXT, TEXT, TEXT, TEXT, UUID, TEXT);
DROP FUNCTION project_investment_transaction();
DROP FUNCTION refresh_holding_projection(UUID);
DROP FUNCTION investment_units_from_events(UUID);
DROP FUNCTION guard_investment_history_insert();
DROP FUNCTION preserve_investment_history();
DROP FUNCTION guard_holding_write();
DROP FUNCTION preserve_account_investment_history();
DROP TABLE acquisition_lot;
DROP TABLE investment_unit_effect;
DROP TABLE investment_transaction;
DROP TABLE holding;
DROP TYPE investment_event_source;
DROP TYPE investment_event_kind;
DROP TRIGGER trg_account_valuation_period_initialized ON account;
DROP TRIGGER trg_account_valuation_children_remain_valid ON account;
DROP TRIGGER trg_account_valuation_period_scope ON account_valuation_period;
DROP TRIGGER trg_account_valuation_period_is_immutable ON account_valuation_period;
DROP TRIGGER trg_security_household_is_immutable ON security;
DROP TRIGGER trg_security_price_source_scope ON security_price_source;
DROP TRIGGER trg_security_price_source_identity_is_immutable ON security_price_source;
DROP TRIGGER trg_price_snapshot_scope ON price_snapshot;
DROP TRIGGER trg_price_snapshot_is_immutable ON price_snapshot;
DROP TRIGGER trg_valuation_snapshot_scope ON valuation_snapshot;
DROP TRIGGER trg_valuation_snapshot_is_immutable ON valuation_snapshot;
DROP FUNCTION initialize_account_valuation_period();
DROP FUNCTION transition_account_valuation_mode(UUID, account_valuation_mode, DATE);
DROP FUNCTION reconcile_account_valuation_children();
DROP FUNCTION assert_account_valuation_period_scope();
DROP FUNCTION preserve_account_valuation_period();
DROP FUNCTION preserve_security_household();
DROP FUNCTION assert_security_price_source_scope();
DROP FUNCTION preserve_security_price_source_identity();
DROP FUNCTION assert_price_snapshot_scope();
DROP FUNCTION preserve_price_snapshot_fact();
DROP FUNCTION assert_valuation_snapshot_scope();
DROP FUNCTION preserve_valuation_snapshot();
DROP TABLE valuation_snapshot;
DROP TABLE price_snapshot;
DROP TABLE security_price_source;
DROP TABLE security;
DROP TABLE account_valuation_period;
DROP TYPE valuation_confidence;
DROP TYPE valuation_method;
DROP TYPE asset_class;
DROP TYPE security_type;
DROP TYPE account_valuation_mode;
ALTER TABLE account_identifier
  DROP CONSTRAINT investment_identifier_shape_is_safe,
  DROP CONSTRAINT account_identifier_kind_is_known,
  ADD CONSTRAINT account_identifier_kind_is_known CHECK (
    kind IN ('bank_account', 'iban', 'card'));

DO $$
DECLARE h UUID; p UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('pre-v30 household', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'pre-v30 owner', true) RETURNING id INTO p;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES
    (h, p, 'Pre-v30 brokerage', 'brokerage', base, 'XZ', 0, '2025-01-01'),
    (h, p, 'Pre-v30 opaque asset', 'property', base, 'XZ', 0, '2025-01-01');
END $$;

\ir ../migrations/V30__investment_instruments_and_valuations.sql

DO $$
BEGIN
  ASSERT (
    SELECT count(*) = 2
      FROM account a
      JOIN account_valuation_period p ON p.account_id = a.id
     WHERE a.name IN ('Pre-v30 brokerage', 'Pre-v30 opaque asset')
       AND p.valuation_mode = 'ledger_balance'
       AND p.effective_from = a.opening_balance_date
       AND p.effective_to IS NULL
  ), 'V29 accounts must remain ledger-valued until an explicit owner transition';
  RAISE NOTICE 'PASS 0027 populated V29 upgrade preserves ledger semantics';
END $$;
ROLLBACK;
