-- db/tests/0001_reference.sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM currency WHERE code IN ('AED','INR','USD')) = 3,
    'expected AED, INR and USD seeded';
  ASSERT (SELECT number_format FROM currency WHERE code = 'INR') = 'indian',
    'INR must use Indian digit grouping';
  ASSERT (SELECT count(*) FROM institution WHERE household_id IS NULL) >= 6,
    'expected system-seeded institutions';
  RAISE NOTICE 'PASS 0001';
END $$;
