-- Phase 1.6 Task 15.1: lot lineage read model, order guard and active-event capacity.
-- No operation yet creates lineage rows; V41/V42 append successor lots and paired legs.

-- A predecessor is household-scoped, not holding-scoped: a platform transfer's successor lives in
-- the destination holding, so this cannot reuse V33's (household_id, id, holding_id) uniqueness.
ALTER TABLE acquisition_lot
  ADD COLUMN predecessor_lot_id UUID,
  ADD CONSTRAINT uq_acquisition_lot_household_id UNIQUE (household_id, id);

ALTER TABLE acquisition_lot
  DROP CONSTRAINT uq_acquisition_lot_origin,
  ADD CONSTRAINT fk_acquisition_lot_predecessor_scope
    FOREIGN KEY (household_id, predecessor_lot_id)
    REFERENCES acquisition_lot(household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT uq_acquisition_lot_origin_predecessor
    UNIQUE NULLS NOT DISTINCT (origin_transaction_id, predecessor_lot_id);

ALTER TABLE investment_transaction
  ADD COLUMN counterpart_transaction_id UUID,
  ADD CONSTRAINT fk_investment_transaction_counterpart_scope
    FOREIGN KEY (household_id, counterpart_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT investment_event_counterpart_agrees CHECK (
    (counterpart_transaction_id IS NOT NULL) = (event_kind IN (
      'switch_in', 'systematic_transfer_in', 'platform_transfer_in')));

CREATE UNIQUE INDEX idx_investment_event_counterpart
  ON investment_transaction (counterpart_transaction_id)
  WHERE counterpart_transaction_id IS NOT NULL;

-- A holding's lineage is unavailable from the date of an active split with no successor lot,
-- i.e. one that committed only its exact unit effect under an incomplete_lot_allocation review.
CREATE FUNCTION investment_lot_lineage_available(p_holding_id UUID, p_as_of DATE) RETURNS BOOLEAN
  LANGUAGE sql STABLE SET search_path = public, pg_temp AS $$
  SELECT NOT EXISTS (
    SELECT FROM investment_transaction split_event
     WHERE split_event.holding_id = p_holding_id
       AND split_event.event_kind = 'split'
       AND split_event.event_date <= p_as_of
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = split_event.id)
       AND NOT EXISTS (
         SELECT FROM acquisition_lot successor
          WHERE successor.origin_transaction_id = split_event.id)
  )
$$;

-- Open lots as of a date: the tip of each lineage chain whose creating event is active and dated
-- on or before p_as_of, less units allocated to active disposals dated on or before p_as_of.
-- Basis follows the same round(basis * open / acquired, 4) rule used across the lot model, cast to
-- a wide intermediate scale first so PostgreSQL's division-scale heuristic cannot truncate
-- significant digits before the final round. A platform transfer's successor can live in a
-- different holding of the same household, so tip detection spans every lot in the household and
-- only the final holding_id = p_holding_id filter selects this holding's own open lots. Returns no
-- rows while investment_lot_lineage_available is false for this holding and date; callers must
-- check it explicitly rather than reading an empty result as "no open lots".
CREATE FUNCTION investment_open_lots(p_holding_id UUID, p_as_of DATE) RETURNS TABLE (
  acquisition_lot_id UUID, acquisition_date DATE,
  open_units NUMERIC(28,8), open_basis_native NUMERIC(20,4)
) LANGUAGE sql STABLE SET search_path = public, pg_temp AS $$
  WITH scope AS (
    SELECT household_id FROM holding WHERE id = p_holding_id
  ),
  active_lot AS (
    SELECT lot.id, lot.holding_id, lot.acquisition_date, lot.acquired_units, lot.cost_basis_native
      FROM acquisition_lot lot
      JOIN scope ON scope.household_id = lot.household_id
      JOIN investment_transaction origin
        ON origin.household_id = lot.household_id AND origin.id = lot.origin_transaction_id
     WHERE origin.event_date <= p_as_of
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = origin.id)
  ),
  tip_lot AS (
    SELECT a.* FROM active_lot a
     WHERE NOT EXISTS (
       SELECT FROM acquisition_lot successor
        WHERE successor.predecessor_lot_id = a.id
          AND EXISTS (SELECT FROM active_lot al WHERE al.id = successor.id))
  ),
  allocated AS (
    SELECT allocation.acquisition_lot_id, sum(allocation.allocated_units) AS units
      FROM investment_lot_allocation allocation
      JOIN investment_transaction disposal
        ON disposal.household_id = allocation.household_id
       AND disposal.id = allocation.disposal_transaction_id
     WHERE disposal.event_date <= p_as_of
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = disposal.id)
     GROUP BY allocation.acquisition_lot_id
  )
  SELECT tip.id, tip.acquisition_date,
         (tip.acquired_units - COALESCE(allocated.units, 0))::NUMERIC(28,8),
         round((tip.cost_basis_native
                * (tip.acquired_units - COALESCE(allocated.units, 0)))::NUMERIC(80,40)
               / tip.acquired_units, 4)::NUMERIC(20,4)
    FROM tip_lot tip
    LEFT JOIN allocated ON allocated.acquisition_lot_id = tip.id
   WHERE tip.holding_id = p_holding_id
     AND investment_lot_lineage_available(p_holding_id, p_as_of)
     AND (tip.acquired_units - COALESCE(allocated.units, 0)) > 0
$$;

-- Splits and platform transfers fix unit/lot history before their date. A new unit- or
-- lot-affecting row cannot predate an active lineage event in its holding, and a correction
-- cannot retarget an event a later active lineage event already fixed (excluding the event it
-- targets from both comparisons, since that event is still "active" during its own replacement's
-- insert). The cash-only exemption requires BOTH the new row and any corrected event to be
-- cash-only: a cash-only correction of a unit-bearing original must not silently bypass the guard.
CREATE FUNCTION guard_investment_unit_event_order() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE corrected_date DATE; corrected_units NUMERIC(28,8); corrected_kind investment_event_kind;
BEGIN
  IF NEW.corrects_transaction_id IS NOT NULL THEN
    SELECT event_date, units, event_kind
      INTO corrected_date, corrected_units, corrected_kind
      FROM investment_transaction
     WHERE household_id = NEW.household_id AND id = NEW.corrects_transaction_id;
  END IF;

  IF (NEW.units IS NULL AND NEW.event_kind <> 'split')
     AND (NEW.corrects_transaction_id IS NULL
          OR (corrected_units IS NULL AND corrected_kind <> 'split')) THEN
    RETURN NEW;
  END IF;

  IF EXISTS (
    SELECT FROM investment_transaction lineage
     WHERE lineage.household_id = NEW.household_id
       AND lineage.holding_id = NEW.holding_id
       AND lineage.event_kind IN ('split', 'platform_transfer_in', 'platform_transfer_out')
       AND lineage.event_date > NEW.event_date
       AND (NEW.corrects_transaction_id IS NULL OR lineage.id <> NEW.corrects_transaction_id)
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = lineage.id)
  ) THEN
    RAISE EXCEPTION 'investment event is dated before an active lineage event'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_unit_event_order';
  END IF;

  IF NEW.corrects_transaction_id IS NOT NULL AND corrected_date IS NOT NULL AND EXISTS (
      SELECT FROM investment_transaction lineage
       WHERE lineage.household_id = NEW.household_id
         AND lineage.holding_id = NEW.holding_id
         AND lineage.event_kind IN ('split', 'platform_transfer_in', 'platform_transfer_out')
         AND lineage.id <> NEW.corrects_transaction_id
         AND lineage.event_date >= corrected_date
         AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = lineage.id)
  ) THEN
    RAISE EXCEPTION 'correction targets an event an active lineage event already fixed'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_unit_event_order';
  END IF;

  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_transaction_order_guard
  BEFORE INSERT ON investment_transaction
  FOR EACH ROW EXECUTE FUNCTION guard_investment_unit_event_order();

-- Lot capacity counts only allocations from active (uncorrected) disposals; a successor lot
-- cannot fund a disposal dated before the lineage event that created it, and no lot may be
-- allocated for a disposal dated on or after a split whose lineage is unavailable.
CREATE OR REPLACE FUNCTION allocate_investment_lots_v33(
  p_disposal_transaction_id UUID,
  p_acquisition_lot_ids UUID[],
  p_allocated_units NUMERIC[]
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; actor_id UUID; item_count INTEGER; item_index INTEGER;
  disposal investment_transaction%ROWTYPE; source_lot acquisition_lot%ROWTYPE;
  lot_group RECORD;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  actor_id := NULLIF(current_setting('app.user_id', true), '')::UUID;
  item_count := cardinality(p_acquisition_lot_ids);
  IF item_count IS NULL OR item_count = 0 OR cardinality(p_allocated_units) <> item_count THEN
    RAISE EXCEPTION 'lot allocation arrays must be non-empty and equal length'
      USING ERRCODE = '22023';
  END IF;
  IF EXISTS (SELECT FROM unnest(p_acquisition_lot_ids) lot_id WHERE lot_id IS NULL)
     OR EXISTS (
       SELECT FROM unnest(p_acquisition_lot_ids) lot_id
        GROUP BY lot_id HAVING count(*) > 1
     )
     OR EXISTS (SELECT FROM unnest(p_allocated_units) units WHERE units IS NULL) THEN
    RAISE EXCEPTION 'lot allocation items must be complete and unique'
      USING ERRCODE = '22023';
  END IF;
  FOR item_index IN 1..item_count LOOP
    IF p_allocated_units[item_index] IS DISTINCT FROM round(p_allocated_units[item_index], 8) THEN
      RAISE EXCEPTION 'lot allocation units exceeds scale 8' USING ERRCODE = '22003';
    END IF;
    IF p_allocated_units[item_index] <= 0 THEN
      RAISE EXCEPTION 'lot allocation units must be positive'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_lot_allocation_units_are_positive';
    END IF;
  END LOOP;
  SELECT * INTO disposal FROM investment_transaction
   WHERE id = p_disposal_transaction_id AND household_id = owner_household FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
  IF disposal.event_kind NOT IN (
    'sale', 'redemption', 'maturity', 'switch_out', 'systematic_transfer_out',
    'platform_transfer_out'
  ) OR disposal.units >= 0 THEN
    RAISE EXCEPTION 'lot allocations require a unit disposal event'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_allocation_uses_disposal';
  END IF;
  IF NOT investment_lot_lineage_available(disposal.holding_id, disposal.event_date) THEN
    RAISE EXCEPTION 'lot allocation is unavailable while holding lineage is incomplete'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_allocation_requires_available_lineage';
  END IF;
  PERFORM 1 FROM acquisition_lot lot
   WHERE lot.id = ANY(p_acquisition_lot_ids)
     AND lot.household_id = owner_household ORDER BY lot.id FOR UPDATE;
  IF (SELECT count(DISTINCT lot.id) FROM acquisition_lot lot
       WHERE lot.id = ANY(p_acquisition_lot_ids)
         AND lot.household_id = owner_household)
     <> (SELECT count(DISTINCT id) FROM unnest(p_acquisition_lot_ids) id) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF EXISTS (
    SELECT FROM acquisition_lot lot
     WHERE lot.id = ANY(p_acquisition_lot_ids) AND lot.holding_id <> disposal.holding_id
  ) THEN
    RAISE EXCEPTION 'an acquisition lot must belong to the disposal holding'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_matches_disposal_holding';
  END IF;
  IF EXISTS (
    SELECT FROM acquisition_lot lot
     WHERE lot.id = ANY(p_acquisition_lot_ids)
       AND lot.acquisition_date > disposal.event_date
  ) THEN
    RAISE EXCEPTION 'an acquisition lot cannot postdate its disposal'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_precedes_disposal';
  END IF;
  IF EXISTS (
    SELECT FROM acquisition_lot lot
    JOIN investment_transaction origin
      ON origin.household_id = lot.household_id AND origin.id = lot.origin_transaction_id
     WHERE lot.id = ANY(p_acquisition_lot_ids)
       AND lot.predecessor_lot_id IS NOT NULL
       AND origin.event_date > disposal.event_date
  ) THEN
    RAISE EXCEPTION 'a successor lot cannot fund a disposal before its creating event'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_succeeds_creating_event';
  END IF;
  IF EXISTS (
    SELECT FROM acquisition_lot lot
    JOIN acquisition_lot successor ON successor.predecessor_lot_id = lot.id
    JOIN investment_transaction succ_origin
      ON succ_origin.household_id = successor.household_id
     AND succ_origin.id = successor.origin_transaction_id
     WHERE lot.id = ANY(p_acquisition_lot_ids)
       AND succ_origin.event_date <= disposal.event_date
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = succ_origin.id)
  ) THEN
    RAISE EXCEPTION 'a lot consumed by an active lineage event cannot fund a disposal'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_consumed_by_lineage';
  END IF;
  IF COALESCE((SELECT sum(allocated_units) FROM investment_lot_allocation
                WHERE disposal_transaction_id = disposal.id), 0)
     + (SELECT sum(units) FROM unnest(p_allocated_units) units) > abs(disposal.units) THEN
    RAISE EXCEPTION 'lot allocations cannot exceed disposal units'
      USING ERRCODE = '23514',
            CONSTRAINT = 'investment_disposal_units_are_not_overallocated';
  END IF;
  FOR lot_group IN
    SELECT lot_id, sum(units) AS allocated_total
      FROM unnest(p_acquisition_lot_ids, p_allocated_units) AS item(lot_id, units)
     GROUP BY lot_id ORDER BY lot_id
  LOOP
    SELECT * INTO STRICT source_lot FROM acquisition_lot WHERE id = lot_group.lot_id;
    IF lot_group.allocated_total + COALESCE((
      SELECT sum(allocation.allocated_units)
        FROM investment_lot_allocation allocation
        JOIN investment_transaction alloc_disposal
          ON alloc_disposal.household_id = allocation.household_id
         AND alloc_disposal.id = allocation.disposal_transaction_id
       WHERE allocation.acquisition_lot_id = source_lot.id
         AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = alloc_disposal.id)
    ), 0) > source_lot.acquired_units THEN
      RAISE EXCEPTION 'lot allocations cannot exceed acquired units'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_lot_units_are_not_overallocated';
    END IF;
  END LOOP;
  PERFORM set_config('oneledger.investment_lot_allocation_write', 'on', true);
  FOR item_index IN 1..item_count LOOP
    INSERT INTO investment_lot_allocation (
      household_id, holding_id, disposal_transaction_id, acquisition_lot_id,
      allocated_units, allocated_by
    ) VALUES (
      owner_household, disposal.holding_id, disposal.id,
      p_acquisition_lot_ids[item_index], p_allocated_units[item_index], actor_id
    );
  END LOOP;
  PERFORM set_config('oneledger.investment_lot_allocation_write', 'off', true);
END $$;

REVOKE ALL ON FUNCTION allocate_investment_lots_v33(UUID, UUID[], NUMERIC[]) FROM authenticated;
REVOKE ALL ON FUNCTION allocate_investment_lots_v33(UUID, UUID[], NUMERIC[]) FROM PUBLIC;

-- confirm_investment_lot_allocation now derives remaining basis from the active-lineage read
-- model, so it agrees with investment_open_lots and withholds it while lineage is unavailable.
CREATE OR REPLACE FUNCTION confirm_investment_lot_allocation(
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
    ) AND investment_lot_lineage_available(disposal.holding_id, 'infinity'::date) THEN
      -- Remaining basis is V39's unbounded current-state claim, not as-of the disposal date: a
      -- later acquisition in the same holding still contributes to it.
      SELECT COALESCE(sum(open_basis_native), 0) INTO remaining
        FROM investment_open_lots(disposal.holding_id, 'infinity'::date);
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

REVOKE ALL ON FUNCTION confirm_investment_lot_allocation(
  UUID, UUID[], NUMERIC[], TEXT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION confirm_investment_lot_allocation(
  UUID, UUID[], NUMERIC[], TEXT, UUID) TO authenticated;

-- A corrected split's or transfer out-leg's own predecessor consumption is expected and exempt;
-- the guard still refuses once that event's own successor lots are allocated or further
-- consumed by an active lineage event, matching the existing funded-disposal/origin checks.
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
    PERFORM 1 FROM acquisition_lot successor
     WHERE successor.household_id = NEW.household_id
       AND successor.predecessor_lot_id IN (
         SELECT lot.id FROM acquisition_lot lot
          WHERE lot.household_id = NEW.household_id
            AND lot.origin_transaction_id = NEW.corrects_transaction_id)
     ORDER BY successor.id FOR UPDATE;
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
    ) OR EXISTS (
      SELECT FROM acquisition_lot lot
      JOIN acquisition_lot successor ON successor.predecessor_lot_id = lot.id
      JOIN investment_transaction succ_origin
        ON succ_origin.household_id = successor.household_id
       AND succ_origin.id = successor.origin_transaction_id
       WHERE lot.household_id = NEW.household_id
         AND lot.origin_transaction_id = NEW.corrects_transaction_id
         AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = succ_origin.id)
    ) THEN
      RAISE EXCEPTION 'a funded investment event cannot be corrected'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_correction_requires_unfunded_event';
    END IF;
  END IF;
  RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION investment_lot_lineage_available(UUID, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION investment_lot_lineage_available(UUID, DATE) TO authenticated;
REVOKE ALL ON FUNCTION investment_open_lots(UUID, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION investment_open_lots(UUID, DATE) TO authenticated;
REVOKE ALL ON FUNCTION guard_investment_unit_event_order() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_correction_unfunded_event() FROM PUBLIC;
