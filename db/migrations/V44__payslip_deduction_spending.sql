-- Phase 1.7 Task 3: a deduction the owner marks as spending becomes a balanced income/expense
-- pair on the linked credit's account and booked date (spec "Deductions counted as spending").
-- Both tables are plain owner-scoped tables written directly by `routes/payslips.py`, the same
-- convention V43's `commitment` table uses: no SECURITY DEFINER write path, RLS as defence in
-- depth, household id filtered explicitly by every writer.

-- A composite unique needed only from here on, to let payslip_line_posting reference one line
-- scoped to its household, the same pattern V42 already uses for payslip itself.
ALTER TABLE payslip_line ADD CONSTRAINT uq_payslip_line_household_id UNIQUE (household_id, id);

CREATE TABLE payslip_deduction_rule (
  household_id UUID NOT NULL REFERENCES household(id),
  label        TEXT NOT NULL,
  category_id  UUID NOT NULL REFERENCES category(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, label)
);

CREATE TABLE payslip_line_posting (
  household_id           UUID NOT NULL REFERENCES household(id),
  payslip_line_id        UUID NOT NULL,
  income_transaction_id   UUID NOT NULL,
  expense_transaction_id  UUID NOT NULL,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, payslip_line_id),
  CONSTRAINT fk_payslip_line_posting_line_scope FOREIGN KEY (household_id, payslip_line_id)
    REFERENCES payslip_line (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_line_posting_income_scope FOREIGN KEY (household_id, income_transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_line_posting_expense_scope
    FOREIGN KEY (household_id, expense_transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT
);

-- The FK above scopes the reference to one household; a CHECK cannot reach another table, so
-- "and a deduction line" (spec, V44 "Data") is this trigger's job alone.
CREATE FUNCTION assert_payslip_line_posting_is_deduction() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT FROM payslip_line l
     WHERE l.household_id = NEW.household_id AND l.id = NEW.payslip_line_id
       AND l.kind = 'deduction'
  ) THEN
    RAISE EXCEPTION 'a spending posting must reference a deduction line'
      USING ERRCODE = '23514', CONSTRAINT = 'payslip_line_posting_is_deduction';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_payslip_line_posting_is_deduction
  BEFORE INSERT ON payslip_line_posting
  FOR EACH ROW EXECUTE FUNCTION assert_payslip_line_posting_is_deduction();

REVOKE ALL ON FUNCTION assert_payslip_line_posting_is_deduction() FROM PUBLIC;

ALTER TABLE payslip_deduction_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE payslip_line_posting ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON payslip_deduction_rule FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON payslip_line_posting FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
