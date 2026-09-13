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
CREATE TABLE investment_lot_allocation_decision (
  household_id             UUID NOT NULL REFERENCES household(id),
  idempotency_key          UUID NOT NULL,
  request_hash             TEXT NOT NULL,
  disposal_transaction_id  UUID NOT NULL,
  realised_cost_native     NUMERIC(20,4),
  realised_gain_native     NUMERIC(20,4),
  remaining_basis_native   NUMERIC(20,4),
  allocation_complete      BOOLEAN NOT NULL,
  decided_by               UUID NOT NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (household_id, idempotency_key),
  FOREIGN KEY (household_id, disposal_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (household_id, decided_by)
    REFERENCES app_user(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT investment_lot_decision_hash_is_sha256
    CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT investment_lot_decision_claims_match_completion CHECK (
    (NOT allocation_complete AND realised_cost_native IS NULL
      AND realised_gain_native IS NULL AND remaining_basis_native IS NULL)
    OR (allocation_complete AND realised_cost_native IS NOT NULL
      AND realised_gain_native IS NOT NULL)),
  CONSTRAINT investment_lot_decision_basis_is_nonnegative CHECK (
    realised_cost_native >= 0 AND remaining_basis_native >= 0)
);
ALTER TABLE investment_lot_allocation_decision ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON investment_lot_allocation_decision FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
GRANT SELECT ON investment_lot_allocation_decision TO authenticated;
CREATE FUNCTION investment_lot_request_hash(
  p_disposal_transaction_id UUID, p_acquisition_lot_ids UUID[],
  p_allocated_units NUMERIC[], p_expected_state TEXT
) RETURNS TEXT LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
  SET search_path = public, pg_temp AS $$
BEGIN
  IF p_disposal_transaction_id IS NULL
     OR p_expected_state IS NULL OR p_expected_state !~ '^[0-9a-f]{64}$'
     OR p_acquisition_lot_ids IS NULL OR p_allocated_units IS NULL
     OR cardinality(p_acquisition_lot_ids) = 0
     OR cardinality(p_acquisition_lot_ids) <> cardinality(p_allocated_units)
     OR array_position(p_acquisition_lot_ids, NULL) IS NOT NULL
     OR array_position(p_allocated_units, NULL) IS NOT NULL
     OR (SELECT count(*) <> count(DISTINCT lot_id)
           FROM unnest(p_acquisition_lot_ids) lot_id)
     OR EXISTS (SELECT FROM unnest(p_allocated_units) units
                 WHERE units <= 0 OR units <> round(units, 8)) THEN
    RAISE EXCEPTION 'lot decision input must be complete and exact'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_decision_input_is_valid';
  END IF;
  RETURN encode(digest(convert_to(jsonb_build_array(
    p_disposal_transaction_id::TEXT, p_expected_state,
    (SELECT jsonb_agg(jsonb_build_array(lot_id::TEXT, (units::NUMERIC(28,8))::TEXT)
                      ORDER BY ordinal)
       FROM unnest(p_acquisition_lot_ids, p_allocated_units) WITH ORDINALITY
         AS item(lot_id, units, ordinal)))::TEXT, 'UTF8'), 'sha256'), 'hex');
END
$$;
CREATE FUNCTION preserve_investment_lot_decision() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'investment lot allocation decisions are immutable'
    USING ERRCODE = '55000';
END $$;
CREATE TRIGGER trg_lot_decision_is_immutable
  BEFORE UPDATE OR DELETE ON investment_lot_allocation_decision
  FOR EACH ROW EXECUTE FUNCTION preserve_investment_lot_decision();
CREATE FUNCTION lock_investment_event_mutations(p_event_ids UUID[]) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE owner_household UUID; event_id UUID;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  IF COALESCE(cardinality(p_event_ids), 0) = 0 THEN RAISE EXCEPTION
    'investment event references are required' USING ERRCODE = '22023'; END IF;
  FOR event_id IN SELECT DISTINCT id FROM unnest(p_event_ids) id ORDER BY id LOOP
    IF event_id IS NULL THEN RAISE EXCEPTION 'investment event references are required'
      USING ERRCODE = '22023'; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('investment-event:' || event_id, 0));
  END LOOP;
  IF (SELECT count(*) FROM investment_transaction WHERE household_id = owner_household
       AND id = ANY(p_event_ids)) <> cardinality(p_event_ids) THEN RAISE EXCEPTION
    'not authorized' USING ERRCODE = '42501'; END IF;
END $$;
CREATE FUNCTION lock_investment_lot_allocation_state(
  p_disposal_transaction_id UUID,
  p_acquisition_lot_ids UUID[]
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; target_holding UUID;
  origin_ids UUID[]; item_count INTEGER;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  item_count := cardinality(p_acquisition_lot_ids);
  IF p_disposal_transaction_id IS NULL OR item_count IS NULL OR item_count = 0
     OR EXISTS (SELECT FROM unnest(p_acquisition_lot_ids) id WHERE id IS NULL)
     OR (SELECT count(DISTINCT id) FROM unnest(p_acquisition_lot_ids) id) <> item_count THEN
    RAISE EXCEPTION 'lot allocation references must be complete and unique'
      USING ERRCODE = '22023';
  END IF;
  SELECT array_agg(lot.origin_transaction_id ORDER BY lot.origin_transaction_id)
    INTO origin_ids FROM acquisition_lot lot
   WHERE lot.household_id = owner_household AND lot.id = ANY(p_acquisition_lot_ids);
  IF COALESCE(cardinality(origin_ids), 0) <> item_count THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  PERFORM lock_investment_event_mutations(ARRAY(SELECT DISTINCT id
    FROM unnest(array_append(origin_ids, p_disposal_transaction_id)) id ORDER BY id));
  PERFORM 1 FROM investment_transaction event
   WHERE event.household_id = owner_household
     AND event.id = p_disposal_transaction_id FOR UPDATE;
  PERFORM 1 FROM investment_transaction event
   WHERE event.household_id = owner_household AND event.id = ANY(origin_ids)
   ORDER BY event.id FOR UPDATE;
  SELECT disposal.holding_id INTO target_holding
    FROM investment_transaction disposal
   WHERE disposal.household_id = owner_household AND disposal.id = p_disposal_transaction_id;
  IF target_holding IS NULL THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF EXISTS (
    SELECT FROM investment_transaction event
     WHERE event.household_id = owner_household
       AND (event.id = p_disposal_transaction_id OR event.id = ANY(origin_ids))
       AND EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.household_id = event.household_id
            AND correction.corrects_transaction_id = event.id)
  ) THEN
    RAISE EXCEPTION 'lot allocation requires active investment events'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_allocation_active_events';
  END IF;
  PERFORM 1 FROM holding
   WHERE household_id = owner_household AND id = target_holding FOR UPDATE;
  PERFORM 1 FROM acquisition_lot lot
   WHERE lot.household_id = owner_household AND lot.id = ANY(p_acquisition_lot_ids)
   ORDER BY lot.id FOR UPDATE;
  PERFORM 1 FROM investment_review_item review
   WHERE review.household_id = owner_household
     AND review.investment_transaction_id = p_disposal_transaction_id
   ORDER BY review.id FOR UPDATE;
END $$;
ALTER FUNCTION allocate_investment_lots(UUID, UUID[], NUMERIC[])
  RENAME TO allocate_investment_lots_v33;
REVOKE ALL ON FUNCTION allocate_investment_lots_v33(UUID, UUID[], NUMERIC[]) FROM authenticated;
REVOKE ALL ON FUNCTION allocate_investment_lots_v33(UUID, UUID[], NUMERIC[]) FROM PUBLIC;
CREATE FUNCTION allocate_investment_lots(
  p_disposal_transaction_id UUID,
  p_acquisition_lot_ids UUID[],
  p_allocated_units NUMERIC[]
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  PERFORM lock_investment_lot_allocation_state(
    p_disposal_transaction_id, p_acquisition_lot_ids);
  PERFORM allocate_investment_lots_v33(
    p_disposal_transaction_id, p_acquisition_lot_ids, p_allocated_units);
END $$;
CREATE FUNCTION confirm_investment_lot_allocation(
  p_disposal_transaction_id UUID, p_acquisition_lot_ids UUID[],
  p_allocated_units NUMERIC[], p_expected_state TEXT, p_idempotency_key UUID
) RETURNS TABLE (
  created BOOLEAN, realised_cost_native NUMERIC, realised_gain_native NUMERIC,
  remaining_basis_native NUMERIC, allocation_complete BOOLEAN)
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; actor_id UUID; expected_hash TEXT;
  prior investment_lot_allocation_decision%ROWTYPE; disposal investment_transaction%ROWTYPE;
  is_complete BOOLEAN; cost NUMERIC(20,4); gain NUMERIC(20,4); remaining NUMERIC(20,4);
  review_id UUID;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  actor_id := NULLIF(current_setting('app.user_id', true), '')::UUID;
  IF p_idempotency_key IS NULL OR p_expected_state !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'lot decision identity must be complete' USING ERRCODE = '22023';
  END IF;
  expected_hash := investment_lot_request_hash(
    p_disposal_transaction_id, p_acquisition_lot_ids, p_allocated_units, p_expected_state);
  PERFORM pg_advisory_xact_lock(hashtextextended(
    owner_household::TEXT || ':' || p_idempotency_key::TEXT, 0));
  SELECT decision.* INTO prior FROM investment_lot_allocation_decision decision
   WHERE decision.household_id = owner_household
     AND decision.idempotency_key = p_idempotency_key FOR UPDATE;
  IF FOUND THEN
    IF prior.request_hash <> expected_hash
       OR prior.disposal_transaction_id <> p_disposal_transaction_id THEN
      RAISE EXCEPTION 'lot allocation idempotency conflict' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT false, prior.realised_cost_native, prior.realised_gain_native,
      prior.remaining_basis_native, prior.allocation_complete;
    RETURN;
  END IF;

  PERFORM allocate_investment_lots(
    p_disposal_transaction_id, p_acquisition_lot_ids, p_allocated_units);
  SELECT event.* INTO STRICT disposal FROM investment_transaction event
   WHERE event.household_id = owner_household AND event.id = p_disposal_transaction_id;
  SELECT COALESCE(sum(allocation.allocated_units), 0) = abs(disposal.units)
    INTO is_complete FROM investment_lot_allocation allocation
   WHERE allocation.household_id = owner_household
     AND allocation.disposal_transaction_id = disposal.id;
  IF is_complete THEN
    SELECT sum(round(
      allocation.allocated_units * lot.cost_basis_native / lot.acquired_units, 4))
      INTO cost FROM investment_lot_allocation allocation
      JOIN acquisition_lot lot ON lot.household_id = allocation.household_id
                              AND lot.id = allocation.acquisition_lot_id
     WHERE allocation.household_id = owner_household
       AND allocation.disposal_transaction_id = disposal.id;
    gain := disposal.net_amount - cost;
    IF NOT EXISTS (
      SELECT FROM investment_transaction active_disposal
       WHERE active_disposal.household_id = owner_household
         AND active_disposal.holding_id = disposal.holding_id
         AND active_disposal.event_kind IN (
           'sale', 'redemption', 'maturity', 'switch_out',
           'systematic_transfer_out', 'platform_transfer_out')
         AND NOT EXISTS (SELECT FROM investment_transaction correction
           WHERE correction.household_id = active_disposal.household_id
             AND correction.corrects_transaction_id = active_disposal.id)
         AND COALESCE((SELECT sum(allocation.allocated_units)
           FROM investment_lot_allocation allocation
          WHERE allocation.household_id = active_disposal.household_id
            AND allocation.disposal_transaction_id = active_disposal.id), 0)
             <> abs(active_disposal.units)
    ) THEN
      SELECT COALESCE(sum(round(lot.cost_basis_native *
        (lot.acquired_units - COALESCE(active_allocation.units, 0)) / lot.acquired_units, 4)), 0)
        INTO remaining FROM acquisition_lot lot
        JOIN investment_transaction origin ON origin.household_id = lot.household_id
                                          AND origin.id = lot.origin_transaction_id
        LEFT JOIN (
          SELECT allocation.household_id, allocation.acquisition_lot_id,
                 sum(allocation.allocated_units) AS units
            FROM investment_lot_allocation allocation
            JOIN investment_transaction active_disposal
              ON active_disposal.household_id = allocation.household_id
             AND active_disposal.id = allocation.disposal_transaction_id
           WHERE NOT EXISTS (SELECT FROM investment_transaction correction
             WHERE correction.household_id = active_disposal.household_id
               AND correction.corrects_transaction_id = active_disposal.id)
           GROUP BY allocation.household_id, allocation.acquisition_lot_id
        ) active_allocation ON active_allocation.household_id = lot.household_id
                           AND active_allocation.acquisition_lot_id = lot.id
       WHERE lot.household_id = owner_household AND lot.holding_id = disposal.holding_id
         AND NOT EXISTS (SELECT FROM investment_transaction correction
           WHERE correction.household_id = origin.household_id
             AND correction.corrects_transaction_id = origin.id);
    END IF;
    SELECT review.id INTO review_id FROM investment_review_item review
     WHERE review.household_id = owner_household
       AND review.investment_transaction_id = disposal.id
       AND review.reason = 'incomplete_lot_allocation' AND review.status = 'open' FOR UPDATE;
    IF review_id IS NOT NULL THEN
      PERFORM resolve_investment_review_item(review_id, 'lot_allocated', disposal.id);
    END IF;
  END IF;
  INSERT INTO investment_lot_allocation_decision (
    household_id, idempotency_key, request_hash, disposal_transaction_id,
    realised_cost_native, realised_gain_native, remaining_basis_native,
    allocation_complete, decided_by)
  VALUES (owner_household, p_idempotency_key, expected_hash, disposal.id,
          cost, gain, remaining, is_complete, actor_id);
  RETURN QUERY SELECT true, cost, gain, remaining, is_complete;
END $$;
CREATE OR REPLACE FUNCTION guard_correction_unfunded_event() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE target_holding UUID;
BEGIN
  IF current_setting('oneledger.investment_event_append', true) = 'on'
     AND NEW.corrects_transaction_id IS NOT NULL THEN
    SELECT event.holding_id INTO target_holding FROM investment_transaction event
     WHERE event.household_id = NEW.household_id
       AND event.id = NEW.corrects_transaction_id FOR UPDATE;
    PERFORM 1 FROM holding
     WHERE household_id = NEW.household_id AND id = target_holding FOR UPDATE;
    PERFORM 1 FROM acquisition_lot lot
     WHERE lot.household_id = NEW.household_id
       AND lot.origin_transaction_id = NEW.corrects_transaction_id
     ORDER BY lot.id FOR UPDATE;
    PERFORM 1 FROM investment_review_item review
     WHERE review.household_id = NEW.household_id
       AND review.investment_transaction_id = NEW.corrects_transaction_id
     ORDER BY review.id FOR UPDATE;
    IF EXISTS (
      SELECT FROM investment_cash_allocation allocation
       WHERE allocation.household_id = NEW.household_id
         AND allocation.investment_transaction_id = NEW.corrects_transaction_id
    ) OR EXISTS (
      SELECT FROM investment_lot_allocation allocation
       WHERE allocation.household_id = NEW.household_id
         AND allocation.disposal_transaction_id = NEW.corrects_transaction_id
    ) OR EXISTS (
      SELECT FROM acquisition_lot lot
      JOIN investment_lot_allocation allocation
        ON allocation.household_id = lot.household_id
       AND allocation.acquisition_lot_id = lot.id
       WHERE lot.household_id = NEW.household_id
         AND lot.origin_transaction_id = NEW.corrects_transaction_id
    ) THEN
      RAISE EXCEPTION 'a funded investment event cannot be corrected'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_correction_requires_unfunded_event';
    END IF;
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION lock_investment_lot_allocation_state(UUID, UUID[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lock_investment_lot_allocation_state(UUID, UUID[]) TO authenticated;
REVOKE ALL ON FUNCTION lock_investment_event_mutations(UUID[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lock_investment_event_mutations(UUID[]) TO authenticated;
REVOKE ALL ON FUNCTION allocate_investment_lots(UUID, UUID[], NUMERIC[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION allocate_investment_lots(UUID, UUID[], NUMERIC[]) TO authenticated;
REVOKE ALL ON FUNCTION confirm_investment_lot_allocation(
  UUID, UUID[], NUMERIC[], TEXT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION confirm_investment_lot_allocation(
  UUID, UUID[], NUMERIC[], TEXT, UUID) TO authenticated;
REVOKE ALL ON FUNCTION preserve_investment_lot_decision() FROM PUBLIC;
