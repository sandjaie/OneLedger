-- db/tests/0007_account_inventory.sql
\set ON_ERROR_STOP on
DO $$
DECLARE h UUID; p UUID; a UUID; base CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  SELECT 'inventory fixture', base, (SELECT min(code) FROM currency WHERE code > base)
  RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Inventory Savings','savings',base,'XB',250000,'2024-01-01')
    RETURNING id INTO a;

  -- An opening balance is a stated fact until it is acknowledged (F05, Invariant I7).
  ASSERT (SELECT opening_balance_confirmed_at FROM account WHERE id = a) IS NULL,
    'an opening balance starts unacknowledged';
  ASSERT (SELECT opening_balance_note FROM account WHERE id = a) IS NULL,
    'an opening balance starts with no stated evidence';

  UPDATE account SET opening_balance_note = 'read from the bank app on 01 Jan 2024',
                     opening_balance_confirmed_at = now()
   WHERE id = a;
  ASSERT (SELECT opening_balance_confirmed_at FROM account WHERE id = a) IS NOT NULL,
    'acknowledging an opening balance records when';

  -- Closing preserves history; it never deletes the row (F06).
  BEGIN
    UPDATE account SET is_active = false WHERE id = a;
    RAISE EXCEPTION 'an inactive account without a closing date must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  UPDATE account SET is_active = false, closed_on = '2026-06-30' WHERE id = a;
  ASSERT (SELECT opening_balance FROM account WHERE id = a) = 250000,
    'a closed account keeps its opening balance and its history';
  ASSERT (SELECT count(*) FROM account WHERE id = a) = 1,
    'closing an account never removes it';

  RAISE NOTICE 'PASS 0007';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
