-- V24: household counterparty identity and evidenced, role-specific aliases.
-- All fixtures are synthetic. Ambiguous aliases are deliberately retained so the Task 5
-- resolver can abstain instead of choosing an entity by insertion or UUID order.
\set ON_ERROR_STOP on

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['counterparty', 'counterparty_alias'] LOOP
    ASSERT to_regclass('public.' || table_name) IS NOT NULL,
      table_name || ' must exist';
    ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = table_name),
      'RLS must be enabled on ' || table_name;
  END LOOP;
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'counterparty'::regclass
       AND tgname = 'trg_counterparty_history_is_preserved'
       AND NOT tgisinternal
  ), 'counterparty history must be protected';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'counterparty_alias'::regclass
       AND tgname = 'trg_counterparty_alias_history_is_preserved'
       AND NOT tgisinternal
  ), 'counterparty alias history must be protected';
  ASSERT EXISTS (
    SELECT FROM pg_trigger
     WHERE tgrelid = 'merchant'::regclass
       AND tgname = 'trg_merchant_household_is_immutable'
       AND NOT tgisinternal
  ), 'merchant tenancy must remain stable for counterparty references';
  RAISE NOTICE 'PASS 0021 structure and RLS';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0021_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, u2 UUID, a1 UUID, a2 UUID,
    m1 UUID, m2 UUID, v1 UUID, v2 UUID
  ) LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p2 UUID; k1 UUID; k2 UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('counterparty household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('counterparty household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'owner one', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'owner two', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'counterparty-one@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'counterparty-two@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h1, p1, 'Synthetic account one', 'savings', base, 'XB', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (h2, p2, 'Synthetic account two', 'savings', base, 'XB', 0, '2026-01-01')
  RETURNING id INTO a2;
  INSERT INTO merchant (household_id, canonical_name)
  VALUES (h1, 'Synthetic provider one') RETURNING id INTO m1;
  INSERT INTO merchant (household_id, canonical_name)
  VALUES (h2, 'Synthetic provider two') RETURNING id INTO m2;
  INSERT INTO classification_knowledge (household_id)
  VALUES (h1) RETURNING id INTO k1;
  INSERT INTO classification_knowledge (household_id)
  VALUES (h2) RETURNING id INTO k2;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope
  ) VALUES (
    h1, k1, 1, 'owner', 'Synthetic owner answer', 'Role confirmed for a test fixture.',
    'matcher-v1', '{"direction":"debit"}'
  ) RETURNING id INTO v1;
  INSERT INTO classification_knowledge_version (
    household_id, knowledge_id, version, source_kind, attribution, evidence_note,
    matcher_version, scope
  ) VALUES (
    h2, k2, 1, 'owner', 'Synthetic owner answer', 'Role confirmed for a test fixture.',
    'matcher-v1', '{"direction":"debit"}'
  ) RETURNING id INTO v2;
  RETURN NEXT;
END $$;

-- A provider reuses one merchant identity while its purchase and wallet services remain distinct.
-- Entity kinds and their optional references must agree at the database boundary.
BEGIN;
DO $$
DECLARE f RECORD; purchase UUID; wallet UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0021_fixture();
  INSERT INTO counterparty (household_id, kind, display_name, merchant_id)
  VALUES (f.h1, 'merchant', 'Synthetic purchase service', f.m1)
  RETURNING id INTO purchase;
  INSERT INTO counterparty (household_id, kind, display_name, merchant_id)
  VALUES (f.h1, 'intermediary', 'Synthetic wallet service', f.m1)
  RETURNING id INTO wallet;
  INSERT INTO counterparty_alias (
    household_id, counterparty_id, knowledge_version_id, pattern, pattern_kind, role
  ) VALUES
    (f.h1, purchase, f.v1, 'SYNTHETIC PROVIDER PURCHASE', 'contains', 'purchase'),
    (f.h1, wallet, f.v1, 'SYNTHETIC PROVIDER WALLET', 'contains', 'wallet');

  ASSERT (SELECT count(DISTINCT merchant_id) FROM counterparty
           WHERE id IN (purchase, wallet)) = 1,
    'services reuse the existing merchant identity';
  ASSERT (SELECT count(DISTINCT kind) FROM counterparty
           WHERE id IN (purchase, wallet)) = 2,
    'purchase and intermediary services retain different economic identities';

  BEGIN
    INSERT INTO counterparty_alias (
      household_id, counterparty_id, knowledge_version_id, pattern, role
    ) VALUES (f.h1, purchase, f.v1, 'PURCHASE MISLABELLED AS WALLET', 'wallet');
    RAISE EXCEPTION 'a role incompatible with its entity kind must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty alias role disagrees with entity kind%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name)
    VALUES (f.h1, 'merchant', 'Merchant without catalogue identity');
    RAISE EXCEPTION 'merchant kind without a merchant reference must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_kind_reference_agrees%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name, merchant_id)
    VALUES (f.h1, 'person', 'Person with business identity', f.m1);
    RAISE EXCEPTION 'a person must not be represented by a merchant row';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_kind_reference_agrees%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name)
    VALUES (f.h1, 'own_account', 'Own account without reference');
    RAISE EXCEPTION 'own-account kind without an account reference must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_kind_reference_agrees%', refusal;
  END;
  RAISE NOTICE 'PASS 0021 provider services and kind/reference agreement';
END $$;
ROLLBACK;

-- A stable alias may retain a masked last four, but full private identifiers are refused even
-- when formatting or a different Unicode decimal script is used to evade an ASCII-only check.
BEGIN;
DO $$
DECLARE f RECORD; entity UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0021_fixture();
  INSERT INTO counterparty (household_id, kind, display_name)
  VALUES (f.h1, 'person', 'Synthetic person ending 1234') RETURNING id INTO entity;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name)
    VALUES (f.h1, 'person', 'Private 1234_5678 identity');
    RAISE EXCEPTION 'a formatted private identifier must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_display_name_excludes_private_identifiers%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name)
    VALUES (f.h1, 'person', 'Private ABCDE1234F identity');
    RAISE EXCEPTION 'an alphanumeric tax identifier must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_display_name_excludes_private_identifiers%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty_alias (
      household_id, counterparty_id, knowledge_version_id, pattern, role
    ) VALUES (f.h1, entity, f.v1, 'PRIVATE ১২৩৪​৫৬৭৮', 'transfer');
    RAISE EXCEPTION 'a Unicode private identifier must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_alias_excludes_private_identifiers%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty_alias (
      household_id, counterparty_id, knowledge_version_id, pattern, role
    ) VALUES (f.h1, entity, f.v1, 'PRIVATE ABCDE1234F', 'transfer');
    RAISE EXCEPTION 'an alphanumeric tax identifier alias must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty_alias_excludes_private_identifiers%', refusal;
  END;

  INSERT INTO counterparty_alias (
    household_id, counterparty_id, knowledge_version_id, pattern, role
  ) VALUES (f.h1, entity, f.v1, 'MASKED ENDING 1234', 'transfer');
  RAISE NOTICE 'PASS 0021 formatted and Unicode private identifiers are not stored';
END $$;
ROLLBACK;

-- Privileged connections cannot create cross-household references even though they bypass RLS.
BEGIN;
DO $$
DECLARE f RECORD; entity UUID; merchant_entity UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0021_fixture();
  INSERT INTO counterparty (household_id, kind, display_name, own_account_id)
  VALUES (f.h1, 'own_account', 'My synthetic account', f.a1)
  RETURNING id INTO entity;
  INSERT INTO counterparty (household_id, kind, display_name, merchant_id)
  VALUES (f.h1, 'merchant', 'Synthetic merchant service', f.m1)
  RETURNING id INTO merchant_entity;

  BEGIN
    UPDATE merchant SET household_id = f.h2 WHERE id = f.m1;
    RAISE EXCEPTION 'a referenced merchant must not be re-homed';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%merchant household is immutable%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name, own_account_id)
    VALUES (f.h1, 'own_account', 'Another household account', f.a2);
    RAISE EXCEPTION 'a cross-household account reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_counterparty_own_account_household%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty (household_id, kind, display_name, merchant_id)
    VALUES (f.h1, 'merchant', 'Another household merchant', f.m2);
    RAISE EXCEPTION 'a cross-household merchant reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty references merchant outside household%', refusal;
  END;

  BEGIN
    INSERT INTO counterparty_alias (
      household_id, counterparty_id, knowledge_version_id, pattern, role
    ) VALUES (f.h1, entity, f.v2, 'CROSS HOUSEHOLD EVIDENCE', 'own_account');
    RAISE EXCEPTION 'cross-household evidence must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_counterparty_alias_knowledge_household%', refusal;
  END;
  RAISE NOTICE 'PASS 0021 household-leading references';
END $$;
ROLLBACK;

-- The same statement pattern may mean different things. Store the ambiguity; Task 5 resolves or
-- abstains. The same private alias may also exist independently in another household.
BEGIN;
DO $$
DECLARE f RECORD; c1 UUID; c2 UUID; c3 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0021_fixture();
  INSERT INTO counterparty (household_id, kind, display_name)
  VALUES (f.h1, 'person', 'Synthetic person') RETURNING id INTO c1;
  INSERT INTO counterparty (household_id, kind, display_name)
  VALUES (f.h1, 'unresolved', 'Synthetic unresolved entity') RETURNING id INTO c2;
  INSERT INTO counterparty (household_id, kind, display_name)
  VALUES (f.h2, 'person', 'Synthetic person') RETURNING id INTO c3;

  INSERT INTO counterparty_alias (
    household_id, counterparty_id, knowledge_version_id, pattern, pattern_kind, role, priority
  ) VALUES
    (f.h1, c1, f.v1, 'SHARED SYNTHETIC ALIAS', 'exact', 'transfer', 100),
    (f.h1, c2, f.v1, 'SHARED SYNTHETIC ALIAS', 'exact', 'transfer', 100),
    (f.h2, c3, f.v2, 'SHARED SYNTHETIC ALIAS', 'exact', 'transfer', 100);

  ASSERT (SELECT count(*) FROM counterparty_alias
           WHERE household_id = f.h1 AND pattern = 'SHARED SYNTHETIC ALIAS') = 2,
    'same-household ambiguity must remain explicit';
  ASSERT (SELECT count(*) FROM counterparty_alias
           WHERE pattern = 'SHARED SYNTHETIC ALIAS') = 3,
    'households keep independent private aliases';

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM counterparty) = 2,
    'the owner sees only this household counterparty identities';
  ASSERT (SELECT count(*) FROM counterparty_alias) = 2,
    'the owner sees only this household aliases';
  RESET ROLE;
  RAISE NOTICE 'PASS 0021 ambiguity and household-isolated aliases';
END $$;
ROLLBACK;

-- Deactivation is the only lifecycle transition. Identity and evidence references remain present
-- for explanations and future classification decisions; neither table can be deleted or rewritten.
BEGIN;
DO $$
DECLARE f RECORD; entity UUID; alias UUID; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0021_fixture();
  INSERT INTO counterparty (household_id, kind, display_name)
  VALUES (f.h1, 'person', 'Historical synthetic person') RETURNING id INTO entity;
  INSERT INTO counterparty_alias (
    household_id, counterparty_id, knowledge_version_id, pattern, role
  ) VALUES (f.h1, entity, f.v1, 'HISTORICAL SYNTHETIC PERSON', 'transfer')
  RETURNING id INTO alias;

  UPDATE counterparty_alias SET is_active = false, deactivated_at = now() WHERE id = alias;
  UPDATE counterparty SET is_active = false, deactivated_at = now() WHERE id = entity;
  ASSERT EXISTS (SELECT FROM counterparty WHERE id = entity AND NOT is_active),
    'deactivated identity remains historical';
  ASSERT EXISTS (SELECT FROM counterparty_alias WHERE id = alias AND NOT is_active),
    'deactivated alias remains historical';

  BEGIN
    UPDATE counterparty SET display_name = 'Rewritten historical identity' WHERE id = entity;
    RAISE EXCEPTION 'counterparty identity history must not be rewritten';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty identity is immutable%', refusal;
  END;

  BEGIN
    UPDATE counterparty_alias SET is_active = true, deactivated_at = NULL WHERE id = alias;
    RAISE EXCEPTION 'counterparty alias must not be reactivated in place';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty alias lifecycle only allows active to inactive%', refusal;
  END;

  BEGIN
    DELETE FROM counterparty_alias WHERE id = alias;
    RAISE EXCEPTION 'counterparty alias history must not be deleted';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%counterparty alias is immutable%', refusal;
  END;
  RAISE NOTICE 'PASS 0021 deactivation preserves history';
END $$;
ROLLBACK;
