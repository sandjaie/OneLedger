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

  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date, credit_limit
  ) VALUES (
    h, p, 'Test Card', 'credit_card', base, 'XA', -1, '2024-01-01', 100
  );

  INSERT INTO account_identifier (
    household_id, account_id, kind, masked_value, visible_suffix,
    full_value, normalized_value, provenance
  ) SELECT h, id, 'card', '****1234', '1234',
           '4111 1111 1111 1234', '4111111111111234', 'owner_entered'
      FROM account WHERE household_id = h AND name = 'Test Card';

  ASSERT EXISTS (
    SELECT FROM account a JOIN account_identifier ai ON ai.account_id = a.id
     WHERE a.household_id = h AND a.account_type = 'credit_card'
       AND ai.kind = 'card' AND ai.full_value = '4111 1111 1111 1234'
       AND ai.normalized_value = '4111111111111234'
  ), 'a card keeps its complete typed local identifier';

  RAISE NOTICE 'PASS 0024 card typed identity';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
