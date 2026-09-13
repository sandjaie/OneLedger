-- Phase 1.6 Task 15.2: holding-level unit actions (split, bonus, rights, switch, systematic
-- transfer, platform transfer). No successor lot, lot selection or realised claim is created;
-- acquisition_lot.predecessor_lot_id stays null until a later task fills it in.

DO $$
BEGIN
  IF EXISTS (SELECT FROM investment_transaction WHERE event_kind = 'split') THEN
    RAISE EXCEPTION 'a split row already exists with the pre-Task-15.2 null-units shape'
      USING ERRCODE = '55000';
  END IF;
END $$;

-- The V37 append body becomes the shared, revoked core so append_investment_split and
-- append_investment_unit_transfer can insert their own event kinds through one boundary.
-- Ratio and counterpart join the exact-replay identity tuple so a retried split or transfer
-- leg compares its full recorded shape, not just the fields append_investment_transaction knew.
CREATE FUNCTION append_investment_event_core(
  p_account_id UUID,
  p_security_id UUID,
  p_event_date DATE,
  p_event_kind investment_event_kind,
  p_units NUMERIC,
  p_price NUMERIC,
  p_gross_amount NUMERIC,
  p_charges NUMERIC,
  p_withheld_tax NUMERIC,
  p_net_amount NUMERIC,
  p_currency CHAR(3),
  p_fx_rate_date DATE,
  p_fx_rate_to_base NUMERIC,
  p_fx_rate_to_secondary NUMERIC,
  p_net_amount_base NUMERIC,
  p_net_amount_secondary NUMERIC,
  p_source_kind investment_event_source,
  p_source_contract TEXT,
  p_source_event_id TEXT,
  p_dedupe_version TEXT,
  p_dedupe_fingerprint TEXT,
  p_corrects_transaction_id UUID,
  p_correction_reason TEXT,
  p_unit_ratio NUMERIC,
  p_counterpart_transaction_id UUID
) RETURNS UUID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; account_currency CHAR(3); account_opened DATE; account_closed DATE;
  security_household UUID; security_currency CHAR(3); holding_row holding%ROWTYPE;
  existing investment_transaction%ROWTYPE; corrected investment_transaction%ROWTYPE;
  new_id UUID;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();

  IF p_units IS DISTINCT FROM round(p_units, 8) THEN
    RAISE EXCEPTION 'units exceeds scale 8' USING ERRCODE = '22003';
  END IF;
  IF p_price IS DISTINCT FROM round(p_price, 6) THEN
    RAISE EXCEPTION 'price exceeds scale 6' USING ERRCODE = '22003';
  END IF;
  IF p_gross_amount IS DISTINCT FROM round(p_gross_amount, 4)
     OR p_charges IS DISTINCT FROM round(p_charges, 4)
     OR p_withheld_tax IS DISTINCT FROM round(p_withheld_tax, 4)
     OR p_net_amount IS DISTINCT FROM round(p_net_amount, 4)
     OR p_net_amount_base IS DISTINCT FROM round(p_net_amount_base, 4)
     OR p_net_amount_secondary IS DISTINCT FROM round(p_net_amount_secondary, 4) THEN
    RAISE EXCEPTION 'money exceeds scale 4' USING ERRCODE = '22003';
  END IF;
  IF p_fx_rate_to_base IS DISTINCT FROM round(p_fx_rate_to_base, 10)
     OR p_fx_rate_to_secondary IS DISTINCT FROM round(p_fx_rate_to_secondary, 10) THEN
    RAISE EXCEPTION 'FX rate exceeds scale 10' USING ERRCODE = '22003';
  END IF;
  IF p_unit_ratio IS DISTINCT FROM round(p_unit_ratio, 10) THEN
    RAISE EXCEPTION 'unit ratio exceeds scale 10' USING ERRCODE = '22003';
  END IF;
  IF p_event_date IS NULL OR p_event_kind IS NULL OR p_source_kind IS NULL THEN
    RAISE EXCEPTION 'event date, kind and source are required' USING ERRCODE = '23502';
  END IF;

  SELECT currency, opened_on, closed_on
    INTO account_currency, account_opened, account_closed
    FROM account
   WHERE id = p_account_id AND household_id = owner_household
     AND account_class = 'asset'
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF (account_opened IS NOT NULL AND p_event_date < account_opened)
     OR (account_closed IS NOT NULL AND p_event_date > account_closed)
     OR NOT EXISTS (
       SELECT FROM account_valuation_period
        WHERE account_id = p_account_id AND valuation_mode = 'holdings'
          AND p_event_date >= effective_from
          AND (effective_to IS NULL OR p_event_date <= effective_to)
     ) THEN
    RAISE EXCEPTION 'investment event date is outside a holdings valuation period'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_event_uses_holdings_mode';
  END IF;

  SELECT household_id, currency INTO security_household, security_currency
    FROM security WHERE id = p_security_id FOR KEY SHARE;
  IF NOT FOUND OR (
    security_household IS NOT NULL AND security_household <> owner_household
  ) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF rtrim(p_currency) <> rtrim(security_currency) THEN
    RAISE EXCEPTION 'investment event currency must match its security'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_event_currency_matches_security';
  END IF;

  SELECT * INTO holding_row FROM holding
   WHERE household_id = owner_household AND account_id = p_account_id
     AND security_id = p_security_id
   FOR UPDATE;
  IF NOT FOUND THEN
    PERFORM set_config('oneledger.investment_event_append', 'on', true);
    INSERT INTO holding (
      household_id, account_id, security_id, current_units, opened_on, closed_on
    ) VALUES (
      owner_household, p_account_id, p_security_id, 0, p_event_date, p_event_date
    ) RETURNING * INTO holding_row;
    PERFORM set_config('oneledger.investment_event_append', 'off', true);
  END IF;

  IF p_source_event_id IS NOT NULL THEN
    SELECT * INTO existing FROM investment_transaction
     WHERE household_id = owner_household AND source_contract = p_source_contract
       AND source_event_id = p_source_event_id;
  ELSIF p_dedupe_fingerprint IS NOT NULL THEN
    SELECT * INTO existing FROM investment_transaction
     WHERE household_id = owner_household AND source_contract = p_source_contract
       AND dedupe_version = p_dedupe_version
       AND dedupe_fingerprint = p_dedupe_fingerprint;
  END IF;
  IF FOUND THEN
    IF (existing.account_id, existing.security_id, existing.event_date, existing.event_kind,
        existing.units, existing.unit_ratio, existing.price, existing.gross_amount,
        existing.charges, existing.withheld_tax, existing.net_amount, existing.currency,
        existing.fx_rate_date, existing.fx_rate_to_base, existing.fx_rate_to_secondary,
        existing.net_amount_base, existing.net_amount_secondary, existing.source_kind,
        existing.corrects_transaction_id, existing.correction_reason,
        existing.counterpart_transaction_id)
       IS NOT DISTINCT FROM
       (p_account_id, p_security_id, p_event_date, p_event_kind,
        p_units, p_unit_ratio, p_price, p_gross_amount, p_charges, p_withheld_tax, p_net_amount,
        p_currency, p_fx_rate_date, p_fx_rate_to_base, p_fx_rate_to_secondary,
        p_net_amount_base, p_net_amount_secondary, p_source_kind,
        p_corrects_transaction_id, p_correction_reason, p_counterpart_transaction_id) THEN
      RETURN existing.id;
    END IF;
    RAISE EXCEPTION 'conflicting investment event uses an existing dedupe identity'
      USING ERRCODE = '23505';
  END IF;

  IF p_corrects_transaction_id IS NOT NULL THEN
    SELECT * INTO corrected FROM investment_transaction
     WHERE id = p_corrects_transaction_id AND household_id = owner_household
     FOR KEY SHARE;
    IF NOT FOUND OR corrected.account_id <> p_account_id
       OR corrected.holding_id <> holding_row.id
       OR corrected.security_id <> p_security_id THEN
      RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
    END IF;
    IF p_source_kind <> 'owner_entered' OR p_correction_reason IS NULL THEN
      RAISE EXCEPTION 'corrections require exact owner-entered provenance'
        USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
      SELECT FROM investment_transaction
       WHERE corrects_transaction_id = p_corrects_transaction_id
    ) THEN
      RAISE EXCEPTION 'an investment event already has a correction'
        USING ERRCODE = '23505';
    END IF;
  END IF;

  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  INSERT INTO investment_transaction (
    household_id, account_id, holding_id, security_id, event_date, event_kind,
    units, unit_ratio, price, gross_amount, charges, withheld_tax, net_amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
    net_amount_base, net_amount_secondary,
    source_kind, source_contract, source_event_id, dedupe_version, dedupe_fingerprint,
    corrects_transaction_id, correction_reason, counterpart_transaction_id
  ) VALUES (
    owner_household, p_account_id, holding_row.id, p_security_id, p_event_date, p_event_kind,
    p_units, p_unit_ratio, p_price, p_gross_amount, p_charges, p_withheld_tax, p_net_amount,
    p_currency, p_fx_rate_date, p_fx_rate_to_base, p_fx_rate_to_secondary,
    p_net_amount_base, p_net_amount_secondary,
    p_source_kind, p_source_contract, p_source_event_id, p_dedupe_version,
    p_dedupe_fingerprint, p_corrects_transaction_id, p_correction_reason,
    p_counterpart_transaction_id
  ) RETURNING id INTO new_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
  RETURN new_id;
END $$;

REVOKE ALL ON FUNCTION append_investment_event_core(
  UUID, UUID, DATE, investment_event_kind, NUMERIC, NUMERIC, NUMERIC, NUMERIC, NUMERIC, NUMERIC,
  CHAR, DATE, NUMERIC, NUMERIC, NUMERIC, NUMERIC, investment_event_source, TEXT, TEXT, TEXT, TEXT,
  UUID, TEXT, NUMERIC, UUID) FROM PUBLIC;

-- The public boundary keeps its unchanged 23-argument shape and now also admits bonus and
-- rights; split and the six paired transfer kinds still require the dedicated operations below.
CREATE OR REPLACE FUNCTION append_investment_transaction(
  p_account_id UUID,
  p_security_id UUID,
  p_event_date DATE,
  p_event_kind investment_event_kind,
  p_units NUMERIC,
  p_price NUMERIC,
  p_gross_amount NUMERIC,
  p_charges NUMERIC,
  p_withheld_tax NUMERIC,
  p_net_amount NUMERIC,
  p_currency CHAR(3),
  p_fx_rate_date DATE,
  p_fx_rate_to_base NUMERIC,
  p_fx_rate_to_secondary NUMERIC,
  p_net_amount_base NUMERIC,
  p_net_amount_secondary NUMERIC,
  p_source_kind investment_event_source,
  p_source_contract TEXT,
  p_source_event_id TEXT,
  p_dedupe_version TEXT,
  p_dedupe_fingerprint TEXT,
  p_corrects_transaction_id UUID,
  p_correction_reason TEXT
) RETURNS UUID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF p_event_kind NOT IN (
    'purchase', 'recurring_purchase', 'sale', 'redemption', 'maturity',
    'cash_distribution', 'interest', 'reinvested_distribution', 'charge', 'withheld_tax',
    'bonus', 'rights'
  ) THEN
    RAISE EXCEPTION 'event kind % requires a later lifecycle operation', p_event_kind
      USING ERRCODE = '0A000';
  END IF;
  RETURN append_investment_event_core(
    p_account_id, p_security_id, p_event_date, p_event_kind, p_units, p_price, p_gross_amount,
    p_charges, p_withheld_tax, p_net_amount, p_currency, p_fx_rate_date, p_fx_rate_to_base,
    p_fx_rate_to_secondary, p_net_amount_base, p_net_amount_secondary, p_source_kind,
    p_source_contract, p_source_event_id, p_dedupe_version, p_dedupe_fingerprint,
    p_corrects_transaction_id, p_correction_reason, NULL, NULL);
END $$;

-- A split now carries its own exact holding-level unit effect instead of a null placeholder.
ALTER TABLE investment_transaction DROP CONSTRAINT investment_event_kind_fields_agree;
ALTER TABLE investment_transaction ADD CONSTRAINT investment_event_kind_fields_agree CHECK (
  CASE
    WHEN event_kind IN ('purchase', 'recurring_purchase', 'rights') THEN
      units > 0 AND unit_ratio IS NULL AND price > 0 AND gross_amount > 0
      AND net_amount = -(gross_amount + charges + withheld_tax)
    WHEN event_kind IN ('sale', 'redemption', 'maturity') THEN
      units < 0 AND unit_ratio IS NULL AND price > 0 AND gross_amount > 0
      AND net_amount = gross_amount - charges - withheld_tax AND net_amount > 0
    WHEN event_kind IN ('cash_distribution', 'interest') THEN
      units IS NULL AND unit_ratio IS NULL AND price IS NULL AND gross_amount > 0
      AND net_amount = gross_amount - charges - withheld_tax AND net_amount > 0
    WHEN event_kind = 'reinvested_distribution' THEN
      units > 0 AND unit_ratio IS NULL AND price > 0 AND gross_amount > 0
      AND charges = 0 AND net_amount = 0
    WHEN event_kind = 'charge' THEN
      units IS NULL AND unit_ratio IS NULL AND price IS NULL AND gross_amount IS NULL
      AND charges > 0 AND withheld_tax = 0 AND net_amount = -charges
    WHEN event_kind = 'withheld_tax' THEN
      units IS NULL AND unit_ratio IS NULL AND price IS NULL AND gross_amount IS NULL
      AND charges = 0 AND withheld_tax > 0 AND net_amount = -withheld_tax
    WHEN event_kind IN (
      'switch_in', 'systematic_transfer_in'
    ) THEN
      units > 0 AND unit_ratio IS NULL AND price > 0 AND gross_amount > 0
      AND charges = 0 AND withheld_tax = 0 AND net_amount = 0
    WHEN event_kind IN (
      'switch_out', 'systematic_transfer_out'
    ) THEN
      units < 0 AND unit_ratio IS NULL AND price > 0 AND gross_amount > 0
      AND charges = 0 AND withheld_tax = 0 AND net_amount = 0
    WHEN event_kind = 'split' THEN
      units IS NOT NULL AND units <> 0 AND unit_ratio IS NOT NULL
      AND price IS NULL AND gross_amount IS NULL
      AND charges = 0 AND withheld_tax = 0 AND net_amount = 0
    WHEN event_kind = 'bonus' THEN
      units > 0 AND unit_ratio IS NULL AND price IS NULL AND gross_amount IS NULL
      AND charges = 0 AND withheld_tax = 0 AND net_amount = 0
    WHEN event_kind = 'platform_transfer_in' THEN
      units > 0 AND unit_ratio IS NULL AND price IS NULL AND gross_amount IS NULL
      AND charges = 0 AND withheld_tax = 0 AND net_amount = 0
    WHEN event_kind = 'platform_transfer_out' THEN
      units < 0 AND unit_ratio IS NULL AND price IS NULL AND gross_amount IS NULL
      AND charges = 0 AND withheld_tax = 0 AND net_amount = 0
    ELSE false
  END
);

-- Owner-only holding-level split: exact replay returns first, then the holding is locked, the
-- split must be the holding's latest unit-affecting event (or the one it corrects), and the
-- written unit effect is holding units excluding any corrected split, times (ratio - 1), exact
-- at scale 8. A split creates no lot.
CREATE FUNCTION append_investment_split(
  p_account_id UUID,
  p_security_id UUID,
  p_event_date DATE,
  p_unit_ratio NUMERIC,
  p_source_kind investment_event_source,
  p_source_contract TEXT,
  p_source_event_id TEXT,
  p_dedupe_version TEXT,
  p_dedupe_fingerprint TEXT,
  p_corrects_transaction_id UUID,
  p_correction_reason TEXT
) RETURNS UUID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; security_currency CHAR(3); holding_row holding%ROWTYPE;
  existing investment_transaction%ROWTYPE; corrected investment_transaction%ROWTYPE;
  baseline_units NUMERIC(28,8); computed_units NUMERIC; new_id UUID; anchor_date DATE;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();

  IF p_unit_ratio IS DISTINCT FROM round(p_unit_ratio, 10) THEN
    RAISE EXCEPTION 'unit ratio exceeds scale 10' USING ERRCODE = '22003';
  END IF;
  IF p_event_date IS NULL OR p_unit_ratio IS NULL OR p_unit_ratio <= 0 OR p_unit_ratio = 1
     OR p_source_kind IS NULL THEN
    RAISE EXCEPTION 'split arguments must be complete and valid' USING ERRCODE = '22023';
  END IF;

  SELECT h.* INTO holding_row FROM holding h
    JOIN account a ON a.household_id = h.household_id AND a.id = h.account_id
   WHERE h.household_id = owner_household AND h.account_id = p_account_id
     AND h.security_id = p_security_id AND a.account_class = 'asset'
   FOR UPDATE OF h;
  IF NOT FOUND THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;

  IF p_source_event_id IS NOT NULL THEN
    SELECT * INTO existing FROM investment_transaction
     WHERE household_id = owner_household AND source_contract = p_source_contract
       AND source_event_id = p_source_event_id;
  ELSIF p_dedupe_fingerprint IS NOT NULL THEN
    SELECT * INTO existing FROM investment_transaction
     WHERE household_id = owner_household AND source_contract = p_source_contract
       AND dedupe_version = p_dedupe_version AND dedupe_fingerprint = p_dedupe_fingerprint;
  END IF;
  IF FOUND THEN
    IF existing.account_id = p_account_id AND existing.security_id = p_security_id
       AND existing.event_date = p_event_date AND existing.event_kind = 'split'
       AND existing.unit_ratio = p_unit_ratio
       AND existing.corrects_transaction_id IS NOT DISTINCT FROM p_corrects_transaction_id
       AND existing.correction_reason IS NOT DISTINCT FROM p_correction_reason THEN
      RETURN existing.id;
    END IF;
    RAISE EXCEPTION 'conflicting investment event uses an existing dedupe identity'
      USING ERRCODE = '23505';
  END IF;

  IF p_corrects_transaction_id IS NOT NULL THEN
    SELECT * INTO corrected FROM investment_transaction
     WHERE id = p_corrects_transaction_id AND household_id = owner_household
       AND event_kind = 'split' FOR KEY SHARE;
    IF NOT FOUND OR corrected.account_id <> p_account_id
       OR corrected.holding_id <> holding_row.id OR corrected.security_id <> p_security_id THEN
      RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
    END IF;
    IF p_source_kind <> 'owner_entered' OR p_correction_reason IS NULL THEN
      RAISE EXCEPTION 'corrections require exact owner-entered provenance'
        USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
      SELECT FROM investment_transaction WHERE corrects_transaction_id = p_corrects_transaction_id
    ) THEN
      RAISE EXCEPTION 'an investment event already has a correction' USING ERRCODE = '23505';
    END IF;
  END IF;

  -- A correction can move the split's date in either direction, so "latest" must be checked
  -- against whichever of the new and the original date comes first: an event skipped over by
  -- moving the split earlier is exactly as disqualifying as one already after it. A same-day
  -- event committed after the corrected split (created_at, not event_date, breaks the tie) is
  -- also disqualifying, since the correction would otherwise silently reorder history that was
  -- already committed against the original split's position.
  anchor_date := CASE WHEN p_corrects_transaction_id IS NULL THEN p_event_date
                       ELSE LEAST(p_event_date, corrected.event_date) END;
  IF EXISTS (
    SELECT FROM investment_transaction later
     WHERE later.household_id = owner_household AND later.holding_id = holding_row.id
       AND later.units IS NOT NULL
       AND (p_corrects_transaction_id IS NULL OR later.id <> p_corrects_transaction_id)
       AND NOT EXISTS (
         SELECT FROM investment_transaction c WHERE c.corrects_transaction_id = later.id)
       AND (
         later.event_date > anchor_date
         OR (p_corrects_transaction_id IS NOT NULL AND later.event_date = anchor_date
             AND later.created_at > corrected.created_at)
       )
  ) THEN
    RAISE EXCEPTION 'a split must be the holding''s latest unit-affecting event'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_split_is_latest';
  END IF;

  baseline_units := holding_row.current_units - COALESCE(corrected.units, 0);
  -- Defense in depth: the corrected split's own recorded effect must still be exactly
  -- reproducible from today's baseline and its original ratio. An intervening active event the
  -- order check above did not catch would desynchronize this product from the retained fact.
  IF p_corrects_transaction_id IS NOT NULL
     AND baseline_units * (corrected.unit_ratio - 1) IS DISTINCT FROM corrected.units THEN
    RAISE EXCEPTION 'a split must be the holding''s latest unit-affecting event'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_split_is_latest';
  END IF;

  computed_units := baseline_units * (p_unit_ratio - 1);
  IF computed_units IS DISTINCT FROM round(computed_units, 8) THEN
    RAISE EXCEPTION 'split unit effect is not exact at scale 8'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_split_units_are_exact';
  END IF;

  SELECT currency INTO security_currency FROM security WHERE id = p_security_id;
  new_id := append_investment_event_core(
    p_account_id, p_security_id, p_event_date, 'split'::investment_event_kind,
    round(computed_units, 8),
    NULL, NULL, 0, 0, 0, security_currency,
    NULL, NULL, NULL, NULL, NULL,
    p_source_kind, p_source_contract, p_source_event_id, p_dedupe_version, p_dedupe_fingerprint,
    p_corrects_transaction_id, p_correction_reason, p_unit_ratio, NULL);
  RETURN new_id;
END $$;

REVOKE ALL ON FUNCTION append_investment_split(
  UUID, UUID, DATE, NUMERIC, investment_event_source, TEXT, TEXT, TEXT, TEXT, UUID, TEXT)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION append_investment_split(
  UUID, UUID, DATE, NUMERIC, investment_event_source, TEXT, TEXT, TEXT, TEXT, UUID, TEXT)
  TO authenticated;

-- Owner-only paired switch/systematic-transfer/platform-transfer: an exact pair replay returns
-- both ids first; both accounts lock in UUID order; the out leg writes then the in leg names it
-- as counterpart, or neither writes. Switch and systematic legs share an account, differ in
-- security and carry equal gross value; platform legs share a security, differ in account and
-- carry opposite units. Charges and withheld tax have no field here, so an exit load or stamp
-- duty on a switch fails closed by construction rather than by a runtime check.
CREATE FUNCTION append_investment_unit_transfer(
  p_out_kind investment_event_kind,
  p_event_date DATE,
  p_out_account_id UUID,
  p_out_security_id UUID,
  p_out_units NUMERIC,
  p_out_price NUMERIC,
  p_out_gross_amount NUMERIC,
  p_in_account_id UUID,
  p_in_security_id UUID,
  p_in_units NUMERIC,
  p_in_price NUMERIC,
  p_in_gross_amount NUMERIC,
  p_currency CHAR(3),
  p_source_kind investment_event_source,
  p_source_contract TEXT,
  p_out_source_event_id TEXT,
  p_out_dedupe_version TEXT,
  p_out_dedupe_fingerprint TEXT,
  p_in_source_event_id TEXT,
  p_in_dedupe_version TEXT,
  p_in_dedupe_fingerprint TEXT,
  p_corrects_out_transaction_id UUID,
  p_correction_reason TEXT
) RETURNS TABLE (out_event_id UUID, in_event_id UUID)
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; in_kind investment_event_kind; account_ids UUID[];
  existing_out investment_transaction%ROWTYPE; existing_in investment_transaction%ROWTYPE;
  corrected_out investment_transaction%ROWTYPE; corrected_in investment_transaction%ROWTYPE;
  new_out_id UUID; new_in_id UUID; pair_agrees BOOLEAN;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();

  IF p_out_kind = 'switch_out' THEN in_kind := 'switch_in';
  ELSIF p_out_kind = 'systematic_transfer_out' THEN in_kind := 'systematic_transfer_in';
  ELSIF p_out_kind = 'platform_transfer_out' THEN in_kind := 'platform_transfer_in';
  ELSE
    RAISE EXCEPTION 'unit transfer requires a paired out leg kind' USING ERRCODE = '22023';
  END IF;
  IF p_event_date IS NULL OR p_source_kind IS NULL OR p_currency IS NULL
     OR p_out_account_id IS NULL OR p_out_security_id IS NULL
     OR p_in_account_id IS NULL OR p_in_security_id IS NULL THEN
    RAISE EXCEPTION 'unit transfer arguments must be complete' USING ERRCODE = '23502';
  END IF;

  account_ids := ARRAY(
    SELECT DISTINCT id FROM unnest(ARRAY[p_out_account_id, p_in_account_id]) id ORDER BY id);
  PERFORM 1 FROM account
   WHERE household_id = owner_household AND id = ANY(account_ids) AND account_class = 'asset'
   ORDER BY id FOR UPDATE;
  IF (SELECT count(*) FROM account
       WHERE household_id = owner_household AND id = ANY(account_ids)
         AND account_class = 'asset') <> cardinality(account_ids) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;

  IF p_out_source_event_id IS NOT NULL THEN
    SELECT * INTO existing_out FROM investment_transaction
     WHERE household_id = owner_household AND source_contract = p_source_contract
       AND source_event_id = p_out_source_event_id;
  ELSIF p_out_dedupe_fingerprint IS NOT NULL THEN
    SELECT * INTO existing_out FROM investment_transaction
     WHERE household_id = owner_household AND source_contract = p_source_contract
       AND dedupe_version = p_out_dedupe_version
       AND dedupe_fingerprint = p_out_dedupe_fingerprint;
  END IF;
  IF FOUND THEN
    SELECT * INTO existing_in FROM investment_transaction
     WHERE household_id = owner_household AND counterpart_transaction_id = existing_out.id;
    IF FOUND
       AND existing_out.account_id = p_out_account_id
       AND existing_out.security_id = p_out_security_id
       AND existing_out.event_date = p_event_date AND existing_out.event_kind = p_out_kind
       AND existing_out.units = p_out_units
       AND existing_out.price IS NOT DISTINCT FROM p_out_price
       AND existing_out.gross_amount IS NOT DISTINCT FROM p_out_gross_amount
       AND rtrim(existing_out.currency) = rtrim(p_currency)
       AND existing_out.corrects_transaction_id IS NOT DISTINCT FROM p_corrects_out_transaction_id
       AND existing_out.correction_reason IS NOT DISTINCT FROM p_correction_reason
       AND existing_in.account_id = p_in_account_id
       AND existing_in.security_id = p_in_security_id
       AND existing_in.event_date = p_event_date AND existing_in.event_kind = in_kind
       AND existing_in.units = p_in_units
       AND existing_in.price IS NOT DISTINCT FROM p_in_price
       AND existing_in.gross_amount IS NOT DISTINCT FROM p_in_gross_amount THEN
      RETURN QUERY SELECT existing_out.id, existing_in.id;
      RETURN;
    END IF;
    RAISE EXCEPTION 'conflicting investment event uses an existing dedupe identity'
      USING ERRCODE = '23505';
  END IF;

  pair_agrees := (
    p_out_kind IN ('switch_out', 'systematic_transfer_out')
    AND p_out_account_id = p_in_account_id AND p_out_security_id <> p_in_security_id
    AND p_out_gross_amount IS NOT NULL AND p_out_gross_amount = p_in_gross_amount
  ) OR (
    p_out_kind = 'platform_transfer_out'
    AND p_out_security_id = p_in_security_id AND p_out_account_id <> p_in_account_id
    AND p_out_units IS NOT NULL AND p_in_units IS NOT NULL AND p_out_units = -p_in_units
  );
  IF NOT pair_agrees THEN
    RAISE EXCEPTION 'unit transfer pair does not agree on account, security, units or value'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_unit_transfer_pair_agrees';
  END IF;

  IF p_corrects_out_transaction_id IS NOT NULL THEN
    SELECT * INTO corrected_out FROM investment_transaction
     WHERE id = p_corrects_out_transaction_id AND household_id = owner_household
       AND event_kind = p_out_kind FOR KEY SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
    SELECT * INTO corrected_in FROM investment_transaction
     WHERE household_id = owner_household AND counterpart_transaction_id = corrected_out.id
     FOR KEY SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501'; END IF;
    IF p_source_kind <> 'owner_entered' OR p_correction_reason IS NULL THEN
      RAISE EXCEPTION 'corrections require exact owner-entered provenance'
        USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
      SELECT FROM investment_transaction
       WHERE corrects_transaction_id IN (corrected_out.id, corrected_in.id)
    ) THEN
      RAISE EXCEPTION 'an investment event already has a correction' USING ERRCODE = '23505';
    END IF;
  END IF;

  new_out_id := append_investment_event_core(
    p_out_account_id, p_out_security_id, p_event_date, p_out_kind, p_out_units,
    p_out_price, p_out_gross_amount, 0, 0, 0, p_currency,
    NULL, NULL, NULL, NULL, NULL,
    p_source_kind, p_source_contract, p_out_source_event_id, p_out_dedupe_version,
    p_out_dedupe_fingerprint, p_corrects_out_transaction_id, p_correction_reason, NULL, NULL);
  new_in_id := append_investment_event_core(
    p_in_account_id, p_in_security_id, p_event_date, in_kind, p_in_units,
    p_in_price, p_in_gross_amount, 0, 0, 0, p_currency,
    NULL, NULL, NULL, NULL, NULL,
    p_source_kind, p_source_contract, p_in_source_event_id, p_in_dedupe_version,
    p_in_dedupe_fingerprint,
    CASE WHEN p_corrects_out_transaction_id IS NULL THEN NULL ELSE corrected_in.id END,
    p_correction_reason, NULL, new_out_id);
  RETURN QUERY SELECT new_out_id, new_in_id;
END $$;

REVOKE ALL ON FUNCTION append_investment_unit_transfer(
  investment_event_kind, DATE, UUID, UUID, NUMERIC, NUMERIC, NUMERIC, UUID, UUID, NUMERIC,
  NUMERIC, NUMERIC, CHAR, investment_event_source, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
  UUID, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION append_investment_unit_transfer(
  investment_event_kind, DATE, UUID, UUID, NUMERIC, NUMERIC, NUMERIC, UUID, UUID, NUMERIC,
  NUMERIC, NUMERIC, CHAR, investment_event_source, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
  UUID, TEXT) TO authenticated;

-- Lineage stays available across a split whose successor lot exists (Task 15.1/15.4 chains); it
-- is unavailable from the date of a split with none, and now also from the date of an active
-- switch, systematic transfer or platform transfer leg, so checkpoint cost withholding
-- (staging.py) and the allocation guard below agree on exactly the same holdings and dates.
CREATE OR REPLACE FUNCTION investment_lot_lineage_available(p_holding_id UUID, p_as_of DATE)
  RETURNS BOOLEAN
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
  ) AND NOT EXISTS (
    SELECT FROM investment_transaction leg_event
     WHERE leg_event.holding_id = p_holding_id
       AND leg_event.event_kind IN (
         'switch_in', 'switch_out', 'systematic_transfer_in', 'systematic_transfer_out',
         'platform_transfer_in', 'platform_transfer_out')
       AND leg_event.event_date <= p_as_of
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = leg_event.id)
  )
$$;

-- A disposal cannot be lot-allocated once it is itself a switch/systematic/platform leg, or once
-- lineage is unavailable as of its date: the corporate action is holding-level and withholds
-- per-lot realised claims. allocate_investment_lots_v33 already refuses the second case (as
-- investment_lot_allocation_requires_available_lineage) before any insert is attempted; this
-- trigger is the same invariant enforced at the table itself, reachable by any write path.
CREATE FUNCTION guard_investment_lot_allocation_requires_cash_disposal() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE disposal_kind investment_event_kind; disposal_date DATE; disposal_holding UUID;
BEGIN
  SELECT event_kind, event_date, holding_id
    INTO disposal_kind, disposal_date, disposal_holding
    FROM investment_transaction
   WHERE household_id = NEW.household_id AND id = NEW.disposal_transaction_id;
  IF disposal_kind IN ('switch_out', 'systematic_transfer_out', 'platform_transfer_out')
     OR NOT investment_lot_lineage_available(disposal_holding, disposal_date) THEN
    RAISE EXCEPTION 'lot allocation requires an ordinary cash disposal unaffected by a corporate action'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_lot_allocation_requires_cash_disposal';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_lot_allocation_requires_cash_disposal
  BEFORE INSERT ON investment_lot_allocation
  FOR EACH ROW EXECUTE FUNCTION guard_investment_lot_allocation_requires_cash_disposal();

REVOKE ALL ON FUNCTION guard_investment_lot_allocation_requires_cash_disposal() FROM PUBLIC;
