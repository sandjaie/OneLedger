-- V55: budget lines — Phase 4.1 Task 2. A budget line is an owner assumption in the household
-- base currency (spec "Budgets are owner assumptions, not postings"): it posts nothing, changes
-- no transaction, and never becomes income or spending. It is scoped to exactly one of a category
-- or a bucket for one month, so a category line and a bucket line for the same month cannot
-- silently double-count the same row.
--
-- `category` is a shared seed table (`household_id IS NULL` for the taxonomy) with no
-- `UNIQUE (household_id, id)`, so a composite tenant-scoped foreign key to it is impossible and
-- would make every seed category unbudgetable — the reference here is the plain
-- `REFERENCES category(id)` V10, V19 and V44 already use, and `routes/budgets.py` enforces the
-- tenant boundary itself (a seed row or this household's own row only), reusing
-- `routes/transactions.py::_assert_category_allowed`.
--
-- A line is freely editable and deletable — no preserve trigger, unlike a closed goal or an
-- archived register record, since a budget assumption carries no ledger side effect to protect.
-- All names, identifiers and amounts in tests and fixtures are synthetic.

CREATE TABLE budget_line (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID NOT NULL REFERENCES household(id),
  idempotency_key   UUID NOT NULL,
  period_month      DATE NOT NULL,
  category_id       UUID REFERENCES category(id),
  bucket            category_bucket,
  amount_base       NUMERIC(20,4) NOT NULL,
  currency          CHAR(3) NOT NULL REFERENCES currency(code),
  rollover_enabled  BOOLEAN NOT NULL DEFAULT false,
  note              TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_budget_line_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_budget_line_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT budget_line_scope_shape CHECK (num_nonnulls(category_id, bucket) = 1),
  CONSTRAINT budget_line_period_month_is_first_day CHECK (
    period_month = date_trunc('month', period_month)::date),
  CONSTRAINT budget_line_amount_is_positive CHECK (amount_base > 0),
  CONSTRAINT budget_line_note_is_bounded CHECK (
    note IS NULL OR char_length(note) BETWEEN 1 AND 200)
);

-- One line per household per month per category, and per bucket — the two scopes never overlap
-- (budget_line_scope_shape already forces exactly one of the two columns per row), so two partial
-- unique indexes cover both without a shared expression column.
CREATE UNIQUE INDEX uq_budget_line_month_category
  ON budget_line (household_id, period_month, category_id) WHERE category_id IS NOT NULL;
CREATE UNIQUE INDEX uq_budget_line_month_bucket
  ON budget_line (household_id, period_month, bucket) WHERE bucket IS NOT NULL;

ALTER TABLE budget_line ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON budget_line FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
