-- db/tests/0004_fx.sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
    VALUES ('2024-01-05','AED','INR',22.6900000000,'derived_peg');

  ASSERT fx_rate_on('AED','INR','2024-01-05') = 22.6900000000, 'exact date lookup';

  -- a carried-forward rate must declare where it came from
  BEGIN
    INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source, is_carried_forward)
      VALUES ('2024-01-06','AED','INR',22.69,'derived_peg',true);
    ASSERT false, 'carried rate without carried_from must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- rates must be positive
  BEGIN
    INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
      VALUES ('2024-01-07','AED','INR',0,'manual');
    ASSERT false, 'zero rate must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  RAISE NOTICE 'PASS 0004';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
