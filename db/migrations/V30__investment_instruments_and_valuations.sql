-- Phase 1.6 foundation: generic instruments, price evidence and account-level valuations.
-- Holdings and investment events arrive in V31; no owner account or holding is seeded here.

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TYPE account_valuation_mode AS ENUM ('ledger_balance', 'holdings', 'snapshot');
CREATE TYPE security_type AS ENUM (
  'mutual_fund', 'equity', 'exchange_traded_fund', 'bond', 'deposit',
  'commodity', 'real_asset', 'alternative', 'other'
);
CREATE TYPE asset_class AS ENUM (
  'equity', 'fixed_income', 'cash', 'commodity', 'real_asset', 'alternative', 'other'
);
CREATE TYPE valuation_method AS ENUM (
  'owner_entered', 'market_quote', 'indexed', 'statement'
);
CREATE TYPE valuation_confidence AS ENUM ('high', 'medium', 'low');

-- Investment source identities remain inside the existing private account-identifier boundary.
ALTER TABLE account_identifier
  DROP CONSTRAINT account_identifier_kind_is_known,
  ADD CONSTRAINT account_identifier_kind_is_known CHECK (
    kind IN ('bank_account', 'iban', 'card', 'folio', 'brokerage_account')),
  ADD CONSTRAINT investment_identifier_shape_is_safe CHECK (
    kind NOT IN ('folio', 'brokerage_account') OR (
      visible_suffix IS NOT NULL
      AND visible_suffix ~ '^[A-Z0-9]{4}$'
      AND masked_value = '****' || visible_suffix
      AND (normalized_value IS NULL OR (
        length(normalized_value) BETWEEN 5 AND 64
        AND normalized_value ~ '^[A-Z0-9]+$'))));

-- Valuation choice is history, not mutable account state. Existing V29 accounts remain
-- ledger-valued because V29 contains no structured holding or snapshot evidence from which a
-- different mode could truthfully be inferred. Accounts created after V30 get an initial mode
-- from their explicit account type; subsequent changes use the owner-authorized function below.
CREATE TABLE account_valuation_period (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  account_id     UUID NOT NULL,
  valuation_mode account_valuation_mode NOT NULL,
  effective_from DATE NOT NULL,
  effective_to   DATE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_account_valuation_period_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT account_valuation_period_is_valid CHECK (
    effective_to IS NULL OR effective_to >= effective_from),
  CONSTRAINT account_valuation_periods_do_not_overlap
    EXCLUDE USING gist (
      account_id WITH =,
      daterange(effective_from, effective_to, '[]') WITH &&)
);

CREATE INDEX idx_account_valuation_period_scope
  ON account_valuation_period (household_id, account_id, effective_from DESC);

CREATE FUNCTION assert_account_valuation_period_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE account_household UUID; account_class_value account_class; account_anchor DATE;
BEGIN
  SELECT household_id, account_class, opening_balance_date
    INTO account_household, account_class_value, account_anchor
    FROM account WHERE id = NEW.account_id FOR KEY SHARE;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF NEW.household_id <> account_household THEN
    RAISE EXCEPTION 'account valuation period is outside its household'
      USING ERRCODE = '23503',
            CONSTRAINT = 'fk_account_valuation_period_account_household';
  END IF;
  IF account_class_value = 'liability' AND NEW.valuation_mode <> 'ledger_balance' THEN
    RAISE EXCEPTION 'liability accounts require ledger_balance valuation mode'
      USING ERRCODE = '23514', CONSTRAINT = 'account_liability_uses_ledger_value';
  END IF;
  IF NEW.effective_from < account_anchor THEN
    RAISE EXCEPTION 'valuation mode cannot predate the account balance anchor'
      USING ERRCODE = '23514',
            CONSTRAINT = 'account_valuation_mode_starts_after_anchor';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_valuation_period_scope
  BEFORE INSERT ON account_valuation_period
  FOR EACH ROW EXECUTE FUNCTION assert_account_valuation_period_scope();

CREATE FUNCTION preserve_account_valuation_period() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  -- The account parent trigger may move only the first period's start while atomically changing
  -- the opening-balance anchor. No ordinary child-table update gets this narrow exception.
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

CREATE TRIGGER trg_account_valuation_period_is_immutable
  BEFORE UPDATE OR DELETE ON account_valuation_period
  FOR EACH ROW EXECUTE FUNCTION preserve_account_valuation_period();

INSERT INTO account_valuation_period (
  household_id, account_id, valuation_mode, effective_from
)
SELECT household_id, id, 'ledger_balance', opening_balance_date
  FROM account;

CREATE FUNCTION initialize_account_valuation_period() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE initial_mode account_valuation_mode;
BEGIN
  initial_mode := CASE
    WHEN NEW.account_type IN ('brokerage', 'mutual_fund_folio') THEN 'holdings'
    WHEN NEW.account_type IN (
      'retirement', 'property', 'precious_metal', 'crypto', 'other_asset'
    ) THEN 'snapshot'
    ELSE 'ledger_balance'
  END::account_valuation_mode;
  INSERT INTO account_valuation_period (
    household_id, account_id, valuation_mode, effective_from
  ) VALUES (
    NEW.household_id, NEW.id, initial_mode, NEW.opening_balance_date
  );
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_valuation_period_initialized
  AFTER INSERT ON account
  FOR EACH ROW EXECUTE FUNCTION initialize_account_valuation_period();

-- Shared securities are read-only reference rows; owner-created securities remain private.
CREATE TABLE security (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID REFERENCES household(id),
  security_type   security_type NOT NULL,
  name            TEXT NOT NULL,
  currency        CHAR(3) NOT NULL REFERENCES currency(code),
  country         CHAR(2) NOT NULL,
  asset_class     asset_class NOT NULL,
  sub_asset_class TEXT,
  isin            TEXT,
  ticker          TEXT,
  exchange        TEXT,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT security_name_is_bounded CHECK (
    length(name) BETWEEN 1 AND 200 AND octet_length(name) <= 800),
  CONSTRAINT security_country_is_canonical CHECK (country ~ '^[A-Z]{2}$'),
  CONSTRAINT security_subclass_is_bounded CHECK (
    sub_asset_class IS NULL OR (
      length(sub_asset_class) BETWEEN 1 AND 80 AND octet_length(sub_asset_class) <= 320)),
  CONSTRAINT security_isin_is_canonical CHECK (
    isin IS NULL OR isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'),
  CONSTRAINT security_market_identity_is_complete CHECK (
    num_nonnulls(ticker, exchange) IN (0, 2)
    AND (ticker IS NULL OR (
      length(ticker) BETWEEN 1 AND 32 AND ticker ~ '^[A-Z0-9._-]+$'
      AND length(exchange) BETWEEN 1 AND 32 AND exchange ~ '^[A-Z0-9._-]+$')))
);

CREATE UNIQUE INDEX idx_security_isin ON security (isin) WHERE isin IS NOT NULL;
CREATE UNIQUE INDEX idx_security_market_identity ON security (exchange, ticker)
  WHERE exchange IS NOT NULL;
CREATE INDEX idx_security_scope ON security (household_id, is_active, name);

CREATE FUNCTION preserve_security_household() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.household_id IS DISTINCT FROM OLD.household_id THEN
    RAISE EXCEPTION 'security household is immutable' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_security_household_is_immutable
  BEFORE UPDATE OF household_id ON security
  FOR EACH ROW EXECUTE FUNCTION preserve_security_household();

-- A source mapping is versioned and effective-dated. The source key is a capability, not a
-- provider, institution, country or currency name embedded in application code.
CREATE TABLE security_price_source (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID REFERENCES household(id),
  security_id          UUID NOT NULL REFERENCES security(id) ON DELETE RESTRICT,
  source_key           TEXT NOT NULL,
  source_version       TEXT NOT NULL,
  source_security_code TEXT NOT NULL,
  effective_from       DATE NOT NULL,
  effective_to         DATE,
  is_active            BOOLEAN NOT NULL DEFAULT true,
  deactivated_at       TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT security_price_source_key_is_safe CHECK (
    length(source_key) BETWEEN 3 AND 64 AND source_key ~ '^[a-z][a-z0-9_]+$'),
  CONSTRAINT security_price_source_values_are_bounded CHECK (
    length(source_version) BETWEEN 1 AND 40 AND octet_length(source_version) <= 160
    AND length(source_security_code) BETWEEN 1 AND 128
    AND octet_length(source_security_code) <= 512),
  CONSTRAINT security_price_source_period_is_valid CHECK (
    effective_to IS NULL OR effective_to >= effective_from),
  CONSTRAINT security_price_source_lifecycle_agrees CHECK (
    (is_active AND effective_to IS NULL AND deactivated_at IS NULL)
    OR (NOT is_active AND effective_to IS NOT NULL AND deactivated_at IS NOT NULL)),
  CONSTRAINT security_price_source_periods_do_not_overlap
    EXCLUDE USING gist (
      security_id WITH =,
      source_key WITH =,
      daterange(effective_from, effective_to, '[]') WITH &&),
  -- These are public instrument resolver codes, never owner account identifiers. One exact
  -- source/version/code may resolve to only one security at a time, across every scope.
  CONSTRAINT security_price_source_identity_is_temporally_unique
    EXCLUDE USING gist (
      source_key WITH =,
      source_version WITH =,
      source_security_code WITH =,
      daterange(effective_from, effective_to, '[]') WITH &&)
);

CREATE UNIQUE INDEX idx_security_price_source_one_active
  ON security_price_source (security_id, source_key) WHERE is_active;
CREATE INDEX idx_security_price_source_lookup
  ON security_price_source (source_key, source_security_code, effective_from DESC);

CREATE FUNCTION assert_security_price_source_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE security_household UUID;
BEGIN
  SELECT household_id INTO security_household
    FROM security WHERE id = NEW.security_id FOR KEY SHARE;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF NEW.household_id IS DISTINCT FROM security_household THEN
    RAISE EXCEPTION 'security price source is outside its scope'
      USING ERRCODE = '23503', CONSTRAINT = 'fk_security_price_source_scope';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_security_price_source_scope
  BEFORE INSERT OR UPDATE ON security_price_source
  FOR EACH ROW EXECUTE FUNCTION assert_security_price_source_scope();

CREATE FUNCTION preserve_security_price_source_identity() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.household_id IS DISTINCT FROM OLD.household_id
     OR NEW.security_id IS DISTINCT FROM OLD.security_id
     OR NEW.source_key IS DISTINCT FROM OLD.source_key
     OR NEW.source_version IS DISTINCT FROM OLD.source_version
     OR NEW.source_security_code IS DISTINCT FROM OLD.source_security_code
     OR NEW.effective_from IS DISTINCT FROM OLD.effective_from THEN
    RAISE EXCEPTION 'security price source identity is immutable'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.effective_to IS DISTINCT FROM OLD.effective_to THEN
    IF OLD.effective_to IS NOT NULL OR NEW.effective_to IS NULL THEN
      RAISE EXCEPTION 'security price source periods cannot be reopened or rewritten'
        USING ERRCODE = '55000';
    END IF;
    IF EXISTS (
      SELECT FROM price_snapshot
       WHERE source_mapping_id = OLD.id AND price_date > NEW.effective_to
    ) THEN
      RAISE EXCEPTION 'security price source cannot end before an observation'
        USING ERRCODE = '23514',
              CONSTRAINT = 'security_price_source_retains_observations';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_security_price_source_identity_is_immutable
  BEFORE UPDATE ON security_price_source
  FOR EACH ROW EXECUTE FUNCTION preserve_security_price_source_identity();

-- Different values for one source identity remain as conflicting observations. Exactly one value
-- per security/date may be selected for valuation.
CREATE TABLE price_snapshot (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID REFERENCES household(id),
  security_id       UUID NOT NULL REFERENCES security(id) ON DELETE RESTRICT,
  source_mapping_id UUID NOT NULL REFERENCES security_price_source(id) ON DELETE RESTRICT,
  price_date        DATE NOT NULL,
  price             NUMERIC(20,6) NOT NULL,
  currency          CHAR(3) NOT NULL REFERENCES currency(code),
  observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_accepted       BOOLEAN NOT NULL DEFAULT false,
  accepted_at       TIMESTAMPTZ,

  CONSTRAINT price_snapshot_price_is_positive CHECK (price > 0),
  CONSTRAINT price_snapshot_acceptance_agrees CHECK (
    (is_accepted AND accepted_at IS NOT NULL)
    OR (NOT is_accepted AND accepted_at IS NULL)),
  CONSTRAINT uq_price_snapshot_observation UNIQUE (
    security_id, price_date, source_mapping_id, price)
);

CREATE UNIQUE INDEX idx_price_snapshot_one_accepted
  ON price_snapshot (security_id, price_date) WHERE is_accepted;
CREATE INDEX idx_price_snapshot_history
  ON price_snapshot (security_id, price_date DESC, observed_at DESC);

CREATE FUNCTION assert_price_snapshot_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE
  security_household UUID; security_currency CHAR(3);
  mapping_household UUID; mapping_security UUID; mapping_from DATE; mapping_to DATE;
BEGIN
  SELECT household_id, currency INTO security_household, security_currency
    FROM security WHERE id = NEW.security_id FOR KEY SHARE;
  SELECT household_id, security_id, effective_from, effective_to
    INTO mapping_household, mapping_security, mapping_from, mapping_to
    -- SHARE serializes an observation insert against a concurrent source-period closure.
    FROM security_price_source WHERE id = NEW.source_mapping_id FOR SHARE;
  IF security_household IS DISTINCT FROM NEW.household_id
     OR mapping_household IS DISTINCT FROM NEW.household_id
     OR mapping_security IS DISTINCT FROM NEW.security_id THEN
    RAISE EXCEPTION 'price observation is outside its scope'
      USING ERRCODE = '23503', CONSTRAINT = 'fk_price_snapshot_scope';
  END IF;
  IF rtrim(NEW.currency) <> rtrim(security_currency) THEN
    RAISE EXCEPTION 'price currency must match its security'
      USING ERRCODE = '23514', CONSTRAINT = 'price_snapshot_currency_matches_security';
  END IF;
  IF NEW.price_date < mapping_from OR (
    mapping_to IS NOT NULL AND NEW.price_date > mapping_to
  ) THEN
    RAISE EXCEPTION 'price date is outside its source mapping period'
      USING ERRCODE = '23514', CONSTRAINT = 'price_snapshot_source_is_effective';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_price_snapshot_scope
  BEFORE INSERT ON price_snapshot
  FOR EACH ROW EXECUTE FUNCTION assert_price_snapshot_scope();

CREATE FUNCTION preserve_price_snapshot_fact() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE'
     OR NEW.household_id IS DISTINCT FROM OLD.household_id
     OR NEW.security_id IS DISTINCT FROM OLD.security_id
     OR NEW.source_mapping_id IS DISTINCT FROM OLD.source_mapping_id
     OR NEW.price_date IS DISTINCT FROM OLD.price_date
     OR NEW.price IS DISTINCT FROM OLD.price
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.observed_at IS DISTINCT FROM OLD.observed_at THEN
    RAISE EXCEPTION 'price_snapshot is immutable; append a new dated value'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_price_snapshot_is_immutable
  BEFORE UPDATE OR DELETE ON price_snapshot
  FOR EACH ROW EXECUTE FUNCTION preserve_price_snapshot_fact();

-- V30 snapshots target accounts. V31 adds a holding target when that relation exists and preserves
-- the exactly-one-target rule. Frozen reporting values make each historical point reproducible.
CREATE TABLE valuation_snapshot (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  account_id           UUID NOT NULL,
  as_of_date           DATE NOT NULL,
  value_native         NUMERIC(20,4) NOT NULL,
  currency             CHAR(3) NOT NULL REFERENCES currency(code),
  fx_rate_date         DATE NOT NULL,
  fx_rate_to_base      NUMERIC(20,10) NOT NULL,
  fx_rate_to_secondary NUMERIC(20,10) NOT NULL,
  value_base           NUMERIC(20,4) NOT NULL,
  value_secondary      NUMERIC(20,4) NOT NULL,
  method               valuation_method NOT NULL,
  confidence           valuation_confidence NOT NULL,
  source_note          TEXT,
  source_document_id   UUID,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_valuation_snapshot_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_valuation_snapshot_document_household
    FOREIGN KEY (household_id, source_document_id)
    REFERENCES document(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT valuation_native_is_nonnegative CHECK (value_native >= 0),
  CONSTRAINT valuation_fx_rates_are_positive CHECK (
    fx_rate_to_base > 0 AND fx_rate_to_secondary > 0),
  CONSTRAINT valuation_fx_rate_is_not_future CHECK (fx_rate_date <= as_of_date),
  CONSTRAINT valuation_base_is_frozen CHECK (
    value_base = round(value_native * fx_rate_to_base, 4)),
  CONSTRAINT valuation_secondary_is_frozen CHECK (
    value_secondary = round(value_native * fx_rate_to_secondary, 4)),
  CONSTRAINT valuation_source_note_is_bounded CHECK (
    source_note IS NULL OR (
      length(source_note) BETWEEN 1 AND 500 AND octet_length(source_note) <= 2000)),
  CONSTRAINT uq_valuation_snapshot_account_date UNIQUE (account_id, as_of_date)
);

CREATE INDEX idx_valuation_snapshot_history
  ON valuation_snapshot (household_id, account_id, as_of_date DESC);

CREATE FUNCTION assert_valuation_snapshot_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE
  account_household UUID; account_currency CHAR(3); account_opened DATE; account_closed DATE;
  account_mode account_valuation_mode;
BEGIN
  SELECT household_id, currency, opened_on, closed_on
    INTO account_household, account_currency, account_opened, account_closed
    -- SHARE conflicts with ordinary non-key account updates. Either this insert commits first and
    -- the parent-edit trigger sees it, or the edit commits first and this trigger sees the new
    -- currency/lifetime/anchor; both cannot validate against different account versions.
    FROM account WHERE id = NEW.account_id FOR SHARE;
  IF NOT FOUND THEN RETURN NEW; END IF;
  IF account_household <> NEW.household_id THEN
    RAISE EXCEPTION 'valuation snapshot household does not own account'
      USING ERRCODE = '23503', CONSTRAINT = 'fk_valuation_snapshot_account_household';
  END IF;
  IF rtrim(account_currency) <> rtrim(NEW.currency) THEN
    RAISE EXCEPTION 'valuation currency must match its account'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_currency_matches_account';
  END IF;
  SELECT valuation_mode INTO account_mode
    FROM account_valuation_period
   WHERE account_id = NEW.account_id
     AND NEW.as_of_date >= effective_from
     AND (effective_to IS NULL OR NEW.as_of_date <= effective_to)
   FOR SHARE;
  IF NOT FOUND OR account_mode <> 'snapshot' THEN
    RAISE EXCEPTION 'valuation snapshots require snapshot mode'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_target_uses_snapshot_mode';
  END IF;
  IF (account_opened IS NOT NULL AND NEW.as_of_date < account_opened)
     OR (account_closed IS NOT NULL AND NEW.as_of_date > account_closed) THEN
    RAISE EXCEPTION 'valuation date is outside its account valuation period'
      USING ERRCODE = '23514', CONSTRAINT = 'valuation_date_is_in_account_period';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_valuation_snapshot_scope
  BEFORE INSERT ON valuation_snapshot
  FOR EACH ROW EXECUTE FUNCTION assert_valuation_snapshot_scope();

CREATE FUNCTION preserve_valuation_snapshot() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'valuation_snapshot is immutable; append a new dated value'
    USING ERRCODE = '55000';
END $$;

CREATE TRIGGER trg_valuation_snapshot_is_immutable
  BEFORE UPDATE OR DELETE ON valuation_snapshot
  FOR EACH ROW EXECUTE FUNCTION preserve_valuation_snapshot();

-- Account edits must not invalidate child facts after their insertion-time checks. The parent row
-- is already locked by UPDATE, which serializes this validation with snapshot insertion and mode
-- transition. A safe anchor correction moves the first period atomically; edits that would strand
-- a period or retained valuation are refused and the parent update rolls back with them.
CREATE FUNCTION reconcile_account_valuation_children() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE initial_period account_valuation_period%ROWTYPE;
BEGIN
  IF NEW.account_class = 'liability' AND EXISTS (
    SELECT FROM account_valuation_period
     WHERE account_id = NEW.id AND valuation_mode <> 'ledger_balance'
  ) THEN
    RAISE EXCEPTION 'liability accounts require ledger_balance valuation mode'
      USING ERRCODE = '23514', CONSTRAINT = 'account_liability_uses_ledger_value';
  END IF;

  IF NEW.currency IS DISTINCT FROM OLD.currency AND EXISTS (
    SELECT FROM valuation_snapshot
     WHERE account_id = NEW.id AND rtrim(currency) <> rtrim(NEW.currency)
  ) THEN
    RAISE EXCEPTION 'account currency cannot relabel retained valuations'
      USING ERRCODE = '23514', CONSTRAINT = 'account_currency_retains_valuations';
  END IF;

  IF (NEW.opened_on IS DISTINCT FROM OLD.opened_on
      OR NEW.closed_on IS DISTINCT FROM OLD.closed_on) AND EXISTS (
    SELECT FROM valuation_snapshot
     WHERE account_id = NEW.id
       AND ((NEW.opened_on IS NOT NULL AND as_of_date < NEW.opened_on)
         OR (NEW.closed_on IS NOT NULL AND as_of_date > NEW.closed_on))
  ) THEN
    RAISE EXCEPTION 'account lifetime cannot strand retained valuations'
      USING ERRCODE = '23514', CONSTRAINT = 'account_lifetime_retains_valuations';
  END IF;

  IF NEW.opening_balance_date IS DISTINCT FROM OLD.opening_balance_date THEN
    SELECT * INTO initial_period
      FROM account_valuation_period
     WHERE account_id = NEW.id
     ORDER BY effective_from, id
     LIMIT 1 FOR UPDATE;
    IF NOT FOUND OR initial_period.effective_from <> OLD.opening_balance_date
       OR (initial_period.effective_to IS NOT NULL
           AND NEW.opening_balance_date > initial_period.effective_to)
       OR EXISTS (
         SELECT FROM valuation_snapshot
          WHERE account_id = NEW.id AND as_of_date < NEW.opening_balance_date
       ) THEN
      RAISE EXCEPTION 'account anchor cannot strand valuation history'
        USING ERRCODE = '23514', CONSTRAINT = 'account_anchor_retains_valuations';
    END IF;
    PERFORM set_config(
      'oneledger.account_valuation_anchor_account_id', NEW.id::TEXT, true
    );
    UPDATE account_valuation_period
       SET effective_from = NEW.opening_balance_date
     WHERE id = initial_period.id;
    PERFORM set_config('oneledger.account_valuation_anchor_account_id', '', true);
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_valuation_children_remain_valid
  AFTER UPDATE OF account_type, currency, opened_on, closed_on, opening_balance_date ON account
  FOR EACH ROW EXECUTE FUNCTION reconcile_account_valuation_children();

-- This is the only owner-facing mode transition. Locking the account serializes it against
-- snapshot insertion; closing the old period and appending the new one preserves every as-of
-- answer. A transition away from snapshot mode cannot strand later retained valuations.
CREATE FUNCTION transition_account_valuation_mode(
  p_account_id UUID,
  p_valuation_mode account_valuation_mode,
  p_effective_from DATE
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; account_household UUID; account_class_value account_class;
  account_anchor DATE; current_period account_valuation_period%ROWTYPE;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();

  SELECT household_id, account_class, opening_balance_date
    INTO account_household, account_class_value, account_anchor
    FROM account WHERE id = p_account_id FOR UPDATE;
  IF NOT FOUND OR account_household <> owner_household THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF p_effective_from IS NULL OR p_effective_from < account_anchor THEN
    RAISE EXCEPTION 'valuation mode cannot predate the account balance anchor'
      USING ERRCODE = '23514',
            CONSTRAINT = 'account_valuation_mode_starts_after_anchor';
  END IF;
  IF account_class_value = 'liability' AND p_valuation_mode <> 'ledger_balance' THEN
    RAISE EXCEPTION 'liability accounts require ledger_balance valuation mode'
      USING ERRCODE = '23514', CONSTRAINT = 'account_liability_uses_ledger_value';
  END IF;

  SELECT * INTO current_period
    FROM account_valuation_period
   WHERE account_id = p_account_id AND effective_to IS NULL
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account has no current valuation period'
      USING ERRCODE = '55000';
  END IF;
  IF p_valuation_mode = current_period.valuation_mode THEN
    RAISE EXCEPTION 'new valuation mode must differ from the current mode'
      USING ERRCODE = '23514';
  END IF;
  IF p_effective_from <= current_period.effective_from THEN
    RAISE EXCEPTION 'valuation transition must follow the current period start'
      USING ERRCODE = '23514';
  END IF;
  IF current_period.valuation_mode = 'snapshot' AND EXISTS (
    SELECT FROM valuation_snapshot
     WHERE account_id = p_account_id AND as_of_date >= p_effective_from
  ) THEN
    RAISE EXCEPTION 'valuation transition would strand retained snapshots'
      USING ERRCODE = '23514',
            CONSTRAINT = 'account_valuation_transition_retains_snapshots';
  END IF;

  UPDATE account_valuation_period
     SET effective_to = p_effective_from - 1
   WHERE id = current_period.id;
  INSERT INTO account_valuation_period (
    household_id, account_id, valuation_mode, effective_from
  ) VALUES (
    owner_household, p_account_id, p_valuation_mode, p_effective_from
  );
END $$;

ALTER TABLE account_valuation_period ENABLE ROW LEVEL SECURITY;
ALTER TABLE security ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_price_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE valuation_snapshot ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON account_valuation_period FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY security_read ON security FOR SELECT TO authenticated
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY security_write ON security FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY security_price_source_read ON security_price_source FOR SELECT TO authenticated
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY security_price_source_write ON security_price_source FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY price_snapshot_read ON price_snapshot FOR SELECT TO authenticated
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY price_snapshot_write ON price_snapshot FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON valuation_snapshot FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
