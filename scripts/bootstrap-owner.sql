BEGIN;

SELECT set_config('oneledger.bootstrap_email', lower(btrim(:'email')), true);

DO $$
DECLARE
  expected_email TEXT := current_setting('oneledger.bootstrap_email');
  self_count INTEGER;
  owner_count INTEGER;
BEGIN
  SELECT count(*) INTO self_count FROM person WHERE is_self;
  IF self_count <> 1 THEN
    RAISE EXCEPTION 'owner bootstrap requires exactly one self person';
  END IF;

  SELECT count(*) INTO owner_count FROM app_user WHERE role = 'owner';
  IF owner_count = 0 THEN
    INSERT INTO app_user (id, household_id, person_id, email, role)
    SELECT gen_random_uuid(), household_id, id, expected_email, 'owner'
      FROM person WHERE is_self;
  ELSIF owner_count <> 1 OR NOT EXISTS (
    SELECT FROM app_user
     WHERE role = 'owner' AND lower(btrim(email)) = expected_email
  ) THEN
    RAISE EXCEPTION 'configured owner does not match the existing owner';
  END IF;
END $$;

COMMIT;

SELECT 'owner ready';
