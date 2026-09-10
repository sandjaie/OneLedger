-- db/migrations/V18__closing_balance_is_verified_as_of_its_own_last_row.sql
-- Slice 1.2, task 2c3: the date a statement's closing balance actually describes.
--
-- V14 verifies a committed batch as of `import_batch.period_end`, on the reasonable assumption
-- that a statement's declared closing is the balance at the close of the period it declares.
-- Slice 1.2's fourth producer disproves it: it books its year-end interest and the tax deducted
-- on it on **1 January**, one day after the period the statement declares, values both on 31
-- December inside it, and carries both in the closing balance it prints. Verified as of
-- `period_end`, those rows are excluded from the sum and every such statement is refused —
-- measured at two of the six statements held, short by exactly the late rows' total.
--
-- The fix is to verify as of the date the declared closing describes: **the latest booked date
-- the batch itself carries**, never earlier than `period_end`. `GREATEST` is what keeps it never
-- earlier: shrinking the window would let a statement be verified against a balance that
-- excludes rows it lists, which is the failure this migration exists to remove, arriving from
-- the other side. For the three layouts whose rows all fall inside their period — every one
-- before this — `max(booked_date)` is at or before `period_end` and nothing changes.
--
-- The window is widened by the same days the statement itself is late by, so a valid older
-- statement committed after a newer one can now see a day or two of the newer one's rows. That
-- hazard is V14's already, not this migration's: it is why the comparison is against the
-- statement's own `declared_closing_balance` and never the maintained `current_balance`
-- (spec section 4.9), and it is why the import screen's own guidance is to import in order.
--
-- Only the verification call changes. `search_path = public, pg_temp` names pg_temp explicitly
-- and last for the reason V15 gives, and is repeated here rather than inherited because
-- CREATE OR REPLACE replaces the whole function, settings included. Do not "simplify" it.

CREATE OR REPLACE FUNCTION commit_import_batch(p_batch_id UUID) RETURNS INTEGER
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE
  v_batch RECORD; v_imported INTEGER; v_household UUID; v_stray INTEGER; v_row RECORD;
  v_as_of DATE;
BEGIN
  PERFORM assert_is_owner();
  v_household := current_household_id();

  PERFORM 1 FROM household WHERE id = v_household FOR UPDATE;

  SELECT * INTO v_batch FROM import_batch
  WHERE id = p_batch_id AND household_id = v_household
  FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no such import batch' USING ERRCODE = '42501';
  END IF;

  IF v_batch.reconciliation_status IS DISTINCT FROM 'matched' THEN
    RAISE EXCEPTION 'this batch did not reconcile (%), so it cannot commit',
      COALESCE(v_batch.reconciliation_status, 'not reconciled')
      USING ERRCODE = '23514';
  END IF;

  IF v_batch.status = 'completed' THEN
    RAISE EXCEPTION 'this batch has already been imported' USING ERRCODE = '23505';
  END IF;
  IF v_batch.status <> 'awaiting_review' THEN
    RAISE EXCEPTION 'a batch in state % cannot be imported', v_batch.status
      USING ERRCODE = '23505';
  END IF;

  SELECT count(*) INTO v_stray FROM staged_transaction s
  WHERE s.import_batch_id = p_batch_id AND s.status = 'accepted'
    AND (s.household_id <> v_household OR s.account_id <> v_batch.account_id);
  IF v_stray > 0 THEN
    RAISE EXCEPTION 'this batch holds % accepted row(s) belonging to another account', v_stray
      USING ERRCODE = '42501';
  END IF;

  SELECT s.source_row_number, s.dedupe_hash INTO v_row
  FROM staged_transaction s
  JOIN transaction t ON t.dedupe_hash = s.dedupe_hash AND t.household_id = v_household
  WHERE s.import_batch_id = p_batch_id AND s.status = 'accepted'
  LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION
      'row % is already in the ledger; reject it rather than accepting it', v_row.source_row_number
      USING ERRCODE = '23505';
  END IF;

  SELECT s.source_row_number, s.booked_date INTO v_row
  FROM staged_transaction s
  JOIN account a ON a.id = v_batch.account_id
  WHERE s.import_batch_id = p_batch_id AND s.status = 'accepted'
    AND s.booked_date < a.opening_balance_date
  ORDER BY s.source_row_number
  LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION
      'row % is dated %, before this account''s opening balance date',
      v_row.source_row_number, v_row.booked_date
      USING ERRCODE = '22003';
  END IF;

  INSERT INTO transaction (
    household_id, account_id, booked_date, value_date, description_raw, description_clean,
    reference_raw, amount, currency, original_amount, original_currency,
    fx_rate_date, fx_rate_is_carried, fx_rate_to_base, fx_rate_to_secondary,
    amount_base, amount_secondary, txn_type, category_id, merchant_id, payment_method,
    country, source, source_document_id, source_page_number, source_row_number,
    import_batch_id, statement_layout_id, layout_version, dedupe_hash,
    classified_by, classification_rule_id, classification_confidence
  )
  SELECT
    v_household, v_batch.account_id, s.booked_date, s.value_date, s.description_raw,
    s.description_clean, s.reference_raw, s.amount, s.currency, s.original_amount,
    s.original_currency,
    s.fx_rate_date, s.fx_rate_is_carried, s.fx_rate_to_base, s.fx_rate_to_secondary,
    s.amount_base, s.amount_secondary,
    s.proposed_txn_type, s.proposed_category_id, s.proposed_merchant_id,
    s.proposed_payment_method, s.proposed_country,
    'statement_import', v_batch.document_id, s.source_page_number, s.source_row_number,
    p_batch_id, v_batch.statement_layout_id, v_batch.layout_version, s.dedupe_hash,
    s.classified_by, s.classification_rule_id, s.classification_confidence
  FROM staged_transaction s
  WHERE s.import_batch_id = p_batch_id AND s.status = 'accepted';

  GET DIAGNOSTICS v_imported = ROW_COUNT;

  -- The date the declared closing describes: the last row this statement lists, and never
  -- earlier than the period it declares. Read from the batch's own accepted rows, so a
  -- statement whose rows all sit inside its period is verified exactly as V14 verified it.
  SELECT GREATEST(COALESCE(max(s.booked_date), v_batch.period_end), v_batch.period_end)
    INTO v_as_of
  FROM staged_transaction s
  WHERE s.import_batch_id = p_batch_id AND s.status = 'accepted';

  -- The verification, inside this same transaction, so a raise takes the insert with it.
  -- `declared_closing_balance` is the batch's recorded closing target — the closing-pattern
  -- capture, or the terminal running balance where the layout declares no closing anchor — and
  -- never the maintained current_balance: the import screen imposes no chronological order, so
  -- a valid older statement committed after a newer one would otherwise be compared against a
  -- balance that already contains the newer postings (spec section 4.9).
  PERFORM assert_account_reconciles(
    v_batch.account_id, v_batch.declared_closing_balance, v_as_of
  );

  UPDATE import_batch
  SET status = 'completed', rows_imported = v_imported, completed_at = now()
  WHERE id = p_batch_id;

  RETURN v_imported;
END $$;

COMMENT ON FUNCTION commit_import_batch(UUID) IS
  'One reviewed batch becomes ledger rows in one transaction, verified against the statement''s '
  'own closing balance as of the last row that statement lists. Returns the number of rows '
  'imported.';
