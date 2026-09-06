# Phase 0 Foundations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the compose stack, the first four migrations, the Python service with its authorization boundary, the FX rate pipeline, and a Next.js shell that formats money correctly — so that M1 has somewhere to land.

**Architecture:** One `docker compose` stack: `web` (Next.js, renders only), `api` (FastAPI, owns all business logic and is the authorization boundary), `db` (Postgres 18, holds the truth and the invariants). Migrations are forward-only SQL. Every route depends on `require_owner()`; every screen formats money through `formatMoney()`.

**Tech Stack:** Docker Compose · Postgres 18 · Python 3.14 · FastAPI · Pydantic · `pdfplumber` · uv · ruff · mypy --strict · pytest · hypothesis · Next.js App Router · TypeScript · Tailwind · `decimal.js` · vitest

**Spec:** [`docs/superpowers/phase-0-foundations.md`](./phase-0-foundations.md) — 11 tasks, 73 steps, every line of code and every verification command. **This plan does not restate that code.** It decides how the 11 tasks become reviewable pull requests, what gates each one, and what "done" means. Executors read both: the spec for *what to type*, this plan for *what to ship and when*.

Supporting specifications: [architecture](../design/architecture.html), [data model](../design/data-model.html), [multi-currency and FX](../design/multi-currency-and-fx.html), [security and privacy](../operations/security-and-privacy.html), [schema reference](./schema.md).

---

## Current execution status — 6 September 2026

GitHub PRs **#3–#17 are merged** into `feature/phase-0-foundations`. Original Tasks
**1–11 are implemented**, including verified Google OIDC, revocable sessions, exact money display,
configured navigation and the Settings FX inspector. The completion deliveries were split into
bounded PRs, including the request-timeout prerequisite and account-chooser follow-up.

Phase 0 remains **in progress pending live Google identity verification**. Automated and
synthetic-session evidence is recorded in the [verification report](reports/2026-09-06-phase-0-verification.md).
Do not restart delivered work because historical instructional checkboxes below remain blank.
The [completion specification](specs/2026-09-06-phase-0-completion.md) supersedes conflicting old
snippets, including cookie-only logout and floating-point formatting. The numbered PR headings
below are original ordinal groups, not GitHub PR numbers. Integration-to-main requires a separate
owner request.

The [current master](plans/implementation-plan.md) carries the revised product
sequence. All new implementation plans go in `docs/superpowers/plans/`. This existing Phase 0
plan stays here for continuity, and Phase 0 scope remains unchanged.


## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the spec.

- **Postgres 18**, **Python 3.14** (`requires-python = ">=3.14"`), **Node 24 LTS**.
- **Money is `NUMERIC` in Postgres and `Decimal` in Python. Never `float`, `real` or `double precision`.** Over the wire it is a **string**. Enforced by lint and by Pydantic validators.
- **Migrations are forward-only, applied by Flyway** ([ADR-0015](../adr/0015-flyway-for-schema-migrations.html)), pinned to `flyway/flyway:13.5.0-alpine`. No down-migrations, and never edit an applied migration — Flyway checksums them. A schema change edits `docs/superpowers/schema.md` **and** `docs/design/data-model.html` in the same commit.
- **No real financial data enters the repository, ever.** `pdf-statements/`, `*.pdf` and `.env` are git-ignored; `scripts/check-no-real-data.sh` runs in pre-commit and in CI.
- **No secret is ever prefixed `NEXT_PUBLIC_`.** `NEXT_PUBLIC_API_URL` is the only variable carrying the prefix, and it holds a URL.
- **No bank name appears as a code identifier, enum value or required seed row.** Institutions, layouts, categories and rules are rows.
- **The reporting pair is configuration** — `household.base_currency` and `household.secondary_currency`. No currency code appears in a column name, enum or constraint.
- **The API service is the authorization boundary.** RLS stays enabled as defence in depth; the service connects privileged and bypasses it. Every route depends on `require_owner()`.
- **History anchor: 01 Jan 2024. Reporting year: calendar.**
- Verified environment facts, checked 2026-09-05, do not re-derive: `api.frankfurter.app` is **dead**; the live endpoint is `https://api.frankfurter.dev/v1/`. Frankfurter omits non-publication days entirely and snaps a range start backward to the previous publication day.

---

## Branch and review workflow

**Branch names** carry a type prefix: `feature/`, `fix/`, `docs/`, `chore/`.

```
main
 └── feature/phase-0-foundations               integration branch for the whole phase
      ├── feature/phase-0-compose-and-secret-scanning
      ├── feature/phase-0-reference-data-and-tenancy
      ├── feature/phase-0-accounts-and-fx-tables
      ├── feature/phase-0-api-authorization-boundary
      ├── feature/phase-0-fx-rate-pipeline
      ├── feature/phase-0-web-shell
      └── feature/phase-0-google-oidc-and-money-format
```

A branch name should say what is inside it. `feature/phase-0-fx-rate-pipeline` tells a reviewer what
they are about to read; `feature/phase0-5` does not.

Task branches are cut **from `feature/phase-0-foundations`** and merged **back into it**. `feature/phase-0-foundations` goes to `main` as one PR when the phase is complete and every invariant test passes.

**Current checkout, sequential work.** Use the active repository root, resolved with
`git rev-parse --show-toplevel`; do not create Git worktrees or substitute another host's path.
This owner preference supersedes skill defaults. Each task plan or spec must declare
the exact workspace, branch and base/PR target before agent dispatch, including separate branch
names for split deliveries. The orchestrator owns branch switches and preserves unrelated local
changes. Finish the active implementer and any reviewer using the checkout before switching it.

**Use the PR template.** The orchestrator fills
[.github/PULL_REQUEST_TEMPLATE.md](../../.github/PULL_REQUEST_TEMPLATE.md) with what changed,
what was implemented, actual verification commands/results and relevant notes or remaining gates.
Do not copy a generic success claim or mark checks passed without evidence. Send Cloud findings
to the same implementer on the same branch. After approval and merge, synchronize the integration
branch in this checkout before dispatching the next task. Integration-to-main requires a separate
owner request even when phase checks pass.

**The review gate.** Every PR waits for **Codex review on GitHub**. Merge only after Codex approves. If Codex raises findings, address them on the same branch and let it re-review — do not merge past an open finding, and do not close and reopen to dodge one.

**The 500-line cap counts code, and only code.** No pull request exceeds **500 lines of actual
implementation and test code**. These do not count against it:

- Markdown and HTML documentation, including this plan and the spec.
- `package.json`, `pyproject.toml`, `docker-compose.yml`, `.env.example` and other configuration.
- Generated files — `web/package-lock.json`, `api/uv.lock`, the `create-next-app` scaffold.

The cap exists so a reviewer can hold the whole change in their head. A lockfile does not consume
that budget because nobody reads it; a hundred lines of migration does, because somebody must.

> **The generated scaffold still needs its own PR.** `npx create-next-app` writes thousands of lines
in one command, and even uncounted they drown a diff. PR 6 exists to absorb it and carries no
hand-written logic beyond configuration — its body says so, and it is reviewed by skimming what was
configured rather than by reading it.

**Commit convention**, matching the existing history: a lowercase type prefix, a subject that states what changed rather than what was done, and a body that explains why when the subject cannot.

```
feat(api): ECB rate source, peg derivation and carry-forward
fix(db): account_class trigger fired on insert but not on update
docs: the reporting currency pair becomes configuration
chore: pin gitleaks to v8.30.1
```

---

## Testing discipline, and why the hooks matter more than the CI

**Test-driven, and the spec already assumes it.** Every task in
[`phase-0-foundations.md`](./phase-0-foundations.md) is shaped the same way: write the
failing test, run it and watch it fail *for the right reason*, write the minimal implementation, run
it again. That is not decoration. In a ledger the tests **are** the invariants — a statement that
doesn't balance doesn't import, a transfer is never spending, last March's total does not move — and
a test written after the implementation tends to assert what the code does rather than what the
product requires. Writing it first is what keeps the invariant in charge.

The step that is easiest to skip and least safe to skip is *running the test before the code
exists*. A test that has never failed has never been shown to test anything.

**The hooks, not the pull request, are where checks belong.** `.pre-commit-config.yaml` is installed
on this branch already. Install it once in a fresh clone:

```bash
pre-commit install --install-hooks -t pre-commit -t pre-push
```

| Stage | What runs |
|---|---|
| **commit** | gitleaks · merge-conflict, case-conflict, yaml/json/toml, large files · whitespace on code only · no real financial data tracked · `scripts/check-docs.py` · `ruff check --fix` · `ruff format` · `mypy --strict` · `eslint` · `tsc --noEmit` |
| **push** | `pytest` · `vitest` · every `db/tests/*.sql` against a live database |

`api/` and `web/` do not exist until PR 4 and PR 6, so every hook targeting them exits cleanly until
they arrive. The database hook is the one that bites: once `db/tests/` has files in it, a push with
no reachable database **fails** and tells you to run `docker compose up -d db`. That is deliberate —
an invariant proven only in CI is proven too late.

A failing push is not a reason to reach for `--no-verify`. Fix it, then push.

---

## PR decomposition

Eleven spec tasks become **seven pull requests**. No task in the spec exceeds 173 lines of code, so the cap permits grouping; the groupings below are drawn where a reviewer could meaningfully reject one and approve its neighbour.

| PR | Branch | Spec tasks | Hand-written lines | Gate |
|---|---|---|---|---|
| 1 | `feature/phase-0-compose-and-secret-scanning` | 1 | ~101 | compose up, secret scan catches a planted key |
| 2 | `feature/phase-0-reference-data-and-tenancy` | 2, 3 | ~203 | `0001`, `0002` tests pass; anon reads zero rows |
| 3 | `feature/phase-0-accounts-and-fx-tables` | 4, 5 | ~201 | `0003`, `0004` tests pass |
| 4 | `feature/phase-0-api-authorization-boundary` | 6 | ~141 | health 200, whoami 401 then 403 |
| 5 | `feature/phase-0-fx-rate-pipeline` | 7, 8 | ~317 | peg identity to 8 dp; backfill writes provenance |
| 6 | `feature/phase-0-web-shell` | 9 | ~55 + generated | `vitest run` and `next build` pass; no secret in bundle |
| 7 | `feature/phase-0-google-oidc-and-money-format` | 10, 11 | ~307 | sign-in end to end both ways; money formats both currencies |

---

### PR 1: Repository skeleton, tooling and secret hygiene

**Branch:** `feature/phase-0-compose-and-secret-scanning` · **Spec:** Task 1, steps 1–7 · **~101 lines**

**Files:**
- Create: `.env.example`, `docker-compose.yml` (including the pinned `flyway` service)
- Create: `db/migrations/.gitkeep`, `db/seeds/.gitkeep`, `db/functions/.gitkeep`, `db/tests/.gitkeep`
- Modify: `.gitignore`

> `.pre-commit-config.yaml`, `scripts/check-no-real-data.sh` and `scripts/hooks/` already exist on
> `feature/phase-0-foundations` — they were installed before the first task branch was cut, so every
> branch inherits them. Spec Task 1 Steps 5 and 6 are therefore already satisfied; run
> `pre-commit run --all-files` to confirm rather than recreating the files.

**Interfaces:**
- Consumes: nothing.
- Produces: the compose stack, with Postgres reachable at `postgresql://oneledger:oneledger@127.0.0.1:5432/oneledger`. **Every later task connects to that URL.**

- [ ] **Step 1: Cut the branch**

```bash
git checkout feature/phase-0-foundations && git pull --ff-only
git checkout -b feature/phase-0-compose-and-secret-scanning
```

- [ ] **Step 2: Work spec Task 1, steps 1–7 in order**

Each spec step carries the file content and the command that verifies it. Do not skip the verification commands — they are the only thing standing between a plausible-looking file and a working one.

- [ ] **Step 3: Prove the secret scan actually catches something**

The hooks are installed, but nobody has yet watched them refuse a commit. Plant a JWT-shaped value
and confirm `gitleaks` fails, then remove it and confirm the run is clean. A secret scanner that has
never caught a planted key is a hypothesis, not a control.

```bash
printf 'LEAKED_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.fake\n' > leak.env
git add leak.env && pre-commit run --all-files    # expect: gitleaks FAILS
git reset leak.env && rm leak.env
```

- [ ] **Step 4: Verify the whole PR**

```bash
docker compose up -d db
docker compose exec db pg_isready -U oneledger    # accepting connections
./scripts/check-no-real-data.sh                   # OK
```

- [ ] **Step 5: Open the PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

### PR 2: Migrations 0001–0002 — reference data, identity and RLS

**Branch:** `feature/phase-0-reference-data-and-tenancy` · **Spec:** Tasks 2 and 3 · **~203 lines**

**Files:**
- Create: `db/migrations/V1__extensions_and_reference.sql`, `db/seeds/institutions.sql`, `db/tests/0001_reference.sql`
- Create: `db/migrations/V2__identity_and_rls.sql`, `db/tests/0002_rls.sql`
- Create: the household/person/app_user seed (spec Task 3 Step 4c)

**Interfaces:**
- Consumes: a running local Postgres from PR 1.
- Produces: `currency(code CHAR(3) PK, name, symbol, decimals, number_format, pegged_to, peg_rate)`; `institution(id UUID PK, household_id, name, short_name, country, institution_type, …)`; `household(id, name, base_currency, secondary_currency)`; `person(id, household_id, …)`; `app_user(id, household_id, person_id, email, role)`; and the SQL function **`current_household_id() RETURNS UUID`**, which every RLS policy from here on calls.

**Why these two ship together:** `V2` adds the foreign key from `institution.household_id` to `household(id)` that `V1` deliberately leaves dangling. Reviewing `V1` alone means reviewing a schema with a knowingly incomplete constraint.

**This PR also seeds the household.** It moved here from Task 10 because PR 5's
`reporting_config()` reads the household's reporting pair and raises without a row — PR 5 would
otherwise depend on PR 7. The seed reads `.env` and needs nothing from Google.

- [ ] **Step 1: Cut the branch from `feature/phase-0-foundations`**
- [ ] **Step 2: Work spec Task 2, then spec Task 3, in order**

Both follow the same shape: write the failing test, run it and watch it fail for the right reason, write the migration, run it again.

- [ ] **Step 3: Verify**

```bash
docker compose run --rm flyway migrate
psql "$DATABASE_URL" -f db/seeds/institutions.sql
psql "$DATABASE_URL" -f db/tests/0001_reference.sql   # no output = pass
psql "$DATABASE_URL" -f db/tests/0002_rls.sql         # no output = pass
```

The `0002` test asserts an unauthenticated role reads **zero rows** from `household`, `person` and `app_user`, and still reads the system institution seeds. A test that passes because the tables are empty proves nothing — the spec seeds a fixture first, and so must you.

- [ ] **Step 4: Confirm the schema docs did not drift**

`docs/superpowers/schema.md` and `docs/design/data-model.html` describe these tables. If what you wrote differs from what they say, one of them is wrong — fix it in this PR.

- [ ] **Step 5: PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

### PR 3: Migrations 0003–0004 — accounts and FX rates

**Branch:** `feature/phase-0-accounts-and-fx-tables` · **Spec:** Tasks 4 and 5 · **~201 lines**

**Files:**
- Create: `db/migrations/V3__accounts.sql`, `db/tests/0003_accounts.sql`
- Create: `db/migrations/V4__fx_rates.sql`, `db/tests/0004_fx.sql`

**Interfaces:**
- Consumes: `household`, `person`, `institution`, `currency`, `current_household_id()`.
- Produces: enums `account_type`, `account_class`; table `account`; trigger **`set_account_class()`** deriving `account_class` from `account_type` on insert **and** update. Table `fx_rate(rate_date, base_currency, quote_currency, rate, source, is_carried_forward, carried_from, fetched_at)` with primary key `(rate_date, base_currency, quote_currency)`, and **`fx_rate_on(base, quote, on_date) RETURNS NUMERIC(20,10)`**. PR 5 populates it; every later milestone reads it.

- [ ] **Step 1: Cut the branch from `feature/phase-0-foundations`**
- [ ] **Step 2: Work spec Task 4, then spec Task 5**
- [ ] **Step 3: Verify the trigger fires on UPDATE, not only INSERT**

The spec's `0003` test changes an account's type after insert and asserts the derived class changed with it. This is the assertion most likely to be dropped when someone rewrites the test, and the one that catches the real bug.

- [ ] **Step 4: Verify**

```bash
docker compose run --rm flyway migrate
psql "$DATABASE_URL" -f db/tests/0003_accounts.sql
psql "$DATABASE_URL" -f db/tests/0004_fx.sql
```

- [ ] **Step 5: PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

### PR 4: Python service skeleton and the authorization boundary

**Branch:** `feature/phase-0-api-authorization-boundary` · **Spec:** Task 6 · **~141 lines**

**Files:**
- Create: `api/pyproject.toml`, `api/Dockerfile`, `api/oneledger/__init__.py`, `api/oneledger/main.py`, `api/oneledger/auth.py`, `api/oneledger/db.py`
- Test: `api/tests/test_health.py`, `api/tests/test_auth.py`

**Interfaces:**
- Consumes: the `db` service from PR 1; `app_user` from PR 2.
- Produces: a FastAPI app on `:8000`; **`require_owner()`**, returning the caller's `household_id`, which **every route from here on depends on**; **`resolve_owner(claims, lookup)`**, the pure function it wraps, reused by PR 7; **`connect()`** for Postgres access.

**This PR ships alone** because `require_owner()` is the contract the rest of the system is built against. A reviewer should be able to reject the shape of the authorization boundary without also rejecting an FX pipeline.

- [ ] **Step 1: Cut the branch from `feature/phase-0-foundations`**
- [ ] **Step 2: Work spec Task 6, steps 1–8**
- [ ] **Step 3: Verify all three authorization outcomes**

Run spec Task 6 Step 7, which starts the service with the environment loaded and checks all
five outcomes. Do not retype the command here — the copy in this file drifted out of step with
the spec's within one review round the last time it existed, and got the startup wrong.

401 and 403 are different answers to different questions — absent credentials versus present-but-unauthorized. A service that returns 401 for both is wrong, and the test asserts the difference.

- [ ] **Step 4: Run the quality gates that will be in CI**

```bash
cd api && uv run ruff check . && uv run mypy . && uv run pytest -v
```

- [ ] **Step 5: PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

### PR 5: FX pipeline — rate source, peg derivation, backfill

**Branch:** `feature/phase-0-fx-rate-pipeline` · **Spec:** Tasks 7 and 8 · **~317 lines**

**Files:**
- Create: `api/oneledger/fx.py`, `api/oneledger/routes/fx.py`
- Modify: `api/oneledger/main.py`, `api/oneledger/db.py`
- Test: `api/tests/test_fx.py`, `api/tests/test_fx_rows.py`

**Interfaces:**
- Consumes: `require_owner` (PR 4); `fx_rate` and `fx_rate_on` (PR 3); `currency.pegged_to` / `peg_rate` (PR 2).
- Produces: `Peg`; **`units_per_eur(published, code, pegs) -> Decimal`**; **`cross_rate(published, base, quote, pegs) -> Decimal`**; **`EcbFrankfurterSource.fetch_range(start, end, codes) -> dict[date, dict[str, Decimal]]`**; **`carry_forward(published, start, end) -> list[tuple[date, T, date | None]]`**; **`upsert_rates(conn, rows) -> int`**; routes `POST /fx/backfill` and `GET /fx/{date}`.

**Why these ship together:** Task 8's route is the only caller of Task 7's functions. Splitting them produces one PR of pure library code with no user and one PR that is untestable without it.

- [ ] **Step 1: Cut the branch from `feature/phase-0-foundations`**
- [ ] **Step 2: Work spec Task 7, then spec Task 8**
- [ ] **Step 3: Hold the two assertions that matter**

The peg identity must hold to **8 decimal places** — `AED/INR × 3.6725 = USD/INR` for every date. And `carry_forward` must **never interpolate**: a Sunday takes Friday's rate, flagged with its origin, not the average of Friday and Monday. Interpolating would make a Saturday transaction's value depend on what happened the following Monday.

- [ ] **Step 4: Verify against the live endpoint, then against the database**

```bash
cd api && uv run pytest tests/test_fx.py tests/test_fx_rows.py -v   # 5 + 1 passed
curl -s -X POST "localhost:8000/fx/backfill?start=2024-01-01&end=2026-09-05" -b "session=$SESSION"
curl -s "localhost:8000/fx/2026-09-03" -b "session=$SESSION"   # published
curl -s "localhost:8000/fx/2026-09-06" -b "session=$SESSION"   # carried, names its origin
curl -s "localhost:8000/fx/2024-01-01" -b "session=$SESSION"   # the anchor returns a rate
```

- [ ] **Step 5: PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

### PR 6: Next.js scaffold and the typed API client

**Branch:** `feature/phase-0-web-shell` · **Spec:** Task 9 · **~55 hand-written lines + generated scaffold**

**Files:**
- Create: `web/` via `create-next-app`, `web/Dockerfile`, `web/vitest.config.ts`, `web/lib/api/client.ts`
- Test: `web/lib/api/__tests__/env.test.ts`

**Interfaces:**
- Consumes: the service from PR 4.
- Produces: **`apiFetch<T>(path, init)`** — the single way the web app reaches the backend. No component calls `fetch` directly, and nothing in `web/` talks to Postgres.

**Read this PR by skimming.** It carries `package-lock.json` and the `create-next-app` output — thousands of generated lines. The PR body must say so explicitly and list the four files that were actually written by hand. Nothing else lands in this PR.

- [ ] **Step 1: Cut the branch from `feature/phase-0-foundations`**
- [ ] **Step 2: Work spec Task 9, steps 1–6**

`create-next-app` sets `@/*` in `tsconfig.json`, but vitest does not read it — spec Step 3 adds the alias to `vitest.config.ts`. Without it every `@/` import fails at test time only, which is a confusing way to learn this.

- [ ] **Step 3: Verify, including that no secret reached the bundle**

```bash
cd web && npx vitest run && npm run build
grep -rE "SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|postgresql://" .next/static/ \
  && echo "LEAK" || echo "OK: clean bundle"
```

- [ ] **Step 4: Write the PR body**

State the generated-file count, name the four hand-written files, and confirm the bundle grep came back clean.

- [ ] **Step 5: PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

### PR 7: Google OIDC, the seed, money formatting and the toggle

**Branch:** `feature/phase-0-google-oidc-and-money-format` · **Spec:** Tasks 10 and 11 · **~307 lines**

**Files:**
- Create: `api/oneledger/routes/auth.py`, `web/app/login/page.tsx`, `web/app/unauthorized/page.tsx`
- Create: `web/lib/money/format.ts`, `web/components/currency-toggle.tsx`
- Modify: `api/oneledger/main.py`, `web/app/layout.tsx`
- Test: `api/tests/test_oidc.py`, `web/lib/money/__tests__/format.test.ts`

**Interfaces:**
- Consumes: `resolve_owner` and `require_owner` (PR 4); `app_user` (PR 2); `apiFetch` (PR 6).
- Produces: `GET /auth/login`, `GET /auth/callback`, `POST /auth/logout` — the session cookie every route depends on. **`formatMoney(value: string, currency: Currency, opts?): string`**, where `Currency` is the row shape `{ code, symbol, decimals, numberFormat }` — every screen from M1 onward formats money through this and nothing else. **No currency code is a type**: a `"INR" | "AED"` union would put the first household's pair into the type system.

**Manual prerequisite, do this before starting:** register both redirect URIs in the Google Cloud console — `http://localhost:8000/auth/callback` and `https://<your-hostname>/auth/callback` — and put the client id and secret in `.env`. Deploying later then needs no console visit. This is the only step in Phase 0 that cannot be completed from the terminal, and it will block PR 7's verification if left until the end.

- [ ] **Step 1: Cut the branch from `feature/phase-0-foundations`**
- [ ] **Step 2: Work spec Task 10, steps 1–8**

The household was seeded in PR 2; Step 6 only confirms the row is there.

- [ ] **Step 3: Verify sign-in end to end, both ways**

The permitted address lands on `/`, and `GET /whoami` returns the `household_id`. **Any other Google account is rejected even though Google authenticated it successfully** — that rejection is the whole point of the pinned address, and it must be tested with a second real account, not asserted in a unit test alone.

- [ ] **Step 4: Work spec Task 11, steps 1–8**

`formatMoney` renders INR in the Indian numbering system (`₹1,02,34,567`, with lakh and crore abbreviations) and AED in international grouping (`AED 1,234,567.89`), following `currency.number_format` rather than a hardcoded locale.

- [ ] **Step 5: Verify**

```bash
cd api && uv run pytest tests/test_oidc.py -v    # 4 passed
cd web && npx vitest run && npm run dev
```

- [ ] **Step 6: PR into `feature/phase-0-foundations`, wait for Codex, merge on approval**

---

## Closing the phase

- [ ] **Every checkbox above is ticked and every PR is merged into `feature/phase-0-foundations`.**
- [ ] **Run the whole gate from a clean clone**, because a green suite on a developer machine that has been accumulating state for a week proves less than it appears to:

```bash
/verify
```

**One definition of green, not a second copy of it.** `.claude/skills/verify/SKILL.md` already
runs the docs checks, the secret scan, ruff, mypy, pytest, the migrations-from-empty pass and
the web suite — and it runs the database section inside an isolated `oneledger-verify` project
with a cleanup trap. An abbreviated copy here drifted from it within a single review round:
the teardown had been isolated but the `flyway migrate` and `psql` lines that followed still
pointed at the default project, so it reset a scratch database and then tested the working one.

If you would rather see the commands, read the skill. Do not restate them.

- [ ] **Open `feature/phase-0-foundations` → `main`.** The PR body states what Phase 0 delivered and what M1 may now assume: a database with four migrations and their invariant tests, a service that is the authorization boundary, an FX table covering the full history horizon, and a web shell that formats money correctly.
- [ ] **Wait for Codex, merge on approval.**

## What Phase 0 deliberately does not deliver

No statement parsing, no transactions table, no classification, no transfers, no dashboard. Those are M1 onward. The measure of this phase is that the next one has somewhere to land — not that anything is visible yet.
