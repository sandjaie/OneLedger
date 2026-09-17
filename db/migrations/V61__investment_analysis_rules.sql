-- V61: allocation targets and benchmarks for investment analysis (Phase 4.3 Task 1). Both tables
-- follow V60's `payslip_earning_rule` convention exactly: plain household-scoped tables, no
-- `SECURITY DEFINER` write path, written directly by `routes/portfolio.py` under the household
-- lock. `api/oneledger/investment_analysis.py` is the pure reader: the owner's stored rows always
-- win over its own read-time inference, and removing them returns to inference (allocation
-- target) or to "No benchmark" (benchmark — there is no inferred benchmark at all).

-- The owner's target allocation by asset class. The application refuses a PUT whose ratios do
-- not sum to exactly 1 at this NUMERIC(20,10) scale, and refuses a ratio with more than ten
-- decimal places rather than rounding — both are read-time-cheap checks the database CHECK below
-- only partially expresses (a per-row bound, not the whole-set sum, which needs a transaction to
-- see every row the PUT is replacing).
CREATE TABLE investment_allocation_target (
  household_id UUID NOT NULL REFERENCES household(id),
  label        asset_class NOT NULL,
  ratio        NUMERIC(20,10) NOT NULL CHECK (ratio >= 0 AND ratio <= 1),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, label)
);

ALTER TABLE investment_allocation_target ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON investment_allocation_target FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- The owner's one security per portfolio and, optionally, one per asset class. There is no
-- inferred benchmark (spec "Benchmarks"): a missing assignment reads as "No benchmark", never a
-- zero-return proxy. `UNIQUE NULLS NOT DISTINCT` follows V40/V45/V46's own convention for a
-- nullable uniqueness column — here `asset_class`, NULL exactly once per household for the
-- portfolio-scope row.
CREATE TABLE investment_benchmark (
  household_id UUID NOT NULL REFERENCES household(id),
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_kind   TEXT NOT NULL CHECK (scope_kind IN ('portfolio', 'asset_class')),
  asset_class  asset_class,
  security_id  UUID NOT NULL REFERENCES security(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT investment_benchmark_scope_matches_asset_class CHECK (
    (scope_kind = 'portfolio' AND asset_class IS NULL)
    OR (scope_kind = 'asset_class' AND asset_class IS NOT NULL)),
  CONSTRAINT uq_investment_benchmark_scope
    UNIQUE NULLS NOT DISTINCT (household_id, scope_kind, asset_class)
);

ALTER TABLE investment_benchmark ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON investment_benchmark FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
