-- V35: staged split ratios are exact, typed and evidence-preserving.
-- All identities and values below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE definition TEXT;
BEGIN
  ASSERT EXISTS (
    SELECT FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'investment_event_draft'
       AND column_name = 'unit_ratio' AND data_type = 'numeric'
       AND numeric_precision = 20 AND numeric_scale = 10 AND is_nullable = 'YES'
  ), 'draft ratio must be nullable NUMERIC(20,10)';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_event_draft'::regclass
       AND conname = 'investment_event_draft_ratio_is_valid'
  ), 'draft ratios need positive non-unity validation';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_event_draft'::regclass
       AND conname = 'investment_event_draft_ratio_shape_agrees'
  ), 'draft kinds and states need ratio-shape validation';
  SELECT pg_get_functiondef('preserve_investment_evidence_target()'::regprocedure)
    INTO definition;
  ASSERT position('NEW.unit_ratio' IN definition) > 0
     AND position('OLD.unit_ratio' IN definition) > 0,
    'attached evidence must preserve staged ratios';
  RAISE NOTICE 'PASS 0032 ratio structure and forward evidence guard';
END $$;

BEGIN;
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); household_id UUID; person_id UUID; user_id UUID;
  v_account_id UUID; v_security_id UUID; document_id UUID; import_id UUID; group_id UUID;
  purchase_id UUID; split_id UUID; holding_id UUID; committed_event_id UUID; refusal TEXT;
  draft_state investment_draft_status;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('ratio invariant household', base, secondary) RETURNING id INTO household_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household_id, 'ratio invariant owner', true) RETURNING id INTO person_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), household_id, person_id, 'ratio-owner@example.invalid', 'owner')
  RETURNING id INTO user_id;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    household_id, person_id, 'Synthetic ratio account', 'brokerage', base, 'XZ',
    0, '2026-01-01'
  ) RETURNING id INTO v_account_id;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (household_id, 'equity', 'Synthetic ratio security', base, 'XZ', 'equity')
  RETURNING id INTO v_security_id;
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    household_id, v_account_id, 'investment_statement', 'masked-synthetic.pdf', 'documents',
    'synthetic/ratio', 'application/pdf', 100, repeat('9', 64), 'parsed', user_id
  ) RETURNING id INTO document_id;
  INSERT INTO investment_import (
    household_id, document_id, source_contract, extraction_version
  ) VALUES (household_id, document_id, 'synthetic_statement_v1', '3')
  RETURNING id INTO import_id;
  INSERT INTO investment_import_group (household_id, import_id, group_number)
  VALUES (household_id, import_id, 1) RETURNING id INTO group_id;

  -- A V34-shaped caller can continue to omit the new nullable field.
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    household_id, group_id, 1, v_account_id, v_security_id, '2026-07-01',
    'purchase', 1.00000000, 10.000000, 10.0000, 0.0000, 0.0000, -10.0000,
    base, 'ready'
  ) RETURNING id INTO purchase_id;
  ASSERT (SELECT unit_ratio IS NULL FROM investment_event_draft WHERE id = purchase_id),
    'existing non-split drafts must upgrade with a null ratio';

  BEGIN
    UPDATE investment_event_draft SET unit_ratio = 2.0000000000 WHERE id = purchase_id;
    RAISE EXCEPTION 'a non-split draft accepted a ratio';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
  END;
  ASSERT (SELECT unit_ratio IS NULL FROM investment_event_draft WHERE id = purchase_id),
    'a refused non-split ratio must leave the draft unchanged';

  FOREACH refusal IN ARRAY ARRAY['0.0000000000', '-2.0000000000', '1.0000000000'] LOOP
    BEGIN
      INSERT INTO investment_event_draft (
        household_id, group_id, source_order, event_kind, units, unit_ratio, state
      ) VALUES (
        household_id, group_id, 2, 'split', NULL, refusal::NUMERIC, 'pending'
      );
      RAISE EXCEPTION 'a non-positive or unity split ratio was accepted';
    EXCEPTION WHEN check_violation THEN
      GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
      ASSERT refusal = 'investment_event_draft_ratio_is_valid', refusal;
    END;
  END LOOP;

  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, unit_ratio, charges, withheld_tax, net_amount, currency, state
  ) VALUES (
    household_id, group_id, 2, v_account_id, v_security_id, '2026-07-15',
    'split', 1.00000000, NULL, 0, 0, 0, base, 'pending'
  ) RETURNING id INTO split_id;
  BEGIN
    UPDATE investment_event_draft SET state = 'ready' WHERE id = split_id;
    RAISE EXCEPTION 'a pending split with source units resolved before transformation';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
  END;
  UPDATE investment_event_draft SET units = NULL WHERE id = split_id;
  BEGIN
    UPDATE investment_event_draft SET state = 'ready' WHERE id = split_id;
    RAISE EXCEPTION 'a ready split accepted no ratio';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
  END;
  UPDATE investment_event_draft SET unit_ratio = 2.0000000000, state = 'ready'
   WHERE id = split_id;
  ASSERT (SELECT unit_ratio = 2.0000000000 AND units IS NULL
            FROM investment_event_draft WHERE id = split_id),
    'a ready split must retain its exact ratio and no units';
  BEGIN
    UPDATE investment_event_draft SET units = 1.00000000 WHERE id = split_id;
    RAISE EXCEPTION 'a split accepted units';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
  END;

  PERFORM set_config('app.user_id', user_id::TEXT, true);
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO holding (
    household_id, account_id, security_id, current_units, opened_on, closed_on
  ) VALUES (
    household_id, v_account_id, v_security_id, 0, '2026-07-15', '2026-07-15'
  ) RETURNING id INTO holding_id;
  INSERT INTO investment_transaction (
    household_id, account_id, holding_id, security_id, event_date, event_kind,
    units, unit_ratio, price, gross_amount, charges, withheld_tax, net_amount,
    currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
    net_amount_base, net_amount_secondary, source_kind, source_contract, source_event_id
  ) VALUES (
    household_id, v_account_id, holding_id, v_security_id, '2026-07-01', 'purchase',
    1.00000000, NULL, 1.000000, 1.0000, 0, 0, -1.0000,
    base, '2026-07-01', 1, 1, -1.0000, -1.0000,
    'statement', 'synthetic_statement_v1', 'synthetic-prior-acquisition'
  );
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO investment_transaction (
    household_id, account_id, holding_id, security_id, event_date, event_kind,
    units, unit_ratio, price, gross_amount, charges, withheld_tax, net_amount,
    currency, source_kind, source_contract, source_event_id
  ) VALUES (
    household_id, v_account_id, holding_id, v_security_id, '2026-07-15', 'split',
    1.00000000, 2.0000000000, NULL, NULL, 0, 0, 0,
    base, 'statement', 'synthetic_statement_v1', 'synthetic-committed-split'
  ) RETURNING id INTO committed_event_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);

  FOREACH draft_state IN ARRAY ARRAY[
    'ready'::investment_draft_status,
    'duplicate'::investment_draft_status,
    'committed'::investment_draft_status
  ] LOOP
    BEGIN
      INSERT INTO investment_event_draft (
        household_id, group_id, source_order, account_id, security_id, event_date,
        event_kind, units, unit_ratio, charges, withheld_tax, net_amount, currency,
        state, committed_transaction_id
      ) VALUES (
        household_id, group_id, 3, v_account_id, v_security_id, '2026-07-15',
        'split', NULL, NULL, 0, 0, 0, base, draft_state,
        CASE WHEN draft_state = 'committed' THEN committed_event_id ELSE NULL END
      );
      RAISE EXCEPTION '% split accepted no ratio', draft_state;
    EXCEPTION WHEN check_violation THEN
      GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
      ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
    END;
    BEGIN
      INSERT INTO investment_event_draft (
        household_id, group_id, source_order, account_id, security_id, event_date,
        event_kind, units, unit_ratio, charges, withheld_tax, net_amount, currency,
        state, committed_transaction_id
      ) VALUES (
        household_id, group_id, 3, v_account_id, v_security_id, '2026-07-15',
        'split', 1.00000000, 2.0000000000, 0, 0, 0, base, draft_state,
        CASE WHEN draft_state = 'committed' THEN committed_event_id ELSE NULL END
      );
      RAISE EXCEPTION '% split accepted source units', draft_state;
    EXCEPTION WHEN check_violation THEN
      GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
      ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
    END;
  END LOOP;

  PERFORM record_investment_evidence(
    group_id, document_id, split_id, NULL, NULL, 1::SMALLINT, 1,
    'synthetic_statement_v1', '3', 'synthetic-split',
    'typed_action_v1', repeat('8', 64)
  );
  BEGIN
    UPDATE investment_event_draft SET unit_ratio = 3.0000000000 WHERE id = split_id;
    RAISE EXCEPTION 'an evidence-backed split changed ratio';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'evidence-backed investment draft facts are immutable', refusal;
  END;
  ASSERT (SELECT unit_ratio = 2.0000000000 FROM investment_event_draft WHERE id = split_id),
    'a refused ratio mutation must preserve the attached fact';
  RAISE NOTICE 'PASS 0032 safe upgrade, ratio shapes, ready state and immutability';
END $$;
ROLLBACK;

-- Rehearse V35 itself with a populated V34-shaped draft, not only the empty migration path.
BEGIN;
CREATE TEMP TABLE t0032_upgrade_ids (
  household_id UUID, account_id UUID, security_id UUID, group_id UUID, draft_id UUID
);
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); h UUID; p UUID; u UUID; a UUID; s UUID; d UUID; i UUID; g UUID;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('ratio upgrade household', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'ratio upgrade owner', true) RETURNING id INTO p;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h, p, 'ratio-upgrade@example.invalid', 'owner') RETURNING id INTO u;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h, p, 'Synthetic upgrade account', 'brokerage', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h, 'equity', 'Synthetic upgrade security', base, 'XZ', 'equity') RETURNING id INTO s;
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    h, a, 'investment_statement', 'masked-upgrade.pdf', 'documents', 'synthetic/ratio-upgrade',
    'application/pdf', 100, repeat('7', 64), 'parsed', u
  ) RETURNING id INTO d;
  INSERT INTO investment_import (household_id, document_id, source_contract, extraction_version)
  VALUES (h, d, 'synthetic_statement_v1', '2') RETURNING id INTO i;
  INSERT INTO investment_import_group (household_id, import_id, group_number)
  VALUES (h, i, 1) RETURNING id INTO g;
  INSERT INTO t0032_upgrade_ids VALUES (h, a, s, g, NULL);
END $$;

ALTER TABLE investment_event_draft
  DROP CONSTRAINT investment_event_draft_ratio_is_valid,
  DROP CONSTRAINT investment_event_draft_ratio_shape_agrees,
  DROP COLUMN unit_ratio;

DO $$
DECLARE f t0032_upgrade_ids%ROWTYPE; base CHAR(3);
BEGIN
  SELECT * INTO f FROM t0032_upgrade_ids;
  SELECT base_currency INTO base FROM household WHERE id = f.household_id;
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.household_id, f.group_id, 1, f.account_id, f.security_id, '2026-07-01',
    'split', 4.00000000, NULL, NULL, 0.0000, 0.0000, 0.0000,
    base, 'pending'
  ) RETURNING id INTO f.draft_id;
  UPDATE t0032_upgrade_ids SET draft_id = f.draft_id;
END $$;

\ir ../migrations/V35__investment_draft_unit_ratio.sql

DO $$
DECLARE refusal TEXT;
BEGIN
  ASSERT (SELECT d.unit_ratio IS NULL AND d.units = 4.00000000
            FROM investment_event_draft d JOIN t0032_upgrade_ids f ON f.draft_id = d.id),
    'V35 must preserve a pending split source row and add a null ratio';
  BEGIN
    UPDATE investment_event_draft SET state = 'ready'
     WHERE id = (SELECT draft_id FROM t0032_upgrade_ids);
    RAISE EXCEPTION 'a preserved pending split resolved with source units and no ratio';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
  END;
  UPDATE investment_event_draft SET units = NULL
   WHERE id = (SELECT draft_id FROM t0032_upgrade_ids);
  BEGIN
    UPDATE investment_event_draft SET state = 'ready'
     WHERE id = (SELECT draft_id FROM t0032_upgrade_ids);
    RAISE EXCEPTION 'a preserved split resolved without its ratio';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_event_draft_ratio_shape_agrees', refusal;
  END;
  UPDATE investment_event_draft SET unit_ratio = 2.0000000000, state = 'ready'
   WHERE id = (SELECT draft_id FROM t0032_upgrade_ids);
  ASSERT (SELECT d.units IS NULL AND d.unit_ratio = 2.0000000000 AND d.state = 'ready'
            FROM investment_event_draft d JOIN t0032_upgrade_ids f ON f.draft_id = d.id),
    'a preserved split resolves only after source units transform into a valid ratio';
  RAISE NOTICE 'PASS 0032 populated V34-to-V35 upgrade preserves existing drafts';
END $$;
ROLLBACK;
