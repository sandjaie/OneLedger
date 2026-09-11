-- Public/shared and household-confirmed institution spellings resolve to the canonical UUID.
-- A shared alias may point only at a shared institution; a household alias may point at a shared
-- institution or one owned by that household. The trigger expresses the NULL/shared alternative
-- that a composite foreign key cannot.
CREATE FUNCTION assert_institution_alias_scope() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE institution_household UUID;
BEGIN
  SELECT household_id INTO institution_household
    FROM institution WHERE id = NEW.institution_id FOR KEY SHARE;
  IF NOT FOUND THEN RETURN NEW; END IF; -- The ordinary foreign key reports the missing row.
  IF (NEW.household_id IS NULL AND institution_household IS NOT NULL)
     OR (NEW.household_id IS NOT NULL
         AND institution_household IS NOT NULL
         AND institution_household <> NEW.household_id) THEN
    RAISE EXCEPTION 'institution alias references institution outside its scope'
      USING ERRCODE = '23503', CONSTRAINT = 'fk_institution_alias_institution_scope';
  END IF;
  RETURN NEW;
END $$;

-- Once another row treats an institution's tenancy as identity, moving it would invalidate that
-- reference after its insertion-time check. Deactivate aliases or create a new institution instead.
CREATE FUNCTION preserve_institution_household() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.household_id IS DISTINCT FROM OLD.household_id THEN
    RAISE EXCEPTION 'institution household is immutable'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_institution_household_is_immutable
  BEFORE UPDATE OF household_id ON institution
  FOR EACH ROW EXECUTE FUNCTION preserve_institution_household();

CREATE TABLE institution_alias (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id     UUID REFERENCES household(id),
  institution_id   UUID NOT NULL REFERENCES institution(id) ON DELETE RESTRICT,
  alias            TEXT NOT NULL,
  alias_normalized TEXT NOT NULL,
  country          CHAR(2) NOT NULL,
  provenance       TEXT NOT NULL,
  is_active        BOOLEAN NOT NULL DEFAULT true,
  deactivated_at   TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT institution_alias_text_is_bounded CHECK (
    length(alias) BETWEEN 1 AND 200 AND octet_length(alias) <= 800
    AND length(alias_normalized) BETWEEN 1 AND 200
    AND octet_length(alias_normalized) <= 800),
  CONSTRAINT institution_alias_provenance_is_scoped CHECK (
    (household_id IS NULL AND provenance = 'public_seed')
    OR (household_id IS NOT NULL AND provenance = 'owner_confirmed')),
  CONSTRAINT institution_alias_lifecycle_agrees CHECK (
    (is_active AND deactivated_at IS NULL)
    OR (NOT is_active AND deactivated_at IS NOT NULL))
);

CREATE TRIGGER trg_institution_alias_scope
  BEFORE INSERT OR UPDATE OF household_id, institution_id ON institution_alias
  FOR EACH ROW EXECUTE FUNCTION assert_institution_alias_scope();

-- One institution needs only one active row for a spelling in a scope. Different institutions may
-- deliberately retain the same spelling: that is resolver ambiguity, not a migration failure.
CREATE UNIQUE INDEX idx_institution_alias_one_active_spelling
  ON institution_alias (household_id, institution_id, country, alias_normalized)
  NULLS NOT DISTINCT WHERE is_active;
CREATE INDEX idx_institution_alias_lookup
  ON institution_alias (country, alias_normalized) WHERE is_active;

ALTER TABLE institution_alias ENABLE ROW LEVEL SECURITY;
CREATE POLICY institution_alias_read ON institution_alias FOR SELECT
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY institution_alias_write ON institution_alias FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- Household-leading composite references make the service connection's tenant scope a database
-- invariant too. These targets predate that convention, so give each the required candidate key.
ALTER TABLE document
  ADD CONSTRAINT uq_document_household_id UNIQUE (household_id, id);
ALTER TABLE statement_layout
  ADD CONSTRAINT uq_statement_layout_household_id UNIQUE (household_id, id);

CREATE TABLE account_identifier (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  account_id              UUID NOT NULL,
  kind                    TEXT NOT NULL,
  masked_value            TEXT NOT NULL,
  visible_suffix          TEXT,

  full_value              TEXT,
  normalized_value        TEXT,
  legacy_fingerprint      BYTEA,
  legacy_fingerprint_scheme TEXT,

  provenance              TEXT NOT NULL,
  source_document_id      UUID,
  source_sha256           TEXT,
  source_layout_id        UUID,
  source_layout_version   SMALLINT,
  source_capture_key      TEXT,

  is_active               BOOLEAN NOT NULL DEFAULT true,
  deactivated_at          TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_account_identifier_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_account_identifier_document_household
    FOREIGN KEY (household_id, source_document_id)
    REFERENCES document(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_account_identifier_layout_household
    FOREIGN KEY (household_id, source_layout_id)
    REFERENCES statement_layout(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT account_identifier_kind_is_known CHECK (
    kind IN ('bank_account', 'iban', 'card')),
  CONSTRAINT account_identifier_mask_is_bounded CHECK (
    length(masked_value) BETWEEN 1 AND 64 AND octet_length(masked_value) <= 256
    AND (visible_suffix IS NULL OR (
      length(visible_suffix) BETWEEN 1 AND 8 AND octet_length(visible_suffix) <= 32))),
  CONSTRAINT account_identifier_complete_value_is_complete CHECK (
    num_nonnulls(full_value, normalized_value) IN (0, 2)
    AND (full_value IS NULL OR (
      length(full_value) BETWEEN 1 AND 128 AND octet_length(full_value) <= 512
      AND normalized_value IS NOT NULL
      AND length(normalized_value) BETWEEN 1 AND 128
      AND octet_length(normalized_value) <= 512))),
  CONSTRAINT account_identifier_legacy_fingerprint_is_complete CHECK (
    num_nonnulls(legacy_fingerprint, legacy_fingerprint_scheme) IN (0, 2)
    AND (legacy_fingerprint IS NULL OR (
      octet_length(legacy_fingerprint) = 32
      AND legacy_fingerprint_scheme = 'legacy_sha256_v1'
      AND provenance = 'legacy_migration'))),
  CONSTRAINT account_identifier_identity_evidence_is_distinct CHECK (
    normalized_value IS NULL OR legacy_fingerprint IS NULL),
  CONSTRAINT account_identifier_provenance_is_complete CHECK (
    (provenance = 'legacy_migration'
      AND full_value IS NULL AND normalized_value IS NULL
      AND source_document_id IS NULL AND source_sha256 IS NULL
      AND source_layout_id IS NULL AND source_layout_version IS NULL
      AND source_capture_key IS NULL)
    OR (provenance = 'owner_entered'
      AND source_document_id IS NULL AND source_sha256 IS NULL
      AND source_layout_id IS NULL AND source_layout_version IS NULL
      AND source_capture_key IS NULL)
    OR (provenance = 'statement'
      AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[[:xdigit:]]{64}$'
      AND source_layout_id IS NOT NULL
      AND source_layout_version IS NOT NULL AND source_layout_version > 0
      AND source_capture_key IS NOT NULL
      AND length(source_capture_key) BETWEEN 1 AND 80)
    OR (provenance = 'statement_backfill'
      AND source_document_id IS NOT NULL
      AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[[:xdigit:]]{64}$'
      AND source_layout_id IS NOT NULL
      AND source_layout_version IS NOT NULL AND source_layout_version > 0
      AND source_capture_key IS NOT NULL
      AND length(source_capture_key) BETWEEN 1 AND 80)),
  CONSTRAINT account_identifier_lifecycle_agrees CHECK (
    (is_active AND deactivated_at IS NULL)
    OR (NOT is_active AND deactivated_at IS NOT NULL))
);

CREATE UNIQUE INDEX idx_account_identifier_active_normalized_value
  ON account_identifier (household_id, kind, normalized_value)
  WHERE is_active AND normalized_value IS NOT NULL;
CREATE INDEX idx_account_identifier_account
  ON account_identifier (household_id, account_id, is_active);
CREATE INDEX idx_account_identifier_mask_fallback
  ON account_identifier (household_id, kind, visible_suffix) WHERE is_active;

ALTER TABLE account_identifier ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON account_identifier
  USING (household_id = current_household_id());

-- Adding a validated constraint makes an invalid pre-V28 row fail the migration atomically and
-- keeps later writers from recreating the loss-prone shape before V29 removes the legacy columns.
ALTER TABLE account
  ADD CONSTRAINT account_legacy_identity_is_migratable CHECK (
    account_number_hash IS NULL OR (
      account_number_masked IS NOT NULL
      AND account_number_hash ~ '^[[:xdigit:]]{64}$'));

INSERT INTO account_identifier (
  household_id, account_id, kind, masked_value,
  legacy_fingerprint, legacy_fingerprint_scheme, provenance
)
SELECT household_id, id,
       CASE WHEN account_type = 'credit_card' THEN 'card' ELSE 'bank_account' END,
       account_number_masked,
       CASE WHEN account_number_hash IS NULL THEN NULL ELSE decode(account_number_hash, 'hex') END,
       CASE WHEN account_number_hash IS NULL THEN NULL ELSE 'legacy_sha256_v1' END,
       'legacy_migration'
  FROM account
 WHERE account_number_masked IS NOT NULL;

INSERT INTO account_identifier (
  household_id, account_id, kind, masked_value, provenance
)
SELECT household_id, id, 'iban', iban_masked, 'legacy_migration'
  FROM account
 WHERE iban_masked IS NOT NULL;
