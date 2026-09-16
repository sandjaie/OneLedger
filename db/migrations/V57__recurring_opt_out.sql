-- Phase 4.1 Task 1: recurring discovery becomes opt-out (spec "Recurring payments are simply
-- just not a subscription"). `POST /recurring/refresh` now creates series on its own; the owner's
-- one correction is the binary subscription flag, never a bill/rent/loan taxonomy. V56's
-- confirm/accept-price/end routes are removed in `routes/recurring.py`, not here.
--
-- The enum values below are added and used in separate migrations by design: Postgres refuses a
-- value used in the same transaction that adds it, and this file references neither 'semiannual'
-- nor 'custom' anywhere else.
-- All names, identifiers and amounts in this migration and its seed are synthetic or seed data.

ALTER TYPE recurring_cadence ADD VALUE 'semiannual';
ALTER TYPE recurring_cadence ADD VALUE 'custom';

-- The subscription flag and its provenance (spec "the owner's decision"). `detection_method`
-- distinguishes a series `POST /recurring/refresh` found from one Task 2's `/recurring/from-
-- transaction` creates directly; `native_amount`/`native_currency` are the latest charge's own
-- original pair (spec "original currency is kept"), separate from the existing `expected_amount_
-- base`/`currency` columns, which stay the household base-currency figures V56 already keeps in
-- sync from the latest member.
ALTER TABLE recurring_series
  ADD COLUMN is_subscription           BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN subscription_set_by_owner BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN detection_method          TEXT NOT NULL DEFAULT 'detected',
  ADD COLUMN named_by_owner            BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN native_amount             NUMERIC(20,4),
  ADD COLUMN native_currency           CHAR(3) REFERENCES currency(code),
  ADD CONSTRAINT recurring_series_detection_method_is_known CHECK (
    detection_method IN ('detected', 'owner')),
  ADD CONSTRAINT recurring_series_native_pair CHECK (
    (native_amount IS NULL) = (native_currency IS NULL)),
  ADD CONSTRAINT recurring_series_native_amount_is_positive CHECK (
    native_amount IS NULL OR native_amount > 0);

-- A charge belongs to at most one series (spec, global execution contract). Existing data can
-- already hold a transaction in two member rows: V56's confirm → end → confirm re-inserted the
-- same charges into the fresh series once the identity's earlier series ended. Keep, for each
-- transaction, the member row on the newest series — open before ended, then the later
-- `created_at`, then the higher `id` — and delete every other row naming that transaction.
DO $$
DECLARE deleted_count INTEGER;
BEGIN
  WITH ranked AS (
    SELECT m.household_id, m.series_id, m.transaction_id,
           row_number() OVER (
             PARTITION BY m.household_id, m.transaction_id
             ORDER BY (s.ended_on IS NULL) DESC, s.created_at DESC, s.id DESC
           ) AS rnk
      FROM recurring_series_member m
      JOIN recurring_series s ON s.household_id = m.household_id AND s.id = m.series_id
  ),
  doomed AS (SELECT household_id, series_id, transaction_id FROM ranked WHERE rnk > 1)
  DELETE FROM recurring_series_member m
   USING doomed d
   WHERE m.household_id = d.household_id AND m.series_id = d.series_id
     AND m.transaction_id = d.transaction_id;
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RAISE NOTICE 'V57: removed % duplicate recurring_series_member row(s) before uq_recurring_series_member_transaction', deleted_count;
END $$;

ALTER TABLE recurring_series_member
  ADD CONSTRAINT uq_recurring_series_member_transaction UNIQUE (household_id, transaction_id);

-- One identity may now carry several open series (spec "one descriptor, several subscriptions":
-- two amount clusters whose date ranges overlap are two subscriptions charged at once).
DROP INDEX uq_recurring_series_open_merchant;
DROP INDEX uq_recurring_series_open_match_key;

-- A dismissal now anchors on an amount too, so removing one subscription on a shared descriptor
-- does not suppress the others (spec). A null anchor keeps V56's meaning: the whole identity.
ALTER TABLE recurring_dismissal
  ADD COLUMN anchor_amount   NUMERIC(20,4),
  ADD COLUMN anchor_currency CHAR(3) REFERENCES currency(code),
  ADD CONSTRAINT recurring_dismissal_anchor_pair CHECK (
    (anchor_amount IS NULL) = (anchor_currency IS NULL));

DROP INDEX uq_recurring_dismissal_merchant;
DROP INDEX uq_recurring_dismissal_match_key;
CREATE UNIQUE INDEX uq_recurring_dismissal_merchant
  ON recurring_dismissal (household_id, merchant_id, anchor_amount, anchor_currency)
  NULLS NOT DISTINCT WHERE merchant_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recurring_dismissal_match_key
  ON recurring_dismissal (household_id, match_key, anchor_amount, anchor_currency)
  NULLS NOT DISTINCT WHERE match_key IS NOT NULL;

-- The deterministic default for the subscription flag (spec: no AI before Phase 5, ADR-0003). A
-- seed row's `slug` is unique only among seed rows (V9's `category_system_slug`), so every lookup
-- below is scoped `household_id IS NULL`; a child category with no row of its own falls back to
-- its parent's row in `routes/recurring.py`. RLS mirrors `currency`'s own read-only policy (V4):
-- no write policy exists, so only a migration (running as the table owner) can change the map.
CREATE TABLE recurring_subscription_category (
  category_id     UUID PRIMARY KEY REFERENCES category(id),
  is_subscription BOOLEAN NOT NULL
);

ALTER TABLE recurring_subscription_category ENABLE ROW LEVEL SECURITY;
CREATE POLICY reference_read ON recurring_subscription_category FOR SELECT TO authenticated
  USING (true);

INSERT INTO recurring_subscription_category (category_id, is_subscription)
SELECT c.id, v.is_subscription
  FROM (VALUES
    ('subscriptions',           true),
    ('entertainment-streaming', true),
    ('housing',                 false),
    ('utilities',               false),
    ('insurance',               false),
    ('debt-service',            false),
    ('financial',               false),
    ('taxes',                   false)
  ) AS v(slug, is_subscription)
  JOIN category c ON c.slug = v.slug AND c.household_id IS NULL;
