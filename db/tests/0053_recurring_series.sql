-- V56: recurring series — the identity shape and the ended-series refusal. The plain
-- `REFERENCES merchant(id)`/`REFERENCES category(id)` foreign keys carry no tenant scope by
-- design (see V56's own comment); the tenant boundary is `routes/recurring.py`'s job and is
-- proven in `api/tests/test_recurring_live.py`, not here. V57 (Phase 4.1 Task 1) drops the
-- open-series partial unique indexes this file originally asserted and tested — one identity may
-- now carry several open series (spec "one descriptor, several subscriptions") — so the
-- structural assertion and the uniqueness test below were updated in that same migration's
-- branch rather than left asserting a rule V57 deliberately reversed; `db/tests/0054_recurring_
-- opt_out.sql` covers what replaced it (`uq_recurring_series_member_transaction`).
-- All names, identifiers and amounts in tests and fixtures are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'recurring_series') = 1,
    'the recurring_series table must exist';
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'recurring_series_member') = 1,
    'the recurring_series_member table must exist';
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'recurring_dismissal') = 1,
    'the recurring_dismissal table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'recurring_series'::regclass),
    'recurring_series must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'recurring_series_member'::regclass),
    'recurring_series_member must have row level security enabled';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'recurring_dismissal'::regclass),
    'recurring_dismissal must have row level security enabled';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'uq_recurring_series_household_id') = 1,
    'uq_recurring_series_household_id must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'uq_recurring_series_idempotency') = 1,
    'uq_recurring_series_idempotency must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_identity_shape') = 1,
    'recurring_series_identity_shape must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'fk_recurring_series_member_series') = 1,
    'fk_recurring_series_member_series must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'fk_recurring_series_member_transaction') = 1,
    'fk_recurring_series_member_transaction must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_dismissal_identity_shape') = 1,
    'recurring_dismissal_identity_shape must exist';
  ASSERT (SELECT count(*) FROM pg_trigger
           WHERE tgname = 'trg_preserve_ended_recurring_series') = 1,
    'trg_preserve_ended_recurring_series must exist';
  RAISE NOTICE 'PASS 0053 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0053_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    merch1 UUID, cat1 UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('recurring household one', base, (SELECT max(code) FROM currency)) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('recurring household two', base, (SELECT max(code) FROM currency)) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'recurring owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'recurring member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other recurring owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'recurring-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'recurring-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-recurring-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO merchant (household_id, canonical_name)
  VALUES (h1, 'Synthetic recurring merchant one') RETURNING id INTO merch1;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h1, 'Synthetic recurring category one', 'synthetic-recurring-category-one', 'expense',
          'need')
  RETURNING id INTO cat1;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0053_series(
    household UUID, merchant UUID, key TEXT, cadence recurring_cadence, amount NUMERIC,
    base CHAR(3)
  ) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE found UUID;
BEGIN
  INSERT INTO recurring_series (household_id, idempotency_key, label, merchant_id, match_key,
                                 cadence, expected_amount_base, currency, first_seen_on,
                                 last_seen_on)
  VALUES (household, gen_random_uuid(), 'Synthetic recurring series', merchant, key, cadence,
          amount, base, '2026-01-01', '2026-03-01')
  RETURNING id INTO found;
  RETURN found;
END $$;

-- The identity shape: neither a merchant nor a match key, or both, is refused.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0053_fixture();

  BEGIN
    PERFORM pg_temp.t0053_series(f.h1, NULL, NULL, 'monthly', 100, f.base);
    RAISE EXCEPTION 'a series with neither a merchant nor a match key must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_identity_shape%', refusal;
  END;
  BEGIN
    PERFORM pg_temp.t0053_series(f.h1, f.merch1, 'synthetic market', 'monthly', 100, f.base);
    RAISE EXCEPTION 'a series with both a merchant and a match key must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_identity_shape%', refusal;
  END;

  PERFORM pg_temp.t0053_series(f.h1, f.merch1, NULL, 'monthly', 100, f.base);
  PERFORM pg_temp.t0053_series(f.h1, NULL, 'synthetic market', 'monthly', 100, f.base);
  ASSERT (SELECT count(*) FROM recurring_series WHERE household_id = f.h1) = 2,
    'exactly the two valid rows must be accepted';

  RAISE NOTICE 'PASS 0053 identity shape';
END $$;
ROLLBACK;

-- At most one open series per identity per household; ending one frees the identity.
-- V57 (Phase 4.1 Task 1) drops the open-series partial unique indexes this block used to test:
-- one identity may now carry several open series at once (spec "one descriptor, several
-- subscriptions" — two amount clusters on one descriptor whose date ranges overlap are two
-- concurrent subscriptions), so a second open series on the same merchant or match key is
-- accepted rather than refused.
BEGIN;
DO $$
DECLARE f RECORD; first_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0053_fixture();

  first_id := pg_temp.t0053_series(f.h1, f.merch1, NULL, 'monthly', 100, f.base);
  PERFORM pg_temp.t0053_series(f.h1, f.merch1, NULL, 'monthly', 200, f.base);
  PERFORM pg_temp.t0053_series(f.h1, NULL, 'synthetic market', 'monthly', 100, f.base);
  PERFORM pg_temp.t0053_series(f.h1, NULL, 'synthetic market', 'monthly', 200, f.base);
  ASSERT (SELECT count(*) FROM recurring_series
           WHERE household_id = f.h1 AND merchant_id = f.merch1) = 2,
    'two open series on the same merchant must both exist';
  ASSERT (SELECT count(*) FROM recurring_series
           WHERE household_id = f.h1 AND match_key = 'synthetic market') = 2,
    'two open series on the same match key must both exist';

  -- A second household may open the same identity freely.
  PERFORM pg_temp.t0053_series(f.h2, f.merch1, NULL, 'monthly', 100, f.base);

  -- Ending a series frees nothing that was not already free, but still leaves the ended row
  -- alongside the still-open ones.
  UPDATE recurring_series SET ended_on = '2026-04-01', ended_reason = 'synthetic: cancelled'
   WHERE id = first_id;
  PERFORM pg_temp.t0053_series(f.h1, f.merch1, NULL, 'monthly', 300, f.base);
  ASSERT (SELECT count(*) FROM recurring_series
           WHERE household_id = f.h1 AND merchant_id = f.merch1) = 3,
    'the ended row and both open rows must all exist';

  RAISE NOTICE 'PASS 0053 several open series may share one identity';
END $$;
ROLLBACK;

-- An ended series is immutable: any further UPDATE is refused, the trigger's own defence in
-- depth behind the route's own ended_on read.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; series_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0053_fixture();
  series_id := pg_temp.t0053_series(f.h1, f.merch1, NULL, 'monthly', 100, f.base);

  -- Setting ended_on the first time succeeds.
  UPDATE recurring_series SET ended_on = '2026-04-01', ended_reason = 'synthetic: cancelled'
   WHERE id = series_id;

  BEGIN
    UPDATE recurring_series SET expected_amount_base = 999 WHERE id = series_id;
    RAISE EXCEPTION 'a further update to an ended series must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE 'an ended recurring series is immutable%', refusal;
  END;

  RAISE NOTICE 'PASS 0053 ended-series refusal';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0053_fixture();
  PERFORM pg_temp.t0053_series(f.h1, f.merch1, NULL, 'monthly', 100, f.base);
  PERFORM pg_temp.t0053_series(f.h2, NULL, 'synthetic other household market', 'monthly', 100,
                                f.base);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM recurring_series) = 1, 'owner sees only its own household''s series';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM recurring_series) = 0, 'a household member reads no series';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM recurring_series) = 1, 'the other household sees only its own series';
  ASSERT (SELECT count(*) FROM recurring_series WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s series';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM recurring_series) = 0, 'anonymous recurring_series reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0053 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
