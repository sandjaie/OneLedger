-- V36: source-contract-aware evidence replay and overlap lookup support.
-- All names, identifiers and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE index_definition TEXT;
BEGIN
  ASSERT (
    SELECT count(*) = 1
      FROM pg_proc
     WHERE oid =
       'record_investment_evidence(uuid,uuid,uuid,uuid,uuid,smallint,integer,text,text,text,text,text)'::regprocedure
       AND prosecdef
  ), 'V36 must preserve the evidence operation signature and owner boundary';

  SELECT indexdef INTO index_definition FROM pg_indexes
   WHERE schemaname = 'public'
     AND indexname = 'idx_investment_evidence_document_fingerprint';
  ASSERT index_definition LIKE
    '%(household_id, document_id, source_contract, fingerprint_version, evidence_fingerprint)%'
    AND index_definition LIKE '%source_event_id IS NULL%',
    coalesce(index_definition, 'missing document fingerprint index');
  ASSERT to_regclass('idx_investment_evidence_source_identity_overlap') IS NOT NULL,
    'typed source identity overlap needs a household/source-contract index';
  ASSERT to_regclass('idx_investment_evidence_fingerprint_overlap') IS NOT NULL,
    'fingerprint overlap needs a household/source-contract index';
  RAISE NOTICE 'PASS 0033 operation signature and overlap indexes';
END $$;

BEGIN;
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); household_id UUID; person_id UUID; user_id UUID;
  account_id UUID; security_id UUID; document_id UUID; import_id UUID;
  group_1 UUID; group_2 UUID; group_3 UUID; group_4 UUID;
  draft_1 UUID; draft_2 UUID; draft_3 UUID; draft_4 UUID;
  evidence_1 UUID; evidence_2 UUID; evidence_3 UUID; evidence_4 UUID;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('source scope household', base, secondary) RETURNING id INTO household_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household_id, 'source scope owner', true) RETURNING id INTO person_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), household_id, person_id, 'source-scope@example.invalid', 'owner')
  RETURNING id INTO user_id;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    household_id, person_id, 'Synthetic source scope account', 'brokerage', base, 'XZ',
    0, '2026-01-01'
  ) RETURNING id INTO account_id;
  INSERT INTO security (
    household_id, security_type, name, currency, country, asset_class
  ) VALUES (
    household_id, 'mutual_fund', 'Synthetic source scope security', base, 'XZ', 'equity'
  ) RETURNING id INTO security_id;
  INSERT INTO document (
    household_id, account_id, document_type, original_filename, storage_bucket,
    storage_path, mime_type, byte_size, sha256, parsed_status, uploaded_by
  ) VALUES (
    household_id, account_id, 'investment_statement', 'masked-source.pdf', 'documents',
    'synthetic/source-scope', 'application/pdf', 100, repeat('d', 64), 'parsed', user_id
  ) RETURNING id INTO document_id;
  INSERT INTO investment_import (
    household_id, document_id, source_contract, extraction_version
  ) VALUES (
    household_id, document_id, 'synthetic_statement_v1', '1'
  ) RETURNING id INTO import_id;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (household_id, import_id, 1, 'ready') RETURNING id INTO group_1;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (household_id, import_id, 2, 'ready') RETURNING id INTO group_2;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (household_id, import_id, 3, 'ready') RETURNING id INTO group_3;
  INSERT INTO investment_import_group (household_id, import_id, group_number, status)
  VALUES (household_id, import_id, 4, 'ready') RETURNING id INTO group_4;

  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    household_id, group_1, 1, account_id, security_id, '2026-03-01',
    'purchase', 1.00000000, 5.000000, 5.0000, 0, 0, -5.0000, base, 'ready'
  ) RETURNING id INTO draft_1;
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    household_id, group_2, 2, account_id, security_id, '2026-03-02',
    'purchase', 2.00000000, 5.000000, 10.0000, 0, 0, -10.0000, base, 'ready'
  ) RETURNING id INTO draft_2;
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    household_id, group_3, 3, account_id, security_id, '2026-03-03',
    'purchase', 3.00000000, 5.000000, 15.0000, 0, 0, -15.0000, base, 'ready'
  ) RETURNING id INTO draft_3;
  INSERT INTO investment_event_draft (
    household_id, group_id, source_order, account_id, security_id, event_date,
    event_kind, units, price, gross_amount, charges, withheld_tax, net_amount,
    currency, state
  ) VALUES (
    household_id, group_4, 4, account_id, security_id, '2026-03-04',
    'purchase', 4.00000000, 5.000000, 20.0000, 0, 0, -20.0000, base, 'ready'
  ) RETURNING id INTO draft_4;

  PERFORM set_config('app.user_id', user_id::TEXT, true);
  evidence_1 := record_investment_evidence(
    group_1, document_id, draft_1, NULL, NULL, 1::SMALLINT, 1,
    'synthetic_statement_v1', '1', 'EVENT-A', 'typed_event_v1', repeat('a', 64)
  );
  evidence_2 := record_investment_evidence(
    group_2, document_id, draft_2, NULL, NULL, 1::SMALLINT, 2,
    'synthetic_statement_v1', '1', 'EVENT-B', 'typed_event_v1', repeat('a', 64)
  );
  ASSERT evidence_1 IS NOT NULL AND evidence_2 IS NOT NULL AND evidence_1 <> evidence_2,
    'distinct authoritative source identities must not collide on a shared fingerprint';

  evidence_3 := record_investment_evidence(
    group_3, document_id, draft_3, NULL, NULL, 1::SMALLINT, 3,
    'synthetic_statement_v1', '1', NULL, 'typed_event_v1', repeat('a', 64)
  );
  ASSERT evidence_3 IS NOT NULL,
    'a no-identity fallback must not collide with authoritative identity evidence';
  evidence_4 := record_investment_evidence(
    group_4, document_id, draft_4, NULL, NULL, 1::SMALLINT, 4,
    'synthetic_statement_v1', '1', NULL, 'typed_event_v1', repeat('a', 64)
  );
  ASSERT evidence_4 IS NULL,
    'the same household, document and source contract must still detect a collision';
  ASSERT EXISTS (
    SELECT FROM investment_review_item
     WHERE group_id = group_4
       AND reason = 'duplicate_conflict' AND status = 'open'
  ), 'a same-contract conflict must remain group-local';
  ASSERT (SELECT count(*) = 3 FROM investment_evidence),
    'a conflict must attach no evidence';
  RAISE NOTICE 'PASS 0033 source-contract-aware replay and atomic conflict';
END $$;
ROLLBACK;
