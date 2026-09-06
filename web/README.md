# OneLedger web

Phase 0 web code is a Next.js App Router app that talks only to the FastAPI service. The API
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

## API Types

The TypeScript API contract is generated from FastAPI's local OpenAPI exporter. The schema is not
served as an anonymous runtime route.

```bash
npm run api:types
npm run api:types:check
```

`npm run api:types` runs `uv run --project api python scripts/openapi.py`, then
`@hey-api/openapi-ts` with only the `@hey-api/typescript` plugin. The committed output lives in
`web/types/api/`. The check command regenerates into a temporary directory and fails on drift.

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
TypeScript 6 compiler-API compatibility check for Next.js and ESLint consumers.
`npm run build` uses Next.js' documented Webpack build mode because the current Turbopack path
panics in this local host while evaluating the PostCSS pipeline.

## Container

```bash
docker build \
  --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 \
  -t oneledger-web .
docker run --rm -p 127.0.0.1:3000:3000 oneledger-web
```

The Docker context excludes local dependencies, build output, environment files, keys, and secret
directories. Next.js telemetry is disabled in build and runtime stages.
