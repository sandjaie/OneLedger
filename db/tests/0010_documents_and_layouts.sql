-- db/tests/0010_documents_and_layouts.sql
-- V12: document, import_batch, statement_layout — RLS, the checksum and balance-anchor
-- invariants (I8), name/version uniqueness, the chronological_order CHECK, and the four
-- deferred foreign keys V12 completes on transaction (schema.md 0018's per-target rule).
-- Fixtures use whatever currencies this deployment seeded and the ISO user-assigned country
-- range, exactly as db/tests/0009 does (ADR-0012).
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'document'),
    'RLS must be enabled on document';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'import_batch'),
    'RLS must be enabled on import_batch';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'statement_layout'),
    'RLS must be enabled on statement_layout';
  RAISE NOTICE 'PASS 0010 structure';
END $$;

DO $$
DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3);
        layout_running UUID; layout_closing UUID; doc1 UUID; batch1 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('import test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Import Savings','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a;

  -- I8: a profile with neither a closing pattern nor a running balance is rejected at insert.
  BEGIN
    INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                  sign_convention)
      VALUES (h, 'Layout A', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- A running-balance column is a sufficient anchor.
  INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                sign_convention, has_running_balance)
    VALUES (h, 'Layout A', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed', true)
    RETURNING id INTO layout_running;

  -- A closing-balance pattern is also sufficient, and a second version of the same name coexists.
  INSERT INTO statement_layout (household_id, name, version, fingerprints, columns, date_format,
                                sign_convention, closing_balance_pattern)
    VALUES (h, 'Layout A', 2, ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed',
            'Closing balance ([\d,.]+)')
    RETURNING id INTO layout_closing;

  -- (household_id, name, version) is unique.
  BEGIN
    INSERT INTO statement_layout (household_id, name, version, fingerprints, columns,
                                  date_format, sign_convention, has_running_balance)
      VALUES (h, 'Layout A', 2, ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed', true);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;

  -- chronological_order is restricted to the documented pair.
  BEGIN
    INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                  sign_convention, has_running_balance, chronological_order)
      VALUES (h, 'Layout B', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed', true,
              'sideways');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- A document, for the batch fixture and the transaction FK checks below.
  INSERT INTO document (household_id, account_id, document_type, original_filename,
                        storage_bucket, storage_path, mime_type, byte_size, sha256)
    VALUES (h, a, 'bank_statement', 'statement-…1234.pdf', 'documents', 'ab/cd/ab12.pdf',
            'application/pdf', 1024, 'a1b2c3')
    RETURNING id INTO doc1;

  -- The same (household_id, sha256) cannot be imported twice.
  BEGIN
    INSERT INTO document (household_id, account_id, document_type, original_filename,
                          storage_bucket, storage_path, mime_type, byte_size, sha256)
      VALUES (h, a, 'bank_statement', 'statement-…5678.pdf', 'documents', 'ef/gh/ef34.pdf',
              'application/pdf', 2048, 'a1b2c3');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;

  INSERT INTO import_batch (household_id, document_id, account_id, statement_layout_id)
    VALUES (h, doc1, a, layout_running) RETURNING id INTO batch1;

  -- The four deferred foreign keys V12 completes on transaction: a dangling id is rejected...
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             source_document_id)
      VALUES (h, a, '2024-02-01', 'DANGLING DOC', -5.0000, base, '2024-02-01', 1, 20,
              -5.0000, -100.0000, 'expense', 'statement_import', 'i1', gen_random_uuid());
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             source_document_id, import_batch_id)
      VALUES (h, a, '2024-02-01', 'DANGLING BATCH', -5.0000, base, '2024-02-01', 1, 20,
              -5.0000, -100.0000, 'expense', 'statement_import', 'i2', doc1, gen_random_uuid());
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source, dedupe_hash,
                             source_document_id, statement_layout_id)
      VALUES (h, a, '2024-02-01', 'DANGLING LAYOUT', -5.0000, base, '2024-02-01', 1, 20,
              -5.0000, -100.0000, 'expense', 'statement_import', 'i3', doc1, gen_random_uuid());
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;

  -- ...and real ids are accepted.
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash,
                           source_document_id, import_batch_id, statement_layout_id)
    VALUES (h, a, '2024-02-01', 'REAL LINKS', -5.0000, base, '2024-02-01', 1, 20,
            -5.0000, -100.0000, 'expense', 'statement_import', 'i4', doc1, batch1, layout_running);

  RAISE NOTICE 'PASS 0010';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;

-- Isolation: another session with no bound household sees none of these three tables — the
-- same idiom db/tests/0009 uses for the ledger, and the stronger proof: current_household_id()
-- is NULL for anon, which never equals any household_id, seeded or not.
BEGIN;
  DO $$
  DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3); layout UUID; doc UUID;
  BEGIN
    SELECT min(code) INTO base FROM currency;
    SELECT min(code) INTO secondary FROM currency WHERE code > base;
    INSERT INTO household (name, base_currency, secondary_currency)
    VALUES ('import rls fixture', base, secondary) RETURNING id INTO h;
    INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;
    INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                         country, opening_balance, opening_balance_date)
      VALUES (h,p,'RLS Savings','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a;
    INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                  sign_convention, has_running_balance)
      VALUES (h, 'RLS Layout', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed', true)
      RETURNING id INTO layout;
    INSERT INTO document (household_id, account_id, document_type, original_filename,
                          storage_bucket, storage_path, mime_type, byte_size, sha256)
      VALUES (h, a, 'bank_statement', 'rls-…0000.pdf', 'documents', 'zz/yy/zz99.pdf',
              'application/pdf', 512, 'rlsfixture')
      RETURNING id INTO doc;
    INSERT INTO import_batch (household_id, document_id, account_id, statement_layout_id)
      VALUES (h, doc, a, layout);
  END $$;

  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM statement_layout) >= 1,
      'the fixture must exist before the isolation check';
  END $$;

  SET LOCAL ROLE anon;
  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM statement_layout) = 0, 'anon must not read layouts';
    ASSERT (SELECT count(*) FROM document) = 0, 'anon must not read documents';
    ASSERT (SELECT count(*) FROM import_batch) = 0, 'anon must not read import batches';
    RAISE NOTICE 'PASS 0010 isolation';
  END $$;
ROLLBACK;
