-- Phase 1.6: exact cash and disposal-lot allocations are the sole investment links.

CREATE FUNCTION assert_legacy_investment_cash_links_empty(p_nonnull_count BIGINT)
  RETURNS VOID LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF p_nonnull_count <> 0 THEN
    RAISE EXCEPTION 'legacy investment cash links must be cleared before V33 can remove them'
      USING ERRCODE = '23514',
            CONSTRAINT = 'legacy_investment_cash_links_must_be_empty';
  END IF;
END $$;

REVOKE ALL ON FUNCTION assert_legacy_investment_cash_links_empty(BIGINT) FROM PUBLIC;

DO $$
BEGIN
  PERFORM assert_legacy_investment_cash_links_empty(
    (SELECT count(*) FROM transaction WHERE investment_transaction_id IS NOT NULL));
END $$;

ALTER TYPE txn_type ADD VALUE 'investment_fee' AFTER 'investment_income';

CREATE TYPE investment_cash_allocation_role AS ENUM (
  'funding', 'proceeds', 'cash_distribution', 'fee'
);

CREATE TABLE investment_cash_allocation (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  investment_transaction_id UUID NOT NULL,
  cash_transaction_id       UUID NOT NULL,
  role                      investment_cash_allocation_role NOT NULL,
  allocated_amount          NUMERIC(20,4) NOT NULL,
  prior_txn_type            txn_type NOT NULL,
  allocated_by              UUID NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_cash_allocation_event_scope
    FOREIGN KEY (household_id, investment_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_cash_allocation_cash_scope
    FOREIGN KEY (household_id, cash_transaction_id)
    REFERENCES transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_cash_allocation_actor_scope
    FOREIGN KEY (household_id, allocated_by)
    REFERENCES app_user(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_cash_allocation_pair
    UNIQUE (investment_transaction_id, cash_transaction_id),
  CONSTRAINT investment_cash_allocation_amount_is_positive CHECK (allocated_amount > 0)
);

CREATE INDEX idx_investment_cash_allocation_event
  ON investment_cash_allocation (household_id, investment_transaction_id, created_at);
CREATE INDEX idx_investment_cash_allocation_cash
  ON investment_cash_allocation (household_id, cash_transaction_id, created_at);

ALTER TABLE investment_transaction
  ADD CONSTRAINT uq_investment_transaction_lot_allocation_scope
    UNIQUE (household_id, id, holding_id);
ALTER TABLE acquisition_lot
  ADD CONSTRAINT uq_acquisition_lot_allocation_scope
    UNIQUE (household_id, id, holding_id);

CREATE TABLE investment_lot_allocation (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  holding_id              UUID NOT NULL,
  disposal_transaction_id UUID NOT NULL,
  acquisition_lot_id      UUID NOT NULL,
  allocated_units         NUMERIC(28,8) NOT NULL,
  allocated_by            UUID NOT NULL,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_lot_allocation_disposal_scope
    FOREIGN KEY (household_id, disposal_transaction_id, holding_id)
    REFERENCES investment_transaction(household_id, id, holding_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_lot_allocation_lot_scope
    FOREIGN KEY (household_id, acquisition_lot_id, holding_id)
    REFERENCES acquisition_lot(household_id, id, holding_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_lot_allocation_actor_scope
    FOREIGN KEY (household_id, allocated_by)
    REFERENCES app_user(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_lot_allocation_pair
    UNIQUE (disposal_transaction_id, acquisition_lot_id),
  CONSTRAINT investment_lot_allocation_units_are_positive CHECK (allocated_units > 0)
);

CREATE INDEX idx_investment_lot_allocation_disposal
  ON investment_lot_allocation (household_id, disposal_transaction_id, created_at);
CREATE INDEX idx_investment_lot_allocation_origin
  ON investment_lot_allocation (household_id, acquisition_lot_id, created_at);

CREATE FUNCTION investment_cash_type_for_role(p_role investment_cash_allocation_role)
  RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_role
    WHEN 'funding' THEN 'investment_buy'
    WHEN 'proceeds' THEN 'investment_sell'
    WHEN 'cash_distribution' THEN 'investment_income'
    WHEN 'fee' THEN 'investment_fee'
  END
$$;

CREATE FUNCTION guard_investment_cash_allocation_write() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('oneledger.investment_cash_allocation_write', true)
       IS DISTINCT FROM 'on'
     OR current_user <> pg_get_userbyid((
       SELECT relation.relowner FROM pg_class relation WHERE relation.oid = TG_RELID
     )) THEN
    RAISE EXCEPTION 'cash allocations must use confirm_investment_cash_allocations or unlink_investment_cash_allocations'
      USING ERRCODE = '55000';
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER trg_investment_cash_allocation_uses_operation
  BEFORE INSERT OR UPDATE OR DELETE ON investment_cash_allocation
  FOR EACH ROW EXECUTE FUNCTION guard_investment_cash_allocation_write();

CREATE FUNCTION guard_allocated_cash_classification() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.txn_type IS DISTINCT FROM OLD.txn_type
     AND EXISTS (
       SELECT FROM investment_cash_allocation allocation
        WHERE allocation.cash_transaction_id = OLD.id
     )
     AND (
       current_setting('oneledger.investment_cash_allocation_write', true)
         IS DISTINCT FROM 'on'
       OR current_user <> pg_get_userbyid((
         SELECT relation.relowner FROM pg_class relation WHERE relation.oid = TG_RELID
       ))
     ) THEN
    RAISE EXCEPTION 'allocated cash classification changes require the allocation operations'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_allocated_cash_classification_uses_operation
  BEFORE UPDATE OF txn_type ON transaction
  FOR EACH ROW EXECUTE FUNCTION guard_allocated_cash_classification();

CREATE FUNCTION guard_investment_lot_allocation_write() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('oneledger.investment_lot_allocation_write', true)
       IS DISTINCT FROM 'on'
     OR current_user <> pg_get_userbyid((
       SELECT relation.relowner FROM pg_class relation WHERE relation.oid = TG_RELID
     )) THEN
    RAISE EXCEPTION 'lot allocations must use allocate_investment_lots'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_lot_allocation_uses_operation
  BEFORE INSERT OR UPDATE OR DELETE ON investment_lot_allocation
  FOR EACH ROW EXECUTE FUNCTION guard_investment_lot_allocation_write();

-- Recreate the one-level split guard without the retired column. An allocated child may carry
-- its role-derived investment type while its ordinary sibling keeps the parent's prior type.
DROP TRIGGER trg_split_one_level ON transaction;
CREATE OR REPLACE FUNCTION assert_split_one_level() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE parent transaction%ROWTYPE;
BEGIN
  IF TG_OP = 'UPDATE' AND EXISTS (
    SELECT FROM transaction child WHERE child.parent_transaction_id = NEW.id
  ) THEN
    IF NEW.household_id         IS DISTINCT FROM OLD.household_id
    OR NEW.account_id           IS DISTINCT FROM OLD.account_id
    OR NEW.booked_date          IS DISTINCT FROM OLD.booked_date
    OR NEW.currency             IS DISTINCT FROM OLD.currency
    OR NEW.source_document_id   IS DISTINCT FROM OLD.source_document_id
    OR NEW.fx_rate_date         IS DISTINCT FROM OLD.fx_rate_date
    OR NEW.fx_rate_is_carried   IS DISTINCT FROM OLD.fx_rate_is_carried
    OR NEW.fx_rate_to_base      IS DISTINCT FROM OLD.fx_rate_to_base
    OR NEW.fx_rate_to_secondary IS DISTINCT FROM OLD.fx_rate_to_secondary
    OR NEW.is_excluded          IS DISTINCT FROM OLD.is_excluded
    OR NEW.txn_type             IS DISTINCT FROM OLD.txn_type
    OR NEW.transfer_id          IS DISTINCT FROM OLD.transfer_id
    OR NEW.card_statement_id    IS DISTINCT FROM OLD.card_statement_id
    OR NEW.loan_id              IS DISTINCT FROM OLD.loan_id
    OR NEW.loan_schedule_entry_id IS DISTINCT FROM OLD.loan_schedule_entry_id
    OR NEW.recurring_series_id  IS DISTINCT FROM OLD.recurring_series_id
    OR NEW.merchant_id          IS DISTINCT FROM OLD.merchant_id
    OR NEW.country              IS DISTINCT FROM OLD.country
    OR NEW.payment_method       IS DISTINCT FROM OLD.payment_method
    OR NEW.source               IS DISTINCT FROM OLD.source
    OR NEW.source_page_number   IS DISTINCT FROM OLD.source_page_number
    OR NEW.source_row_number    IS DISTINCT FROM OLD.source_row_number
    OR NEW.import_batch_id      IS DISTINCT FROM OLD.import_batch_id
    OR NEW.statement_layout_id  IS DISTINCT FROM OLD.statement_layout_id
    OR NEW.layout_version       IS DISTINCT FROM OLD.layout_version
    OR NEW.value_date           IS DISTINCT FROM OLD.value_date
    OR NEW.description_clean    IS DISTINCT FROM OLD.description_clean
    OR NEW.reference_raw        IS DISTINCT FROM OLD.reference_raw
    OR NEW.counterparty_raw     IS DISTINCT FROM OLD.counterparty_raw
    OR NEW.receipt_document_id  IS DISTINCT FROM OLD.receipt_document_id
    OR NEW.tags                 IS DISTINCT FROM OLD.tags THEN
      RAISE EXCEPTION 'a split parent''s inherited facts are fixed while children exist: %', NEW.id;
    END IF;
  END IF;
  IF NEW.parent_transaction_id IS NOT NULL THEN
    IF NEW.parent_transaction_id = NEW.id THEN
      RAISE EXCEPTION 'a transaction cannot be its own split parent: %', NEW.id;
    END IF;
    SELECT * INTO parent FROM transaction WHERE id = NEW.parent_transaction_id FOR UPDATE;
    IF parent.id IS NULL THEN RETURN NEW; END IF;
    IF parent.parent_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'splits are one level deep: the parent of % is itself a split child', NEW.id;
    END IF;
    IF EXISTS (
      SELECT FROM investment_cash_allocation allocation
       WHERE allocation.cash_transaction_id = parent.id
    ) THEN
      RAISE EXCEPTION 'an allocated cash leaf must be unlinked before it can be split'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_cash_transaction_must_remain_leaf';
    END IF;
    IF EXISTS (SELECT FROM transaction child WHERE child.parent_transaction_id = NEW.id) THEN
      RAISE EXCEPTION 'splits are one level deep: % has children and cannot become a child', NEW.id;
    END IF;
    IF NEW.household_id          IS DISTINCT FROM parent.household_id
    OR NEW.account_id            IS DISTINCT FROM parent.account_id
    OR NEW.booked_date           IS DISTINCT FROM parent.booked_date
    OR NEW.currency              IS DISTINCT FROM parent.currency
    OR NEW.source_document_id    IS DISTINCT FROM parent.source_document_id
    OR NEW.fx_rate_date          IS DISTINCT FROM parent.fx_rate_date
    OR NEW.fx_rate_is_carried    IS DISTINCT FROM parent.fx_rate_is_carried
    OR NEW.fx_rate_to_base       IS DISTINCT FROM parent.fx_rate_to_base
    OR NEW.fx_rate_to_secondary  IS DISTINCT FROM parent.fx_rate_to_secondary
    OR NEW.is_excluded           IS DISTINCT FROM parent.is_excluded
    OR (
      NEW.txn_type IS DISTINCT FROM parent.txn_type
      AND NOT (
        current_user = pg_get_userbyid((
          SELECT relation.relowner FROM pg_class relation
           WHERE relation.oid = 'investment_cash_allocation'::regclass
        ))
        AND current_setting('oneledger.investment_cash_allocation_write', true) = 'on'
        AND EXISTS (
          SELECT FROM investment_cash_allocation allocation
           WHERE allocation.cash_transaction_id = NEW.id
             AND CASE allocation.role
                   WHEN 'funding' THEN 'investment_buy'
                   WHEN 'proceeds' THEN 'investment_sell'
                   WHEN 'cash_distribution' THEN 'investment_income'
                   WHEN 'fee' THEN 'investment_fee'
                 END = NEW.txn_type::TEXT
        )
      )
    )
    OR NEW.transfer_id           IS DISTINCT FROM parent.transfer_id
    OR NEW.card_statement_id     IS DISTINCT FROM parent.card_statement_id
    OR NEW.loan_id               IS DISTINCT FROM parent.loan_id
    OR NEW.loan_schedule_entry_id IS DISTINCT FROM parent.loan_schedule_entry_id
    OR NEW.recurring_series_id   IS DISTINCT FROM parent.recurring_series_id
    OR NEW.merchant_id           IS DISTINCT FROM parent.merchant_id
    OR NEW.country               IS DISTINCT FROM parent.country
    OR NEW.payment_method        IS DISTINCT FROM parent.payment_method
    OR NEW.source                IS DISTINCT FROM parent.source
    OR NEW.source_page_number    IS DISTINCT FROM parent.source_page_number
    OR NEW.source_row_number     IS DISTINCT FROM parent.source_row_number
    OR NEW.import_batch_id       IS DISTINCT FROM parent.import_batch_id
    OR NEW.statement_layout_id   IS DISTINCT FROM parent.statement_layout_id
    OR NEW.layout_version        IS DISTINCT FROM parent.layout_version
    OR NEW.value_date            IS DISTINCT FROM parent.value_date
    OR NEW.description_raw       IS DISTINCT FROM parent.description_raw
    OR NEW.description_clean     IS DISTINCT FROM parent.description_clean
    OR NEW.reference_raw         IS DISTINCT FROM parent.reference_raw
    OR NEW.counterparty_raw      IS DISTINCT FROM parent.counterparty_raw
    OR NEW.original_currency     IS DISTINCT FROM parent.original_currency
    OR (NEW.original_amount IS NULL) <> (parent.original_amount IS NULL)
    OR NEW.receipt_document_id   IS DISTINCT FROM parent.receipt_document_id
    OR NEW.tags                  IS DISTINCT FROM parent.tags THEN
      RAISE EXCEPTION 'a split child inherits everything but amount, category, notes and an allocated investment type; % diverges from parent %', NEW.id, parent.id;
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_split_one_level
  BEFORE INSERT OR UPDATE ON transaction
  FOR EACH ROW EXECUTE FUNCTION assert_split_one_level();

-- The views store transaction's row shape, so recreate both around the legacy-column removal.
DROP VIEW v_transaction_reportable;
DROP VIEW v_transaction_posting;
ALTER TABLE transaction DROP CONSTRAINT IF EXISTS fk_txn_inv_txn;
ALTER TABLE transaction DROP COLUMN investment_transaction_id;

CREATE VIEW v_transaction_posting WITH (security_invoker = true) AS
SELECT ledger.* FROM transaction ledger
WHERE NOT EXISTS (
  SELECT 1 FROM transaction child WHERE child.parent_transaction_id = ledger.id);

CREATE VIEW v_transaction_reportable WITH (security_invoker = true) AS
SELECT * FROM v_transaction_posting
WHERE transfer_id IS NULL
  AND is_excluded = false
  AND txn_type::TEXT NOT IN (
    'opening_balance', 'adjustment', 'transfer_in', 'transfer_out',
    'investment_buy', 'investment_sell', 'investment_income', 'investment_fee',
    'loan_disbursement', 'loan_repayment');

CREATE OR REPLACE FUNCTION classification_state_is_valid(value JSONB) RETURNS BOOLEAN
  LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  allowed CONSTANT TEXT[] := ARRAY[
    'txn_type', 'category_id', 'merchant_id', 'payment_method', 'classified_by',
    'classification_rule_id', 'classification_confidence', 'is_reviewed', 'country',
    'tags', 'is_excluded'
  ];
  key TEXT;
  item JSONB;
  uuid_pattern CONSTANT TEXT :=
    '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
BEGIN
  IF jsonb_typeof(value) <> 'object' OR NOT (value ?& allowed) THEN RETURN false; END IF;
  FOR key IN SELECT jsonb_object_keys(value) LOOP
    IF NOT (key = ANY(allowed)) THEN RETURN false; END IF;
  END LOOP;
  IF jsonb_typeof(value->'txn_type') <> 'string'
     OR value->>'txn_type' NOT IN (
       'income', 'expense', 'fee', 'interest', 'tax', 'refund',
       'transfer_in', 'transfer_out', 'investment_buy', 'investment_sell',
       'investment_income', 'investment_fee', 'loan_disbursement', 'loan_repayment',
       'adjustment', 'opening_balance') THEN RETURN false;
  END IF;
  IF jsonb_typeof(value->'classified_by') <> 'string'
     OR value->>'classified_by' NOT IN (
       'user_manual', 'user_rule', 'merchant_alias', 'recurring_series', 'prior_match',
       'seed_rule', 'unclassified', 'ai') THEN RETURN false;
  END IF;
  IF value->'payment_method' <> 'null'::JSONB
     AND (jsonb_typeof(value->'payment_method') <> 'string'
       OR value->>'payment_method' NOT IN (
         'card', 'contactless', 'atm', 'cash', 'cheque', 'upi', 'neft', 'imps',
         'rtgs', 'wire', 'direct_debit', 'standing_instruction', 'wallet', 'online',
         'other')) THEN RETURN false;
  END IF;
  FOREACH key IN ARRAY ARRAY['category_id', 'merchant_id', 'classification_rule_id'] LOOP
    IF value->key <> 'null'::JSONB
       AND (jsonb_typeof(value->key) <> 'string' OR value->>key !~ uuid_pattern) THEN
      RETURN false;
    END IF;
  END LOOP;
  IF value->'classification_confidence' <> 'null'::JSONB
     AND (jsonb_typeof(value->'classification_confidence') <> 'number'
       OR (value->>'classification_confidence')::NUMERIC < 0
       OR (value->>'classification_confidence')::NUMERIC > 1) THEN RETURN false;
  END IF;
  IF jsonb_typeof(value->'is_reviewed') <> 'boolean'
     OR jsonb_typeof(value->'is_excluded') <> 'boolean' THEN RETURN false;
  END IF;
  IF value->'country' <> 'null'::JSONB
     AND (jsonb_typeof(value->'country') <> 'string'
       OR length(value->>'country') <> 2) THEN RETURN false;
  END IF;
  IF value->'tags' <> 'null'::JSONB THEN
    IF jsonb_typeof(value->'tags') <> 'array' OR jsonb_array_length(value->'tags') > 32 THEN
      RETURN false;
    END IF;
    FOR item IN SELECT * FROM jsonb_array_elements(value->'tags') LOOP
      IF jsonb_typeof(item) <> 'string' OR length(item #>> '{}') > 100 THEN RETURN false; END IF;
    END LOOP;
  END IF;
  RETURN octet_length(value::TEXT) <= 4096;
EXCEPTION WHEN OTHERS THEN
  RETURN false;
END $$;

CREATE FUNCTION confirm_investment_cash_allocations(
  p_investment_transaction_ids UUID[],
  p_cash_transaction_ids UUID[],
  p_roles investment_cash_allocation_role[],
  p_allocated_amounts NUMERIC[]
) RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; actor_id UUID; item_count INTEGER; item_index INTEGER;
  item_event investment_transaction%ROWTYPE; item_cash transaction%ROWTYPE;
  cash_group RECORD; event_group RECORD; expected_type TEXT;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  actor_id := NULLIF(current_setting('app.user_id', true), '')::UUID;
  item_count := cardinality(p_investment_transaction_ids);
  IF item_count IS NULL OR item_count = 0
     OR cardinality(p_cash_transaction_ids) <> item_count
     OR cardinality(p_roles) <> item_count
     OR cardinality(p_allocated_amounts) <> item_count THEN
    RAISE EXCEPTION 'cash allocation arrays must be non-empty and equal length'
      USING ERRCODE = '22023';
  END IF;
  IF EXISTS (
    SELECT FROM unnest(p_investment_transaction_ids, p_cash_transaction_ids)
      AS item(event_id, cash_id)
     WHERE event_id IS NULL OR cash_id IS NULL
  ) OR EXISTS (
    SELECT FROM unnest(p_investment_transaction_ids, p_cash_transaction_ids)
      AS item(event_id, cash_id)
     GROUP BY event_id, cash_id HAVING count(*) > 1
  ) OR EXISTS (SELECT FROM unnest(p_roles) role WHERE role IS NULL)
    OR EXISTS (SELECT FROM unnest(p_allocated_amounts) amount WHERE amount IS NULL) THEN
    RAISE EXCEPTION 'cash allocation items must be complete and unique'
      USING ERRCODE = '22023';
  END IF;
  FOR item_index IN 1..item_count LOOP
    IF p_allocated_amounts[item_index] IS DISTINCT FROM
         round(p_allocated_amounts[item_index], 4) THEN
      RAISE EXCEPTION 'cash allocation money exceeds scale 4'
        USING ERRCODE = '22003';
    END IF;
    IF p_allocated_amounts[item_index] <= 0 THEN
      RAISE EXCEPTION 'cash allocation amounts must be positive'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_cash_allocation_amount_is_positive';
    END IF;
  END LOOP;

  PERFORM 1 FROM investment_transaction event
   WHERE event.id = ANY(p_investment_transaction_ids)
     AND event.household_id = owner_household ORDER BY event.id FOR UPDATE;
  IF (SELECT count(DISTINCT event.id) FROM investment_transaction event
       WHERE event.id = ANY(p_investment_transaction_ids)
         AND event.household_id = owner_household)
     <> (SELECT count(DISTINCT id) FROM unnest(p_investment_transaction_ids) id) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  PERFORM 1 FROM transaction cash
   WHERE cash.id = ANY(p_cash_transaction_ids)
     AND cash.household_id = owner_household ORDER BY cash.id FOR UPDATE;
  IF (SELECT count(DISTINCT cash.id) FROM transaction cash
       WHERE cash.id = ANY(p_cash_transaction_ids)
         AND cash.household_id = owner_household)
     <> (SELECT count(DISTINCT id) FROM unnest(p_cash_transaction_ids) id) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;

  FOR cash_group IN
    SELECT cash_id, min(role::TEXT) AS first_role, max(role::TEXT) AS last_role,
           sum(amount) AS allocated_total
      FROM unnest(p_cash_transaction_ids, p_roles, p_allocated_amounts)
        AS item(cash_id, role, amount)
     GROUP BY cash_id ORDER BY cash_id
  LOOP
    IF cash_group.first_role <> cash_group.last_role THEN
      RAISE EXCEPTION 'one cash leaf cannot mix investment allocation roles'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_cash_leaf_has_one_role';
    END IF;
    SELECT * INTO STRICT item_cash FROM transaction WHERE id = cash_group.cash_id;
    IF EXISTS (
      SELECT FROM transaction child WHERE child.parent_transaction_id = item_cash.id
    ) THEN
      RAISE EXCEPTION 'a split parent cannot be investment cash evidence'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_cash_transaction_is_leaf';
    END IF;
    IF EXISTS (
      SELECT FROM investment_cash_allocation WHERE cash_transaction_id = item_cash.id
    ) THEN
      RAISE EXCEPTION 'a cash leaf is already fully allocated'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_cash_leaf_is_fully_allocated';
    END IF;
    IF item_cash.txn_type::TEXT IN (
      'investment_buy', 'investment_sell', 'investment_income', 'investment_fee'
    ) THEN
      RAISE EXCEPTION 'cash allocation requires an ordinary prior transaction type'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_cash_prior_type_is_ordinary';
    END IF;
    IF cash_group.allocated_total <> abs(item_cash.amount) THEN
      RAISE EXCEPTION 'a confirmed investment cash leaf must be fully allocated'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_cash_leaf_is_fully_allocated';
    END IF;
  END LOOP;

  FOR item_index IN 1..item_count LOOP
    SELECT * INTO STRICT item_event FROM investment_transaction
     WHERE id = p_investment_transaction_ids[item_index];
    SELECT * INTO STRICT item_cash FROM transaction
     WHERE id = p_cash_transaction_ids[item_index];
    IF rtrim(item_event.currency) <> rtrim(item_cash.currency) THEN
      RAISE EXCEPTION 'cash allocation currency must match its investment event'
        USING ERRCODE = '23514', CONSTRAINT = 'investment_cash_currency_matches_event';
    END IF;
    IF NOT (
      (p_roles[item_index] = 'funding'
        AND item_event.event_kind IN ('purchase', 'recurring_purchase', 'rights')
        AND item_cash.amount < 0)
      OR (p_roles[item_index] = 'proceeds'
        AND item_event.event_kind IN ('sale', 'redemption', 'maturity')
        AND item_cash.amount > 0)
      OR (p_roles[item_index] = 'cash_distribution'
        AND item_event.event_kind IN ('cash_distribution', 'interest')
        AND item_cash.amount > 0)
      OR (p_roles[item_index] = 'fee'
        AND item_event.event_kind IN ('charge', 'withheld_tax')
        AND item_cash.amount < 0)
    ) THEN
      RAISE EXCEPTION 'cash allocation role is incompatible with its event or cash direction'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_cash_role_matches_event';
    END IF;
  END LOOP;

  FOR event_group IN
    SELECT event_id, sum(amount) AS allocated_total
      FROM unnest(p_investment_transaction_ids, p_allocated_amounts)
        AS item(event_id, amount)
     GROUP BY event_id ORDER BY event_id
  LOOP
    SELECT * INTO STRICT item_event FROM investment_transaction
     WHERE id = event_group.event_id;
    IF event_group.allocated_total + COALESCE((
      SELECT sum(allocated_amount) FROM investment_cash_allocation
       WHERE investment_transaction_id = item_event.id
    ), 0) > abs(item_event.net_amount) THEN
      RAISE EXCEPTION 'cash allocations cannot exceed the investment event cash side'
        USING ERRCODE = '23514',
              CONSTRAINT = 'investment_event_cash_is_not_overallocated';
    END IF;
  END LOOP;

  PERFORM set_config('oneledger.investment_cash_allocation_write', 'on', true);
  FOR item_index IN 1..item_count LOOP
    SELECT * INTO STRICT item_cash FROM transaction
     WHERE id = p_cash_transaction_ids[item_index];
    INSERT INTO investment_cash_allocation (
      household_id, investment_transaction_id, cash_transaction_id, role,
      allocated_amount, prior_txn_type, allocated_by
    ) VALUES (
      owner_household, p_investment_transaction_ids[item_index], item_cash.id,
      p_roles[item_index], p_allocated_amounts[item_index], item_cash.txn_type, actor_id
    );
  END LOOP;
  FOR cash_group IN
    SELECT cash_id, min(role::TEXT)::investment_cash_allocation_role AS role
      FROM unnest(p_cash_transaction_ids, p_roles) AS item(cash_id, role)
     GROUP BY cash_id ORDER BY cash_id
  LOOP
    expected_type := investment_cash_type_for_role(cash_group.role);
    UPDATE transaction SET txn_type = expected_type::txn_type
     WHERE id = cash_group.cash_id;
  END LOOP;
  PERFORM set_config('oneledger.investment_cash_allocation_write', 'off', true);
END $$;

CREATE FUNCTION unlink_investment_cash_allocations(p_cash_transaction_id UUID)
  RETURNS VOID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE owner_household UUID; prior_type txn_type; prior_count INTEGER;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  PERFORM 1 FROM transaction
   WHERE id = p_cash_transaction_id AND household_id = owner_household FOR UPDATE;
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

CREATE FUNCTION allocate_investment_lots(
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
      SELECT sum(allocated_units) FROM investment_lot_allocation
       WHERE acquisition_lot_id = source_lot.id
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

REVOKE ALL ON FUNCTION investment_cash_type_for_role(investment_cash_allocation_role) FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_investment_cash_allocation_write() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_allocated_cash_classification() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_investment_lot_allocation_write() FROM PUBLIC;

ALTER TABLE investment_cash_allocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_lot_allocation ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON investment_cash_allocation FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_lot_allocation FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
