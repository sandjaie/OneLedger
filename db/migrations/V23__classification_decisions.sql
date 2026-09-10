-- Classification knowledge and correction safety, Task 2: storage only.
--
-- The general audit_log in schema.md is still planned, not applied. These records are narrower:
-- they preserve the exact classification preview and its immutable outcome, which Task 3 needs
-- to apply or undo a decision safely. They do not create the unrelated general audit subsystem.

-- Composite foreign keys make household agreement a database property. The leading household
-- column is deliberately redundant with each primary key; it is what prevents a valid UUID from
-- another household being persisted through a privileged service connection that bypasses RLS.
ALTER TABLE app_user
  ADD CONSTRAINT uq_app_user_household_id UNIQUE (household_id, id);
ALTER TABLE transaction
  ADD CONSTRAINT uq_transaction_household_id UNIQUE (household_id, id);

CREATE TABLE classification_knowledge (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT uq_classification_knowledge_household_id UNIQUE (household_id, id)
);

-- A knowledge identity is stable; evidence changes by appending a numbered version. The state
-- is the one mutable lifecycle fact, so revocation can stop future use without deleting evidence.
CREATE TABLE classification_knowledge_version (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL,
  knowledge_id    UUID NOT NULL,
  version         INTEGER NOT NULL,
  source_kind     TEXT NOT NULL,
  source_url      TEXT,
  attribution     TEXT NOT NULL,
  evidence_note   TEXT NOT NULL,
  matcher_version TEXT NOT NULL,
  scope           JSONB NOT NULL DEFAULT '{}'::JSONB,
  state           TEXT NOT NULL DEFAULT 'active',
  verified_at     TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_classification_knowledge_version_identity_household
    FOREIGN KEY (household_id, knowledge_id)
    REFERENCES classification_knowledge(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_classification_knowledge_version_number
    UNIQUE (household_id, knowledge_id, version),
  CONSTRAINT uq_classification_knowledge_version_household_id
    UNIQUE (household_id, id),
  CONSTRAINT classification_knowledge_version_positive CHECK (version > 0),
  CONSTRAINT classification_evidence_source_is_known CHECK (
    source_kind IN ('owner', 'public_source', 'statement', 'model_proposal')),
  CONSTRAINT classification_evidence_state_is_known CHECK (state IN ('active', 'revoked')),
  CONSTRAINT classification_evidence_public_url CHECK (
    source_url IS NULL OR source_kind = 'public_source'),
  CONSTRAINT classification_evidence_scope_is_object CHECK (jsonb_typeof(scope) = 'object'),
  CONSTRAINT classification_evidence_is_bounded CHECK (
    length(attribution) BETWEEN 1 AND 200
    AND length(evidence_note) BETWEEN 1 AND 1000
    AND length(matcher_version) BETWEEN 1 AND 100
    AND (source_url IS NULL OR length(source_url) BETWEEN 1 AND 2048)
    AND octet_length(scope::TEXT) <= 4096),
  CONSTRAINT classification_evidence_revocation_agrees CHECK (
    (state = 'active' AND revoked_at IS NULL)
    OR (state = 'revoked' AND revoked_at IS NOT NULL))
);

CREATE UNIQUE INDEX idx_classification_knowledge_one_active_version
  ON classification_knowledge_version (household_id, knowledge_id) WHERE state = 'active';

CREATE FUNCTION assert_classification_knowledge_version_immutable() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'classification_knowledge_version evidence is immutable; revoke it and append a successor';
  END IF;
  IF NEW.id              IS DISTINCT FROM OLD.id
  OR NEW.household_id    IS DISTINCT FROM OLD.household_id
  OR NEW.knowledge_id    IS DISTINCT FROM OLD.knowledge_id
  OR NEW.version         IS DISTINCT FROM OLD.version
  OR NEW.source_kind     IS DISTINCT FROM OLD.source_kind
  OR NEW.source_url      IS DISTINCT FROM OLD.source_url
  OR NEW.attribution     IS DISTINCT FROM OLD.attribution
  OR NEW.evidence_note   IS DISTINCT FROM OLD.evidence_note
  OR NEW.matcher_version IS DISTINCT FROM OLD.matcher_version
  OR NEW.scope           IS DISTINCT FROM OLD.scope
  OR NEW.verified_at     IS DISTINCT FROM OLD.verified_at
  OR NEW.created_at      IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'classification_knowledge_version evidence is immutable; revoke it and append a successor';
  END IF;
  IF NEW.state IS NOT DISTINCT FROM OLD.state
     AND NEW.revoked_at IS NOT DISTINCT FROM OLD.revoked_at THEN
    RETURN NEW;
  END IF;
  IF OLD.state = 'active' AND OLD.revoked_at IS NULL
     AND NEW.state = 'revoked' AND NEW.revoked_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'classification_knowledge_version lifecycle only allows active to revoked';
END $$;

CREATE TRIGGER trg_classification_knowledge_version_immutable
  BEFORE UPDATE OR DELETE ON classification_knowledge_version
  FOR EACH ROW EXECUTE FUNCTION assert_classification_knowledge_version_immutable();

-- The JSON snapshots contain the complete set of classification-derived fields, including
-- explicit nulls. Requiring the full allowlist prevents a partial preview from applying against
-- state it never checked, and prevents raw descriptions or money from being copied into history.
CREATE FUNCTION classification_state_is_valid(value JSONB) RETURNS BOOLEAN
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
  IF jsonb_typeof(value) <> 'object' OR NOT (value ?& allowed) THEN
    RETURN false;
  END IF;
  FOR key IN SELECT jsonb_object_keys(value) LOOP
    IF NOT (key = ANY(allowed)) THEN RETURN false; END IF;
  END LOOP;

  IF jsonb_typeof(value->'txn_type') <> 'string'
     OR value->>'txn_type' NOT IN (
       'income','expense','fee','interest','tax','refund','transfer_in','transfer_out',
       'investment_buy','investment_sell','investment_income','loan_disbursement',
       'loan_repayment','adjustment','opening_balance') THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(value->'classified_by') <> 'string'
     OR value->>'classified_by' NOT IN (
       'user_manual','user_rule','merchant_alias','recurring_series','prior_match',
       'seed_rule','unclassified','ai') THEN
    RETURN false;
  END IF;
  IF value->'payment_method' <> 'null'::JSONB
     AND (jsonb_typeof(value->'payment_method') <> 'string'
          OR value->>'payment_method' NOT IN (
            'card','contactless','atm','cash','cheque','upi','neft','imps','rtgs','wire',
            'direct_debit','standing_instruction','wallet','online','other')) THEN
    RETURN false;
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
          OR (value->>'classification_confidence')::NUMERIC > 1) THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(value->'is_reviewed') <> 'boolean'
     OR jsonb_typeof(value->'is_excluded') <> 'boolean' THEN
    RETURN false;
  END IF;
  IF value->'country' <> 'null'::JSONB
     AND (jsonb_typeof(value->'country') <> 'string' OR length(value->>'country') <> 2) THEN
    RETURN false;
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

-- Evidence scope and aggregate run results may evolve without a migration, but they never carry
-- source-row originals. The recursive check closes the same boundary for nested objects/arrays.
CREATE FUNCTION classification_payload_has_originals(value JSONB) RETURNS BOOLEAN
  LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE key TEXT; item JSONB;
BEGIN
  IF jsonb_typeof(value) = 'object' THEN
    FOR key, item IN SELECT * FROM jsonb_each(value) LOOP
      IF key = ANY(ARRAY[
        'description_raw', 'description_clean', 'reference_raw', 'counterparty_raw',
        'amount', 'original_amount', 'original_currency', 'amount_base', 'amount_secondary',
        'fx_rate_date', 'fx_rate_is_carried', 'fx_rate_to_base', 'fx_rate_to_secondary',
        'source_document_id', 'source_page_number', 'source_row_number', 'import_batch_id',
        'statement_layout_id', 'layout_version', 'dedupe_hash'
      ]) OR classification_payload_has_originals(item) THEN
        RETURN true;
      END IF;
    END LOOP;
  ELSIF jsonb_typeof(value) = 'array' THEN
    FOR item IN SELECT * FROM jsonb_array_elements(value) LOOP
      IF classification_payload_has_originals(item) THEN RETURN true; END IF;
    END LOOP;
  END IF;
  RETURN false;
END $$;

ALTER TABLE classification_knowledge_version
  ADD CONSTRAINT classification_evidence_has_no_originals
    CHECK (NOT classification_payload_has_originals(scope));

CREATE TABLE classification_run (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL,
  knowledge_version_id UUID NOT NULL,
  actor_user_id        UUID NOT NULL,
  state                TEXT NOT NULL DEFAULT 'proposed',
  reason               TEXT NOT NULL,
  result               JSONB NOT NULL DEFAULT '{}'::JSONB,
  result_reason        TEXT,
  expires_at           TIMESTAMPTZ NOT NULL,
  resolved_at          TIMESTAMPTZ,
  idempotency_key      TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_classification_run_knowledge_household
    FOREIGN KEY (household_id, knowledge_version_id)
    REFERENCES classification_knowledge_version(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_classification_run_actor_household
    FOREIGN KEY (household_id, actor_user_id)
    REFERENCES app_user(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_classification_run_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_classification_run_decision_identity
    UNIQUE (household_id, id, knowledge_version_id, actor_user_id),
  CONSTRAINT classification_run_state_is_known CHECK (
    state IN ('proposed', 'applied', 'rejected', 'conflict', 'expired')),
  CONSTRAINT classification_run_reason_is_bounded CHECK (
    length(reason) BETWEEN 1 AND 1000
    AND (result_reason IS NULL OR length(result_reason) BETWEEN 1 AND 1000)
    AND (idempotency_key IS NULL OR length(idempotency_key) BETWEEN 1 AND 200)
    AND octet_length(result::TEXT) <= 4096),
  CONSTRAINT classification_run_result_is_object CHECK (jsonb_typeof(result) = 'object'),
  CONSTRAINT classification_run_has_no_originals CHECK (
    NOT classification_payload_has_originals(result)),
  CONSTRAINT classification_run_expiry_is_later CHECK (expires_at > created_at)
);

CREATE UNIQUE INDEX idx_classification_run_idempotency
  ON classification_run (household_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_classification_run_history
  ON classification_run (household_id, created_at DESC);

CREATE TABLE classification_run_item (
  household_id                   UUID NOT NULL,
  run_id                         UUID NOT NULL,
  transaction_id                 UUID NOT NULL,
  expected_transaction_updated_at TIMESTAMPTZ NOT NULL,
  expected_source_key            TEXT NOT NULL,
  expected_state                 JSONB NOT NULL,
  proposed_state                 JSONB NOT NULL,
  reason                         TEXT NOT NULL,
  created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (run_id, transaction_id),
  CONSTRAINT fk_classification_run_item_run_household
    FOREIGN KEY (household_id, run_id)
    REFERENCES classification_run(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_classification_run_item_transaction_household
    FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_classification_run_item_household
    UNIQUE (household_id, run_id, transaction_id),
  CONSTRAINT classification_run_item_states_are_derived CHECK (
    classification_state_is_valid(expected_state)
    AND classification_state_is_valid(proposed_state)
    AND expected_state <> proposed_state),
  CONSTRAINT classification_run_item_is_bounded CHECK (
    length(expected_source_key) BETWEEN 1 AND 128
    AND length(reason) BETWEEN 1 AND 1000)
);

CREATE INDEX idx_classification_run_item_transaction
  ON classification_run_item (household_id, transaction_id, created_at DESC);

CREATE TABLE classification_decision (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL,
  run_id                  UUID NOT NULL,
  transaction_id          UUID NOT NULL,
  knowledge_version_id    UUID NOT NULL,
  actor_user_id           UUID NOT NULL,
  before_state            JSONB NOT NULL,
  after_state             JSONB NOT NULL,
  reason                  TEXT NOT NULL,
  supersedes_decision_id  UUID,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_classification_decision_run_identity
    FOREIGN KEY (household_id, run_id, knowledge_version_id, actor_user_id)
    REFERENCES classification_run(household_id, id, knowledge_version_id, actor_user_id)
    ON DELETE RESTRICT,
  CONSTRAINT fk_classification_decision_preview_item
    FOREIGN KEY (household_id, run_id, transaction_id)
    REFERENCES classification_run_item(household_id, run_id, transaction_id) ON DELETE RESTRICT,
  CONSTRAINT uq_classification_decision_household_transaction_id
    UNIQUE (household_id, transaction_id, id),
  CONSTRAINT fk_classification_decision_supersedes_same_transaction
    FOREIGN KEY (household_id, transaction_id, supersedes_decision_id)
    REFERENCES classification_decision(household_id, transaction_id, id) ON DELETE RESTRICT,
  CONSTRAINT classification_decision_states_are_derived CHECK (
    classification_state_is_valid(before_state)
    AND classification_state_is_valid(after_state)
    AND before_state <> after_state),
  CONSTRAINT classification_decision_reason_is_bounded CHECK (length(reason) BETWEEN 1 AND 1000)
);

CREATE UNIQUE INDEX idx_classification_decision_superseded_once
  ON classification_decision (supersedes_decision_id) WHERE supersedes_decision_id IS NOT NULL;
CREATE INDEX idx_classification_decision_history
  ON classification_decision (household_id, transaction_id, created_at DESC);

-- JSON is used for exact before/after state, but its UUID references are still checked here.
-- System categories and merchants are shared; household rows must be owned by the preview's
-- household. Rules have no shared rows.
CREATE FUNCTION assert_classification_state_references() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE state JSONB; reference_id UUID;
BEGIN
  FOREACH state IN ARRAY ARRAY[NEW.expected_state, NEW.proposed_state] LOOP
    reference_id := NULLIF(state->>'category_id', '')::UUID;
    IF reference_id IS NOT NULL AND NOT EXISTS (
      SELECT FROM category WHERE id = reference_id
        AND (household_id IS NULL OR household_id = NEW.household_id)
    ) THEN
      RAISE EXCEPTION 'classification state references category outside household'
        USING ERRCODE = '23503', CONSTRAINT = 'fk_classification_state_category_household';
    END IF;
    reference_id := NULLIF(state->>'merchant_id', '')::UUID;
    IF reference_id IS NOT NULL AND NOT EXISTS (
      SELECT FROM merchant WHERE id = reference_id
        AND (household_id IS NULL OR household_id = NEW.household_id)
    ) THEN
      RAISE EXCEPTION 'classification state references merchant outside household'
        USING ERRCODE = '23503', CONSTRAINT = 'fk_classification_state_merchant_household';
    END IF;
    reference_id := NULLIF(state->>'classification_rule_id', '')::UUID;
    IF reference_id IS NOT NULL AND NOT EXISTS (
      SELECT FROM classification_rule WHERE id = reference_id
        AND household_id = NEW.household_id
    ) THEN
      RAISE EXCEPTION 'classification state references rule outside household'
        USING ERRCODE = '23503', CONSTRAINT = 'fk_classification_state_rule_household';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_classification_run_item_references
  BEFORE INSERT ON classification_run_item
  FOR EACH ROW EXECUTE FUNCTION assert_classification_state_references();

CREATE FUNCTION assert_classification_decision_matches_preview() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE expected JSONB; proposed JSONB; prior_after JSONB;
BEGIN
  SELECT expected_state, proposed_state INTO expected, proposed
    FROM classification_run_item
   WHERE household_id = NEW.household_id
     AND run_id = NEW.run_id
     AND transaction_id = NEW.transaction_id;
  IF FOUND AND (NEW.before_state IS DISTINCT FROM expected OR NEW.after_state IS DISTINCT FROM proposed) THEN
    RAISE EXCEPTION 'classification decision must exactly match its persisted preview item';
  END IF;
  IF NEW.supersedes_decision_id IS NOT NULL THEN
    SELECT after_state INTO prior_after
      FROM classification_decision
     WHERE household_id = NEW.household_id
       AND transaction_id = NEW.transaction_id
       AND id = NEW.supersedes_decision_id;
    IF FOUND AND NEW.before_state IS DISTINCT FROM prior_after THEN
      RAISE EXCEPTION 'a correction must begin from the state left by the decision it supersedes';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_classification_decision_matches_preview
  BEFORE INSERT ON classification_decision
  FOR EACH ROW EXECUTE FUNCTION assert_classification_decision_matches_preview();

CREATE FUNCTION assert_classification_record_immutable() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is immutable; append a new classification record', TG_TABLE_NAME;
END $$;

CREATE TRIGGER trg_classification_run_item_immutable
  BEFORE UPDATE OR DELETE ON classification_run_item
  FOR EACH ROW EXECUTE FUNCTION assert_classification_record_immutable();
CREATE TRIGGER trg_classification_decision_immutable
  BEFORE UPDATE OR DELETE ON classification_decision
  FOR EACH ROW EXECUTE FUNCTION assert_classification_record_immutable();

ALTER TABLE classification_knowledge         ENABLE ROW LEVEL SECURITY;
ALTER TABLE classification_knowledge_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE classification_run               ENABLE ROW LEVEL SECURITY;
ALTER TABLE classification_run_item          ENABLE ROW LEVEL SECURITY;
ALTER TABLE classification_decision          ENABLE ROW LEVEL SECURITY;

CREATE POLICY classification_knowledge_isolation ON classification_knowledge FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY classification_knowledge_version_isolation ON classification_knowledge_version FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY classification_run_isolation ON classification_run FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY classification_run_item_isolation ON classification_run_item FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY classification_decision_isolation ON classification_decision FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

COMMENT ON TABLE classification_knowledge_version IS
  'Versioned, bounded evidence for one household classification fact; no copied source rows.';
COMMENT ON TABLE classification_run_item IS
  'One immutable exact transaction preview: source guard plus complete expected/proposed derived state.';
COMMENT ON TABLE classification_decision IS
  'Append-only classification outcome. Corrections supersede a prior event; they never rewrite it.';
