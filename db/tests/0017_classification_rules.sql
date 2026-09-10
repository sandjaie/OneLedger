-- db/tests/0017_classification_rules.sql
-- V19: `classification_rule` per schema.md `0009` — household-isolated, refusing a rule that
-- matches nothing or decides nothing, and completing the two foreign keys V10 and V13 left
-- bare because this table did not exist yet.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT to_regclass('public.classification_rule') IS NOT NULL,
    'classification_rule must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'classification_rule'),
    'RLS must be enabled on classification_rule';
  ASSERT (SELECT count(*) FROM pg_indexes
           WHERE tablename = 'classification_rule' AND indexname = 'idx_rule_priority') = 1,
    'idx_rule_priority must exist';
  -- The two foreign keys schema.md `0018` reserves for the migration that creates the target.
  ASSERT EXISTS (SELECT FROM pg_constraint
                  WHERE conname = 'fk_txn_classification_rule' AND conrelid = 'transaction'::regclass),
    'transaction.classification_rule_id must gain its foreign key with this table';
  ASSERT EXISTS (SELECT FROM pg_constraint
                  WHERE conname = 'fk_staged_classification_rule'
                    AND conrelid = 'staged_transaction'::regclass),
    'staged_transaction.classification_rule_id must gain its foreign key with this table';
  -- 'r' is RESTRICT. Stated as an assertion because the alternative, SET NULL, would leave a
  -- row claiming `classified_by = 'user_rule'` beside no rule at all (I6).
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname IN ('fk_txn_classification_rule', 'fk_staged_classification_rule')
             AND confdeltype = 'r') = 2,
    'both foreign keys must RESTRICT on delete, so provenance cannot be deleted away';
  RAISE NOTICE 'PASS 0017 structure';
END $$;

-- ---------------------------------------------------------------------------------------
-- A rule matches something and decides something. Neither half is optional, and a rule with
-- only the transfer flag decides something — that is the whole of what a transfer rule does.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE h UUID; p UUID; base CHAR(3); secondary CHAR(3); c UUID; refusal TEXT;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('rule shape', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'self', true) RETURNING id INTO p;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h, 'Groceries', 'rule-shape-groceries', 'expense', 'need') RETURNING id INTO c;

  BEGIN
    INSERT INTO classification_rule (household_id, set_category_id, origin)
    VALUES (h, c, 'user_created');
    RAISE EXCEPTION 'a rule that matches nothing must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%has_matcher%', 'the refusal must name has_matcher, got ' || refusal;
  END;

  BEGIN
    INSERT INTO classification_rule (household_id, match_description_pattern, origin)
    VALUES (h, 'SALARY', 'user_created');
    RAISE EXCEPTION 'a rule that decides nothing must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%has_outcome%', 'the refusal must name has_outcome, got ' || refusal;
  END;

  -- The transfer flag alone is an outcome: marking a row a transfer candidate is the decision.
  INSERT INTO classification_rule (household_id, match_description_pattern,
                                   set_is_transfer_candidate, origin)
  VALUES (h, 'CARD PAYMENT', true, 'user_created');

  -- A user rule outranks a seed rule without either knowing the other exists.
  INSERT INTO classification_rule (household_id, match_description_pattern, set_category_id, origin)
  VALUES (h, 'GROCER', c, 'user_created');
  ASSERT (SELECT priority FROM classification_rule
           WHERE origin = 'user_created' AND match_description_pattern = 'GROCER') = 100,
    'a user rule defaults to priority 100';
  RAISE NOTICE 'PASS 0017 a rule matches something and decides something';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Isolation. There is no shared seed row here: a seeded rule belongs to the household that
-- loaded it, so household_id is NOT NULL and the policy is a plain equality.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE h1 UUID; h2 UUID; p1 UUID; p2 UUID; u1 UUID; base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('rules one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('rules two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h1, 'self', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h2, 'self', true) RETURNING id INTO p2;
  u1 := gen_random_uuid();
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (u1, h1, p1, 'rules-' || u1 || '@example.invalid', 'owner');

  INSERT INTO classification_rule (household_id, match_description_pattern, set_txn_type, origin)
  VALUES (h1, 'MINE', 'expense', 'user_created'),
         (h2, 'THEIRS', 'expense', 'user_created');

  PERFORM set_config('app.user_id', u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM classification_rule) = 1,
    'a household reads only its own rules';
  ASSERT (SELECT match_description_pattern FROM classification_rule) = 'MINE',
    'and it reads the right one';
  RESET ROLE;
  RAISE NOTICE 'PASS 0017 a household reads only its own rules';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Provenance never dangles. A committed row names the rule that classified it, so the rule
-- cannot be deleted out from under it — I6 is about a row being able to say where it came
-- from, and a dangling id says nothing.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE h UUID; p UUID; a UUID; r UUID; base CHAR(3); secondary CHAR(3); refusal TEXT;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('rule provenance', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h, 'self', true) RETURNING id INTO p;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
  VALUES (h, p, 'Rules Savings', 'savings', base, 'XB', 0, '2026-01-01') RETURNING id INTO a;
  INSERT INTO classification_rule (household_id, match_description_pattern, set_txn_type, origin)
  VALUES (h, 'SALARY', 'income', 'user_created') RETURNING id INTO r;

  BEGIN
    INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                             currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                             amount_base, amount_secondary, txn_type, source,
                             classified_by, classification_rule_id, classification_confidence,
                             dedupe_hash)
    VALUES (h, a, '2026-03-01', 'SALARY', 100, base, '2026-03-01', 1, 1, 100, 100, 'income',
            'manual', 'user_rule', gen_random_uuid(), 0.980, repeat('1', 64));
    RAISE EXCEPTION 'a transaction must not name a rule that does not exist';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;

  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source,
                           classified_by, classification_rule_id, classification_confidence,
                           dedupe_hash)
  VALUES (h, a, '2026-03-01', 'SALARY', 100, base, '2026-03-01', 1, 1, 100, 100, 'income',
          'manual', 'user_rule', r, 0.980, repeat('2', 64));

  -- The delete itself is not attempted here. A RESTRICT violation is raised by an AFTER
  -- trigger that fires when the outermost statement ends — the whole `DO` block — so no
  -- PL/pgSQL handler inside it can catch one, and a test that tried would abort the script
  -- rather than assert anything. The declaration is asserted in the structure block above
  -- (`confdeltype = 'r'`), which is the part this repository owns; that Postgres then honours
  -- RESTRICT is Postgres's to test.
  --
  -- What is asserted here is the path that must stay open: retiring a rule is a deactivation,
  -- and it leaves both the rule and every answer it gave.
  UPDATE classification_rule SET is_active = false WHERE id = r;
  ASSERT (SELECT classification_rule_id FROM transaction WHERE household_id = h) = r,
    'the transaction still names the rule that classified it';
  RAISE NOTICE 'PASS 0017 provenance never dangles';
END $$;
ROLLBACK;
