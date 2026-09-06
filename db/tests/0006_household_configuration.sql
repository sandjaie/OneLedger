-- db/tests/0006_household_configuration.sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM household) <= 1,
    'the seed creates at most one household';
  ASSERT NOT EXISTS (
    SELECT FROM household h
     WHERE NOT EXISTS (SELECT FROM currency WHERE code = h.base_currency)
        OR NOT EXISTS (SELECT FROM currency WHERE code = h.secondary_currency)),
    'a seeded reporting pair names seeded currencies';
END $$;

BEGIN;
  WITH pair AS (
    SELECT (SELECT min(code) FROM currency) AS base,
           (SELECT min(code) FROM currency
             WHERE code > (SELECT min(code) FROM currency)) AS secondary
  )
  INSERT INTO household (id, name, base_currency, secondary_currency)
  SELECT '61111111-1111-1111-1111-111111111111', 'config fixture', base, secondary FROM pair;
  INSERT INTO person (id, household_id, display_name, is_self) VALUES
    ('62222222-2222-2222-2222-222222222222',
     '61111111-1111-1111-1111-111111111111', 'fixture owner', true);
  INSERT INTO app_user (id, household_id, person_id, email) VALUES
    ('63333333-3333-3333-3333-333333333333',
     '61111111-1111-1111-1111-111111111111',
     '62222222-2222-2222-2222-222222222222', 'config@example.test');

  DO $$
  DECLARE was_base CHAR(3); was_secondary CHAR(3);
  BEGIN
    SELECT base_currency, secondary_currency INTO was_base, was_secondary
      FROM household WHERE id = '61111111-1111-1111-1111-111111111111';

    -- Authentication is not authorization: an unscoped caller reconfigures nothing.
    BEGIN
      PERFORM update_household_configuration('x','y',NULL, was_base, was_secondary);
      RAISE EXCEPTION 'an unscoped caller must not reconfigure a household';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    PERFORM set_config('app.user_id', '63333333-3333-3333-3333-333333333333', true);

    ASSERT reporting_pair_is_locked() = false,
      'no amount is frozen, so the pair is still editable';

    PERFORM update_household_configuration(
      'Renamed household', 'Renamed owner', 'XA', was_secondary, was_base);
    ASSERT (SELECT name FROM household WHERE id = '61111111-1111-1111-1111-111111111111')
             = 'Renamed household', 'the household name is editable';
    ASSERT (SELECT base_currency FROM household WHERE id = '61111111-1111-1111-1111-111111111111')
             = was_secondary, 'the pair is editable while nothing is frozen';
    ASSERT (SELECT display_name FROM person WHERE id = '62222222-2222-2222-2222-222222222222')
             = 'Renamed owner', 'the owner display name is editable';
    ASSERT (SELECT tax_residency FROM person WHERE id = '62222222-2222-2222-2222-222222222222')
             = 'XA', 'tax residency is editable';

    BEGIN
      PERFORM update_household_configuration('n','o',NULL, was_base, was_base);
      RAISE EXCEPTION 'two identical currencies must be refused';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
  END $$;

  -- A table shaped exactly like the ones M2 adds, so the guard is proved rather than assumed.
  -- It lives and dies inside this rolled-back transaction.
  CREATE TABLE frozen_amount_probe (
    household_id UUID NOT NULL,
    amount_base  NUMERIC(20,4) NOT NULL
  );
  INSERT INTO frozen_amount_probe VALUES ('61111111-1111-1111-1111-111111111111', 100.0000);

  DO $$
  DECLARE was_base CHAR(3); was_secondary CHAR(3);
  BEGIN
    PERFORM set_config('app.user_id', '63333333-3333-3333-3333-333333333333', true);
    SELECT base_currency, secondary_currency INTO was_base, was_secondary
      FROM household WHERE id = '61111111-1111-1111-1111-111111111111';

    ASSERT reporting_pair_is_locked() = true, 'a frozen amount locks the reporting pair';

    -- Swapping the two is refused with the rest: amount_base and amount_secondary are frozen
    -- against particular currencies, so exchanging the roles relabels every stored amount.
    BEGIN
      PERFORM update_household_configuration(
        'still editable', 'still editable', NULL, was_secondary, was_base);
      RAISE EXCEPTION 'a frozen pair must not be swapped';
    EXCEPTION WHEN object_in_use THEN NULL;
    END;

    -- The rest of the configuration stays editable while the pair is locked.
    PERFORM update_household_configuration(
      'Renamed again', 'Renamed again', NULL, was_base, was_secondary);
    ASSERT (SELECT name FROM household WHERE id = '61111111-1111-1111-1111-111111111111')
             = 'Renamed again', 'name and person stay editable behind a locked pair';
  END $$;
ROLLBACK;
