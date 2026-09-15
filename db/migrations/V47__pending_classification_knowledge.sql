-- V47: a knowledge version may sit pending until its first successful classification-run apply
-- activates it. Nothing previewed changes future classification before that: a pending version is
-- never returned by GET /merchant-knowledge and never read by load_knowledge's `state = 'active'`
-- filter, so it cascades into no classifier until apply says so.

ALTER TABLE classification_knowledge_version
  DROP CONSTRAINT classification_evidence_state_is_known,
  ADD CONSTRAINT classification_evidence_state_is_known CHECK (
    state IN ('active', 'pending', 'revoked'));

ALTER TABLE classification_knowledge_version
  DROP CONSTRAINT classification_evidence_revocation_agrees,
  ADD CONSTRAINT classification_evidence_revocation_agrees CHECK (
    (state IN ('active', 'pending') AND revoked_at IS NULL)
    OR (state = 'revoked' AND revoked_at IS NOT NULL));

-- Lifecycle now allows exactly three edges beyond the identity-immutability already enforced
-- above: active -> revoked (unchanged), pending -> active (an apply activates it; revoked_at
-- stays NULL) and pending -> revoked (owner or one-row-answer cleanup revokes it unused).
CREATE OR REPLACE FUNCTION assert_classification_knowledge_version_immutable() RETURNS TRIGGER
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
  IF OLD.state = 'pending' AND OLD.revoked_at IS NULL
     AND NEW.state = 'active' AND NEW.revoked_at IS NULL THEN
    RETURN NEW;
  END IF;
  IF OLD.state = 'pending' AND OLD.revoked_at IS NULL
     AND NEW.state = 'revoked' AND NEW.revoked_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'classification_knowledge_version lifecycle only allows active to revoked, pending to active or pending to revoked';
END $$;
