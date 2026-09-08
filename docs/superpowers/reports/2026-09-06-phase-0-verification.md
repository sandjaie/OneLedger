# Phase 0 implementation and verification record

Snapshot date: 6 September 2026. Integration branch: `feature/phase-0-foundations`.
This report preserves verification evidence from that run. For live implementation status, use
[the progress file](../progress.md); do not refresh this report as a tracker.

At this snapshot, real permitted-account and second-account Google checks were unverified. Automated
and synthetic-session checks do not substitute for those checks. The presence-only configuration
check found the Google client ID and secret missing and the allowed email placeholder unchanged. No
credential values are included in this record.

## Reviewed deliveries

| GitHub PR | Delivery | Integration commit |
| --- | --- | --- |
| #3–#7 | Compose, database V1–V4, owner boundary and FX pipeline | Existing baseline |
| #8 | Current web scaffold, browser API client, Node and dependency audit | 7aa111f |
| #9 | V5 identity/session persistence and revocable owner sessions | 796e7c2 |
| #10 | Protected logout, household configuration and typed FX responses | fe54461 |
| #11 | Google OIDC code flow with state, nonce, PKCE and subject binding | 3b8eb9c |
| #12 | Exact money formatting and generated API contract with enforced drift check | bd9f6ba |
| #14 | Bounded browser requests with compatible caller cancellation | 07dc7df |
| #13 | Authenticated shell and configured currency preference | f4afb69 |
| #15 | Settings FX inspector with exact rates, provenance and recovery | 360f5ed |
| #16 | Fixed Google account chooser on sign-in retry | cdaea94 |
| #17 | Explicit dual-compiler verification and corrected API boundary comments | 0cb557b |

Each completion delivery (#8–#17) passed local review, current-head Codex Cloud review and required
hooks before merging. No implementation was merged into main. The original numbered plan deliveries
were split into bounded PRs to retain the 500-line implementation/test limit.

## Dependency audit

The web app uses Node **24.20.0 LTS**, Next.js **16.3.4**, React/React DOM **19.2.8**, Tailwind
**4.3.3** and native TypeScript **7.0.2**. Every direct dependency was checked against current
stable
releases and peer ranges on 6 September 2026. Direct versions are pinned and installation passes
`npm ci --strict-peer-deps`; no forced or legacy peer resolution is used. The final `npm audit`
reports **zero vulnerabilities**.

There are four runtime dependencies: Next.js, React, React DOM and `client-only`. Development
dependencies compile CSS, generate/check TypeScript contracts, lint or run tests. Their number
does not represent the production runtime footprint.
[Dependency roles](../../../web/README.md#dependency-roles)
explains why each group is present.

The TypeScript 6 compiler-API package remains under the standard `typescript` name because
framework/lint consumers still require that API. Native TypeScript 7 remains the primary compiler.
ESLint 9.39.5 remains because the current Next import plugin's peer range excludes ESLint 10. These
exceptions are documented and checked; no forced or legacy peer resolution is used.

The API generator is development-only `@hey-api/openapi-ts` 0.99.0, configured for types only. A
narrowly scoped js-yaml 4.3.1 override patches the parser's transitive dependency. The exact
formatter adds no runtime dependency. Runtime image inspection confirmed Node 24.20, UID 1000, and
the absence of TypeScript/native TypeScript, ESLint, Vitest, Tailwind, the API generator, Testing
Library and jsdom. ESLint 9's deprecation notice and dependency installation notices remain visible;
they were not suppressed.

## Database and API evidence collected

- All five migrations and every reference seed were applied to an empty isolated PostgreSQL 18.4
  scratch project; all five SQL suites passed.
- A deliberately permissive policy in that scratch project caused the existing anonymous-isolation
  assertion to fail. Removing the probe restored a passing suite. The working database and document
  volumes were untouched.
- Reference FX coverage was checked across 980 days. One missing pair on 2 June 2025 was repaired
  through the existing reviewed pipeline for that date only, preserving authoritative-rate behavior.
  The final aggregate had 1,960 rows, 980 complete peg pairs and zero violations at the documented
  tolerance.
- An actual API connected to synthetic scratch identity data returned protected household
  configuration and decimal-string FX rates with carry-forward provenance. Wrong-origin logout
  failed while retaining the session; allowed-origin logout deleted the cookie, and replay of the
  captured revoked cookie failed.
- Missing OAuth configuration returned a safe service-unavailable response. Invalid callback input
  redirected safely and cleared transient cookies. A synthetic callback-code sentinel was absent
  from response content and API logs.

## Browser evidence collected

Fresh isolated Playwright/Chromium contexts used synthetic identities and no user browser profile.
The shell passed desktop and 390px mobile layout inspection, keyboard navigation with visible focus,
configured currency selection and reload persistence, safe login error handling, sign-out clearing
and protected re-entry. No page errors or horizontal overflow occurred. The inspector also passed
checks against the actual API with synthetic identity data, including exact rates/provenance,
empty/error/retry states, session revocation and rejection of a captured revoked cookie. A race
check held the logout response after server revocation and confirmed a concurrent FX rejection could
not divert sign-out. Rate/provenance layout was checked at 1440, 1024, 820 and 390 pixels.
Production Settings also displayed the exact stored rates and provenance for both explicit exit
dates, 1 January 2024 and 3 September 2026. These checks did not contact Google.

## Final integration verification

The integration implementation at `cdaea942c9bbd58bdd600ba4309b11b08887a16b` passed:

| Check | Result |
| --- | --- |
| API pytest | 59 passed; two existing dependency deprecation warnings remain visible |
| API lint, formatting and strict types | Ruff and mypy passed; 17 Python source files checked |
| Web unit and rendered interaction suites | 33 passed across four files |
| Native TypeScript 7 and TypeScript 6 compatibility | Both passed sequentially on Node 24.20 |
| Generated API contracts | Regeneration/drift check passed |
| Web lint and dependency security | ESLint passed; npm audit reported zero vulnerabilities |
| Empty database, reference seeds and invariant tests | All five migrations and five SQL suites passed |
| RLS negative control | Deliberately permissive anonymous policy failed the isolation assertion; removal restored success |
| Applied migration immutability | V1–V4 unchanged from the completed baseline; V5 unchanged from its merge |
| Default production build and local stack | API and web images built; default Next Turbopack build and Compose startup passed |
| Offline cached-image startup | PostgreSQL, API and web started on a fresh internal-only Docker network; health and login returned 200 |
| Production browser, synthetic identity | Exact FX/provenance, mobile/desktop layout, focus, preference, retry, revocation and the concurrent FX/logout case passed |
| Runtime and browser asset inspection | Non-root Node 24.20, development packages absent, no private configuration markers or DSNs in browser assets |
| Repository checks | Required commit/push hooks, financial-file exclusion and documentation checks passed |

The production browser check used the final API and web images connected to synthetic scratch
identity data. It did not use the owner's browser profile or contact Google. The offline check
used already-built images; first installation/build, FX fetching and Google sign-in still need
network access. Scratch cleanup targeted only the created containers, network and volumes; the
normal database and document volumes were preserved.

The default container build is the verified build path. This host has a diagnosed Turbopack/PostCSS
port-binding restriction during host builds; the explicit Webpack fallback remains documented.
The standard build command was not replaced. A separate clean-copy review confirmed both compiler
checks work without pre-existing Next output; the earlier intermittent failure was caused by a
build regenerating `.next` concurrently with type checks.

## Whole-phase review

The strongest available review model, GPT-6 Astra, reviewed the complete phase from its branch
merge-base, including the initial database/FX/API foundation and all completion deliveries. It
found **no Critical or Important defects**. Its two minor maintenance findings were to make the
compatibility compiler explicit in the standard verification gate and correct outdated comments
about public API routes. Both were addressed in
[PR #17](https://github.com/sandjaie/OneLedger/pull/17), merged as `0cb557b`
after the strongest scoped re-review and a clean current-head Codex Cloud review. No review
findings remain. The dual-compiler hook was checked against real dependencies and an isolated
command shim: either compiler failure propagates; the second is skipped when the first fails.
The final follow-up changes only the verification gate and comments; application behavior and
web dependencies remain those exercised by the production acceptance checks.

## Unverified at the snapshot

The implementation and synthetic integration checks above were verified; these real identity
steps had not been verified at the snapshot:

1. Privately configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and the real
   `ALLOWED_USER_EMAIL` in ignored `.env`; ensure the seeded owner matches the permitted email.
   The local callback URI is `http://localhost:8000/auth/callback`.
2. Verify the permitted Google account can sign in and access the protected application.
3. Verify a real second Google account is rejected, can choose another account on retry, and gains
   no protected API access.

Mocked Google protocol tests, synthetic signed sessions and browser checks are separate evidence.
They do not establish those live results. Subsequent verification and phase-closure state belong
in the progress file. The reviewed implementation was on `feature/phase-0-foundations`; merging
it to `main` requires a separate owner request.

## Execution decisions and trade-offs

These decisions preserve the Phase 0 scope and the owner's requested review workflow. They are
listed in the order recorded in the execution ledger.

1. Split session/household persistence from OIDC if needed to keep full security coverage under the
   PR cap. Cost: an additional review cycle.
2. Define `/household`'s `id` as the household UUID and return only its configured currency pair.
   Cost if the interpretation changes: a small contract rename.
3. Disable raw callback access logging because authorization codes can appear in request URLs. Cost:
   less HTTP logging until safe structured logging exists.
4. Use native TypeScript 7 alongside the official TypeScript 6 compiler-API compatibility package.
   Cost: one documented development package until upstream consumers support the new compiler.
5. Retain ESLint 9 while Next's import plugin excludes ESLint 10 in its peer range. Cost: postponing
   the ESLint 10 upgrade.
6. Split session persistence/core from logout/household HTTP delivery when their combined tests and
   implementation exceeded 500 lines. Cost: another review cycle; no security requirement was
   removed.
7. Add typed OpenAPI schemas to household and the existing FX response while preserving wire
   behavior. Cost: bounded schema and test code; no FX calculation/query change.
8. Generate and check committed API types, including the currency alias, instead of duplicating wire
   contracts by hand. Cost: a development-only generator and drift check. Associate FX results with
   their requested date without inventing a response field.
9. Choose the current compatible type-only Hey API generator because the previously suggested tool
   required TypeScript 5. Cost: adapting the generator command and output shape.
10. Refresh only the missing reference-FX date, 2 June 2025, through the existing reviewed pipeline.
    Cost: refreshing two non-authoritative reference rows while preserving manual and
    actual-transfer precedence.
11. Keep generated artifacts in the generator's native `web/types/api/` directory. Cost: updating
    imports and documentation instead of hand-editing generated files.
12. Split the authenticated shell from the FX inspector to preserve the PR cap and complete test
    coverage. Cost: another review cycle; Settings remains explicitly unavailable until its
    delivery.
13. Retain the default Next build because it works in the intended container. Document the explicit
    Webpack flag only for the diagnosed host restriction. Cost: affected host developers need the
    fallback flag.
14. Restore the fixed Google account chooser in a bounded OIDC follow-up so retry can select a
    different account. Cost: one chooser step per login and an additional reviewed PR.
15. Split the shared request-timeout correction into a prerequisite PR because its cancellation
    regressions put the shell over the cap. Cost: an additional review cycle and refreshing the
    shell branch after the dependency merge.
16. Start the FX inspector with an explicit date choice instead of a fixed historical sample date.
    Cost: one date-selection action before the first lookup; the view does not imply an arbitrary
    old snapshot is the default.
