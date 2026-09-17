-- V61: allocation targets and benchmarks for investment analysis. All names, identifiers and
-- amounts below are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'investment_allocation_target') = 1,
    'investment_allocation_target must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'investment_allocation_target'::regclass),
    'investment_allocation_target must have row level security enabled';
  ASSERT (SELECT string_agg(column_name || ':' || data_type || ':' || is_nullable, ',' ORDER BY
                            ordinal_position)
            FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'investment_allocation_target')
       = 'household_id:uuid:NO,label:USER-DEFINED:NO,ratio:numeric:NO,'
         'created_at:timestamp with time zone:NO,updated_at:timestamp with time zone:NO',
    'investment_allocation_target columns must match the V61 contract';
  ASSERT (SELECT array_agg(a.attname::TEXT ORDER BY k.ord)
            FROM pg_constraint c
            CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
           WHERE c.conrelid = 'investment_allocation_target'::regclass AND c.contype = 'p')
       = ARRAY['household_id', 'label'],
    'the allocation target primary key must be (household_id, label)';

  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'investment_benchmark') = 1,
    'investment_benchmark must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'investment_benchmark'::regclass),
    'investment_benchmark must have row level security enabled';
  ASSERT (SELECT string_agg(column_name || ':' || data_type || ':' || is_nullable, ',' ORDER BY
                            ordinal_position)
            FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = 'investment_benchmark')
       = 'household_id:uuid:NO,id:uuid:NO,scope_kind:text:NO,asset_class:USER-DEFINED:YES,'
         'security_id:uuid:NO,created_at:timestamp with time zone:NO,'
         'updated_at:timestamp with time zone:NO',
    'investment_benchmark columns must match the V61 contract';
  ASSERT (SELECT indexdef FROM pg_indexes
           WHERE schemaname = 'public' AND tablename = 'investment_benchmark'
             AND indexname = 'uq_investment_benchmark_scope') LIKE '%NULLS NOT DISTINCT%',
    'the benchmark scope uniqueness must treat NULL asset_class as one value per household';
  RAISE NOTICE 'PASS 0058 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0058_fixture()
  RETURNS TABLE (h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID, s1 UUID, s2 UUID)
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID; sec1 UUID; sec2 UUID;
BEGIN
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('analysis rule household one', (SELECT min(code) FROM currency),
          (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency)))
  RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('analysis rule household two', (SELECT min(code) FROM currency),
          (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency)))
  RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'analysis rule owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'analysis rule member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other analysis rule owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'analysis-rule-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'analysis-rule-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-analysis-rule-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h1, 'mutual_fund', 'analysis rule security one', (SELECT min(code) FROM currency), 'XZ',
          'equity')
  RETURNING id INTO sec1;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h2, 'mutual_fund', 'analysis rule security two', (SELECT min(code) FROM currency), 'XZ',
          'equity')
  RETURNING id INTO sec2;
  s1 := sec1; s2 := sec2;
  RETURN NEXT;
END $$;

-- The ratio check refuses anything outside [0, 1]; the primary key is (household_id, label).
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0058_fixture();
  BEGIN
    INSERT INTO investment_allocation_target (household_id, label, ratio)
    VALUES (f.h1, 'equity', 1.1);
    RAISE EXCEPTION 'a ratio above 1 must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO investment_allocation_target (household_id, label, ratio)
    VALUES (f.h1, 'equity', -0.1);
    RAISE EXCEPTION 'a negative ratio must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  INSERT INTO investment_allocation_target (household_id, label, ratio)
  VALUES (f.h1, 'equity', 0.6000000000);
  BEGIN
    INSERT INTO investment_allocation_target (household_id, label, ratio)
    VALUES (f.h1, 'equity', 0.5000000000);
    RAISE EXCEPTION 'a duplicate (household_id, label) insert must have been refused';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  INSERT INTO investment_allocation_target (household_id, label, ratio)
  VALUES (f.h1, 'fixed_income', 0.4000000000);
  INSERT INTO investment_allocation_target (household_id, label, ratio)
  VALUES (f.h2, 'equity', 1.0000000000);
  ASSERT (SELECT ratio FROM investment_allocation_target WHERE household_id = f.h1
           AND label = 'equity') = 0.6000000000, 'household one keeps its own equity ratio';
  ASSERT (SELECT ratio FROM investment_allocation_target WHERE household_id = f.h2
           AND label = 'equity') = 1.0000000000, 'household two keeps its own equity ratio';
  RAISE NOTICE 'PASS 0058 allocation target checks and primary key';
END $$;
ROLLBACK;

-- The benchmark scope check and its NULLS NOT DISTINCT uniqueness.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0058_fixture();
  BEGIN
    INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
    VALUES (f.h1, 'portfolio', 'equity', f.s1);
    RAISE EXCEPTION 'a portfolio-scope row with an asset class must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
    VALUES (f.h1, 'asset_class', NULL, f.s1);
    RAISE EXCEPTION 'a class-scope row with no asset class must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
    VALUES (f.h1, 'index', NULL, f.s1);
    RAISE EXCEPTION 'an unknown scope_kind must have been refused';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
  VALUES (f.h1, 'portfolio', NULL, f.s1);
  BEGIN
    INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
    VALUES (f.h1, 'portfolio', NULL, f.s1);
    RAISE EXCEPTION 'a second portfolio-scope row for the same household must have been refused';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
  VALUES (f.h1, 'asset_class', 'equity', f.s1);
  BEGIN
    INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
    VALUES (f.h1, 'asset_class', 'equity', f.s1);
    RAISE EXCEPTION 'a second equity-scope row for the same household must have been refused';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  -- A different household may assign the same security as its own benchmark independently.
  INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
  VALUES (f.h2, 'portfolio', NULL, f.s2);
  RAISE NOTICE 'PASS 0058 benchmark scope check and uniqueness';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS on both tables.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0058_fixture();
  INSERT INTO investment_allocation_target (household_id, label, ratio)
  VALUES (f.h1, 'equity', 1.0000000000);
  INSERT INTO investment_allocation_target (household_id, label, ratio)
  VALUES (f.h2, 'equity', 1.0000000000);
  INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
  VALUES (f.h1, 'portfolio', NULL, f.s1);
  INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)
  VALUES (f.h2, 'portfolio', NULL, f.s2);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM investment_allocation_target) = 1,
    'owner sees only its own household target rows';
  ASSERT (SELECT count(*) FROM investment_benchmark) = 1,
    'owner sees only its own household benchmark rows';
  BEGIN
    INSERT INTO investment_allocation_target (household_id, label, ratio)
    VALUES (f.h2, 'fixed_income', 1.0000000000);
    RAISE EXCEPTION 'an owner must not write a target into another household';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM investment_allocation_target) = 0,
    'a household member reads no target row';
  ASSERT (SELECT count(*) FROM investment_benchmark) = 0,
    'a household member reads no benchmark row';
  BEGIN
    INSERT INTO investment_allocation_target (household_id, label, ratio)
    VALUES (f.h1, 'cash', 1.0000000000);
    RAISE EXCEPTION 'a household member must not write a target row';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM investment_allocation_target) = 1,
    'the other household sees only its own target row';
  ASSERT (SELECT count(*) FROM investment_benchmark) = 1,
    'the other household sees only its own benchmark row';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM investment_allocation_target) = 0,
    'anonymous target reads are empty';
  ASSERT (SELECT count(*) FROM investment_benchmark) = 0, 'anonymous benchmark reads are empty';
  RESET ROLE;
  RAISE NOTICE 'PASS 0058 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
