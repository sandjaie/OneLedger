-- Phase 1.6: source-contract-aware replay and indexed cross-document overlap evidence.

DROP INDEX idx_investment_evidence_document_fingerprint;
CREATE UNIQUE INDEX idx_investment_evidence_document_fingerprint
  ON investment_evidence (
    household_id, document_id, source_contract, fingerprint_version, evidence_fingerprint);

CREATE INDEX idx_investment_evidence_source_identity_overlap
  ON investment_evidence (
    household_id, source_contract, source_event_id, created_at, id)
  WHERE source_event_id IS NOT NULL;
CREATE INDEX idx_investment_evidence_fingerprint_overlap
  ON investment_evidence (
    household_id, source_contract, fingerprint_version, evidence_fingerprint, created_at, id);

CREATE OR REPLACE FUNCTION record_investment_evidence(
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
     AND source_contract = p_source_contract
     AND CASE
       WHEN p_source_event_id IS NOT NULL THEN source_event_id = p_source_event_id
       ELSE fingerprint_version = p_fingerprint_version
         AND evidence_fingerprint = p_evidence_fingerprint
     END
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
