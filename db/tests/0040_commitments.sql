-- V43: commitments are owner assumptions, not evidenced facts — no SECURITY DEFINER write path,
-- just household-scoped RLS and an account-scoped composite foreign key. All names, identifiers
-- and amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'commitment') = 1,
    'the commitment table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'commitment'::regclass),
    'commitment must have row level security enabled';
  RAISE NOTICE 'PASS 0040 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0040_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID, a1 UUID, a2 UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('commitment household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('commitment household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'commitment owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'commitment member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other commitment owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'commitment-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'commitment-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-commitment-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic commitment account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Other synthetic commitment account', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a2;
  RETURN NEXT;
END $$;

-- A cross-household account reference is refused by the composite foreign key.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0040_fixture();
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date)
    VALUES (f.h1, gen_random_uuid(), f.a2, 'Synthetic rent', -1000.0000, f.base, 'monthly',
            '2026-01-01');
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_commitment_account_scope%', refusal;
  END;
  RAISE NOTICE 'PASS 0040 cross-household account foreign key refused';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS: only the owning household's authenticated owner
-- reads or writes its own commitment rows.
BEGIN;
DO $$
DECLARE f RECORD; c1 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0040_fixture();
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic salary', 5000.0000, f.base, 'monthly',
          '2026-01-01')
  RETURNING id INTO c1;
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date)
  VALUES (f.h2, gen_random_uuid(), f.a2, 'Other synthetic rent', -2000.0000, f.base, 'monthly',
          '2026-01-01');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM commitment) = 1, 'owner sees only its own household commitment';
  ASSERT (SELECT label FROM commitment WHERE id = c1) = 'Synthetic salary',
    'owner reads its own commitment row';
  RESET ROLE;

  -- current_household_id() resolves only an 'owner' row (V5): a member's app.user_id never
  -- names a household at all, so household_isolation excludes every row for a member exactly
  -- as it does for a stranger.
  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM commitment) = 0, 'a household member reads no commitment';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM commitment) = 1, 'the other household sees only its own row';
  ASSERT (SELECT label FROM commitment WHERE household_id = f.h1) IS NULL,
    'the other household cannot read the first household''s commitment';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM commitment) = 0, 'anonymous commitment reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0040 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
