-- db/migrations/V7__household_configuration.sql
-- The household's name, the owner's display name and tax residency, and the reporting pair are
-- user-editable application data, not deployment configuration. They were seeded from five .env
-- variables; they now start neutral here and are edited in Settings > Household.

-- One neutral household and person, so an owner row can exist before the first sign-in:
-- require_owner() resolves app_user -> person -> household, and there is no authenticated
-- moment before all three exist in which an onboarding screen could run.
--
-- No currency code appears here. The pair is the first two seeded currencies by code, and the
-- owner chooses the real one in Settings before the first import (ADR-0002, ADR-0012).
INSERT INTO household (name, base_currency, secondary_currency)
SELECT 'Household',
       (SELECT min(code) FROM currency),
       (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency))
 WHERE NOT EXISTS (SELECT FROM household)
   AND (SELECT count(*) FROM currency) >= 2;

INSERT INTO person (household_id, display_name, is_self)
SELECT id, 'Owner', true FROM household
 WHERE NOT EXISTS (SELECT FROM person);

-- "Is any amount already frozen against the current pair?", answered from the catalogue rather
-- than a hardcoded table list, so it stays correct when the transaction table arrives in M2
-- without anyone remembering to come back here. Today the set is empty and it answers false.
CREATE FUNCTION reporting_pair_is_locked() RETURNS BOOLEAN
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE frozen_table TEXT; rows_found BIGINT;
BEGIN
  PERFORM assert_is_owner();
  FOR frozen_table IN
    SELECT c.table_name
      FROM information_schema.columns c
     WHERE c.table_schema = 'public' AND c.column_name = 'amount_base'
       AND EXISTS (SELECT FROM information_schema.columns h
                    WHERE h.table_schema = 'public' AND h.table_name = c.table_name
                      AND h.column_name = 'household_id')
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM public.%I WHERE household_id = current_household_id()',
      frozen_table) INTO rows_found;
    IF rows_found > 0 THEN RETURN true; END IF;
  END LOOP;
  RETURN false;
END $$;

-- The guard and the write are one function because an import could otherwise freeze the first
-- amount between a separate check and a separate UPDATE (api-contracts.html section 3). One
-- function gives atomic rollback but not inter-transaction serialization, so the function also
-- locks the household row; every writer of a frozen amount_base/amount_secondary row takes the
-- same lock before writing (spec B3.5 — M1b manual entry first, M2 import after it).
CREATE FUNCTION update_household_configuration(
  p_name               TEXT,
  p_display_name       TEXT,
  p_tax_residency      CHAR(2),
  p_base_currency      CHAR(3),
  p_secondary_currency CHAR(3)
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE h UUID;
BEGIN
  PERFORM assert_is_owner();
  h := current_household_id();

  -- Serialize with any concurrent frozen-amount writer: a transaction that has read the old
  -- pair but not yet committed its first frozen row would make reporting_pair_is_locked()
  -- answer false here while old-pair amounts are about to freeze. The household row is the
  -- rendezvous; the interleaving test arrives with M1b's manual entry, the first such writer.
  PERFORM 1 FROM household WHERE id = h FOR UPDATE;

  IF p_base_currency = p_secondary_currency THEN
    RAISE EXCEPTION 'the reporting pair must name two different currencies'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  IF EXISTS (SELECT FROM household
              WHERE id = h
                AND (base_currency, secondary_currency)
                    IS DISTINCT FROM (p_base_currency, p_secondary_currency))
     AND reporting_pair_is_locked() THEN
    RAISE EXCEPTION 'the reporting pair is fixed once amounts are frozen'
      USING ERRCODE = 'object_in_use';
  END IF;

  UPDATE household
     SET name               = p_name,
         base_currency      = p_base_currency,
         secondary_currency = p_secondary_currency
   WHERE id = h;

  UPDATE person
     SET display_name  = p_display_name,
         tax_residency = p_tax_residency
   WHERE household_id = h AND is_self;
END $$;
