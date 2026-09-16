# OneLedger web

The web app is a Next.js App Router app that talks only to the FastAPI service. The API
service owns authentication and financial access control; the browser uses `apiFetch` with cookies
and generated OpenAPI response types.

## Setup

Use the pinned Node release and install from the committed lockfile:

```bash
nvm use
npm ci
```

The browser calls `http://localhost:8000` unless `NEXT_PUBLIC_API_URL` is set. That is the only
supported `NEXT_PUBLIC_` variable and it must contain only a public API origin.

```bash
npm run dev
```

Open <http://localhost:3000>.

The root route is an owner-only workspace. It checks `GET /household` through the browser client,
uses the returned reporting pair for the persisted currency preference, and sends 401/403 responses
to `/login` or `/unauthorized`. The sign-in link performs a top-level
navigation to the API's `GET /auth/login`; `POST /auth/logout` must succeed before the owner view is
cleared. A transient API or logout failure stays visible with a retry action.

The workspace sections — overview, accounts, transactions, review, import, documents, investments,
salary, plans, wealth, coverage and settings — each read their own API routes; money arrives as
decimal strings and is only formatted in `lib/money/format.ts`, never computed.

## API Types

The TypeScript API contract is generated from FastAPI's local OpenAPI exporter. The schema is not
served as an anonymous runtime route.

```bash
npm run api:types
npm run api:types:check
```

`npm run api:types` runs `uv run --project api --frozen python scripts/openapi.py`, then
`@hey-api/openapi-ts` with only the `@hey-api/typescript` plugin. The committed output lives in
`web/types/api/`. The check command regenerates into a temporary directory and fails on drift.
It is part of the repository pre-push gate and the canonical verify skill. It is not part of the
web image build, so the production container stays Node-only.

## Authentication Configuration

Google sign-in is configured on the API service, not in the web bundle. Local authenticated flows
need API-side values for `WEB_ORIGIN`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`ALLOWED_USER_EMAIL`, `SESSION_SECRET`, and `COOKIE_SECURE` as appropriate for the environment.
Do not commit those values.

## Checks

```bash
npm test
npm run api:types:check
npm run lint
npm run typecheck
npm run typecheck:compat
npm run build
```

`typecheck` runs the current native TypeScript 7 compiler. `typecheck:compat` keeps the documented
TypeScript 6 compiler-API compatibility check for Next.js and ESLint consumers. `npm run build`
runs `next build --webpack`.

## Container

```bash
docker build \
  --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 \
  -t oneledger-web .
docker run --rm -p 127.0.0.1:3000:3000 oneledger-web
```

The Docker context excludes local dependencies, build output, environment files, keys, and secret
directories. Next.js telemetry is disabled in build and runtime stages.

## Dependency Roles

- Next.js and React provide the App Router runtime; `client-only` enforces the browser API boundary.
- Tailwind CSS and its PostCSS adapter compile the application styles.
- React and Node type packages provide declarations for the target runtimes.
- ESLint 9 plus `eslint-config-next` enforce framework, React, accessibility, and TypeScript rules.
  ESLint 9 remains pinned because the current Next import plugin peer excludes ESLint 10.
- Native TypeScript 7 performs the primary typecheck. TypeScript 6 remains installed under the
  standard `typescript` package name for compiler-API consumers that have not moved to native TS 7.
- `@hey-api/openapi-ts` 0.99.0 generates type-only API artifacts. It supports the TypeScript 6
  compiler-API package, while the previously proposed `openapi-typescript` release peers only
  against TypeScript 5.
- The narrow `js-yaml` 4.3.1 override patches `@hey-api/json-schema-ref-parser`'s exact 4.2.0
  transitive dependency and keeps `npm audit` clean without `--force` or legacy peer resolution.
- Vitest runs the API client and money formatter suites and the jsdom component tests beside each
  screen in `__tests__/`.
- Testing Library 16.3.3 and its DOM package exercise rendered React interactions. Their React 19
  peer ranges match the runtime; jsdom 30.0.1 supplies the browser test environment and supports the
  pinned Node 24.20 runtime.

Direct dependency versions are intentionally pinned. Audit them against current stable releases and
peer ranges before changing the lockfile; record compatibility exceptions instead of suppressing
install, type, lint, build, generator or audit failures.
