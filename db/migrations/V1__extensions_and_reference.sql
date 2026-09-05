-- db/migrations/V1__extensions_and_reference.sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() is built in on PG15,
                                           -- pgcrypto is for digest() used in account hashing

-- Two NOLOGIN roles the RLS policies and the isolation tests are written against.
-- `anon` is the unauthenticated role every policy must return nothing to; `authenticated`
-- is what a future non-service client would connect as. Neither can log in, and neither is
-- what the API service uses — it connects privileged and bypasses RLS (ADR-0013).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated NOLOGIN;
  END IF;
END $$;

-- The grant matters as much as the role. Without it the isolation test dies with
-- "permission denied for table household" instead of returning zero rows, and a test that
-- fails for the wrong reason proves nothing.
GRANT USAGE ON SCHEMA public TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon, authenticated;

CREATE TABLE currency (
  code          CHAR(3) PRIMARY KEY,
  name          TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  decimals      SMALLINT NOT NULL DEFAULT 2,
  number_format TEXT NOT NULL DEFAULT 'international'
    CHECK (number_format IN ('international','indian')),
  pegged_to     CHAR(3) REFERENCES currency(code),
  peg_rate      NUMERIC(20,10),
  CONSTRAINT peg_is_complete CHECK ((pegged_to IS NULL) = (peg_rate IS NULL)),
  CONSTRAINT peg_is_not_self CHECK (pegged_to IS DISTINCT FROM code)
);

-- A peg is data. AED has held at 3.6725 per USD since 1997; a deployment that pegs nothing
-- simply seeds no peg, and the rate pipeline fetches every currency it needs.
INSERT INTO currency (code, name, symbol, decimals, number_format, pegged_to, peg_rate) VALUES
  ('USD', 'US Dollar',       '$',   2, 'international', NULL,  NULL),
  ('EUR', 'Euro',            '€',   2, 'international', NULL,  NULL),
  ('GBP', 'Pound Sterling',  '£',   2, 'international', NULL,  NULL),
  ('INR', 'Indian Rupee',    '₹',   2, 'indian',        NULL,  NULL),
  ('AED', 'UAE Dirham',      'AED', 2, 'international', 'USD', 3.6725);

CREATE TABLE institution (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id     UUID,                            -- NULL = system seed; FK added in 0002
  name             TEXT NOT NULL,
  short_name       TEXT,
  country          CHAR(2) NOT NULL,
  institution_type TEXT NOT NULL CHECK (institution_type IN
    ('bank','card_issuer','broker','fund_house','lender','govt','insurer','employer',
     'exchange_house','other')),
  website          TEXT,
  logo_url         TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_institution_country ON institution (country, name);
