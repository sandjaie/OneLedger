CREATE FUNCTION guard_cash_allocation_active_event() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('oneledger.investment_cash_allocation_write', true) = 'on' THEN
    PERFORM 1 FROM investment_transaction event
     WHERE event.household_id = NEW.household_id
       AND event.id = NEW.investment_transaction_id FOR UPDATE;
    IF EXISTS (
      SELECT FROM investment_transaction correction
       WHERE correction.household_id = NEW.household_id
         AND correction.corrects_transaction_id = NEW.investment_transaction_id
    ) THEN
      RAISE EXCEPTION 'cash funding requires the active investment event'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_cash_allocation_active_event';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_cash_allocation_requires_active_event
  BEFORE INSERT ON investment_cash_allocation
  FOR EACH ROW EXECUTE FUNCTION guard_cash_allocation_active_event();

CREATE FUNCTION guard_correction_unfunded_event() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('oneledger.investment_event_append', true) = 'on'
     AND NEW.corrects_transaction_id IS NOT NULL THEN
    PERFORM 1 FROM investment_transaction event
     WHERE event.household_id = NEW.household_id
       AND event.id = NEW.corrects_transaction_id FOR UPDATE;
    IF EXISTS (
      SELECT FROM investment_cash_allocation allocation
       WHERE allocation.household_id = NEW.household_id
         AND allocation.investment_transaction_id = NEW.corrects_transaction_id
    ) THEN
      RAISE EXCEPTION 'a funded investment event cannot be corrected'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_correction_requires_unfunded_event';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_correction_requires_unfunded_event
  BEFORE INSERT ON investment_transaction
  FOR EACH ROW EXECUTE FUNCTION guard_correction_unfunded_event();

CREATE OR REPLACE FUNCTION unlink_investment_cash_allocations(p_cash_transaction_id UUID)
  RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE owner_household UUID; prior_type txn_type; prior_count INTEGER;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  PERFORM 1 FROM investment_transaction event
   WHERE event.household_id = owner_household
     AND EXISTS (
       SELECT FROM investment_cash_allocation allocation
        WHERE allocation.household_id = owner_household
          AND allocation.cash_transaction_id = p_cash_transaction_id
          AND allocation.investment_transaction_id = event.id
     )
   ORDER BY event.id FOR UPDATE;
  PERFORM 1 FROM transaction cash
   WHERE cash.id = p_cash_transaction_id AND cash.household_id = owner_household FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  SELECT min(prior_txn_type::TEXT)::txn_type, count(DISTINCT prior_txn_type)
    INTO prior_type, prior_count
    FROM investment_cash_allocation
   WHERE household_id = owner_household
     AND cash_transaction_id = p_cash_transaction_id;
  IF prior_type IS NULL THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF prior_count <> 1 THEN
    RAISE EXCEPTION 'cash allocations do not agree on the prior transaction type'
      USING ERRCODE = '23514';
  END IF;
  PERFORM set_config('oneledger.investment_cash_allocation_write', 'on', true);
  UPDATE transaction SET txn_type = prior_type WHERE id = p_cash_transaction_id;
  DELETE FROM investment_cash_allocation
   WHERE household_id = owner_household
     AND cash_transaction_id = p_cash_transaction_id;
  PERFORM set_config('oneledger.investment_cash_allocation_write', 'off', true);
END $$;

REVOKE ALL ON FUNCTION guard_cash_allocation_active_event() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_correction_unfunded_event() FROM PUBLIC;
