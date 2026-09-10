BEGIN;

DO $$
DECLARE
  v_type TEXT;
BEGIN
  SELECT data_type
    INTO v_type
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'statement_layout'
     AND column_name = 'account_type';

  IF v_type IS DISTINCT FROM 'USER-DEFINED' THEN
    RAISE EXCEPTION 'statement_layout.account_type must use the account_type enum';
  END IF;
END $$;

ROLLBACK;
