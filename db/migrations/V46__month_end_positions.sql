-- Phase 2.2 Task 1: month_end_position — stored evidence for wealth history (spec "Data (V46)").
-- A recomputable cache, not history: `wealth_history.refresh` replaces a household's rows in one
-- transaction, never edits one in place. No SECURITY DEFINER write path, same convention as V43's
-- commitment and V45's coverage_gap_acknowledgement: household-scoped RLS and explicit
-- `household_id` filters are the authorization boundary, enforced by `require_owner` in
-- `routes/wealth_history.py`. All names, identifiers and amounts below are synthetic.

CREATE TYPE month_end_position_status AS ENUM (
  'available', 'missing_price', 'missing_rate', 'no_snapshot', 'before_opening');

CREATE TABLE month_end_position (
  household_id        UUID NOT NULL REFERENCES household(id),
  month_end           DATE NOT NULL,
  account_id          UUID NOT NULL,
  holding_id          UUID,
  valuation_mode      account_valuation_mode NOT NULL,
  currency            CHAR(3) NOT NULL REFERENCES currency(code),
  native_value        NUMERIC(20,4),
  evidence_date       DATE,
  rate_to_base        NUMERIC(20,10),
  rate_to_base_date   DATE,
  rate_to_base_carried BOOLEAN,
  rate_to_secondary   NUMERIC(20,10),
  rate_to_secondary_date DATE,
  rate_to_secondary_carried BOOLEAN,
  value_base          NUMERIC(20,4),
  value_secondary     NUMERIC(20,4),
  status              month_end_position_status NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_month_end_position_account FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_month_end_position_holding FOREIGN KEY (household_id, holding_id)
    REFERENCES holding (household_id, id) ON DELETE CASCADE,
  CONSTRAINT month_end_position_values_agree CHECK (
    (status = 'available' AND native_value IS NOT NULL AND value_base IS NOT NULL
      AND value_secondary IS NOT NULL)
    OR (status <> 'available' AND value_base IS NULL AND value_secondary IS NULL))
);
CREATE UNIQUE INDEX uq_month_end_position
  ON month_end_position (household_id, month_end, account_id, holding_id) NULLS NOT DISTINCT;
CREATE INDEX idx_month_end_position_month
  ON month_end_position (household_id, month_end);

ALTER TABLE month_end_position ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON month_end_position FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
