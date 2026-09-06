# Phase 0 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.
> The owner authorized subagents using other models, sequential PRs, Cloud review/fix loops and merges
> to the Phase 0 integration branch. Checkbox states are updated only from verified evidence.

**Goal:** Complete original Tasks 9–11 and all Phase 0 exit checks without expanding product scope.

**Architecture:** Existing Next.js → FastAPI → Postgres boundary; exact money display in the web app,
owner authorization in the API, persistent session revocation in a forward migration.

**Tech Stack:** Node 24, current compatible Next.js/React, TypeScript, Tailwind, Vitest, Python 3.14,
FastAPI, PyJWT, httpx, psycopg and Postgres 18; existing Compose/Flyway tooling.

**Spec:** [Phase 0 completion specification](../specs/2026-09-06-phase-0-completion.md).
Read it together with [the original task reference](../phase-0-foundations.md) and
[PR workflow](../phase-0-pull-requests.md). The completion spec corrects the defective old snippets.

## Global Constraints

- Work branches target `feature/phase-0-foundations`; never push implementation directly to main.
- All new plans/specs live under docs/superpowers. No real financial data or secrets in git or reports.
- Python Decimal / Postgres NUMERIC / string wire money. No Number/parseFloat on money in the web app.
- Account and reporting currency configuration comes from the database; no hardcoded household pair.
- Public endpoints are limited to health and OIDC bootstrap; every financial endpoint requires the owner.
- Retain full auth validation, server-side revocation and CSRF protection. Document boundary clarification.
- Applied V1–V4 remain unchanged. Session/identity changes use a forward migration and matching references.
- Each PR has at most 500 implementation/test lines; split a task into sequential coherent PRs if required.
- Every PR must receive Codex Cloud review of its current head; implementer addresses findings and
  requests re-review. A clean bot review/comment for the current head counts as approval; silence does not.
- Hooks and required checks run without bypass. Do not remove real database or document volumes.
- Controller manages branches/PRs/merges; implementers may commit only their scoped task changes.

## Preflight rulings

| Tasks / interface | Conflict found | Resolution |
|---|---|---|
| Task 1 client → Tasks 2/3 | Old client always parses JSON; logout returns 204 | Explicit undefined return on 204 with a client test |
| Task 2 auth → Task 3 shell | Old toggle references undefined household data | Owner-scoped GET /household contract in the completion spec |
| Task 2 itself | Every route requires owner, but login must create a session | New ADR for two bootstrap exceptions; all financial endpoints remain protected |
| Task 2 itself | Cookie deletion conflicts with server-side sign-out revocation | Persistent session record; logout replay must fail |
| Task 3 itself | Old formatter casts money to Number despite global prohibition | Exact decimal formatting including large-value tests |
| Tasks 1/3 | Full shell would link to nonexistent financial modules | Disabled/unavailable navigation until the corresponding milestone |
| All | Missing local OAuth configuration | Complete automated checks and code; live checks stay pending until owner configures it |

### Task 1: Web scaffold and API client

**Delivered:** reviewed GitHub PR #8. The standalone container and HTTP response were verified;
the whole-Compose integration remains part of Task 4 rather than a Phase 0 completion claim.

**Branch:** `feature/phase-0-web-shell`.
**Files:** create `web/package.json`, lockfile, App Router scaffold, `web/Dockerfile`, `.dockerignore`,
`web/vitest.config.ts`, `web/lib/api/client.ts`, `web/lib/api/__tests__/client.test.ts`.
**Interfaces:** consumes existing API; produces `apiFetch<T>` and `ApiError` per spec.

- [x] Resolve current Next.js setup docs using ctx7 outside the sandbox; scaffold with npm, TypeScript,
  Tailwind, App Router, no src directory and the existing @ alias. Use system fonts.
- [x] Audit every direct dependency against current stable releases and peer ranges as of 6 September
  2026. Pin Node 24 LTS in local and container configuration with matching Node declarations; replace
  unverified scaffold ranges and document any required compatibility package rather than suppressing
  install, lint, type or build failures.
- [x] Add meaningful client tests first. At minimum, use mocked fetch with this assertion:

```typescript
const request = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
vi.stubGlobal("fetch", request);
await expect(apiFetch<void>("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
expect(request).toHaveBeenCalledWith(expect.stringContaining("/auth/logout"),
  expect.objectContaining({ method: "POST", credentials: "include" }));
```

- [x] Confirm tests fail for the missing client behavior, then implement the specified request contract;
  add JSON, non-2xx and caller Headers preservation checks. Restore stubbed globals between tests.
- [x] Add npm lint/typecheck/test scripts, Dockerfile honoring public API build arg, and Docker exclusions
  for node_modules, builds, env and secrets. Render a truthful foundation page; no live auth UI yet.
- [x] Run Vitest, ESLint, TypeScript and production build. Build the web image, start it with the existing
  Compose stack and verify HTTP 200. Scan the browser bundle for secret-shaped names and DSNs.
- [x] Commit, write test evidence to the task report, submit to controller for local and Cloud review.

### Task 2: Verified Google identity and revocable sessions

**Branch:** `feature/phase-0-google-oidc` (split suffix branches if line cap requires it).
**Files:** `api/oneledger/routes/auth.py`, `api/oneledger/auth.py`, `api/oneledger/main.py`,
`api/oneledger/routes/household.py`, auth/session tests, V5 migration and its SQL test;
`.env.example`, relevant security/API/data-model/schema docs and the new boundary ADR.
**Interfaces:** consumes seeded app_user and connect(); produces login/callback/logout,
revocable require_owner and owner-scoped household configuration from the completion spec.

- [ ] Read current PyJWT and Google OIDC documentation with ctx7; define token verification and
  HTTP/database seams so real protocol branches can be tested with synthetic identities.
- [ ] Write failure checks first for state/cookie mismatch and missing expiry; prove callback makes no
  token exchange or session issuance for invalid state. Also cover denied identity and unverified email.
- [x] Add a forward session/Google-subject migration with RLS and expiry/revocation semantics, updating
  schema references. Keep original V1–V4 bytes unchanged and preserve the existing seeded UUID id.
- [ ] Implement state/nonce/PKCE, Google signature/issuer/audience/expiry validation, pinned verified
  identity and persisted subject binding; handle denial, malformed tokens and network failure without leaks.
- [x] Implement exp/iat/jti/issuer/audience session verification plus active-record lookup, protected logout
  and allowed-origin mutation checks. Verify a captured cookie fails after logout.
- [x] Implement GET /household using only the owner resolved from the request; expose the configured pair
  and currency metadata required by Task 3. Test no-cookie, forged, wrong-owner and correct-owner paths.
- [x] Document the narrow bootstrap exceptions and 12-hour sign-in-again policy in a new ADR/current docs.
  Update route authorization coverage so a newly unguarded financial route fails the tests.
- [ ] Run targeted auth tests, all Python checks, fresh migrations and SQL tests; report red-green evidence,
  line count and live-configuration limitations. Split before exceeding the PR cap; never weaken checks.
- [ ] Commit and return to controller for review, Cloud review, fixes and merge before Task 3.

### Task 3: Exact money, configured shell and FX inspector

**Branch:** `feature/phase-0-money-and-navigation`.
**Files:** `web/lib/money/format.ts` and tests, currency preference/toggle component and tests,
protected shell, login/unauthorized pages, Settings FX page and integration tests; API client usage only.
**Interfaces:** consumes Task 1 client and Task 2 household/auth; produces the usable Phase 0 web shell.

- [ ] Write formatter checks first, including these exact expectations:

```typescript
expect(formatMoney("13642180", {code:"XIN", symbol:"₹", decimals:2,
  numberFormat:"indian"})).toBe("₹1,36,42,180.00");
expect(formatMoney("9007199254740993.12", {code:"XUS", symbol:"$", decimals:2,
  numberFormat:"international"})).toBe("$9,007,199,254,740,993.12");
```

- [ ] Add negative, rounded-negative-zero, 0/3 decimal, L/Cr/M, invalid/nonfinite/non-string cases;
  implement exact decimal display without Number/parseFloat or currency-code branches.
- [ ] Build login and unauthorized pages; guard the application view through authenticated household
  loading; logout clears the view after the server invalidates the session. Handle offline service errors.
- [ ] Add a configured pair toggle with persistence, invalid-preference fallback, storage-error tolerance
  and accessible pressed state. It must change the displayed currency, not only its own button style.
- [ ] Add navigation and Settings FX inspector with date input and protected API request; show stored
  source/carry origin. Keep unavailable financial modules disabled or clearly labelled.
- [ ] Run unit/integration tests, lint/types, production build and secret scan; browser-check keyboard
  access, auth transitions, preference reload and FX display using safe synthetic test sessions.
- [ ] Commit and return for local/Cloud review, fixes and merge.

### Task 4: Phase exit verification and integration closeout

**Files:** only required verification tooling/tests and status/spec evidence; fixes go to their original
implementer and a reviewed PR, not directly to integration.
**Interfaces:** consumes all three merged deliveries and local owner OAuth configuration.

- [ ] Run all existing Python/SQL/web checks on the integration head and build/start all Compose services.
- [ ] Apply every migration and seed to a scratch Compose project, run every SQL suite, and prove RLS
  tests fail with a deliberately permissive policy in that scratch database only. Remove only scratch volumes.
- [ ] Verify dated FX rows/history coverage and peg checks without printing private ledger data.
- [ ] Verify real permitted Google login and a second-account rejection when owner configuration is ready;
  explicitly distinguish mocked protocol evidence from live identity verification.
- [ ] Obtain final whole-phase review, resolve findings through the responsible implementer and reviewed PR.
- [ ] Record actual check results and update Phase 0 status. Mark complete only if every required gate is met.
  Push final records to integration. Integration-to-main merge occurs only if the user requests it separately.
