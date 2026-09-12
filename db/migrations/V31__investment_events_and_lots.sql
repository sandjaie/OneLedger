-- Phase 1.6: immutable investment events, rebuildable holdings and acquisition-origin lots.
-- All writes enter through append_investment_transaction; projections are never owner-edited.

CREATE TYPE investment_event_kind AS ENUM (
  'purchase', 'recurring_purchase', 'sale', 'redemption', 'maturity',
  'cash_distribution', 'interest', 'reinvested_distribution',
  'charge', 'withheld_tax',
  'switch_in', 'switch_out', 'systematic_transfer_in', 'systematic_transfer_out',
  'split', 'bonus', 'rights', 'platform_transfer_in', 'platform_transfer_out'
);

CREATE TYPE investment_event_source AS ENUM ('statement', 'owner_entered');

CREATE TABLE holding (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id  UUID NOT NULL REFERENCES household(id),
  account_id    UUID NOT NULL,
  security_id   UUID NOT NULL REFERENCES security(id) ON DELETE RESTRICT,
  current_units NUMERIC(28,8) NOT NULL DEFAULT 0,
  opened_on     DATE NOT NULL,
  closed_on     DATE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_holding_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_holding_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_holding_event_scope UNIQUE (
    household_id, id, account_id, security_id),
  CONSTRAINT uq_holding_account_security UNIQUE (
    household_id, account_id, security_id),
  CONSTRAINT holding_units_are_nonnegative CHECK (current_units >= 0),
  CONSTRAINT holding_lifecycle_agrees CHECK (
    (current_units > 0 AND closed_on IS NULL)
    OR (current_units = 0 AND closed_on IS NOT NULL AND closed_on >= opened_on))
);

CREATE INDEX idx_holding_scope
  ON holding (household_id, account_id, closed_on, security_id);

CREATE FUNCTION guard_holding_write() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE security_household UUID;
BEGIN
  IF current_setting('oneledger.investment_event_append', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'holding projection is derived from immutable investment events'
      USING ERRCODE = '55000';
  END IF;
  IF TG_OP = 'UPDATE' AND (
    NEW.household_id IS DISTINCT FROM OLD.household_id
    OR NEW.account_id IS DISTINCT FROM OLD.account_id
    OR NEW.security_id IS DISTINCT FROM OLD.security_id
    OR NEW.created_at IS DISTINCT FROM OLD.created_at
  ) THEN
    RAISE EXCEPTION 'holding identity is immutable'
      USING ERRCODE = '55000';
  END IF;
  IF TG_OP = 'INSERT' THEN
    SELECT household_id INTO security_household
      FROM security WHERE id = NEW.security_id FOR KEY SHARE;
    IF NOT FOUND OR (
      security_household IS NOT NULL AND security_household <> NEW.household_id
    ) THEN
      RAISE EXCEPTION 'holding security is outside its household'
        USING ERRCODE = '23503', CONSTRAINT = 'fk_holding_security_scope';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_holding_write_is_derived
  BEFORE INSERT OR UPDATE OR DELETE ON holding
  FOR EACH ROW EXECUTE FUNCTION guard_holding_write();

CREATE TABLE investment_transaction (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  account_id              UUID NOT NULL,
  holding_id              UUID NOT NULL,
  security_id             UUID NOT NULL REFERENCES security(id) ON DELETE RESTRICT,
  event_date              DATE NOT NULL,
  event_kind              investment_event_kind NOT NULL,
  units                   NUMERIC(28,8),
  unit_ratio              NUMERIC(20,10),
  price                   NUMERIC(20,6),
  gross_amount            NUMERIC(20,4),
  charges                 NUMERIC(20,4) NOT NULL DEFAULT 0,
  withheld_tax            NUMERIC(20,4) NOT NULL DEFAULT 0,
  net_amount              NUMERIC(20,4) NOT NULL,
  currency                CHAR(3) NOT NULL REFERENCES currency(code),
  fx_rate_date            DATE,
  fx_rate_to_base         NUMERIC(20,10),
  fx_rate_to_secondary    NUMERIC(20,10),
  net_amount_base         NUMERIC(20,4),
  net_amount_secondary    NUMERIC(20,4),
  source_kind             investment_event_source NOT NULL,
  source_contract         TEXT NOT NULL,
  source_event_id         TEXT,
  dedupe_version          TEXT,
  dedupe_fingerprint      TEXT,
  corrects_transaction_id UUID,
  correction_reason       TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_transaction_holding_scope
    FOREIGN KEY (household_id, holding_id, account_id, security_id)
    REFERENCES holding(household_id, id, account_id, security_id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_transaction_household_id UNIQUE (household_id, id),
  CONSTRAINT fk_investment_transaction_correction_scope
    FOREIGN KEY (household_id, corrects_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT investment_event_amounts_are_nonnegative CHECK (
    (gross_amount IS NULL OR gross_amount > 0)
    AND charges >= 0 AND withheld_tax >= 0
    AND (price IS NULL OR price > 0)),
  CONSTRAINT investment_event_ratio_is_valid CHECK (
    unit_ratio IS NULL OR (unit_ratio > 0 AND unit_ratio <> 1)),
  CONSTRAINT investment_event_source_contract_is_safe CHECK (
    length(source_contract) BETWEEN 3 AND 64
    AND source_contract ~ '^[a-z][a-z0-9_]+$'),
  CONSTRAINT investment_event_source_identity_is_bounded CHECK (
    source_event_id IS NULL OR (
      length(source_event_id) BETWEEN 1 AND 128
      AND octet_length(source_event_id) <= 512)),
  CONSTRAINT investment_event_dedupe_shape_agrees CHECK (
    (source_event_id IS NOT NULL
      AND dedupe_version IS NULL AND dedupe_fingerprint IS NULL)
    OR (source_event_id IS NULL
      AND dedupe_version IS NOT NULL AND dedupe_fingerprint IS NOT NULL
      AND length(dedupe_version) BETWEEN 1 AND 40
      AND length(dedupe_fingerprint) = 64
      AND dedupe_fingerprint ~ '^[0-9a-f]{64}$')),
  CONSTRAINT investment_event_correction_agrees CHECK (
    (corrects_transaction_id IS NULL AND correction_reason IS NULL)
    OR (corrects_transaction_id IS NOT NULL
      AND source_kind = 'owner_entered'
      AND correction_reason IS NOT NULL
      AND length(correction_reason) BETWEEN 1 AND 500
      AND octet_length(correction_reason) <= 2000)),
  CONSTRAINT investment_event_flow_is_frozen CHECK (
    (net_amount = 0
      AND fx_rate_date IS NULL
      AND fx_rate_to_base IS NULL AND fx_rate_to_secondary IS NULL
      AND net_amount_base IS NULL AND net_amount_secondary IS NULL)
    OR (net_amount <> 0
      AND fx_rate_date IS NOT NULL AND fx_rate_date <= event_date
      AND fx_rate_to_base > 0 AND fx_rate_to_secondary > 0
      AND net_amount_base = round(net_amount * fx_rate_to_base, 4)
      AND net_amount_secondary = round(net_amount * fx_rate_to_secondary, 4))),
  CONSTRAINT investment_event_kind_fields_agree CHECK (
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
        units IS NULL AND unit_ratio IS NOT NULL AND price IS NULL AND gross_amount IS NULL
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
    END)
);

CREATE UNIQUE INDEX idx_investment_event_source_identity
  ON investment_transaction (household_id, source_contract, source_event_id)
  WHERE source_event_id IS NOT NULL;
CREATE UNIQUE INDEX idx_investment_event_fingerprint
  ON investment_transaction (
    household_id, source_contract, dedupe_version, dedupe_fingerprint)
  WHERE dedupe_fingerprint IS NOT NULL;
CREATE UNIQUE INDEX idx_investment_event_one_correction
  ON investment_transaction (corrects_transaction_id)
  WHERE corrects_transaction_id IS NOT NULL;
CREATE INDEX idx_investment_event_holding_date
  ON investment_transaction (holding_id, event_date, created_at, id);

CREATE TABLE investment_unit_effect (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  holding_id                UUID NOT NULL,
  investment_transaction_id UUID NOT NULL,
  effect_date               DATE NOT NULL,
  signed_units              NUMERIC(28,8) NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_unit_effect_holding_scope
    FOREIGN KEY (household_id, holding_id)
    REFERENCES holding(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_unit_effect_event_scope
    FOREIGN KEY (household_id, investment_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_unit_effect_event UNIQUE (investment_transaction_id),
  CONSTRAINT investment_unit_effect_is_nonzero CHECK (signed_units <> 0)
);

CREATE INDEX idx_investment_unit_effect_rebuild
  ON investment_unit_effect (holding_id, effect_date, investment_transaction_id);

CREATE TABLE acquisition_lot (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  holding_id            UUID NOT NULL,
  origin_transaction_id UUID NOT NULL,
  acquisition_date      DATE NOT NULL,
  acquired_units        NUMERIC(28,8) NOT NULL,
  cost_basis_native     NUMERIC(20,4) NOT NULL,
  currency              CHAR(3) NOT NULL REFERENCES currency(code),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_acquisition_lot_holding_scope
    FOREIGN KEY (household_id, holding_id)
    REFERENCES holding(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_acquisition_lot_origin_scope
    FOREIGN KEY (household_id, origin_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_acquisition_lot_origin UNIQUE (origin_transaction_id),
  CONSTRAINT acquisition_lot_units_are_positive CHECK (acquired_units > 0),
  CONSTRAINT acquisition_lot_basis_is_nonnegative CHECK (cost_basis_native >= 0)
);

CREATE INDEX idx_acquisition_lot_holding_date
  ON acquisition_lot (holding_id, acquisition_date, origin_transaction_id);

-- Parent edits must preserve the denomination and date boundaries of retained immutable facts.
CREATE OR REPLACE FUNCTION preserve_security_household() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.household_id IS DISTINCT FROM OLD.household_id THEN
    RAISE EXCEPTION 'security household is immutable' USING ERRCODE = '55000';
  END IF;
  IF NEW.currency IS DISTINCT FROM OLD.currency AND (
    EXISTS (SELECT FROM price_snapshot WHERE security_id = OLD.id)
    OR EXISTS (SELECT FROM investment_transaction WHERE security_id = OLD.id)
  ) THEN
    RAISE EXCEPTION 'security currency cannot relabel retained investment facts'
      USING ERRCODE = '23514', CONSTRAINT = 'security_currency_retains_investment_facts';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER trg_security_household_is_immutable ON security;
CREATE TRIGGER trg_security_household_is_immutable
  BEFORE UPDATE OF household_id, currency ON security
  FOR EACH ROW EXECUTE FUNCTION preserve_security_household();

CREATE FUNCTION preserve_account_investment_history() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.opened_on IS DISTINCT FROM OLD.opened_on
      OR NEW.closed_on IS DISTINCT FROM OLD.closed_on) AND EXISTS (
    SELECT FROM investment_transaction
     WHERE account_id = NEW.id
       AND ((NEW.opened_on IS NOT NULL AND event_date < NEW.opened_on)
         OR (NEW.closed_on IS NOT NULL AND event_date > NEW.closed_on))
  ) THEN
    RAISE EXCEPTION 'account lifetime cannot strand retained investment events'
      USING ERRCODE = '23514', CONSTRAINT = 'account_lifetime_retains_investment_events';
  END IF;
  IF NEW.opening_balance_date IS DISTINCT FROM OLD.opening_balance_date AND EXISTS (
    SELECT FROM investment_transaction
     WHERE account_id = NEW.id AND event_date < NEW.opening_balance_date
  ) THEN
    RAISE EXCEPTION 'account anchor cannot strand retained investment events'
      USING ERRCODE = '23514', CONSTRAINT = 'account_anchor_retains_investment_events';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_investment_history_remains_valid
  BEFORE UPDATE OF opened_on, closed_on, opening_balance_date ON account
  FOR EACH ROW EXECUTE FUNCTION preserve_account_investment_history();

CREATE OR REPLACE FUNCTION preserve_account_valuation_period() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE'
     AND current_setting('oneledger.account_valuation_anchor_account_id', true) =
         OLD.account_id::TEXT
     AND NEW.household_id IS NOT DISTINCT FROM OLD.household_id
     AND NEW.account_id IS NOT DISTINCT FROM OLD.account_id
     AND NEW.valuation_mode IS NOT DISTINCT FROM OLD.valuation_mode
     AND NEW.effective_from IS DISTINCT FROM OLD.effective_from
     AND NEW.effective_to IS NOT DISTINCT FROM OLD.effective_to
     AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.valuation_mode = 'holdings'
     AND NEW.effective_to IS DISTINCT FROM OLD.effective_to
     AND NEW.effective_to IS NOT NULL
     AND EXISTS (
       SELECT FROM investment_transaction
        WHERE account_id = OLD.account_id AND event_date > NEW.effective_to
     ) THEN
    RAISE EXCEPTION 'valuation transition cannot strand retained investment events'
      USING ERRCODE = '23514',
            CONSTRAINT = 'account_valuation_transition_retains_investment_events';
  END IF;
  IF TG_OP = 'DELETE'
     OR NEW.household_id IS DISTINCT FROM OLD.household_id
     OR NEW.account_id IS DISTINCT FROM OLD.account_id
     OR NEW.valuation_mode IS DISTINCT FROM OLD.valuation_mode
     OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR OLD.effective_to IS NOT NULL
     OR NEW.effective_to IS NULL THEN
    RAISE EXCEPTION 'account valuation history is immutable; append a dated transition'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION preserve_investment_history() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'investment_transaction' THEN
    RAISE EXCEPTION 'investment transactions are immutable; append a correction instead'
      USING ERRCODE = '55000';
  END IF;
  RAISE EXCEPTION '% is immutable', TG_TABLE_NAME USING ERRCODE = '55000';
END $$;

CREATE TRIGGER trg_investment_transaction_is_immutable
  BEFORE UPDATE OR DELETE ON investment_transaction
  FOR EACH ROW EXECUTE FUNCTION preserve_investment_history();
CREATE TRIGGER trg_investment_unit_effect_is_immutable
  BEFORE UPDATE OR DELETE ON investment_unit_effect
  FOR EACH ROW EXECUTE FUNCTION preserve_investment_history();
CREATE TRIGGER trg_acquisition_lot_is_immutable
  BEFORE UPDATE OR DELETE ON acquisition_lot
  FOR EACH ROW EXECUTE FUNCTION preserve_investment_history();

CREATE FUNCTION guard_investment_history_insert() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('oneledger.investment_event_append', true) IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'investment history must use append_investment_transaction'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_transaction_uses_append
  BEFORE INSERT ON investment_transaction
  FOR EACH ROW EXECUTE FUNCTION guard_investment_history_insert();
CREATE TRIGGER trg_investment_unit_effect_uses_append
  BEFORE INSERT ON investment_unit_effect
  FOR EACH ROW EXECUTE FUNCTION guard_investment_history_insert();
CREATE TRIGGER trg_acquisition_lot_uses_append
  BEFORE INSERT ON acquisition_lot
  FOR EACH ROW EXECUTE FUNCTION guard_investment_history_insert();

CREATE FUNCTION investment_units_from_events(p_holding_id UUID) RETURNS NUMERIC(28,8)
  LANGUAGE sql STABLE SET search_path = public, pg_temp AS $$
  SELECT COALESCE(sum(effect.signed_units), 0)::NUMERIC(28,8)
    FROM investment_unit_effect effect
    JOIN investment_transaction event
      ON event.id = effect.investment_transaction_id
   WHERE effect.holding_id = p_holding_id
     AND NOT EXISTS (
       SELECT FROM investment_transaction correction
        WHERE correction.corrects_transaction_id = event.id)
$$;

CREATE FUNCTION refresh_holding_projection(p_holding_id UUID) RETURNS VOID
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
  PERFORM set_config('oneledger.investment_event_append', 'on', true);
  UPDATE holding
     SET current_units = rebuilt_units,
         opened_on = first_date,
         closed_on = CASE WHEN rebuilt_units = 0 THEN last_date ELSE NULL END,
         updated_at = now()
   WHERE id = p_holding_id;
  PERFORM set_config('oneledger.investment_event_append', 'off', true);
END $$;

CREATE FUNCTION project_investment_transaction() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.units IS NOT NULL THEN
    INSERT INTO investment_unit_effect (
      household_id, holding_id, investment_transaction_id, effect_date, signed_units
    ) VALUES (
      NEW.household_id, NEW.holding_id, NEW.id, NEW.event_date, NEW.units
    );
  END IF;
  IF NEW.event_kind IN (
    'purchase', 'recurring_purchase', 'reinvested_distribution',
    'switch_in', 'systematic_transfer_in', 'bonus', 'rights'
  ) THEN
    INSERT INTO acquisition_lot (
      household_id, holding_id, origin_transaction_id, acquisition_date,
      acquired_units, cost_basis_native, currency
    ) VALUES (
      NEW.household_id, NEW.holding_id, NEW.id, NEW.event_date, NEW.units,
      CASE
        WHEN NEW.event_kind = 'bonus' THEN 0
        WHEN NEW.event_kind = 'reinvested_distribution' THEN NEW.gross_amount
        ELSE COALESCE(NEW.gross_amount, 0) + NEW.charges + NEW.withheld_tax
      END,
      NEW.currency
    );
  END IF;
  PERFORM refresh_holding_projection(NEW.holding_id);
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_transaction_projects
  AFTER INSERT ON investment_transaction
  FOR EACH ROW EXECUTE FUNCTION project_investment_transaction();

CREATE FUNCTION append_investment_transaction(
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
    'purchase', 'recurring_purchase', 'sale', 'redemption', 'maturity'
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

REVOKE ALL ON FUNCTION guard_holding_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_investment_history_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION preserve_investment_history() FROM PUBLIC;
REVOKE ALL ON FUNCTION refresh_holding_projection(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION project_investment_transaction() FROM PUBLIC;

-- V30 deliberately created account-only valuations. Holdings now add the alternative target
-- while keeping old rows and their account uniqueness unchanged.
ALTER TABLE valuation_snapshot
  ALTER COLUMN account_id DROP NOT NULL,
  ADD COLUMN holding_id UUID,
  ADD CONSTRAINT fk_valuation_snapshot_holding_household
    FOREIGN KEY (household_id, holding_id)
    REFERENCES holding(household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT valuation_snapshot_has_one_target CHECK (
    num_nonnulls(account_id, holding_id) = 1),
  DROP CONSTRAINT uq_valuation_snapshot_account_date;

CREATE UNIQUE INDEX idx_valuation_snapshot_account_date
  ON valuation_snapshot (account_id, as_of_date) WHERE account_id IS NOT NULL;
CREATE UNIQUE INDEX idx_valuation_snapshot_holding_date
  ON valuation_snapshot (holding_id, as_of_date) WHERE holding_id IS NOT NULL;
CREATE INDEX idx_valuation_snapshot_holding_history
  ON valuation_snapshot (household_id, holding_id, as_of_date DESC)
  WHERE holding_id IS NOT NULL;

CREATE OR REPLACE FUNCTION assert_valuation_snapshot_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE
  target_account UUID; account_household UUID; target_currency CHAR(3);
  account_opened DATE; account_closed DATE; account_mode account_valuation_mode;
BEGIN
  IF num_nonnulls(NEW.account_id, NEW.holding_id) <> 1 THEN
    RAISE EXCEPTION 'valuation snapshot must target exactly one account or holding'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_snapshot_has_one_target';
  END IF;
  IF NEW.account_id IS NOT NULL THEN
    target_account := NEW.account_id;
    SELECT household_id, currency, opened_on, closed_on
      INTO account_household, target_currency, account_opened, account_closed
      FROM account WHERE id = target_account FOR SHARE;
  ELSE
    SELECT h.account_id, h.household_id, s.currency, a.opened_on, a.closed_on
      INTO target_account, account_household, target_currency, account_opened, account_closed
      FROM holding h
      JOIN account a ON a.id = h.account_id
      JOIN security s ON s.id = h.security_id
     WHERE h.id = NEW.holding_id
     FOR SHARE OF h, a, s;
  END IF;
  IF NOT FOUND OR account_household <> NEW.household_id THEN
    RAISE EXCEPTION 'valuation snapshot target is outside its household'
      USING ERRCODE = '23503', CONSTRAINT = 'fk_valuation_snapshot_target_scope';
  END IF;
  IF rtrim(target_currency) <> rtrim(NEW.currency) THEN
    RAISE EXCEPTION 'valuation currency must match its target'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_currency_matches_target';
  END IF;
  SELECT valuation_mode INTO account_mode
    FROM account_valuation_period
   WHERE account_id = target_account
     AND NEW.as_of_date >= effective_from
     AND (effective_to IS NULL OR NEW.as_of_date <= effective_to)
   FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'valuation target has no account valuation period'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_target_uses_required_mode';
  END IF;
  IF NEW.account_id IS NOT NULL AND account_mode <> 'snapshot' THEN
    RAISE EXCEPTION 'valuation snapshots require snapshot mode'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_target_uses_snapshot_mode';
  END IF;
  IF NEW.holding_id IS NOT NULL AND account_mode <> 'holdings' THEN
    RAISE EXCEPTION 'holding valuations require holdings mode'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_target_uses_holdings_mode';
  END IF;
  IF (account_opened IS NOT NULL AND NEW.as_of_date < account_opened)
     OR (account_closed IS NOT NULL AND NEW.as_of_date > account_closed) THEN
    RAISE EXCEPTION 'valuation date is outside its account valuation period'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_date_is_in_account_period';
  END IF;
  RETURN NEW;
END $$;

ALTER TABLE holding ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_transaction ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_unit_effect ENABLE ROW LEVEL SECURITY;
ALTER TABLE acquisition_lot ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON holding FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_transaction FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_unit_effect FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON acquisition_lot FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
