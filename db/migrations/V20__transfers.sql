-- Transfers: schema.md `0010`, and the two functions that are the only way a leg is linked.
--
-- A transfer is money the household already had, moving between two accounts it owns. It is
-- never income and never spending, which is Invariant 3 — and I3 is why linking cannot be
-- three statements in a route. `transaction.transfer_type_agrees` (V10) says a row with a
-- `transfer_id` must also carry `transfer_in` or `transfer_out`, so the id and the type move
-- in the same UPDATE or neither moves. A route that set them separately would be refused by
-- its own constraint halfway through, leaving one leg linked and the other not
-- (`api-contracts.html` §3).
--
-- Both functions are SECURITY DEFINER and both call assert_is_owner() first. The service
-- connects privileged and bypasses RLS (ADR-0013, refined by ADR-0016), so the assertions in
-- here are the boundary rather than a convenience over one, and every household predicate is
-- written out rather than left to a policy that will not run.
--
-- `search_path = public, pg_temp` names pg_temp EXPLICITLY AND LAST, as V14, V15 and V18 do:
-- absent, Postgres searches the session's temporary schema first, and a temp table named
-- `transaction` would be what these functions read.

CREATE TYPE transfer_kind AS ENUM (
  'internal_same_currency','remittance','card_payment','loan_payment',
  'investment_funding','cash_withdrawal','other'
);

CREATE TYPE transfer_status AS ENUM ('candidate','confirmed','rejected');

CREATE TABLE transfer (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),

  source_account_id         UUID NOT NULL REFERENCES account(id),
  destination_account_id    UUID NOT NULL REFERENCES account(id),
  source_transaction_id     UUID REFERENCES transaction(id),
  destination_transaction_id UUID REFERENCES transaction(id),

  -- Both positive. The legs carry the signs (I2); a transfer carries magnitudes, so a
  -- cross-currency pair can be compared without reading either leg's convention.
  source_amount             NUMERIC(20,4) NOT NULL,
  source_currency           CHAR(3) NOT NULL,
  destination_amount        NUMERIC(20,4) NOT NULL,
  destination_currency      CHAR(3) NOT NULL,

  transfer_date             DATE NOT NULL,
  settlement_date           DATE,

  -- What the movement actually cost, as stored labelled facts rather than a figure recomputed
  -- at read time (Invariant 7, and `04-multi-currency-and-fx.md` §5). `implied_rate` is what
  -- the pair implies; `market_rate` is what the day's rate was; the spread between them is the
  -- cost the owner paid without seeing a fee line.
  implied_rate              NUMERIC(20,10),
  market_rate               NUMERIC(20,10),
  fx_spread_pct             NUMERIC(9,4),
  fx_spread_cost_source     NUMERIC(20,4),
  explicit_fee_amount       NUMERIC(20,4),
  explicit_fee_currency     CHAR(3),
  total_cost_source         NUMERIC(20,4),

  transfer_kind             transfer_kind NOT NULL,
  -- Free text the owner types, never an identifier: whoever moved the money is data
  -- (ADR-0012), so nothing in this schema enumerates them.
  provider                  TEXT,

  match_confidence          NUMERIC(4,3),
  match_method              TEXT NOT NULL,
  status                    transfer_status NOT NULL DEFAULT 'candidate',

  notes                     TEXT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT different_accounts CHECK (source_account_id <> destination_account_id),
  CONSTRAINT amounts_positive   CHECK (source_amount > 0 AND destination_amount > 0),
  CONSTRAINT confirmed_has_legs CHECK (
    status <> 'confirmed'
    OR (source_transaction_id IS NOT NULL AND destination_transaction_id IS NOT NULL))
);

-- A transaction is a leg of at most one *confirmed* transfer. Partial, on the status, because
-- the matcher is expected to propose the same row in several candidates and only one of them
-- can win: a total unique index would make proposing a second candidate an error.
CREATE UNIQUE INDEX idx_transfer_source ON transfer (source_transaction_id)
  WHERE source_transaction_id IS NOT NULL AND status = 'confirmed';
CREATE UNIQUE INDEX idx_transfer_dest   ON transfer (destination_transaction_id)
  WHERE destination_transaction_id IS NOT NULL AND status = 'confirmed';


-- The two partial indexes are not the whole invariant, and `data-model.html` §8 says why:
-- they stop one *transfer* claiming the same transaction twice in the same role, but nothing
-- in them stops one *transaction* being the source of transfer A and the destination of
-- transfer B. Each index is satisfied independently while `transaction.transfer_id` can point
-- at only one of the two, leaving the other silently inconsistent. The matcher will not
-- produce that pairing — it scores debit→credit only — but manual linking can
-- (`transfers-and-reconciliation.html` §6).
--
-- DEFERRABLE INITIALLY DEFERRED because confirm_transfer() sets the legs *after* the row is
-- confirmed; an immediate trigger would fire on a state that is one statement from correct.
CREATE FUNCTION assert_legs_unclaimed() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE v_claimed UUID;
BEGIN
  SELECT t.transfer_id INTO v_claimed FROM transaction t
   WHERE t.id IN (NEW.source_transaction_id, NEW.destination_transaction_id)
     AND t.transfer_id IS NOT NULL AND t.transfer_id <> NEW.id
   LIMIT 1;
  IF FOUND THEN
    RAISE EXCEPTION 'a transaction is already a leg of confirmed transfer %', v_claimed
      USING ERRCODE = 'unique_violation';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_one_transfer_per_leg
  AFTER INSERT OR UPDATE ON transfer
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.status = 'confirmed')
  EXECUTE FUNCTION assert_legs_unclaimed();

ALTER TABLE transfer ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON transfer
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- The foreign key schema.md `0018` reserves for the migration that creates the target.
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_transfer
  FOREIGN KEY (transfer_id) REFERENCES transfer(id) ON DELETE SET NULL;

-- V13 wrote proposed_transfer_kind as TEXT with a CHECK listing the seven values, because the
-- enum did not exist yet. It does now, and the CHECK becomes the type.
ALTER TABLE staged_transaction DROP CONSTRAINT staged_transaction_proposed_transfer_kind_check;
ALTER TABLE staged_transaction
  ALTER COLUMN proposed_transfer_kind TYPE transfer_kind
  USING proposed_transfer_kind::transfer_kind;

-- ---------------------------------------------------------------------------------------
-- Linking, and unlinking, as one act each.
-- ---------------------------------------------------------------------------------------

CREATE FUNCTION confirm_transfer(p_transfer_id UUID) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE v_transfer RECORD; v_household UUID;
BEGIN
  PERFORM assert_is_owner();
  v_household := current_household_id();

  SELECT * INTO v_transfer FROM transfer
   WHERE id = p_transfer_id AND household_id = v_household
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'transfer % not found', p_transfer_id USING ERRCODE = 'no_data_found';
  END IF;
  IF v_transfer.status = 'confirmed' THEN
    RETURN;                                   -- confirming twice is the same transfer
  END IF;
  IF v_transfer.source_transaction_id IS NULL OR v_transfer.destination_transaction_id IS NULL THEN
    RAISE EXCEPTION 'transfer % has no legs to link', p_transfer_id
      USING ERRCODE = 'check_violation';
  END IF;

  -- Both legs must be this household's, and must sit in the accounts the transfer names. The
  -- definer bypasses RLS, so this predicate is the isolation rather than a second opinion on
  -- one, and it is checked before anything is written.
  IF NOT EXISTS (
    SELECT FROM transaction s, transaction d
     WHERE s.id = v_transfer.source_transaction_id
       AND d.id = v_transfer.destination_transaction_id
       AND s.household_id = v_household AND d.household_id = v_household
       AND s.account_id = v_transfer.source_account_id
       AND d.account_id = v_transfer.destination_account_id)
  THEN
    RAISE EXCEPTION 'both legs must be this household''s and in the accounts the transfer names'
      USING ERRCODE = '42501';
  END IF;

  -- One statement set, so transfer_type_agrees never sees a half-linked pair. The money
  -- columns are untouched: assert_transaction_money_immutable() guards those and this changes
  -- only the type, the link and updated_at (I1, I5).
  UPDATE transaction SET transfer_id = p_transfer_id, txn_type = 'transfer_out', updated_at = now()
   WHERE id = v_transfer.source_transaction_id;
  UPDATE transaction SET transfer_id = p_transfer_id, txn_type = 'transfer_in', updated_at = now()
   WHERE id = v_transfer.destination_transaction_id;
  UPDATE transfer SET status = 'confirmed', updated_at = now() WHERE id = p_transfer_id;
END $$;

CREATE FUNCTION unlink_transfer(p_transfer_id UUID) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp
AS $$
DECLARE v_transfer RECORD; v_household UUID;
BEGIN
  PERFORM assert_is_owner();
  v_household := current_household_id();

  SELECT * INTO v_transfer FROM transfer
   WHERE id = p_transfer_id AND household_id = v_household
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'transfer % not found', p_transfer_id USING ERRCODE = 'no_data_found';
  END IF;

  -- Each leg's type comes back from its own sign, which is the rule `normalize.py` applies to
  -- a freshly parsed row. Restoring a remembered type would be remembering something the
  -- ledger never stored.
  -- `household_id = v_household` is unreachable given the transfer was fetched with that
  -- predicate and confirm_transfer() is the only writer of a leg — it is defence against a
  -- future second writer, and no test can reach it. Recorded here so a reviewer does not
  -- hunt for the test that proves it.
  UPDATE transaction
     SET transfer_id = NULL,
         txn_type = CASE WHEN amount > 0 THEN 'income'::txn_type ELSE 'expense'::txn_type END,
         updated_at = now()
   WHERE transfer_id = p_transfer_id AND household_id = v_household;

  -- Rejected, not back to candidate: the owner has said this pair is not a transfer, and the
  -- matcher must not propose it again on the next run.
  UPDATE transfer SET status = 'rejected', updated_at = now() WHERE id = p_transfer_id;
END $$;
