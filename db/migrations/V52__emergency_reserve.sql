-- Phase 3.3 Task 2: the emergency reserve — a target in months of essential spending, an explicit
-- baseline (chosen category buckets over a reference period, or a manual monthly amount), and
-- which asset accounts the owner marks eligible to cover it. `emergency.py` computes the baseline
-- and coverage in pure Decimal from rows `routes/emergency_reserve.py` reads; neither table has a
-- SECURITY DEFINER write path, the same convention as `emergency_reserve`'s Task 1 siblings `goal`
-- and `resource_reservation` (V51). The action key shape widens to admit `reserve.<householdId>`
-- (this task) and Phase 3.4's `record.` keys, so that phase needs no further constraint change.
-- All names, identifiers and amounts in tests and fixtures are synthetic.

CREATE TYPE baseline_method AS ENUM ('reference_period', 'manual');

CREATE TABLE emergency_reserve (
  household_id         UUID PRIMARY KEY REFERENCES household(id),
  target_months        NUMERIC(4,1) NOT NULL,
  baseline_method       baseline_method NOT NULL,
  reference_months      SMALLINT NOT NULL DEFAULT 6,
  buckets               category_bucket[] NOT NULL DEFAULT '{need,debt_service}',
  manual_monthly_base   NUMERIC(20,4),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT emergency_target_is_bounded CHECK (target_months > 0 AND target_months <= 60),
  CONSTRAINT emergency_reference_is_bounded CHECK (reference_months BETWEEN 1 AND 24),
  CONSTRAINT emergency_buckets_are_spending CHECK (
    cardinality(buckets) >= 1
    AND buckets <@ '{need,want,investment,debt_service}'::category_bucket[]),
  CONSTRAINT emergency_manual_agrees CHECK (
    (baseline_method = 'manual') = (manual_monthly_base IS NOT NULL)
    AND (manual_monthly_base IS NULL OR manual_monthly_base > 0))
);

CREATE TABLE emergency_resource_account (
  household_id     UUID NOT NULL REFERENCES household(id),
  account_id       UUID NOT NULL,
  access_days      SMALLINT NOT NULL DEFAULT 0,
  restriction_note TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, account_id),
  CONSTRAINT fk_emergency_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT emergency_access_is_bounded CHECK (access_days BETWEEN 0 AND 365),
  CONSTRAINT emergency_note_is_bounded CHECK (restriction_note IS NULL OR (
    char_length(restriction_note) BETWEEN 1 AND 200 AND restriction_note = btrim(restriction_note)))
);

ALTER TABLE emergency_reserve ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON emergency_reserve FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

ALTER TABLE emergency_resource_account ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON emergency_resource_account FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- Widen the key shape (V49) to admit reserve.<householdId> (this task) and Phase 3.4's
-- record.<id>.review / record.<id>.(renewal|maturity|deadline).<date> so that phase needs no
-- further constraint change; action_mark_dismissal_is_card is unchanged (a snooze only, on both
-- new kinds — routes/actions.py never accepts a dismissal on either).
ALTER TABLE action_mark DROP CONSTRAINT action_mark_key_shape;
ALTER TABLE action_mark ADD CONSTRAINT action_mark_key_shape CHECK (action_key ~ (
  '^((follow-up|card|goal|reserve)\.[0-9a-f-]{36}'
  || '|commitment\.[0-9a-f-]{36}\.[0-9]{4}-[0-9]{2}-[0-9]{2}'
  || '|record\.[0-9a-f-]{36}\.(review|(renewal|maturity|deadline)\.[0-9]{4}-[0-9]{2}-[0-9]{2}))$'));
