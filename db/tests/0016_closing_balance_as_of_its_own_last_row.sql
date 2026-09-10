-- db/tests/0016_closing_balance_as_of_its_own_last_row.sql
-- V18: a statement's declared closing is verified as of the last row that statement lists,
-- never as of a period end that excludes rows the statement itself carries.
\set ON_ERROR_STOP on

DROP FUNCTION IF EXISTS _t0016_fixture();

-- A batch whose last two rows are booked after the period it declares — the shape slice 1.2's
-- fourth producer ships, where year-end interest and its tax post on 1 January, are valued 31
-- December, and are inside the closing balance the statement prints.
CREATE OR REPLACE FUNCTION _t0016_fixture(
  OUT household UUID, OUT account UUID, OUT batch UUID, OUT owner_user UUID
) LANGUAGE plpgsql AS $$
DECLARE p UUID; base CHAR(3); secondary CHAR(3); layout UUID; doc UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('late booking test', base, secondary) RETURNING id INTO household;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household, 'self', true) RETURNING id INTO p;
  owner_user := gen_random_uuid();
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (owner_user, household, p, 'late-' || owner_user || '@example.invalid', 'owner');

  -- Opening 100. The three rows below sum to +90, so the statement closes at 190 — and 40 of
  -- that +90 is booked after the period ends.
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
  VALUES (household, p, 'Late Savings', 'savings', base, 'XB', 100, '2026-01-01')
  RETURNING id INTO account;

  INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                sign_convention, has_running_balance)
  VALUES (household, 'Late Layout', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy',
          'signed', true)
  RETURNING id INTO layout;

  INSERT INTO document (household_id, account_id, document_type, original_filename,
                        storage_bucket, storage_path, mime_type, byte_size, sha256)
  VALUES (household, account, 'bank_statement', 'late-…0000.pdf', 'documents',
          'cc/dd/late.pdf', 'application/pdf', 2048, 'latefixture')
  RETURNING id INTO doc;

  INSERT INTO import_batch (household_id, document_id, account_id, status, statement_layout_id,
                            layout_version, rows_extracted, rows_new,
                            declared_opening_balance, declared_closing_balance,
                            computed_closing_balance, reconciliation_status,
                            period_start, period_end)
  VALUES (household, doc, account, 'awaiting_review', layout, 1, 3, 3,
          100, 190, 190, 'matched', '2026-03-01', '2026-03-31')
  RETURNING id INTO batch;

  INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                  source_page_number, source_row_number, booked_date, value_date,
                                  description_raw, amount, currency, fx_rate_date,
                                  fx_rate_is_carried, fx_rate_to_base, fx_rate_to_secondary,
                                  amount_base, amount_secondary, proposed_txn_type,
                                  dedupe_hash, status)
  VALUES
    (batch, household, account, '{}'::jsonb, 1, 1, '2026-03-02', '2026-03-02', 'SALARY',
     50, base, '2026-03-02', false, 1, 1, 50, 50, 'income', repeat('d', 64), 'accepted'),
    -- Booked one day after the period closes, valued inside it, and in the closing balance.
    (batch, household, account, '{}'::jsonb, 1, 2, '2026-04-01', '2026-03-31', 'INTEREST',
     45, base, '2026-04-01', false, 1, 1, 45, 45, 'income', repeat('e', 64), 'accepted'),
    (batch, household, account, '{}'::jsonb, 1, 3, '2026-04-01', '2026-03-31', 'TAX',
     -5, base, '2026-04-01', false, 1, 1, -5, -5, 'expense', repeat('f', 64), 'accepted');
END $$;

-- ---------------------------------------------------------------------------------------
-- The behaviour V18 adds: the batch commits, because the balance is taken as of 1 April.
-- Verified as of the period end it would be short by the 40 those two rows carry, which is
-- the refusal every such statement met before.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; imported INTEGER; computed NUMERIC(20,4); refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0016_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  imported := commit_import_batch(f.batch);
  ASSERT imported = 3, 'all three rows commit, got ' || imported;

  SELECT sum(amount) INTO computed FROM transaction WHERE account_id = f.account;
  ASSERT computed = 90, 'the ledger holds the whole statement, got ' || computed;

  -- The old behaviour, asserted directly against the function the commit path calls: as of the
  -- period end the ledger is 40 short, and it says so rather than passing vacuously.
  BEGIN
    PERFORM assert_account_reconciles(f.account, 190, DATE '2026-03-31');
    RAISE EXCEPTION 'the period end must not reconcile: it excludes the rows booked after it';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%150%',
      'the refusal must name the balance the ledger holds as of the period end, got ' || refusal;
  END;

  -- And as of the last row the statement lists, it does reconcile.
  PERFORM assert_account_reconciles(f.account, 190, DATE '2026-04-01');
  RAISE NOTICE 'PASS 0016 a statement is verified as of the last row it lists';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The window only ever widens. A statement whose rows all fall inside its period is verified
-- exactly as V14 verified it, so every layout that worked before still does.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0016_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  -- Pull the two late rows back inside the period and make the statement close at 190 from
  -- rows that all sit inside it: the as-of date is then the period end, unchanged.
  -- The rate date moves with the row: V14's staged_carried_rate_dates ties the two together,
  -- and a fixture that broke it would be testing the constraint rather than the as-of date.
  UPDATE staged_transaction SET booked_date = '2026-03-20', fx_rate_date = '2026-03-20'
   WHERE import_batch_id = f.batch AND booked_date = '2026-04-01';

  PERFORM commit_import_batch(f.batch);
  PERFORM assert_account_reconciles(f.account, 190, DATE '2026-03-31');
  RAISE NOTICE 'PASS 0016 a statement inside its own period is verified as it always was';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Widening is not weakening: a batch whose rows do not add up is still refused, and the
-- refusal still names both figures.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0016_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  UPDATE import_batch SET declared_closing_balance = 191 WHERE id = f.batch;
  BEGIN
    PERFORM commit_import_batch(f.batch);
    RAISE EXCEPTION 'a batch that does not reconcile must not commit';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%191%' AND refusal LIKE '%190%',
      'the refusal must name both figures, got ' || refusal;
  END;
  ASSERT (SELECT count(*) FROM transaction WHERE account_id = f.account) = 0,
    'a failed verification leaves nothing behind';
  RAISE NOTICE 'PASS 0016 a batch that does not add up is still refused';
END $$;
ROLLBACK;

DROP FUNCTION IF EXISTS _t0016_fixture();
