-- db/migrations/V15__definer_search_path_excludes_pg_temp.sql
-- Every SECURITY DEFINER function names `pg_temp` in its search_path, explicitly and last.
--
-- V2, V5 and V7 each set `SET search_path = public` on their definer functions. That reads like
-- hardening and is not: **Postgres searches the session's temporary schema first precisely when
-- `pg_temp` is absent from the path.** So every unqualified relation in those function bodies was
-- resolvable to a table the caller had created, and the definer here is the owning superuser,
-- which bypasses RLS — so whatever the caller substituted was read with full privilege.
--
-- `current_household_id()` is the one that matters. Every RLS policy in the schema reads it and
-- `assert_is_owner()` is a wrapper around it, so it is the authorization boundary (ADR-0013,
-- ADR-0016). Verified on PostgreSQL 18.6: with
--
--     CREATE TEMP TABLE app_user (id UUID, household_id UUID, ...);
--     INSERT INTO pg_temp.app_user VALUES (current_setting('app.user_id')::UUID, '9999…', …);
--
-- in the session, `current_household_id()` returned `9999…` — a household of the caller's
-- choosing. `reporting_pair_is_locked()` is the same shape one step down: captured by an empty
-- temp `transaction`, it reports a pair unlocked that real frozen amounts have locked, letting
-- the reporting pair change out from under them.
--
-- The bodies below are unchanged from V2, V5 and V7. Only the `search_path` moves. Those
-- migrations are applied and Flyway checksums them, so the repair arrives as a new migration
-- rather than as an edit to them — which is also why this file restates each definition in full.
--
-- `db/tests/0013` enforces the rule against the catalogue rather than against a list, so a
-- definer function added later without `pg_temp` fails on the day it is added.

-- V5's definition, which replaced V2's by adding the `role = 'owner'` predicate.
CREATE OR REPLACE FUNCTION current_household_id() RETURNS UUID
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
  SELECT household_id FROM app_user
  WHERE id = NULLIF(current_setting('app.user_id', true), '')::UUID AND role = 'owner'
$$;

CREATE OR REPLACE FUNCTION assert_is_owner() RETURNS VOID
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
BEGIN
  IF current_household_id() IS NULL THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION reporting_pair_is_locked() RETURNS BOOLEAN
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp
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

CREATE OR REPLACE FUNCTION update_household_configuration(
  p_name TEXT, p_display_name TEXT, p_tax_residency CHAR(2),
  p_base_currency CHAR(3), p_secondary_currency CHAR(3)
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE h UUID;
BEGIN
  PERFORM assert_is_owner();
  h := current_household_id();

  -- Serialize with any concurrent frozen-amount writer: a transaction that has read the old
  -- pair but not yet committed its first frozen row would make reporting_pair_is_locked()
  -- answer false here while old-pair amounts are about to freeze. The household row is the
  -- rendezvous; `commit_import_batch` (V14) is now the second such writer and takes it too.
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
