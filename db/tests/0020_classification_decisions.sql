-- V23: durable classification evidence, bounded preview scope and immutable decisions.
-- Every figure and label is synthetic. The suite exercises storage only; Task 3 owns the
-- authenticated API operations that create previews, apply them and conditionally undo them.
\set ON_ERROR_STOP on

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'classification_knowledge',
    'classification_knowledge_version',
    'classification_run',
    'classification_run_item',
    'classification_decision'
  ]
  LOOP
    ASSERT to_regclass('public.' || table_name) IS NOT NULL,
      table_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = table_name),
      'RLS must be enabled on ' || table_name;
  END LOOP;
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'classification_decision'::regclass
       AND tgname = 'trg_classification_decision_immutable'
       AND NOT tgisinternal
  ), 'classification decisions must have an immutability trigger';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'classification_run_item'::regclass
       AND tgname = 'trg_classification_run_item_immutable'
       AND NOT tgisinternal
  ), 'a persisted preview item must not change beneath its run';
  RAISE NOTICE 'PASS 0020 structure and RLS';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0020_state(
  p_type txn_type,
  p_category UUID DEFAULT NULL,
  p_merchant UUID DEFAULT NULL,
  p_payment payment_method DEFAULT NULL,
  p_classified classified_by DEFAULT 'unclassified',
  p_rule UUID DEFAULT NULL,
  p_confidence NUMERIC DEFAULT NULL,
  p_reviewed BOOLEAN DEFAULT false,
  p_country TEXT DEFAULT NULL,
  p_tags TEXT[] DEFAULT NULL,
  p_excluded BOOLEAN DEFAULT false
) RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_build_object(
    'txn_type', p_type,
    'category_id', p_category,
    'merchant_id', p_merchant,
    'payment_method', p_payment,
    'classified_by', p_classified,
    'classification_rule_id', p_rule,
    'classification_confidence', p_confidence,
    'is_reviewed', p_reviewed,
    'country', p_country,
    'tags', p_tags,
    'is_excluded', p_excluded
  )
$$;

CREATE OR REPLACE FUNCTION pg_temp.t0020_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, t1 UUID, t2 UUID,
    category1 UUID, category2 UUID, base CHAR(3)
  ) LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p2 UUID; a1 UUID; a2 UUID; secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('decision household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('decision household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'decision-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'decision-two@example.invalid', 'owner')
  RETURNING id INTO u2;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Decision account one', 'savings', base, 'XB', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h2, p2, 'Decision account two', 'savings', base, 'XB', 0, '2026-01-01')
  RETURNING id INTO a2;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h1, 'Synthetic shopping', 'decision-shopping-one', 'expense', 'want')
  RETURNING id INTO category1;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h2, 'Synthetic shopping', 'decision-shopping-two', 'expense', 'want')
  RETURNING id INTO category2;

  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, source, classified_by, dedupe_hash
  ) VALUES (
    h1, a1, '2026-04-01', 'SYNTHETIC DECISION ONE', -37.2500, base,
    '2026-04-01', 1, 1, -37.2500, -37.2500,
    'loan_repayment', 'manual', 'seed_rule', repeat('d', 64)
  ) RETURNING id INTO t1;
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary,
    txn_type, source, classified_by, dedupe_hash
  ) VALUES (
    h2, a2, '2026-04-01', 'SYNTHETIC DECISION TWO', -12.5000, base,
    '2026-04-01', 1, 1, -12.5000, -12.5000,
    'expense', 'manual', 'unclassified', repeat('e', 64)
  ) RETURNING id INTO t2;
  RETURN NEXT;
END $$;

-- Constrained evidence and run states, and bounded payloads, make malformed storage fail at
-- the database boundary instead of becoming an unreadable future API record.
BEGIN;
DO $$
DECLARE f RECORD; knowledge UUID; version UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0020_fixture();
  INSERT INTO classification_knowledge (household_id)
  VALUES (f.h1) RETURNING id INTO knowledge;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, source_url, attribution,
    evidence_note, matcher_version, scope, state, verified_at
  ) VALUES (
    f.h1, knowledge, 1, 'public_source', 'https://example.invalid/provider',
    'Synthetic public directory', 'Provider identity only; no copied page.', 'matcher-v1',
    '{"direction":"debit","service":"purchase"}', 'active', now()
  ) RETURNING id INTO version;

  BEGIN
    INSERT INTO classification_run (
      household_id, knowledge_version_id, actor_user_id, state, reason, expires_at
    ) VALUES (f.h1, version, f.u1, 'running', 'synthetic invalid state', now() + interval '1 hour');
    RAISE EXCEPTION 'an unknown run state must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification_run_state_is_known%',
      'expected classification_run_state_is_known, got ' || refusal;
  END;

  BEGIN
    INSERT INTO classification_knowledge_version (
      household_id, knowledge_id, version, source_kind, attribution, evidence_note,
      matcher_version, scope, state
    ) VALUES (
      f.h1, knowledge, 2, 'public_source', repeat('a', 201), 'bounded',
      'matcher-v1', '{}', 'revoked'
    );
    RAISE EXCEPTION 'overlong attribution must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification_evidence_is_bounded%',
      'expected classification_evidence_is_bounded, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0020 evidence and run values are constrained';
END $$;
ROLLBACK;

-- Composite foreign keys carry the household alongside every reference. A valid UUID from a
-- different household is not merely hidden by RLS; it is impossible to persist.
BEGIN;
DO $$
DECLARE f RECORD; k1 UUID; k2 UUID; v1 UUID; v2 UUID; r1 UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0020_fixture();
  INSERT INTO classification_knowledge (household_id) VALUES (f.h1) RETURNING id INTO k1;
  INSERT INTO classification_knowledge (household_id) VALUES (f.h2) RETURNING id INTO k2;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h1, k1, 1, 'owner', 'owner one', 'synthetic correction', 'matcher-v1', '{}', 'active')
  RETURNING id INTO v1;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h2, k2, 1, 'owner', 'owner two', 'synthetic correction', 'matcher-v1', '{}', 'active')
  RETURNING id INTO v2;

  BEGIN
    INSERT INTO classification_run (
      household_id, knowledge_version_id, actor_user_id, state, reason, expires_at
    ) VALUES (f.h1, v2, f.u1, 'proposed', 'cross-household evidence', now() + interval '1 hour');
    RAISE EXCEPTION 'another household''s knowledge version must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_classification_run_knowledge_household%',
      'expected the knowledge household foreign key, got ' || refusal;
  END;

  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at
  ) VALUES (f.h1, v1, f.u1, 'proposed', 'own preview', now() + interval '1 hour')
  RETURNING id INTO r1;
  BEGIN
    INSERT INTO classification_run_item (
      household_id, run_id, transaction_id, expected_transaction_updated_at,
      expected_source_key, expected_state, proposed_state, reason
    ) SELECT f.h1, r1, f.t2, t.updated_at, t.dedupe_hash,
             pg_temp.t0020_state('expense'),
             pg_temp.t0020_state('expense', f.category1, p_classified => 'user_manual'),
             'cross-household transaction'
        FROM transaction t WHERE t.id = f.t2;
    RAISE EXCEPTION 'another household''s transaction must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_classification_run_item_transaction_household%',
      'expected the transaction household foreign key, got ' || refusal;
  END;
  RAISE NOTICE 'PASS 0020 every knowledge and transaction reference stays in its household';
END $$;
ROLLBACK;

-- Preview states are a complete allowlisted snapshot of derived fields. Original financial
-- fields remain only on transaction: smuggling amount into either JSON payload is refused.
BEGIN;
DO $$
DECLARE f RECORD; knowledge UUID; version UUID; run UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0020_fixture();
  INSERT INTO classification_knowledge (household_id) VALUES (f.h1) RETURNING id INTO knowledge;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h1, knowledge, 1, 'owner', 'owner one', 'synthetic correction',
            'matcher-v1', '{}', 'active') RETURNING id INTO version;
  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at
  ) VALUES (f.h1, version, f.u1, 'proposed', 'bounded preview', now() + interval '1 hour')
  RETURNING id INTO run;

  BEGIN
    INSERT INTO classification_run_item (
      household_id, run_id, transaction_id, expected_transaction_updated_at,
      expected_source_key, expected_state, proposed_state, reason
    ) SELECT f.h1, run, f.t1, t.updated_at, t.dedupe_hash,
             pg_temp.t0020_state('loan_repayment') || '{"amount":-37.25}',
             pg_temp.t0020_state('expense', f.category1, p_classified => 'user_manual'),
             'original fields do not belong in preview state'
        FROM transaction t WHERE t.id = f.t1;
    RAISE EXCEPTION 'an original financial field in state must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification_run_item_states_are_derived%',
      'expected classification_run_item_states_are_derived, got ' || refusal;
  END;
  ASSERT (SELECT amount FROM transaction WHERE id = f.t1) = -37.2500,
    'the financial original remains unchanged in transaction';
  RAISE NOTICE 'PASS 0020 preview state cannot copy transaction originals';
END $$;
ROLLBACK;

-- The apply idempotency key belongs to a household. A second run cannot claim the same key,
-- while another household may independently use the same opaque value.
BEGIN;
DO $$
DECLARE f RECORD; k1 UUID; k2 UUID; v1 UUID; v2 UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0020_fixture();
  INSERT INTO classification_knowledge (household_id) VALUES (f.h1) RETURNING id INTO k1;
  INSERT INTO classification_knowledge (household_id) VALUES (f.h2) RETURNING id INTO k2;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h1, k1, 1, 'owner', 'owner one', 'synthetic', 'matcher-v1', '{}', 'active')
  RETURNING id INTO v1;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h2, k2, 1, 'owner', 'owner two', 'synthetic', 'matcher-v1', '{}', 'active')
  RETURNING id INTO v2;

  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at, idempotency_key
  ) VALUES (f.h1, v1, f.u1, 'applied', 'first result', now() + interval '1 hour', 'apply-key-1');
  BEGIN
    INSERT INTO classification_run (
      household_id, knowledge_version_id, actor_user_id, state, reason, expires_at, idempotency_key
    ) VALUES (f.h1, v1, f.u1, 'applied', 'duplicate result', now() + interval '1 hour', 'apply-key-1');
    RAISE EXCEPTION 'a duplicate household idempotency key must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%idx_classification_run_idempotency%',
      'expected idx_classification_run_idempotency, got ' || refusal;
  END;
  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at, idempotency_key
  ) VALUES (f.h2, v2, f.u2, 'applied', 'independent result', now() + interval '1 hour', 'apply-key-1');
  RAISE NOTICE 'PASS 0020 apply idempotency is durable and household-scoped';
END $$;
ROLLBACK;

-- Synthetic preview then correction: each run stores its exact expected and proposed state;
-- the correction appends an event that supersedes the prior one without altering it.
BEGIN;
DO $$
DECLARE
  f RECORD; knowledge UUID; version UUID; run1 UUID; run2 UUID; decision1 UUID; decision2 UUID;
  original_state JSONB; first_state JSONB; corrected_state JSONB; original_created TIMESTAMPTZ;
  refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0020_fixture();
  original_state := pg_temp.t0020_state('loan_repayment', p_classified => 'seed_rule');
  first_state := pg_temp.t0020_state('expense', f.category1, p_classified => 'user_manual',
                                     p_confidence => 1, p_reviewed => true);
  corrected_state := pg_temp.t0020_state('refund', f.category1, p_classified => 'user_manual',
                                         p_confidence => 1, p_reviewed => true);

  INSERT INTO classification_knowledge (household_id) VALUES (f.h1) RETURNING id INTO knowledge;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state, verified_at
  ) VALUES (f.h1, knowledge, 1, 'owner', 'owner one', 'synthetic owner explanation',
            'matcher-v1', '{"direction":"debit"}', 'active', now()) RETURNING id INTO version;

  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at, idempotency_key
  ) VALUES (f.h1, version, f.u1, 'applied', 'repair synthetic exclusion',
            now() + interval '1 hour', 'apply-first') RETURNING id INTO run1;
  INSERT INTO classification_run_item (
    household_id, run_id, transaction_id, expected_transaction_updated_at,
    expected_source_key, expected_state, proposed_state, reason
  ) SELECT f.h1, run1, f.t1, t.updated_at, t.dedupe_hash, original_state, first_state,
           'supported synthetic evidence'
      FROM transaction t WHERE t.id = f.t1;
  INSERT INTO classification_decision (
    household_id, run_id, transaction_id, knowledge_version_id, actor_user_id,
    before_state, after_state, reason
  ) VALUES (
    f.h1, run1, f.t1, version, f.u1, original_state, first_state, 'first synthetic decision'
  ) RETURNING id, created_at INTO decision1, original_created;

  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at, idempotency_key
  ) VALUES (f.h1, version, f.u1, 'applied', 'correct synthetic role',
            now() + interval '1 hour', 'apply-correction') RETURNING id INTO run2;
  INSERT INTO classification_run_item (
    household_id, run_id, transaction_id, expected_transaction_updated_at,
    expected_source_key, expected_state, proposed_state, reason
  ) SELECT f.h1, run2, f.t1, t.updated_at, t.dedupe_hash, first_state, corrected_state,
           'later synthetic correction'
      FROM transaction t WHERE t.id = f.t1;
  INSERT INTO classification_decision (
    household_id, run_id, transaction_id, knowledge_version_id, actor_user_id,
    before_state, after_state, reason, supersedes_decision_id
  ) VALUES (
    f.h1, run2, f.t1, version, f.u1, first_state, corrected_state,
    'corrected synthetic decision', decision1
  ) RETURNING id INTO decision2;

  ASSERT decision2 <> decision1, 'a correction must append a distinct decision';
  ASSERT (SELECT count(*) FROM classification_decision WHERE transaction_id = f.t1) = 2,
    'both the original decision and correction must remain';
  ASSERT (SELECT before_state FROM classification_decision WHERE id = decision1) = original_state,
    'the original decision must retain its before state';
  ASSERT (SELECT after_state FROM classification_decision WHERE id = decision1) = first_state,
    'the original decision must retain its after state';
  ASSERT (SELECT supersedes_decision_id FROM classification_decision WHERE id = decision2) = decision1,
    'the correction must name the decision it supersedes';

  BEGIN
    UPDATE classification_decision SET reason = 'rewritten' WHERE id = decision1;
    RAISE EXCEPTION 'a decision update must be refused';
  EXCEPTION WHEN raise_exception THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification_decision is immutable%',
      'expected immutable update refusal, got ' || refusal;
  END;
  BEGIN
    DELETE FROM classification_decision WHERE id = decision1;
    RAISE EXCEPTION 'a decision deletion must be refused';
  EXCEPTION WHEN raise_exception THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%classification_decision is immutable%',
      'expected immutable delete refusal, got ' || refusal;
  END;
  ASSERT (SELECT created_at FROM classification_decision WHERE id = decision1) = original_created,
    'the refused mutations must leave the first event untouched';
  RAISE NOTICE 'PASS 0020 correction appends immutable evidence without losing prior state';
END $$;
ROLLBACK;

-- Authenticated reads see only their household's history. Fixtures exist on both sides before
-- the role switch, so an empty/permissive-policy false positive is impossible.
BEGIN;
DO $$
DECLARE f RECORD; k1 UUID; k2 UUID; v1 UUID; v2 UUID; r1 UUID; r2 UUID;
  state1 JSONB; state2 JSONB;
BEGIN
  SELECT * INTO f FROM pg_temp.t0020_fixture();
  state1 := pg_temp.t0020_state('expense');
  state2 := pg_temp.t0020_state('income');
  INSERT INTO classification_knowledge (household_id) VALUES (f.h1) RETURNING id INTO k1;
  INSERT INTO classification_knowledge (household_id) VALUES (f.h2) RETURNING id INTO k2;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h1, k1, 1, 'owner', 'one', 'synthetic', 'matcher-v1', '{}', 'active')
  RETURNING id INTO v1;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope, state
  ) VALUES (f.h2, k2, 1, 'owner', 'two', 'synthetic', 'matcher-v1', '{}', 'active')
  RETURNING id INTO v2;
  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at
  ) VALUES (f.h1, v1, f.u1, 'applied', 'one', now() + interval '1 hour') RETURNING id INTO r1;
  INSERT INTO classification_run (
    household_id, knowledge_version_id, actor_user_id, state, reason, expires_at
  ) VALUES (f.h2, v2, f.u2, 'applied', 'two', now() + interval '1 hour') RETURNING id INTO r2;
  INSERT INTO classification_run_item (
    household_id, run_id, transaction_id, expected_transaction_updated_at,
    expected_source_key, expected_state, proposed_state, reason
  ) SELECT f.h1, r1, f.t1, t.updated_at, t.dedupe_hash, state1, state2, 'one'
      FROM transaction t WHERE t.id = f.t1;
  INSERT INTO classification_run_item (
    household_id, run_id, transaction_id, expected_transaction_updated_at,
    expected_source_key, expected_state, proposed_state, reason
  ) SELECT f.h2, r2, f.t2, t.updated_at, t.dedupe_hash, state1, state2, 'two'
      FROM transaction t WHERE t.id = f.t2;
  INSERT INTO classification_decision (
    household_id, run_id, transaction_id, knowledge_version_id, actor_user_id,
    before_state, after_state, reason
  ) VALUES (f.h1, r1, f.t1, v1, f.u1, state1, state2, 'mine'),
           (f.h2, r2, f.t2, v2, f.u2, state1, state2, 'theirs');

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM classification_decision) = 1,
    'an owner reads exactly their own decision history';
  ASSERT (SELECT reason FROM classification_decision) = 'mine',
    'another household''s decision history must be invisible';
  ASSERT (SELECT count(*) FROM classification_run_item) = 1,
    'the exact preview scope is household-isolated too';
  RESET ROLE;
  RAISE NOTICE 'PASS 0020 an owner reads own history and not another household';
END $$;
ROLLBACK;
