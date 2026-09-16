-- Phase 4.1 Task 3: recurring discovery, confirmation, price changes and inactive candidates
-- (spec "Discovery proposes, the owner decides"). Discovery itself reads the ledger and writes
-- nothing; only confirming a candidate writes here. `merchant_id` and `category_id` are plain
-- `REFERENCES merchant(id)` / `REFERENCES category(id)` by the plan review's non-negotiable
-- ruling (the plan review's owner overrode Task 3's own draft text, which had asked for a
-- tenant-scoped FK on `merchant_id`): both are shared seed tables with a nullable `household_id`
-- and no `UNIQUE (household_id, id)`, so a composite FK to either is impossible, the same
-- exception V10, V19, V44 and V55 already apply to `category`. The tenant boundary for both is
-- `routes/recurring.py`'s job, proven in `api/tests/test_recurring_live.py`, not here.
-- `recurring_series_member`'s FKs to `recurring_series` and `transaction` ARE composite: both
-- targets carry `uq_*_household_id` (this migration's own, and V23's `uq_transaction_household_id`),
-- so a tenant-scoped FK is possible there and is used, `ON DELETE RESTRICT` — a member row is
-- never silently orphaned. `transaction.recurring_series_id` (V10) stays the unconstrained,
-- unwritten column it has always been: a posted ledger row is immutable, so recording series
-- membership belongs in this table's own join, never in a rewrite of the row it observes.
-- All names, identifiers and amounts in tests and fixtures are synthetic.

CREATE TYPE recurring_cadence AS ENUM ('weekly', 'monthly', 'quarterly', 'annual');

CREATE TABLE recurring_series (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  idempotency_key       UUID NOT NULL,
  label                 TEXT NOT NULL,
  merchant_id           UUID REFERENCES merchant(id),
  match_key             TEXT,
  cadence               recurring_cadence NOT NULL,
  expected_amount_base  NUMERIC(20,4) NOT NULL,
  currency              CHAR(3) NOT NULL REFERENCES currency(code),
  category_id           UUID REFERENCES category(id),
  first_seen_on         DATE NOT NULL,
  last_seen_on          DATE NOT NULL,
  ended_on              DATE,
  ended_reason          TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_recurring_series_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_recurring_series_idempotency UNIQUE (household_id, idempotency_key),
  -- Exactly one of a merchant or a normalized description names a series' identity (spec): a
  -- merchant classified row is grouped by merchant, everything else by its own cleaned text.
  CONSTRAINT recurring_series_identity_shape CHECK (num_nonnulls(merchant_id, match_key) = 1),
  CONSTRAINT recurring_series_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 120 AND label = btrim(label)),
  CONSTRAINT recurring_series_amount_is_positive CHECK (expected_amount_base > 0),
  CONSTRAINT recurring_series_ended_reason_is_bounded CHECK (
    ended_reason IS NULL OR char_length(ended_reason) BETWEEN 1 AND 200),
  CONSTRAINT recurring_series_ended_shape CHECK ((ended_on IS NULL) = (ended_reason IS NULL)),
  CONSTRAINT recurring_series_dates_are_ordered CHECK (
    last_seen_on >= first_seen_on AND (ended_on IS NULL OR ended_on >= last_seen_on))
);

-- At most one open (unended) series per identity per household — a confirmed candidate cannot
-- be confirmed a second time, and an ended series frees its identity for a fresh one.
CREATE UNIQUE INDEX uq_recurring_series_open_merchant ON recurring_series (household_id, merchant_id)
  WHERE ended_on IS NULL AND merchant_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recurring_series_open_match_key ON recurring_series (household_id, match_key)
  WHERE ended_on IS NULL AND match_key IS NOT NULL;

CREATE TABLE recurring_series_member (
  household_id   UUID NOT NULL,
  series_id      UUID NOT NULL,
  transaction_id UUID NOT NULL,
  observed_on    DATE NOT NULL,
  amount_base    NUMERIC(20,4) NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, series_id, transaction_id),
  CONSTRAINT fk_recurring_series_member_series FOREIGN KEY (household_id, series_id)
    REFERENCES recurring_series (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_recurring_series_member_transaction FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT
);

-- A dismissed identity does not return as a candidate (spec); the identity itself is the natural
-- idempotency key for `POST /recurring/dismiss`; no `ended_on` scoping is needed since a
-- dismissal is not reversed by confirming — confirming is its own, separate write.
CREATE TABLE recurring_dismissal (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  merchant_id  UUID REFERENCES merchant(id),
  match_key    TEXT,
  dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT recurring_dismissal_identity_shape CHECK (num_nonnulls(merchant_id, match_key) = 1)
);

CREATE UNIQUE INDEX uq_recurring_dismissal_merchant
  ON recurring_dismissal (household_id, merchant_id) WHERE merchant_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recurring_dismissal_match_key
  ON recurring_dismissal (household_id, match_key) WHERE match_key IS NOT NULL;

-- Ending is terminal, the same shape as preserve_closed_goal (V51) and
-- preserve_archived_register_record (V53): a bare ERRCODE 23514 with a distinctive first line,
-- since routes/goals.py and routes/register.py already map their own preserve triggers by
-- matching that first line and routes/recurring.py does the same.
CREATE FUNCTION preserve_ended_recurring_series() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.ended_on IS NOT NULL THEN
    RAISE EXCEPTION 'an ended recurring series is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_preserve_ended_recurring_series
  BEFORE UPDATE ON recurring_series
  FOR EACH ROW EXECUTE FUNCTION preserve_ended_recurring_series();

REVOKE ALL ON FUNCTION preserve_ended_recurring_series() FROM PUBLIC;

ALTER TABLE recurring_series ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON recurring_series FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

ALTER TABLE recurring_series_member ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON recurring_series_member FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

ALTER TABLE recurring_dismissal ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON recurring_dismissal FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
