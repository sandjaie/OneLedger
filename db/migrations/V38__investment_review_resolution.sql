-- Phase 1.6: validate investment-event review resolution while retaining review history.

CREATE UNIQUE INDEX idx_investment_review_one_event_reason
  ON investment_review_item (household_id, investment_transaction_id, reason)
  WHERE investment_transaction_id IS NOT NULL;

CREATE OR REPLACE FUNCTION preserve_investment_review_item() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'investment_review_item is append-only'
      USING ERRCODE = '55000';
  END IF;
  IF current_user = pg_get_userbyid((
       SELECT relation.relowner FROM pg_class relation
        WHERE relation.oid = 'investment_review_item'::regclass
     ))
     AND current_setting('oneledger.investment_review_resolution', true) = 'on'
     AND OLD.status = 'open' AND NEW.status = 'resolved'
     AND (NEW.id, NEW.household_id, NEW.import_id, NEW.group_id, NEW.draft_id,
          NEW.checkpoint_id, NEW.investment_transaction_id, NEW.holding_id,
          NEW.reason, NEW.created_at)
         IS NOT DISTINCT FROM
         (OLD.id, OLD.household_id, OLD.import_id, OLD.group_id, OLD.draft_id,
          OLD.checkpoint_id, OLD.investment_transaction_id, OLD.holding_id,
          OLD.reason, OLD.created_at)
     AND NEW.resolution_kind IS NOT NULL
     AND NEW.resolution_reference_id IS NOT NULL
     AND NEW.resolved_by IS NOT NULL
     AND NEW.resolved_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'investment review state requires a validated resolution operation'
    USING ERRCODE = '55000';
END $$;

CREATE FUNCTION resolve_investment_review_item(
  p_review_id UUID,
  p_resolution_kind investment_review_resolution_kind,
  p_resolution_reference_id UUID
) RETURNS UUID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; actor_id UUID; target investment_review_item%ROWTYPE;
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
  IF target.reason <> 'incomplete_lot_allocation'
     OR target.investment_transaction_id IS NULL
     OR p_resolution_kind <> 'event_corrected'
     OR NOT EXISTS (
       SELECT FROM investment_transaction replacement
        WHERE replacement.household_id = owner_household
          AND replacement.id = p_resolution_reference_id
          AND replacement.corrects_transaction_id = target.investment_transaction_id
     ) THEN
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

REVOKE ALL ON FUNCTION preserve_investment_review_item() FROM PUBLIC;
REVOKE ALL ON FUNCTION resolve_investment_review_item(
  UUID, investment_review_resolution_kind, UUID
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION resolve_investment_review_item(
  UUID, investment_review_resolution_kind, UUID
) TO authenticated;
