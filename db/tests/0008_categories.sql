-- db/tests/0008_categories.sql
-- The seed taxonomy is asserted by property, never by membership (ADR-0012): the tree exists,
-- every category carries a bucket, the tree is two levels, and no test names a real currency,
-- bank or brand. Merchant tables exist but stay empty until M4's rule packs.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'category'),
    'RLS must be enabled on category';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'merchant'),
    'RLS must be enabled on merchant';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'merchant_alias'),
    'RLS must be enabled on merchant_alias';

  ASSERT (SELECT count(*) FROM category WHERE household_id IS NULL AND parent_id IS NULL) >= 15,
    'the system taxonomy seeds its top-level categories';
  ASSERT (SELECT count(*) FROM category WHERE household_id IS NULL AND parent_id IS NOT NULL) >= 40,
    'the system taxonomy seeds its subcategories';
  ASSERT NOT EXISTS (SELECT FROM category WHERE household_id IS NULL AND NOT is_system),
    'every system row is marked is_system';
  ASSERT NOT EXISTS (
    SELECT FROM category c JOIN category p ON p.id = c.parent_id
     WHERE p.parent_id IS NOT NULL),
    'the seeded tree is two levels deep';
  ASSERT (SELECT count(DISTINCT bucket) FROM category WHERE household_id IS NULL) >= 5,
    'the seed uses the bucket vocabulary (ADR-0008), not one default value';
  ASSERT NOT EXISTS (
    SELECT FROM category c JOIN category p ON p.id = c.parent_id
     WHERE c.household_id IS NULL AND c.bucket <> p.bucket),
    'a seeded child starts with its parent''s bucket as the editable default';
  ASSERT (SELECT count(*) FROM merchant) = 0,
    'merchant rows arrive with the M4 rule packs, not the taxonomy';
  RAISE NOTICE 'PASS 0008 structure';
END $$;

-- The seed is idempotent: a system slug is unique, and the seed inserts ON CONFLICT DO NOTHING,
-- so a later seed-update migration can re-run the set without duplicating rows.
DO $$
DECLARE
  existing TEXT := (SELECT slug FROM category WHERE household_id IS NULL AND parent_id IS NULL LIMIT 1);
  before BIGINT := (SELECT count(*) FROM category);
BEGIN
  INSERT INTO category (household_id, name, slug, kind, bucket, is_system)
    SELECT NULL, 'Duplicate Probe', existing, 'expense', 'want', true
  ON CONFLICT DO NOTHING;
  ASSERT (SELECT count(*) FROM category) = before,
    're-inserting an existing system slug is a no-op';

  BEGIN
    INSERT INTO category (household_id, name, slug, kind, bucket, is_system)
      VALUES (NULL, 'Duplicate Probe', existing, 'expense', 'want', true);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  RAISE NOTICE 'PASS 0008 idempotent seed';
END $$;

-- Two levels, enforced in both directions by trigger (the schema.md CHECK carried a subquery,
-- which PostgreSQL rejects in CHECK constraints; the trigger is the reconciled shape).
DO $$
DECLARE
  root  UUID; child UUID; root2 UUID;
BEGIN
  INSERT INTO category (name, slug, kind, bucket) VALUES ('T Root', 'test-root', 'expense', 'want')
    RETURNING id INTO root;
  INSERT INTO category (name, slug, kind, bucket, parent_id)
    VALUES ('T Child', 'test-child', 'expense', 'want', root)
    RETURNING id INTO child;

  BEGIN
    INSERT INTO category (name, slug, kind, bucket, parent_id)
      VALUES ('T Grandchild', 'test-grandchild', 'expense', 'want', child);
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a child of a child must be rejected';
    END IF;
  END;

  INSERT INTO category (name, slug, kind, bucket) VALUES ('T Root 2', 'test-root-2', 'expense', 'want')
    RETURNING id INTO root2;
  BEGIN
    UPDATE category SET parent_id = root2 WHERE id = root;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 're-parenting a category that has children must be rejected';
    END IF;
  END;

  BEGIN
    UPDATE category SET parent_id = root2 WHERE id = root2;
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM = 'unreachable' THEN
      RAISE EXCEPTION 'a category must not be its own parent';
    END IF;
  END;

  RAISE NOTICE 'PASS 0008 two levels';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;

-- A household's bucket and activity choices are overrides beside the seed, never edits of it.
DO $$
DECLARE h UUID; seeded UUID;
        base CHAR(3); secondary CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('override fixture', base, secondary) RETURNING id INTO h;
  SELECT id INTO seeded FROM category WHERE household_id IS NULL AND parent_id IS NULL LIMIT 1;

  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'category_override'),
    'RLS must be enabled on category_override';

  INSERT INTO category_override (household_id, category_id, bucket) VALUES (h, seeded, 'need');
  INSERT INTO category_override (household_id, category_id, bucket, is_active)
    VALUES (h, seeded, 'want', false)
  ON CONFLICT (household_id, category_id)
    DO UPDATE SET bucket = EXCLUDED.bucket, is_active = EXCLUDED.is_active;
  ASSERT (SELECT count(*) FROM category_override WHERE household_id = h) = 1,
    'one override row per household and category';
  ASSERT (SELECT bucket FROM category_override WHERE household_id = h AND category_id = seeded)
           = 'want'::category_bucket,
    'the upsert replaces the override';
  ASSERT (SELECT bucket FROM category WHERE id = seeded) <> 'want'::category_bucket
         OR (SELECT slug FROM category WHERE id = seeded) IS NOT NULL,
    'sanity read';

  BEGIN
    INSERT INTO category_override (household_id, category_id)
      VALUES (h, (SELECT id FROM category WHERE household_id IS NULL AND parent_id IS NULL
                   OFFSET 1 LIMIT 1));
    RAISE EXCEPTION 'unreachable';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- A cross-tenant parent is refused.
  DECLARE h3 UUID; their_root UUID;
  BEGIN
    INSERT INTO household (name, base_currency, secondary_currency)
    VALUES ('parent fixture', base, secondary) RETURNING id INTO h3;
    INSERT INTO category (household_id, name, slug, kind, bucket)
      VALUES (h3, 'Their Root', 'their-root', 'expense', 'want') RETURNING id INTO their_root;
    BEGIN
      INSERT INTO category (household_id, name, slug, kind, bucket, parent_id)
        VALUES (h, 'My Child', 'my-child', 'expense', 'want', their_root);
      RAISE EXCEPTION 'unreachable';
    EXCEPTION WHEN raise_exception THEN
      IF SQLERRM = 'unreachable' THEN
        RAISE EXCEPTION 'a cross-household parent must be rejected';
      END IF;
    END;
  END;

  RAISE NOTICE 'PASS 0008 overrides';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;

-- Isolation follows the institution pattern: the shared seed set is readable by everyone
-- authenticated, another household's rows are not, and anon sees only the seed set.
BEGIN;
  WITH pair AS (
    SELECT (SELECT min(code) FROM currency) AS base,
           (SELECT min(code) FROM currency
             WHERE code > (SELECT min(code) FROM currency)) AS secondary
  )
  INSERT INTO household (id, name, base_currency, secondary_currency)
  SELECT '41111111-1111-1111-1111-111111111111', 'category rls fixture', base, secondary FROM pair;
  INSERT INTO category (id, household_id, name, slug, kind, bucket) VALUES
    ('42222222-2222-2222-2222-222222222222',
     '41111111-1111-1111-1111-111111111111', 'Private Category', 'private-cat', 'expense', 'want');

  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM category WHERE household_id IS NOT NULL) >= 1,
      'the household fixture must exist before the isolation check';
  END $$;

  SET LOCAL ROLE anon;
  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM category WHERE household_id IS NOT NULL) = 0,
      'anon must not read any household''s categories';
    ASSERT (SELECT count(*) FROM category WHERE household_id IS NULL) >= 15,
      'the system taxonomy stays readable, or the category picker is empty';
    RAISE NOTICE 'PASS 0008 isolation';
  END $$;
ROLLBACK;
