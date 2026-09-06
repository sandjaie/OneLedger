-- db/tests/0003_accounts.sql
\set ON_ERROR_STOP on
DO $$
DECLARE h UUID; p UUID; a_card UUID; a_sav UUID; cls account_class;
        base CHAR(3); secondary CHAR(3);
BEGIN
  -- The fixture takes whatever two currencies this deployment seeded. Naming them would make
  -- them required seed rows (ADR-0012). Countries use the ISO user-assigned range for the
  -- same reason.
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date, credit_limit)
    VALUES (h,p,'Test Card','credit_card',secondary,'XA',-4500,'2024-01-01',20000)
    RETURNING id INTO a_card;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Test Savings','savings',base,'XB',100000,'2024-01-01')
    RETURNING id INTO a_sav;

  SELECT account_class INTO cls FROM account WHERE id = a_card;
  ASSERT cls = 'liability', 'credit_card must derive account_class = liability';
  SELECT account_class INTO cls FROM account WHERE id = a_sav;
  ASSERT cls = 'asset',     'savings must derive account_class = asset';

  -- account_class is never set by hand: a wrong explicit value is corrected, not accepted
  UPDATE account SET account_class = 'asset' WHERE id = a_card;
  SELECT account_class INTO cls FROM account WHERE id = a_card;
  ASSERT cls = 'liability', 'trigger must overwrite a hand-set account_class';

  -- is_liquid defaults on (Q22): everything counts unless explicitly de-selected
  ASSERT (SELECT is_liquid FROM account WHERE id = a_card), 'is_liquid must default true';

  -- balance = opening_balance + SUM(transactions). No transaction table exists yet, so a new
  -- account's current balance is its opening balance, dated to the opening balance date.
  ASSERT (SELECT current_balance FROM account WHERE id = a_sav) = 100000,
    'an omitted current_balance starts at the opening balance';
  ASSERT (SELECT current_balance FROM account WHERE id = a_card) = -4500,
    'a liability account starts at its signed opening balance';
  ASSERT (SELECT current_balance_as_of FROM account WHERE id = a_sav)
           = '2024-01-01'::timestamptz,
    'a derived balance carries the date it is true for';

  -- An explicit value is still the caller's, including a deliberate zero.
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date, current_balance)
    VALUES (h,p,'Explicitly Zero','savings',base,'XB',100000,'2024-01-01',0);
  ASSERT (SELECT current_balance FROM account WHERE name = 'Explicitly Zero') = 0,
    'an explicit current_balance is never overwritten';

  RAISE NOTICE 'PASS 0003';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
