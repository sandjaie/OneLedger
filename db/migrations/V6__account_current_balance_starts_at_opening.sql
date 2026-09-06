-- db/migrations/V6__account_current_balance_starts_at_opening.sql
-- current_balance is materialised: opening_balance + SUM(transactions). DEFAULT 0 made "the
-- caller did not supply one" indistinguishable from "the caller means zero", so an account
-- created with a nonzero opening balance started at zero and contradicted its own documented
-- equation. Dropping the default lets NULL mean "derive it"; a BEFORE row trigger fills the
-- column before the NOT NULL check runs, so the column stays NOT NULL.
ALTER TABLE account ALTER COLUMN current_balance DROP DEFAULT;

CREATE FUNCTION derive_account_current_balance() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.current_balance IS NULL THEN
    NEW.current_balance := NEW.opening_balance;
    -- The materialised figure is true on the opening date. A caller-supplied as-of is not
    -- kept: that would present opening_balance as observed on another day (Invariant I7).
    NEW.current_balance_as_of := NEW.opening_balance_date::timestamptz;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_current_balance
  BEFORE INSERT ON account
  FOR EACH ROW EXECUTE FUNCTION derive_account_current_balance();

-- Existing rows predate the trigger — but for them "current_balance = 0 with a nonzero opening
-- balance" is exactly the ambiguity DEFAULT 0 created: an omitted value and an observed zero
-- are the same stored bytes, and rewriting both would overwrite a possible observation
-- (Invariant I7: nothing about money is invented silently). No account write path shipped
-- before this migration and no migration seeds account rows, so a real database has no such
-- row; if one exists it was inserted by hand, and the migration refuses and names the rows
-- for the operator to resolve explicitly instead of guessing.
--
-- Executable remediation (setting current_balance to the zero it already holds is a no-op and
-- would leave the predicate true forever):
--   1. DEFAULT-bug row → UPDATE account SET current_balance = opening_balance,
--      current_balance_as_of = opening_balance_date WHERE id = '<id>';
--   2. Observed-zero row → CREATE TABLE IF NOT EXISTS _v6_confirm_observed_zero (
--        account_id UUID PRIMARY KEY REFERENCES account(id));
--      INSERT INTO _v6_confirm_observed_zero (account_id) VALUES ('<id>');
-- Then re-run. This DO block drops _v6_confirm_observed_zero when present.
DO $$
DECLARE ambiguous BIGINT;
        ids TEXT;
BEGIN
  IF to_regclass('public._v6_confirm_observed_zero') IS NULL THEN
    SELECT count(*), string_agg(quote_literal(id::text), ', ' ORDER BY id)
      INTO ambiguous, ids
      FROM account
     WHERE current_balance = 0 AND opening_balance <> 0;
  ELSE
    SELECT count(*), string_agg(quote_literal(a.id::text), ', ' ORDER BY a.id)
      INTO ambiguous, ids
      FROM account a
     WHERE a.current_balance = 0 AND a.opening_balance <> 0
       AND NOT EXISTS (
             SELECT 1 FROM _v6_confirm_observed_zero z WHERE z.account_id = a.id
           );
  END IF;
  IF ambiguous > 0 THEN
    RAISE EXCEPTION USING MESSAGE = format(
      'V6: %s account row(s) have current_balance = 0 beside a nonzero opening balance: %s. '
      'DEFAULT 0 hid whether that zero was observed or merely omitted. Resolve each row: '
      'UPDATE account SET current_balance = opening_balance, '
      'current_balance_as_of = opening_balance_date WHERE id IN (%s)  (DEFAULT bug), or '
      'INSERT INTO _v6_confirm_observed_zero(account_id) SELECT unnest(ARRAY[%s]::uuid[]) after '
      'CREATE TABLE IF NOT EXISTS _v6_confirm_observed_zero '
      '(account_id UUID PRIMARY KEY REFERENCES account(id))  (preserve observed zero); '
      'then re-run the migration.',
      ambiguous, ids, ids, ids);
  END IF;
  DROP TABLE IF EXISTS _v6_confirm_observed_zero;
END $$;
