-- db/tests/0014_staged_row_number_is_required.sql
-- V16: `staged_transaction.source_row_number` is NOT NULL, because everything that reads it
-- already requires it.
--
-- The column was nullable while every path that writes it types the value `int`:
-- `normalize.NormalizedRow`, `schemas.staging.StagedTransaction` and the pipeline's insert.
-- Everything that reads it requires it too — reconciliation names the divergent row by it, the
-- review payload labels each row by it, and `GET /import/{batch}` declares it a required `int`
-- and would answer 500 for a row that had none.
--
-- So a null here is a row nothing can produce and nothing can review: a state the schema
-- permitted and the system has no meaning for. The repair is the column, not the API model —
-- widening the model would make the service promise to render a row it cannot.
\set ON_ERROR_STOP on

DO $$
DECLARE nullable BOOLEAN;
BEGIN
  SELECT is_nullable = 'YES' INTO nullable
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'staged_transaction'
     AND column_name = 'source_row_number';

  ASSERT nullable IS NOT NULL, 'staged_transaction.source_row_number is missing';
  ASSERT NOT nullable,
    'staged_transaction.source_row_number must be NOT NULL: every writer types it int and '
    'every reader requires it';
  RAISE NOTICE 'PASS 0014 a staged row always states which row of the statement it came from';
END $$;

-- `transaction.source_row_number` stays nullable, and that is not an oversight: a manually
-- entered transaction (M1b) comes from no statement and has no row to name. Asserted so that a
-- later reader does not "fix" it to match its staged counterpart.
DO $$
DECLARE nullable BOOLEAN;
BEGIN
  SELECT is_nullable = 'YES' INTO nullable
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'transaction' AND column_name = 'source_row_number';

  ASSERT nullable,
    'transaction.source_row_number must stay nullable: a manual entry names no statement row';
  RAISE NOTICE 'PASS 0014 a manual transaction still needs no source row';
END $$;

-- The constraint refuses the row rather than being decorative.
BEGIN;
DO $$
DECLARE h UUID; p UUID; a UUID; b UUID; d UUID; base CHAR(3); secondary CHAR(3); refused BOOLEAN;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('row number test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'self', true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
  VALUES (h, p, 'Staged', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO a;
  INSERT INTO import_batch (household_id, account_id, status)
  VALUES (h, a, 'awaiting_review') RETURNING id INTO b;

  refused := false;
  BEGIN
    INSERT INTO staged_transaction
      (import_batch_id, household_id, account_id, raw_row, source_row_number, booked_date,
       description_raw, amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
       amount_base, amount_secondary, dedupe_hash)
    VALUES (b, h, a, '{}'::JSONB, NULL, '2026-03-01', 'NO ROW NUMBER', 10, base,
            '2026-03-01', 1, 1, 10, 10, repeat('a', 64));
  EXCEPTION WHEN not_null_violation THEN
    refused := true;
  END;

  ASSERT refused, 'a staged row with no source row number must be refused by the database';
  RAISE NOTICE 'PASS 0014 the constraint refuses the row, not merely describes it';
END $$;
ROLLBACK;
