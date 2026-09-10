# Local Production Stack Implementation Plan

> **For agentic workers:** Execute inline, task by task, using
> `superpowers:executing-plans`. Implementation status remains only in
> `docs/superpowers/progress.md`; this plan is not updated as a tracker.

**Goal:** Run an empty, persistent, production-built OneLedger stack beside the existing working
ledger, with passwordless local owner sessions and Google OIDC retained for a later cloud deployment.

**Architecture:** An explicit authentication mode changes only the public login bootstrap; both
modes issue the existing revocable server-side session and all protected routes keep the same
authorization boundary. A second Compose file and guarded wrapper own an isolated project, ports,
secrets and external volumes, with Flyway and owner bootstrap completing before the API starts.

**Tech Stack:** FastAPI, PyJWT, PostgreSQL 18, Flyway 13, Docker Compose, Next.js 16, Bash 3.2.

**Spec:** `docs/superpowers/specs/2026-09-10-local-production-stack.md`

## Global constraints

- Workspace: `/Users/sandjaieravi/dev/OneLedger`.
- Branch: `feature/local-production-stack`; base and PR target: `develop`.
- The existing `docker-compose.yml` volumes and working ledger are never reset, renamed or mounted
  by the production project.
- Application code defaults to `AUTH_MODE=google`; local mode is valid only for an `http` loopback
  `WEB_ORIGIN`.
- Local login issues the normal signed, persisted, revocable HttpOnly cookie and never bypasses
  `require_owner`, CSRF origin checks or household scoping.
- The production project is `oneledger-production`, with web `3100`, API `8100`, database `55432`,
  and exact external volumes `oneledger-production_pgdata` and
  `oneledger-production_documents`.
- No real figure, filename, transaction, statement or secret enters Git or test output.
- Bash scripts remain compatible with macOS Bash 3.2 and do not weaken a check to pass.

---

### Task 1: Local owner authentication with the production boundary intact

**Files:**
- Modify: `api/oneledger/auth.py`
- Modify: `api/oneledger/routes/auth.py`
- Modify: `api/oneledger/main.py`
- Modify: `api/tests/test_auth.py`
- Modify: `api/tests/test_oidc.py`
- Create: `docs/adr/0018-local-owner-sessions.html`
- Modify: `docs/adr/index.html`, `docs/design/api-contracts.html`,
  `docs/operations/security-and-privacy.html`

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/local-production-stack`
**Base and PR target:** `develop`
**Depends on:** the approved design commit.
**Forecast:** ~70 added production lines and ~140 test lines; documentation is uncounted.

**Interfaces:**
- Produce `auth.auth_mode(value: str | None = None, web_origin: str | None = None) -> str`, returning
  only `local` or `google` and rejecting local mode outside an HTTP loopback origin.
- Produce `auth.local_owner() -> tuple[UUID, str]`, resolving exactly one configured owner without
  reading or changing `google_subject`.
- `GET /auth/login` branches on `auth_mode()`. Local mode calls `issue_session`, sets the existing
  session cookie and redirects to the configured web root; Google mode is byte-for-byte equivalent
  in behavior to the existing route.

**Test-first sequence:**
1. Add failing unit and route tests: default and explicit Google mode, invalid mode, allowed
   `localhost`/`127.0.0.1`, refused public/HTTPS origins, unique local-owner resolution, local login
   persisting a twelve-hour session, and no Google configuration or flow cookie in local mode.
2. Run `api/.venv/bin/pytest -q api/tests/test_auth.py api/tests/test_oidc.py`; confirm failures are
   missing `auth_mode`, `local_owner`, and the local route branch.
3. Implement the minimal validation, lookup and login branch. Call `auth_mode()` at API startup so
   unsafe deployment configuration fails before serving.
4. Re-run the focused tests, then `api/.venv/bin/ruff check api` and
   `api/.venv/bin/mypy --strict api/oneledger`.
5. Add ADR-0018 and update the three owning documents. Run `scripts/check-docs.py` and
   `scripts/check-identifiers.py`, then commit `feat(api): localhost issues a real owner session`.

**Acceptance:** local login creates an active server-backed owner session without Google, while
every existing Google and session-negative test remains green.

---

### Task 2: Automatic, mode-neutral login transition

**Files:**
- Modify: `web/app/login/page.tsx`
- Create: `web/app/login/page.test.tsx`

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/local-production-stack`
**Base and PR target:** `develop`
**Depends on:** Task 1's `/auth/login` mode switch.
**Forecast:** ~20 added production lines and ~50 test lines.

**Interfaces:**
- An initial `/login` render with no `reason` invokes Next.js `redirect(apiUrl("/auth/login"))`.
- `reason=authentication_failed` renders a neutral retry explanation and link to `/auth/login`;
  it never auto-redirects and therefore cannot loop.

**Test-first sequence:**
1. Read the repository-bundled Next.js 16 App Router redirect guidance named by `web/AGENTS.md`.
2. Add failing component tests that mock `next/navigation`: an initial visit redirects once to the
   configured API, while an authentication failure renders and never calls `redirect`.
3. Run `npm --prefix web test -- web/app/login/page.test.tsx`; confirm the initial-visit case fails.
4. Implement the server-page branch and neutral recovery copy; do not add browser session code or
   expose an auth-mode endpoint.
5. Re-run the focused test, `npm --prefix web run lint`, both type checks, and
   `npm --prefix web run build`; commit `feat(web): login enters the configured owner flow`.

**Acceptance:** opening either local stack enters its API login flow automatically, and a failed
flow remains recoverable rather than redirecting forever.

---

### Task 3: Isolated production Compose topology and guarded lifecycle

**Files:**
- Create: `docker-compose.production.yml`
- Create: `.env.production.example`
- Create: `scripts/bootstrap-owner.sql`
- Create: `scripts/production-stack.sh`
- Create: `api/tests/test_production_stack.py`
- Modify: `docker-compose.yml`
- Create: `docs/adr/0019-separate-local-production-stack.html`
- Modify: `docs/adr/index.html`
- Modify: `README.md`, `docs/design/architecture.html`, `docs/operations/security-and-privacy.html`
- Modify: `docs/superpowers/progress.md`

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/local-production-stack`
**Base and PR target:** `develop`
**Depends on:** Tasks 1 and 2.
**Forecast:** ~180 added production lines and ~260 test lines; Compose and documentation are
uncounted. Whole-branch forecast: ~270 production and ~450 test lines, below both budgets.

**Interfaces:**
- `scripts/production-stack.sh init|up|down|status|reset` always invokes Compose with
  `--project-name oneledger-production --env-file .env.production -f docker-compose.production.yml`.
- `reset` accepts only `--confirm oneledger-production` and removes only the two exact production
  external volumes after the production project is down.
- Compose services are `db`, `migrate`, `bootstrap`, `backup`, `api`, and `web`; API depends on
  successful bootstrap, bootstrap on successful migration, and both one-shot services on a healthy
  database.

**Test-first sequence:**
1. Add failing tests using a fake `docker` executable to assert project/file/env arguments, exact
   volume creation, retained-volume `down`, guarded reset refusal, and exact confirmed removals.
   Add structural assertions for ports, volume names, local auth, production builds and dependency
   conditions without loading secrets.
2. Run `api/.venv/bin/pytest -q api/tests/test_production_stack.py`; confirm it fails because the
   production file, wrapper and bootstrap SQL do not exist.
3. Implement the Compose file, environment template, idempotent owner bootstrap and Bash 3.2
   wrapper. Set `AUTH_MODE=${AUTH_MODE:-local}` only in the two local Compose definitions; code still
   defaults to Google.
4. Re-run the focused tests, `bash -n scripts/production-stack.sh`, and
   `docker compose --env-file .env.production.example -f docker-compose.production.yml config`.
5. Run `scripts/production-stack.sh init` and `up`. Verify health on `3100` and `8100`; query only
   booleans proving current Flyway version, zero financial/import rows, and exactly one owner.
6. Verify the existing development database still has its handed-off ledger count and no confirmed
   transfers. Run `down`, `up` again and prove the production owner/session-independent database
   identity persists. Do not execute `reset` against the created production volume in this delivery.
7. Update the operating documents and progress tracker without real figures. Run the full local
   `/verify` gate and commit `feat(ops): a clean production stack runs beside development`.

**Acceptance:** both stacks run concurrently, production starts migrated and financially empty,
normal teardown preserves it, and no production command can address a development or test volume.

---

## Branch closeout

Run `/verify`, then `/oneledger-code-review`; fix blocking findings, run `/verify` again, and re-review
only the fixes. Rebase onto `origin/develop` with `set -o pipefail` before the first push. Push through
hooks, open one PR to `develop` from `.github/PULL_REQUEST_TEMPLATE.md`, merge when green, and prove
the pre-merge `develop` commit is an ancestor of `origin/develop`. Then restore the development API
image and Next dev server and resume the slice 1.3/1.4 acceptance handoff.
