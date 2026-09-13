-- Phase 1.6: resolve incomplete disposal-lot review only from complete retained allocation evidence.

CREATE OR REPLACE FUNCTION resolve_investment_review_item(
  p_review_id UUID,
  p_resolution_kind investment_review_resolution_kind,
  p_resolution_reference_id UUID
) RETURNS UUID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; actor_id UUID; target investment_review_item%ROWTYPE;
  valid_resolution BOOLEAN;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  actor_id := NULLIF(current_setting('app.user_id', true), '')::UUID;
  IF p_review_id IS NULL OR p_resolution_kind IS NULL OR p_resolution_reference_id IS NULL THEN
    RAISE EXCEPTION 'review resolution arguments must be complete' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO target FROM investment_review_item
   WHERE household_id = owner_household AND id = p_review_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  IF target.status = 'resolved' THEN
    IF target.resolution_kind = p_resolution_kind
       AND target.resolution_reference_id = p_resolution_reference_id THEN
      RETURN target.id;
    END IF;
    RAISE EXCEPTION 'investment review is already resolved' USING ERRCODE = '23505';
  END IF;

  valid_resolution := target.reason = 'incomplete_lot_allocation'
    AND target.investment_transaction_id IS NOT NULL
    AND (
      (p_resolution_kind = 'event_corrected' AND EXISTS (
        SELECT FROM investment_transaction replacement
         WHERE replacement.household_id = owner_household
           AND replacement.id = p_resolution_reference_id
           AND replacement.corrects_transaction_id = target.investment_transaction_id
      ))
      OR
      (p_resolution_kind = 'lot_allocated'
       AND p_resolution_reference_id = target.investment_transaction_id
       AND EXISTS (
         SELECT FROM investment_transaction disposal
          WHERE disposal.household_id = owner_household
            AND disposal.id = target.investment_transaction_id
            AND disposal.event_kind IN (
              'sale', 'redemption', 'maturity', 'switch_out',
              'systematic_transfer_out', 'platform_transfer_out')
            AND disposal.units < 0
            AND NOT EXISTS (
              SELECT FROM investment_transaction correction
               WHERE correction.household_id = disposal.household_id
                 AND correction.corrects_transaction_id = disposal.id)
            AND COALESCE((
              SELECT sum(allocation.allocated_units)
                FROM investment_lot_allocation allocation
               WHERE allocation.household_id = disposal.household_id
                 AND allocation.disposal_transaction_id = disposal.id
            ), 0) = abs(disposal.units)
       ))
    );
  IF NOT valid_resolution THEN
    RAISE EXCEPTION 'review resolution does not match its retained event evidence'
      USING ERRCODE = '23514';
  END IF;
  PERFORM set_config('oneledger.investment_review_resolution', 'on', true);
  UPDATE investment_review_item
     SET status = 'resolved', resolution_kind = p_resolution_kind,
         resolution_reference_id = p_resolution_reference_id,
         resolved_by = actor_id, resolved_at = now()
   WHERE id = target.id;
  PERFORM set_config('oneledger.investment_review_resolution', 'off', true);
  RETURN target.id;
END $$;
