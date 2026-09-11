-- V28: institution aliases and local, typed account identifiers.
-- Every value is synthetic. The upgrade rehearsal removes V28 inside a transaction, inserts
-- pre-V28 rows, executes the real migration, asserts the copy, then rolls the rehearsal back.
\set ON_ERROR_STOP on

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['institution_alias', 'account_identifier'] LOOP
    ASSERT to_regclass('public.' || table_name) IS NOT NULL,
      table_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = table_name),
      'RLS must be enabled on ' || table_name;
  END LOOP;
  RAISE NOTICE 'PASS 0025 structure and RLS';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0025_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, a1 UUID, a2 UUID,
    shared_institution UUID, private_institution_1 UUID, private_institution_2 UUID,
    document_1 UUID, document_2 UUID, layout_1 UUID, layout_2 UUID
  ) LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p2 UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('identifier household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('identifier household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'identifier owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'identifier owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'identifier-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'identifier-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO institution (household_id, name, country, institution_type)
  VALUES (NULL, 'Synthetic Shared Institution', 'XZ', 'bank')
  RETURNING id INTO shared_institution;
  INSERT INTO institution (household_id, name, country, institution_type)
  VALUES (h1, 'Synthetic Institution One', 'XZ', 'bank')
  RETURNING id INTO private_institution_1;
  INSERT INTO institution (household_id, name, country, institution_type)
  VALUES (h2, 'Synthetic Institution Two', 'XZ', 'bank')
  RETURNING id INTO private_institution_2;

  INSERT INTO account (
    household_id, owner_person_id, institution_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, private_institution_1, 'Synthetic Account One', 'savings', base, 'XZ',
    0, '2026-01-01'
  ) RETURNING id INTO a1;
  INSERT INTO account (
    household_id, owner_person_id, institution_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h2, p2, private_institution_2, 'Synthetic Account Two', 'savings', base, 'XZ',
    0, '2026-01-01'
  ) RETURNING id INTO a2;

  INSERT INTO statement_layout (
    household_id, institution_id, account_type, name, version, fingerprints, columns,
    date_format, sign_convention, closing_balance_pattern
  ) VALUES (
    h1, private_institution_1, 'savings', 'Synthetic Layout One', 1,
    ARRAY['SYNTHETIC ONE'], '[]', 'dd/MM/yyyy', 'signed', 'closing'
  ) RETURNING id INTO layout_1;
  INSERT INTO statement_layout (
    household_id, institution_id, account_type, name, version, fingerprints, columns,
    date_format, sign_convention, closing_balance_pattern
  ) VALUES (
    h2, private_institution_2, 'savings', 'Synthetic Layout Two', 1,
    ARRAY['SYNTHETIC TWO'], '[]', 'dd/MM/yyyy', 'signed', 'closing'
  ) RETURNING id INTO layout_2;

  INSERT INTO document (
    household_id, institution_id, account_id, document_type, original_filename,
    storage_bucket, storage_path, mime_type, byte_size, sha256, statement_layout_id,
    layout_version
  ) VALUES (
    h1, private_institution_1, a1, 'bank_statement', 'synthetic-one.pdf',
    'local', 'synthetic/one', 'application/pdf', 1, repeat('1', 64), layout_1, 1
  ) RETURNING id INTO document_1;
  INSERT INTO document (
    household_id, institution_id, account_id, document_type, original_filename,
    storage_bucket, storage_path, mime_type, byte_size, sha256, statement_layout_id,
    layout_version
  ) VALUES (
    h2, private_institution_2, a2, 'bank_statement', 'synthetic-two.pdf',
    'local', 'synthetic/two', 'application/pdf', 1, repeat('2', 64), layout_2, 1
  ) RETURNING id INTO document_2;
  RETURN NEXT;
END $$;

-- A shared alias may reference only a shared institution. A private alias may reference the
-- household's institution or a shared one, never another household's institution.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; first_alias UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0025_fixture();
  INSERT INTO institution_alias (
    household_id, institution_id, alias, alias_normalized, country, provenance
  ) VALUES (
    NULL, f.shared_institution, 'Synthetic Shared', 'synthetic shared', 'XZ', 'public_seed'
  ) RETURNING id INTO first_alias;
  INSERT INTO institution_alias (
    household_id, institution_id, alias, alias_normalized, country, provenance
  ) VALUES
    (f.h1, f.shared_institution, 'Synthetic Shared Private',
      'synthetic shared private', 'XZ', 'owner_confirmed'),
    (f.h1, f.private_institution_1, 'Synthetic Private',
      'synthetic private', 'XZ', 'owner_confirmed');

  BEGIN
    INSERT INTO institution_alias (
      household_id, institution_id, alias, alias_normalized, country, provenance
    ) VALUES (NULL, f.private_institution_1, 'Wrong Shared', 'wrong shared', 'XZ', 'public_seed');
    RAISE EXCEPTION 'a shared alias referenced a private institution';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%institution alias references institution outside its scope%', refusal;
  END;

  BEGIN
    UPDATE institution SET household_id = f.h2 WHERE id = f.private_institution_1;
    RAISE EXCEPTION 'an institution moved after household-scoped aliases referenced it';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'institution household is immutable', refusal;
  END;
  ASSERT (SELECT household_id FROM institution WHERE id = f.private_institution_1) = f.h1,
    'a refused institution move must retain its original household';
  ASSERT EXISTS (
    SELECT FROM institution_alias
     WHERE household_id = f.h1 AND institution_id = f.private_institution_1
  ), 'a refused institution move must preserve its alias scope';

  BEGIN
    INSERT INTO institution_alias (
      household_id, institution_id, alias, alias_normalized, country, provenance
    ) VALUES (
      f.h1, f.private_institution_2, 'Wrong Household', 'wrong household', 'XZ',
      'owner_confirmed'
    );
    RAISE EXCEPTION 'a private alias referenced another household institution';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%institution alias references institution outside its scope%', refusal;
  END;

  BEGIN
    INSERT INTO institution_alias (
      household_id, institution_id, alias, alias_normalized, country, provenance
    ) VALUES (
      NULL, f.shared_institution, 'Synthetic Shared Again', 'synthetic shared', 'XZ',
      'public_seed'
    );
    RAISE EXCEPTION 'a duplicate active alias for one institution was accepted';
  EXCEPTION WHEN unique_violation THEN
    NULL;
  END;

  UPDATE institution_alias
     SET is_active = false, deactivated_at = now()
   WHERE id = first_alias;
  INSERT INTO institution_alias (
    household_id, institution_id, alias, alias_normalized, country, provenance
  ) VALUES (
    NULL, f.shared_institution, 'Synthetic Shared Replacement', 'synthetic shared', 'XZ',
    'public_seed'
  );
  RAISE NOTICE 'PASS 0025 institution alias scope, uniqueness and lifecycle';
END $$;
ROLLBACK;

-- Complete identifiers retain their source spelling plus a normalized exact-match value. Both
-- columns are present or absent together; a mask alone remains deliberately ambiguous.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; identifier UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0025_fixture();
  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) VALUES (
    f.h1, f.a1, 'bank_account', '••••1234', '1234',
    'SYN 0000 1234', 'SYN00001234', 'owner_entered'
  ) RETURNING id INTO identifier;

  ASSERT (SELECT full_value FROM account_identifier WHERE id = identifier) = 'SYN 0000 1234',
    'the local database must retain the complete source value';

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix,
      full_value, normalized_value, provenance
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••1234', '1234',
      'SYN-0000-1234', 'SYN00001234', 'owner_entered'
    );
    RAISE EXCEPTION 'a duplicate active exact identity was accepted';
  EXCEPTION WHEN unique_violation THEN
    NULL;
  END;

  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) VALUES (
    f.h1, f.a1, 'iban', '••••1234', '1234',
    'XZ00 SYN0 0001 234', 'XZ00SYN00001234', 'owner_entered'
  );
  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) VALUES (
    f.h2, f.a2, 'bank_account', '••••1234', '1234',
    'SYN-0000-1234', 'SYN00001234', 'owner_entered'
  );
  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix, provenance
  ) VALUES
    (f.h1, f.a1, 'card', '••••1234', '1234', 'owner_entered'),
    (f.h1, f.a1, 'card', '••••1234', '1234', 'owner_entered');

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix,
      full_value, provenance
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••5678', '5678',
      'SYN 0000 5678', 'owner_entered'
    );
    RAISE EXCEPTION 'a full value without its normalized match value was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_identifier_complete_value_is_complete%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix,
      legacy_fingerprint, provenance
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••5678', '5678',
      decode(repeat('cd', 32), 'hex'), 'owner_entered'
    );
    RAISE EXCEPTION 'a partial legacy fingerprint was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_identifier_legacy_fingerprint_is_complete%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix, provenance
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••9001', '9001', 'statement'
    );
    RAISE EXCEPTION 'incomplete statement provenance was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_identifier_provenance_is_complete%', refusal;
  END;

  BEGIN
    UPDATE account
       SET account_number_hash = repeat('d', 64), account_number_masked = NULL
     WHERE id = f.a1;
    RAISE EXCEPTION 'a legacy hash without its display mask was accepted';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_legacy_identity_is_migratable%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix, provenance,
      source_sha256, source_layout_id, source_layout_version, source_capture_key
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••9003', '9003', 'statement',
      repeat('3', 64), f.layout_1, NULL, 'account_number'
    );
    RAISE EXCEPTION 'statement provenance accepted a null layout version';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_identifier_provenance_is_complete%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix, provenance,
      source_document_id, source_sha256, source_layout_id, source_layout_version,
      source_capture_key
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••9004', '9004', 'statement_backfill',
      f.document_1, repeat('4', 64), f.layout_1, NULL, 'account_number'
    );
    RAISE EXCEPTION 'statement backfill provenance accepted a null layout version';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_identifier_provenance_is_complete%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix,
      full_value, normalized_value, provenance
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••9002', '9002',
      'SYN 0000 9002', 'SYN00009002', 'legacy_migration'
    );
    RAISE EXCEPTION 'legacy migration provenance accepted an invented complete value';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%account_identifier_provenance_is_complete%', refusal;
  END;

  UPDATE account_identifier
     SET is_active = false, deactivated_at = now()
   WHERE id = identifier;
  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) VALUES (
    f.h1, f.a1, 'bank_account', '••••1234', '1234',
    'SYN 0000 1234', 'SYN00001234', 'owner_entered'
  );
  RAISE NOTICE 'PASS 0025 local value, exact identity, mask ambiguity and lifecycle';
END $$;
ROLLBACK;

-- Privileged service connections bypass RLS, so composite references must still reject account,
-- document and layout rows from another household.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0025_fixture();
  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix, provenance
    ) VALUES (f.h1, f.a2, 'bank_account', '••••1001', '1001', 'owner_entered');
    RAISE EXCEPTION 'a cross-household account reference was accepted';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_account_identifier_account_household%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix, provenance,
      source_document_id, source_sha256, source_layout_id, source_layout_version,
      source_capture_key
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••1002', '1002', 'statement',
      f.document_2, repeat('2', 64), f.layout_1, 1, 'account_number'
    );
    RAISE EXCEPTION 'a cross-household document reference was accepted';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_account_identifier_document_household%', refusal;
  END;

  BEGIN
    INSERT INTO account_identifier (
      household_id, account_id, kind, masked_value, visible_suffix, provenance,
      source_sha256, source_layout_id, source_layout_version, source_capture_key
    ) VALUES (
      f.h1, f.a1, 'bank_account', '••••1003', '1003', 'statement',
      repeat('3', 64), f.layout_2, 1, 'account_number'
    );
    RAISE EXCEPTION 'a cross-household layout reference was accepted';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_account_identifier_layout_household%', refusal;
  END;
  RAISE NOTICE 'PASS 0025 household-leading references';
END $$;
ROLLBACK;

-- Authenticated table reads include shared aliases plus this household, and never another
-- household's aliases or account identifiers.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0025_fixture();
  INSERT INTO institution_alias (
    household_id, institution_id, alias, alias_normalized, country, provenance
  ) VALUES
    (NULL, f.shared_institution, 'Shared Visible', 'shared visible', 'XZ', 'public_seed'),
    (f.h1, f.private_institution_1, 'Private One', 'private one', 'XZ', 'owner_confirmed'),
    (f.h2, f.private_institution_2, 'Private Two', 'private two', 'XZ', 'owner_confirmed');
  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) VALUES
    (f.h1, f.a1, 'bank_account', '••••2001', '2001',
      'SYN 0000 2001', 'SYN00002001', 'owner_entered'),
    (f.h2, f.a2, 'bank_account', '••••2002', '2002',
      'SYN 0000 2002', 'SYN00002002', 'owner_entered');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM institution_alias) = 2,
    'owner sees shared and own institution aliases only';
  ASSERT (SELECT count(*) FROM account_identifier) = 1,
    'owner sees own account identifiers only';
  ASSERT (SELECT full_value FROM account_identifier) = 'SYN 0000 2001',
    'owner can read the complete local value through household RLS';
  RESET ROLE;
  RAISE NOTICE 'PASS 0025 row-level isolation';
END $$;
ROLLBACK;

-- Run the actual migration over synthetic pre-V28 rows. It must retain masks, copy valid legacy
-- digests without claiming they contain plaintext, keep cards mask-only, and split IBAN evidence.
BEGIN;
DROP TABLE account_identifier;
DROP TABLE institution_alias;
DROP TRIGGER trg_institution_household_is_immutable ON institution;
DROP FUNCTION preserve_institution_household();
DROP FUNCTION assert_institution_alias_scope();
ALTER TABLE document DROP CONSTRAINT uq_document_household_id;
ALTER TABLE statement_layout DROP CONSTRAINT uq_statement_layout_household_id;
ALTER TABLE account DROP CONSTRAINT account_legacy_identity_is_migratable;

DO $$
DECLARE h UUID; p UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('pre-v28 household', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'pre-v28 owner', true) RETURNING id INTO p;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    account_number_masked, account_number_hash, iban_masked, opening_balance,
    opening_balance_date
  ) VALUES (
    h, p, 'Legacy bank account', 'savings', base, 'XZ', 'XXXX3001', repeat('a', 64),
    'XX00 XXXX 3001', 0, '2026-01-01'
  );
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    account_number_masked, opening_balance, opening_balance_date, credit_limit
  ) VALUES (
    h, p, 'Legacy card account', 'credit_card', base, 'XZ', 'XXXX3002',
    0, '2026-01-01', 100
  );
END $$;

\ir ../migrations/V28__institution_and_account_identifiers.sql

DO $$
BEGIN
  ASSERT EXISTS (
    SELECT FROM account a JOIN account_identifier ai ON ai.account_id = a.id
     WHERE a.name = 'Legacy bank account'
       AND ai.kind = 'bank_account'
       AND ai.masked_value = 'XXXX3001'
       AND ai.legacy_fingerprint = decode(repeat('a', 64), 'hex')
       AND ai.legacy_fingerprint_scheme = 'legacy_sha256_v1'
       AND ai.full_value IS NULL
       AND ai.normalized_value IS NULL
  ), 'legacy bank mask and digest must be copied without invented plaintext';
  ASSERT EXISTS (
    SELECT FROM account a JOIN account_identifier ai ON ai.account_id = a.id
     WHERE a.name = 'Legacy bank account' AND ai.kind = 'iban'
       AND ai.masked_value = 'XX00 XXXX 3001' AND ai.legacy_fingerprint IS NULL
  ), 'legacy IBAN mask must become an independent mask-only identifier';
  ASSERT EXISTS (
    SELECT FROM account a JOIN account_identifier ai ON ai.account_id = a.id
     WHERE a.name = 'Legacy card account' AND ai.kind = 'card'
       AND ai.masked_value = 'XXXX3002' AND ai.legacy_fingerprint IS NULL
  ), 'legacy card must remain mask-only';
  ASSERT NOT EXISTS (
    SELECT FROM account_identifier
     WHERE provenance = 'legacy_migration'
       AND (full_value IS NOT NULL OR normalized_value IS NOT NULL)
  ), 'migration must not invent complete values';
  RAISE NOTICE 'PASS 0025 legacy upgrade copy';
END $$;
ROLLBACK;
