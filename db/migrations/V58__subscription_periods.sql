-- Phase 4.1 Task 5: subscription billing periods (spec "A subscription has billing periods").
-- A subscription is one record for its whole life; cancelling and resuming toggle its billing
-- periods, never the series itself, so an owner's name and "not a subscription" choice carry
-- across a cancel/resume by construction. `recurring_series.ended_on` now means only "removed as
-- not recurring" (the final `POST /recurring/{id}/remove` action, V56): cancellation and lapsing
-- are the state of a billing period, never of the series.
-- All names, identifiers and amounts in this migration are synthetic or seed data.

CREATE TABLE recurring_series_period (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL,
  series_id    UUID NOT NULL,
  started_on   DATE NOT NULL,
  ended_on     DATE,
  end_reason   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_recurring_series_period_household_id UNIQUE (household_id, id),
  CONSTRAINT fk_recurring_series_period_series FOREIGN KEY (household_id, series_id)
    REFERENCES recurring_series (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT recurring_series_period_end_reason_is_known CHECK (
    end_reason IS NULL OR end_reason IN ('cancelled', 'lapsed')),
  CONSTRAINT recurring_series_period_end_shape CHECK ((ended_on IS NULL) = (end_reason IS NULL)),
  CONSTRAINT recurring_series_period_dates_are_ordered CHECK (
    ended_on IS NULL OR ended_on >= started_on)
);

-- At most one open period per series — a subscription is current when its latest period is open,
-- and never has two (spec).
CREATE UNIQUE INDEX uq_recurring_series_period_open ON recurring_series_period (household_id, series_id)
  WHERE ended_on IS NULL;

ALTER TABLE recurring_series_period ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON recurring_series_period FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- The owner's status and a custom cadence (spec "what the owner can set"). V57 added and
-- committed 'custom' in its own migration by design (Postgres refuses a value used in the same
-- transaction that adds it); this migration is free to use it.
ALTER TABLE recurring_series
  ADD COLUMN lifecycle             TEXT NOT NULL DEFAULT 'active',
  ADD COLUMN custom_interval_days  SMALLINT,
  ADD CONSTRAINT recurring_series_lifecycle_is_known CHECK (
    lifecycle IN ('active', 'trial', 'paused')),
  ADD CONSTRAINT recurring_series_custom_interval_is_bounded CHECK (
    custom_interval_days IS NULL OR custom_interval_days BETWEEN 7 AND 730),
  ADD CONSTRAINT recurring_series_custom_interval_shape CHECK (
    (cadence = 'custom') = (custom_interval_days IS NOT NULL));

-- Backfill. Every still-open series (never ended) gets one open period starting at its first
-- charge — it was always "current" and stays so.
INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on, end_reason)
SELECT household_id, id, first_seen_on, NULL, NULL
  FROM recurring_series
 WHERE ended_on IS NULL;

-- Every series ended with a reason other than 'not_recurring' — V56's own former free-text `/end`,
-- the only way a series could have ended before V57 replaced it with the dedicated `/remove`
-- action — was in truth a cancellation of one billing period, not the series' own removal: it
-- gets one period covering its whole recorded life, closed 'cancelled', and the series itself is
-- reopened (its `ended_on`/`ended_reason` cleared) so it reads as a past, not a removed,
-- subscription. `trg_preserve_ended_recurring_series` refuses any UPDATE to an already-ended row,
-- so it is disabled for this one statement and re-enabled immediately after. A series removed as
-- 'not_recurring' is untouched by both statements below and gets no period at all — V58's own
-- meaning for `ended_on` going forward.
ALTER TABLE recurring_series DISABLE TRIGGER trg_preserve_ended_recurring_series;

INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on, end_reason)
SELECT household_id, id, first_seen_on, ended_on, 'cancelled'
  FROM recurring_series
 WHERE ended_on IS NOT NULL AND ended_reason <> 'not_recurring';

UPDATE recurring_series
   SET ended_on = NULL, ended_reason = NULL, updated_at = now()
 WHERE ended_on IS NOT NULL AND ended_reason <> 'not_recurring';

ALTER TABLE recurring_series ENABLE TRIGGER trg_preserve_ended_recurring_series;
