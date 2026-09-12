-- Phase 1.6: investment import parents, atomic groups, typed drafts, checkpoints,
-- evidence attachments and fixed-severity review items.

CREATE TYPE investment_import_status AS ENUM (
  'staged', 'awaiting_review', 'partially_committed', 'completed', 'cancelled', 'failed'
);
CREATE TYPE investment_group_status AS ENUM (
  'draft', 'ready', 'blocked', 'committed', 'cancelled'
);
CREATE TYPE investment_draft_status AS ENUM (
  'pending', 'ready', 'duplicate', 'conflict', 'blocked', 'committed'
);
CREATE TYPE investment_checkpoint_status AS ENUM (
  'pending', 'matched', 'mismatched', 'blocked'
);
CREATE TYPE investment_review_reason AS ENUM (
  'unsupported_format', 'unknown_security', 'ambiguous_account',
  'ambiguous_identifier', 'unrecognized_event', 'duplicate_conflict',
  'unit_mismatch', 'cost_mismatch', 'missing_price', 'unmatched_cash',
  'incomplete_lot_allocation', 'stale_valuation'
);
CREATE TYPE investment_review_status AS ENUM ('open', 'resolved');
CREATE TYPE investment_review_resolution_kind AS ENUM (
  'account_resolved', 'security_resolved', 'event_corrected',
  'evidence_confirmed', 'checkpoint_reconciled', 'price_recorded',
  'funding_linked', 'lot_allocated', 'unsupported_acknowledged'
);

CREATE TABLE investment_import (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id       UUID NOT NULL REFERENCES household(id),
  document_id        UUID NOT NULL,
  source_contract    TEXT NOT NULL,
  extraction_version TEXT NOT NULL,
  status             investment_import_status NOT NULL DEFAULT 'staged',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at       TIMESTAMPTZ,

  CONSTRAINT fk_investment_import_document_household
    FOREIGN KEY (household_id, document_id)
    REFERENCES document(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_import_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_investment_import_document UNIQUE (document_id),
  CONSTRAINT investment_import_source_is_safe CHECK (
    length(source_contract) BETWEEN 3 AND 64
    AND source_contract ~ '^[a-z][a-z0-9_]+$'
    AND length(extraction_version) BETWEEN 1 AND 40
    AND octet_length(extraction_version) <= 160),
  CONSTRAINT investment_import_completion_agrees CHECK (
    (status = 'completed' AND completed_at IS NOT NULL)
    OR (status <> 'completed' AND completed_at IS NULL))
);

CREATE INDEX idx_investment_import_status
  ON investment_import (household_id, status, created_at DESC);

CREATE FUNCTION assert_investment_import_document() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE target_household UUID; target_type document_type;
BEGIN
  SELECT household_id, document_type INTO target_household, target_type
    FROM document WHERE id = NEW.document_id FOR KEY SHARE;
  IF NOT FOUND OR target_household <> NEW.household_id THEN
    RETURN NEW;
  END IF;
  IF target_type NOT IN ('investment_statement', 'cas') THEN
    RAISE EXCEPTION 'investment imports require an investment document'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_import_uses_investment_document';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_import_document
  BEFORE INSERT OR UPDATE OF household_id, document_id ON investment_import
  FOR EACH ROW EXECUTE FUNCTION assert_investment_import_document();

CREATE TABLE investment_import_group (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  import_id    UUID NOT NULL,
  group_number INTEGER NOT NULL,
  status       investment_group_status NOT NULL DEFAULT 'draft',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  committed_at TIMESTAMPTZ,

  CONSTRAINT fk_investment_import_group_import_household
    FOREIGN KEY (household_id, import_id)
    REFERENCES investment_import(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_import_group_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_investment_import_group_scope UNIQUE (household_id, id, import_id),
  CONSTRAINT uq_investment_import_group_number UNIQUE (import_id, group_number),
  CONSTRAINT investment_import_group_number_is_positive CHECK (group_number > 0),
  CONSTRAINT investment_import_group_commit_agrees CHECK (
    (status = 'committed' AND committed_at IS NOT NULL)
    OR (status <> 'committed' AND committed_at IS NULL))
);

CREATE INDEX idx_investment_import_group_status
  ON investment_import_group (household_id, import_id, status, group_number);

CREATE TABLE investment_event_draft (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  group_id                  UUID NOT NULL,
  source_order              INTEGER NOT NULL,
  account_id                UUID,
  security_id               UUID REFERENCES security(id) ON DELETE RESTRICT,
  event_date                DATE,
  event_kind                investment_event_kind,
  units                     NUMERIC(28,8),
  price                     NUMERIC(20,6),
  gross_amount              NUMERIC(20,4),
  charges                   NUMERIC(20,4),
  withheld_tax              NUMERIC(20,4),
  net_amount                NUMERIC(20,4),
  currency                  CHAR(3) REFERENCES currency(code),
  state                     investment_draft_status NOT NULL DEFAULT 'pending',
  committed_transaction_id  UUID,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_event_draft_group_household
    FOREIGN KEY (household_id, group_id)
    REFERENCES investment_import_group(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_event_draft_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_event_draft_committed_household
    FOREIGN KEY (household_id, committed_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_event_draft_scope UNIQUE (household_id, id, group_id),
  CONSTRAINT uq_investment_event_draft_order UNIQUE (group_id, source_order),
  CONSTRAINT investment_event_draft_order_is_positive CHECK (source_order > 0),
  CONSTRAINT investment_event_draft_amounts_are_nonnegative CHECK (
    (units IS NULL OR units <> 0)
    AND (price IS NULL OR price > 0)
    AND (gross_amount IS NULL OR gross_amount > 0)
    AND (charges IS NULL OR charges >= 0)
    AND (withheld_tax IS NULL OR withheld_tax >= 0)),
  CONSTRAINT investment_event_draft_ready_is_complete CHECK (
    state NOT IN ('ready', 'duplicate', 'committed')
    OR (account_id IS NOT NULL AND security_id IS NOT NULL AND event_date IS NOT NULL
      AND event_kind IS NOT NULL AND charges IS NOT NULL AND withheld_tax IS NOT NULL
      AND net_amount IS NOT NULL AND currency IS NOT NULL)),
  CONSTRAINT investment_event_draft_commit_agrees CHECK (
    (state = 'committed' AND committed_transaction_id IS NOT NULL)
    OR (state <> 'committed' AND committed_transaction_id IS NULL))
);

CREATE INDEX idx_investment_event_draft_group
  ON investment_event_draft (household_id, group_id, source_order);

CREATE TABLE investment_position_checkpoint (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  group_id               UUID NOT NULL,
  source_order           INTEGER NOT NULL,
  account_id             UUID,
  security_id            UUID REFERENCES security(id) ON DELETE RESTRICT,
  holding_id             UUID,
  as_of_date             DATE NOT NULL,
  reported_units         NUMERIC(28,8),
  reported_cost_native   NUMERIC(20,4),
  reported_value_native  NUMERIC(20,4),
  currency               CHAR(3) REFERENCES currency(code),
  declared_unit_scale    SMALLINT,
  reconciliation_status  investment_checkpoint_status NOT NULL DEFAULT 'pending',
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_checkpoint_group_household
    FOREIGN KEY (household_id, group_id)
    REFERENCES investment_import_group(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_checkpoint_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_checkpoint_holding_household
    FOREIGN KEY (household_id, holding_id)
    REFERENCES holding(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_checkpoint_scope UNIQUE (household_id, id, group_id),
  CONSTRAINT uq_investment_checkpoint_order UNIQUE (group_id, source_order),
  CONSTRAINT investment_checkpoint_order_is_positive CHECK (source_order > 0),
  CONSTRAINT investment_checkpoint_has_measurement CHECK (
    num_nonnulls(reported_units, reported_cost_native, reported_value_native) > 0),
  CONSTRAINT investment_checkpoint_values_are_nonnegative CHECK (
    (reported_units IS NULL OR reported_units >= 0)
    AND (reported_cost_native IS NULL OR reported_cost_native >= 0)
    AND (reported_value_native IS NULL OR reported_value_native >= 0)),
  CONSTRAINT investment_checkpoint_money_has_currency CHECK (
    (reported_cost_native IS NULL AND reported_value_native IS NULL)
    OR currency IS NOT NULL),
  CONSTRAINT investment_checkpoint_unit_scale_agrees CHECK (
    (reported_units IS NULL AND declared_unit_scale IS NULL)
    OR (reported_units IS NOT NULL AND declared_unit_scale BETWEEN 0 AND 8)),
  CONSTRAINT investment_checkpoint_match_has_holding CHECK (
    reconciliation_status <> 'matched' OR holding_id IS NOT NULL)
);

CREATE INDEX idx_investment_checkpoint_group
  ON investment_position_checkpoint (household_id, group_id, source_order);

CREATE FUNCTION assert_investment_staging_security_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE target_household UUID; target_currency CHAR(3);
BEGIN
  IF NEW.security_id IS NULL THEN RETURN NEW; END IF;
  SELECT household_id, currency INTO target_household, target_currency
    FROM security WHERE id = NEW.security_id FOR KEY SHARE;
  IF NOT FOUND OR (
    target_household IS NOT NULL AND target_household <> NEW.household_id
  ) THEN
    RAISE EXCEPTION 'staged investment security is outside its household'
      USING ERRCODE = '23503',
            CONSTRAINT = CASE
              WHEN TG_TABLE_NAME = 'investment_event_draft'
                THEN 'fk_investment_event_draft_security_household'
              ELSE 'fk_investment_checkpoint_security_household'
            END;
  END IF;
  IF NEW.currency IS NOT NULL AND rtrim(NEW.currency) <> rtrim(target_currency) THEN
    RAISE EXCEPTION 'staged investment currency must match its security'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_staging_currency_matches_security';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_event_draft_security_scope
  BEFORE INSERT OR UPDATE OF household_id, security_id, currency ON investment_event_draft
  FOR EACH ROW EXECUTE FUNCTION assert_investment_staging_security_scope();
CREATE TRIGGER trg_investment_checkpoint_security_scope
  BEFORE INSERT OR UPDATE OF household_id, security_id, currency
  ON investment_position_checkpoint
  FOR EACH ROW EXECUTE FUNCTION assert_investment_staging_security_scope();

CREATE TABLE investment_evidence (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  group_id                  UUID NOT NULL,
  document_id               UUID NOT NULL,
  draft_id                  UUID,
  checkpoint_id             UUID,
  investment_transaction_id UUID,
  page_number               SMALLINT,
  row_number                INTEGER,
  source_contract           TEXT NOT NULL,
  extraction_version        TEXT NOT NULL,
  source_event_id           TEXT,
  fingerprint_version       TEXT NOT NULL,
  evidence_fingerprint      TEXT NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_evidence_group_household
    FOREIGN KEY (household_id, group_id)
    REFERENCES investment_import_group(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_evidence_document_household
    FOREIGN KEY (household_id, document_id)
    REFERENCES document(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_evidence_draft_scope
    FOREIGN KEY (household_id, draft_id, group_id)
    REFERENCES investment_event_draft(household_id, id, group_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_evidence_checkpoint_scope
    FOREIGN KEY (household_id, checkpoint_id, group_id)
    REFERENCES investment_position_checkpoint(household_id, id, group_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_evidence_event_household
    FOREIGN KEY (household_id, investment_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_investment_evidence_household_id UNIQUE (household_id, id),
  CONSTRAINT investment_evidence_has_one_target CHECK (
    num_nonnulls(draft_id, checkpoint_id, investment_transaction_id) = 1),
  CONSTRAINT investment_evidence_location_is_valid CHECK (
    (page_number IS NULL OR page_number > 0)
    AND (row_number IS NULL OR row_number > 0)),
  CONSTRAINT investment_evidence_source_is_safe CHECK (
    length(source_contract) BETWEEN 3 AND 64
    AND source_contract ~ '^[a-z][a-z0-9_]+$'
    AND length(extraction_version) BETWEEN 1 AND 40
    AND octet_length(extraction_version) <= 160
    AND (source_event_id IS NULL OR (
      length(source_event_id) BETWEEN 1 AND 128 AND octet_length(source_event_id) <= 512))
    AND length(fingerprint_version) BETWEEN 1 AND 40
    AND octet_length(fingerprint_version) <= 160
    AND evidence_fingerprint ~ '^[0-9a-f]{64}$')
);

CREATE UNIQUE INDEX idx_investment_evidence_document_fingerprint
  ON investment_evidence (
    household_id, document_id, fingerprint_version, evidence_fingerprint);
CREATE UNIQUE INDEX idx_investment_evidence_document_source_identity
  ON investment_evidence (household_id, document_id, source_contract, source_event_id)
  WHERE source_event_id IS NOT NULL;
CREATE INDEX idx_investment_evidence_group
  ON investment_evidence (household_id, group_id, created_at);

CREATE TABLE investment_review_item (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  import_id               UUID,
  group_id                UUID,
  draft_id                UUID,
  checkpoint_id           UUID,
  investment_transaction_id UUID,
  holding_id              UUID,
  reason                  investment_review_reason NOT NULL,
  is_blocking             BOOLEAN GENERATED ALWAYS AS (reason IN (
    'unsupported_format', 'unknown_security', 'ambiguous_account',
    'ambiguous_identifier', 'unrecognized_event', 'duplicate_conflict',
    'unit_mismatch', 'cost_mismatch')) STORED,
  status                  investment_review_status NOT NULL DEFAULT 'open',
  resolution_kind         investment_review_resolution_kind,
  resolution_reference_id UUID,
  resolved_by             UUID,
  resolved_at             TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_investment_review_import_household
    FOREIGN KEY (household_id, import_id)
    REFERENCES investment_import(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_review_group_scope
    FOREIGN KEY (household_id, group_id, import_id)
    REFERENCES investment_import_group(household_id, id, import_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_review_draft_scope
    FOREIGN KEY (household_id, draft_id, group_id)
    REFERENCES investment_event_draft(household_id, id, group_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_review_checkpoint_scope
    FOREIGN KEY (household_id, checkpoint_id, group_id)
    REFERENCES investment_position_checkpoint(household_id, id, group_id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_review_event_household
    FOREIGN KEY (household_id, investment_transaction_id)
    REFERENCES investment_transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_review_holding_household
    FOREIGN KEY (household_id, holding_id)
    REFERENCES holding(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_investment_review_resolver_household
    FOREIGN KEY (household_id, resolved_by)
    REFERENCES app_user(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT investment_review_has_subject CHECK (
    num_nonnulls(import_id, group_id, draft_id, checkpoint_id,
      investment_transaction_id, holding_id) > 0),
  CONSTRAINT investment_review_group_has_import CHECK (
    group_id IS NULL OR import_id IS NOT NULL),
  CONSTRAINT investment_review_resolution_agrees CHECK (
    (status = 'open' AND resolution_kind IS NULL AND resolution_reference_id IS NULL
      AND resolved_by IS NULL AND resolved_at IS NULL)
    OR (status = 'resolved' AND resolution_kind IS NOT NULL
      AND resolution_reference_id IS NOT NULL AND resolved_by IS NOT NULL
      AND resolved_at IS NOT NULL))
);

CREATE UNIQUE INDEX idx_investment_review_one_open_reason
  ON investment_review_item (
    household_id, reason, import_id, group_id, draft_id, checkpoint_id,
    investment_transaction_id, holding_id) NULLS NOT DISTINCT
  WHERE status = 'open';
CREATE INDEX idx_investment_review_queue
  ON investment_review_item (household_id, status, is_blocking DESC, created_at);

CREATE FUNCTION preserve_investment_evidence() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'investment_evidence is immutable'
    USING ERRCODE = '55000';
END $$;

CREATE FUNCTION guard_investment_evidence_insert() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('oneledger.investment_evidence_append', true)
       IS DISTINCT FROM 'on' THEN
    RAISE EXCEPTION 'investment evidence must use record_investment_evidence'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_evidence_is_immutable
  BEFORE UPDATE OR DELETE ON investment_evidence
  FOR EACH ROW EXECUTE FUNCTION preserve_investment_evidence();
CREATE TRIGGER trg_investment_evidence_uses_operation
  BEFORE INSERT ON investment_evidence
  FOR EACH ROW EXECUTE FUNCTION guard_investment_evidence_insert();

CREATE FUNCTION preserve_investment_review_item() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'investment_review_item is append-only'
      USING ERRCODE = '55000';
  END IF;
  RAISE EXCEPTION 'investment review state requires a validated resolution operation'
    USING ERRCODE = '55000';
END $$;

CREATE TRIGGER trg_investment_review_item_is_append_only
  BEFORE UPDATE OR DELETE ON investment_review_item
  FOR EACH ROW EXECUTE FUNCTION preserve_investment_review_item();

CREATE FUNCTION guard_investment_group_transition() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'investment import groups retain their history'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.household_id IS DISTINCT FROM OLD.household_id
     OR NEW.import_id IS DISTINCT FROM OLD.import_id
     OR NEW.group_number IS DISTINCT FROM OLD.group_number
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'investment import group identity is immutable'
      USING ERRCODE = '55000';
  END IF;
  IF OLD.status IN ('committed', 'cancelled')
     AND NEW.status IS DISTINCT FROM OLD.status THEN
    RAISE EXCEPTION 'terminal investment import groups cannot be reopened'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.status = 'committed' AND EXISTS (
    SELECT FROM investment_review_item
     WHERE group_id = OLD.id AND status = 'open' AND is_blocking
  ) THEN
    RAISE EXCEPTION 'investment group has an unresolved blocking review'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_group_has_open_blocker';
  END IF;
  IF NEW.status = 'ready' AND EXISTS (
    SELECT FROM investment_review_item
     WHERE group_id = OLD.id AND status = 'open' AND is_blocking
  ) THEN
    RAISE EXCEPTION 'investment group cannot be ready with a blocking review'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_group_has_open_blocker';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_import_group_transition
  BEFORE UPDATE OR DELETE ON investment_import_group
  FOR EACH ROW EXECUTE FUNCTION guard_investment_group_transition();

CREATE FUNCTION apply_investment_review_block() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'open' THEN
    IF NEW.is_blocking AND NEW.group_id IS NOT NULL THEN
      UPDATE investment_import_group
         SET status = 'blocked', updated_at = now(), committed_at = NULL
       WHERE id = NEW.group_id AND status NOT IN ('committed', 'cancelled');
    END IF;
    IF NEW.import_id IS NOT NULL THEN
      UPDATE investment_import
         SET status = 'awaiting_review', updated_at = now(), completed_at = NULL
       WHERE id = NEW.import_id AND status NOT IN ('completed', 'cancelled');
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_investment_review_item_blocks_group
  AFTER INSERT ON investment_review_item
  FOR EACH ROW EXECUTE FUNCTION apply_investment_review_block();

CREATE FUNCTION record_investment_evidence(
  p_group_id UUID,
  p_document_id UUID,
  p_draft_id UUID,
  p_checkpoint_id UUID,
  p_investment_transaction_id UUID,
  p_page_number SMALLINT,
  p_row_number INTEGER,
  p_source_contract TEXT,
  p_extraction_version TEXT,
  p_source_event_id TEXT,
  p_fingerprint_version TEXT,
  p_evidence_fingerprint TEXT
) RETURNS UUID
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  owner_household UUID; target_import UUID; existing investment_evidence%ROWTYPE;
  new_id UUID;
BEGIN
  PERFORM assert_is_owner();
  owner_household := current_household_id();
  IF num_nonnulls(p_draft_id, p_checkpoint_id, p_investment_transaction_id) <> 1 THEN
    RAISE EXCEPTION 'investment evidence requires exactly one target'
      USING ERRCODE = '23514', CONSTRAINT = 'investment_evidence_has_one_target';
  END IF;

  SELECT child.import_id INTO target_import
    FROM investment_import_group child
    JOIN investment_import parent
      ON parent.household_id = child.household_id AND parent.id = child.import_id
   WHERE child.id = p_group_id AND child.household_id = owner_household
     AND parent.document_id = p_document_id
   FOR UPDATE OF parent, child;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF p_draft_id IS NOT NULL AND NOT EXISTS (
    SELECT FROM investment_event_draft
     WHERE household_id = owner_household AND group_id = p_group_id AND id = p_draft_id
  ) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF p_checkpoint_id IS NOT NULL AND NOT EXISTS (
    SELECT FROM investment_position_checkpoint
     WHERE household_id = owner_household AND group_id = p_group_id AND id = p_checkpoint_id
  ) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
  IF p_investment_transaction_id IS NOT NULL AND NOT EXISTS (
    SELECT FROM investment_transaction
     WHERE household_id = owner_household AND id = p_investment_transaction_id
  ) THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;

  SELECT * INTO existing
    FROM investment_evidence
   WHERE household_id = owner_household AND document_id = p_document_id
     AND ((p_source_event_id IS NOT NULL
       AND source_contract = p_source_contract AND source_event_id = p_source_event_id)
       OR (fingerprint_version = p_fingerprint_version
         AND evidence_fingerprint = p_evidence_fingerprint))
   ORDER BY created_at, id
   LIMIT 1;
  IF FOUND THEN
    IF (existing.group_id, existing.draft_id, existing.checkpoint_id,
        existing.investment_transaction_id, existing.page_number, existing.row_number,
        existing.source_contract, existing.extraction_version, existing.source_event_id,
        existing.fingerprint_version, existing.evidence_fingerprint)
       IS NOT DISTINCT FROM
       (p_group_id, p_draft_id, p_checkpoint_id, p_investment_transaction_id,
        p_page_number, p_row_number, p_source_contract, p_extraction_version,
        p_source_event_id, p_fingerprint_version, p_evidence_fingerprint) THEN
      RETURN existing.id;
    END IF;
    INSERT INTO investment_review_item (
      household_id, import_id, group_id, draft_id, checkpoint_id,
      investment_transaction_id, reason
    ) VALUES (
      owner_household, target_import, p_group_id, p_draft_id, p_checkpoint_id,
      p_investment_transaction_id, 'duplicate_conflict'
    ) ON CONFLICT DO NOTHING;
    RETURN NULL;
  END IF;

  PERFORM set_config('oneledger.investment_evidence_append', 'on', true);
  INSERT INTO investment_evidence (
    household_id, group_id, document_id, draft_id, checkpoint_id,
    investment_transaction_id, page_number, row_number, source_contract,
    extraction_version, source_event_id, fingerprint_version, evidence_fingerprint
  ) VALUES (
    owner_household, p_group_id, p_document_id, p_draft_id, p_checkpoint_id,
    p_investment_transaction_id, p_page_number, p_row_number, p_source_contract,
    p_extraction_version, p_source_event_id, p_fingerprint_version,
    p_evidence_fingerprint
  ) RETURNING id INTO new_id;
  PERFORM set_config('oneledger.investment_evidence_append', 'off', true);
  RETURN new_id;
END $$;

REVOKE ALL ON FUNCTION assert_investment_import_document() FROM PUBLIC;
REVOKE ALL ON FUNCTION assert_investment_staging_security_scope() FROM PUBLIC;
REVOKE ALL ON FUNCTION preserve_investment_evidence() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_investment_evidence_insert() FROM PUBLIC;
REVOKE ALL ON FUNCTION preserve_investment_review_item() FROM PUBLIC;
REVOKE ALL ON FUNCTION guard_investment_group_transition() FROM PUBLIC;
REVOKE ALL ON FUNCTION apply_investment_review_block() FROM PUBLIC;

ALTER TABLE investment_import ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_import_group ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_event_draft ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_position_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_review_item ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON investment_import FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_import_group FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_event_draft FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_position_checkpoint FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_evidence FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY household_isolation ON investment_review_item FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
