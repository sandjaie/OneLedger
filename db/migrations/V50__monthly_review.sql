-- Phase 3.2 Task 2: the owner's record that a calendar month's review is complete.
-- `routes/monthly_review.py` computes every step (coverage gaps, transfer candidates,
-- unclassified postings, month-end position, upcoming commitments, open actions) on read from
-- existing tables and views; this migration adds only the one fact nothing else already holds —
-- that the owner marked the month reviewed, and when. No SECURITY DEFINER write path: the same
-- plain owner-scoped convention as `commitment` (V43) and `action_item`/`action_mark` (V49). All
-- names, identifiers and amounts in tests and fixtures are synthetic.

CREATE TABLE monthly_review (
  household_id UUID NOT NULL REFERENCES household(id),
  review_month DATE NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, review_month),
  CONSTRAINT monthly_review_month_is_first_day CHECK (
    review_month = date_trunc('month', review_month)::date)
);

ALTER TABLE monthly_review ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON monthly_review FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
