-- db/tests/0001_reference.sql
-- Reference data is asserted by property, never by membership. Naming a currency here would
-- make it a required seed row, and every currency in these documents is an example (ADR-0012):
-- a household reporting in any other pair must pass this suite unchanged.
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM currency) >= 2,
    'a reporting pair needs at least two seeded currencies';
  ASSERT NOT EXISTS (SELECT FROM currency WHERE decimals < 0 OR decimals > 6),
    'currency decimals are a plausible minor-unit scale';
  ASSERT NOT EXISTS (SELECT FROM currency WHERE number_format NOT IN ('international', 'indian')),
    'number_format is one of the two supported values';
  ASSERT NOT EXISTS (
    SELECT FROM currency WHERE (pegged_to IS NULL) IS DISTINCT FROM (peg_rate IS NULL)),
    'a peg is complete or absent';
  ASSERT NOT EXISTS (
    SELECT FROM currency c
     WHERE c.pegged_to IS NOT NULL
       AND NOT EXISTS (SELECT FROM currency a WHERE a.code = c.pegged_to)),
    'every peg anchor is itself a seeded currency';
  ASSERT NOT EXISTS (SELECT FROM currency WHERE peg_rate IS NOT NULL AND peg_rate <= 0),
    'a peg rate is positive';
  -- A peg may point at another peg (A→B→USD). The API walks that chain
  -- (anchor_chain); forbidding a pegged anchor would fail a valid seed. A cycle
  -- is the case that cannot terminate.
  ASSERT NOT EXISTS (
    WITH RECURSIVE walk AS (
      SELECT rtrim(code) AS origin,
             rtrim(pegged_to) AS at,
             ARRAY[rtrim(code)] AS seen
        FROM currency
       WHERE pegged_to IS NOT NULL
      UNION ALL
      SELECT w.origin,
             rtrim(c.pegged_to),
             w.seen || w.at
        FROM walk w
        JOIN currency c ON rtrim(c.code) = w.at
       WHERE w.at IS NOT NULL
         AND NOT (w.at = ANY (w.seen))
    )
    SELECT 1 FROM walk
     WHERE at IS NOT NULL AND at = ANY (seen)
  ),
    'a peg chain must not cycle';
  ASSERT (SELECT count(*) FROM institution WHERE household_id IS NULL) >= 6,
    'the shared institution suggestions are seeded';
  RAISE NOTICE 'PASS 0001';
END $$;
