-- Phase 3.4 Task 2: a commitment may be planned from a register record — a policy's premium, a
-- deposit's expected proceeds or a claim's expected reimbursement — the same one-source shape V49
-- gave a card statement, extended to registers: at most one source per commitment
-- (`commitment_has_one_source`) and at most one open (unarchived) commitment per record
-- (`uq_commitment_open_register_record`, the same pattern as `uq_commitment_open_card_statement`).
-- `routes/plans.py` carries every kind-specific schedule, sign, account and currency rule — there
-- is no fixed CHECK for them here, since they vary by the source record's kind.
--
-- `CREATE OR REPLACE FUNCTION assert_commitment_payment()` keeps every V48 condition unchanged and
-- adds one: when the commitment's source record is a `claim` or `deposit`, the settling posting's
-- `txn_type` must not be `income` — a reimbursement or returned principal is never counted as
-- newly earned income (spec "Register-sourced commitments (V54)"). CREATE OR REPLACE preserves the
-- function's existing ownership and REVOKE ALL, so neither is repeated here.
-- `routes/commitment_resolutions.py`'s own candidates predicate mirrors this addition, keeping its
-- sync comment — keep both in sync. All names, identifiers and amounts in tests and fixtures are
-- synthetic.

ALTER TABLE commitment
  ADD COLUMN source_register_record_id UUID,
  ADD CONSTRAINT fk_commitment_register_scope FOREIGN KEY (household_id, source_register_record_id)
    REFERENCES register_record (household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT commitment_has_one_source CHECK (
    num_nonnulls(source_card_statement_id, source_register_record_id) <= 1);

CREATE UNIQUE INDEX uq_commitment_open_register_record ON commitment (source_register_record_id)
  WHERE source_register_record_id IS NOT NULL AND archived_at IS NULL;

CREATE OR REPLACE FUNCTION assert_commitment_payment() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE
  target commitment%ROWTYPE;
  source_kind register_kind;
BEGIN
  SELECT * INTO target FROM commitment
   WHERE household_id = NEW.household_id AND id = NEW.commitment_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'this posting cannot settle this commitment'
      USING ERRCODE = '23514', CONSTRAINT = 'commitment_payment_is_eligible';
  END IF;
  IF target.source_register_record_id IS NOT NULL THEN
    SELECT kind INTO source_kind FROM register_record
     WHERE household_id = target.household_id AND id = target.source_register_record_id;
  END IF;
  IF NOT EXISTS (
    SELECT FROM v_transaction_posting t
     WHERE t.household_id = NEW.household_id AND t.id = NEW.transaction_id
       AND NOT t.is_excluded
       AND t.txn_type NOT IN ('opening_balance', 'adjustment')
       AND (source_kind IS NULL OR source_kind NOT IN ('claim', 'deposit')
            OR t.txn_type <> 'income')
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
