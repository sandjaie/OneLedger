# Local production stack and local owner sessions

Design approved by the owner on 10 September 2026, against `develop` at `a73bf8b`.
This work precedes the remaining slice 1.3 and 1.4 acceptance actions. It leaves the standing
development ledger untouched.

## Outcome

OneLedger has two local environments that can run at the same time:

| Environment | Compose file | Web | API | Database | Purpose |
| --- | --- | --- | --- | --- | --- |
| Development and acceptance | `docker-compose.yml` | `3000` | `8000` | `5432` | The existing working ledger and the disposable `db-test` profile |
| Local production | `docker-compose.production.yml` | `3100` | `8100` | `55432` | An optimized build on a separate, initially empty, persistent database |

The local-production database and document store use the external volumes
`oneledger-production_pgdata` and `oneledger-production_documents`. They are never shared with
the development stack or its test database, and ordinary teardown cannot remove them.

The application opens the single seeded owner workspace locally without contacting Google.
Google OIDC remains implemented and becomes the authentication mode when a later cloud deployment
sets `AUTH_MODE=google`.

## Decisions

### Two Compose files, two projects

The owner explicitly chose a separate production Compose file. This supersedes ADR-0014's
"one environment definition" detail while preserving its substantive topology: Postgres, Flyway,
FastAPI, the optimized Next.js image, encrypted backups, and later a TLS reverse proxy.

`docker-compose.production.yml` declares the project name `oneledger-production`, distinct
loopback host ports, and exact external volume names. It does not include `db-test` and it mounts
no development source directory. Both application services run their existing production images.

The production database persists across `down` and restarts. It is empty only on first creation;
an explicit, guarded reset command is the sole supported way to discard it. "Empty" means no
imported or mock financial rows. Migrations still install reference currencies, the configured
household and taxonomy rows required by the application.

### Bootstrap is part of startup

A one-shot Flyway service migrates the clean database before any application service starts. A
second one-shot service idempotently creates or verifies the single configured owner after Flyway
completes. The API depends on successful bootstrap; the web depends on the API. Startup therefore
cannot expose a half-migrated or ownerless application.

The bootstrap stores configuration only: the existing household/person migration rows and one
owner address. It imports no statements, layouts, rules, transactions, FX history, or fixtures.

### Local authentication still issues a real session

`AUTH_MODE` has two values:

- `local`: `GET /auth/login` resolves the one configured seeded owner, writes a normal revocable
  `app_session`, sets the existing signed HttpOnly cookie, and redirects to the web application.
- `google`: the existing state, nonce, PKCE, Google-token verification, subject binding and owner
  checks run unchanged.

The default in application code is `google`. Local Compose files opt into `local`. The API refuses
local mode unless `WEB_ORIGIN` is an `http` loopback origin, so a copied cloud configuration fails
closed instead of exposing a passwordless public login.

Local mode does not bypass `require_owner`, the session table, household scoping, CSRF origin
checks, expiry or revocation. No session token is placed in browser code or a URL. The cookie is
created only by the API.

The login page redirects to `/auth/login` automatically on an initial visit. A returned failure
renders a neutral retry screen instead of immediately looping. This works for both authentication
modes and contains no Google-specific promise while local mode is active.

### Production environment and lifecycle

`.env.production.example` documents the separate configuration and carries no secret. The ignored
`.env.production` is created locally with generated database, session, hashing and backup secrets;
it may reuse the configured owner address but not the development database connection.

`scripts/production-stack.sh` is the supported interface:

- `init` creates `.env.production` once and the two exact external volumes.
- `up` validates configuration, creates missing volumes, builds, migrates, bootstraps and waits for
  the production stack.
- `down` stops and removes its containers and network while retaining both volumes.
- `status` shows only the production project.
- `reset --confirm oneledger-production` stops the stack and removes only its two exact external
  volumes; the next `up` starts clean. The confirmation is deliberately verbose and non-positional
  accidents are refused.

The production backup service writes encrypted dumps with the `oneledger-production` prefix. Its
database credentials and passphrase come only from `.env.production`; the web image receives only
its public API URL.

## Repository delivery

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`

**Branch:** `feature/local-production-stack`

**Base and PR target:** `develop`

The change is delivered TDD in three seams:

1. local authentication and the automatic login transition;
2. the isolated production Compose topology and bootstrap;
3. the guarded lifecycle script and operating documentation.

The implementation updates the applicable ADR, architecture, security and API documents rather
than leaving ADR-0014 and the Google-only text contradicted. It records no real financial value,
filename, row or secret.

## Acceptance

1. The development stack retains its current containers, volumes and ledger counts.
2. The production stack can run concurrently on `3100`, `8100` and `55432`.
3. Its database migrates through the current version and contains no imported documents,
   transactions, statement layouts, classification rules, transfers or card cycles.
4. Opening `http://localhost:3100` creates a normal local owner session and reaches the empty
   workspace without a Google request.
5. Local authentication is refused with a non-loopback web origin; Google mode retains its existing
   tests and behavior.
6. `down` preserves production data. The guarded reset removes only the two named production
   volumes and the next `up` recreates a clean migrated database.
7. The normal `/verify` and `/oneledger-code-review` gates pass before the first push.

## Not in this change

Cloud deployment, TLS, DNS, Caddy configuration, Google credentials, restoring a production
backup, importing the development ledger into production, changing the test database, or
implementing the product mockups. The remaining slice 1.3 and 1.4 acceptance run resumes after
this branch merges; UI implementation is planned only after those acceptances finish.
