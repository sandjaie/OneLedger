ALTER TABLE app_user ADD COLUMN google_subject TEXT;
CREATE UNIQUE INDEX idx_app_user_google_subject
  ON app_user (google_subject) WHERE google_subject IS NOT NULL;

CREATE TABLE app_session (
  id          UUID PRIMARY KEY,
  app_user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  expires_at  TIMESTAMPTZ NOT NULL,
  revoked_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT session_expires_after_creation CHECK (expires_at > created_at)
);
CREATE INDEX idx_app_session_owner_active ON app_session (app_user_id, expires_at)
  WHERE revoked_at IS NULL;

CREATE OR REPLACE FUNCTION current_household_id() RETURNS UUID
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT household_id FROM app_user
  WHERE id = NULLIF(current_setting('app.user_id', true), '')::UUID AND role = 'owner'
$$;

ALTER TABLE app_session ENABLE ROW LEVEL SECURITY;
CREATE POLICY owner_session_isolation ON app_session
  USING (EXISTS (
    SELECT 1 FROM app_user
    WHERE id = app_session.app_user_id AND role = 'owner'
      AND household_id = current_household_id()
  ));
