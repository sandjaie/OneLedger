-- V60: earning rules for salary analysis (Phase 4.2 Task 1). The owner's component choice for an
-- earning label always wins over the read-time inference `salary_analysis.py` computes; removing
-- the rule returns the label to inference. Plain household-scoped table written directly by
-- `routes/salary.py`, no `SECURITY DEFINER` write path — the same convention V44's
-- `payslip_deduction_rule` uses.

CREATE TABLE payslip_earning_rule (
  household_id UUID NOT NULL REFERENCES household(id),
  label        TEXT NOT NULL,
  component    TEXT NOT NULL CHECK (component IN ('fixed', 'variable', 'reimbursement')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, label),
  -- The same bound as `payslip_line.label` (V42), so a rule can name exactly the stored labels.
  CONSTRAINT payslip_earning_rule_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 80 AND label = btrim(label))
);

ALTER TABLE payslip_earning_rule ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON payslip_earning_rule FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
