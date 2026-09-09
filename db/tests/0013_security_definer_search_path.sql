-- db/tests/0013_security_definer_search_path.sql
-- V15: every SECURITY DEFINER function names `pg_temp` in its search_path, explicitly and last.
--
-- Postgres searches the session's temporary schema **first** precisely when `pg_temp` is absent
-- from a function's `search_path`. So `SET search_path = public` — which reads like hardening and
-- was applied as such — leaves every unqualified relation in a definer function resolvable to a
-- table the caller created. The definer runs as the owner, which here is a superuser that
-- bypasses RLS, so whatever the caller substitutes is read with full privilege.
--
-- `current_household_id()` is the one that matters: every RLS policy on every table reads it, and
-- `assert_is_owner()` is a wrapper around it. A caller-created `pg_temp.app_user` makes it return
-- a household of the caller's choosing, which is the authorization boundary answering to the
-- attacker. This suite is the regression test for that.
\set ON_ERROR_STOP on

-- The rule, enforced against the catalogue rather than against a list someone has to remember to
-- update. A function added later without `pg_temp` fails here on the day it is added.
DO $$
DECLARE offender TEXT;
BEGIN
  SELECT string_agg(p.proname || ' (' || COALESCE(array_to_string(p.proconfig, ','), 'no search_path') || ')', ', ')
    INTO offender
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'public'
    AND p.prosecdef
    AND NOT EXISTS (
      SELECT 1 FROM unnest(COALESCE(p.proconfig, ARRAY[]::TEXT[])) AS cfg
      WHERE cfg LIKE 'search_path=%' AND cfg LIKE '%pg_temp%'
    );

  ASSERT offender IS NULL,
    'every SECURITY DEFINER function must name pg_temp in its search_path; these do not: '
    || offender;
  RAISE NOTICE 'PASS 0013 every definer function names pg_temp';
END $$;

-- `pg_temp` must be LAST. Naming it first would be worse than omitting it — it would make the
-- capture explicit policy rather than an oversight.
DO $$
DECLARE offender TEXT;
BEGIN
  SELECT string_agg(p.proname, ', ') INTO offender
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'public' AND p.prosecdef
    AND EXISTS (
      SELECT 1 FROM unnest(p.proconfig) AS cfg
      WHERE cfg LIKE 'search_path=%' AND cfg LIKE '%pg_temp%'
        AND cfg NOT LIKE '%pg_temp'
    );

  ASSERT offender IS NULL,
    'pg_temp must be the last entry in search_path, not the first: ' || offender;
  RAISE NOTICE 'PASS 0013 pg_temp is searched last';
END $$;

-- The boundary itself, exercised rather than inspected. A temp `app_user` that claims the caller
-- belongs to a household of their choosing must change nothing.
BEGIN;
DO $$
DECLARE h UUID; p UUID; u UUID; hijacked UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('search path test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'self', true) RETURNING id INTO p;
  u := gen_random_uuid();
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (u, h, p, 'search-path-' || u || '@example.invalid', 'owner');
  PERFORM set_config('app.user_id', u::TEXT, true);

  ASSERT current_household_id() = h, 'the genuine household is returned to begin with';

  -- The attack: a temporary table of the same name, claiming a different household.
  CREATE TEMP TABLE app_user (id UUID, household_id UUID, person_id UUID, email TEXT,
                              role TEXT, created_at TIMESTAMPTZ) ON COMMIT DROP;
  INSERT INTO pg_temp.app_user
  VALUES (u, '99999999-9999-9999-9999-999999999999', p, 'x', 'owner', now());

  hijacked := current_household_id();
  ASSERT hijacked = h,
    format('current_household_id() must read public.app_user, not a temp table; got %s',
           hijacked);
  ASSERT hijacked <> '99999999-9999-9999-9999-999999999999',
    'the caller must not be able to choose their own household';

  -- assert_is_owner() is a wrapper around it, so it inherits the fix rather than needing one.
  PERFORM assert_is_owner();
  RAISE NOTICE 'PASS 0013 the authorization boundary cannot be captured';
END $$;
ROLLBACK;

-- The same for the reporting-pair lock, which decides whether the household's currency pair may
-- still change. Captured, `reporting_pair_is_locked()` reads a temp `transaction` holding no
-- rows and reports the pair unlocked, letting it be changed out from under frozen amounts.
BEGIN;
DO $$
DECLARE h UUID; p UUID; u UUID; a UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('lock test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'self', true) RETURNING id INTO p;
  u := gen_random_uuid();
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (u, h, p, 'lock-' || u || '@example.invalid', 'owner');
  PERFORM set_config('app.user_id', u::TEXT, true);

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
  VALUES (h, p, 'Locked', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO a;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_is_carried, fx_rate_to_base,
                           fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
                           source, dedupe_hash)
  VALUES (h, a, '2026-03-01', 'FROZEN', 10, base, '2026-03-01', false, 1, 1, 10, 10,
          'income', 'manual', repeat('e', 64));

  ASSERT reporting_pair_is_locked(), 'a frozen amount locks the pair';

  CREATE TEMP TABLE transaction (LIKE public.transaction INCLUDING DEFAULTS) ON COMMIT DROP;
  ASSERT reporting_pair_is_locked(),
    'an empty temp table must not unlock a pair that real frozen amounts have locked';
  RAISE NOTICE 'PASS 0013 the reporting-pair lock cannot be unlocked by a temp table';
END $$;
ROLLBACK;
