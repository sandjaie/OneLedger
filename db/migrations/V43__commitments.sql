-- Phase 1.7 Task 2: commitments, the owner's one-off, monthly or annual signed amount
-- assumptions that drive the read-only forecast. A commitment is an owner assumption, not an
-- evidenced fact (spec "Commitments (V43)"): it carries no SECURITY DEFINER write path, is
-- replaced in place by an ordinary owner-scoped UPDATE, and archiving is terminal. All names
-- and amounts below are synthetic.

CREATE TYPE commitment_cadence AS ENUM ('once', 'monthly', 'annual');

CREATE TABLE commitment (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL REFERENCES household(id),
  idempotency_key UUID NOT NULL,
  account_id      UUID NOT NULL,
  label           TEXT NOT NULL,
  amount          NUMERIC(20,4) NOT NULL,
  currency        CHAR(3) NOT NULL REFERENCES currency(code),
  cadence         commitment_cadence NOT NULL,
  first_date      DATE NOT NULL,
  end_date        DATE,
  archived_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_commitment_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_commitment_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT commitment_amount_is_nonzero CHECK (amount <> 0),
  CONSTRAINT commitment_end_date_agrees CHECK (
    end_date IS NULL OR (cadence <> 'once' AND end_date >= first_date)),
  CONSTRAINT commitment_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 80 AND label = btrim(label))
);
CREATE INDEX idx_commitment_household_account ON commitment (household_id, account_id)
  WHERE archived_at IS NULL;

ALTER TABLE commitment ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON commitment FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
