-- V58: subscription billing periods — the period table's shape and open-period uniqueness, the
-- lifecycle and custom-interval checks on recurring_series, and the backfill rule replayed on an
-- open series, a V56-ended ("cancelled") series and a removed ("not recurring") series.
-- All names, identifiers and amounts in tests and fixtures are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'recurring_series_period') = 1,
    'the recurring_series_period table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'recurring_series_period'::regclass),
    'recurring_series_period must have row level security enabled';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'uq_recurring_series_period_household_id') = 1,
    'uq_recurring_series_period_household_id must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'fk_recurring_series_period_series') = 1,
    'fk_recurring_series_period_series must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_period_end_reason_is_known') = 1,
    'recurring_series_period_end_reason_is_known must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_period_end_shape') = 1,
    'recurring_series_period_end_shape must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_period_dates_are_ordered') = 1,
    'recurring_series_period_dates_are_ordered must exist';
  ASSERT (SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_recurring_series_period_open') = 1,
    'uq_recurring_series_period_open must exist';

  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'recurring_series'
             AND column_name IN ('lifecycle', 'custom_interval_days')) = 2,
    'recurring_series must carry the V58 lifecycle and custom_interval_days columns';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_lifecycle_is_known') = 1,
    'recurring_series_lifecycle_is_known must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_custom_interval_is_bounded') = 1,
    'recurring_series_custom_interval_is_bounded must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_custom_interval_shape') = 1,
    'recurring_series_custom_interval_shape must exist';

  RAISE NOTICE 'PASS 0055 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0055_fixture()
  RETURNS TABLE (h UUID, p UUID, merch UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('subscription periods household', base, (SELECT max(code) FROM currency)) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'subscription periods owner', true) RETURNING id INTO p;
  INSERT INTO merchant (household_id, canonical_name)
  VALUES (h, 'Synthetic subscription periods merchant') RETURNING id INTO merch;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0055_series(
    household UUID, merchant UUID, base CHAR(3), ended BOOLEAN, ended_reason TEXT
  ) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE found UUID;
BEGIN
  INSERT INTO recurring_series (household_id, idempotency_key, label, merchant_id, cadence,
                                 expected_amount_base, currency, first_seen_on, last_seen_on,
                                 ended_on, ended_reason)
  VALUES (household, gen_random_uuid(), 'Synthetic subscription periods series', merchant,
          'monthly', 100, base, '2026-01-01', '2026-03-01',
          CASE WHEN ended THEN '2026-04-01'::DATE END,
          CASE WHEN ended THEN ended_reason END)
  RETURNING id INTO found;
  RETURN found;
END $$;

-- The period shapes: the end shape and date-ordering CHECKs, and at most one open period per
-- series.
BEGIN;
DO $$
DECLARE f RECORD; sid UUID; period_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0055_fixture();
  sid := pg_temp.t0055_series(f.h, f.merch, f.base, false, NULL);

  BEGIN
    INSERT INTO recurring_series_period (household_id, series_id, started_on, end_reason)
    VALUES (f.h, sid, '2026-01-01', 'cancelled');
    RAISE EXCEPTION 'an end_reason without ended_on must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_period_end_shape%', refusal;
  END;

  BEGIN
    INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on, end_reason)
    VALUES (f.h, sid, '2026-03-01', '2026-01-01', 'cancelled');
    RAISE EXCEPTION 'an ended_on before started_on must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_period_dates_are_ordered%', refusal;
  END;

  BEGIN
    INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on, end_reason)
    VALUES (f.h, sid, '2026-01-01', '2026-02-01', 'synthetic-invalid');
    RAISE EXCEPTION 'an unknown end_reason must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_period_end_reason_is_known%', refusal;
  END;

  INSERT INTO recurring_series_period (household_id, series_id, started_on)
  VALUES (f.h, sid, '2026-01-01') RETURNING id INTO period_id;

  BEGIN
    INSERT INTO recurring_series_period (household_id, series_id, started_on)
    VALUES (f.h, sid, '2026-02-01');
    RAISE EXCEPTION 'a second open period on the same series must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_recurring_series_period_open%', refusal;
  END;

  -- Closing the first period frees the series for a new open one.
  UPDATE recurring_series_period SET ended_on = '2026-02-01', end_reason = 'cancelled'
   WHERE id = period_id;
  INSERT INTO recurring_series_period (household_id, series_id, started_on)
  VALUES (f.h, sid, '2026-02-15');
  ASSERT (SELECT count(*) FROM recurring_series_period WHERE series_id = sid) = 2,
    'the closed period and the fresh open one must both exist';

  RAISE NOTICE 'PASS 0055 period shapes and open-period uniqueness';
END $$;
ROLLBACK;

-- The lifecycle and custom-interval CHECKs.
BEGIN;
DO $$
DECLARE f RECORD; series_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0055_fixture();
  series_id := pg_temp.t0055_series(f.h, f.merch, f.base, false, NULL);

  ASSERT (SELECT lifecycle FROM recurring_series WHERE id = series_id) = 'active',
    'lifecycle must default to active';

  BEGIN
    UPDATE recurring_series SET lifecycle = 'synthetic-unknown' WHERE id = series_id;
    RAISE EXCEPTION 'an unknown lifecycle must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_lifecycle_is_known%', refusal;
  END;
  UPDATE recurring_series SET lifecycle = 'trial' WHERE id = series_id;
  UPDATE recurring_series SET lifecycle = 'paused' WHERE id = series_id;

  BEGIN
    UPDATE recurring_series SET custom_interval_days = 6 WHERE id = series_id;
    RAISE EXCEPTION 'a custom interval under 7 days must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_custom_interval_is_bounded%', refusal;
  END;
  BEGIN
    UPDATE recurring_series SET custom_interval_days = 731 WHERE id = series_id;
    RAISE EXCEPTION 'a custom interval over 730 days must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_custom_interval_is_bounded%', refusal;
  END;

  -- The shape: custom_interval_days is set if and only if cadence is 'custom'.
  BEGIN
    UPDATE recurring_series SET custom_interval_days = 45 WHERE id = series_id;
    RAISE EXCEPTION 'a custom interval on a non-custom cadence must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_custom_interval_shape%', refusal;
  END;
  BEGIN
    UPDATE recurring_series SET cadence = 'custom' WHERE id = series_id;
    RAISE EXCEPTION 'switching to custom without an interval must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_custom_interval_shape%', refusal;
  END;
  UPDATE recurring_series SET cadence = 'custom', custom_interval_days = 45 WHERE id = series_id;
  ASSERT (SELECT custom_interval_days FROM recurring_series WHERE id = series_id) = 45,
    'a custom cadence with its interval set together must be accepted';

  RAISE NOTICE 'PASS 0055 lifecycle and custom-interval checks';
END $$;
ROLLBACK;

-- The backfill rule (V58's own migration statements), replayed inline on fixture rows exactly as
-- it ran against live data, in a rolled-back transaction — as db/tests/0054 replays V57's member
-- deduplication rule.
BEGIN;
DO $$
DECLARE
  f RECORD; open_id UUID; ended_id UUID; removed_id UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0055_fixture();
  open_id := pg_temp.t0055_series(f.h, f.merch, f.base, false, NULL);
  ended_id := pg_temp.t0055_series(f.h, f.merch, f.base, true, 'synthetic: cancelled');
  removed_id := pg_temp.t0055_series(f.h, f.merch, f.base, true, 'not_recurring');

  INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on, end_reason)
  SELECT household_id, id, first_seen_on, NULL, NULL
    FROM recurring_series WHERE household_id = f.h AND ended_on IS NULL;

  ALTER TABLE recurring_series DISABLE TRIGGER trg_preserve_ended_recurring_series;

  INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on, end_reason)
  SELECT household_id, id, first_seen_on, ended_on, 'cancelled'
    FROM recurring_series
   WHERE household_id = f.h AND ended_on IS NOT NULL AND ended_reason <> 'not_recurring';

  UPDATE recurring_series
     SET ended_on = NULL, ended_reason = NULL, updated_at = now()
   WHERE household_id = f.h AND ended_on IS NOT NULL AND ended_reason <> 'not_recurring';

  ALTER TABLE recurring_series ENABLE TRIGGER trg_preserve_ended_recurring_series;

  ASSERT (SELECT count(*) FROM recurring_series_period WHERE series_id = open_id) = 1,
    'the open series must get exactly one period';
  ASSERT (SELECT ended_on FROM recurring_series_period WHERE series_id = open_id) IS NULL,
    'the open series'' backfilled period must itself be open';
  ASSERT (SELECT started_on FROM recurring_series_period WHERE series_id = open_id) = '2026-01-01',
    'the open series'' period must start at first_seen_on';

  ASSERT (SELECT count(*) FROM recurring_series_period WHERE series_id = ended_id) = 1,
    'the V56-ended series must get exactly one period';
  ASSERT (SELECT started_on FROM recurring_series_period WHERE series_id = ended_id) = '2026-01-01'
     AND (SELECT ended_on FROM recurring_series_period WHERE series_id = ended_id) = '2026-04-01'
     AND (SELECT end_reason FROM recurring_series_period WHERE series_id = ended_id) = 'cancelled',
    'the V56-ended series'' period must span its recorded life, closed cancelled';
  ASSERT (SELECT ended_on FROM recurring_series WHERE id = ended_id) IS NULL,
    'the V56-ended series itself must be reopened (ended_on cleared)';
  ASSERT (SELECT ended_reason FROM recurring_series WHERE id = ended_id) IS NULL,
    'the V56-ended series'' ended_reason must be cleared with it';

  ASSERT (SELECT count(*) FROM recurring_series_period WHERE series_id = removed_id) = 0,
    'a series removed as not_recurring must get no period';
  ASSERT (SELECT ended_on FROM recurring_series WHERE id = removed_id) = '2026-04-01',
    'a series removed as not_recurring must keep its ended_on';
  ASSERT (SELECT ended_reason FROM recurring_series WHERE id = removed_id) = 'not_recurring',
    'a series removed as not_recurring must keep its ended_reason';

  RAISE NOTICE 'PASS 0055 backfill replayed on an open, a V56-ended and a removed series';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS, the same shape 0053 proves for recurring_series.
BEGIN;
DO $$
DECLARE f RECORD; f2 RECORD; series_id UUID; other_series UUID; u1 UUID; m1 UUID; u2 UUID; p1m UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0055_fixture();
  SELECT * INTO f2 FROM pg_temp.t0055_fixture();
  series_id := pg_temp.t0055_series(f.h, f.merch, f.base, false, NULL);
  other_series := pg_temp.t0055_series(f2.h, f2.merch, f2.base, false, NULL);
  INSERT INTO recurring_series_period (household_id, series_id, started_on) VALUES (f.h, series_id, '2026-01-01');
  INSERT INTO recurring_series_period (household_id, series_id, started_on)
  VALUES (f2.h, other_series, '2026-01-01');

  INSERT INTO person (household_id, display_name, is_self) VALUES (f.h, 'member', false) RETURNING id INTO p1m;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), f.h, f.p, 'subperiods-owner@example.invalid', 'owner') RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), f.h, p1m, 'subperiods-member@example.invalid', 'member') RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), f2.h, f2.p, 'subperiods-other@example.invalid', 'owner') RETURNING id INTO u2;

  PERFORM set_config('app.user_id', u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM recurring_series_period) = 1, 'owner sees only its own household''s periods';
  RESET ROLE;

  PERFORM set_config('app.user_id', m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM recurring_series_period) = 0, 'a household member reads no periods';
  RESET ROLE;

  PERFORM set_config('app.user_id', u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM recurring_series_period) = 1, 'the other household sees only its own periods';
  ASSERT (SELECT count(*) FROM recurring_series_period WHERE household_id = f.h) = 0,
    'the other household cannot read the first household''s periods';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM recurring_series_period) = 0, 'anonymous recurring_series_period reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0055 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
