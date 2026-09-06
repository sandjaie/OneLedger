-- db/tests/0004_fx.sql
\set ON_ERROR_STOP on

-- Fixture dates are 1900, deliberately. The backfill anchor is 01 Jan 2024 (ADR-0011) and a
-- rate before that has no meaning, so nothing this system stores can ever collide with these
-- rows. Using 2024 dates would work exactly once: the moment PR 5 backfills daily AED/INR
-- rates, the insert below raises unique_violation before reaching a single assertion, and the
-- pre-push hook then fails on every push. A test that only passes against an empty table is
-- not a test of a table that fills up.
DO $$
DECLARE
  base CHAR(3);
  quote CHAR(3);
BEGIN
  -- V4 references currency(code) on both legs. Naming a pair here would make those rows
  -- required seed data (ADR-0012); the rate and provenance checks do not care which pair.
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO quote FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND quote IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
    VALUES ('1900-01-05', base, quote, 22.6900000000, 'derived_peg');

  ASSERT fx_rate_on(base, quote, '1900-01-05') = 22.6900000000, 'exact date lookup';

  -- a carried-forward rate must declare where it came from
  BEGIN
    INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source, is_carried_forward)
      VALUES ('1900-01-06', base, quote, 22.69, 'derived_peg', true);
    ASSERT false, 'carried rate without carried_from must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- rates must be positive
  BEGIN
    INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
      VALUES ('1900-01-07', base, quote, 0, 'manual');
    ASSERT false, 'zero rate must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  RAISE NOTICE 'PASS 0004';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
