-- db/migrations/V16__staged_row_number_is_required.sql
-- `staged_transaction.source_row_number` is NOT NULL.
--
-- V13 left it nullable, and every path that writes it types the value `int`:
-- `normalize.NormalizedRow`, `schemas.staging.StagedTransaction` and `pipeline._stage`'s insert.
-- Every path that reads it requires it too — `reconcile` names the divergent row by it,
-- `GET /import/{batch}` labels each row by it, and that route declares it a required `int`, so a
-- row carrying none would answer 500 rather than render.
--
-- A null here is therefore a row nothing can produce and nothing can review: a state the schema
-- permitted and the system has no meaning for. The repair belongs on the column rather than on
-- the API model — widening the model would have the service promise to render a row it cannot,
-- and would leave reconciliation reporting a divergence at row `None`.
--
-- **This migration fails loudly if any staged row has a null.** That is deliberate: the only way
-- to satisfy it otherwise is to invent a row number, and a fabricated provenance figure is
-- exactly what Invariant I6 exists to prevent. A staged batch is a working copy — if this ever
-- fires, the batch is cancelled and the statement re-imported, not backfilled.
--
-- `transaction.source_row_number` stays nullable on purpose: a manually entered transaction
-- (M1b) comes from no statement and has no row to name. `db/tests/0014` asserts both halves.

-- The guard exists so the failure says what to do. `ALTER ... SET NOT NULL` reports only that
-- the column contains nulls, and this is the one migration in the tree that can fail on real
-- data — so it is the one that most needs to explain itself where the person actually reads it,
-- which is the migration runner's output rather than this file.
DO $$
DECLARE offending BIGINT;
BEGIN
  SELECT count(*) INTO offending
    FROM staged_transaction WHERE source_row_number IS NULL;

  IF offending > 0 THEN
    RAISE EXCEPTION
      '% staged row(s) state no source row number, so this constraint cannot be applied',
      offending
      USING HINT = 'A staged batch is a working copy. Cancel the batch holding these rows and '
                   're-import the statement; do not backfill a row number, because inventing '
                   'one fabricates the provenance Invariant I6 exists to keep honest.',
            ERRCODE = 'not_null_violation';
  END IF;
END $$;

ALTER TABLE staged_transaction
  ALTER COLUMN source_row_number SET NOT NULL;
