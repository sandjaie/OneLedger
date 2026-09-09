-- db/tests/0015_period_provenance.sql
-- V17: `import_batch.period_is_derived` exists, and stays nullable so an older batch can say
-- "not recorded" rather than assert a provenance nobody observed (Invariant I7).
\set ON_ERROR_STOP on

DO $$
DECLARE kind TEXT; nullable BOOLEAN;
BEGIN
  SELECT data_type, is_nullable = 'YES' INTO kind, nullable
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'import_batch' AND column_name = 'period_is_derived';

  ASSERT kind = 'boolean', 'import_batch.period_is_derived must be boolean, got ' || COALESCE(kind, 'nothing');
  ASSERT nullable,
    'period_is_derived must stay nullable: a batch staged before V17 recorded no provenance, '
    'and a default would assert one nobody observed';
  RAISE NOTICE 'PASS 0015 the period carries its provenance, or says it has none';
END $$;

-- All three values are storable, and the column holds what it was given rather than deriving
-- anything from the dates beside it.
BEGIN;
DO $$
DECLARE h UUID; p UUID; a UUID; base CHAR(3); secondary CHAR(3); found BOOLEAN;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('period provenance', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'self', true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
  VALUES (h, p, 'Periods', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO a;

  -- Identical dates, different provenance: only the stored flag tells these two apart, which is
  -- the whole reason it is a column and not a computation.
  INSERT INTO import_batch (household_id, account_id, status, period_start, period_end,
                            period_is_derived)
  VALUES (h, a, 'awaiting_review', '2026-03-01', '2026-03-02', true);
  INSERT INTO import_batch (household_id, account_id, status, period_start, period_end,
                            period_is_derived)
  VALUES (h, a, 'awaiting_review', '2026-03-01', '2026-03-02', false);
  INSERT INTO import_batch (household_id, account_id, status, period_start, period_end)
  VALUES (h, a, 'awaiting_review', '2026-03-01', '2026-03-02');

  ASSERT (SELECT count(*) FROM import_batch
           WHERE household_id = h AND period_is_derived) = 1, 'one derived';
  ASSERT (SELECT count(*) FROM import_batch
           WHERE household_id = h AND NOT period_is_derived) = 1, 'one stated';
  ASSERT (SELECT count(*) FROM import_batch
           WHERE household_id = h AND period_is_derived IS NULL) = 1, 'one unrecorded';
  RAISE NOTICE 'PASS 0015 stated, derived and unrecorded are three distinguishable answers';

  -- A provenance for a period that does not exist is a row with nothing to interpret it.
  found := false;
  BEGIN
    INSERT INTO import_batch (household_id, account_id, status, period_start, period_end,
                              period_is_derived)
    VALUES (h, a, 'awaiting_review', NULL, NULL, true);
  EXCEPTION WHEN check_violation THEN
    found := true;
  END;
  ASSERT found,
    'a batch claiming its period was derived must hold a period to have derived';

  -- The converse stays legal: that is the pre-V17 batch the column is nullable for.
  INSERT INTO import_batch (household_id, account_id, status, period_start, period_end)
  VALUES (h, a, 'awaiting_review', '2026-04-01', '2026-04-30');
  RAISE NOTICE 'PASS 0015 provenance needs a period; a period needs no provenance';
END $$;
ROLLBACK;
