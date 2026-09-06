-- db/migrations/V11__category_overrides.sql
-- The seeded taxonomy is versioned shared data. The service connects privileged and bypasses
-- RLS, so a bucket or activity edit on a system row must not rewrite the seed in place — the
-- same rule V9's RLS policies state for direct writers. The household's choices live beside
-- the seed and win on read; the seed stays re-runnable and recognisable.
CREATE TABLE category_override (
  household_id UUID NOT NULL REFERENCES household(id),
  category_id  UUID NOT NULL REFERENCES category(id) ON DELETE CASCADE,
  bucket       category_bucket,
  is_active    BOOLEAN,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, category_id),
  CONSTRAINT override_overrides_something CHECK (bucket IS NOT NULL OR is_active IS NOT NULL)
);

ALTER TABLE category_override ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON category_override
  USING (household_id = current_household_id());
