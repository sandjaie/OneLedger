-- db/tests/0056_payment_channels.sql
-- V59: the payment_channel shape, its seed rows, its RLS (the same shape 0008 proves for
-- category), and recurring_series.payment_channel_id.
-- All names, identifiers and amounts in tests and fixtures are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'payment_channel'),
    'RLS must be enabled on payment_channel';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'payment_channel_name_is_bounded') = 1,
    'payment_channel_name_is_bounded must exist';
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'recurring_series' AND column_name = 'payment_channel_id') = 1,
    'recurring_series must carry payment_channel_id';
  ASSERT EXISTS (
    SELECT FROM pg_constraint
     WHERE conrelid = 'recurring_series'::regclass AND contype = 'f'
       AND confrelid = 'payment_channel'::regclass),
    'recurring_series.payment_channel_id must be a foreign key to payment_channel';

  ASSERT (SELECT count(*) FROM payment_channel WHERE household_id IS NULL) = 6,
    'the seed carries exactly six generic channel rows';
  ASSERT NOT EXISTS (SELECT FROM payment_channel WHERE household_id IS NULL AND NOT is_active),
    'every seed channel starts active';
  ASSERT (SELECT count(DISTINCT name) FROM payment_channel WHERE household_id IS NULL) = 6,
    'seed channel names are distinct';
  -- ADR-0012: no institution, store or wallet brand as a seed name.
  ASSERT NOT EXISTS (
    SELECT FROM payment_channel
     WHERE household_id IS NULL
       AND lower(name) ~ '(visa|mastercard|amex|paypal|apple|google|stripe|venmo)'),
    'seed channel names must be generic kinds, never a brand';

  RAISE NOTICE 'PASS 0056 structure and seed';
END $$;

-- The name bound, the same shape category's own CHECK takes.
BEGIN;
DO $$
DECLARE refusal TEXT;
BEGIN
  BEGIN
    INSERT INTO payment_channel (name) VALUES ('');
    RAISE EXCEPTION 'an empty name must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%payment_channel_name_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO payment_channel (name) VALUES (' padded ');
    RAISE EXCEPTION 'an untrimmed name must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%payment_channel_name_is_bounded%', refusal;
  END;
  BEGIN
    INSERT INTO payment_channel (name) VALUES (repeat('x', 61));
    RAISE EXCEPTION 'a name over 60 characters must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%payment_channel_name_is_bounded%', refusal;
  END;
  RAISE NOTICE 'PASS 0056 name bound';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS — the shared-seed shape category's own read/write
-- policies take (0008), proven here for payment_channel.
BEGIN;
DO $$
DECLARE h UUID; h2 UUID; p1 UUID; p1m UUID; p2 UUID; base CHAR(3); secondary CHAR(3);
        u1 UUID; m1 UUID; u2 UUID; own_channel UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT max(code) INTO secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('payment channel household', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'member', false) RETURNING id INTO p1m;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('other payment channel household', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h2, 'them', true) RETURNING id INTO p2;

  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h, p1, 'paychan-owner@example.invalid', 'owner') RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h, p1m, 'paychan-member@example.invalid', 'member') RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'paychan-other@example.invalid', 'owner') RETURNING id INTO u2;

  INSERT INTO payment_channel (household_id, name) VALUES (h, 'Synthetic household channel')
  RETURNING id INTO own_channel;

  PERFORM set_config('app.user_id', u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payment_channel WHERE household_id IS NULL) = 6,
    'owner reads the seed channels';
  ASSERT (SELECT count(*) FROM payment_channel WHERE id = own_channel) = 1,
    'owner reads its own household channel';
  RESET ROLE;

  PERFORM set_config('app.user_id', m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payment_channel WHERE household_id IS NOT NULL) = 0,
    'a household member reads no household-owned channel (current_household_id is owner-only)';
  ASSERT (SELECT count(*) FROM payment_channel WHERE household_id IS NULL) = 6,
    'a household member still reads the seed channels';
  RESET ROLE;

  PERFORM set_config('app.user_id', u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM payment_channel WHERE id = own_channel) = 0,
    'the other household cannot read the first household''s channel';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM payment_channel WHERE household_id IS NULL) = 6,
    'anon still reads the shared seed channels';
  ASSERT (SELECT count(*) FROM payment_channel WHERE household_id IS NOT NULL) = 0,
    'anon reads no household-owned channel';

  RAISE NOTICE 'PASS 0056 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
