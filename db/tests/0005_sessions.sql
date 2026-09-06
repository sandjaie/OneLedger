\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT EXISTS (SELECT FROM information_schema.columns
    WHERE table_name = 'app_user' AND column_name = 'google_subject' AND is_nullable = 'YES'),
    'Google subject must be optional until first login';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'app_session'),
    'RLS must be enabled on app_session';
END $$;

BEGIN;
  INSERT INTO household (id, name, base_currency, secondary_currency) VALUES
    ('51111111-1111-1111-1111-111111111111', 'session fixture', 'INR', 'AED');
  INSERT INTO person (id, household_id, display_name) VALUES
    ('52222222-2222-2222-2222-222222222222',
     '51111111-1111-1111-1111-111111111111', 'owner'),
    ('52222222-2222-2222-2222-222222222223',
     '51111111-1111-1111-1111-111111111111', 'member');
  INSERT INTO app_user (id, household_id, person_id, email, role, google_subject) VALUES
    ('53333333-3333-3333-3333-333333333333',
     '51111111-1111-1111-1111-111111111111',
     '52222222-2222-2222-2222-222222222222', 'owner@example.test', 'owner', 'google-owner'),
    ('53333333-3333-3333-3333-333333333334',
     '51111111-1111-1111-1111-111111111111',
     '52222222-2222-2222-2222-222222222223', 'member@example.test', 'member', NULL);
  INSERT INTO app_session (id, app_user_id, expires_at, revoked_at, created_at) VALUES
    ('54444444-4444-4444-4444-444444444441',
     '53333333-3333-3333-3333-333333333333', now() + interval '1 hour', NULL, now()),
    ('54444444-4444-4444-4444-444444444442',
     '53333333-3333-3333-3333-333333333333', now() + interval '1 hour', now(), now()),
    ('54444444-4444-4444-4444-444444444443',
     '53333333-3333-3333-3333-333333333333', now() - interval '1 hour', NULL,
     now() - interval '2 hours'),
    ('54444444-4444-4444-4444-444444444444',
     '53333333-3333-3333-3333-333333333334', now() + interval '1 hour', NULL, now());
  DO $$
  BEGIN
    BEGIN
      UPDATE app_user SET google_subject = 'google-owner'
       WHERE id = '53333333-3333-3333-3333-333333333334';
      RAISE EXCEPTION 'duplicate Google subject was accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    ASSERT (SELECT count(*) FROM app_session s JOIN app_user u ON u.id = s.app_user_id
            WHERE s.revoked_at IS NULL AND s.expires_at > now() AND u.role = 'owner') = 1,
      'only an unexpired, unrevoked owner session is active';
    ASSERT NOT EXISTS (SELECT FROM app_session WHERE id =
      '54444444-4444-4444-4444-444444444442' AND revoked_at IS NULL),
      'revoked session must be inactive';
    ASSERT NOT EXISTS (SELECT FROM app_session WHERE id =
      '54444444-4444-4444-4444-444444444443' AND expires_at > now()),
      'expired session must be inactive';
    UPDATE app_session SET revoked_at = now()
     WHERE id = '54444444-4444-4444-4444-444444444441';
    ASSERT NOT EXISTS (SELECT FROM app_session WHERE id =
      '54444444-4444-4444-4444-444444444441' AND revoked_at IS NULL),
      'revoking the exact active session must make it inactive';
    PERFORM set_config('app.user_id', '53333333-3333-3333-3333-333333333334', true);
    ASSERT current_household_id() IS NULL, 'member role must not establish owner scope';
  END $$;
  SET LOCAL ROLE anon;
  DO $$ BEGIN
    ASSERT (SELECT count(*) FROM app_session) = 0, 'anonymous role must not read sessions';
  END $$;
  RESET ROLE;
  SET LOCAL app.user_id = '53333333-3333-3333-3333-333333333333';
  SET LOCAL ROLE authenticated;
  DO $$ BEGIN
    ASSERT (SELECT count(*) FROM app_session) = 3, 'owner may read only their sessions';
  END $$;
ROLLBACK;
