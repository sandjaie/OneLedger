-- db/migrations/V14__commit_import_batch.sql
-- M2 Task 6d: the commit path.
--
-- `api-contracts.html` section 3 states the rule this migration exists to satisfy: if a failure
-- halfway through would leave the ledger inconsistent, it belongs in one function in the
-- database. Turning a reviewed batch into ledger rows is exactly that shape — the insert and the
-- balance verification must succeed or fail together, and no application-side sequence of
-- statements can promise it across a dropped connection.
--
-- Both functions are SECURITY DEFINER and both call assert_is_owner() first. The API service
-- connects privileged and bypasses RLS (ADR-0013, refined by ADR-0016), so these assertions are
-- the boundary rather than a convenience over one.

-- The account's balance as the statement's closing date saw it, compared against what the
-- statement declared. Raises on any difference; the caller's transaction carries the rollback.
--
-- Separate from commit_import_batch, and public, because the review screen and `GET /doctor`
-- will both want to ask the question without committing anything.
CREATE FUNCTION assert_account_reconciles(
  p_account_id UUID, p_declared_closing NUMERIC, p_period_end DATE
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE v_opening NUMERIC(20,4); v_computed NUMERIC(20,4); v_household UUID;
BEGIN
  PERFORM assert_is_owner();

  -- A null is not a passed verification. `v_computed <> NULL` is NULL, which is not true, so a
  -- check written the obvious way would return silently having verified nothing — the one
  -- outcome worse than a wrong answer, because it looks like a right one (Invariant I7).
  IF p_declared_closing IS NULL OR p_period_end IS NULL THEN
    RAISE EXCEPTION 'cannot verify a balance against a null closing figure or period end'
      USING ERRCODE = '22004';
  END IF;

  SELECT opening_balance, household_id INTO v_opening, v_household
  FROM account WHERE id = p_account_id;
  IF v_opening IS NULL THEN
    RAISE EXCEPTION 'no such account' USING ERRCODE = '23503';
  END IF;
  IF v_household <> current_household_id() THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;

  -- v_transaction_posting, not the base table: a split parent is a container and its children
  -- already sum to it, so counting both would double every split. Not v_transaction_reportable
  -- either — that view drops transfers and adjustments, which move money and therefore belong
  -- in a balance even though they belong in no spending report (Invariant I3 is about reports,
  -- and this is not one).
  SELECT v_opening + COALESCE(sum(amount), 0) INTO v_computed
  FROM v_transaction_posting
  WHERE account_id = p_account_id AND booked_date <= p_period_end;

  IF v_computed <> p_declared_closing THEN
    RAISE EXCEPTION
      'the account does not reconcile as of %: the statement declares % and the ledger holds %',
      p_period_end, p_declared_closing, v_computed
      USING ERRCODE = '23514';
  END IF;
END $$;

COMMENT ON FUNCTION assert_account_reconciles(UUID, NUMERIC, DATE) IS
  'Invariant I8''s post-commit half: the account balance as of a statement''s close against the '
  'figure that statement declared. Raises rather than returns false, so a caller cannot ignore '
  'it by forgetting to read the result.';

-- One reviewed batch becomes ledger rows, or nothing does. Returns the number of rows imported.
CREATE FUNCTION commit_import_batch(p_batch_id UUID) RETURNS INTEGER
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE v_batch RECORD; v_imported INTEGER; v_household UUID;
BEGIN
  PERFORM assert_is_owner();
  v_household := current_household_id();

  -- The household row is this ledger's serialization point for anything that writes a frozen
  -- amount (spec B3.5): taking it here stops a reporting-pair change from interleaving with a
  -- commit whose amount_base was frozen against the pair as it was at staging.
  PERFORM 1 FROM household WHERE id = v_household FOR UPDATE;

  SELECT * INTO v_batch FROM import_batch
  WHERE id = p_batch_id AND household_id = v_household
  FOR UPDATE;
  IF NOT FOUND THEN
    -- Deliberately one message for "no such batch" and "another household's batch": telling
    -- them apart tells a caller whether an id they guessed exists.
    RAISE EXCEPTION 'no such import batch' USING ERRCODE = '42501';
  END IF;

  -- Checked rather than assumed. `matched` is the only status a staged batch can carry in M2,
  -- because every other verdict blocks staging before the transaction opens (spec sections 4.6
  -- and 5.5) — but this function is the last thing standing between a batch and the ledger, and
  -- "the caller already checked" is how that stops being true.
  IF v_batch.reconciliation_status IS DISTINCT FROM 'matched' THEN
    RAISE EXCEPTION 'this batch did not reconcile (%), so it cannot commit',
      COALESCE(v_batch.reconciliation_status, 'not reconciled')
      USING ERRCODE = '23514';
  END IF;
  IF v_batch.status = 'completed' THEN
    RAISE EXCEPTION 'this batch has already been imported' USING ERRCODE = '23505';
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
    s.household_id, s.account_id, s.booked_date, s.value_date, s.description_raw,
    s.description_clean, s.reference_raw, s.amount, s.currency, s.original_amount,
    s.original_currency,
    -- The frozen facts are carried through unchanged, never recomputed. Re-deriving them here
    -- against today's stored rate would silently re-price a statement at commit time, which is
    -- the whole thing freezing at staging exists to prevent (Invariant I5).
    s.fx_rate_date, s.fx_rate_is_carried, s.fx_rate_to_base, s.fx_rate_to_secondary,
    s.amount_base, s.amount_secondary,
    -- Non-null on every accepted row by V13's accepted_row_is_committable CHECK, so this
    -- satisfies transaction.txn_type NOT NULL without a COALESCE inventing a type.
    s.proposed_txn_type, s.proposed_category_id, s.proposed_merchant_id,
    s.proposed_payment_method, s.proposed_country,
    'statement_import', v_batch.document_id, s.source_page_number, s.source_row_number,
    -- Invariant I6: the row names the document, page, row, batch and layout version that
    -- produced it, and carries the fingerprint that recognised it.
    p_batch_id, v_batch.statement_layout_id, v_batch.layout_version, s.dedupe_hash,
    s.classified_by, s.classification_rule_id, s.classification_confidence
  FROM staged_transaction s
  WHERE s.import_batch_id = p_batch_id AND s.status = 'accepted';

  GET DIAGNOSTICS v_imported = ROW_COUNT;

  -- The verification, inside this same transaction, so a raise takes the insert with it.
  -- `declared_closing_balance` is the batch's recorded closing target — the closing-pattern
  -- capture, or the terminal running balance where the layout declares no closing anchor — and
  -- never the maintained current_balance: the import screen imposes no chronological order, so
  -- a valid older statement committed after a newer one would otherwise be compared against a
  -- balance that already contains the newer postings (spec section 4.9).
  PERFORM assert_account_reconciles(
    v_batch.account_id, v_batch.declared_closing_balance, v_batch.period_end
  );

  UPDATE import_batch
  SET status = 'completed', rows_imported = v_imported, completed_at = now()
  WHERE id = p_batch_id;

  RETURN v_imported;
END $$;

COMMENT ON FUNCTION commit_import_batch(UUID) IS
  'One reviewed batch becomes ledger rows in one transaction, verified against the statement''s '
  'own closing balance before it is marked completed. Returns the number of rows imported.';
