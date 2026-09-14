-- Phase 1.7 Task 1: payslips, entered or imported, immutable except their deposit link.
-- record_payslip is the one write path for entry, correction and import (spec section
-- "Data model contract"); payslip_totals_agree defers its own check per call so several
-- payslips recorded in one transaction (an import's savepoints) are each checked complete.

CREATE TYPE payslip_line_kind AS ENUM ('earning', 'deduction');

CREATE TABLE payslip (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  idempotency_key        UUID NOT NULL,
  supersedes_payslip_id  UUID,
  source_document_id     UUID,
  pay_month              DATE NOT NULL,
  currency               CHAR(3) NOT NULL REFERENCES currency(code),
  gross_earnings         NUMERIC(20,4) NOT NULL,
  total_deductions       NUMERIC(20,4) NOT NULL,
  net_pay                NUMERIC(20,4) NOT NULL,
  deposit_transaction_id UUID,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_payslip_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_payslip_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT uq_payslip_supersedes UNIQUE (supersedes_payslip_id),
  CONSTRAINT fk_payslip_supersedes_scope FOREIGN KEY (household_id, supersedes_payslip_id)
    REFERENCES payslip (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_document_scope FOREIGN KEY (household_id, source_document_id)
    REFERENCES document (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_deposit_scope FOREIGN KEY (household_id, deposit_transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT payslip_month_is_first_day CHECK (pay_month = date_trunc('month', pay_month)::date),
  CONSTRAINT payslip_amounts_are_valid CHECK (
    gross_earnings > 0 AND total_deductions >= 0 AND net_pay >= 0),
  CONSTRAINT payslip_net_is_gross_less_deductions CHECK (
    net_pay = gross_earnings - total_deductions)
);
CREATE UNIQUE INDEX uq_payslip_deposit ON payslip (deposit_transaction_id)
  WHERE deposit_transaction_id IS NOT NULL;
CREATE INDEX idx_payslip_household_month ON payslip (household_id, pay_month DESC);

CREATE TABLE payslip_line (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  payslip_id   UUID NOT NULL,
  line_no      SMALLINT NOT NULL,
  kind         payslip_line_kind NOT NULL,
  label        TEXT NOT NULL,
  amount       NUMERIC(20,4) NOT NULL,
  CONSTRAINT fk_payslip_line_payslip_scope FOREIGN KEY (household_id, payslip_id)
    REFERENCES payslip (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_payslip_line_no UNIQUE (payslip_id, line_no),
  CONSTRAINT payslip_line_no_is_bounded CHECK (line_no BETWEEN 1 AND 40),
  CONSTRAINT payslip_line_amount_is_positive CHECK (amount > 0),
  CONSTRAINT payslip_line_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 80 AND label = btrim(label))
);

-- The one write path for entry, correction and import. Idempotent replay compares the full
-- request shape (never the source document, so the first provenance stays); a correction
-- requires the original to be unlinked; a fresh entry refuses a duplicate active month.
CREATE FUNCTION record_payslip(
  p_idempotency_key UUID,
  p_supersedes_payslip_id UUID,
  p_source_document_id UUID,
  p_pay_month DATE,
  p_currency CHAR(3),
  p_gross_earnings NUMERIC,
  p_total_deductions NUMERIC,
  p_net_pay NUMERIC,
  p_line_kinds payslip_line_kind[],
  p_line_labels TEXT[],
  p_line_amounts NUMERIC[]
) RETURNS TABLE (recorded_payslip_id UUID, created BOOLEAN)
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID;
  existing payslip%ROWTYPE;
  supersedes_row payslip%ROWTYPE;
  n_lines INT;
  new_id UUID;
  i INT;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  PERFORM 1 FROM household WHERE id = owner_household FOR UPDATE;

  IF p_gross_earnings IS DISTINCT FROM round(p_gross_earnings, 4)
     OR p_total_deductions IS DISTINCT FROM round(p_total_deductions, 4)
     OR p_net_pay IS DISTINCT FROM round(p_net_pay, 4)
     OR EXISTS (SELECT FROM unnest(p_line_amounts) a WHERE a IS DISTINCT FROM round(a, 4)) THEN
    RAISE EXCEPTION 'payslip amounts exceed scale 4' USING ERRCODE = '22003';
  END IF;

  n_lines := coalesce(array_length(p_line_kinds, 1), 0);
  IF p_line_kinds IS NULL OR p_line_labels IS NULL OR p_line_amounts IS NULL
     OR array_length(p_line_labels, 1) IS DISTINCT FROM n_lines
     OR array_length(p_line_amounts, 1) IS DISTINCT FROM n_lines
     OR n_lines NOT BETWEEN 1 AND 40
     OR EXISTS (SELECT FROM unnest(p_line_kinds) k WHERE k IS NULL)
     OR EXISTS (SELECT FROM unnest(p_line_labels) l WHERE l IS NULL)
     OR EXISTS (SELECT FROM unnest(p_line_amounts) a WHERE a IS NULL) THEN
    RAISE EXCEPTION 'payslip lines must be equal-length arrays of 1-40 entries with no null'
      USING ERRCODE = '22023';
  END IF;

  SELECT * INTO existing FROM payslip
   WHERE household_id = owner_household AND idempotency_key = p_idempotency_key;
  IF FOUND THEN
    IF existing.supersedes_payslip_id IS NOT DISTINCT FROM p_supersedes_payslip_id
       AND existing.pay_month = p_pay_month
       AND rtrim(existing.currency) = rtrim(p_currency)
       AND existing.gross_earnings = p_gross_earnings
       AND existing.total_deductions = p_total_deductions
       AND existing.net_pay = p_net_pay
       AND (SELECT array_agg(kind ORDER BY line_no) FROM payslip_line
             WHERE household_id = owner_household AND payslip_id = existing.id) = p_line_kinds
       AND (SELECT array_agg(label ORDER BY line_no) FROM payslip_line
             WHERE household_id = owner_household AND payslip_id = existing.id) = p_line_labels
       AND (SELECT array_agg(amount ORDER BY line_no) FROM payslip_line
             WHERE household_id = owner_household AND payslip_id = existing.id) = p_line_amounts
    THEN
      recorded_payslip_id := existing.id;
      created := false;
      RETURN NEXT;
      RETURN;
    END IF;
    RAISE EXCEPTION 'payslip idempotency key reused with different arguments'
      USING ERRCODE = '23505', CONSTRAINT = 'payslip_idempotency_conflict';
  END IF;

  IF p_supersedes_payslip_id IS NOT NULL THEN
    SELECT * INTO supersedes_row FROM payslip
     WHERE id = p_supersedes_payslip_id AND household_id = owner_household
     FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'payslip % not found', p_supersedes_payslip_id USING ERRCODE = 'no_data_found';
    END IF;
    IF supersedes_row.deposit_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'a linked payslip must be unlinked before it can be corrected'
        USING ERRCODE = '23514', CONSTRAINT = 'payslip_is_linked';
    END IF;
    -- A second correction of the same original fails on uq_payslip_supersedes below.
  ELSIF EXISTS (
    SELECT FROM payslip p
     WHERE p.household_id = owner_household
       AND p.pay_month = p_pay_month AND rtrim(p.currency) = rtrim(p_currency)
       AND p.net_pay = p_net_pay
       AND NOT EXISTS (SELECT FROM payslip s WHERE s.supersedes_payslip_id = p.id)
  ) THEN
    RAISE EXCEPTION 'a payslip for this month, currency and net pay is already recorded'
      USING ERRCODE = '23505', CONSTRAINT = 'payslip_duplicate';
  END IF;

  SET CONSTRAINTS payslip_totals_agree DEFERRED;

  INSERT INTO payslip (
    household_id, idempotency_key, supersedes_payslip_id, source_document_id,
    pay_month, currency, gross_earnings, total_deductions, net_pay
  ) VALUES (
    owner_household, p_idempotency_key, p_supersedes_payslip_id, p_source_document_id,
    p_pay_month, p_currency, p_gross_earnings, p_total_deductions, p_net_pay
  ) RETURNING id INTO new_id;

  FOR i IN 1 .. n_lines LOOP
    INSERT INTO payslip_line (household_id, payslip_id, line_no, kind, label, amount)
    VALUES (owner_household, new_id, i, p_line_kinds[i], p_line_labels[i], p_line_amounts[i]);
  END LOOP;

  SET CONSTRAINTS payslip_totals_agree IMMEDIATE;

  recorded_payslip_id := new_id;
  created := true;
  RETURN NEXT;
END $$;

REVOKE ALL ON FUNCTION record_payslip(
  UUID, UUID, UUID, DATE, CHAR, NUMERIC, NUMERIC, NUMERIC, payslip_line_kind[], TEXT[], NUMERIC[]
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION record_payslip(
  UUID, UUID, UUID, DATE, CHAR, NUMERIC, NUMERIC, NUMERIC, payslip_line_kind[], TEXT[], NUMERIC[]
) TO authenticated;

-- Named as one constraint so both tables' triggers report the same name; deferred to the end
-- of each SET CONSTRAINTS ... IMMEDIATE inside record_payslip, not the outer transaction, so a
-- multi-month import checks each recorded payslip as it is written.
CREATE FUNCTION assert_payslip_totals_agree() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE
  target_id UUID;
  target_household UUID;
  header payslip%ROWTYPE;
  earnings_sum NUMERIC;
  deductions_sum NUMERIC;
BEGIN
  IF TG_TABLE_NAME = 'payslip' THEN
    target_id := NEW.id;
  ELSE
    target_id := NEW.payslip_id;
  END IF;
  target_household := NEW.household_id;

  SELECT * INTO header FROM payslip
   WHERE id = target_id AND household_id = target_household;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'payslip % is missing for its totals check', target_id
      USING ERRCODE = '23514', CONSTRAINT = 'payslip_totals_agree';
  END IF;

  SELECT coalesce(sum(amount) FILTER (WHERE kind = 'earning'), 0),
         coalesce(sum(amount) FILTER (WHERE kind = 'deduction'), 0)
    INTO earnings_sum, deductions_sum
    FROM payslip_line WHERE payslip_id = target_id AND household_id = target_household;

  IF earnings_sum IS DISTINCT FROM header.gross_earnings
     OR deductions_sum IS DISTINCT FROM header.total_deductions THEN
    RAISE EXCEPTION 'payslip % lines do not sum to its stated totals', target_id
      USING ERRCODE = '23514', CONSTRAINT = 'payslip_totals_agree';
  END IF;
  RETURN NULL;
END $$;

-- Both triggers share this exact name (legal: trigger names are unique per table, not
-- database-wide) so one `SET CONSTRAINTS payslip_totals_agree ...` inside record_payslip
-- governs both the header and line insert.
CREATE CONSTRAINT TRIGGER payslip_totals_agree
  AFTER INSERT ON payslip
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION assert_payslip_totals_agree();

CREATE CONSTRAINT TRIGGER payslip_totals_agree
  AFTER INSERT ON payslip_line
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION assert_payslip_totals_agree();

REVOKE ALL ON FUNCTION assert_payslip_totals_agree() FROM PUBLIC;

-- Immutable except the deposit link: every other field, and any delete, is refused.
CREATE FUNCTION preserve_payslip() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'a payslip cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.household_id IS DISTINCT FROM OLD.household_id
     OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.supersedes_payslip_id IS DISTINCT FROM OLD.supersedes_payslip_id
     OR NEW.source_document_id IS DISTINCT FROM OLD.source_document_id
     OR NEW.pay_month IS DISTINCT FROM OLD.pay_month
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.gross_earnings IS DISTINCT FROM OLD.gross_earnings
     OR NEW.total_deductions IS DISTINCT FROM OLD.total_deductions
     OR NEW.net_pay IS DISTINCT FROM OLD.net_pay
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'a payslip is immutable except its deposit link' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_preserve_payslip
  BEFORE UPDATE OR DELETE ON payslip
  FOR EACH ROW EXECUTE FUNCTION preserve_payslip();

REVOKE ALL ON FUNCTION preserve_payslip() FROM PUBLIC;

CREATE FUNCTION preserve_payslip_line() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'a payslip line cannot be changed' USING ERRCODE = '23514';
END $$;

CREATE TRIGGER trg_preserve_payslip_line
  BEFORE UPDATE OR DELETE ON payslip_line
  FOR EACH ROW EXECUTE FUNCTION preserve_payslip_line();

REVOKE ALL ON FUNCTION preserve_payslip_line() FROM PUBLIC;

-- Deposit eligibility, checked whenever deposit_transaction_id is set or changed: a superseded
-- payslip can never be linked, and the credit must be this household's reportable income row in
-- the payslip's currency for exactly its net pay.
CREATE FUNCTION assert_payslip_deposit() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.deposit_transaction_id IS NULL THEN
    RETURN NEW;
  END IF;
  IF EXISTS (SELECT FROM payslip s WHERE s.supersedes_payslip_id = NEW.id) THEN
    RAISE EXCEPTION 'a superseded payslip cannot be linked'
      USING ERRCODE = '23514', CONSTRAINT = 'payslip_is_superseded';
  END IF;
  IF NOT EXISTS (
    SELECT FROM v_transaction_reportable r
     WHERE r.id = NEW.deposit_transaction_id AND r.household_id = NEW.household_id
       AND r.txn_type = 'income' AND rtrim(r.currency) = rtrim(NEW.currency)
       AND r.amount = NEW.net_pay
  ) THEN
    RAISE EXCEPTION 'the linked credit is not an eligible net-pay deposit'
      USING ERRCODE = '23514', CONSTRAINT = 'payslip_deposit_is_eligible';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_assert_payslip_deposit
  BEFORE INSERT OR UPDATE OF deposit_transaction_id ON payslip
  FOR EACH ROW EXECUTE FUNCTION assert_payslip_deposit();

REVOKE ALL ON FUNCTION assert_payslip_deposit() FROM PUBLIC;

ALTER TABLE payslip ENABLE ROW LEVEL SECURITY;
ALTER TABLE payslip_line ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON payslip FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON payslip_line FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
