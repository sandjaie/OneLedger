-- V47: pending knowledge coexists with an active version under the same identity (only one
-- active version is unique), and the state column still refuses anything outside the three
-- known lifecycle states.
\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION pg_temp.t0044_fixture()
  RETURNS TABLE (h1 UUID, u1 UUID) LANGUAGE plpgsql AS $$
DECLARE p1 UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('pending knowledge household', base, secondary) RETURNING id INTO h1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'owner', true) RETURNING id INTO p1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'pending-knowledge@example.invalid', 'owner')
  RETURNING id INTO u1;
  RETURN NEXT;
END $$;

BEGIN;
DO $$
DECLARE f RECORD; knowledge UUID; active_version UUID; pending_version UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0044_fixture();
  INSERT INTO classification_knowledge (household_id) VALUES (f.h1) RETURNING id INTO knowledge;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state, verified_at
  ) VALUES (
    f.h1, knowledge, 1, 'owner', 'owner', 'active synthetic evidence', 'matcher-v1', '{}',
    'active', now()
  ) RETURNING id INTO active_version;

  -- A second, pending version appended under the SAME knowledge identity as the active one
  -- above must not be blocked by the one-active-version index, which is scoped to
  -- state = 'active' and therefore has nothing to say about a coexisting 'pending' row.
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (
    f.h1, knowledge, 2, 'owner', 'owner', 'pending synthetic guess', 'matcher-v1', '{}', 'pending'
  ) RETURNING id INTO pending_version;
  ASSERT (SELECT count(*) FROM classification_knowledge_version WHERE knowledge_id = knowledge)
           = 2,
    'an active and a pending version coexist under one knowledge identity';

  BEGIN
    INSERT INTO classification_knowledge_version (
      household_id, knowledge_id, version, source_kind, attribution, evidence_note,
      matcher_version, scope, state
    ) VALUES (
      f.h1, knowledge, 3, 'owner', 'owner', 'unknown state', 'matcher-v1', '{}', 'proposed'
    );
    RAISE EXCEPTION 'an unknown evidence state must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification_evidence_state_is_known%'
        OR refusal LIKE '%classification_evidence_revocation_agrees%',
      'expected a known-state or revocation-agreement refusal, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0044 a pending version coexists with active and unknown states are refused';
END $$;
ROLLBACK;
