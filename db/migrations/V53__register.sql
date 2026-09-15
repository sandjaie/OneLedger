-- Phase 3.4 Task 1: the register — insurance policies, deposits, claims, nominee records and
-- administration deadlines, plus the (Task 2) document links table. A register record is an owner
-- assumption like `commitment` and `goal`: no SECURITY DEFINER write path, plain household-scoped
-- RLS as defence in depth (the API connects as table owner, so `routes/register.py` locks the
-- household row and filters explicitly, the same convention as V51/V52). Archiving is terminal —
-- `preserve_archived_register_record()` refuses any further UPDATE and every DELETE, the same
-- shape as `preserve_closed_goal()` (V51) without a one-time exception, since a register record has
-- no closing write that itself needs to pass. `register_document` is created here so V54 can add
-- its foreign key without a further migration; its routes, links and upload are Task 2 — nothing in
-- this migration or Task 1's API populates it. All names, identifiers and amounts in tests and
-- fixtures are synthetic.

CREATE TYPE register_kind AS ENUM (
  'insurance_policy', 'deposit', 'claim', 'nominee', 'administration');

CREATE TABLE register_record (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID NOT NULL REFERENCES household(id),
  idempotency_key   UUID NOT NULL,
  kind              register_kind NOT NULL,
  title             TEXT NOT NULL,
  counterparty      TEXT,
  covered           TEXT,
  amount            NUMERIC(20,4),
  currency          CHAR(3) REFERENCES currency(code),
  premium_amount    NUMERIC(20,4),
  premium_cadence   commitment_cadence,
  key_date          DATE,
  account_id        UUID,
  instruction       TEXT,
  nominee_recorded  BOOLEAN,
  last_reviewed_on  DATE,
  completed_at      TIMESTAMPTZ,
  archived_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_register_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_register_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_register_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT register_texts_are_bounded CHECK (
    char_length(title) BETWEEN 1 AND 120 AND title = btrim(title)
    AND (counterparty IS NULL OR char_length(counterparty) BETWEEN 1 AND 120)
    AND (covered IS NULL OR char_length(covered) BETWEEN 1 AND 120)
    AND (instruction IS NULL OR char_length(instruction) BETWEEN 1 AND 200)),
  CONSTRAINT register_amounts_are_valid CHECK (
    (amount IS NULL OR amount > 0) AND (premium_amount IS NULL OR premium_amount > 0)
    AND ((amount IS NULL AND premium_amount IS NULL) OR currency IS NOT NULL)
    AND ((premium_amount IS NULL) = (premium_cadence IS NULL))
    AND (premium_cadence IS NULL OR premium_cadence <> 'once')),
  CONSTRAINT register_kind_shape CHECK (
       (kind = 'insurance_policy' AND key_date IS NOT NULL AND account_id IS NULL
        AND instruction IS NULL AND nominee_recorded IS NULL AND completed_at IS NULL)
    OR (kind = 'deposit' AND key_date IS NOT NULL AND account_id IS NOT NULL
        AND covered IS NULL AND premium_amount IS NULL AND nominee_recorded IS NULL
        AND completed_at IS NULL)
    OR (kind = 'claim' AND key_date IS NOT NULL AND counterparty IS NOT NULL
        AND amount IS NOT NULL AND covered IS NULL AND premium_amount IS NULL
        AND account_id IS NULL AND instruction IS NULL AND nominee_recorded IS NULL)
    OR (kind = 'nominee' AND account_id IS NOT NULL AND nominee_recorded IS NOT NULL
        AND amount IS NULL AND premium_amount IS NULL AND covered IS NULL
        AND instruction IS NULL AND key_date IS NULL AND completed_at IS NULL)
    OR (kind = 'administration' AND key_date IS NOT NULL AND amount IS NULL
        AND premium_amount IS NULL AND covered IS NULL AND account_id IS NULL
        AND instruction IS NULL AND nominee_recorded IS NULL))
);

CREATE TABLE register_document (
  household_id UUID NOT NULL REFERENCES household(id),
  record_id    UUID NOT NULL,
  document_id  UUID NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, record_id, document_id),
  CONSTRAINT fk_register_document_record FOREIGN KEY (household_id, record_id)
    REFERENCES register_record (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_register_document_document FOREIGN KEY (household_id, document_id)
    REFERENCES document (household_id, id) ON DELETE RESTRICT
);

-- Archiving is terminal: every UPDATE on an already-archived row is refused, and DELETE is always
-- refused. The route enforces every other rule (kind shape, account validity) before insert or
-- update; this trigger is the backstop that an archived record cannot be quietly edited.
CREATE FUNCTION preserve_archived_register_record() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'a register record cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF OLD.archived_at IS NOT NULL THEN
    RAISE EXCEPTION 'an archived register record is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_preserve_archived_register_record
  BEFORE UPDATE OR DELETE ON register_record
  FOR EACH ROW EXECUTE FUNCTION preserve_archived_register_record();

REVOKE ALL ON FUNCTION preserve_archived_register_record() FROM PUBLIC;

ALTER TABLE register_record ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON register_record FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

ALTER TABLE register_document ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON register_document FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
