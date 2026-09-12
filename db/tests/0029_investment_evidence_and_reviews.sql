-- V32: investment imports, atomic groups, evidence, checkpoints and review states.
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE relation_name TEXT;
BEGIN
  FOREACH relation_name IN ARRAY ARRAY[
    'investment_import', 'investment_import_group', 'investment_event_draft',
    'investment_position_checkpoint', 'investment_evidence', 'investment_review_item'
  ] LOOP
    ASSERT to_regclass('public.' || relation_name) IS NOT NULL,
      relation_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = relation_name),
      'RLS must be enabled on ' || relation_name;
  END LOOP;
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_import'::regclass
       AND conname = 'fk_investment_import_document_household'
  ), 'imports need a household-leading document foreign key';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'investment_evidence'::regclass
       AND conname = 'investment_evidence_has_one_target'
  ), 'evidence must name exactly one draft, checkpoint or committed event';
  ASSERT NOT EXISTS (
    SELECT FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name IN (
         'investment_import', 'investment_import_group', 'investment_event_draft',
         'investment_position_checkpoint', 'investment_evidence', 'investment_review_item')
       AND column_name LIKE '%filename%'
  ), 'investment staging must not persist a source filename';
  ASSERT EXISTS (
    SELECT FROM pg_proc
     WHERE oid = 'record_investment_evidence(uuid,uuid,uuid,uuid,uuid,smallint,integer,text,text,text,text,text)'::regprocedure
       AND prosecdef
  ), 'evidence attachment must use an owner-authorized operation';
  RAISE NOTICE 'PASS 0029 structure, RLS, scoped keys and filename-free evidence';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0029_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, p1 UUID, p2 UUID,
    account_1 UUID, account_2 UUID, security_1 UUID, security_2 UUID,
    document_1 UUID, document_2 UUID, import_1 UUID, import_2 UUID,
    group_1 UUID, group_1b UUID, group_2 UUID, base CHAR(3), secondary CHAR(3)
  ) LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('evidence household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('evidence household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'evidence owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'evidence owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'evidence-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'evidence-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic evidence account one', 'brokerage', base, 'XZ', 0,
    '2026-01-01'
  ) RETURNING id INTO account_1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h2, p2, 'Synthetic evidence account two', 'brokerage', base, 'XY', 0,
    '2026-01-01'
  ) RETURNING id INTO account_2;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h1, 'equity', 'Synthetic evidence security one', base, 'XZ', 'equity',
    'EVD1', 'SYNX'
  ) RETURNING id INTO security_1;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class, ticker, exchange
  ) VALUES (
    h2, 'equity', 'Synthetic evidence security two', base, 'XY', 'equity',
    'EVD2', 'SYNX'
  ) RETURNING id INTO security_2;

  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    h1, account_1, 'investment_statement', 'masked-investment.pdf', 'documents',
    'synthetic/evidence-one', 'application/pdf', 100, repeat('a', 64), 'parsed', u1
  ) RETURNING id INTO document_1;
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    h2, account_2, 'investment_statement', 'masked-investment.pdf', 'documents',
    'synthetic/evidence-two', 'application/pdf', 100, repeat('b', 64), 'parsed', u2
  ) RETURNING id INTO document_2;

  INSERT INTO investment_import (
    household_id, document_id, source_contract, extraction_version, status
  ) VALUES (h1, document_1, 'synthetic_statement_v1', '1', 'staged')
  RETURNING id INTO import_1;
  INSERT INTO investment_import (
    household_id, document_id, source_contract, extraction_version, status
  ) VALUES (h2, document_2, 'synthetic_statement_v1', '1', 'staged')
  RETURNING id INTO import_2;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (h1, import_1, 1, 'ready') RETURNING id INTO group_1;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (h1, import_1, 2, 'ready') RETURNING id INTO group_1b;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (h2, import_2, 1, 'ready') RETURNING id INTO group_2;
  RETURN NEXT;
END $$;

-- Import parents accept only same-household investment documents, remain tied to their source,
-- and allow one parent per document.
BEGIN;
DO $$
DECLARE f RECORD; foreign_document UUID; replacement_document UUID; bank_document UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    f.h2, f.account_2, 'investment_statement', 'masked-cross-scope.pdf', 'documents',
    'synthetic/cross-scope', 'application/pdf', 100, repeat('d', 64), 'parsed', f.u2
  ) RETURNING id INTO foreign_document;
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    f.h1, f.account_1, 'investment_statement', 'masked-replacement.pdf', 'documents',
    'synthetic/replacement', 'application/pdf', 100, repeat('e', 64), 'parsed', f.u1
  ) RETURNING id INTO replacement_document;
  BEGIN
    INSERT INTO investment_import (
      household_id, document_id, source_contract, extraction_version
    ) VALUES (f.h1, foreign_document, 'synthetic_statement_v1', '1');
    RAISE EXCEPTION 'an import crossed document households';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'fk_investment_import_document_household', refusal;
  END;
  BEGIN
    INSERT INTO investment_import (
      household_id, document_id, source_contract, extraction_version
    ) VALUES (f.h1, f.document_1, 'synthetic_statement_v1', '1');
    RAISE EXCEPTION 'one document created two investment import parents';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'uq_investment_import_document', refusal;
  END;
  BEGIN
    UPDATE investment_import SET document_id = replacement_document WHERE id = f.import_1;
    RAISE EXCEPTION 'an import parent was relabelled to another document';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment import identity is immutable%', refusal;
  END;
  BEGIN
    UPDATE investment_import SET source_contract = 'replacement_contract_v1'
     WHERE id = f.import_1;
    RAISE EXCEPTION 'an import parent source contract was relabelled';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment import identity is immutable%', refusal;
  END;
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    f.h1, f.account_1, 'bank_statement', 'masked-source.pdf', 'documents',
    'synthetic/not-investment', 'application/pdf', 100, repeat('c', 64), 'parsed', f.u1
  ) RETURNING id INTO bank_document;
  BEGIN
    INSERT INTO investment_import (
      household_id, document_id, source_contract, extraction_version
    ) VALUES (f.h1, bank_document, 'synthetic_statement_v1', '1');
    RAISE EXCEPTION 'a non-investment document became an investment import';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_import_uses_investment_document', refusal;
  END;
  RAISE NOTICE 'PASS 0029 import parent document scope, type and retained identity';
END $$;
ROLLBACK;

-- Review subjects cannot bypass their household-leading group scope with nullable composite keys.
BEGIN;
DO $$
DECLARE f RECORD; foreign_draft UUID; foreign_checkpoint UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h2, f.group_2, 1, f.account_2, f.security_2, '2026-02-01',
    'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
    f.base, 'ready'
  ) RETURNING id INTO foreign_draft;
  INSERT INTO investment_position_checkpoint (
    household_id, group_id, source_order, account_id, security_id, as_of_date,
    reported_units, currency, declared_unit_scale
  ) VALUES (
    f.h2, f.group_2, 2, f.account_2, f.security_2, '2026-02-28',
    1.00000000, f.base, 8
  ) RETURNING id INTO foreign_checkpoint;
  BEGIN
    INSERT INTO investment_review_item (household_id, draft_id, reason)
    VALUES (f.h1, foreign_draft, 'unrecognized_event');
    RAISE EXCEPTION 'a review bypassed draft household scope with a null group';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_review_staged_subject_has_group', refusal;
  END;
  BEGIN
    INSERT INTO investment_review_item (household_id, checkpoint_id, reason)
    VALUES (f.h1, foreign_checkpoint, 'unit_mismatch');
    RAISE EXCEPTION 'a review bypassed checkpoint household scope with a null group';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_review_staged_subject_has_group', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM investment_review_item),
    'refused cross-household reviews must leave no rows';
  RAISE NOTICE 'PASS 0029 review staged subjects require their scoped group';
END $$;
ROLLBACK;

-- The evidence operation must be the first-write boundary and fail closed without an owner.
BEGIN;
DO $$
DECLARE f RECORD; draft_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-01',
    'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_id;
  ASSERT current_setting('oneledger.investment_evidence_append', true) IS NULL,
    'fresh-session evidence test requires an unset append flag';
  BEGIN
    INSERT INTO investment_evidence (
      household_id, group_id, document_id, draft_id, page_number, row_number,
      source_contract, extraction_version, source_event_id,
      fingerprint_version, evidence_fingerprint
    ) VALUES (
      f.h1, f.group_1, f.document_1, draft_id, 1, 1,
      'synthetic_statement_v1', '1', 'source-event-direct', 'typed_v1', repeat('1', 64)
    );
    RAISE EXCEPTION 'a direct evidence insert bypassed the owner operation';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%record_investment_evidence%', refusal;
  END;
  BEGIN
    PERFORM record_investment_evidence(
      f.group_1, f.document_1, draft_id, NULL, NULL, 1::SMALLINT, 1,
      'synthetic_statement_v1', '1', 'source-event-unauthorized',
      'typed_v1', repeat('2', 64)
    );
    RAISE EXCEPTION 'an unauthenticated caller attached investment evidence';
  EXCEPTION WHEN insufficient_privilege THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'not authorized', refusal;
  END;
  ASSERT NOT EXISTS (SELECT FROM investment_evidence),
    'refused evidence writes must be atomic';
  RAISE NOTICE 'PASS 0029 evidence write boundary fails closed';
END $$;
ROLLBACK;

-- A summary checkpoint is reconciliation evidence and never manufactures an event.
BEGIN;
DO $$
DECLARE f RECORD; v_checkpoint_id UUID; evidence_id UUID; event_count BIGINT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SELECT count(*) INTO event_count FROM investment_transaction;
  INSERT INTO investment_position_checkpoint (
    household_id, group_id, source_order, account_id, security_id, as_of_date,
    reported_units, reported_cost_native, reported_value_native, currency,
    declared_unit_scale, reconciliation_status
  ) VALUES (
    f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-28',
    2.00000000, 10.0000, 12.0000, f.base, 8, 'pending'
  ) RETURNING id INTO v_checkpoint_id;
  evidence_id := record_investment_evidence(
    f.group_1, f.document_1, NULL, v_checkpoint_id, NULL, 1::SMALLINT, 1,
    'synthetic_summary_v1', '1', NULL, 'typed_v1', repeat('3', 64)
  );
  ASSERT evidence_id IS NOT NULL, 'checkpoint evidence must attach';
  ASSERT (SELECT count(*) = event_count FROM investment_transaction),
    'a summary checkpoint must create no investment event';
  ASSERT EXISTS (
    SELECT FROM investment_evidence
     WHERE id = evidence_id AND checkpoint_id = v_checkpoint_id
       AND draft_id IS NULL AND investment_transaction_id IS NULL
  ), 'summary evidence must retain only its checkpoint target';
  RAISE NOTICE 'PASS 0029 summary checkpoints create no event';
END $$;
ROLLBACK;

-- Repeating the same typed evidence returns one retained attachment.
BEGIN;
DO $$
DECLARE f RECORD; draft_id UUID; first_id UUID; replay_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-01',
    'purchase', 2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_id;
  first_id := record_investment_evidence(
    f.group_1, f.document_1, draft_id, NULL, NULL, 2::SMALLINT, 4,
    'synthetic_statement_v1', '1', 'source-event-repeat',
    'typed_v1', repeat('4', 64)
  );
  replay_id := record_investment_evidence(
    f.group_1, f.document_1, draft_id, NULL, NULL, 2::SMALLINT, 4,
    'synthetic_statement_v1', '1', 'source-event-repeat',
    'typed_v1', repeat('4', 64)
  );
  ASSERT replay_id = first_id, 'an exact evidence replay returns the retained row';
  ASSERT (SELECT count(*) = 1 FROM investment_evidence),
    'an exact evidence replay must not duplicate attachment rows';
  ASSERT NOT EXISTS (SELECT FROM investment_review_item),
    'an exact evidence replay is not a conflict';
  RAISE NOTICE 'PASS 0029 exact evidence replay is idempotent';
END $$;
ROLLBACK;

-- Conflicting evidence attaches nothing and blocks only the affected atomic group.
BEGIN;
DO $$
DECLARE f RECORD; draft_1 UUID; draft_2 UUID; first_id UUID; conflict_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-01',
    'purchase', 2.00000000, 5.000000, 10.0000, 0.0000, 0.0000, -10.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_1;
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h1, f.group_1b, 1, f.account_1, f.security_1, '2026-02-01',
    'purchase', 3.00000000, 5.000000, 15.0000, 0.0000, 0.0000, -15.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_2;
  first_id := record_investment_evidence(
    f.group_1, f.document_1, draft_1, NULL, NULL, 2::SMALLINT, 4,
    'synthetic_statement_v1', '1', 'source-event-conflict',
    'typed_v1', repeat('5', 64)
  );
  conflict_id := record_investment_evidence(
    f.group_1b, f.document_1, draft_2, NULL, NULL, 2::SMALLINT, 5,
    'synthetic_statement_v1', '1', 'source-event-conflict',
    'typed_v1', repeat('6', 64)
  );
  ASSERT first_id IS NOT NULL AND conflict_id IS NULL,
    'conflicting source evidence must not attach';
  ASSERT (SELECT count(*) = 1 FROM investment_evidence),
    'a conflict must preserve only the first evidence fact';
  ASSERT (SELECT status = 'ready' FROM investment_import_group WHERE id = f.group_1),
    'a conflict must not block an unrelated group';
  ASSERT (SELECT status = 'blocked' FROM investment_import_group WHERE id = f.group_1b),
    'a conflict must block its own atomic group';
  ASSERT EXISTS (
    SELECT FROM investment_review_item
     WHERE group_id = f.group_1b AND draft_id = draft_2
       AND reason = 'duplicate_conflict' AND is_blocking AND status = 'open'
  ), 'a conflict must retain one actionable blocking review';
  ASSERT (SELECT status = 'awaiting_review' FROM investment_import WHERE id = f.import_1),
    'a blocked group makes its import await review';
  RAISE NOTICE 'PASS 0029 conflicts attach nothing and block one group';
END $$;
ROLLBACK;

-- Review severity is fixed by reason, not selected by a caller.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  INSERT INTO investment_review_item (household_id, import_id, group_id, reason)
  SELECT f.h1, f.import_1, f.group_1, reason::investment_review_reason
    FROM unnest(ARRAY[
      'unsupported_format', 'unknown_security', 'ambiguous_account',
      'ambiguous_identifier', 'unrecognized_event', 'duplicate_conflict',
      'unit_mismatch', 'cost_mismatch', 'missing_price', 'unmatched_cash',
      'incomplete_lot_allocation', 'stale_valuation'
    ]) AS reason;
  ASSERT (
    SELECT count(*) = 8 FROM investment_review_item WHERE is_blocking
  ), 'the fixed matrix must classify eight blocking reasons';
  ASSERT (
    SELECT count(*) = 4 FROM investment_review_item WHERE NOT is_blocking
  ), 'the fixed matrix must classify four advisory reasons';
  RAISE NOTICE 'PASS 0029 review blocking severity is fixed';
END $$;
ROLLBACK;

-- A group cannot close around an open blocker; advisory review does not stop valid facts.
BEGIN;
DO $$
DECLARE f RECORD; review_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  INSERT INTO investment_review_item (household_id, import_id, group_id, reason)
  VALUES (f.h1, f.import_1, f.group_1, 'unit_mismatch') RETURNING id INTO review_id;
  ASSERT (SELECT status = 'blocked' FROM investment_import_group WHERE id = f.group_1),
    'an open blocking review derives blocked group state';
  BEGIN
    UPDATE investment_import_group
       SET status = 'committed', committed_at = now()
     WHERE id = f.group_1;
    RAISE EXCEPTION 'a group closed around an unresolved blocker';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_group_has_open_blocker', refusal;
  END;
  BEGIN
    UPDATE investment_review_item
       SET status = 'resolved', resolved_at = now(), resolved_by = f.u1,
           resolution_kind = 'evidence_confirmed', resolution_reference_id = gen_random_uuid()
     WHERE id = review_id;
    RAISE EXCEPTION 'a review item was closed without its validated resolution operation';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%validated resolution operation%', refusal;
  END;

  INSERT INTO investment_review_item (household_id, import_id, group_id, reason)
  VALUES (f.h1, f.import_1, f.group_1b, 'unmatched_cash');
  UPDATE investment_import_group
     SET status = 'committed', committed_at = now()
   WHERE id = f.group_1b;
  ASSERT (SELECT status = 'committed' FROM investment_import_group WHERE id = f.group_1b),
    'an advisory funding review does not block otherwise valid group facts';
  RAISE NOTICE 'PASS 0029 unresolved blockers refuse close while advisory review does not';
END $$;
ROLLBACK;

-- Invalid or cross-household rows roll back as one group without touching sibling groups.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  BEGIN
    INSERT INTO investment_event_draft (
      household_id, group_id, source_order, account_id, security_id, event_date,
      event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
      currency, state
    ) VALUES (
      f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-01',
      'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
      f.base, 'ready'
    );
    INSERT INTO investment_event_draft (
      household_id, group_id, source_order, account_id, security_id, event_date,
      event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
      currency, state
    ) VALUES (
      f.h1, f.group_1, 2, f.account_2, f.security_2, '2026-02-02',
      'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
      f.base, 'ready'
    );
    RAISE EXCEPTION 'a group accepted cross-household draft references';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal IN (
      'fk_investment_event_draft_account_household',
      'fk_investment_event_draft_security_household'
    ), refusal;
  END;
  ASSERT NOT EXISTS (
    SELECT FROM investment_event_draft WHERE group_id = f.group_1
  ), 'a failed group write must roll back every draft in that group attempt';
  ASSERT (SELECT status = 'ready' FROM investment_import_group WHERE id = f.group_1b),
    'a failed group write must not alter its sibling group';
  RAISE NOTICE 'PASS 0029 group writes roll back atomically and stay household-scoped';
END $$;
ROLLBACK;

-- A checkpoint can match only the account and security of its retained holding.
BEGIN;
DO $$
DECLARE f RECORD; other_account UUID; event_id UUID; holding_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    f.h1, f.p1, 'Synthetic checkpoint account', 'brokerage', f.base, 'XZ', 0,
    '2026-01-01'
  ) RETURNING id INTO other_account;
  event_id := append_investment_transaction(
    f.account_1, f.security_1, '2026-02-01', 'purchase',
    1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000, f.base,
    '2026-02-01', 1.0000000000, 2.0000000000, -5.0000, -10.0000,
    'owner_entered', 'checkpoint_test_v1', 'synthetic-checkpoint-event',
    NULL, NULL, NULL, NULL
  );
  SELECT event.holding_id INTO holding_id
    FROM investment_transaction event WHERE event.id = event_id;
  BEGIN
    INSERT INTO investment_position_checkpoint (
      household_id, group_id, source_order, account_id, security_id, holding_id,
      as_of_date, reported_units, currency, declared_unit_scale, reconciliation_status
    ) VALUES (
      f.h1, f.group_1, 1, other_account, f.security_1, holding_id,
      '2026-02-28', 1.00000000, f.base, 8, 'matched'
    );
    RAISE EXCEPTION 'a checkpoint matched an unrelated holding';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = CONSTRAINT_NAME;
    ASSERT refusal = 'investment_checkpoint_target_matches_holding', refusal;
  END;
  RAISE NOTICE 'PASS 0029 checkpoint target agrees with its holding';
END $$;
ROLLBACK;

-- Evidence and review facts are append-only.
BEGIN;
DO $$
DECLARE f RECORD; draft_id UUID; checkpoint_id UUID; evidence_id UUID;
  checkpoint_evidence_id UUID; review_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-01',
    'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_id;
  evidence_id := record_investment_evidence(
    f.group_1, f.document_1, draft_id, NULL, NULL, 1::SMALLINT, 1,
    'synthetic_statement_v1', '1', NULL, 'typed_v1', repeat('7', 64)
  );
  INSERT INTO investment_position_checkpoint (
    household_id, group_id, source_order, account_id, security_id, as_of_date,
    reported_units, currency, declared_unit_scale
  ) VALUES (
    f.h1, f.group_1, 2, f.account_1, f.security_1, '2026-02-28',
    1.00000000, f.base, 8
  ) RETURNING id INTO checkpoint_id;
  checkpoint_evidence_id := record_investment_evidence(
    f.group_1, f.document_1, NULL, checkpoint_id, NULL, 1::SMALLINT, 2,
    'synthetic_summary_v1', '1', NULL, 'typed_v1', repeat('8', 64)
  );
  INSERT INTO investment_review_item (household_id, import_id, group_id, draft_id, reason)
  VALUES (f.h1, f.import_1, f.group_1, draft_id, 'unrecognized_event')
  RETURNING id INTO review_id;
  BEGIN
    UPDATE investment_evidence SET row_number = 2 WHERE id = evidence_id;
    RAISE EXCEPTION 'investment evidence was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment_evidence is immutable%', refusal;
  END;
  BEGIN
    UPDATE investment_event_draft SET gross_amount = 6.0000 WHERE id = draft_id;
    RAISE EXCEPTION 'an evidence-backed draft fact was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%evidence-backed investment draft facts are immutable%', refusal;
  END;
  BEGIN
    UPDATE investment_position_checkpoint SET reported_units = 2.00000000
     WHERE id = checkpoint_id;
    RAISE EXCEPTION 'an evidence-backed checkpoint fact was edited';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%evidence-backed investment checkpoint facts are immutable%', refusal;
  END;
  ASSERT checkpoint_evidence_id IS NOT NULL,
    'checkpoint evidence must remain attached while source facts are protected';
  BEGIN
    DELETE FROM investment_review_item WHERE id = review_id;
    RAISE EXCEPTION 'an investment review item was deleted';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%investment_review_item is append-only%', refusal;
  END;
  RAISE NOTICE 'PASS 0029 evidence and review history are append-only';
END $$;
ROLLBACK;

-- Owners read only their household import surface; anonymous readers see none.
BEGIN;
DO $$
DECLARE f RECORD; draft_1 UUID; draft_2 UUID; evidence_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0029_fixture();
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-01',
    'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_1;
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    f.h2, f.group_2, 1, f.account_2, f.security_2, '2026-02-01',
    'purchase', 1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, -5.0000,
    f.base, 'ready'
  ) RETURNING id INTO draft_2;
  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  evidence_id := record_investment_evidence(
    f.group_1, f.document_1, draft_1, NULL, NULL, 1::SMALLINT, 1,
    'synthetic_statement_v1', '1', NULL, 'typed_v1', repeat('9', 64)
  );
  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  evidence_id := record_investment_evidence(
    f.group_2, f.document_2, draft_2, NULL, NULL, 1::SMALLINT, 1,
    'synthetic_statement_v1', '1', NULL, 'typed_v1', repeat('a', 64)
  );
  INSERT INTO investment_position_checkpoint (
    household_id, group_id, source_order, account_id, security_id, as_of_date,
    reported_units, currency, declared_unit_scale, reconciliation_status
  ) VALUES
    (f.h1, f.group_1, 1, f.account_1, f.security_1, '2026-02-28',
     1.00000000, f.base, 8, 'pending'),
    (f.h2, f.group_2, 1, f.account_2, f.security_2, '2026-02-28',
     1.00000000, f.base, 8, 'pending');
  INSERT INTO investment_review_item (household_id, import_id, group_id, reason)
  VALUES
    (f.h1, f.import_1, f.group_1, 'missing_price'),
    (f.h2, f.import_2, f.group_2, 'missing_price');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM investment_import) = 1,
    'owner sees only own investment imports';
  ASSERT (SELECT count(*) FROM investment_import_group) = 2,
    'owner sees only own investment groups';
  ASSERT (SELECT count(*) FROM investment_event_draft) = 1,
    'owner sees only own investment drafts';
  ASSERT (SELECT count(*) FROM investment_position_checkpoint) = 1,
    'owner sees only own checkpoints';
  ASSERT (SELECT count(*) FROM investment_evidence) = 1,
    'owner sees only own investment evidence';
  ASSERT (SELECT count(*) FROM investment_review_item) = 1,
    'owner sees only own investment reviews';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM investment_import) = 0,
    'anonymous investment import reads are empty';
  ASSERT (SELECT count(*) FROM investment_review_item) = 0,
    'anonymous investment review reads are empty';
  ASSERT (SELECT count(*) FROM investment_event_draft) = 0,
    'anonymous investment draft reads are empty';
  ASSERT (SELECT count(*) FROM investment_evidence) = 0,
    'anonymous investment evidence reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0029 authenticated investment-import RLS';
END $$;
ROLLBACK;
