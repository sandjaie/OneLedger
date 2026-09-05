-- db/migrations/V2__identity_and_rls.sql
CREATE TABLE household (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT NOT NULL,
  base_currency      CHAR(3) NOT NULL REFERENCES currency(code),
  secondary_currency CHAR(3) NOT NULL REFERENCES currency(code),
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT reporting_pair_differs CHECK (base_currency <> secondary_currency)
);

CREATE TABLE person (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID NOT NULL REFERENCES household(id),
  display_name      TEXT NOT NULL,
  is_self           BOOLEAN NOT NULL DEFAULT false,
  tax_residency     CHAR(2),
  tax_id_last4      TEXT CHECK (tax_id_last4 IS NULL OR length(tax_id_last4) = 4),
  national_id_last4 TEXT CHECK (national_id_last4 IS NULL OR length(national_id_last4) = 4),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
  id           UUID PRIMARY KEY,                 -- = auth.users.id
  household_id UUID NOT NULL REFERENCES household(id),
  person_id    UUID NOT NULL REFERENCES person(id),
  email        TEXT NOT NULL,
  role         TEXT NOT NULL DEFAULT 'owner' CHECK (role IN ('owner','member')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE institution ADD CONSTRAINT fk_institution_household
  FOREIGN KEY (household_id) REFERENCES household(id);

-- The single source of the RLS predicate. STABLE so the planner caches it per statement;
-- SECURITY DEFINER so reading app_user is not itself blocked by app_user's own policy.
CREATE FUNCTION current_household_id() RETURNS UUID
  LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT household_id FROM app_user
  WHERE id = NULLIF(current_setting('app.user_id', true), '')::UUID
$$;

-- Authentication is not authorization. Google authenticates ANY Google account, and the UI is
-- not the boundary. Every SECURITY DEFINER function calls this first.
CREATE FUNCTION assert_is_owner() RETURNS VOID
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  IF current_household_id() IS NULL THEN
    RAISE EXCEPTION 'not authorized' USING ERRCODE = '42501';
  END IF;
END $$;

ALTER TABLE household   ENABLE ROW LEVEL SECURITY;
ALTER TABLE person      ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user    ENABLE ROW LEVEL SECURITY;
ALTER TABLE institution ENABLE ROW LEVEL SECURITY;

CREATE POLICY household_isolation ON household
  USING (id = current_household_id());
CREATE POLICY household_isolation ON person
  USING (household_id = current_household_id());
CREATE POLICY household_isolation ON app_user
  USING (household_id = current_household_id());

-- Institutions carry system seed rows with household_id IS NULL. Read must admit them or the
-- seeded list is invisible; write must not, or a user could edit the shared set in place.
CREATE POLICY institution_read ON institution FOR SELECT
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY institution_write ON institution FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
