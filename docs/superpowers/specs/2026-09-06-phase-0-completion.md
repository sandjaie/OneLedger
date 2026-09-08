# Phase 0 completion specification

Date: 6 September 2026. Execution authorized by the owner: finish Phase 0 with subagent
implementation, GitHub PRs to `feature/phase-0-foundations`, Codex Cloud review, fixes by the
same implementer and sequential merges. Product-expansion features remain outside this phase.

## Baseline and scope

This specification covers original Task 9 web scaffold/client, Task 10 Google OIDC, Task 11 exact
money display and configured currency navigation, plus full-stack and scratch-database exit
verification. Consult [implementation progress](../progress.md) for execution state before starting
work.

The previous executable snippets are historical guidance. This spec resolves identified gaps:
the environment-only client test does not exercise a client; `Number()` loses money precision;
login/callback cannot require a session that they exist to create; and a cookie-only logout does
not implement the documented server-side revocation. Do not copy those defects into application
code.

## Delivery 1 — web foundation

A Next.js App Router app using TypeScript, Tailwind and npm on Node 24. Use system fonts,
no third-party page resources or financial data. The initial page states the current foundation
status and links to sign-in only once sign-in exists. No unimplemented feature claims.

`web/lib/api/client.ts` owns browser requests. It exports `ApiError(status, detail)` and
`apiFetch<T>(path, init?)`. Read the public API base from `NEXT_PUBLIC_API_URL`, defaulting to
`http://localhost:8000`. Send `credentials: "include"`, preserve caller method/body/headers,
parse successful JSON, return undefined for a 204 response, and throw ApiError for non-2xx.
This is the browser client; server-side requests must not silently assume localhost or a browser
cookie.

Include meaningful client tests (credentials, header preservation, JSON, 204, error), npm lint,
typecheck, test and build scripts, lockfile, Dockerfile and Docker context exclusions. The web
container receives only public configuration. Verify an actual image build and HTTP response.
Do not add business pages or a second auth library in this delivery.

The 6 September 2026 dependency audit verifies every direct package against the current stable npm
release and its peer ranges. Pin the Node 24 LTS development and container runtime and use matching
Node 24 declarations. Do not retain broad scaffold ranges without verifying them. When a current
tool cannot yet consume a newer compiler API, keep the smallest compatible package beside the
current
compiler, document why it remains, and continue to run both checks without suppressing failures.

## Delivery 2 — Google authentication and revocable owner sessions

Retain the pinned-email plus seeded app_user policy, and preserve the existing UUID app_user id
as the local session subject. Compare configured email consistently after trimming/lowercasing.
Only a verified Google email may bootstrap the existing seeded owner. Bind the Google subject
at the first successful login and require that binding on subsequent logins.

Bootstrap endpoints: GET /auth/login and GET /auth/callback are public by necessity. They expose
no financial data. /health remains public. Every other API endpoint, including POST /auth/logout
and GET /household, requires the owner. Document this narrow clarification in a new ADR and update
current boundary docs; existing ADR bodies remain immutable.

Login: use Google's authorization-code flow, state, nonce and PKCE S256. Keep short-lived signed
flow context in an HttpOnly SameSite=Lax cookie, expiry 10 minutes, Secure when COOKIE_SECURE=1.
Callback must reject absent, expired or mismatched state before exchange; validate ID-token
signature using Google JWKS, accepted Google issuer, configured audience, expiry, nonce,
nonempty subject and boolean email_verified=true. Require the pinned address and seeded row.
Never trust browser-submitted email or an unsigned token. Reject denied/malformed flows safely,
clear transient cookies, and never echo a token, code, secret or financial payload in errors/logs.

Use a signed 12-hour session with exp, iat, jti, issuer/audience and the seeded local subject.
Store a server-side session record, with expiry and owner reference, in a forward V5 migration;
require the active record on every authenticated request. Logout removes/revokes it so replay of
the old cookie is rejected. No silent refresh mechanism: after expiry the user signs in again.
This resolves the mismatch between the existing 12-hour plan and security prose promising rotating
refresh; update the current specification explicitly. Validate origin on mutating
cookie-authenticated
requests using WEB_ORIGIN; do not treat CORS alone as CSRF enforcement. Do not weaken existing
wrong-account checks or leave legacy JWTs without expiry accepted by the completed boundary.

GET /household returns the request owner's id, baseCurrency, secondaryCurrency and the two
currency rows (code, symbol, decimals, numberFormat); read actual database values. This is the
missing source for Task 11's toggle. No arbitrary household id from the browser.

Keep the implementation cohesive and tests adversarial. If authentication plus tests exceeds
500 changed implementation/test lines, split coherent persistence and callback PRs rather than
remove validation. Each PR still receives Cloud review and merges before the next begins.

## Delivery 3 — usable shell and exact money display

The web interface has /login and /unauthorized pages, a protected application shell, logout,
configured currency toggle, navigation, and an authenticated Settings / FX inspector for an
as-of date. Routes for later modules must show a clear unavailable state or be disabled; avoid
broken links or pretending financial features exist. Phase 0 overview states that ledger import
arrives later. The FX inspector displays rates as decimal strings with source/carry-forward date.

Check the owner session through the browser API client; unauthenticated navigation goes to login,
rejected sessions to unauthorized. The API remains the boundary. Logout POST invalidates the
server session then clears the application view. Show a useful service-unavailable state instead
of spinning indefinitely. Fetch household configuration to supply the toggle and formatter.

`formatMoney(value: string, currency: Currency, opts?: {abbreviate?: boolean}): string` supports
international/Indian grouping, currency decimals, word/glyph symbols, signs and L/Cr/M abbreviation.
Use exact decimal-string operations or the already planned decimal.js for formatting only; never
convert money through Number()/parseFloat. Half-up display rounding; suppress negative zero.
Reject non-string, NaN, infinity and malformed decimal values. Verify large values beyond safe
integer precision, 0/2/3 decimals, negatives, abbreviation thresholds and arbitrary currency codes.

The currency preference persists across reload and drives the shell's displayed currency. An
obsolete stored choice falls back to the configured base. Handle unavailable localStorage safely.
Use semantic navigation, visible focus and accessible toggle state; no external fonts or telemetry.

## Verification and external prerequisites

Every implementation task leaves passing targeted tests, lint/types and relevant existing suites.
Run meaningful red-green checks before implementation. Use a scratch Compose project for fresh
migrations, seeds, SQL tests and RLS permissive-policy failure proof; never remove production
volumes.
After image build, the stack should start from cached images without contacting Google or fetching
fonts. Google sign-in and FX fetching naturally require network access when invoked.

Verify protected route behavior through HTTP, cookie flags, logout replay, owner-scoped
configuration,
FX range endpoints and full-stack web navigation. Browser checks cover the login flow, toggle
persistence,
keyboard focus and the Settings FX inspector. Test mocked Google flows separately from real Google
sign-in; do not describe mocked evidence as a successful live Google login.

Live Google checks require privately configured GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and the
permitted ALLOWED_USER_EMAIL, plus interactive identity approval. Record configuration blockers and
completed checks only in the progress file. Never commit secrets or test with real statements.
Do not claim Phase 0 complete while a required exit check remains unverified.
