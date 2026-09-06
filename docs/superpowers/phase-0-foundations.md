# Phase 0 — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Historical step
> checkboxes below are instructions; maintain execution state only in [progress.md](progress.md).

**Goal:** A working local stack — schema, RLS, auth and 36 months of FX rates — with nothing built
on top of it yet.

**Architecture:** One `docker compose` stack — `web`, `api`, `db`
([ADR-0014](../adr/0014-one-vps-one-compose-file.html)). A **FastAPI service on :8000** owns all
business logic and is the authorization boundary; a **Next.js app on :3000** is the interface and
holds none; **Postgres 18** holds the truth and the invariants. Migrations are forward-only SQL,
applied by Flyway ([ADR-0015](../adr/0015-flyway-for-schema-migrations.html)).

**The compose file you build here is the one that deploys later.** Nothing in Phase 0 depends on a
managed service, so reaching the hosted target is running the same file on a VPS.

Tasks are ordered backend-first: the schema, then the service, then the UI on top of it.

**Tech Stack:** Docker Compose · Postgres 18 · Python 3.14 · FastAPI · Pydantic · `pdfplumber` ·
uv · ruff · mypy --strict · pytest · hypothesis · Next.js App Router · TypeScript · Tailwind ·
`decimal.js` · vitest

**Spec:** [02-architecture.md](../design/architecture.html),
[03-data-model.md](../design/data-model.html) §1–§5,
[04-multi-currency-and-fx.md](../design/multi-currency-and-fx.html) §3,
[05-statement-ingestion.md](../design/statement-ingestion.html) §4,
[09-security-and-privacy.md](../operations/security-and-privacy.html),
[ADR-0013](../adr/0013-python-backend-service.html)

## Using this plan

This is an execution reference, not a status tracker. Read [implementation progress](progress.md)
before starting work. Original PR headings are ordinal groups, not GitHub PR numbers.
The [completion specification](specs/2026-09-06-phase-0-completion.md) supersedes conflicting
historical snippets. The [master plan](plans/implementation-plan.md) defines product sequencing.
New plans and specifications live under `docs/superpowers/`.

## Global Constraints

Inherited verbatim from [implementation-plan.md](plans/implementation-plan.md#global-constraints). The
ones this plan touches directly:

- Money is `NUMERIC(20,4)`; FX rates are `NUMERIC(20,10)`; percentages `NUMERIC(9,4)` stored
  human-readable (`15.2500` = 15.25%).
- Money is `Decimal` in Python, `NUMERIC` in Postgres, and a **string** on the wire. In
  TypeScript it is formatted, never computed on — `parseFloat`/`Number()` on a money field is a
  lint failure.
- Business dates are `DATE`. Audit timestamps are `TIMESTAMPTZ`, UTC.
- Every household-scoped table carries `household_id` and has RLS **enabled**, from its first
  migration, even with one user.
- No secret is ever prefixed `NEXT_PUBLIC_`. `NEXT_PUBLIC_API_URL` is the only variable that carries
  the prefix, and it holds a URL.
- No real financial data in the repository, ever.
- Migrations are forward-only, applied by Flyway. No down-migrations. Never edit an applied
  migration — Flyway checksums them and will refuse to run.
- History anchor: **01 Jan 2024**. Reporting year: **calendar**.

**Verified environment facts** (checked 2026-09-05, do not re-derive):

- `docker` 29.6.2, `node` v24.15.0 (the active LTS line), `uv` and `python3.14` are installed. No
  managed service is used, so nothing else needs an account.
- The FX source `api.frankfurter.app` is **dead** (301 to nothing). The live endpoint is
  `https://api.frankfurter.dev/v1/`. [04](../design/multi-currency-and-fx.html) §3 names the old
  host; it is corrected here.
- Frankfurter omits non-publication days entirely and snaps a range start backward to the previous
  publication day. Requesting `2024-01-01..2024-01-05` returns `2023-12-29, 01-02, 01-03, 01-04,
  01-05`. Carry-forward is therefore mandatory, not optional.

---

### Task 1: Repository skeleton, tooling and secret hygiene

**Files:**
- Create: `.env.example`, `docker-compose.yml`
- Create: `db/migrations/.gitkeep`, `db/seeds/.gitkeep`, `db/functions/.gitkeep`, `db/tests/.gitkeep`
- Modify: `.gitignore` (already exists)
- Test: `scripts/check-no-real-data.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: the compose stack, with Postgres at
  `postgresql://oneledger:oneledger@127.0.0.1:5432/oneledger`. Every later task connects to that URL.

- [ ] **Step 1: Write the failing test**

```bash
# scripts/check-no-real-data.sh
#!/usr/bin/env bash
# Fails if any real statement is tracked by git. Invariant: no real financial data in the repo.
set -euo pipefail
tracked=$(git ls-files | grep -iE '\.pdf$|^pdf-statements/|^statements/' || true)
if [ -n "$tracked" ]; then
  echo "FAIL: real financial data is tracked by git:" >&2
  echo "$tracked" >&2
  exit 1
fi
echo "OK: no statements tracked"
```

- [ ] **Step 2: Run it to verify it passes on a clean tree, and fails when it should**

```bash
chmod +x scripts/check-no-real-data.sh
./scripts/check-no-real-data.sh                    # expect: OK
git add -f pdf-statements/transactions_january_2025.pdf
./scripts/check-no-real-data.sh                    # expect: FAIL, exit 1
git reset pdf-statements/transactions_january_2025.pdf
```

Expected: `OK` first, then `FAIL: real financial data is tracked by git:` and exit 1, then `OK`.

- [ ] **Step 3: Write the compose file and bring up the database**

```yaml
# docker-compose.yml — this file is the deployment (ADR-0014). The same compose file runs on a laptop and on the
# VPS; only .env and whether Caddy is enabled differ.
services:
  db:
    image: postgres:18
    environment:
      POSTGRES_USER: oneledger
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-oneledger}
      POSTGRES_DB: oneledger
    # Loopback only. ADR-0014 exposes Caddy and nothing else; a short-form mapping would
    # publish Postgres on every host interface, reachable the moment a firewall rule slips.
    # DB_PORT lets a scratch stack run beside the working one without a port clash.
    ports: ["127.0.0.1:${DB_PORT:-5432}:5432"]
    # 18+ stores data in a major-version subdirectory, so the mount goes one level up.
    # Mounting /var/lib/postgresql/data is the <=17 convention and the image refuses to
    # start on it.
    volumes: ["pgdata:/var/lib/postgresql"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U oneledger"]
      interval: 5s
      retries: 10

  api:
    build: ./api
    env_file: .env
    environment:
      # .env points at 127.0.0.1 so `uv run` works on the laptop. Inside this container that
      # is the container itself, so only the host changes — the password is not restated.
      POSTGRES_HOST: db
      POSTGRES_PORT: 5432
    # Loopback, for the same reason as db: on the VPS only Caddy is exposed, and it
    # reaches these over the compose network rather than the host interface.
    ports: ["127.0.0.1:8000:8000"]
    volumes: ["documents:/var/lib/oneledger/documents"]
    depends_on:
      db: { condition: service_healthy }

  web:
    build:
      context: ./web
      args:
        # Next.js inlines NEXT_PUBLIC_* at `next build` and freezes them into the bundle.
        # Supplying it at runtime is too late: the browser would fall back to localhost and
        # every hosted visitor would call their own machine instead of the API.
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8000}
    # Deliberately NOT env_file: .env. The web service renders and holds no secrets, and
    # handing it the service-wide file would give a compromised dependency SESSION_SECRET,
    # GOOGLE_CLIENT_SECRET, POSTGRES_PASSWORD and ACCOUNT_HASH_SALT — defeating the point
    # of the API being the authorization boundary (ADR-0013).
    environment:
      NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-http://localhost:8000}
    ports: ["127.0.0.1:3000:3000"]
    depends_on: [api]

  # Runs on demand, never at boot:  docker compose run --rm flyway migrate | info
  flyway:
    image: flyway/flyway:13.5.0-alpine
    profiles: ["tools"]
    environment:
      FLYWAY_URL: jdbc:postgresql://db:5432/oneledger
      FLYWAY_USER: oneledger
      FLYWAY_PASSWORD: ${POSTGRES_PASSWORD:-oneledger}
      FLYWAY_LOCATIONS: filesystem:/flyway/sql
      FLYWAY_CONNECT_RETRIES: "20"
    volumes: ["./db/migrations:/flyway/sql:ro"]
    depends_on:
      db: { condition: service_healthy }

volumes:
  pgdata:
  documents:
```

```bash
# .env first: api and web declare `env_file: .env`, and compose refuses to parse the file
# at all when it is missing — so even `up -d db` fails on a fresh clone without this.
cp .env.example .env

docker compose up -d db
docker compose exec db pg_isready -U oneledger      # expect: accepting connections
docker compose run --rm flyway info                 # expect: connects, "<< Empty Schema >>"
```

`api` and `web` have nothing to build yet — they arrive in Tasks 6 and 9. Bring up `db` alone for
now.

> **Prove Flyway reaches Postgres 18 before anything is built on it.** Flyway 13.5.0 was confirmed
> on 2026-09-05 to connect to Postgres 18.4 and read its schema, but a migration was never observed
> applying end to end. `flyway info` closes that gap in one command. If it fails, that is a reason
> to revisit [ADR-0015](../adr/0015-flyway-for-schema-migrations.html), not to work around it.

- [ ] **Step 4: Write `.env.example`**

The five household variables that once lived in this block (`HOUSEHOLD_NAME`,
`OWNER_DISPLAY_NAME`, `OWNER_TAX_RESIDENCY`, `BASE_CURRENCY`, `SECONDARY_CURRENCY`) moved into
the database in Phase 1 and are edited in Settings › Household.

```bash
# .env.example — committed. Copy to .env (git-ignored) and fill in.
# Public — the browser needs to know where the API is.
NEXT_PUBLIC_API_URL=http://localhost:8000

# Service only. NEVER prefixed NEXT_PUBLIC_. Never committed.
#
# The database connection lives here as PARTS, not as a URL. A DSN in this file would hold a
# second copy of the password, and two copies of one value drift: set the password without
# updating the URL and the containers come up fine while every local `uv run` fails to
# authenticate, which reads as a broken service rather than a typo.
#   Host-side DSN, when a shell needs one:   DATABASE_URL="$(scripts/dsn.sh)"
POSTGRES_USER=oneledger
POSTGRES_PASSWORD=oneledger
POSTGRES_DB=oneledger
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432

SESSION_SECRET=                 # openssl rand -hex 32 — signs the session cookie
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
# Where the service sends the browser after sign-in. The web app, not the API.
WEB_ORIGIN=http://localhost:3000

# The single permitted Google account. Any other is rejected at the app layer.
ALLOWED_USER_EMAIL=you@example.com

# Seeded into `household` and `person` by Task 3. The reporting pair is the one decision
# here that is expensive to change later — see multi-currency-and-fx.html §4.
HOUSEHOLD_NAME=Household
OWNER_DISPLAY_NAME=Owner
OWNER_TAX_RESIDENCY=AE
BASE_CURRENCY=INR
SECONDARY_CURRENCY=AED

# Per-installation salt for account-number hashing. Held outside the database.
# Rotating this invalidates every stored hash. See security-and-privacy.html §4.
ACCOUNT_HASH_SALT=

DOCUMENTS_DIR=/var/lib/oneledger/documents
```

- [ ] **Step 5: Add the pre-commit secret scan**

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.1
    hooks:
      - id: gitleaks
  - repo: local
    hooks:
      - id: no-real-data
        name: no real financial data tracked
        entry: scripts/check-no-real-data.sh
        language: system
        pass_filenames: false
```

- [ ] **Step 6: Verify the secret scan actually catches something**

```bash
pre-commit install
# a JWT-shaped value, because that is a rule gitleaks ships with — the point is to prove the
# scanner fires, not to plant a secret this project actually holds
printf 'LEAKED_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.fake\n' > /tmp/leak.env
cp /tmp/leak.env ./leak.env && git add leak.env
pre-commit run --all-files          # expect: gitleaks FAILS
git reset leak.env && rm leak.env
```

Expected: gitleaks reports a finding and exits non-zero. A secret scanner that has never caught a
planted key is a hypothesis, not a control.

- [ ] **Step 7: Commit**

```bash
git add .gitignore .env.example .pre-commit-config.yaml scripts/check-no-real-data.sh \
        docker-compose.yml db/
git commit -m "chore: repo skeleton, compose stack, secret scanning"
```

---

### Task 2: Migration 0001 — extensions and reference data

**Files:**
- Create: `db/migrations/V1__extensions_and_reference.sql`
- Create: `db/seeds/institutions.sql`
- Test: `db/tests/0001_reference.sql`

**Interfaces:**
- Consumes: a running local Postgres from Task 1.
- Produces: tables `currency(code CHAR(3) PK, name, symbol, decimals, number_format)` and
  `institution(id UUID PK, household_id, name, short_name, country, institution_type, ...)`. Later
  migrations reference `currency(code)`.

- [ ] **Step 1: Write the failing test**

```sql
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
```

- [ ] **Step 2: Run it to verify it fails**

```bash
psql "$DATABASE_URL" -f db/tests/0001_reference.sql
```

Expected: `ERROR: relation "currency" does not exist`.

- [ ] **Step 3: Write the migration**

```sql
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
```

- [ ] **Step 4: Write the institution seed**

**Suggestions, not a supported list.** `institution` is a table; any institution can be added from
the account form at any time, and nothing structural depends on these rows
([ADR-0012](../adr/0012-statement-layouts-as-data.html)). Seed only what is actually evidenced in the
gathered statements plus the remittance providers from Q11 — speculative seeds are noise in a
picker, and the user can delete every row here without consequence.

```sql
-- db/seeds/institutions.sql
INSERT INTO institution (household_id, name, short_name, country, institution_type) VALUES
  (NULL, 'Emirates NBD',   'ENBD',  'AE', 'bank'),
  (NULL, 'HDFC Bank',      'HDFC',  'IN', 'bank'),
  (NULL, 'Axis Bank',      'Axis',  'IN', 'bank'),
  (NULL, 'Wise',           'Wise',  'AE', 'exchange_house'),
  (NULL, 'Careem Pay',     'Careem','AE', 'exchange_house'),
  (NULL, 'ETMoney',        'ETM',   'IN', 'broker'),
  (NULL, 'Kuvera',         'Kuvera','IN', 'broker'),
  (NULL, 'CAMS',           'CAMS',  'IN', 'other'),
  (NULL, 'KFintech',       'KFin',  'IN', 'other');
```

- [ ] **Step 5: Apply and run the test to verify it passes**

```bash
docker compose run --rm flyway migrate
psql "$DATABASE_URL" -f db/seeds/institutions.sql
psql "$DATABASE_URL" -f db/tests/0001_reference.sql
```

Expected: `NOTICE: PASS 0001`.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/V1__extensions_and_reference.sql db/seeds/institutions.sql db/tests/0001_reference.sql
git commit -m "feat(db): extensions, currency and institution reference data"
```

---

### Task 3: Migration 0002 — identity, tenancy and RLS

**Files:**
- Create: `db/migrations/V2__identity_and_rls.sql`
- Test: `db/tests/0002_rls.sql`

**Interfaces:**
- Consumes: `institution` from Task 2.
- Produces: `household(id, name, base_currency)`, `person(id, household_id, ...)`,
  `app_user(id, household_id, person_id, email, role)`, and the SQL function
  `current_household_id() RETURNS UUID` used by every RLS policy from here on.

- [ ] **Step 1: Write the failing test**

```sql
-- db/tests/0002_rls.sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'person'),
    'RLS must be enabled on person';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE relname = 'app_user'),
    'RLS must be enabled on app_user';
  RAISE NOTICE 'PASS 0002 structure';
END $$;

-- Isolation. Two things this test MUST get right, both easy to get wrong:
--
--   1. Rows must exist first. Asserting count = 0 against an empty table passes whether or
--      not a policy exists, which is a test that proves nothing.
--   2. SET LOCAL ROLE only lasts for the enclosing transaction. psql runs in autocommit, so
--      a bare SET LOCAL is discarded immediately and the assertions below would run as the
--      table owner -- who bypasses RLS anyway. The explicit BEGIN is load-bearing.
BEGIN;
  -- Seed a household that WOULD be visible if the policy were missing or permissive.
  INSERT INTO household (id, name, base_currency, secondary_currency) VALUES
    ('11111111-1111-1111-1111-111111111111', 'rls fixture', 'INR', 'AED');
  INSERT INTO person (id, household_id, display_name) VALUES
    ('22222222-2222-2222-2222-222222222222',
     '11111111-1111-1111-1111-111111111111', 'rls fixture');
  INSERT INTO app_user (id, household_id, person_id, email) VALUES
    ('33333333-3333-3333-3333-333333333333',
     '11111111-1111-1111-1111-111111111111',
     '22222222-2222-2222-2222-222222222222', 'fixture@example.test');

  -- Prove the fixture is actually there, as owner.
  DO $$
  BEGIN
    ASSERT (SELECT count(*) FROM person) >= 1, 'fixture must exist before the isolation check';
  END $$;

  SET LOCAL ROLE anon;
  DO $$
  BEGIN
    -- app.user_id is unset here, so current_household_id() is NULL, so every predicate is
    -- NULL -> no rows. If any of these returns > 0, a policy is missing or permissive.
    ASSERT (SELECT count(*) FROM household) = 0, 'anon must not read household';
    ASSERT (SELECT count(*) FROM person)    = 0, 'anon must not read person';
    ASSERT (SELECT count(*) FROM app_user)  = 0, 'anon must not read app_user';
    -- But the shared seed set must remain readable, or M1 has no institution picker.
    ASSERT (SELECT count(*) FROM institution WHERE household_id IS NULL) > 0,
      'system seed institutions must stay visible';
    RAISE NOTICE 'PASS 0002 isolation';
  END $$;
  RESET ROLE;
ROLLBACK;   -- the fixture is a test artefact and must not survive
```

> This test is the reason Phase 0 exists before feature work. An isolation check that passes
> against an empty table is the database equivalent of a backup that has never been restored.

- [ ] **Step 2: Run it to verify it fails**

```bash
psql "$DATABASE_URL" -f db/tests/0002_rls.sql
```

Expected: `ERROR: relation "person" does not exist`.

- [ ] **Step 3: Write the migration**

```sql
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
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm flyway migrate
psql "$DATABASE_URL" -f db/tests/0002_rls.sql
```

Expected: `NOTICE: PASS 0002 structure` then `NOTICE: PASS 0002 isolation`.

- [ ] **Step 4b: Prove the test can fail**

A test that has never failed is a hypothesis. Break the policy deliberately and confirm the test
catches it:

```bash
psql "$DATABASE_URL" -c "ALTER POLICY household_isolation ON person USING (true);"
psql "$DATABASE_URL" -f db/tests/0002_rls.sql    # expect: ASSERT failure on 'anon must not read person'
psql "$DATABASE_URL" -c "ALTER POLICY household_isolation ON person USING (household_id = current_household_id());"
psql "$DATABASE_URL" -f db/tests/0002_rls.sql    # expect: PASS
```

Expected: a failed assertion, then a pass. If the permissive policy still passes, the test is not
running as `anon` — check that the `BEGIN` is present, since `SET LOCAL ROLE` outside a transaction
is silently discarded.

- [ ] **Step 4c: Seed the household, person and app_user row**

```bash
./scripts/seed-household.sh
```

The script reads `.env` and derives its DSN from `scripts/dsn.sh`, so the password is not
restated. It is deliberately not idempotent — running it twice creates a second household,
which the app has no concept of. Run it once per database.


The seed reads `.env` rather than naming anyone. Nothing about the person who runs this is
written into a document that gets committed.

**It lives here, not with OIDC, for a dependency reason.** Task 8's `reporting_config()` reads
the household's reporting pair; with no row it unpacks `None` and raises. Seeding at the end of
identity is the earliest point the row can exist, and it needs nothing from Google.

Expected: one row.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/V2__identity_and_rls.sql db/tests/0002_rls.sql
git commit -m "feat(db): household identity and row-level security"
```

---

### Task 4: Migration 0003 — accounts

**Files:**
- Create: `db/migrations/V3__accounts.sql`
- Test: `db/tests/0003_accounts.sql`

**Interfaces:**
- Consumes: `household`, `person`, `institution`, `currency`, `current_household_id()`.
- Produces: enums `account_type`, `account_class`; table `account`; trigger
  `set_account_class()` deriving `account_class` from `account_type` on every insert and update.

- [ ] **Step 1: Write the failing test**

```sql
-- db/tests/0003_accounts.sql
\set ON_ERROR_STOP on
DO $$
DECLARE h UUID; p UUID; a_card UUID; a_sav UUID; cls account_class;
BEGIN
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('test', 'INR', 'AED') RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date, credit_limit)
    VALUES (h,p,'Test Card','credit_card','AED','AE',-4500,'2024-01-01',20000)
    RETURNING id INTO a_card;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Test Savings','savings','INR','IN',100000,'2024-01-01')
    RETURNING id INTO a_sav;

  SELECT account_class INTO cls FROM account WHERE id = a_card;
  ASSERT cls = 'liability', 'credit_card must derive account_class = liability';
  SELECT account_class INTO cls FROM account WHERE id = a_sav;
  ASSERT cls = 'asset',     'savings must derive account_class = asset';

  -- account_class is never set by hand: a wrong explicit value is corrected, not accepted
  UPDATE account SET account_class = 'asset' WHERE id = a_card;
  SELECT account_class INTO cls FROM account WHERE id = a_card;
  ASSERT cls = 'liability', 'trigger must overwrite a hand-set account_class';

  -- is_liquid defaults on (Q22): everything counts unless explicitly de-selected
  ASSERT (SELECT is_liquid FROM account WHERE id = a_card), 'is_liquid must default true';

  RAISE NOTICE 'PASS 0003';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
```

- [ ] **Step 2: Run it to verify it fails**

```bash
psql "$DATABASE_URL" -f db/tests/0003_accounts.sql
```

Expected: `ERROR: type "account_class" does not exist`.

- [ ] **Step 3: Write the migration**

```sql
-- db/migrations/V3__accounts.sql
CREATE TYPE account_type AS ENUM (
  'cash','current','savings','salary','fixed_deposit','recurring_deposit',
  'credit_card',
  'personal_loan','home_loan','auto_loan','education_loan','gold_loan','loan_other',
  'brokerage','mutual_fund_folio','retirement',
  'property','precious_metal','crypto','other_asset',
  'receivable','payable'
);

CREATE TYPE account_class AS ENUM ('asset','liability');

CREATE TABLE account (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  owner_person_id       UUID NOT NULL REFERENCES person(id),
  joint_owner_person_id UUID REFERENCES person(id),   -- unused in Phase 1; spouse is Phase 2
  institution_id        UUID REFERENCES institution(id),

  name                  TEXT NOT NULL,
  account_type          account_type NOT NULL,
  account_class         account_class NOT NULL DEFAULT 'asset',   -- set by trigger, never by hand
  account_subtype       TEXT,
  currency              CHAR(3) NOT NULL REFERENCES currency(code),
  country               CHAR(2) NOT NULL,
  tax_residency_context CHAR(2),

  account_number_masked TEXT,
  account_number_hash   TEXT,
  iban_masked           TEXT,

  opened_on             DATE,
  closed_on             DATE,
  is_active             BOOLEAN NOT NULL DEFAULT true,
  include_in_net_worth  BOOLEAN NOT NULL DEFAULT true,
  is_liquid             BOOLEAN NOT NULL DEFAULT true,   -- Q22: default on, user de-selects

  opening_balance       NUMERIC(20,4) NOT NULL DEFAULT 0,
  opening_balance_date  DATE NOT NULL,
  current_balance       NUMERIC(20,4) NOT NULL DEFAULT 0,
  current_balance_as_of TIMESTAMPTZ,

  credit_limit          NUMERIC(20,4),
  statement_day         SMALLINT CHECK (statement_day BETWEEN 1 AND 31),
  payment_due_day       SMALLINT CHECK (payment_due_day BETWEEN 1 AND 31),

  interest_rate_pct     NUMERIC(9,4),
  maturity_date         DATE,

  display_order         SMALLINT NOT NULL DEFAULT 0,
  color                 TEXT,
  notes                 TEXT,
  tags                  TEXT[],
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT closed_after_opened
    CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on >= opened_on),
  CONSTRAINT card_has_limit
    CHECK (account_type <> 'credit_card' OR credit_limit IS NOT NULL)
);

-- account_class is derivable from account_type but stored, because net-worth queries filter on
-- it constantly. The trigger is what keeps the denormalisation honest.
CREATE FUNCTION set_account_class() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.account_class := CASE
    WHEN NEW.account_type IN ('credit_card','personal_loan','home_loan','auto_loan',
                              'education_loan','gold_loan','loan_other','payable')
    THEN 'liability'::account_class
    ELSE 'asset'::account_class
  END;
  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_class
  BEFORE INSERT OR UPDATE ON account
  FOR EACH ROW EXECUTE FUNCTION set_account_class();

CREATE INDEX idx_account_household ON account (household_id, is_active);
CREATE UNIQUE INDEX idx_account_number_hash
  ON account (household_id, account_number_hash) WHERE account_number_hash IS NOT NULL;

ALTER TABLE account ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON account USING (household_id = current_household_id());
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm flyway migrate
psql "$DATABASE_URL" -f db/tests/0003_accounts.sql
```

Expected: `NOTICE: PASS 0003`, then the deliberate `rollback test data` exception is swallowed.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/V3__accounts.sql db/tests/0003_accounts.sql
git commit -m "feat(db): accounts with derived account_class"
```

---

### Task 5: Migration 0004 — FX rates

**Files:**
- Create: `db/migrations/V4__fx_rates.sql`
- Test: `db/tests/0004_fx.sql`

**Interfaces:**
- Consumes: `currency` from Task 2.
- Produces: table `fx_rate(rate_date, base_currency, quote_currency, rate, source,
  is_carried_forward, carried_from, fetched_at)` with PK `(rate_date, base_currency,
  quote_currency)`, and `fx_rate_on(base, quote, on_date) RETURNS NUMERIC(20,10)`. Task 9 populates
  it; every later milestone reads it.

- [ ] **Step 1: Write the failing test**

```sql
-- db/tests/0004_fx.sql
\set ON_ERROR_STOP on

-- Fixture dates are 1900, deliberately. The backfill anchor is 01 Jan 2024 (ADR-0011) and a
-- rate before that has no meaning, so nothing this system stores can ever collide with these
-- rows. Using 2024 dates would work exactly once: the moment PR 5 backfills daily AED/INR
-- rates, the insert below raises unique_violation before reaching a single assertion, and the
-- pre-push hook then fails on every push. A test that only passes against an empty table is
-- not a test of a table that fills up.
DO $$
BEGIN
  INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
    VALUES ('1900-01-05','AED','INR',22.6900000000,'derived_peg');

  ASSERT fx_rate_on('AED','INR','1900-01-05') = 22.6900000000, 'exact date lookup';

  -- a carried-forward rate must declare where it came from
  BEGIN
    INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source, is_carried_forward)
      VALUES ('1900-01-06','AED','INR',22.69,'derived_peg',true);
    ASSERT false, 'carried rate without carried_from must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  -- rates must be positive
  BEGIN
    INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
      VALUES ('1900-01-07','AED','INR',0,'manual');
    ASSERT false, 'zero rate must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  RAISE NOTICE 'PASS 0004';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
```

- [ ] **Step 2: Run it to verify it fails**

```bash
psql "$DATABASE_URL" -f db/tests/0004_fx.sql
```

Expected: `ERROR: relation "fx_rate" does not exist`.

- [ ] **Step 3: Write the migration**

```sql
-- db/migrations/V4__fx_rates.sql
-- Global, not household-scoped: exchange rates are public data. But "public" means readable,
-- not writable. RLS is defence in depth here: if the service ever connects as a role other than
-- the owner, an unscoped connection must not be able to rewrite the historical rate table and
-- silently corrupt every balance conversion.
CREATE TABLE fx_rate (
  rate_date          DATE    NOT NULL,
  base_currency      CHAR(3) NOT NULL REFERENCES currency(code),
  quote_currency     CHAR(3) NOT NULL REFERENCES currency(code),
  rate               NUMERIC(20,10) NOT NULL,     -- 1 base = <rate> quote
  source             TEXT NOT NULL
    CHECK (source IN ('ecb','derived_peg','manual','actual_transfer')),
  is_carried_forward BOOLEAN NOT NULL DEFAULT false,
  carried_from       DATE,
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (rate_date, base_currency, quote_currency),
  CONSTRAINT rate_positive     CHECK (rate > 0),
  CONSTRAINT carried_has_source CHECK (NOT is_carried_forward OR carried_from IS NOT NULL),
  CONSTRAINT no_self_pair       CHECK (base_currency <> quote_currency)
);

CREATE INDEX idx_fx_lookup ON fx_rate (base_currency, quote_currency, rate_date DESC);

-- Exact date only. Carry-forward is materialised as real rows at backfill time, not resolved
-- here, so that every transaction's fx_rate_is_carried flag reflects a stored fact
-- (Invariant I7) rather than a query-time inference.
CREATE FUNCTION fx_rate_on(p_base CHAR(3), p_quote CHAR(3), p_date DATE)
  RETURNS NUMERIC(20,10) LANGUAGE sql STABLE
AS $$
  SELECT rate FROM fx_rate
  WHERE base_currency = p_base AND quote_currency = p_quote AND rate_date = p_date
$$;

-- Read-only to every role. No INSERT/UPDATE/DELETE policy exists, so writes are denied and
-- must go through upsert_fx_rates(), which authorizes independently.
ALTER TABLE fx_rate  ENABLE ROW LEVEL SECURITY;
ALTER TABLE currency ENABLE ROW LEVEL SECURITY;
CREATE POLICY reference_read ON fx_rate  FOR SELECT TO authenticated USING (true);
CREATE POLICY reference_read ON currency FOR SELECT TO authenticated USING (true);
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
docker compose run --rm flyway migrate
psql "$DATABASE_URL" -f db/tests/0004_fx.sql
```

Expected: `NOTICE: PASS 0004`.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/V4__fx_rates.sql db/tests/0004_fx.sql
git commit -m "feat(db): fx_rate with provenance and carry-forward constraints"
```

---

### Task 6: Python service skeleton

**Files:**
- Create: `api/pyproject.toml`, `api/Dockerfile`, `api/oneledger/__init__.py`,
  `api/oneledger/main.py`, `api/oneledger/auth.py`, `api/oneledger/db.py`
- Test: `api/tests/test_health.py`, `api/tests/test_auth.py`

**Interfaces:**
- Consumes: the `db` service from Task 1; `app_user` from Task 3.
- Produces: a FastAPI app on `:8000`; the dependency `require_owner()` returning the caller's
  `household_id`, which **every** route from here on depends on;
  `resolve_owner(claims, lookup, allowed_email)`,
  the pure function it wraps, which Task 10 reuses; `connect()` for Postgres access.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_auth.py
import pytest

from oneledger.auth import NotAuthorized, resolve_owner


def test_rejects_a_token_with_no_matching_app_user() -> None:
    """Google authenticates any Google account. Authentication is not authorization."""
    with pytest.raises(NotAuthorized):
        resolve_owner(
            claims={"sub": "00000000-0000-0000-0000-000000000000", "email": "stranger@example.com"},
            lookup=lambda uid: None,
            allowed_email="owner@example.com",
        )


def test_rejects_a_missing_subject_rather_than_defaulting_open() -> None:
    with pytest.raises(NotAuthorized):
        resolve_owner(
            claims={}, lookup=lambda uid: "household-1", allowed_email="owner@example.com"
        )


def test_accepts_a_token_that_resolves_to_an_app_user() -> None:
    household = resolve_owner(
        claims={"sub": "11111111-1111-1111-1111-111111111111", "email": "owner@example.com"},
        lookup=lambda uid: "household-1",
        allowed_email="owner@example.com",
    )
    assert household == "household-1"
```

```python
# api/tests/test_health.py
"""The authorization boundary, as HTTP sees it.

`test_auth.py` covers `resolve_owner` as a function. These cover the same rules through the
app, because a dependency that is correct in isolation and unwired in `main.py` fails exactly
the same way as one that is wrong — and only this test can tell the difference.
"""

import pytest
from fastapi.testclient import TestClient

from oneledger.main import app

client = TestClient(app)


def test_health_needs_no_session() -> None:
    """The one route that is deliberately open. There is no second."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_no_session_is_401_not_403() -> None:
    """Absent credentials and rejected credentials are different answers.

    A service that returns 403 for both tells a caller their account is not permitted when in
    fact they never presented one, which sends them to the wrong fix.
    """
    assert client.get("/whoami").status_code == 401


def test_a_forged_session_is_403() -> None:
    """Google authenticates any Google account. Authentication is not authorization."""
    forged = TestClient(app)
    forged.cookies.set("session", "not-a-real-token")
    assert forged.get("/whoami").status_code == 403


@pytest.mark.parametrize("route", ["/whoami"])
def test_every_route_but_health_requires_the_owner(route: str) -> None:
    """A route added without `Depends(require_owner)` is the failure this catches.

    Extend the parameter list as routes land; a route that answers anonymously will fail here
    rather than in production.
    """
    assert client.get(route).status_code in (401, 403)
```

```python
"""Environment these tests need before oneledger.main is imported.

The service refuses to start without SESSION_SECRET, deliberately — so the suite has to supply
one. It is a throwaway value: nothing here signs anything a real client will ever present.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret-not-used-outside-this-suite")
os.environ.setdefault("ALLOWED_USER_EMAIL", "owner@example.com")
```

`test_health.py` covers the same rules through the app that `test_auth.py` covers as a
function, because a dependency that is correct in isolation and unwired in `main.py` fails
identically to one that is wrong — and only the HTTP test tells them apart. `conftest.py`
supplies the session secret the service now refuses to start without.

- [ ] **Step 2: Run it to verify it fails**

```bash
cd api && uv run pytest tests/test_auth.py -v
```

Expected: FAIL — `No module named 'oneledger.auth'`.

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "oneledger"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
  "fastapi>=0.141", "uvicorn[standard]>=0.52", "pydantic>=2.13",
  "psycopg[binary]>=3.3", "httpx>=0.28", "pdfplumber>=0.11.10", "pyjwt[crypto]>=2.13",
]

[dependency-groups]
dev = [
    "pytest>=9",
    "mypy>=2.3",
    "ruff>=0.16",
    "hypothesis>=6.167",
    "httpx>=0.28.1",
]

[tool.mypy]
strict = true

[tool.ruff]
# 88 is ruff's default, not a decision anyone made here. The specification's code and this
# repository's prose are both written to ~96, so the limit follows the code rather than the
# code being rewrapped to satisfy a default.
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ANN"]

# Say which package to ship. Hatchling's heuristics do not find `oneledger` on their own and
# fail the build rather than guessing.
[tool.hatch.build.targets.wheel]
packages = ["oneledger"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 4: Write the authorization layer**

```python
# api/oneledger/auth.py
"""Authorization. Since ADR-0013 this is the boundary, not RLS.

Google authenticates any Google account, so a valid sign-in proves identity and nothing else.
A caller is authorized only if their session resolves to a row in app_user.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import jwt
from fastapi import Cookie, HTTPException

from .db import connect


class NotAuthorized(Exception):
    pass


def resolve_owner(
    claims: dict[str, Any],
    lookup: Callable[[str], str | None],
    allowed_email: str | None = None,
) -> str:
    """Fails closed: a missing subject, an unknown subject, or the wrong address is a rejection."""
    subject = claims.get("sub")
    if not subject:
        raise NotAuthorized("no subject in token")

    # Passed in, not read from the environment: a function that reaches for os.environ is a
    # function whose tests need monkeypatching to say anything. claims_from_id_token already
    # takes `allowed` this way; this matches it.
    allowed = (allowed_email or "").strip().lower()
    email = (claims.get("email") or "").strip().lower()
    if not allowed or email != allowed:
        raise NotAuthorized("address is not the permitted account")

    household_id = lookup(subject)
    if household_id is None:
        raise NotAuthorized("no app_user for this subject")
    return household_id


def _lookup(subject: str) -> str | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT household_id FROM app_user WHERE id = %s", (subject,))
        row = cur.fetchone()
    return str(row[0]) if row else None


def session_secret() -> str:
    """The key that signs session cookies. Absent is a configuration failure, not a request one.

    Reading os.environ["SESSION_SECRET"] inline raised KeyError from inside require_owner's
    try block, where the except clause does not catch it — so a service deployed without the
    variable answered every request with a 500 and no explanation. main.py calls this at
    startup so the process refuses to run instead.
    """
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET is not set: sessions cannot be verified without it. "
            "Copy .env.example to .env and generate one with `openssl rand -hex 32`."
        )
    return secret


def require_owner(session: str = Cookie(default="")) -> str:
    """FastAPI dependency. Every route depends on this — there is no unauthenticated route.

    The cookie is issued by this service after Google OIDC (Task 10), signed with SESSION_SECRET.
    """
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    try:
        claims = jwt.decode(session, session_secret(), algorithms=["HS256"])
        return resolve_owner(claims, _lookup, os.environ.get("ALLOWED_USER_EMAIL"))
    except (jwt.PyJWTError, NotAuthorized) as exc:
        raise HTTPException(status_code=403, detail="not authorized") from exc
```

- [ ] **Step 5: Write the database helper and the app**

```python
# api/oneledger/db.py
from __future__ import annotations

import os
from urllib.parse import quote

import psycopg


def _user() -> str:
    return os.environ.get("POSTGRES_USER", "oneledger")


def _password() -> str:
    try:
        return os.environ["POSTGRES_PASSWORD"]
    except KeyError:
        raise SystemExit("POSTGRES_PASSWORD is not set. Copy .env.example to .env.") from None


def dsn() -> str:
    """The connection as a URI, for anything that needs a string.

    Percent-encoded, matching scripts/dsn.sh. A password is invited to be real, and `@`, `/`,
    `:` and `#` are all legal in one and all structural in a URI — interpolating them raw
    changes how libpq parses the string and authentication fails for no visible reason.
    """
    return (
        f"postgresql://{quote(_user(), safe='')}:{quote(_password(), safe='')}"
        f"@{os.environ.get('POSTGRES_HOST', '127.0.0.1')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('POSTGRES_DB', 'oneledger')}"
    )


def connect() -> psycopg.Connection:
    """Connect with keyword parameters rather than a URI.

    Nothing is parsed, so no character in a password can change the meaning of the
    connection. dsn() exists for the places that genuinely need a string.
    """
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "oneledger"),
        user=_user(),
        password=_password(),
    )
```

```python
# api/oneledger/main.py
import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import require_owner, session_secret
from .routes import fx

# Fail at startup, not on the first request. A service that boots without the key that signs
# its sessions is broken in a way every caller will experience as an unexplained 500.
session_secret()

# /docs, /redoc and /openapi.json are registered by default and depend on nothing, which
# would make three unauthenticated routes rather than the one the boundary allows —
# /openapi.json alone hands an anonymous caller the entire route surface. Generating the
# client's types does not need them served: `scripts/openapi.py` writes the schema to a file.
app = FastAPI(
    title="OneLedger",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# The web app is served from :3000 and calls this service on :8000 — a different origin,
# because the port differs. Without this the browser blocks every response, and because
# apiFetch sends credentials the origin must be named explicitly: "*" is rejected outright
# for credentialed requests. WEB_ORIGIN is the same variable the OIDC callback redirects to.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("WEB_ORIGIN", "http://localhost:3000")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)


app.include_router(fx.router)


@app.get("/health")
def health() -> dict[str, str]:
    """The only route that does not require authorization."""
    return {"status": "ok"}


@app.get("/whoami")
def whoami(household_id: str = Depends(require_owner)) -> dict[str, str]:
    return {"household_id": household_id}
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd api && uv sync && uv run pytest -v && uv run ruff check . && uv run mypy .
```

Expected: 7 passed; ruff and mypy clean.

- [ ] **Step 7: Verify the service runs and rejects an anonymous caller**

```bash
# From the repository root. Neither Python nor `uv run` reads .env, and the service now
# refuses to start without SESSION_SECRET — so loading it is the first step, not an
# afterthought. Run this without it and you get the RuntimeError, which is correct but
# is not what you were trying to test.
. scripts/load-env.sh
uv run --project api uvicorn oneledger.main:app --reload --port 8000 &

curl -s localhost:8000/health                                    # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/whoami   # 401
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/whoami \
  -H "Cookie: session=not-a-real-token"                           # 403

# /health is the only route that answers without a session. Prove it.
for r in /docs /redoc /openapi.json; do
  curl -s -o /dev/null -w "$r %{http_code}\n" "localhost:8000$r"  # 404 each
done
```

Expected: `ok`, then `401`, then `403`, then three `404`s.

> **`SESSION_SECRET` must be a real value before this works.** `.env.example` ships it empty
> with the command that fills it: `openssl rand -hex 32`.

- [ ] **Step 7b: Write the Dockerfile**

`docker-compose.yml` has said `build: ./api` since Task 1. Nothing has built it yet.

```dockerfile
# api/Dockerfile
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first, so a source change does not re-resolve them. --no-install-project is
# load-bearing: the package directory does not exist at this layer, and without it hatchling
# fails with "Unable to determine which files to ship inside the wheel".
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY oneledger ./oneledger
RUN uv sync --frozen --no-dev
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "oneledger.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```bash
docker compose build api && docker compose up -d api
curl -s localhost:8000/health          # {"status":"ok"} — from the container this time
```

- [ ] **Step 8: Commit**

```bash
git add api/
git commit -m "feat(api): FastAPI skeleton with JWT authorization"
```

---

### Task 7: FX — ECB via Frankfurter, with peg derivation

**Files:**
- Create: `api/oneledger/fx.py`
- Test: `api/tests/test_fx.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Pegs are passed in, read from `currency` by the caller.
- Produces: `Peg`; `units_per_eur(published, code, pegs) -> Decimal`;
  `cross_rate(published, base, quote, pegs) -> Decimal`;
  `EcbFrankfurterSource.fetch_range(start, end, codes) -> dict[date, dict[str, Decimal]]`;
  `carry_forward(published, start, end) -> list[tuple[date, T, date | None]]`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_fx.py
from datetime import date
from decimal import Decimal

import pytest

from oneledger.fx import Peg, carry_forward, cross_rate, terminal_anchor

# What the `currency` table holds for the first household: one peg, held since 1997.
PEGS = {"AED": Peg(anchor="USD", units_per_anchor=Decimal("3.6725"))}


def test_cross_rate_matches_published_ecb_values() -> None:
    """ECB 2024-01-02: EUR/INR 91.285, EUR/USD 1.0956. Verified against the live API."""
    published = {"INR": Decimal("91.285"), "USD": Decimal("1.0956")}
    usd_inr = cross_rate(published, "USD", "INR", PEGS)
    assert usd_inr.quantize(Decimal("0.0001")) == Decimal("83.3196")


def test_a_pegged_currency_needs_no_published_rate() -> None:
    """The ECB publishes no AED, which is exactly why the peg derivation exists."""
    published = {"INR": Decimal("91.285"), "USD": Decimal("1.0956")}
    aed_inr = cross_rate(published, "AED", "INR", PEGS)
    usd_inr = cross_rate(published, "USD", "INR", PEGS)
    # the peg identity must hold to 8 dp — test 2 of multi-currency-and-fx.html §9
    assert (aed_inr * PEGS["AED"].units_per_anchor).quantize(
        Decimal("0.00000001")
    ) == usd_inr.quantize(Decimal("0.00000001"))


def test_an_unpublished_unpegged_currency_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="neither published nor pegged"):
        cross_rate({"INR": Decimal("91.285")}, "BRL", "INR", PEGS)


def test_carry_forward_fills_gaps_and_never_interpolates() -> None:
    """A Sunday uses Friday's rate, flagged — not the average of Friday and Monday."""
    published = {date(2024, 1, 5): Decimal("83.00"), date(2024, 1, 8): Decimal("84.00")}
    filled = carry_forward(published, date(2024, 1, 5), date(2024, 1, 8))
    assert [(d.isoformat(), str(v), c.isoformat() if c else None) for d, v, c in filled] == [
        ("2024-01-05", "83.00", None),
        ("2024-01-06", "83.00", "2024-01-05"),
        ("2024-01-07", "83.00", "2024-01-05"),
        ("2024-01-08", "84.00", None),
    ]


def test_carry_forward_refuses_to_start_on_a_gap() -> None:
    with pytest.raises(ValueError, match="no published rate at or before"):
        carry_forward({date(2024, 1, 8): Decimal("84.00")}, date(2024, 1, 5), date(2024, 1, 8))


def test_eur_is_present_even_though_it_is_never_a_quoted_symbol() -> None:
    """Frankfurter quotes against EUR, so EUR is absent from its own rates map.

    A household reporting in EUR, or holding it, would otherwise hit "neither published nor
    pegged" for the currency the feed is denominated in.
    """
    published = {"EUR": Decimal(1), "INR": Decimal("91.285")}
    assert cross_rate(published, "EUR", "INR", {}) == Decimal("91.285")


def test_a_cross_through_a_pegged_quote_is_derived_not_published() -> None:
    """Provenance follows either side of the cross, not just the base.

    Reporting in a pegged currency makes every rate derived, however the base was obtained.
    """
    from oneledger.routes.fx import to_rows

    published = {date(2024, 1, 2): {"EUR": Decimal(1), "USD": Decimal("1.0956")}}
    rows = to_rows(
        published, date(2024, 1, 2), date(2024, 1, 2), quote="AED", bases=["USD"], pegs=PEGS
    )
    assert {r["source"] for r in rows} == {"derived_peg"}


def test_a_peg_chain_resolves_to_the_currency_the_feed_publishes() -> None:
    """A peg can point at another peg. Only the end of the chain is ever quoted."""
    chain = {
        "XAA": Peg(anchor="XBB", units_per_anchor=Decimal(2)),
        "XBB": Peg(anchor="USD", units_per_anchor=Decimal(3)),
    }
    assert terminal_anchor("XAA", chain) == "USD"
    assert terminal_anchor("USD", chain) == "USD"


def test_a_peg_cycle_raises_rather_than_looping() -> None:
    cycle = {
        "XAA": Peg(anchor="XBB", units_per_anchor=Decimal(2)),
        "XBB": Peg(anchor="XAA", units_per_anchor=Decimal(3)),
    }
    with pytest.raises(ValueError, match="peg cycle"):
        terminal_anchor("XAA", cycle)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd api && uv run pytest tests/test_fx.py -v
```

Expected: FAIL — `No module named 'oneledger.fx'`.

- [ ] **Step 3: Write the implementation**

```python
# api/oneledger/fx.py
"""Exchange rates: fetch, peg derivation, gap filling.

No currency code is hardcoded here. The ECB quotes everything against EUR, so any pair is a
division; a currency the ECB does not publish is reached through its peg, which is a row in
`currency`. See multi-currency-and-fx.html §3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import httpx

# The FX doc names api.frankfurter.app; that host now 301s to nothing. Verified live 2026-09-05.
FRANKFURTER = "https://api.frankfurter.dev/v1"


@dataclass(frozen=True)
class Peg:
    """`units_per_anchor` of this currency buy 1 `anchor`. From currency.pegged_to/peg_rate."""

    anchor: str
    units_per_anchor: Decimal


def units_per_eur(published: Mapping[str, Decimal], code: str, pegs: Mapping[str, Peg]) -> Decimal:
    """How many units of `code` buy 1 EUR, following pegs for currencies the ECB skips."""
    seen: set[str] = set()
    factor = Decimal(1)
    while code not in published:
        if code in seen:
            raise ValueError(f"peg cycle through {code}")
        seen.add(code)
        peg = pegs.get(code)
        if peg is None:
            raise ValueError(f"{code} is neither published nor pegged")
        factor *= peg.units_per_anchor
        code = peg.anchor
    return published[code] * factor


def anchor_chain(code: str, pegs: Mapping[str, Peg]) -> list[str]:
    """`code` and every currency its peg passes through, ending at one a feed publishes.

    A peg can point at another peg — A pegged to B, B pegged to USD — and only USD is ever
    published. Both callers need the whole chain, for different reasons: the fetch would
    otherwise ask for B and never USD, and the stored rows would carry A and B but no USD,
    leaving the B->USD peg identity with nothing to check itself against.
    """
    chain = [code]
    while code in pegs:
        code = pegs[code].anchor
        if code in chain:
            raise ValueError(f"peg cycle through {code}")
        chain.append(code)
    return chain


def terminal_anchor(code: str, pegs: Mapping[str, Peg]) -> str:
    """The currency a feed will actually quote for `code`."""
    return anchor_chain(code, pegs)[-1]


def cross_rate(
    published: Mapping[str, Decimal], base: str, quote: str, pegs: Mapping[str, Peg]
) -> Decimal:
    """1 `base` = <returned> `quote`. Rates derived through a peg are stored as 'derived_peg'."""
    return units_per_eur(published, quote, pegs) / units_per_eur(published, base, pegs)


def carry_forward[T](
    published: dict[date, T], start: date, end: date
) -> list[tuple[date, T, date | None]]:
    """Fill every calendar date from the last published value.

    Forward-fill, never interpolate: interpolating between Friday and Monday would fabricate a
    Saturday rate that never existed, and would make a Saturday transaction's base-currency
    value depend on what happened the following Monday.
    """
    prior = [d for d in sorted(published) if d <= start]
    if not prior:
        raise ValueError(f"no published rate at or before {start.isoformat()}")

    source_date = prior[-1]
    value = published[source_date]

    out: list[tuple[date, T, date | None]] = []
    day = start
    while day <= end:
        if day in published:
            source_date, value = day, published[day]
            out.append((day, value, None))
        else:
            out.append((day, value, source_date))
        day += timedelta(days=1)
    return out


class EcbFrankfurterSource:
    """ECB daily reference rates. No key, no account, no rate limit."""

    name = "ecb"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30.0)

    def fetch_range(
        self, start: date, end: date, codes: list[str]
    ) -> dict[date, dict[str, Decimal]]:
        """Return {published_date: {code: units_per_eur}}. Unpublished days are absent, not filled.

        `codes` is what the household needs quoted — its reporting pair plus every currency its
        accounts hold, minus the pegged ones, which are derived rather than fetched.
        """
        url = f"{FRANKFURTER}/{start.isoformat()}..{end.isoformat()}"
        response = self._client.get(url, params={"base": "EUR", "symbols": ",".join(codes)})
        response.raise_for_status()

        return {
            date.fromisoformat(day): {
                # The base is never among the quoted symbols, so a household reporting in EUR
                # or holding it would hit "neither published nor pegged" for a currency the
                # feed is denominated in. One EUR is one EUR; state it.
                "EUR": Decimal(1),
                # str() first: json produces floats, and Decimal(float) inherits the binary
                # rounding error. This is the most common way float creeps into money code.
                **{code: Decimal(str(value)) for code, value in rates.items()},
            }
            for day, rates in response.json()["rates"].items()
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd api && uv run pytest tests/test_fx.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add api/oneledger/fx.py api/tests/test_fx.py
git commit -m "feat(api): ECB rate source, peg derivation and carry-forward"
```

---

### Task 8: `POST /fx/backfill` and the 36-month backfill

**Files:**
- Create: `api/oneledger/routes/fx.py`
- Modify: `api/oneledger/main.py`, `api/oneledger/db.py`
- Test: `api/tests/test_fx_rows.py`

**Interfaces:**
- Consumes: `EcbFrankfurterSource`, `carry_forward` (Task 7); `require_owner` (Task 6);
  `fx_rate` (Task 5).
- Produces: `upsert_rates(conn, rows) -> int`; the routes `POST /fx/backfill` and
  `GET /fx/{date}`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_fx_rows.py
from __future__ import annotations

from datetime import date
from decimal import Decimal

from oneledger.fx import Peg
from oneledger.routes.fx import reporting_config, to_rows

PEGS = {"AED": Peg(anchor="USD", units_per_anchor=Decimal("3.6725"))}


def test_rows_carry_provenance() -> None:
    """Every stored rate declares whether it was published or carried, and from where."""
    published = {
        date(2024, 1, 5): {"INR": Decimal("91.28"), "USD": Decimal("1.0956")},
        date(2024, 1, 8): {"INR": Decimal("91.50"), "USD": Decimal("1.0940")},
    }
    rows = to_rows(
        published, date(2024, 1, 5), date(2024, 1, 8), quote="INR", bases=["USD", "AED"], pegs=PEGS
    )

    assert len(rows) == 8  # 4 days x 2 currency pairs
    usd = [r for r in rows if r["base_currency"] == "USD"]
    assert [r["is_carried_forward"] for r in usd] == [False, True, True, False]
    assert usd[1]["carried_from"] == date(2024, 1, 5)
    # the fx_rate CHECK requires a carried row to name its origin
    assert all(not r["is_carried_forward"] or r["carried_from"] for r in rows)
    # a pegged currency is always derived, never fetched
    assert {r["source"] for r in rows if r["base_currency"] == "AED"} == {"derived_peg"}


class _Cursor:
    """Answers `reporting_config`'s three queries in order. No database, no fixtures."""

    def __init__(
        self, household: tuple[str, str], pegs: list[object], accounts: list[object]
    ) -> None:
        self._household = household
        self._fetchall = [pegs, accounts]

    def execute(self, *_: object, **__: object) -> None: ...
    def fetchone(self) -> object:
        return self._household

    def fetchall(self) -> object:
        return self._fetchall.pop(0)

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None: ...


def _conn(household: tuple[str, str], pegs: list[object], accounts: list[object]) -> object:
    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor(household, pegs, accounts)

    return _Conn()


def test_the_reporting_currency_is_never_its_own_base() -> None:
    """Adding peg anchors can put the quote currency back into the bases.

    Reporting in USD with a secondary of AED pulls USD in as AED's anchor, and a USD/USD row
    both violates the no_self_pair constraint and means nothing.
    """
    quote, bases, _ = reporting_config(
        _conn(("USD", "AED"), [("AED", "USD", Decimal("3.6725"))], [("AED",)])  # type: ignore[arg-type]
    )
    assert quote == "USD"
    assert "USD" not in bases, "the reporting currency must never appear as a base"


def test_every_currency_in_a_peg_chain_is_stored_not_only_its_ends() -> None:
    """A->B->USD must store B as well, or the B->USD peg identity has nothing to check.

    Expanding one hop stores A and B and omits USD; the identity check then joins B->INR rows
    against USD->INR rows that were never written, matches nothing, and reports a clean run.
    """
    _, bases, _ = reporting_config(
        _conn(
            ("INR", "XAA"),
            [("XAA", "XBB", Decimal(2)), ("XBB", "USD", Decimal(3))],
            [("XAA",)],
        )  # type: ignore[arg-type]
    )
    assert bases == ["USD", "XAA", "XBB"]


def test_a_pegged_reporting_currency_stores_its_own_anchor() -> None:
    """Reporting in a pegged currency writes INR->AED and nothing that exposes the peg.

    Every one of those rows travelled through the 3.6725, and without a USD->AED row there is
    nothing to check that factor against. The stored USD->AED rate *is* the peg rate.
    """
    _, bases, _ = reporting_config(
        _conn(("AED", "INR"), [("AED", "USD", Decimal("3.6725"))], [("INR",)])  # type: ignore[arg-type]
    )
    assert "USD" in bases, "a pegged reporting currency must store its anchor"
    assert "AED" not in bases
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd api && uv run pytest tests/test_fx_rows.py -v
```

Expected: FAIL — `No module named 'oneledger.routes.fx'`.

- [ ] **Step 3: Write the route**

```python
# api/oneledger/routes/fx.py
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_owner
from ..db import connect
from ..fx import (
    EcbFrankfurterSource,
    Peg,
    anchor_chain,
    carry_forward,
    cross_rate,
    terminal_anchor,
)

router = APIRouter(prefix="/fx", tags=["fx"])


def reporting_config(conn: psycopg.Connection) -> tuple[str, list[str], dict[str, Peg]]:
    """What this deployment needs quoted: (base currency, the others, the pegs).

    Read, never assumed. A household reporting in EUR against CHF fetches EUR and CHF.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT base_currency, secondary_currency FROM household LIMIT 1")
        row = cur.fetchone()
        if row is None:
            # Seeded by Task 3. Failing here with a sentence beats unpacking None.
            raise RuntimeError("no household row — run the Task 3 seed before backfilling")
        quote, secondary = row
        cur.execute("SELECT code, pegged_to, peg_rate FROM currency WHERE pegged_to IS NOT NULL")
        pegs = {code: Peg(anchor, rate) for code, anchor, rate in cur.fetchall()}
        cur.execute(
            "SELECT DISTINCT currency FROM account WHERE currency <> %s UNION SELECT %s",
            (quote, secondary),
        )
        bases = {row[0] for row in cur.fetchall()}

    # Every anchor along every chain is quoted too, even when no account holds it. Without
    # this the peg-identity check joins AED->INR rows against USD->INR rows that were never
    # written, finds nothing, and reports zero violations while proving nothing.
    #
    # The whole chain, not one hop: A->B->USD stored as A and B leaves the B->USD identity
    # just as vacuous as storing A alone left A->USD. And the quote's own chain, not only the
    # bases': reporting in a pegged AED writes INR->AED and nothing that exposes the 3.6725,
    # so the derivation every one of those rows travelled through is never checked. Storing
    # USD->AED makes it the peg rate itself, and a row that must equal currency.peg_rate.
    bases = {c for b in [quote, *bases] for c in anchor_chain(b, pegs)}
    # ...but a chain can pass through the reporting currency — base USD with secondary AED
    # puts USD back, and the quote is in the seed set by construction. A USD/USD row violates
    # the no_self_pair constraint and means nothing.
    bases.discard(quote)
    return quote, sorted(bases), pegs


def to_rows(
    published: dict[date, dict[str, Decimal]],
    start: date,
    end: date,
    quote: str,
    bases: list[str],
    pegs: dict[str, Peg],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in bases:
        series = {day: cross_rate(rates, base, quote, pegs) for day, rates in published.items()}
        # Either side going through a peg makes the result derived. Checking only the base
        # labels an ECB-published USD as `ecb` while reporting in a pegged AED, when the cross
        # in fact travelled through the peg.
        derived = base in pegs or quote in pegs
        source = "derived_peg" if derived else EcbFrankfurterSource.name
        for day, rate, carried in carry_forward(series, start, end):
            rows.append(
                {
                    "rate_date": day,
                    "base_currency": base,
                    "quote_currency": quote,
                    "rate": rate,
                    "source": source,
                    "is_carried_forward": carried is not None,
                    "carried_from": carried,
                }
            )
    return rows


def upsert_rates(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    """Idempotent: re-running a backfill overwrites with the same values rather than failing.

    Returns rows actually written, not rows offered. The conflict clause skips authoritative
    sources, so `len(rows)` would report writes that deliberately did not happen — and a
    backfill claiming to have stored a rate it declined to touch is worse than no number.
    """
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source,
                                 is_carried_forward, carried_from)
            VALUES (%(rate_date)s, %(base_currency)s, %(quote_currency)s, %(rate)s, %(source)s,
                    %(is_carried_forward)s, %(carried_from)s)
            ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
              SET rate = EXCLUDED.rate, source = EXCLUDED.source,
                  is_carried_forward = EXCLUDED.is_carried_forward,
                  carried_from = EXCLUDED.carried_from, fetched_at = now()
              -- Only reference rates are refreshable. A `manual` rate someone typed and an
              -- `actual_transfer` rate read off a statement are observations, and a reference
              -- feed does not get to overwrite an observation — multi-currency-and-fx.html §5
              -- is explicit that actual rates beat reference rates.
              WHERE fx_rate.source IN ('ecb', 'derived_peg')
            """,
            rows,
        )
        written = cur.rowcount
    conn.commit()
    return written


@router.post("/backfill")
def backfill(start: date, end: date, _: str = Depends(require_owner)) -> dict[str, int]:
    # carry_forward fills every calendar day in the range, so an end date in the future
    # writes the last known rate forward into it — years of invented history from one typo.
    # Nothing is silently invented (Invariant 7): refuse rather than clamp, so the caller
    # learns their range was wrong instead of quietly getting a different one.
    # One day of slack, deliberately. The service runs UTC and the caller may not: a request
    # for their genuine "today" from UTC+4 arrives a calendar day ahead of the server's, and
    # rejecting it would refuse a correct range. A typo like 2030-01-01 is still refused.
    if end > date.today() + timedelta(days=1):
        raise HTTPException(status_code=422, detail="end date is in the future")
    if start > end:
        raise HTTPException(status_code=422, detail="start date is after end date")

    with connect() as conn:
        quote, bases, pegs = reporting_config(conn)
        # A pegged currency is derived from its anchor, so only the end of each chain is
        # fetched. `bases` already carries the intermediates; they are stored, not requested.
        fetch = sorted({terminal_anchor(c, pegs) for c in [quote, *bases]})
        # Frankfurter snaps a range start backward to the previous publication day, so asking
        # from 10 days earlier guarantees carry_forward has something to fill from.
        published = EcbFrankfurterSource().fetch_range(start - timedelta(days=10), end, fetch)
        written = upsert_rates(conn, to_rows(published, start, end, quote, bases, pegs))
    return {"written": written}


@router.get("/{on}")
def rate_on(on: date, _: str = Depends(require_owner)) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT base_currency, quote_currency, rate, source,
                      is_carried_forward, carried_from
               FROM fx_rate WHERE rate_date = %s ORDER BY base_currency""",
            (on,),
        )
        return [
            {"base": b, "quote": q, "rate": str(r), "source": s, "carried": c, "carried_from": cf}
            for b, q, r, s, c, cf in cur.fetchall()
        ]
```

Register it in `main.py`:

```python
from .routes import fx

app.include_router(fx.router)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd api && uv run pytest tests/test_fx_rows.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Run the real backfill to the ADR-0011 anchor**

```bash
# From the repository root, and .env first — uv does not read it and the service refuses
# to start without SESSION_SECRET.
. scripts/load-env.sh
uv run --project api uvicorn oneledger.main:app --port 8000 &

# `date +%F`, not a literal. A hardcoded end date silently stops covering today the day
# after it is written, and the carried-rate probe below then reads an empty range rather
# than a carried rate — which looks like a broken carry-forward and is not.
curl -s -X POST "localhost:8000/fx/backfill?start=2024-01-01&end=$(date +%F)" \
  -b "session=$SESSION"

LAST_SUNDAY=$(date -v-sunday +%F 2>/dev/null || date -d 'last sunday' +%F)
curl -s "localhost:8000/fx/$(date -v-1d +%F 2>/dev/null || date -d yesterday +%F)" \
  -b "session=$SESSION"                                          # published, if a weekday
curl -s "localhost:8000/fx/$LAST_SUNDAY" -b "session=$SESSION"   # carried, names its origin
curl -s "localhost:8000/fx/2024-01-01" -b "session=$SESSION"     # the anchor
```

Expected: **two rows per calendar day** from the 01 Jan 2024 anchor to today — `AED` from the
reporting pair and `USD` as its peg anchor. Measured on 2026-09-06: 1,960 rows for 980 days.
Once accounts exist in other currencies the count rises by one row per day per currency, so
compute it rather than matching a number written months earlier.

A Sunday carries from the preceding Friday and says so; the anchor itself carries from
2023-12-29, the last publication day of the previous year. `2026-09-03` shows `[ecb]`/`[derived_peg]` with no carry
note; `2026-09-06` names `2026-09-04` as its origin; `2024-01-01` returns a rate.

- [ ] **Step 6: Verify the peg identity holds across the whole table**

```bash
psql "$DATABASE_URL" -c "
SELECT count(*) AS violations
FROM fx_rate a
JOIN fx_rate u ON u.rate_date = a.rate_date
             AND u.base_currency = 'USD' AND u.quote_currency = 'INR'
WHERE a.base_currency = 'AED' AND a.quote_currency = 'INR'
  -- A tolerance, not rounded equality. round(x,8) <> round(y,8) flags a pair that differs by
  -- 2e-10 when it happens to straddle a rounding boundary, which is not what "holds to 8
  -- decimal places" means. Measured worst case across 979 days is 1.8e-10: NUMERIC(20,10)
  -- storage granularity amplified by the 3.6725 multiplication, and two orders of magnitude
  -- inside the tolerance.
  AND abs(a.rate * 3.6725 - u.rate) > 1e-8;"
```

Expected: `violations | 0` — test 2 of
[multi-currency-and-fx.html](../design/multi-currency-and-fx.html) §9, run against real data.

- [ ] **Step 7: Prove an unpermitted account cannot reach the route**

Since [ADR-0013](../adr/0013-python-backend-service.html) the service is the authorization
boundary, so this test matters more than it did under RLS. Use a **second, real Google account**.

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "localhost:8000/fx/backfill?start=2024-01-01&end=2024-01-02" \
  -b "session=$SECOND_ACCOUNT_SESSION"      # expect 403
curl -s -o /dev/null -w '%{http_code}\n' "localhost:8000/fx/2024-01-02" \
  -b "session=$SECOND_ACCOUNT_SESSION"      # expect 403
```

Expected: `403` on both — including the read. Then confirm the rate is unchanged.

- [ ] **Step 8: Commit**

```bash
git add api/oneledger/routes api/tests/test_fx_rows.py api/oneledger/main.py
git commit -m "feat(api): fx backfill and inspection routes"
```

---

### Task 9: Next.js shell with a typed API client

**Files:**
- Create: `web/` via `create-next-app`, `web/Dockerfile`, `web/vitest.config.ts`,
  `web/lib/api/client.ts`
- Test: `web/lib/api/__tests__/env.test.ts`

**Interfaces:**
- Consumes: the service from Task 6.
- Produces: `apiFetch<T>(path, init)` — the single way the web app reaches the backend. No
  component calls `fetch` directly, and nothing in `web/` talks to Postgres.

- [ ] **Step 1: Scaffold and configure vitest**

```bash
npx create-next-app@latest web --typescript --tailwind --app --eslint --no-src-dir --use-npm
cd web && npm i decimal.js && npm i -D vitest
```

```typescript
// web/vitest.config.ts
import { defineConfig } from "vitest/config";
import path from "node:path";

export default defineConfig({
  test: { environment: "node", globals: false },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
```

`create-next-app` sets `@/*` in `tsconfig.json`, but vitest does not read it — without this every
`@/lib/...` import in every later test fails to resolve.

- [ ] **Step 2: Write the failing test**

```typescript
// web/lib/api/__tests__/env.test.ts
import { describe, expect, it } from "vitest";

describe("environment hygiene", () => {
  it("never exposes a secret to the browser", () => {
    const leaked = Object.keys(process.env).filter(
      (k) => k.startsWith("NEXT_PUBLIC_") && /SERVICE_ROLE|SECRET|PASSWORD|SALT|JWT/i.test(k),
    );
    expect(leaked).toEqual([]);
  });
});
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd web && npx vitest run
```

Expected: FAIL — no test files found, or the alias cannot resolve.

- [ ] **Step 4: Write the API client**

```typescript
// web/lib/api/client.ts
/**
 * The only route from the web app to the backend. Nothing in web/ touches Postgres, and no
 * component calls fetch directly — see ADR-0013.
 */
const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(readonly status: number, readonly detail: string) {
    super(`${status}: ${detail}`);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // The session is an httpOnly cookie the service sets. Browser JS cannot read it, so
    // there is no token to pass — the browser attaches it when told to include credentials.
    credentials: "include",
    headers: { ...init.headers },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => response.statusText);
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}
```

- [ ] **Step 5: Run the test and verify no secret ships**

```bash
cd web && npx vitest run && npm run build
grep -rE "SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://" .next/static/ \
  && echo "LEAK" || echo "OK: clean bundle"
```

Expected: 1 passed, then `OK: clean bundle`. A checklist item in
[security-and-privacy.html](../operations/security-and-privacy.html) §11.

- [ ] **Step 5b: Write the Dockerfile**

```dockerfile
# web/Dockerfile
FROM node:24-alpine
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Next.js inlines NEXT_PUBLIC_* during `next build` and freezes them into the bundle, so this
# has to arrive as a build argument. Supplying it at runtime is too late: the browser would
# fall back to localhost and every hosted visitor would call their own machine.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

EXPOSE 3000
CMD ["npm", "start"]
```

```bash
docker compose build web && docker compose up -d
curl -s -o /dev/null -w '%{http_code}\n' localhost:3000    # 200
```

- [ ] **Step 6: Commit**

```bash
git add web/
git commit -m "feat(web): Next.js shell with a typed API client"
```

---

### Task 10: Google OIDC in the service, with a pinned account

**Files:**
- Create: `api/oneledger/routes/auth.py`, `web/app/login/page.tsx`, `web/app/unauthorized/page.tsx`
- Modify: `api/oneledger/main.py`
- Test: `api/tests/test_oidc.py`

**Interfaces:**
- Consumes: `resolve_owner` and `require_owner` (Task 6); `app_user` (Task 3).
- Produces: `GET /auth/login` (redirect to Google), `GET /auth/callback` (exchange, verify, issue
  the session cookie), `POST /auth/logout`. The cookie every route in the system depends on.

**Why this lives in the service.** [ADR-0014](../adr/0014-one-vps-one-compose-file.html) dropped
Supabase, so there is no managed auth to lean on. For a single pinned user this is small: Google
OIDC is a redirect, a code exchange, a signature check, and a cookie.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/test_oidc.py
import pytest

from oneledger.routes.auth import claims_from_id_token
from oneledger.auth import NotAuthorized


def _token(email: str, verified: bool = True) -> dict[str, object]:
    return {"sub": "google-subject-1", "email": email, "email_verified": verified,
            "aud": "test-client", "iss": "https://accounts.google.com"}


def test_accepts_the_pinned_address():
    assert claims_from_id_token(_token("owner@example.com"), allowed="owner@example.com")


def test_rejects_any_other_google_account():
    """Google authenticating an account is necessary but not sufficient."""
    with pytest.raises(NotAuthorized):
        claims_from_id_token(_token("someone.else@example.com"), allowed="owner@example.com")


def test_rejects_an_unverified_address():
    """email_verified false means Google has not confirmed the address belongs to the account."""
    with pytest.raises(NotAuthorized):
        claims_from_id_token(_token("owner@example.com", verified=False),
                             allowed="owner@example.com")


def test_rejects_when_no_allowed_address_is_configured():
    with pytest.raises(NotAuthorized):
        claims_from_id_token(_token("owner@example.com"), allowed=None)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd api && uv run pytest tests/test_oidc.py -v
```

Expected: FAIL — `No module named 'oneledger.routes.auth'`.

- [ ] **Step 3: Write the OIDC routes**

```python
# api/oneledger/routes/auth.py
"""Google OIDC. No password, no signup, no registration, one pinned account (ADR-0004).

The mechanism moved out of Supabase Auth in ADR-0014; the policy is unchanged.
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import RedirectResponse

from ..auth import NotAuthorized
from ..db import connect

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"  # noqa: S105 — a URL, not a secret
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
# Read at call time, not at import: a module-level constant is fixed by whatever the
# environment held when the module first loaded, which no test can change afterwards.
def _pinned() -> str:
    return os.environ.get("ALLOWED_USER_EMAIL", "")


# The redirect targets are web routes, and the service is not the web origin. Sending the
# browser to a bare "/" lands it on the API's own port, which serves no such page.
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:3000").rstrip("/")

SESSION_TTL_SECONDS = 60 * 60 * 12


def claims_from_id_token(claims: dict[str, Any], allowed: str | None) -> dict[str, Any]:
    """Fails closed. A verified address that is not the pinned one is still a rejection."""
    if not allowed:
        raise NotAuthorized("no permitted address configured")
    if not claims.get("email_verified"):
        raise NotAuthorized("address is not verified by Google")
    if (claims.get("email") or "").strip().lower() != allowed.strip().lower():
        raise NotAuthorized("address is not the permitted account")
    if not claims.get("sub"):
        raise NotAuthorized("no subject in token")
    return claims


@router.get("/login")
def login() -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    }
    response = RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}")
    # CSRF: the callback must see the same state it issued.
    response.set_cookie("oidc_state", state, httponly=True, samesite="lax", max_age=600)
    return response


@router.get("/callback")
def callback(
    code: str,
    state: str,
    response: Response,
    # Cookie(), not a bare default: FastAPI binds a plain `str` to a QUERY parameter, and
    # Google never sends one, so the CSRF check would compare state against "" every time
    # and reject every sign-in.
    oidc_state: str = Cookie(default=""),
) -> RedirectResponse:
    if not state or state != oidc_state:
        raise HTTPException(status_code=400, detail="state mismatch")

    with httpx.Client(timeout=15.0) as client:
        token = client.post(GOOGLE_TOKEN, data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
            "grant_type": "authorization_code",
        }).raise_for_status().json()

        # Verify the signature against Google's published keys — never decode unverified.
        signing_key = jwt.PyJWKClient(GOOGLE_JWKS).get_signing_key_from_jwt(token["id_token"])
        claims = jwt.decode(token["id_token"], signing_key.key, algorithms=["RS256"],
                            audience=os.environ["GOOGLE_CLIENT_ID"])

    try:
        claims = claims_from_id_token(claims, _pinned())
    except NotAuthorized:
        return RedirectResponse(f"{WEB_ORIGIN}/unauthorized", status_code=303)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM app_user WHERE email = %s", (claims["email"],))
        row = cur.fetchone()
    if not row:
        return RedirectResponse(f"{WEB_ORIGIN}/unauthorized", status_code=303)

    session = jwt.encode(
        {"sub": str(row[0]), "email": claims["email"], "exp": int(time.time()) + SESSION_TTL_SECONDS},
        os.environ["SESSION_SECRET"], algorithm="HS256",
    )
    redirect = RedirectResponse(f"{WEB_ORIGIN}/", status_code=303)
    redirect.set_cookie("session", session, httponly=True, samesite="lax",
                        secure=os.environ.get("COOKIE_SECURE", "0") == "1",
                        max_age=SESSION_TTL_SECONDS)
    redirect.delete_cookie("oidc_state")
    return redirect


@router.post("/logout")
def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie("session")
    return response
```

Register it in `main.py`:

```python
from .routes import auth, fx

app.include_router(auth.router)
app.include_router(fx.router)
```

> `app_user` is matched on **email**, not on Google's subject, because the row is seeded before
> the first sign-in (Step 6). Storing `sub` on first login and matching on it thereafter is a
> reasonable hardening later; it is not needed for one pinned address.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd api && uv run pytest tests/test_oidc.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Register the redirect URIs with Google**

In the Google Cloud console, create an OAuth client (Web application) and add **both** redirect
URIs now, so deploying later needs no console visit:

```
http://localhost:8000/auth/callback
https://<your-hostname>/auth/callback
```

Put the client id and secret in `.env`.

- [ ] **Step 6: Confirm the household row exists**

The seed moved to Task 3, because it needs nothing from OIDC and Task 8 needs it to exist. If
Task 3 was run against this database it is already there.

```bash
psql "$DATABASE_URL" -c "SELECT email, role FROM app_user;"
```

Expected: one row, holding `ALLOWED_USER_EMAIL`.

- [ ] **Step 7: Verify sign-in end to end, both ways**

```bash
docker compose up -d
open http://localhost:3000
```

- Sign in as `$ALLOWED_USER_EMAIL` → lands on `/`, and `GET /whoami` returns the `household_id`.
- Sign in as **any other Google account** → lands on `/unauthorized`.

That second case is the one worth testing, because Google authenticated it successfully and the
service rejected it anyway.

- [ ] **Step 8: Prove the rejection holds without a browser**

The UI is not the boundary. Take the session cookie from a second account's browser session and
call the API directly:

```bash
curl -s -o /dev/null -w '%{http_code}
' localhost:8000/whoami   -b "session=$SECOND_ACCOUNT_SESSION"      # expect 403
curl -s -o /dev/null -w '%{http_code}
' localhost:8000/fx/2024-01-02   -b "session=$SECOND_ACCOUNT_SESSION"      # expect 403 — reads too
```

Expected: `403` on both. If the second account never gets a session at all, better still — the
callback rejected it before issuing one.

- [ ] **Step 9: Commit**

```bash
git add api/oneledger/routes/auth.py api/tests/test_oidc.py api/oneledger/main.py \
        web/app/login web/app/unauthorized
git commit -m "feat(api): Google OIDC with a pinned account and signed sessions"
```

---

### Task 11: Money formatting, currency toggle and navigation

**Files:**
- Create: `web/lib/money/format.ts`, `web/components/currency-toggle.tsx`
- Modify: `web/app/layout.tsx`
- Test: `web/lib/money/__tests__/format.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `formatMoney(value: string, currency: Currency, opts?): string`, where `Currency` is
  the row shape — `{ code, symbol, decimals, numberFormat }`. Every screen from M1 onward formats
  money through this and nothing else.

> **No currency code is a type here.** `"INR" | "AED"` as a union would put the first household's
> reporting pair into the type system, which is precisely what ADR-0002 moved out of the schema and
> what the README forbids. Formatting reads `currency.symbol`, `currency.decimals` and
> `currency.number_format` — the columns that exist for it. A household reporting in EUR and CHF
> gets correct output with no code change.

- [ ] **Step 1: Write the failing test**

```typescript
// web/lib/money/__tests__/format.test.ts
import { describe, expect, it } from "vitest";
import { formatMoney } from "@/lib/money/format";

// Rows as the `currency` table holds them. Nothing here is a literal in the source under test.
const INR = { code: "INR", symbol: "₹", decimals: 2, numberFormat: "indian" } as const;
const AED = { code: "AED", symbol: "AED", decimals: 2, numberFormat: "international" } as const;
const KWD = { code: "KWD", symbol: "KD", decimals: 3, numberFormat: "international" } as const;

describe("formatMoney", () => {
  it("groups by number_format, not by code (indian: 2,2,3)", () => {
    expect(formatMoney("13642180", INR)).toBe("₹1,36,42,180.00");
  });
  it("groups internationally when the row says so", () => {
    expect(formatMoney("5982530", AED)).toBe("AED 5,982,530.00");
  });
  it("abbreviates in crore and lakh for indian grouping", () => {
    expect(formatMoney("13642180", INR, { abbreviate: true })).toBe("₹1.36 Cr");
    expect(formatMoney("4210400", INR, { abbreviate: true })).toBe("₹42.10 L");
  });
  it("honours a currency with other than two decimals", () => {
    expect(formatMoney("1234.5", KWD)).toBe("KD 1,234.500");
  });
  it("keeps the minus sign — never colour alone", () => {
    expect(formatMoney("-310.00", AED)).toBe("-AED 310.00");
  });
  it("rejects a number, so no float can reach it", () => {
    // @ts-expect-error money crosses the wire as a string
    expect(() => formatMoney(310, AED)).toThrow(TypeError);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd web && npx vitest run lib/money/__tests__/format.test.ts
```

Expected: FAIL — cannot resolve `@/lib/money/format`.

- [ ] **Step 3: Write the formatter**

```typescript
// web/lib/money/format.ts
import Decimal from "decimal.js";

/** The `currency` row, as the API returns it. No code is ever a literal in this module. */
export interface Currency {
  code: string;
  symbol: string;
  decimals: number;
  numberFormat: "international" | "indian";
}

/**
 * Formats money for display. Takes a string because money crosses the wire as a string — a JSON
 * number would already be an IEEE-754 float by the time it arrived. See api-contracts.html §6.
 */
export function formatMoney(
  value: string,
  currency: Currency,
  opts: { abbreviate?: boolean } = {},
): string {
  if (typeof value !== "string") {
    throw new TypeError("money must be a string, never a number");
  }
  const amount = new Decimal(value);
  const sign = amount.isNegative() ? "-" : "";
  const abs = amount.abs();

  // A word-shaped symbol needs a space after it; a glyph does not. "AED 310", "₹310".
  const symbol = currency.symbol + (/[A-Za-z]$/.test(currency.symbol) ? " " : "");

  if (opts.abbreviate) {
    if (currency.numberFormat === "indian") {
      if (abs.gte(10_000_000)) return `${sign}${symbol}${abs.div(10_000_000).toFixed(2)} Cr`;
      if (abs.gte(100_000)) return `${sign}${symbol}${abs.div(100_000).toFixed(2)} L`;
    } else if (abs.gte(1_000_000)) {
      return `${sign}${symbol}${abs.div(1_000_000).toFixed(2)} M`;
    }
  }

  const locale = currency.numberFormat === "indian" ? "en-IN" : "en-US";
  const grouped = new Intl.NumberFormat(locale, {
    minimumFractionDigits: currency.decimals,
    maximumFractionDigits: currency.decimals,
  }).format(Number(abs.toFixed(currency.decimals)));

  return `${sign}${symbol}${grouped}`;
}
```

> `Number()` appears deliberately and is safe: it is the last step before rendering a string,
> after all arithmetic is done, and `Intl.NumberFormat` takes a number. Nothing is computed from
> the result.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd web && npx vitest run lib/money/__tests__/format.test.ts
```

Expected: 6 passed.

- [ ] **Step 5: Write the currency toggle**

```typescript
// web/components/currency-toggle.tsx
"use client";
import { useEffect, useState } from "react";

/**
 * The pair comes from `household` — base first, secondary second. No currency code appears
 * in this file, so a household reporting in EUR and CHF renders correctly with no change.
 */
export function CurrencyToggle({ pair }: { pair: readonly [string, string] }) {
  const [base, secondary] = pair;
  const [currency, setCurrency] = useState<string>(base);

  useEffect(() => {
    const saved = window.localStorage.getItem("oneledger.currency");
    const chosen = saved === base || saved === secondary ? saved : base;
    setCurrency(chosen);
    // Also on mount, not only on click: after a reload the dataset would otherwise be unset
    // until the next click, and CSS keyed on it would render the wrong currency's styling.
    document.documentElement.dataset.currency = chosen;
  }, [base, secondary]);

  function choose(next: string) {
    setCurrency(next);
    window.localStorage.setItem("oneledger.currency", next);
    document.documentElement.dataset.currency = next;
  }

  return (
    <div role="group" aria-label="Reporting currency" className="inline-flex rounded border">
      {pair.map((c) => (
        <button
          key={c}
          onClick={() => choose(c)}
          aria-pressed={currency === c}
          className={`px-3 py-1 text-sm ${currency === c ? "bg-neutral-900 text-white" : ""}`}
        >
          {c}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 6: Add the navigation**

Routes arrive milestone by milestone; linking them now means the shell is complete and each
milestone fills in a page rather than also editing navigation.

```typescript
// web/app/layout.tsx — inside <body>, above {children}
import Link from "next/link";
import { CurrencyToggle } from "@/components/currency-toggle";

const NAV = [
  ["/", "Overview"], ["/accounts", "Accounts"], ["/transactions", "Transactions"],
  ["/cashflow", "Cash Flow"], ["/investments", "Investments"], ["/loans", "Loans"],
  ["/networth", "Net Worth"], ["/documents", "Documents"], ["/import", "Import"],
  ["/review", "Review"], ["/settings", "Settings"],
] as const;

<header className="flex items-center justify-between border-b px-4 py-2">
  <nav className="flex gap-4 text-sm">
    {NAV.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
  </nav>
  {/* The pair comes from GET /household. Hardcoding it here would undo the point of the
      component taking it as a prop. */}
  <CurrencyToggle pair={[household.baseCurrency, household.secondaryCurrency]} />
</header>
```

- [ ] **Step 7: Verify the toggle persists and the shell is keyboard-navigable**

```bash
cd web && npm run dev
# Toggle to AED, reload — it must still read AED.
# Tab through the header: every link and both toggle buttons must take focus visibly.
```

Expected: the selection survives a reload, and nothing in the header is reachable only by mouse.
Keyboard operability is an accessibility requirement (WCAG AA,
[prd.html](../product/prd.html) §6), not a preference.

- [ ] **Step 8: Commit**

```bash
git add web/lib/money web/components/currency-toggle.tsx web/app/layout.tsx web/vitest.config.ts
git commit -m "feat(web): money formatting, currency toggle and navigation shell"
```

---

## Phase 0 exit criteria

Every one is a command with a checkable result, so "done" is verifiable rather than felt.

```
☐  docker compose up gives the whole stack — web, api, db — with no network dependency
☐  GET /health returns ok; GET /whoami returns 401 with no cookie, 403 with a bad one
☐  Google sign-in works locally; a non-permitted account reaches /unauthorized
☐  A second Google account is rejected at /auth/callback, and gets 403 from every api route
☐  GET /fx/2026-09-03 returns a rate with its source; /fx/2026-09-06 names its carry origin
☐  GET /fx/2024-01-01 returns a rate — the backfill reaches the ADR-0011 anchor
☐  The peg identity holds for every row: 0 violations
☐  The RLS isolation test fails when a policy is deliberately made permissive
☐  System seed institutions remain visible to the signed-in user
☐  No secret in .next/static/
☐  ./scripts/check-no-real-data.sh passes; no statement is tracked by git
☐  uv run pytest, ruff and mypy --strict all clean in api/
☐  All migrations apply cleanly from empty, seeds included, every test checked — in a
   SCRATCH project, because `down -v` on the default one deletes pgdata AND documents.
   Run `/verify` section 4, which is this in a subshell with a cleanup trap:
     COMPOSE_PROJECT_NAME=oneledger-verify DB_PORT=55432 POSTGRES_PASSWORD=oneledger
     POSTGRES_PORT=55432 DATABASE_URL="$(scripts/dsn.sh)"
     down -v; up -d db; flyway migrate; every db/seeds/*.sql; every db/tests/*.sql
   (the seeds are not optional: 0001_reference.sql asserts six system institutions, and
    0002_rls.sql asserts they stay visible to anon. Without `|| exit 1` the loop reports
    only the last file's status.)
☐  The compose file that runs locally is the one that would deploy — no managed service anywhere
```

**Next:** `02-m1-accounts.md`, written once Phase 0 is green.
