-- V29 completes the account-identity transition: the typed relation is authoritative and the
-- legacy account columns can no longer receive a second, drifting copy.
DO $$
DECLARE legacy_columns TEXT[];
BEGIN
  SELECT array_agg(column_name ORDER BY column_name)
    INTO legacy_columns
    FROM information_schema.columns
   WHERE table_schema = 'public'
     AND table_name = 'account'
     AND column_name IN ('account_number_masked', 'account_number_hash', 'iban_masked');

  IF legacy_columns IS NOT NULL THEN
    RAISE EXCEPTION 'legacy account identity columns remain: %', legacy_columns;
  END IF;

  IF to_regclass('public.idx_account_number_hash') IS NOT NULL THEN
    RAISE EXCEPTION 'legacy account identity index remains';
  END IF;

  IF EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'account'::regclass
       AND conname IN (
         'account_legacy_identity_is_migratable',
         'card_has_no_account_number_hash'
       )
  ) THEN
    RAISE EXCEPTION 'legacy account identity constraint remains';
  END IF;

  IF to_regclass('public.account_identifier') IS NULL THEN
    RAISE EXCEPTION 'typed account identifier relation is missing';
  END IF;

  RAISE NOTICE 'PASS 0026 retired account identity columns';
END $$;
