-- Cash-only history still needs a scoped holding without manufacturing a unit effect.
CREATE OR REPLACE FUNCTION refresh_holding_projection(p_holding_id UUID) RETURNS VOID
  LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE rebuilt_units NUMERIC(28,8); first_date DATE; last_date DATE;
BEGIN
  SELECT investment_units_from_events(p_holding_id),
         min(effect.effect_date), max(effect.effect_date)
    INTO rebuilt_units, first_date, last_date
    FROM investment_unit_effect effect
    JOIN investment_transaction event
      ON event.id = effect.investment_transaction_id
   WHERE effect.holding_id = p_holding_id
     AND NOT EXISTS (
       SELECT FROM investment_transaction correction
        WHERE correction.corrects_transaction_id = event.id);
  IF rebuilt_units < 0 THEN
    RAISE EXCEPTION 'investment events cannot produce negative holding units'
      USING ERRCODE = '23514', CONSTRAINT = 'holding_units_cannot_be_negative';
  END IF;
  IF first_date IS NULL THEN
    SELECT min(event.event_date), max(event.event_date)
      INTO first_date, last_date
      FROM investment_transaction event
     WHERE event.holding_id = p_holding_id
       AND NOT EXISTS (
         SELECT FROM investment_transaction correction
          WHERE correction.corrects_transaction_id = event.id);
  END IF;
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  UPDATE holding
     SET current_units = rebuilt_units,
         opened_on = first_date,
         closed_on = CASE WHEN rebuilt_units = 0 THEN last_date ELSE NULL END,
         updated_at = now()
   WHERE id = p_holding_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
END $$;

-- Extend the single owner-authorized append boundary to the evidenced cash lifecycle.
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
  IF p_event_date IS NULL OR p_event_kind IS NULL OR p_source_kind IS NULL THEN
    RAISE EXCEPTION 'event date, kind and source are required' USING ERRCODE = '23502';
  END IF;
  IF p_event_kind NOT IN (
    'purchase', 'recurring_purchase', 'sale', 'redemption', 'maturity',
    'cash_distribution', 'interest', 'reinvested_distribution', 'charge', 'withheld_tax'
  ) THEN
    RAISE EXCEPTION 'event kind % requires a later lifecycle operation', p_event_kind
      USING ERRCODE = '0A000';
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
        existing.units, existing.price, existing.gross_amount, existing.charges,
        existing.withheld_tax, existing.net_amount, existing.currency,
        existing.fx_rate_date, existing.fx_rate_to_base, existing.fx_rate_to_secondary,
        existing.net_amount_base, existing.net_amount_secondary, existing.source_kind,
        existing.corrects_transaction_id, existing.correction_reason)
       IS NOT DISTINCT FROM
       (p_account_id, p_security_id, p_event_date, p_event_kind,
        p_units, p_price, p_gross_amount, p_charges, p_withheld_tax, p_net_amount,
        p_currency, p_fx_rate_date, p_fx_rate_to_base, p_fx_rate_to_secondary,
        p_net_amount_base, p_net_amount_secondary, p_source_kind,
        p_corrects_transaction_id, p_correction_reason) THEN
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
    units, price, gross_amount, charges, withheld_tax, net_amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
    net_amount_base, net_amount_secondary,
    source_kind, source_contract, source_event_id, dedupe_version, dedupe_fingerprint,
    corrects_transaction_id, correction_reason
  ) VALUES (
    owner_household, p_account_id, holding_row.id, p_security_id, p_event_date, p_event_kind,
    p_units, p_price, p_gross_amount, p_charges, p_withheld_tax, p_net_amount, p_currency,
    p_fx_rate_date, p_fx_rate_to_base, p_fx_rate_to_secondary,
    p_net_amount_base, p_net_amount_secondary,
    p_source_kind, p_source_contract, p_source_event_id, p_dedupe_version,
    p_dedupe_fingerprint, p_corrects_transaction_id, p_correction_reason
  ) RETURNING id INTO new_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
  RETURN new_id;
END $$;
