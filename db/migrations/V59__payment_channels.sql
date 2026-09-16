-- db/migrations/V59__payment_channels.sql
-- Phase 4.1 Task 6: payment channels (spec "a payment channel such as an app store, a wallet
-- autopay or a direct card"). A shared seed table with a nullable household_id, the same shape
-- category (V9) and merchant (V9) already use — RLS policies copied from category's. No
-- institution, store or wallet name is ever a code identifier (ADR-0012): the seed rows name the
-- channel kind only, never a brand.
-- All names in this migration are generic channel kinds, never real institution or product names.

CREATE TABLE payment_channel (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID REFERENCES household(id),      -- NULL = system seed
  name         TEXT NOT NULL,
  sort_order   SMALLINT NOT NULL DEFAULT 0,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT payment_channel_name_is_bounded CHECK (
    char_length(name) BETWEEN 1 AND 60 AND name = btrim(name))
);

ALTER TABLE payment_channel ENABLE ROW LEVEL SECURITY;
CREATE POLICY payment_channel_read ON payment_channel FOR SELECT
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY payment_channel_write ON payment_channel FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

INSERT INTO payment_channel (household_id, name, sort_order) VALUES
  (NULL, 'Direct card',    10),
  (NULL, 'App store',      20),
  (NULL, 'Wallet autopay', 30),
  (NULL, 'Bank autopay',   40),
  (NULL, 'Bank debit',     50),
  (NULL, 'Other',          60);

-- A shared seed table with no UNIQUE (household_id, id) (the category/merchant exception, global
-- execution contract): a plain REFERENCES, the tenant boundary enforced in routes/recurring.py.
ALTER TABLE recurring_series
  ADD COLUMN payment_channel_id UUID REFERENCES payment_channel(id);
