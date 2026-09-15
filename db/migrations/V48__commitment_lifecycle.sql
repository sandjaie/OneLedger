-- Phase 3.1 Task 1: a commitment's lifecycle over exactly one open occurrence, its next due
-- date, and settlement by linking the ledger posting that resolves it. `commitment_resolution`
-- is the one write path for a payment link, a mark-done or a dismissal; every row is immutable
-- once inserted (only UPDATE is refused — DELETE is the undo path, gated by the route, not the
-- database) and closing an occurrence advances `commitment.next_due_date` in the same
-- transaction as the resolution that closed it. All names, identifiers and amounts in tests and
-- fixtures are synthetic.

CREATE TYPE commitment_certainty AS ENUM ('confirmed', 'estimated');
CREATE TYPE commitment_resolution_kind AS ENUM ('payment', 'completed', 'dismissed');

ALTER TABLE commitment
  ADD COLUMN certainty commitment_certainty NOT NULL DEFAULT 'confirmed',
  ADD COLUMN next_due_date DATE,
  ADD CONSTRAINT uq_commitment_household_id UNIQUE (household_id, id),
  ADD CONSTRAINT commitment_next_due_in_schedule CHECK (
    next_due_date IS NULL OR (next_due_date >= first_date
                              AND (end_date IS NULL OR next_due_date <= end_date)));

CREATE TABLE commitment_resolution (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  commitment_id  UUID NOT NULL,
  due_date       DATE NOT NULL,
  kind           commitment_resolution_kind NOT NULL,
  closes         BOOLEAN NOT NULL,
  transaction_id UUID,
  reason         TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_commitment_resolution_commitment_scope FOREIGN KEY (household_id, commitment_id)
    REFERENCES commitment (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_commitment_resolution_transaction_scope FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT commitment_resolution_shape CHECK (
       (kind = 'payment'   AND transaction_id IS NOT NULL AND reason IS NULL)
    OR (kind = 'completed' AND transaction_id IS NULL AND closes AND reason IS NULL)
    OR (kind = 'dismissed' AND transaction_id IS NULL AND closes AND reason IS NOT NULL
        AND char_length(reason) BETWEEN 1 AND 200 AND reason = btrim(reason)))
);
CREATE UNIQUE INDEX uq_commitment_resolution_closing
  ON commitment_resolution (commitment_id, due_date) WHERE closes;
CREATE UNIQUE INDEX uq_commitment_resolution_transaction
  ON commitment_resolution (transaction_id) WHERE transaction_id IS NOT NULL;
CREATE INDEX idx_commitment_resolution_commitment
  ON commitment_resolution (household_id, commitment_id, due_date DESC);

-- Backfill: every existing commitment gets `next_due_date` by the default next-due rule (spec
-- "Occurrence rules"), evaluated at this migration's `current_date` — a `once` commitment always
-- resolves to `first_date`; a monthly or annual commitment resolves to the first occurrence
-- strictly after its account's `current_balance_as_of::date` when that date is before today (so
-- an occurrence the forecast already treats as unposted stays open), else the first occurrence on
-- or after today, or NULL when none remains before `end_date`. Occurrence `n` is
-- `(first_date + make_interval(months => n * step))::date`, PostgreSQL's own month-end clamp,
-- which equals `forecast._add_months(first_date, n * step)`. The bound of 2400 steps (200 years
-- monthly, 2400 years annual) comfortably covers any realistic first date; this is a one-time
-- backfill over a personal-scale number of rows.
UPDATE commitment c
   SET next_due_date = picked.next_due_date
  FROM (
    SELECT DISTINCT ON (c2.id) c2.id, gen.candidate AS next_due_date
      FROM commitment c2
      JOIN account a ON a.id = c2.account_id
      CROSS JOIN LATERAL (
        SELECT CASE
                 WHEN a.current_balance_as_of IS NOT NULL
                      AND a.current_balance_as_of::date < current_date
                 THEN a.current_balance_as_of::date
                 ELSE current_date
               END AS reference,
               (a.current_balance_as_of IS NOT NULL
                AND a.current_balance_as_of::date < current_date) AS strict
      ) ref
      CROSS JOIN LATERAL generate_series(0, 2400) AS n
      CROSS JOIN LATERAL (
        SELECT (c2.first_date + make_interval(
                  months => n * (CASE c2.cadence WHEN 'annual' THEN 12 ELSE 1 END)))::date
               AS candidate
      ) gen
     WHERE c2.cadence <> 'once'
       AND (CASE WHEN ref.strict THEN gen.candidate > ref.reference
                 ELSE gen.candidate >= ref.reference END)
       AND (c2.end_date IS NULL OR gen.candidate <= c2.end_date)
     ORDER BY c2.id, gen.candidate
  ) picked
 WHERE c.id = picked.id;

UPDATE commitment SET next_due_date = first_date WHERE cadence = 'once';

-- Eligibility for a payment resolution (spec "Commitment lifecycle (V48)"): the linked posting
-- must be a leaf row of `v_transaction_posting` in the same household, not excluded, not an
-- opening balance or adjustment, on the commitment's account and in its currency, signed the same
-- way as the commitment's amount, and not one leg of a payslip deduction pair (those rows are
-- deleted on unlink, which this resolution's own RESTRICT key would otherwise block). A transfer
-- leg is eligible: a card payment or planned transfer is a real cash movement on the paying
-- account. `routes/commitment_resolutions.py` mirrors this predicate for its candidates read —
-- keep both in sync, each names the other.
CREATE FUNCTION assert_commitment_payment() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE
  target commitment%ROWTYPE;
BEGIN
  SELECT * INTO target FROM commitment
   WHERE household_id = NEW.household_id AND id = NEW.commitment_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'this posting cannot settle this commitment'
      USING ERRCODE = '23514', CONSTRAINT = 'commitment_payment_is_eligible';
  END IF;
  IF NOT EXISTS (
    SELECT FROM v_transaction_posting t
     WHERE t.household_id = NEW.household_id AND t.id = NEW.transaction_id
       AND NOT t.is_excluded
       AND t.txn_type NOT IN ('opening_balance', 'adjustment')
       AND t.account_id = target.account_id
       AND rtrim(t.currency) = rtrim(target.currency)
       AND sign(t.amount) = sign(target.amount)
       AND NOT EXISTS (
         SELECT FROM payslip_line_posting p
          WHERE p.household_id = t.household_id
            AND (p.income_transaction_id = t.id OR p.expense_transaction_id = t.id))
  ) THEN
    RAISE EXCEPTION 'this posting cannot settle this commitment'
      USING ERRCODE = '23514', CONSTRAINT = 'commitment_payment_is_eligible';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_assert_commitment_payment
  BEFORE INSERT ON commitment_resolution
  FOR EACH ROW WHEN (NEW.kind = 'payment') EXECUTE FUNCTION assert_commitment_payment();

REVOKE ALL ON FUNCTION assert_commitment_payment() FROM PUBLIC;

-- Immutable once inserted: every field of a resolution is fixed history. DELETE is the undo
-- path — the database does not restrict it beyond the ordinary RESTRICT foreign keys above;
-- `routes/commitment_resolutions.py`'s undo rule is what limits which row and when.
CREATE FUNCTION preserve_commitment_resolution() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'a commitment resolution is immutable' USING ERRCODE = '23514';
END $$;

CREATE TRIGGER trg_preserve_commitment_resolution
  BEFORE UPDATE ON commitment_resolution
  FOR EACH ROW EXECUTE FUNCTION preserve_commitment_resolution();

REVOKE ALL ON FUNCTION preserve_commitment_resolution() FROM PUBLIC;

ALTER TABLE commitment_resolution ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON commitment_resolution FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
