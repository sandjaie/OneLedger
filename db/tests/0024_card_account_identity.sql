\set ON_ERROR_STOP on
DO $$
DECLARE h UUID; p UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'self', true) RETURNING id INTO p;

  BEGIN
    INSERT INTO account (
      household_id, owner_person_id, name, account_type, currency, country,
      account_number_masked, account_number_hash, opening_balance,
      opening_balance_date, credit_limit
    ) VALUES (
      h, p, 'Test Card', 'credit_card', base, 'XA',
      '****1234', repeat('a', 64), -1, '2024-01-01', 100
    );
    RAISE EXCEPTION 'a credit card account hash was accepted';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;

  RAISE NOTICE 'PASS 0024';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
