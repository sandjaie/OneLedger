# OneLedger web

This is the Phase 0 browser foundation for OneLedger. It currently provides the application shell
and the tested browser-to-API client. Owner sign-in and financial views are later deliveries.

## Local setup

Use the pinned Node 24 LTS release, then install exactly what the lockfile records:

```bash
nvm use
npm ci
```

The browser calls `http://localhost:8000` by default. To use another public API origin, set
`NEXT_PUBLIC_API_URL` before starting or building the app. It is the only supported
`NEXT_PUBLIC_` variable and must never contain credentials.

```bash
npm run dev
```

Open <http://localhost:3000>.

## Checks

```bash
npm test
npm run lint
npm run typecheck
npm run typecheck:compat
npm run build
```

`typecheck` runs the current native TypeScript 7 compiler. `typecheck:compat` runs TypeScript 6,
which remains installed under the standard `typescript` package name because Next.js and
typescript-eslint still consume its JavaScript compiler API.

## Container

The production image uses the pinned Node 24 LTS release and Next.js standalone output, so build,
lint, and test dependencies do not ship in the runtime stage.

```bash
docker build \
  --build-arg NEXT_PUBLIC_API_URL=http://localhost:8000 \
  -t oneledger-web .
docker run --rm -p 127.0.0.1:3000:3000 oneledger-web
```

The Docker context excludes local dependencies, build output, environment files, keys, and secret
directories. Next.js CLI telemetry is disabled in both build and runtime stages.

## Dependency roles

- Next.js and React provide the App Router runtime; `client-only` enforces the browser API boundary.
- Tailwind CSS and its PostCSS adapter compile the application styles.
- React and Node type packages provide declarations for the target runtimes.
- ESLint plus `eslint-config-next` enforce framework, React, accessibility, and TypeScript rules.
- Native TypeScript 7 performs the primary typecheck; TypeScript 6 supports compiler-API consumers.
- Vitest runs the API-client contract suite in Node.

Direct dependency versions are intentionally pinned. Audit them against current stable releases and
peer ranges before changing the lockfile; record any compatibility exception instead of suppressing
an install, type, lint, or build failure.
