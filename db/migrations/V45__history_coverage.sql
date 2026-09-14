-- Phase 2.1 Task 1: coverage_gap_acknowledgement — the owner's declared reason a month has no
-- evidence (spec "Data (V45)"). Like commitment (V43) this is an owner assumption, not an
-- evidenced fact: no SECURITY DEFINER write path, just household-scoped RLS and a composite
-- foreign key when the subject is an account. All names, identifiers and amounts below are
-- synthetic.

CREATE TYPE coverage_gap_reason AS ENUM (
  'not_issued', 'unobtainable', 'unsupported_format', 'account_not_in_use');

CREATE TABLE coverage_gap_acknowledgement (
  household_id UUID NOT NULL REFERENCES household(id),
  subject      TEXT NOT NULL CHECK (subject IN ('account', 'payslips')),
  account_id   UUID,
  month        DATE NOT NULL CHECK (month = date_trunc('month', month)::date),
  reason       coverage_gap_reason NOT NULL,
  note         TEXT CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT coverage_gap_subject_agrees CHECK (
    (subject = 'account' AND account_id IS NOT NULL)
    OR (subject = 'payslips' AND account_id IS NULL)),
  CONSTRAINT fk_coverage_gap_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_coverage_gap_month
  ON coverage_gap_acknowledgement (household_id, subject, account_id, month) NULLS NOT DISTINCT;

ALTER TABLE coverage_gap_acknowledgement ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON coverage_gap_acknowledgement FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
