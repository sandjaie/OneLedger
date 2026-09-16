-- V57: recurring opt-out — the enum values, the subscription and native-amount columns, the
-- member-transaction uniqueness (and the duplicate cleanup that made it possible), the anchored
-- dismissal indexes, and the seed subscription-category map. All names, identifiers and amounts
-- are synthetic or seed data.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
           WHERE t.typname = 'recurring_cadence' AND e.enumlabel = 'semiannual') = 1,
    'recurring_cadence must carry semiannual';
  ASSERT (SELECT count(*) FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
           WHERE t.typname = 'recurring_cadence' AND e.enumlabel = 'custom') = 1,
    'recurring_cadence must carry custom';

  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'recurring_series'
             AND column_name IN ('is_subscription', 'subscription_set_by_owner',
                                  'detection_method', 'named_by_owner', 'native_amount',
                                  'native_currency')) = 6,
    'recurring_series must carry every V57 column';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_detection_method_is_known') = 1,
    'recurring_series_detection_method_is_known must exist';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'recurring_series_native_pair') = 1,
    'recurring_series_native_pair must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'recurring_series_native_amount_is_positive') = 1,
    'recurring_series_native_amount_is_positive must exist';

  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'uq_recurring_series_member_transaction') = 1,
    'uq_recurring_series_member_transaction must exist';
  ASSERT (SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_recurring_series_open_merchant') = 0,
    'uq_recurring_series_open_merchant must be gone: one identity may carry several open series';
  ASSERT (SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_recurring_series_open_match_key') = 0,
    'uq_recurring_series_open_match_key must be gone';

  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'recurring_dismissal'
             AND column_name IN ('anchor_amount', 'anchor_currency')) = 2,
    'recurring_dismissal must carry the anchor pair';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'recurring_dismissal_anchor_pair') = 1,
    'recurring_dismissal_anchor_pair must exist';
  ASSERT (SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_recurring_dismissal_merchant')
         LIKE '%NULLS NOT DISTINCT%',
    'uq_recurring_dismissal_merchant must be NULLS NOT DISTINCT';
  ASSERT (SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_recurring_dismissal_match_key')
         LIKE '%NULLS NOT DISTINCT%',
    'uq_recurring_dismissal_match_key must be NULLS NOT DISTINCT';

  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'recurring_subscription_category') = 1,
    'recurring_subscription_category must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class
           WHERE oid = 'recurring_subscription_category'::regclass),
    'recurring_subscription_category must have row level security enabled';

  RAISE NOTICE 'PASS 0054 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0054_fixture()
  RETURNS TABLE (h UUID, p UUID, a UUID, merch UUID, base CHAR(3))
  LANGUAGE plpgsql AS $$
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('recurring opt-out household', base, (SELECT max(code) FROM currency)) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h, 'recurring opt-out owner', true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h, p, 'Synthetic opt-out account', 'savings', base, 'XB', 0, '2026-01-01')
  RETURNING id INTO a;
  INSERT INTO merchant (household_id, canonical_name)
  VALUES (h, 'Synthetic opt-out merchant') RETURNING id INTO merch;
  RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0054_series(
    household UUID, merchant UUID, cadence recurring_cadence, base CHAR(3), ended BOOLEAN
  ) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE found UUID;
BEGIN
  INSERT INTO recurring_series (household_id, idempotency_key, label, merchant_id, cadence,
                                 expected_amount_base, currency, first_seen_on, last_seen_on,
                                 ended_on, ended_reason)
  VALUES (household, gen_random_uuid(), 'Synthetic opt-out series', merchant, cadence, 100, base,
          '2026-01-01', '2026-03-01',
          CASE WHEN ended THEN '2026-04-01'::DATE END,
          CASE WHEN ended THEN 'synthetic: cancelled' END)
  RETURNING id INTO found;
  RETURN found;
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0054_txn(household UUID, account UUID, base CHAR(3), on_date DATE)
  RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE found UUID;
BEGIN
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
  VALUES (household, account, on_date, 'SYNTHETIC OPT-OUT CHARGE', -20, base, on_date, 1, 1,
          -20, -20, 'expense', 'manual', md5(random()::text))
  RETURNING id INTO found;
  RETURN found;
END $$;

-- New columns and their CHECK shapes.
BEGIN;
DO $$
DECLARE f RECORD; series_id UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0054_fixture();
  series_id := pg_temp.t0054_series(f.h, f.merch, 'monthly', f.base, false);

  BEGIN
    UPDATE recurring_series SET detection_method = 'guessed' WHERE id = series_id;
    RAISE EXCEPTION 'an unknown detection_method must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_detection_method_is_known%', refusal;
  END;

  BEGIN
    UPDATE recurring_series SET native_amount = 10 WHERE id = series_id;
    RAISE EXCEPTION 'a native amount without a native currency must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_native_pair%', refusal;
  END;

  BEGIN
    UPDATE recurring_series SET native_amount = -10, native_currency = f.base WHERE id = series_id;
    RAISE EXCEPTION 'a non-positive native amount must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%recurring_series_native_amount_is_positive%', refusal;
  END;

  UPDATE recurring_series SET native_amount = 10, native_currency = f.base WHERE id = series_id;
  UPDATE recurring_series SET cadence = 'semiannual' WHERE id = series_id;
  UPDATE recurring_series SET cadence = 'custom' WHERE id = series_id;

  RAISE NOTICE 'PASS 0054 new columns and shapes';
END $$;
ROLLBACK;

-- Member uniqueness after duplicate removal. V57's own migration deleted, for each duplicated
-- transaction, every member row but the one on the newest series (open before ended, then latest
-- created_at, then id) before adding uq_recurring_series_member_transaction; this rehearses that
-- exact rule against a fresh duplicate, dropping and restoring the constraint inside this rolled-
-- back transaction so the live schema is never actually widened.
BEGIN;
DO $$
DECLARE f RECORD; ended_id UUID; open_id UUID; txn_id UUID; deleted_count INTEGER; survivor UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0054_fixture();
  ended_id := pg_temp.t0054_series(f.h, f.merch, 'monthly', f.base, true);
  open_id := pg_temp.t0054_series(f.h, f.merch, 'monthly', f.base, false);
  txn_id := pg_temp.t0054_txn(f.h, f.a, f.base, '2026-02-01');

  ALTER TABLE recurring_series_member DROP CONSTRAINT uq_recurring_series_member_transaction;

  INSERT INTO recurring_series_member (household_id, series_id, transaction_id, observed_on, amount_base)
  VALUES (f.h, ended_id, txn_id, '2026-02-01', 20);
  INSERT INTO recurring_series_member (household_id, series_id, transaction_id, observed_on, amount_base)
  VALUES (f.h, open_id, txn_id, '2026-02-01', 20);
  ASSERT (SELECT count(*) FROM recurring_series_member WHERE household_id = f.h) = 2,
    'both duplicate member rows must exist before cleanup';

  WITH ranked AS (
    SELECT m.household_id, m.series_id, m.transaction_id,
           row_number() OVER (
             PARTITION BY m.household_id, m.transaction_id
             ORDER BY (s.ended_on IS NULL) DESC, s.created_at DESC, s.id DESC
           ) AS rnk
      FROM recurring_series_member m
      JOIN recurring_series s ON s.household_id = m.household_id AND s.id = m.series_id
     WHERE m.household_id = f.h
  ),
  doomed AS (SELECT household_id, series_id, transaction_id FROM ranked WHERE rnk > 1)
  DELETE FROM recurring_series_member m
   USING doomed d
   WHERE m.household_id = d.household_id AND m.series_id = d.series_id
     AND m.transaction_id = d.transaction_id;
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  ASSERT deleted_count = 1, 'exactly the ended series'' row must be removed';

  SELECT series_id INTO survivor FROM recurring_series_member
   WHERE household_id = f.h AND transaction_id = txn_id;
  ASSERT survivor = open_id, 'the surviving member row must be the open series'' one';

  ALTER TABLE recurring_series_member
    ADD CONSTRAINT uq_recurring_series_member_transaction UNIQUE (household_id, transaction_id);

  BEGIN
    INSERT INTO recurring_series_member (household_id, series_id, transaction_id, observed_on, amount_base)
    VALUES (f.h, ended_id, txn_id, '2026-02-01', 20);
    RAISE EXCEPTION 'a transaction already a member elsewhere must be refused';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;

  RAISE NOTICE 'PASS 0054 member uniqueness after duplicate removal';
END $$;
ROLLBACK;

-- The dismissal indexes: a null anchor still means "the whole identity" and a duplicate is
-- refused; a distinct anchor is a separate dismissal on the same descriptor.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0054_fixture();

  INSERT INTO recurring_dismissal (household_id, merchant_id) VALUES (f.h, f.merch);
  BEGIN
    INSERT INTO recurring_dismissal (household_id, merchant_id) VALUES (f.h, f.merch);
    RAISE EXCEPTION 'a second null-anchor dismissal for the same identity must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_recurring_dismissal_merchant%', refusal;
  END;

  -- A distinct anchor on the same identity is a different dismissal (spec: several subscriptions
  -- on one descriptor are removed independently).
  INSERT INTO recurring_dismissal (household_id, merchant_id, anchor_amount, anchor_currency)
  VALUES (f.h, f.merch, 9.99, f.base);
  ASSERT (SELECT count(*) FROM recurring_dismissal WHERE household_id = f.h) = 2,
    'a differently anchored dismissal on the same identity must be accepted';

  RAISE NOTICE 'PASS 0054 dismissal anchor indexes';
END $$;
ROLLBACK;

-- The seed subscription-category map is looked up on seed rows only (household_id IS NULL);
-- a household's own category sharing the same slug text is never matched by it.
BEGIN;
DO $$
DECLARE f RECORD; seed_subscriptions UUID; household_subscriptions UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0054_fixture();
  SELECT id INTO seed_subscriptions FROM category
   WHERE slug = 'subscriptions' AND household_id IS NULL;
  ASSERT seed_subscriptions IS NOT NULL, 'the seed subscriptions category must exist';

  ASSERT (SELECT count(*) FROM recurring_subscription_category) = 8,
    'exactly the 8 seeded mapping rows must exist';
  ASSERT (SELECT is_subscription FROM recurring_subscription_category
           WHERE category_id = seed_subscriptions) = true,
    'the Subscriptions tree parent must map true';
  ASSERT (SELECT is_subscription FROM recurring_subscription_category rsc
            JOIN category c ON c.id = rsc.category_id
           WHERE c.slug = 'entertainment-streaming' AND c.household_id IS NULL) = true,
    'Streaming must map true';
  FOR seed_subscriptions IN
    SELECT c.id FROM category c
     WHERE c.household_id IS NULL
       AND c.slug IN ('housing', 'utilities', 'insurance', 'debt-service', 'financial', 'taxes')
  LOOP
    ASSERT (SELECT is_subscription FROM recurring_subscription_category
             WHERE category_id = seed_subscriptions) = false,
      'each non-subscription seed category must map false';
  END LOOP;

  -- A household's own category can reuse the slug text; the map never matches it.
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (f.h, 'Synthetic household subscriptions', 'subscriptions', 'expense', 'want')
  RETURNING id INTO household_subscriptions;
  ASSERT (SELECT count(*) FROM recurring_subscription_category
           WHERE category_id = household_subscriptions) = 0,
    'a household category must never be matched by the seed-only mapping';

  RAISE NOTICE 'PASS 0054 seed subscription-category map';
END $$;
ROLLBACK;
