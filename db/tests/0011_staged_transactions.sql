-- db/tests/0011_staged_transactions.sql
-- V13: staged_transaction — RLS, the status vocabulary, the committable-type CHECK, the
-- cascade from import_batch, and the consequence the plan names rather than works around:
-- a staged, uncommitted batch locks the household's reporting pair, because its amount_base
-- is already frozen against the current pair (plan section 5.2).
-- Fixtures use whatever currencies this deployment seeded and the ISO user-assigned country
-- range, exactly as db/tests/0010 does (ADR-0012).
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'staged_transaction'),
    'RLS must be enabled on staged_transaction';
  RAISE NOTICE 'PASS 0011 structure';
END $$;

BEGIN;
DO $$
DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3);
        layout UUID; doc UUID; batch UUID; staged UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('staging test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Staging Savings','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a;
  INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                sign_convention, has_running_balance)
    VALUES (h, 'Staging Layout', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy', 'signed', true)
    RETURNING id INTO layout;
  INSERT INTO document (household_id, account_id, document_type, original_filename,
                        storage_bucket, storage_path, mime_type, byte_size, sha256)
    VALUES (h, a, 'bank_statement', 'staging-…0000.pdf', 'documents', 'aa/bb/cc11.pdf',
            'application/pdf', 1024, 'stagingfixture')
    RETURNING id INTO doc;
  INSERT INTO import_batch (household_id, document_id, account_id, statement_layout_id)
    VALUES (h, doc, a, layout) RETURNING id INTO batch;

  -- A clean staged row.
  INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                  booked_date, description_raw, amount, currency,
                                  fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                  amount_base, amount_secondary, dedupe_hash,
                                  proposed_txn_type)
    VALUES (batch, h, a, '{"booked_date":"01/02/2024"}'::jsonb,
            '2024-02-01', 'POS PURCHASE', -310.0000, base,
            '2024-02-01', 1, 20, -310.0000, -6200.0000, 's1', 'expense')
    RETURNING id INTO staged;

  ASSERT (SELECT status FROM staged_transaction WHERE id = staged) = 'new',
    'a staged row defaults to new';

  -- The status vocabulary is closed: M2 stages new/duplicate/near_duplicate, M4 sets
  -- needs_review, review sets accepted/rejected. Nothing else may reach the column.
  BEGIN
    UPDATE staged_transaction SET status = 'imported' WHERE id = staged;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- An accepted row always carries a committable type: transaction.txn_type is NOT NULL, so a
  -- row that can be accepted but never committed is a state the database refuses to represent.
  BEGIN
    INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                    booked_date, description_raw, amount, currency,
                                    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                    amount_base, amount_secondary, dedupe_hash, status)
      VALUES (batch, h, a, '{}'::jsonb, '2024-02-02', 'NO TYPE', -5.0000, base,
              '2024-02-02', 1, 20, -5.0000, -100.0000, 's2', 'accepted');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- The same row with a type is accepted.
  INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                  booked_date, description_raw, amount, currency,
                                  fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                  amount_base, amount_secondary, dedupe_hash, status,
                                  proposed_txn_type)
    VALUES (batch, h, a, '{}'::jsonb, '2024-02-02', 'WITH TYPE', -5.0000, base,
            '2024-02-02', 1, 20, -5.0000, -100.0000, 's3', 'accepted', 'expense');

  -- V10's amount_not_zero permits a zero amount only for 'adjustment', and review may
  -- override a proposed type. An accepted zero row typed anything else is a row the
  -- all-or-nothing commit must reject, so the database refuses to hold it.
  BEGIN
    INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                    booked_date, description_raw, amount, currency,
                                    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                    amount_base, amount_secondary, dedupe_hash, status,
                                    proposed_txn_type)
      VALUES (batch, h, a, '{}'::jsonb, '2024-02-03', 'ZERO EXPENSE', 0, base,
              '2024-02-03', 1, 20, 0, 0, 's4', 'accepted', 'expense');
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- The same zero row typed adjustment is accepted, and a zero row not yet accepted may
  -- carry any type while review decides.
  INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                  booked_date, description_raw, amount, currency,
                                  fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                  amount_base, amount_secondary, dedupe_hash, status,
                                  proposed_txn_type)
    VALUES (batch, h, a, '{}'::jsonb, '2024-02-03', 'ZERO ADJUSTMENT', 0, base,
            '2024-02-03', 1, 20, 0, 0, 's5', 'accepted', 'adjustment');
  INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                  booked_date, description_raw, amount, currency,
                                  fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                  amount_base, amount_secondary, dedupe_hash,
                                  proposed_txn_type)
    VALUES (batch, h, a, '{}'::jsonb, '2024-02-03', 'ZERO UNDER REVIEW', 0, base,
            '2024-02-03', 1, 20, 0, 0, 's6', 'expense');

  -- A staged row is batch-scoped working state: deleting the batch takes its rows with it.
  DELETE FROM import_batch WHERE id = batch;
  ASSERT (SELECT count(*) FROM staged_transaction WHERE import_batch_id = batch) = 0,
    'staged rows cascade away with their import batch';

  RAISE NOTICE 'PASS 0011 staging';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
ROLLBACK;

-- The consequence to test rather than work around (plan section 5.2): reporting_pair_is_locked()
-- discovers every table carrying both amount_base and household_id, so creating
-- staged_transaction makes a staged, uncommitted batch lock the pair. That is correct — those
-- amounts are already frozen against the current pair — and it is asserted here so nobody
-- later reads it as an accident.
BEGIN;
  INSERT INTO household (id, name, base_currency, secondary_currency)
    SELECT '71111111-1111-1111-1111-111111111111', 'staging lock',
           min(code), (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency))
      FROM currency;
  INSERT INTO person (id, household_id, display_name, is_self)
    VALUES ('72222222-2222-2222-2222-222222222222',
            '71111111-1111-1111-1111-111111111111', 'self', true);
  INSERT INTO app_user (id, household_id, person_id, email)
    VALUES ('73333333-3333-3333-3333-333333333333',
            '71111111-1111-1111-1111-111111111111',
            '72222222-2222-2222-2222-222222222222', 'staging@example.test');

  DO $$
  DECLARE was_base CHAR(3); was_secondary CHAR(3); a UUID; layout UUID; doc UUID; batch UUID;
  BEGIN
    PERFORM set_config('app.user_id', '73333333-3333-3333-3333-333333333333', true);
    SELECT base_currency, secondary_currency INTO was_base, was_secondary
      FROM household WHERE id = '71111111-1111-1111-1111-111111111111';

    ASSERT reporting_pair_is_locked() = false,
      'nothing is staged or committed, so the pair is still editable';

    INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                         country, opening_balance, opening_balance_date)
      VALUES ('71111111-1111-1111-1111-111111111111',
              '72222222-2222-2222-2222-222222222222',
              'Lock Savings','savings',was_base,'XB',0,'2024-01-01') RETURNING id INTO a;
    INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                  sign_convention, has_running_balance)
      VALUES ('71111111-1111-1111-1111-111111111111', 'Lock Layout', ARRAY['FINGERPRINT'],
              '[]'::jsonb, 'dd/MM/yyyy', 'signed', true) RETURNING id INTO layout;
    INSERT INTO document (household_id, account_id, document_type, original_filename,
                          storage_bucket, storage_path, mime_type, byte_size, sha256)
      VALUES ('71111111-1111-1111-1111-111111111111', a, 'bank_statement', 'lock-…0000.pdf',
              'documents', 'dd/ee/ff22.pdf', 'application/pdf', 1024, 'lockfixture')
      RETURNING id INTO doc;
    INSERT INTO import_batch (household_id, document_id, account_id, statement_layout_id)
      VALUES ('71111111-1111-1111-1111-111111111111', doc, a, layout) RETURNING id INTO batch;
    INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                    booked_date, description_raw, amount, currency,
                                    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                    amount_base, amount_secondary, dedupe_hash,
                                    proposed_txn_type)
      VALUES (batch, '71111111-1111-1111-1111-111111111111', a, '{}'::jsonb,
              '2024-02-01', 'STAGED ONLY', -310.0000, was_base,
              '2024-02-01', 1, 20, -310.0000, -6200.0000, 'lock1', 'expense');

    ASSERT reporting_pair_is_locked() = true,
      'a staged, uncommitted row locks the reporting pair';

    BEGIN
      PERFORM update_household_configuration(
        'still editable', 'still editable', NULL, was_secondary, was_base);
      RAISE EXCEPTION 'a pair frozen by staged rows must not be changed';
    EXCEPTION WHEN object_in_use THEN NULL;
    END;

    RAISE NOTICE 'PASS 0011 reporting pair lock';
  END $$;
ROLLBACK;

-- Isolation: a session with no bound household sees no staged rows — the idiom db/tests/0010
-- uses, and the stronger proof: current_household_id() is NULL for anon, which never equals
-- any household_id.
BEGIN;
  DO $$
  DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3);
          layout UUID; doc UUID; batch UUID;
  BEGIN
    SELECT min(code) INTO base FROM currency;
    SELECT min(code) INTO secondary FROM currency WHERE code > base;
    INSERT INTO household (name, base_currency, secondary_currency)
      VALUES ('staging rls fixture', base, secondary) RETURNING id INTO h;
    INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true)
      RETURNING id INTO p;
    INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                         country, opening_balance, opening_balance_date)
      VALUES (h,p,'RLS Staging','savings',base,'XB',0,'2024-01-01') RETURNING id INTO a;
    INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                  sign_convention, has_running_balance)
      VALUES (h, 'RLS Staging Layout', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy',
              'signed', true) RETURNING id INTO layout;
    INSERT INTO document (household_id, account_id, document_type, original_filename,
                          storage_bucket, storage_path, mime_type, byte_size, sha256)
      VALUES (h, a, 'bank_statement', 'rls-…0000.pdf', 'documents', 'gg/hh/ii33.pdf',
              'application/pdf', 512, 'stagingrlsfixture')
      RETURNING id INTO doc;
    INSERT INTO import_batch (household_id, document_id, account_id, statement_layout_id)
      VALUES (h, doc, a, layout) RETURNING id INTO batch;
    INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                    booked_date, description_raw, amount, currency,
                                    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                                    amount_base, amount_secondary, dedupe_hash,
                                    proposed_txn_type)
      VALUES (batch, h, a, '{}'::jsonb, '2024-02-01', 'ISOLATED', -1.0000, base,
              '2024-02-01', 1, 20, -1.0000, -20.0000, 'rls1', 'expense');
  END $$;

  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM staged_transaction) >= 1,
      'the fixture must exist before the isolation check';
  END $$;

  SET LOCAL ROLE anon;
  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM staged_transaction) = 0, 'anon must not read staged rows';
    RAISE NOTICE 'PASS 0011 isolation';
  END $$;
ROLLBACK;
