# Phase 1 — Trusted Financial Position Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Execution status is maintained only in
> [implementation progress](../progress.md) — never in this file, the roadmap or the README.

**Goal:** Repair the five open defects in the delivered Phase 0 code, move household
configuration out of `.env` into the database and the UI, and deliver M1a — the account
inventory with opening-balance evidence and closed-account history — then sequence M1b to M9b.

**Architecture:** The existing three-service boundary is unchanged: React renders, the Python
service owns domain logic and the authorization boundary, Postgres enforces invariants and does
the aggregation. New multi-step or guarded writes are `SECURITY DEFINER` Postgres functions the
service calls after scoping the connection to the resolved owner; single-row writes are
parameterized statements scoped by an explicit `household_id` predicate.

**Tech Stack:** Postgres 18 with Flyway 13.5.0-alpine, Python 3.14 with FastAPI, psycopg, PyJWT,
ruff and mypy `--strict` under `uv`, Node 24 LTS with Next.js 16 App Router, React 19,
TypeScript, Tailwind and Vitest, Docker Compose.

**Spec:** [Phase 1 specification](../specs/2026-09-06-phase-1-trusted-financial-position.md).
Read it with this plan; it carries the verbatim Codex findings, the reporting-pair rule, the
mockup rulings and the milestone entry criteria. Supporting sources:
[master implementation plan](implementation-plan.md), [roadmap](../../product/roadmap.html),
[finance OS requirements](../../product/finance-os.html),
[UI and information architecture](../../product/ui-information-architecture.html),
[data model](../../design/data-model.html),
[multi-currency and FX](../../design/multi-currency-and-fx.html),
[API contracts](../../design/api-contracts.html),
[security and privacy](../../operations/security-and-privacy.html),
[testing and data quality](../../operations/testing-and-data-quality.html),
[schema reference](../schema.md).

## Global Constraints

Every task's requirements implicitly include this section. Values are copied verbatim from the
master plan's global constraints.

- Postgres 18, Python 3.14 and Node 24 LTS remain the documented baseline.
- Money is `NUMERIC(20,4)` in Postgres, `Decimal` in Python and a string on the wire. FX rates are
  `NUMERIC(20,10)`; units `NUMERIC(28,8)`; prices `NUMERIC(20,6)`; percentages `NUMERIC(9,4)` in
  human-readable units (`15.2500` means 15.25%). No float money or frontend money arithmetic.
  Never `parseFloat` or `Number()` on a money field.
- Business dates are `DATE`; audit timestamps are `TIMESTAMPTZ` in UTC. Reporting year is calendar.
- Reporting currencies are configuration. Preserve frozen historical amounts and the
  **01 Jan 2024 history anchor** (ADR-0002 / ADR-0011).
- Consumption, budget and spending reports use `v_transaction_reportable` and frozen amounts.
  Reports never read the base transaction table and never multiply an amount by a rate.
- Every household-scoped table has household identity and RLS. The API owner check —
  `require_owner()` — remains the authorization boundary. `/health`, `GET /auth/login` and
  `GET /auth/callback` are the complete public set. Every `SECURITY DEFINER` function calls
  `assert_is_owner()` first. No financial data, passwords or secrets enter the repository or logs.
- Migrations are forward-only and applied by Flyway. Never rewrite applied migrations. A table
  change edits the migration, `docs/superpowers/schema.md` and `docs/design/data-model.html` in
  one commit.
- No institution, country or currency becomes a hardcoded domain identifier. Reuse existing
  patterns. Tests use the reserved synthetic codes already in use (`XIN`, `XAE`, `XA`, `XB`) or
  read real codes from the `currency` table.
- No LLM calls in Phase 1 or Phase 2. No automatic payments, trades or new shared access.
- Read ADRs as decisions, design/operations as specifications and plans as execution
  instructions. Resolve conflicting specifications explicitly before implementing.
- At most **500 lines of implementation and test code per PR** (documentation, configuration and
  generated files excluded); split a task into sequential coherent PRs if required.
- Every PR is created from [.github/PULL_REQUEST_TEMPLATE.md](../../../.github/PULL_REQUEST_TEMPLATE.md)
  with the actual change, implementation detail and honest check results — record failures,
  skipped checks and reasons; never pre-check success. Every PR waits for Codex review on GitHub
  and merges only on approval. Findings go to the same implementer on the same branch.
- Never weaken an invariant, loosen an assertion or delete a test to reach green. A genuinely
  wrong invariant is a superseding ADR, not an edit.
- Hooks are the gate. `pre-commit install --install-hooks -t pre-commit -t pre-push` once;
  never `--no-verify`.
- Branching, phase integration and releases follow
  [docs/git-workflow.md](../../git-workflow.md); this plan's branch table conforms to it.

## Workspace and branches

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` (confirm with `git rev-parse --show-toplevel`;
never substitute a path from another machine). Do **not** create a Git worktree — this owner
preference overrides the skill default. Only one implementation agent works at a time; wait for
any reviewer using this checkout before switching branches.

**Branching follows [docs/git-workflow.md](../../git-workflow.md).** The Phase 1 integration
branch is `feature/phase-1-trusted-financial-position`: every task below branches from it and
PRs back into it, named `<type>/task-<n>-<description>` after its task number. When the phase's
exit gates are verified and recorded in the progress file, the phase branch is PRed into
`develop` on a separate owner request; `develop` reaches `main` only through a `release/*`
branch carrying the changelog, release notes and tag.

| Task | Branch | Base and PR target |
|---|---|---|
| 1 | `fix/task-1-api-import-syntax` | `feature/phase-1-trusted-financial-position` |
| 2 | `fix/task-2-db-tests-currency-agnostic` | `feature/phase-1-trusted-financial-position` |
| 3 | `fix/task-3-account-opening-balance` | `feature/phase-1-trusted-financial-position` |
| 4 | `fix/task-4-auth-database-errors` | `feature/phase-1-trusted-financial-position` |
| 5 | `chore/task-5-compose-database-identity` | `feature/phase-1-trusted-financial-position` |
| 6 | `feature/task-6-household-configuration-api` | `feature/phase-1-trusted-financial-position` |
| 7 | `feature/task-7-household-settings-ui` | `feature/phase-1-trusted-financial-position` |
| 8 | `feature/task-8-account-inventory-schema` | `feature/phase-1-trusted-financial-position` |
| 9 | `feature/task-9-account-inventory-api` | `feature/phase-1-trusted-financial-position` |
| 10 | `feature/task-10-account-inventory-ui` | `feature/phase-1-trusted-financial-position` |
| 11 | `docs/task-11-phase-1-progress` | `feature/phase-1-trusted-financial-position` |

Tasks run in order. Tasks 2 and 3 both touch `db/tests/0003_accounts.sql`; merge 2 before
starting 3. Task 1 unblocks every Python check and must merge first.

## Migration numbering

Applied migrations are V1–V5. This plan adds **V6** (Task 3), **V7** (Task 6) and **V8**
(Task 8). The planned migration list in `docs/design/data-model.html` §17 currently assigns
V6–V18 to later feature work; Task 3 renumbers that planned list once, shifting
`categories_and_merchants` to V9 and everything after it by three, and reserving V6–V8 for the
work in this plan. Later tasks then fill their reserved slots without renumbering again.

## Preflight rulings

| Area | Conflict found | Resolution |
|---|---|---|
| API package | `api/oneledger/routes/auth.py:74` is Python 2 `except` syntax; the package cannot be imported on `main`, `develop` or this branch, so no Python check can run | Task 1 fixes it first and adds the test that exercises the clause |
| `db/tests/0003`, `0005` | Both insert `household(base_currency, secondary_currency)` as literals with a foreign key to `currency(code)`, making those two currencies required seed rows — the same ADR-0012 violation Codex raised for `0001` | Task 2 fixes all three; the fixtures select their pair from `currency`. `0004` is already compliant and is not touched |
| Roadmap M1 vs. master-plan M1 | Roadmap M1 includes manual transactions, the seed taxonomy and the transaction list; the master plan's M1 bullet and the owner's scheduling cover only the account inventory | Split into M1a (Tasks 8–10) and M1b (its own dated plan). Roadmap M1's *Done when* list is satisfied only when both are complete; the progress file records it that way. No acceptance criterion is dropped |
| Accounts grouping | The Accounts artboard groups by country; UI §6 specifies "grouped by class, then country" and states that document wins over the mockups | Class first, then country (Task 10) |
| Converted balance totals | The Accounts artboard shows a single converted group total; translating balances needs an as-of-date rate and a coverage label, which is M9a's contract | M1a shows native balances and per-currency subtotals with their as-of dates; no untagged converted total |
| Error envelope | API contracts §6 specifies `{"error": {"code", "message", "detail"}}`; every delivered route uses FastAPI's `{"detail": …}` | The typed envelope arrives with the first route that needs machine-readable failure classes — the import path (`RECONCILIATION_FAILED`), which is the doc's own example. Until then `PATCH /household` distinguishes cases by status code: 409 locked, 422 invalid, 403 unscoped |
| Account number hash | `account_number_hash` matches an incoming statement to an account, which needs the full number once | M1a stores the mask only and leaves the hash NULL; M2 designs the matching flow and pays that cost. No full account number is accepted, transmitted or logged in this plan |
| First-login onboarding | `require_owner()` needs `app_user` → `person` → `household`, so no authenticated moment exists before the household does | V7 seeds one neutral household and person; the owner edits them in Settings. `scripts/seed-household.sh` seeds only the `app_user` row from `ALLOWED_USER_EMAIL` |

---

### Task 1: Restore the API package to importable

**Files:**
- Modify: `api/oneledger/routes/auth.py:74`
- Test: `api/tests/test_oidc.py`

**Interfaces:**
- Consumes: `auth.create_oidc_flow()`, `auth.session_secret()`, `auth_routes._config()`.
- Produces: nothing new. Every later task depends on `pytest` being able to collect.

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_oidc.py`:

```python
@pytest.mark.parametrize(
    "missing", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"]
)
def test_login_without_google_configuration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv(missing, "")
    response = TestClient(app).get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json() == {"detail": "sign-in unavailable"}
    assert "oidc_flow" not in response.cookies


def test_login_without_a_session_secret_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RuntimeError arm of the handler: session_secret() raises when the key is unset."""
    monkeypatch.setenv("SESSION_SECRET", "")
    response = TestClient(app).get("/auth/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json() == {"detail": "sign-in unavailable"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/sandjaieravi/dev/OneLedger/api && uv run pytest -q`
Expected: collection fails, not a test failure —
`File ".../api/oneledger/routes/auth.py", line 74` … `except OAuthExchangeError, RuntimeError:` …
`SyntaxError: invalid syntax`. Confirm `uv run ruff check .` reports the same parse error.

- [ ] **Step 3: Write the minimal implementation**

In `api/oneledger/routes/auth.py`, line 74:

```python
    except (OAuthExchangeError, RuntimeError):
```

Both members are load-bearing: `_config()` raises `OAuthExchangeError` for absent Google
configuration and `session_secret()` raises `RuntimeError` when `SESSION_SECRET` is unset. Change
nothing else.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/sandjaieravi/dev/OneLedger/api && uv run ruff check . && uv run mypy . && uv run pytest -q`
Expected: ruff and mypy clean; pytest collects the whole suite and reports **63 passed**
(59 previously reported plus the 4 added here). A run collecting 0 tests is a failure.

- [ ] **Step 5: Commit**

```bash
git add api/oneledger/routes/auth.py api/tests/test_oidc.py
git commit -m "fix(api): the sign-in route handles both unavailable-configuration errors

The except clause used Python 2 tuple syntax, so the routes module raised SyntaxError and the
whole package failed to import: every lint, type and test run died at collection and the service
could not start. The clause catches both arms it always meant to, and the tests now exercise
each one.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: SQL fixtures stop requiring named currencies

**Files:**
- Modify: `db/tests/0001_reference.sql` (replace the assertions)
- Modify: `db/tests/0003_accounts.sql:4-18`
- Modify: `db/tests/0005_sessions.sql:12-13`

**Interfaces:**
- Consumes: the `currency` and `institution` tables from V1.
- Produces: the fixture pattern later SQL tests reuse — `min(code)` and the next code above it,
  read from `currency`, never written as a literal.

- [ ] **Step 1: Write the failing test**

Replace the body of `db/tests/0001_reference.sql`:

```sql
-- db/tests/0001_reference.sql
-- Reference data is asserted by property, never by membership. Naming a currency here would
-- make it a required seed row, and every currency in these documents is an example (ADR-0012):
-- a household reporting in any other pair must pass this suite unchanged.
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM currency) >= 2,
    'a reporting pair needs at least two seeded currencies';
  ASSERT NOT EXISTS (SELECT FROM currency WHERE decimals < 0 OR decimals > 6),
    'currency decimals are a plausible minor-unit scale';
  ASSERT NOT EXISTS (SELECT FROM currency WHERE peg_rate IS NOT NULL AND peg_rate <= 0),
    'a peg rate is positive';
  ASSERT NOT EXISTS (
    SELECT FROM currency c JOIN currency anchor ON anchor.code = c.pegged_to
     WHERE anchor.pegged_to IS NOT NULL),
    'a peg anchor is not itself pegged, so every derivation chain terminates';
  ASSERT (SELECT count(*) FROM institution WHERE household_id IS NULL) >= 6,
    'the shared institution suggestions are seeded';
  RAISE NOTICE 'PASS 0001';
END $$;
```

In `db/tests/0003_accounts.sql`, declare and read the pair, and replace the literals:

```sql
DECLARE h UUID; p UUID; a_card UUID; a_sav UUID; cls account_class;
        base CHAR(3); secondary CHAR(3);
BEGIN
  -- The fixture takes whatever two currencies this deployment seeded. Naming them would make
  -- them required seed rows (ADR-0012). Countries use the ISO user-assigned range for the
  -- same reason.
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('test', base, secondary) RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date, credit_limit)
    VALUES (h,p,'Test Card','credit_card',secondary,'XA',-4500,'2024-01-01',20000)
    RETURNING id INTO a_card;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Test Savings','savings',base,'XB',100000,'2024-01-01')
    RETURNING id INTO a_sav;
```

In `db/tests/0005_sessions.sql`, replace the household insert:

```sql
  WITH pair AS (
    SELECT (SELECT min(code) FROM currency) AS base,
           (SELECT min(code) FROM currency
             WHERE code > (SELECT min(code) FROM currency)) AS secondary
  )
  INSERT INTO household (id, name, base_currency, secondary_currency)
  SELECT '51111111-1111-1111-1111-111111111111', 'session fixture', base, secondary FROM pair;
```

- [ ] **Step 2: Run the tests to verify the old ones failed for the right reason**

Prove the previous assertion was membership-based rather than property-based, in a scratch
database only:

```bash
cd /Users/sandjaieravi/dev/OneLedger
export COMPOSE_PROJECT_NAME=oneledger-verify DB_PORT=55432 \
       POSTGRES_PASSWORD=oneledger POSTGRES_PORT=55432
export DATABASE_URL="$(scripts/dsn.sh)"
trap 'docker compose down -v >/dev/null 2>&1' EXIT
docker compose down -v && docker compose up -d db && docker compose run --rm flyway migrate
for f in db/seeds/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
psql "$DATABASE_URL" -c "DELETE FROM currency WHERE code NOT IN (SELECT code FROM currency LIMIT 2)"
git stash && psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/0001_reference.sql; git stash pop
```

Expected with the old file: `ERROR: expected AED, INR and USD seeded`.
Expected with the new file: `NOTICE: PASS 0001`. `COMPOSE_PROJECT_NAME` is the safety mechanism —
without it `down -v` destroys the working `pgdata` and `documents` volumes.

- [ ] **Step 3: The implementation is the fixture change above**

There is no application code in this task. The three files are the change.

- [ ] **Step 4: Run every SQL suite to verify they pass**

```bash
for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
```

Expected: `PASS 0001`, `PASS 0002 structure`, `PASS 0002 isolation`, `PASS 0003`, `PASS 0004`,
and `0005` completing without error. Then confirm the fixtures no longer name a currency:

```bash
grep -nE "'(AED|INR|USD|EUR|GBP)'" db/tests/0001_reference.sql db/tests/0003_accounts.sql db/tests/0005_sessions.sql
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add db/tests/0001_reference.sql db/tests/0003_accounts.sql db/tests/0005_sessions.sql
git commit -m "test(db): the SQL fixtures require no currency by name

0001 required AED, INR and USD as seeded rows, and 0003 and 0005 required INR and AED through
the household foreign key. That made three currencies part of the schema contract, which
ADR-0012 forbids: every currency in these documents is an example. The fixtures now assert
reference-data and peg properties and read their pair from the currency table.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: A new account's current balance starts at its opening balance

**Files:**
- Create: `db/migrations/V6__account_current_balance_starts_at_opening.sql`
- Modify: `db/tests/0003_accounts.sql` (add assertions after the existing `is_liquid` check)
- Modify: `docs/superpowers/schema.md` (the `0003` accounts section and the numbering note)
- Modify: `docs/design/data-model.html` (§5 Accounts, §17 the planned migration list)

**Interfaces:**
- Consumes: the `account` table and the `trg_account_class` trigger from V3.
- Produces: `derive_account_current_balance()` and `trg_account_current_balance`. Later balance
  work replaces the trigger body in its own forward migration; it never edits V6.

- [ ] **Step 1: Write the failing test**

Insert into `db/tests/0003_accounts.sql`, immediately before `RAISE NOTICE 'PASS 0003'`:

```sql
  -- balance = opening_balance + SUM(transactions). No transaction table exists yet, so a new
  -- account's current balance is its opening balance, dated to the opening balance date.
  ASSERT (SELECT current_balance FROM account WHERE id = a_sav) = 100000,
    'an omitted current_balance starts at the opening balance';
  ASSERT (SELECT current_balance FROM account WHERE id = a_card) = -4500,
    'a liability account starts at its signed opening balance';
  ASSERT (SELECT current_balance_as_of FROM account WHERE id = a_sav)
           = '2024-01-01'::timestamptz,
    'a derived balance carries the date it is true for';

  -- An explicit value is still the caller's, including a deliberate zero.
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date, current_balance)
    VALUES (h,p,'Explicitly Zero','savings',base,'XB',100000,'2024-01-01',0);
  ASSERT (SELECT current_balance FROM account WHERE name = 'Explicitly Zero') = 0,
    'an explicit current_balance is never overwritten';
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/0003_accounts.sql
```

Expected: `ERROR: an omitted current_balance starts at the opening balance` — the row is created
with `current_balance = 0` because `DEFAULT 0` fires.

- [ ] **Step 3: Write the minimal implementation**

Create `db/migrations/V6__account_current_balance_starts_at_opening.sql`. V3 is applied and
Flyway checksums it, so this is a forward migration and V3 is not touched (ADR-0015):

```sql
-- db/migrations/V6__account_current_balance_starts_at_opening.sql
-- current_balance is materialised: opening_balance + SUM(transactions). DEFAULT 0 made "the
-- caller did not supply one" indistinguishable from "the caller means zero", so an account
-- created with a nonzero opening balance started at zero and contradicted its own documented
-- equation. Dropping the default lets NULL mean "derive it"; a BEFORE row trigger fills the
-- column before the NOT NULL check runs, so the column stays NOT NULL.
ALTER TABLE account ALTER COLUMN current_balance DROP DEFAULT;

CREATE FUNCTION derive_account_current_balance() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.current_balance IS NULL THEN
    NEW.current_balance := NEW.opening_balance;
    -- The materialised figure carries the date it is actually true for. Nothing derived is
    -- presented as if observed (Invariant I7).
    NEW.current_balance_as_of := COALESCE(NEW.current_balance_as_of,
                                          NEW.opening_balance_date::timestamptz);
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_current_balance
  BEFORE INSERT ON account
  FOR EACH ROW EXECUTE FUNCTION derive_account_current_balance();

-- Existing rows predate the trigger. No table carrying transactions exists at this point in the
-- schema timeline, so opening_balance + SUM(transactions) is exactly opening_balance for all
-- of them.
UPDATE account
   SET current_balance = opening_balance,
       current_balance_as_of = COALESCE(current_balance_as_of,
                                        opening_balance_date::timestamptz)
 WHERE current_balance = 0 AND opening_balance <> 0;
```

In `docs/superpowers/schema.md`, change the `current_balance` line of the `0003` account DDL to
`current_balance NUMERIC(20,4) NOT NULL,  -- materialised; starts at opening_balance (V6)` and
update the numbering note to record that V6–V8 are applied Phase 1 migrations and the unapplied
reference sequence is unchanged. In `docs/design/data-model.html` §5, add the sentence that
`current_balance` defaults to the opening balance on insert and carries
`current_balance_as_of`; in §17, renumber the planned list so
`V9__categories_and_merchants.sql` follows the three Phase 1 migrations, shifting every later
entry by three.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose run --rm flyway migrate
for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
python3 scripts/check-docs.py
```

Expected: Flyway reports `Successfully applied 1 migration` (V6); `PASS 0003`; every other suite
still passes; `check-docs.py` reports `ok` for all four checks. Then re-run migrations from empty
in the scratch project to prove V1→V6 applies cleanly to a fresh database.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/V6__account_current_balance_starts_at_opening.sql \
        db/tests/0003_accounts.sql docs/superpowers/schema.md docs/design/data-model.html
git commit -m "fix(db): a new account's current balance starts at its opening balance

An account created with a nonzero opening balance and no explicit current balance was stored
with a current balance of zero, contradicting balance = opening_balance + SUM(transactions).
DEFAULT 0 was the cause: it hid the difference between an omitted value and a deliberate zero.
V6 drops the default and derives the balance and its as-of date on insert.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Database failures are not authorization denials

**Files:**
- Modify: `api/oneledger/auth.py:143-164` (`bind_google_owner`)
- Test: `api/tests/test_oidc.py`

**Interfaces:**
- Consumes: `connect()`, `_allowed_email()`.
- Produces: `bind_google_owner(subject, email) -> UUID` raising `NotAuthorized` **only** for a
  non-permitted address, no unique permitted owner, or a conflicting `google_subject`; every
  database exception propagates unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `api/tests/test_oidc.py` (`import psycopg` at the top):

```python
def test_owner_binding_lets_database_failures_through(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable() -> object:
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(auth, "connect", unreachable, raising=False)
    with pytest.raises(psycopg.OperationalError):
        auth.bind_google_owner("google-subject", EMAIL)


def test_a_database_outage_during_binding_offers_retry_not_rejection(
    monkeypatch: pytest.MonkeyPatch, google_keys: tuple[RSAPrivateKey, dict[str, Any]]
) -> None:
    """A correctly permitted owner must never be told their account is not allowed because the
    database was down. /unauthorized is a verdict about identity; this is an outage."""
    private_key, jwk = google_keys
    client = TestClient(app)
    login = client.get("/auth/login", follow_redirects=False)
    flow = decode_flow(login.cookies["oidc_flow"])
    token = sign_google_token(private_key, nonce=flow["nonce"])

    monkeypatch.setattr(auth_routes, "_exchange_code", lambda *_: token)
    monkeypatch.setattr(auth, "_google_jwks_client", lambda: StaticJwksClient(jwk))
    monkeypatch.setattr(
        auth, "connect", lambda: (_ for _ in ()).throw(psycopg.OperationalError("down"))
    )

    response = client.get(
        f"/auth/callback?code={SENTINEL}&state={flow['state']}", follow_redirects=False
    )

    assert response.headers["location"] == (
        "http://localhost:3000/login?reason=authentication_failed"
    )
    assert not any(c.startswith("session=") for c in response.headers.get_list("set-cookie"))
    assert SENTINEL not in response.text
```

The existing `test_identity_or_database_denial_gets_no_session` keeps both of its cases: a
non-permitted address still redirects to `/unauthorized`. Do not weaken it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/sandjaieravi/dev/OneLedger/api && uv run pytest -q -k "binding or outage" -v`
Expected: `test_owner_binding_lets_database_failures_through` FAILS with
`DID NOT RAISE <class 'psycopg.OperationalError'>` (it raises `NotAuthorized` instead), and
`test_a_database_outage_during_binding_offers_retry_not_rejection` FAILS on the location
assertion — actual `http://localhost:3000/unauthorized`.

- [ ] **Step 3: Write the minimal implementation**

In `api/oneledger/auth.py`, delete the broad handler around the `connect()` block in
`bind_google_owner` so the body reads:

```python
def bind_google_owner(subject: str, email: str) -> UUID:
    normalized = email.strip().lower()
    if normalized != _allowed_email():
        raise NotAuthorized("address is not the permitted account")
    # A database failure is not a verdict about this identity. Converting one into NotAuthorized
    # sent a correctly configured owner to /unauthorized instead of the retry path; the callback
    # already routes unexpected exceptions to retry, exactly as it does for issue_session.
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """WITH candidate AS (
                 SELECT id FROM app_user
                  WHERE role = 'owner' AND lower(btrim(email)) = %s
               ), eligible AS (
                 SELECT id FROM candidate WHERE (SELECT count(*) FROM candidate) = 1
               )
               UPDATE app_user u SET google_subject = COALESCE(u.google_subject, %s)
                FROM eligible e
               WHERE u.id = e.id
                 AND (u.google_subject IS NULL OR u.google_subject = %s)
               RETURNING u.id""",
            (normalized, subject, subject),
        )
        row = cur.fetchone()
    if row is None:
        raise NotAuthorized("no unique permitted owner binding")
    return UUID(str(row[0]))
```

Change nothing else. `_store_session()` already raises `NotAuthorized` only on a missing row and
lets database errors propagate; `verify_session()` catches only JWT, key, type and value errors,
so a database outage during session lookup surfaces as a server error rather than a 403. Both are
correct and must not regress.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /Users/sandjaieravi/dev/OneLedger/api && uv run ruff check . && uv run mypy . && uv run pytest -q`
Expected: ruff and mypy clean; **65 passed** (63 after Task 1, plus 2 here).

- [ ] **Step 5: Commit**

```bash
git add api/oneledger/auth.py api/tests/test_oidc.py
git commit -m "fix(api): a database outage during owner binding offers a retry, not a rejection

bind_google_owner wrapped its query in a bare except Exception and re-raised NotAuthorized, so
an unreachable database told a correctly permitted owner that their account was not allowed.
Database exceptions now propagate to the callback's retry path; NotAuthorized is reserved for a
non-permitted address, no unique permitted owner and a conflicting subject binding.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Compose honours the configured database identity

**Files:**
- Modify: `docker-compose.yml:6-21` (`db`) and `docker-compose.yml:56-67` (`flyway`)

**Interfaces:**
- Consumes: `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` from the project `.env`, which
  Compose reads for interpolation.
- Produces: no code interface. The `api` service keeps receiving the parts through
  `env_file: .env`.

- [ ] **Step 1: Write the failing check**

There is no unit-test seam for a Compose file, and adding one would be more machinery than the
fix. The runnable check is `docker compose config`, which resolves interpolation:

```bash
cd /Users/sandjaieravi/dev/OneLedger
POSTGRES_USER=ledgerowner POSTGRES_DB=ledgerdb docker compose config \
  | grep -E 'POSTGRES_USER|POSTGRES_DB|FLYWAY_URL|FLYWAY_USER|pg_isready'
```

- [ ] **Step 2: Run the check to verify it fails**

Expected before the fix — the database is created under the hardcoded identity while the API is
told to use the configured one, so the API cannot authenticate:

```text
POSTGRES_USER: oneledger
POSTGRES_DB: oneledger
- pg_isready -U oneledger
FLYWAY_URL: jdbc:postgresql://db:5432/oneledger
FLYWAY_USER: oneledger
```

- [ ] **Step 3: Write the minimal implementation**

```yaml
  db:
    image: postgres:18
    environment:
      # The same three variables the API and Flyway read. Hardcoding them here created the
      # database under one identity while every client was configured with another.
      POSTGRES_USER: ${POSTGRES_USER:-oneledger}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-oneledger}
      POSTGRES_DB: ${POSTGRES_DB:-oneledger}
    ports: ["127.0.0.1:${DB_PORT:-5432}:5432"]
    volumes: ["pgdata:/var/lib/postgresql"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-oneledger} -d ${POSTGRES_DB:-oneledger}"]
      interval: 5s
      retries: 10
```

```yaml
  flyway:
    image: flyway/flyway:13.5.0-alpine
    profiles: ["tools"]
    environment:
      FLYWAY_URL: jdbc:postgresql://db:5432/${POSTGRES_DB:-oneledger}
      FLYWAY_USER: ${POSTGRES_USER:-oneledger}
      FLYWAY_PASSWORD: ${POSTGRES_PASSWORD:-oneledger}
      FLYWAY_LOCATIONS: filesystem:/flyway/sql
      FLYWAY_CONNECT_RETRIES: "20"
    volumes: ["./db/migrations:/flyway/sql:ro"]
    depends_on:
      db: { condition: service_healthy }
```

Keep every existing comment in the file. Do not add `POSTGRES_USER` or `POSTGRES_DB` to the
`api` service's `environment` block: it already receives them through `env_file: .env`, and
restating the password is the duplication `.env.example` exists to prevent.

- [ ] **Step 4: Run the check to verify it passes**

Same command. Expected after the fix:

```text
POSTGRES_USER: ledgerowner
POSTGRES_DB: ledgerdb
- pg_isready -U ledgerowner -d ledgerdb
FLYWAY_URL: jdbc:postgresql://db:5432/ledgerdb
FLYWAY_USER: ledgerowner
```

Then prove it end to end in the scratch project with `POSTGRES_USER=ledgerowner
POSTGRES_DB=ledgerdb`: `docker compose up -d db`, `docker compose run --rm flyway migrate`
(expected: V1–V6 applied), and every `db/tests/*.sql` passing against that DSN. Tear the scratch
stack down with `COMPOSE_PROJECT_NAME` set.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "fix(ci): compose creates the database under the configured identity

The db service hardcoded oneledger/oneledger and Flyway's URL and user restated it, so setting
POSTGRES_USER or POSTGRES_DB in .env produced a database no client could authenticate against.
All three variables are now interpolated with the same fallbacks in the database service, its
healthcheck and the Flyway connection.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Household configuration lives in the database

**Files:**
- Create: `db/migrations/V7__household_configuration.sql`
- Create: `db/tests/0006_household_configuration.sql`
- Create: `api/oneledger/routes/currencies.py`
- Modify: `api/oneledger/db.py` (add `scope_to_owner`)
- Modify: `api/oneledger/routes/household.py` (extend the response; add `PATCH /household`)
- Modify: `api/oneledger/main.py` (register the currencies router)
- Modify: `api/oneledger/routes/fx.py:44` (the "no household row" message)
- Modify: `api/tests/test_household.py`
- Modify: `scripts/seed-household.sh`
- Modify: `.env.example`, `README.md`, `docs/superpowers/schema.md`,
  `docs/design/data-model.html`, `docs/design/api-contracts.html`,
  `docs/superpowers/phase-0-foundations.md` (one pointer line)
- Regenerate: `web/types/api/`

**Interfaces:**
- Consumes: `require_owner()`, `OwnerSession`, `connect()`, `assert_is_owner()`,
  `current_household_id()`.
- Produces:
  - `scope_to_owner(conn: psycopg.Connection, user_id: UUID) -> None`
  - SQL `reporting_pair_is_locked() RETURNS BOOLEAN`
  - SQL `update_household_configuration(p_name TEXT, p_display_name TEXT, p_tax_residency CHAR(2), p_base_currency CHAR(3), p_secondary_currency CHAR(3)) RETURNS VOID`
  - `GET /household` → `HouseholdResponse { id, name, ownerDisplayName, ownerTaxResidency, baseCurrency, secondaryCurrency, reportingPairLocked, currencies: HouseholdCurrency[] }`
  - `PATCH /household` ← `HouseholdUpdate { name, ownerDisplayName, ownerTaxResidency, baseCurrency, secondaryCurrency }` → 204
  - `GET /currencies` → `CurrencyRow[] { code, name, symbol, decimals, numberFormat }`

- [ ] **Step 1: Write the failing database test**

Create `db/tests/0006_household_configuration.sql`:

```sql
-- db/tests/0006_household_configuration.sql
\set ON_ERROR_STOP on
DO $$
BEGIN
  ASSERT (SELECT count(*) FROM household) <= 1,
    'the seed creates at most one household';
  ASSERT NOT EXISTS (
    SELECT FROM household h
     WHERE NOT EXISTS (SELECT FROM currency WHERE code = h.base_currency)
        OR NOT EXISTS (SELECT FROM currency WHERE code = h.secondary_currency)),
    'a seeded reporting pair names seeded currencies';
END $$;

BEGIN;
  WITH pair AS (
    SELECT (SELECT min(code) FROM currency) AS base,
           (SELECT min(code) FROM currency
             WHERE code > (SELECT min(code) FROM currency)) AS secondary
  )
  INSERT INTO household (id, name, base_currency, secondary_currency)
  SELECT '61111111-1111-1111-1111-111111111111', 'config fixture', base, secondary FROM pair;
  INSERT INTO person (id, household_id, display_name, is_self) VALUES
    ('62222222-2222-2222-2222-222222222222',
     '61111111-1111-1111-1111-111111111111', 'fixture owner', true);
  INSERT INTO app_user (id, household_id, person_id, email) VALUES
    ('63333333-3333-3333-3333-333333333333',
     '61111111-1111-1111-1111-111111111111',
     '62222222-2222-2222-2222-222222222222', 'config@example.test');

  DO $$
  DECLARE was_base CHAR(3); was_secondary CHAR(3);
  BEGIN
    SELECT base_currency, secondary_currency INTO was_base, was_secondary
      FROM household WHERE id = '61111111-1111-1111-1111-111111111111';

    -- Authentication is not authorization: an unscoped caller reconfigures nothing.
    BEGIN
      PERFORM update_household_configuration('x','y',NULL, was_base, was_secondary);
      RAISE EXCEPTION 'an unscoped caller must not reconfigure a household';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    PERFORM set_config('app.user_id', '63333333-3333-3333-3333-333333333333', true);

    ASSERT reporting_pair_is_locked() = false,
      'no amount is frozen, so the pair is still editable';

    PERFORM update_household_configuration(
      'Renamed household', 'Renamed owner', 'XA', was_secondary, was_base);
    ASSERT (SELECT name FROM household WHERE id = '61111111-1111-1111-1111-111111111111')
             = 'Renamed household', 'the household name is editable';
    ASSERT (SELECT base_currency FROM household WHERE id = '61111111-1111-1111-1111-111111111111')
             = was_secondary, 'the pair is editable while nothing is frozen';
    ASSERT (SELECT display_name FROM person WHERE id = '62222222-2222-2222-2222-222222222222')
             = 'Renamed owner', 'the owner display name is editable';
    ASSERT (SELECT tax_residency FROM person WHERE id = '62222222-2222-2222-2222-222222222222')
             = 'XA', 'tax residency is editable';

    BEGIN
      PERFORM update_household_configuration('n','o',NULL, was_base, was_base);
      RAISE EXCEPTION 'two identical currencies must be refused';
    EXCEPTION WHEN invalid_parameter_value THEN NULL;
    END;
  END $$;

  -- A table shaped exactly like the ones M2 adds, so the guard is proved rather than assumed.
  -- It lives and dies inside this rolled-back transaction.
  CREATE TABLE frozen_amount_probe (
    household_id UUID NOT NULL,
    amount_base  NUMERIC(20,4) NOT NULL
  );
  INSERT INTO frozen_amount_probe VALUES ('61111111-1111-1111-1111-111111111111', 100.0000);

  DO $$
  DECLARE was_base CHAR(3); was_secondary CHAR(3);
  BEGIN
    PERFORM set_config('app.user_id', '63333333-3333-3333-3333-333333333333', true);
    SELECT base_currency, secondary_currency INTO was_base, was_secondary
      FROM household WHERE id = '61111111-1111-1111-1111-111111111111';

    ASSERT reporting_pair_is_locked() = true, 'a frozen amount locks the reporting pair';

    -- Swapping the two is refused with the rest: amount_base and amount_secondary are frozen
    -- against particular currencies, so exchanging the roles relabels every stored amount.
    BEGIN
      PERFORM update_household_configuration(
        'still editable', 'still editable', NULL, was_secondary, was_base);
      RAISE EXCEPTION 'a frozen pair must not be swapped';
    EXCEPTION WHEN object_in_use THEN NULL;
    END;

    -- The rest of the configuration stays editable while the pair is locked.
    PERFORM update_household_configuration(
      'Renamed again', 'Renamed again', NULL, was_base, was_secondary);
    ASSERT (SELECT name FROM household WHERE id = '61111111-1111-1111-1111-111111111111')
             = 'Renamed again', 'name and person stay editable behind a locked pair';
  END $$;
ROLLBACK;
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/0006_household_configuration.sql
```

Expected: `ERROR: function update_household_configuration(...) does not exist`.

- [ ] **Step 3: Write the migration**

Create `db/migrations/V7__household_configuration.sql`:

```sql
-- db/migrations/V7__household_configuration.sql
-- The household's name, the owner's display name and tax residency, and the reporting pair are
-- user-editable application data, not deployment configuration. They were seeded from five .env
-- variables; they now start neutral here and are edited in Settings > Household.

-- One neutral household and person, so an owner row can exist before the first sign-in:
-- require_owner() resolves app_user -> person -> household, and there is no authenticated
-- moment before all three exist in which an onboarding screen could run.
--
-- No currency code appears here. The pair is the first two seeded currencies by code, and the
-- owner chooses the real one in Settings before the first import (ADR-0002, ADR-0012).
INSERT INTO household (name, base_currency, secondary_currency)
SELECT 'Household',
       (SELECT min(code) FROM currency),
       (SELECT min(code) FROM currency WHERE code > (SELECT min(code) FROM currency))
 WHERE NOT EXISTS (SELECT FROM household)
   AND (SELECT count(*) FROM currency) >= 2;

INSERT INTO person (household_id, display_name, is_self)
SELECT id, 'Owner', true FROM household
 WHERE NOT EXISTS (SELECT FROM person);

-- "Is any amount already frozen against the current pair?", answered from the catalogue rather
-- than a hardcoded table list, so it stays correct when the transaction table arrives in M2
-- without anyone remembering to come back here. Today the set is empty and it answers false.
CREATE FUNCTION reporting_pair_is_locked() RETURNS BOOLEAN
  LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public
AS $$
DECLARE frozen_table TEXT; rows_found BIGINT;
BEGIN
  PERFORM assert_is_owner();
  FOR frozen_table IN
    SELECT c.table_name
      FROM information_schema.columns c
     WHERE c.table_schema = 'public' AND c.column_name = 'amount_base'
       AND EXISTS (SELECT FROM information_schema.columns h
                    WHERE h.table_schema = 'public' AND h.table_name = c.table_name
                      AND h.column_name = 'household_id')
  LOOP
    EXECUTE format(
      'SELECT count(*) FROM public.%I WHERE household_id = current_household_id()',
      frozen_table) INTO rows_found;
    IF rows_found > 0 THEN RETURN true; END IF;
  END LOOP;
  RETURN false;
END $$;

-- The guard and the write are one function because an import could otherwise freeze the first
-- amount between a separate check and a separate UPDATE (api-contracts.html section 3).
CREATE FUNCTION update_household_configuration(
  p_name               TEXT,
  p_display_name       TEXT,
  p_tax_residency      CHAR(2),
  p_base_currency      CHAR(3),
  p_secondary_currency CHAR(3)
) RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
DECLARE h UUID;
BEGIN
  PERFORM assert_is_owner();
  h := current_household_id();

  IF p_base_currency = p_secondary_currency THEN
    RAISE EXCEPTION 'the reporting pair must name two different currencies'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  IF EXISTS (SELECT FROM household
              WHERE id = h
                AND (base_currency, secondary_currency)
                    IS DISTINCT FROM (p_base_currency, p_secondary_currency))
     AND reporting_pair_is_locked() THEN
    RAISE EXCEPTION 'the reporting pair is fixed once amounts are frozen'
      USING ERRCODE = 'object_in_use';
  END IF;

  UPDATE household
     SET name               = p_name,
         base_currency      = p_base_currency,
         secondary_currency = p_secondary_currency
   WHERE id = h;

  UPDATE person
     SET display_name  = p_display_name,
         tax_residency = p_tax_residency
   WHERE household_id = h AND is_self;
END $$;
```

- [ ] **Step 4: Run the database test to verify it passes**

```bash
docker compose run --rm flyway migrate
for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
```

Expected: `Successfully applied 1 migration` (V7); `0006` completes without error; every earlier
suite still passes. Confirm `SELECT name, base_currency, secondary_currency FROM household;`
returns one row named `Household` whose pair is the first two seeded codes.

- [ ] **Step 5: Write the failing API tests**

Add to `api/tests/test_household.py`, and extend the existing fake `Connection` with an
`execute` method that records its parameters:

```python
def test_household_scopes_the_connection_before_reading(monkeypatch: MonkeyPatch) -> None:
    connection = Connection()
    monkeypatch.setattr(household, "connect", lambda: connection, raising=False)
    result = household.fetch_household(owner())
    assert result is not None
    assert result.name == "Fixture household"
    assert result.ownerDisplayName == "Fixture owner"
    assert result.reportingPairLocked is False
    assert connection.scoped == [str(USER_ID)]


def test_updating_the_configuration_passes_every_field_to_the_owner_guarded_function(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = Connection()
    monkeypatch.setattr(household, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch("/household", json=BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert connection.scoped == [str(USER_ID)]
    assert connection.called[-1] == (
        "New name", "New owner", "XA", "XIN", "XAE",
    )


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (psycopg.errors.ObjectInUse, 409),
        (psycopg.errors.InvalidParameterValue, 422),
        (psycopg.errors.ForeignKeyViolation, 422),
        (psycopg.errors.InsufficientPrivilege, 403),
    ],
)
def test_reporting_pair_failures_stay_distinguishable(
    monkeypatch: MonkeyPatch, error: type[Exception], status: int
) -> None:
    """A locked pair is not an invalid one, and neither is a missing authorization scope."""
    def refuse() -> object:
        raise error("refused")

    monkeypatch.setattr(household, "connect", refuse, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch("/household", json=BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status
    if status == 409:
        assert "backfill" in response.json()["detail"]


def test_currencies_come_from_the_table_not_a_hardcoded_list(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(currencies, "connect", lambda: CurrencyConnection(), raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/currencies")
    finally:
        app.dependency_overrides.clear()
    assert [row["code"] for row in response.json()] == ["XAE", "XIN", "XZZ"]
```

with the shared fixtures at module level:

```python
BODY = {
    "name": "New name",
    "ownerDisplayName": "New owner",
    "ownerTaxResidency": "XA",
    "baseCurrency": "XIN",
    "secondaryCurrency": "XAE",
}
# The CSRF middleware only inspects cookie-authenticated mutations; the header is sent so the
# test exercises the same shape the browser will.
ORIGIN = {"Origin": "http://localhost:3000"}
```

- [ ] **Step 6: Run the API tests to verify they fail**

Run: `cd /Users/sandjaieravi/dev/OneLedger/api && uv run pytest -q tests/test_household.py -v`
Expected: FAIL — `AttributeError: module 'oneledger.routes' has no attribute 'currencies'`, and
`405 Method Not Allowed` for the PATCH cases because the route does not exist.

- [ ] **Step 7: Write the service implementation**

`api/oneledger/db.py`:

```python
def scope_to_owner(conn: psycopg.Connection, user_id: UUID) -> None:
    """Identity for the SECURITY DEFINER functions, per transaction (data-model.html section 3).

    set_config() rather than SET LOCAL because SET takes a literal, and a literal assembled from
    a value is string-interpolated SQL. The third argument makes the setting transaction-local,
    so a pooled connection never carries one request's identity into the next.
    """
    conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
```

`api/oneledger/routes/household.py` — extend the response model and the query, and add the
route:

```python
class HouseholdResponse(BaseModel):
    id: UUID
    name: str
    ownerDisplayName: str
    ownerTaxResidency: str | None
    baseCurrency: str = Field(min_length=3, max_length=3)
    secondaryCurrency: str = Field(min_length=3, max_length=3)
    reportingPairLocked: bool
    currencies: list[HouseholdCurrency]


class HouseholdUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ownerDisplayName: str = Field(min_length=1, max_length=120)
    ownerTaxResidency: str | None = Field(default=None, min_length=2, max_length=2)
    baseCurrency: str = Field(min_length=3, max_length=3)
    secondaryCurrency: str = Field(min_length=3, max_length=3)


LOCKED = (
    "The reporting pair is fixed once amounts are frozen. Changing it is a backfill from stored "
    "FX history (ADR-0002), not a settings change."
)


@router.patch("/household", status_code=204)
def update_household(
    body: HouseholdUpdate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> None:
    try:
        with connect() as conn:
            scope_to_owner(conn, owner.user_id)
            conn.execute(
                "SELECT update_household_configuration(%s, %s, %s, %s, %s)",
                (
                    body.name,
                    body.ownerDisplayName,
                    body.ownerTaxResidency,
                    body.baseCurrency,
                    body.secondaryCurrency,
                ),
            )
    except psycopg.errors.ObjectInUse as exc:
        raise HTTPException(status_code=409, detail=LOCKED) from exc
    except (psycopg.errors.InvalidParameterValue, psycopg.errors.ForeignKeyViolation) as exc:
        raise HTTPException(
            status_code=422,
            detail="The reporting pair must name two different seeded currencies.",
        ) from exc
    except psycopg.errors.InsufficientPrivilege as exc:
        raise HTTPException(status_code=403, detail="not authorized") from exc
```

`fetch_household` calls `scope_to_owner(conn, owner.user_id)` before its query, and the query
gains `h.name`, `p.display_name`, `p.tax_residency` and `reporting_pair_is_locked()`, joining
`person p ON p.id = u.person_id`. The `currencies` array keeps its current meaning — exactly the
configured pair, base first — because the header toggle consumes it.

Create `api/oneledger/routes/currencies.py`:

```python
# api/oneledger/routes/currencies.py
"""The currency catalogue. Options come from this table, never from a union type (ADR-0012)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(tags=["reference"])


class CurrencyRow(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    name: str
    symbol: str
    decimals: int = Field(ge=0)
    numberFormat: Literal["international", "indian"]


@router.get("/currencies")
def currencies(owner: Annotated[OwnerSession, Depends(require_owner)]) -> list[CurrencyRow]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code, name, symbol, decimals, number_format FROM currency ORDER BY code"
        )
        rows = cur.fetchall()
    return [
        CurrencyRow(code=code, name=name, symbol=symbol, decimals=decimals, numberFormat=fmt)
        for code, name, symbol, decimals, fmt in rows
    ]
```

Register it in `api/oneledger/main.py`: `from .routes import auth, currencies, fx, household`
and `app.include_router(currencies.router)`.

In `api/oneledger/routes/fx.py`, replace the `reporting_config` error message
`"no household row — run the Task 3 seed before backfilling"` with
`"no household row — apply the migrations: docker compose run --rm flyway migrate"`.

- [ ] **Step 8: Run the API tests to verify they pass**

```bash
cd /Users/sandjaieravi/dev/OneLedger/api && uv run ruff check . && uv run mypy . && uv run pytest -q
cd /Users/sandjaieravi/dev/OneLedger/web && npm run api:types && npm run typecheck && npm run typecheck:compat
```

Expected: ruff and mypy clean; pytest green with the new cases, and
`test_every_current_non_bootstrap_route_depends_on_the_owner` still passing — it fails if any of
the new routes is added without `require_owner`. `web/types/api/` gains `HouseholdUpdate`,
`CurrencyRow` and the new `HouseholdResponse` fields; commit the regenerated output so
`npm run api:types:check` passes.

- [ ] **Step 9: Move the five variables out of `.env` and update the documents**

Delete from `.env.example` the block containing `HOUSEHOLD_NAME`, `OWNER_DISPLAY_NAME`,
`OWNER_TAX_RESIDENCY`, `BASE_CURRENCY` and `SECONDARY_CURRENCY`, replacing it with:

```bash
# The household name, the owner's display name and tax residency, and the reporting pair are
# application data, not configuration: they are seeded neutral by the migrations and edited in
# Settings > Household. Choose the reporting pair there before the first import — both amounts
# are frozen per row, so changing the pair afterwards is a backfill (ADR-0002).
```

Rewrite `scripts/seed-household.sh` to seed only the owner row:

```bash
#!/usr/bin/env bash
# Seeds the owner's app_user row against the household and person created by V7. The household
# name, the owner's display name and tax residency, and the reporting pair are application data
# and are edited in Settings > Household, not here.
#
# ALLOWED_USER_EMAIL stays in .env: it is the authorization pin, and Flyway cannot read it.
# Deliberately refuses a second owner — the app has no concept of one.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
. scripts/load-env.sh
: "${ALLOWED_USER_EMAIL:?ALLOWED_USER_EMAIL is unset — copy .env.example to .env}"
: "${DATABASE_URL:=$(scripts/dsn.sh)}"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v email="$ALLOWED_USER_EMAIL" <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM person WHERE is_self) THEN
    RAISE EXCEPTION 'no household yet — run: docker compose run --rm flyway migrate';
  END IF;
  IF EXISTS (SELECT FROM app_user WHERE role = 'owner') THEN
    RAISE EXCEPTION 'an owner already exists — this script runs once per database';
  END IF;
END $$;

INSERT INTO app_user (id, household_id, person_id, email, role)
SELECT gen_random_uuid(), p.household_id, p.id, :'email', 'owner'
  FROM person p WHERE p.is_self LIMIT 1;
SQL
psql "$DATABASE_URL" -c "SELECT email, role FROM app_user;"
```

In `README.md`, change the "What you choose at setup" table: the reporting pair row no longer
names `BASE_CURRENCY` / `SECONDARY_CURRENCY` but says it is chosen in **Settings › Household**
before the first import, keeping the existing "expensive to change afterwards" explanation and
the §4 link; and change the sentence above the table from "Three decisions, made in `.env` and
applied by the Phase 0 seed" to name `.env` for the permitted Google address and the app for
the rest. In `docs/superpowers/schema.md` and `docs/design/data-model.html` §3, record the two
new functions and the neutral seed. In `docs/design/api-contracts.html` §2, add the
`PATCH /household` and `GET /currencies` rows. In
`docs/superpowers/phase-0-foundations.md`, add one line above its `.env` block noting that the
five household variables moved into the database in Phase 1; do not rewrite the historical task
text.

- [ ] **Step 10: Verify the whole path from empty**

```bash
cd /Users/sandjaieravi/dev/OneLedger
python3 scripts/check-docs.py
# scratch stack, as in Task 2 Step 2
docker compose down -v && docker compose up -d db && docker compose run --rm flyway migrate
for f in db/seeds/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
ALLOWED_USER_EMAIL=owner@example.test scripts/seed-household.sh
for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
grep -c 'HOUSEHOLD_NAME\|BASE_CURRENCY\|SECONDARY_CURRENCY\|OWNER_DISPLAY_NAME\|OWNER_TAX_RESIDENCY' .env.example
```

Expected: docs check `ok`; migrations V1–V7 applied; the seed prints one owner row; every SQL
suite passes; the `grep -c` prints `0`. Run the script a second time and expect
`ERROR: an owner already exists`.

- [ ] **Step 11: Commit**

```bash
git add db/migrations/V7__household_configuration.sql db/tests/0006_household_configuration.sql \
        api/oneledger/db.py api/oneledger/main.py api/oneledger/routes/household.py \
        api/oneledger/routes/currencies.py api/oneledger/routes/fx.py \
        api/tests/test_household.py web/types/api scripts/seed-household.sh .env.example \
        README.md docs/superpowers/schema.md docs/design/data-model.html \
        docs/design/api-contracts.html docs/superpowers/phase-0-foundations.md
git commit -m "feat(api): household configuration is application data, not deployment config

The household name, the owner's display name and tax residency and the reporting pair were five
.env variables applied by a seed script, so changing any of them meant editing a deployment file
and reseeding. V7 seeds them neutral - naming no currency - and an owner-guarded function makes
them editable. The reporting pair is freely editable until an amount is frozen and refused with
the ADR-0002 backfill explanation afterwards.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Settings › Household

**Mockup reference:** Settings is a nav destination in every artboard (`OneLedger.dc.html`,
`Review.dc.html`, `NetWorth.dc.html`) and is explicitly not drawn in mockup set 01 — the
"NOT IN MOCKUP SET 01" frame. Build it in the artboards' design language (IBM Plex, mono
uppercase section labels, hairline-bordered cards, tabular figures, colour reserved for meaning)
and to `ui-information-architecture.html` §6's Settings content list.

**Files:**
- Create: `web/app/settings/household-form.tsx`
- Create: `web/app/settings/__tests__/household-form.test.tsx`
- Modify: `web/app/workspace.tsx` (fetch `/currencies`; render the form in the settings section)
- Modify: `web/app/__tests__/workspace.test.tsx`

**Interfaces:**
- Consumes: `HouseholdResponse`, `HouseholdUpdate`, `CurrencyRow` from `@/types/api`;
  `apiFetch`, `ApiError` from `@/lib/api/client`.
- Produces: `<HouseholdForm household currencies save onSaved />` where
  `save: (body: HouseholdUpdate) => Promise<void>` and `onSaved: () => void`.

- [ ] **Step 1: Write the failing tests**

Create `web/app/settings/__tests__/household-form.test.tsx`:

```tsx
// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HouseholdForm } from "@/app/settings/household-form";
import { ApiError } from "@/lib/api/client";
import type { CurrencyRow, HouseholdResponse } from "@/types/api";

const currencies: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "I", decimals: 2, numberFormat: "indian" },
  { code: "XZZ", name: "Synthetic Z", symbol: "Z", decimals: 3, numberFormat: "international" },
];

const household: HouseholdResponse = {
  id: "synthetic-owner",
  name: "Household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: null,
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  reportingPairLocked: false,
  currencies: [currencies[1], currencies[0]],
};

afterEach(cleanup);

describe("Settings › Household", () => {
  it("offers every catalogue currency, not only the configured pair", () => {
    render(<HouseholdForm household={household} currencies={currencies}
      save={vi.fn()} onSaved={vi.fn()} />);
    const base = screen.getByLabelText("Base currency") as HTMLSelectElement;
    expect([...base.options].map((option) => option.value)).toEqual(["XAE", "XIN", "XZZ"]);
    expect(base.value).toBe("XIN");
  });

  it("saves every editable field in one request", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const onSaved = vi.fn();
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText("Household name"), { target: { value: "Home" } });
    fireEvent.change(screen.getByLabelText("Your display name"), { target: { value: "Me" } });
    fireEvent.change(screen.getByLabelText("Tax residency"), { target: { value: "XA" } });
    fireEvent.change(screen.getByLabelText("Secondary currency"), { target: { value: "XZZ" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith({
      name: "Home",
      ownerDisplayName: "Me",
      ownerTaxResidency: "XA",
      baseCurrency: "XIN",
      secondaryCurrency: "XZZ",
    });
  });

  it("refuses one currency in both roles without calling the API", () => {
    const save = vi.fn();
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Secondary currency"), { target: { value: "XIN" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    expect(screen.getByRole("alert")).toHaveTextContent("two different currencies");
    expect(save).not.toHaveBeenCalled();
  });

  it("disables the pair and explains why once amounts are frozen", () => {
    render(<HouseholdForm household={{ ...household, reportingPairLocked: true }}
      currencies={currencies} save={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText("Base currency")).toBeDisabled();
    expect(screen.getByLabelText("Secondary currency")).toBeDisabled();
    expect(screen.getByText(/backfill/i)).toBeInTheDocument();
    // The rest of the configuration stays editable behind a locked pair.
    expect(screen.getByLabelText("Household name")).not.toBeDisabled();
  });

  it("shows the server's explanation when the pair is refused", async () => {
    const save = vi.fn().mockRejectedValue(
      new ApiError(409, "The reporting pair is fixed once amounts are frozen."),
    );
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Secondary currency"), { target: { value: "XZZ" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/fixed once amounts are frozen/),
    );
    expect(screen.getByLabelText("Household name")).not.toBeDisabled();
  });
});
```

Add to `web/app/__tests__/workspace.test.tsx` a test that the saved pair reaches the header
toggle, driving the request sequence `/household`, `/currencies`, `PATCH /household`,
`/household` (refetch), `/currencies`:

```tsx
it("refreshes the reporting-currency toggle after the pair changes", async () => {
  const request = requestThat(household, catalogue, undefined, swappedHousehold, catalogue);
  render(<Workspace section="settings" request={request} navigate={vi.fn()} />);
  await screen.findByLabelText("Base currency");
  fireEvent.change(screen.getByLabelText("Base currency"), { target: { value: "XAE" } });
  fireEvent.click(screen.getByRole("button", { name: "Save household" }));
  await waitFor(() =>
    expect(screen.getByRole("group", { name: "Reporting currency" })).toHaveTextContent("XAE"),
  );
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/sandjaieravi/dev/OneLedger/web && npx vitest run app/settings app/__tests__/workspace.test.tsx`
Expected: FAIL — `Failed to resolve import "@/app/settings/household-form"`.

- [ ] **Step 3: Write the minimal implementation**

Create `web/app/settings/household-form.tsx`:

```tsx
"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import type { CurrencyRow, HouseholdResponse, HouseholdUpdate } from "@/types/api";

type Props = {
  household: HouseholdResponse;
  currencies: Array<CurrencyRow>;
  save: (body: HouseholdUpdate) => Promise<void>;
  onSaved: () => void;
};

const LOCKED_EXPLANATION =
  "Both amounts are frozen on every row, so changing which two currencies the pair contains is " +
  "a backfill from stored FX history, not a settings change. The pair is fixed now that amounts " +
  "are frozen; which of the two you look at is still yours to choose in the header.";

export function HouseholdForm({ household, currencies, save, onSaved }: Props) {
  const [name, setName] = useState(household.name);
  const [displayName, setDisplayName] = useState(household.ownerDisplayName);
  const [residency, setResidency] = useState(household.ownerTaxResidency ?? "");
  const [base, setBase] = useState(household.baseCurrency);
  const [secondary, setSecondary] = useState(household.secondaryCurrency);
  const [problem, setProblem] = useState<string>();
  const [saving, setSaving] = useState(false);
  const locked = household.reportingPairLocked;

  async function submit() {
    if (base === secondary) {
      setProblem("The reporting pair must name two different currencies.");
      return;
    }
    setProblem(undefined);
    setSaving(true);
    try {
      await save({
        name,
        ownerDisplayName: displayName,
        ownerTaxResidency: residency === "" ? null : residency.toUpperCase(),
        baseCurrency: base,
        secondaryCurrency: secondary,
      });
      onSaved();
    } catch (error) {
      setProblem(
        error instanceof ApiError ? error.detail : "The household could not be saved. Try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="household-card" aria-labelledby="household-title">
      <div>
        <p className="section-label">Household</p>
        <h2 id="household-title">Name and reporting pair</h2>
      </div>

      <label className="text-field">
        <span>Household name</span>
        <input onChange={(e) => setName(e.target.value)} required value={name} />
      </label>
      <label className="text-field">
        <span>Your display name</span>
        <input onChange={(e) => setDisplayName(e.target.value)} required value={displayName} />
      </label>
      <label className="text-field">
        <span>Tax residency</span>
        <input
          maxLength={2}
          onChange={(e) => setResidency(e.target.value.toUpperCase())}
          placeholder="ISO 3166-1 alpha-2"
          value={residency}
        />
      </label>

      {(
        [
          ["Base currency", base, setBase],
          ["Secondary currency", secondary, setSecondary],
        ] as const
      ).map(([label, value, set]) => (
        <label className="select-field" key={label}>
          <span>{label}</span>
          <select disabled={locked} onChange={(e) => set(e.target.value)} value={value}>
            {currencies.map((option) => (
              <option key={option.code} value={option.code}>
                {option.code} — {option.name}
              </option>
            ))}
          </select>
        </label>
      ))}

      {locked && <p className="inline-note">{LOCKED_EXPLANATION}</p>}
      {!locked && (
        <p className="inline-note">
          Choose the pair before the first import. Afterwards it is a backfill, not a setting.
        </p>
      )}
      {problem && (
        <p className="inline-alert" role="alert">
          {problem}
        </p>
      )}

      <button className="primary-action" disabled={saving} onClick={submit} type="button">
        {saving ? "Saving…" : "Save household"}
      </button>
    </section>
  );
}
```

In `web/app/workspace.tsx`: load `/currencies` alongside `/household` in the existing effect,
hold it in state, render `<HouseholdForm>` above the FX inspector in the settings section, pass
`save={(body) => request<void>("/household", { method: "PATCH", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } })`
and `onSaved={() => setAttempt((value) => value + 1)}` so the household effect refetches and the
toggle updates. Add the matching styles to `web/app/globals.css` in the existing visual language.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/sandjaieravi/dev/OneLedger/web
npx vitest run && npm run lint && npm run typecheck && npm run typecheck:compat && npm run build
grep -rE 'SESSION_SECRET|POSTGRES_PASSWORD|ACCOUNT_HASH_SALT|GOOGLE_CLIENT_SECRET|postgresql://' .next/static/ || echo "no secrets in the bundle"
```

Expected: every Vitest file green including the existing 33 tests; lint, both TypeScript
compilers and the production build clean; the bundle grep printing the "no secrets" line. Then
browser-check against the running stack: Tab reaches every control with a visible focus ring, the
selects announce their labels, and a same-currency choice is refused before any request is sent.

- [ ] **Step 5: Commit**

```bash
git add web/app/settings/household-form.tsx web/app/settings/__tests__/household-form.test.tsx \
        web/app/workspace.tsx web/app/__tests__/workspace.test.tsx web/app/globals.css
git commit -m "feat(web): Settings offers the household name and reporting pair

The household's name, the owner's display name and tax residency and the reporting pair could
only be changed by editing .env and reseeding. Settings now edits them, with currency options
read from the catalogue rather than a union type, and with the pair disabled and explained once
amounts are frozen.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Account inventory schema

**Files:**
- Create: `db/migrations/V8__account_inventory.sql`
- Create: `db/tests/0007_account_inventory.sql`
- Modify: `docs/superpowers/schema.md` (`0003` accounts section)
- Modify: `docs/design/data-model.html` (§5 Accounts)

**Interfaces:**
- Consumes: the `account` table from V3 and the V6 balance trigger.
- Produces: `account.opening_balance_note TEXT`,
  `account.opening_balance_confirmed_at TIMESTAMPTZ`, and the
  `closed_account_has_a_date` constraint. Task 9's routes read and write all three.

- [ ] **Step 1: Write the failing test**

Create `db/tests/0007_account_inventory.sql`:

```sql
-- db/tests/0007_account_inventory.sql
\set ON_ERROR_STOP on
DO $$
DECLARE h UUID; p UUID; a UUID; base CHAR(3);
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  SELECT 'inventory fixture', base, (SELECT min(code) FROM currency WHERE code > base)
  RETURNING id INTO h;
  INSERT INTO person (household_id, display_name, is_self) VALUES (h,'self',true) RETURNING id INTO p;

  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
    VALUES (h,p,'Inventory Savings','savings',base,'XB',250000,'2024-01-01')
    RETURNING id INTO a;

  -- An opening balance is a stated fact until it is acknowledged (F05, Invariant I7).
  ASSERT (SELECT opening_balance_confirmed_at FROM account WHERE id = a) IS NULL,
    'an opening balance starts unacknowledged';
  ASSERT (SELECT opening_balance_note FROM account WHERE id = a) IS NULL,
    'an opening balance starts with no stated evidence';

  UPDATE account SET opening_balance_note = 'read from the bank app on 01 Jan 2024',
                     opening_balance_confirmed_at = now()
   WHERE id = a;
  ASSERT (SELECT opening_balance_confirmed_at FROM account WHERE id = a) IS NOT NULL,
    'acknowledging an opening balance records when';

  -- Closing preserves history; it never deletes the row (F06).
  BEGIN
    UPDATE account SET is_active = false WHERE id = a;
    RAISE EXCEPTION 'an inactive account without a closing date must be rejected';
  EXCEPTION WHEN check_violation THEN NULL;
  END;

  UPDATE account SET is_active = false, closed_on = '2026-06-30' WHERE id = a;
  ASSERT (SELECT opening_balance FROM account WHERE id = a) = 250000,
    'a closed account keeps its opening balance and its history';
  ASSERT (SELECT count(*) FROM account WHERE id = a) = 1,
    'closing an account never removes it';

  RAISE NOTICE 'PASS 0007';
  RAISE EXCEPTION 'rollback test data';
EXCEPTION WHEN OTHERS THEN
  IF SQLERRM <> 'rollback test data' THEN RAISE; END IF;
END $$;
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/0007_account_inventory.sql
```

Expected: `ERROR: column "opening_balance_confirmed_at" does not exist`.

- [ ] **Step 3: Write the minimal implementation**

Create `db/migrations/V8__account_inventory.sql`:

```sql
-- db/migrations/V8__account_inventory.sql
-- M1a. An opening balance typed from a bank app is a stated fact and must read as one until a
-- statement supersedes it (Invariant I7, F05). The note records where the figure came from; the
-- confirmation timestamp stays NULL until the owner acknowledges it. The evidence document
-- reference is deliberately absent: `document` arrives with M2/M10, and a forward migration
-- adds the reference then rather than reserving a column for a table that does not exist.
ALTER TABLE account
  ADD COLUMN opening_balance_note         TEXT,
  ADD COLUMN opening_balance_confirmed_at TIMESTAMPTZ;

-- Closing an account preserves its history and never deletes the row (F06). An inactive account
-- with no closing date is a row whose history cannot be dated, so it is refused here rather than
-- discovered later in a report.
ALTER TABLE account
  ADD CONSTRAINT closed_account_has_a_date CHECK (is_active OR closed_on IS NOT NULL);
```

Update `docs/superpowers/schema.md`'s `0003` account DDL with the two columns and the
constraint, and `docs/design/data-model.html` §5's Anchoring row to mention the note and the
acknowledgement.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker compose run --rm flyway migrate
for f in db/tests/*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
python3 scripts/check-docs.py
```

Expected: `Successfully applied 1 migration` (V8); `PASS 0007`; every earlier suite still
passing; docs check `ok`. Re-run migrations from empty in the scratch project.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/V8__account_inventory.sql db/tests/0007_account_inventory.sql \
        docs/superpowers/schema.md docs/design/data-model.html
git commit -m "feat(db): an opening balance carries its evidence and a closed account its date

An opening balance had nowhere to record where the figure came from or whether the owner had
acknowledged it, so a typed figure was indistinguishable from an imported one. An account could
also be deactivated with no closing date, leaving history that could not be dated. V8 adds the
note and the acknowledgement, and constrains an inactive account to carry a closing date.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Account inventory API

**Files:**
- Create: `api/oneledger/routes/accounts.py`
- Create: `api/tests/test_accounts.py`
- Modify: `api/oneledger/main.py` (register the router)
- Modify: `docs/design/api-contracts.html` §2 (the four new rows)
- Regenerate: `web/types/api/`

**Interfaces:**
- Consumes: `require_owner()`, `OwnerSession`, `connect()`; the V8 columns.
- Produces:
  - `GET /accounts` → `AccountsResponse { accounts: AccountRow[], totals: AccountGroupTotal[] }`
  - `POST /accounts` ← `AccountCreate` → `AccountRow`
  - `PATCH /accounts/{account_id}` ← `AccountUpdate` → `AccountRow`
  - `GET /institutions` → `InstitutionRow[] { id, name, shortName, country, institutionType }`
  - `AccountRow` fields: `id, name, institutionId, institutionName, accountType, accountClass,
    accountSubtype, currency, country, accountNumberMasked, openedOn, closedOn, isActive,
    includeInNetWorth, isLiquid, openingBalance, openingBalanceDate, openingBalanceNote,
    openingBalanceConfirmedAt, currentBalance, currentBalanceAsOf, creditLimit`. Every money
    field is a decimal string.
  - `AccountGroupTotal` fields: `accountClass, country, currency, total` (a decimal string).

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_accounts.py`, following the fake-connection pattern already in
`api/tests/test_household.py` (these tests run without a database):

```python
def test_money_must_be_an_exact_decimal_string() -> None:
    """A JSON number would be an IEEE-754 float by the time it reached React."""
    for bad in [1000, "1000.00000", "", "1e3", None]:
        with pytest.raises(ValidationError):
            accounts.AccountCreate(**{**CREATE, "openingBalance": bad})
    assert accounts.AccountCreate(**{**CREATE, "openingBalance": "-4500.0000"})


def test_creating_an_account_never_accepts_a_class_from_the_client() -> None:
    """account_class is derived by the V3 trigger; a client value would be a second source."""
    assert "accountClass" not in accounts.AccountCreate.model_fields


def test_the_row_mapper_emits_money_exactly_as_stored() -> None:
    row = accounts.account_row(FETCHED_ROW)
    assert row.openingBalance == "-4500.0000"
    assert row.currentBalance == "-4500.0000"
    assert row.creditLimit == "20000.0000"


def test_the_inventory_includes_closed_accounts_and_database_computed_totals(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(rows=[FETCHED_ROW, CLOSED_ROW], totals=[TOTAL])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        body = TestClient(app).get("/accounts").json()
    finally:
        app.dependency_overrides.clear()

    assert [a["isActive"] for a in body["accounts"]] == [True, False]
    # Aggregation happens in Postgres; the service shapes the response and sums nothing.
    assert body["totals"] == [
        {"accountClass": "asset", "country": "XB", "currency": "XIN", "total": "250000.0000"}
    ]
    assert "SUM(" in connection.statements[-1] and "GROUP BY" in connection.statements[-1]


def test_every_statement_is_scoped_to_the_resolved_household(monkeypatch: MonkeyPatch) -> None:
    """The service connects privileged and bypasses RLS, so the predicate is the boundary."""
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).get("/accounts")
    finally:
        app.dependency_overrides.clear()
    for statement, params in zip(connection.statements, connection.parameters, strict=True):
        assert "household_id = %s" in statement
        assert HOUSEHOLD_ID in params


def test_closing_an_account_derives_is_active_from_the_closing_date(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(rows=[CLOSED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}", json={**UPDATE, "closedOn": "2026-06-30"}, headers=ORIGIN
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["isActive"] is False
    assert "is_active = (%s::date IS NULL)" in connection.statements[0]


def test_acknowledging_an_opening_balance_is_recorded_and_reversible(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "acknowledgeOpeningBalance": True},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert "opening_balance_confirmed_at" in connection.statements[0]
    assert True in connection.parameters[0]


def test_accounts_are_never_deleted() -> None:
    assert not any(
        "DELETE" in (route.methods or ()) and route.path.startswith("/accounts")
        for route in app.routes
        if isinstance(route, APIRoute)
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/sandjaieravi/dev/OneLedger/api && uv run pytest -q tests/test_accounts.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'oneledger.routes.accounts'`.

- [ ] **Step 3: Write the minimal implementation**

Create `api/oneledger/routes/accounts.py`:

```python
# api/oneledger/routes/accounts.py
"""M1a: the account inventory. Balances are native here; translating one needs an as-of-date
rate and a coverage label, which is M9a's contract (multi-currency-and-fx.html section 6)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(tags=["accounts"])

# Money crosses the wire as a decimal string in both directions. api-contracts.html section 6.
MONEY = re.compile(r"-?\d{1,16}(\.\d{1,4})?")


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ValueError("money must be a decimal string with at most four decimal places")
    return value


def _out(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class AccountRow(BaseModel):
    id: UUID
    name: str
    institutionId: UUID | None
    institutionName: str | None
    accountType: str
    accountClass: Literal["asset", "liability"]
    accountSubtype: str | None
    currency: str = Field(min_length=3, max_length=3)
    country: str = Field(min_length=2, max_length=2)
    accountNumberMasked: str | None
    openedOn: date | None
    closedOn: date | None
    isActive: bool
    includeInNetWorth: bool
    isLiquid: bool
    openingBalance: str
    openingBalanceDate: date
    openingBalanceNote: str | None
    openingBalanceConfirmedAt: datetime | None
    currentBalance: str
    currentBalanceAsOf: datetime | None
    creditLimit: str | None


class AccountGroupTotal(BaseModel):
    """Computed by Postgres. Native per currency: an untagged converted total would be a dated
    position without its date."""

    accountClass: Literal["asset", "liability"]
    country: str
    currency: str
    total: str


class AccountsResponse(BaseModel):
    accounts: list[AccountRow]
    totals: list[AccountGroupTotal]


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    institutionId: UUID | None = None
    # Validated by the account_type enum in the database rather than a second copy here that
    # would drift from it. An unknown value fails as 422.
    accountType: str = Field(min_length=1, max_length=40)
    accountSubtype: str | None = Field(default=None, max_length=40)
    currency: str = Field(min_length=3, max_length=3)
    country: str = Field(min_length=2, max_length=2)
    # Masked only. The full number is never accepted, transmitted or logged
    # (security-and-privacy.html section 1); account_number_hash arrives with M2's matching flow.
    accountNumberMasked: str | None = Field(default=None, max_length=32)
    openedOn: date | None = None
    includeInNetWorth: bool = True
    isLiquid: bool = True
    openingBalance: str
    openingBalanceDate: date
    openingBalanceNote: str | None = None
    creditLimit: str | None = None

    @field_validator("openingBalance", "creditLimit")
    @classmethod
    def exact_decimal_string(cls, value: str | None) -> str | None:
        return _money(value)


class AccountUpdate(BaseModel):
    """Every editable field, always sent: a full replacement has no absent-versus-null ambiguity
    and the client always holds the whole row."""

    name: str = Field(min_length=1, max_length=120)
    institutionId: UUID | None = None
    accountSubtype: str | None = Field(default=None, max_length=40)
    accountNumberMasked: str | None = Field(default=None, max_length=32)
    openedOn: date | None = None
    closedOn: date | None = None
    includeInNetWorth: bool
    isLiquid: bool
    openingBalanceNote: str | None = None
    acknowledgeOpeningBalance: bool


class InstitutionRow(BaseModel):
    id: UUID
    name: str
    shortName: str | None
    country: str
    institutionType: str


COLUMNS = """a.id, a.name, a.institution_id, i.name, a.account_type::text, a.account_class::text,
             a.account_subtype, a.currency, a.country, a.account_number_masked, a.opened_on,
             a.closed_on, a.is_active, a.include_in_net_worth, a.is_liquid, a.opening_balance,
             a.opening_balance_date, a.opening_balance_note, a.opening_balance_confirmed_at,
             a.current_balance, a.current_balance_as_of, a.credit_limit"""

FROM = "FROM account a LEFT JOIN institution i ON i.id = a.institution_id"

FIELDS = [
    "id", "name", "institutionId", "institutionName", "accountType", "accountClass",
    "accountSubtype", "currency", "country", "accountNumberMasked", "openedOn", "closedOn",
    "isActive", "includeInNetWorth", "isLiquid", "openingBalance", "openingBalanceDate",
    "openingBalanceNote", "openingBalanceConfirmedAt", "currentBalance", "currentBalanceAsOf",
    "creditLimit",
]
MONEY_FIELDS = {"openingBalance", "currentBalance", "creditLimit"}


def account_row(values: tuple[Any, ...]) -> AccountRow:
    fields = dict(zip(FIELDS, values, strict=True))
    for name in MONEY_FIELDS:
        fields[name] = _out(fields[name])
    return AccountRow(**fields)


def _one(cur: psycopg.Cursor, account_id: UUID, household_id: UUID) -> AccountRow:
    cur.execute(
        f"SELECT {COLUMNS} {FROM} WHERE a.id = %s AND a.household_id = %s",
        (account_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such account")
    return account_row(row)


@router.get("/accounts")
def list_accounts(owner: Annotated[OwnerSession, Depends(require_owner)]) -> AccountsResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""SELECT {COLUMNS} {FROM}
                 WHERE a.household_id = %s
                 ORDER BY a.account_class, a.country, a.display_order, a.name""",
            (owner.household_id,),
        )
        rows = [account_row(row) for row in cur.fetchall()]
        # Aggregation happens in Postgres, not in Python; money arithmetic stays in NUMERIC.
        cur.execute(
            """SELECT account_class::text, country, currency, SUM(current_balance)
                 FROM account
                WHERE household_id = %s AND is_active AND include_in_net_worth
                GROUP BY 1, 2, 3
                ORDER BY 1, 2, 3""",
            (owner.household_id,),
        )
        totals = [
            AccountGroupTotal(
                accountClass=cls, country=country, currency=currency, total=str(total)
            )
            for cls, country, currency, total in cur.fetchall()
        ]
    return AccountsResponse(accounts=rows, totals=totals)


@router.post("/accounts", status_code=201)
def create_account(
    body: AccountCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> AccountRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, institution_id, name, account_type,
                       account_subtype, currency, country, account_number_masked, opened_on,
                       include_in_net_worth, is_liquid, opening_balance, opening_balance_date,
                       opening_balance_note, credit_limit)
                   SELECT %s, p.id, %s, %s, %s::account_type, %s, %s, %s, %s, %s, %s, %s,
                          %s, %s, %s, %s
                     FROM person p WHERE p.household_id = %s AND p.is_self
                   RETURNING id""",
                (
                    owner.household_id, body.institutionId, body.name, body.accountType,
                    body.accountSubtype, body.currency, body.country, body.accountNumberMasked,
                    body.openedOn, body.includeInNetWorth, body.isLiquid,
                    Decimal(body.openingBalance), body.openingBalanceDate,
                    body.openingBalanceNote,
                    None if body.creditLimit is None else Decimal(body.creditLimit),
                    owner.household_id,
                ),
            )
            created = cur.fetchone()
            if created is None:
                raise HTTPException(status_code=409, detail="no self person for this household")
            return _one(cur, UUID(str(created[0])), owner.household_id)
    except (psycopg.errors.InvalidTextRepresentation, psycopg.errors.CheckViolation,
            psycopg.errors.ForeignKeyViolation, psycopg.errors.UniqueViolation) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: UUID,
    body: AccountUpdate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> AccountRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE account
                      SET name = %s, institution_id = %s, account_subtype = %s,
                          account_number_masked = %s, opened_on = %s, closed_on = %s,
                          is_active = (%s::date IS NULL),
                          include_in_net_worth = %s, is_liquid = %s, opening_balance_note = %s,
                          opening_balance_confirmed_at =
                            CASE WHEN %s THEN COALESCE(opening_balance_confirmed_at, now())
                                 ELSE NULL END
                    WHERE id = %s AND household_id = %s
                   RETURNING id""",
                (
                    body.name, body.institutionId, body.accountSubtype, body.accountNumberMasked,
                    body.openedOn, body.closedOn, body.closedOn, body.includeInNetWorth,
                    body.isLiquid, body.openingBalanceNote, body.acknowledgeOpeningBalance,
                    account_id, owner.household_id,
                ),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="no such account")
            return _one(cur, account_id, owner.household_id)
    except (psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc


@router.get("/institutions")
def list_institutions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> list[InstitutionRow]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id, name, short_name, country, institution_type
                 FROM institution
                WHERE household_id IS NULL OR household_id = %s
                ORDER BY name""",
            (owner.household_id,),
        )
        return [
            InstitutionRow(
                id=i, name=n, shortName=s, country=c, institutionType=t
            )
            for i, n, s, c, t in cur.fetchall()
        ]
```

with `def _first_line(exc: Exception) -> str: return str(exc).splitlines()[0]` so a constraint
name reaches the UI as a specific message and never a stack trace or a row of financial data.

There is deliberately no `DELETE /accounts/{id}`: closing preserves history (F06). Register the
router in `api/oneledger/main.py`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/sandjaieravi/dev/OneLedger/api && uv run ruff check . && uv run mypy . && uv run pytest -q
cd /Users/sandjaieravi/dev/OneLedger/web && npm run api:types && npm run typecheck && npm run typecheck:compat
```

Expected: ruff and mypy clean; every `test_accounts.py` case green;
`test_every_current_non_bootstrap_route_depends_on_the_owner` still passing for the four new
routes. `web/types/api/` gains `AccountRow`, `AccountsResponse`, `AccountCreate`,
`AccountUpdate`, `AccountGroupTotal` and `InstitutionRow`; commit the regenerated output.

Then exercise the routes against the running stack with a synthetic session, confirming that
`openingBalance` and `currentBalance` arrive as quoted strings — `"250000.0000"`, never
`250000.0` — and that a nonzero opening balance is echoed as the current balance.

- [ ] **Step 5: Commit**

```bash
git add api/oneledger/routes/accounts.py api/oneledger/main.py api/tests/test_accounts.py \
        web/types/api docs/design/api-contracts.html
git commit -m "feat(api): the account inventory reads, creates and edits accounts

M1a had no route surface: accounts could only be created by hand in SQL, so opening-balance
evidence and closed-account history had no way into the product. The four routes list, create
and edit accounts and list institutions, with money as decimal strings both ways, group totals
aggregated in Postgres, and no delete - closing an account preserves its history.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Account inventory screen

**Mockup reference:** the **Accounts** screen of `OneLedger.dc.html` — grouped rows with a
two-letter institution mark, institution and subtype, native balance, masked number and an
"Updated" date, plus group headers with subtotals; and its **Overview** ACCOUNTS card for the
row treatment. Per UI §6 the grouping is **class, then country**, not country alone. Every
currency, bank and figure in the artboard is example data (ADR-0012).

**Files:**
- Create: `web/app/accounts/page.tsx`
- Create: `web/app/accounts/accounts-screen.tsx`
- Create: `web/app/accounts/__tests__/accounts-screen.test.tsx`
- Modify: `web/app/workspace.tsx` (nav entry, `"accounts"` section, `sectionFromPath()`)
- Modify: `web/app/__tests__/workspace.test.tsx`
- Modify: `web/app/globals.css`

**Interfaces:**
- Consumes: `AccountsResponse`, `AccountRow`, `AccountCreate`, `AccountUpdate`,
  `InstitutionRow`, `CurrencyRow` from `@/types/api`; `formatMoney` from `@/lib/money/format`.
- Produces: `<AccountsScreen accounts totals institutions currencies request onChanged />`.

- [ ] **Step 1: Write the failing tests**

Create `web/app/accounts/__tests__/accounts-screen.test.tsx`:

```tsx
// @vitest-environment jsdom
describe("Accounts", () => {
  it("groups by class then country, as UI §6 specifies over the artboard", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent))
      .toEqual(["Assets · XB", "Liabilities · XA"]);
  });

  it("shows a native balance formatted by its own currency row", () => {
    render(<AccountsScreen {...props} />);
    // XIN declares Indian grouping and 2 decimals in the catalogue.
    expect(screen.getByText("I2,50,000.00")).toBeInTheDocument();
    // A liability reads as what is owed while the stored value stays negative.
    expect(screen.getByLabelText("Inventory Card balance")).toHaveTextContent("You owe A4,500.00");
  });

  it("keeps closed accounts visible with their closing date", () => {
    render(<AccountsScreen {...props} />);
    const closed = screen.getByRole("row", { name: /Old Savings/ });
    expect(closed).toHaveTextContent("Closed 30 Jun 2026");
  });

  it("marks an unacknowledged opening balance as stated, not verified", () => {
    render(<AccountsScreen {...props} />);
    expect(screen.getByText("Opening balance not acknowledged")).toBeInTheDocument();
  });

  it("teaches rather than showing a blank table when there are no accounts", () => {
    render(<AccountsScreen {...props} accounts={[]} totals={[]} />);
    expect(screen.getByRole("heading", { name: /first account/i })).toBeInTheDocument();
  });

  it("offers every catalogue currency and every institution when adding an account", () => {
    render(<AccountsScreen {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    const currency = screen.getByLabelText("Currency") as HTMLSelectElement;
    expect([...currency.options].map((o) => o.value)).toEqual(["XAE", "XIN", "XZZ"]);
    expect(screen.getByLabelText("Institution")).toBeInTheDocument();
  });

  it("sends the opening balance as a decimal string", async () => {
    const request = vi.fn().mockResolvedValue(props.accounts[0]);
    render(<AccountsScreen {...props} request={request} />);
    fireEvent.click(screen.getByRole("button", { name: "Add an account" }));
    fireEvent.change(screen.getByLabelText("Account name"), { target: { value: "New" } });
    fireEvent.change(screen.getByLabelText("Opening balance"), { target: { value: "1234.50" } });
    fireEvent.change(screen.getByLabelText("Opening balance date"),
      { target: { value: "2024-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const body = JSON.parse(request.mock.calls[0][1].body);
    expect(body.openingBalance).toBe("1234.50");
    expect(typeof body.openingBalance).toBe("string");
  });
});
```

Add one workspace test asserting `/accounts` renders the screen and the nav marks it current.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/sandjaieravi/dev/OneLedger/web && npx vitest run app/accounts`
Expected: FAIL — `Failed to resolve import "@/app/accounts/accounts-screen"`.

- [ ] **Step 3: Write the minimal implementation**

`web/app/accounts/accounts-screen.tsx` renders, in the artboards' language:

- Group headings `Assets · <country>` / `Liabilities · <country>`, ordered class then country,
  each with its per-currency subtotals from `totals` formatted by that currency's catalogue row.
- A semantic `<table>` per group. Each row: the two-letter institution mark, the account name,
  `institutionName · accountSubtype · currency`, the masked number, the native
  `currentBalance` formatted with `formatMoney(balance, currencyRow)`, and
  `Updated <currentBalanceAsOf>`. A liability's balance reads as
  `You owe <formatMoney(absolute)>` while the stored value stays negative (UI §7); the sign is
  carried by the words and a class, never by colour alone.
- `Opening balance not acknowledged` beside any account whose `openingBalanceConfirmedAt` is
  null, with an `Acknowledge` action that PATCHes `acknowledgeOpeningBalance: true` and an
  optional note field for where the figure came from.
- A `Closed <date>` group for inactive accounts, always rendered when any exist.
- `Add an account` opening a form with name, institution (from `/institutions`), type, subtype,
  currency and country selects, masked number, opened date, opening balance and its date, the
  evidence note, and the `includeInNetWorth` / `isLiquid` toggles. Money inputs are `type="text"`
  with an `inputMode="decimal"` and are sent verbatim as strings — never through `Number()` or
  `parseFloat`.
- `Close account` on a row, setting `closedOn`; no delete anywhere.
- The empty state explains what an account is here and offers the first one.
- Skeleton rows while loading, never a spinner where a number belongs.

`web/app/accounts/page.tsx` is `export default function AccountsPage() { return <Workspace section="accounts" />; }`.
In `web/app/workspace.tsx`, add `"accounts"` to the section union, add the `/accounts` case to
`sectionFromPath()`, replace the disabled "Activity" nav button with a real `Accounts` link, and
fetch `/accounts` and `/institutions` when that section is active.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/sandjaieravi/dev/OneLedger/web
npx vitest run && npm run lint && npm run typecheck && npm run typecheck:compat && npm run build
grep -rnE 'parseFloat|Number\(' app/accounts lib/money || echo "no float money"
```

Expected: every Vitest file green; lint, both compilers and the build clean; the grep printing
the "no float money" line. Then browser-check against the running stack: create an asset account
and a liability account, confirm each lands in the right group with the right sign, acknowledge
an opening balance, close an account and confirm it stays visible with its date and history, and
walk the whole screen by keyboard with a visible focus ring.

- [ ] **Step 5: Commit**

```bash
git add web/app/accounts web/app/workspace.tsx web/app/__tests__/workspace.test.tsx \
        web/app/globals.css
git commit -m "feat(web): the accounts screen shows the inventory and its evidence

The account inventory had a route surface and no screen, so opening balances could not be
acknowledged and a closed account had nowhere to remain visible. Accounts groups by class then
country per UI section 6, shows native balances with their as-of dates, marks an unacknowledged
opening balance as stated rather than verified, and keeps closed accounts with their dates.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Record Phase 0 closed, Phase 1 active, and the M1a/M1b split

**Files:**
- Modify: `docs/superpowers/progress.md`
- Modify: `docs/superpowers/plans/implementation-plan.md` (§2 plan outputs for task group A only —
  the branching constraint already defers to `docs/git-workflow.md`)

**Interfaces:** none. Documentation only; no implementation or test lines count against the cap.

- [ ] **Step 1: Confirm the state you are about to record**

```bash
cd /Users/sandjaieravi/dev/OneLedger
git log --oneline feature/phase-1-trusted-financial-position -15
gh pr list --state merged --base feature/phase-1-trusted-financial-position
```

Record only what the owner has confirmed and what these commands show. The Phase 0 live Google
identity checks are the owner's to confirm; if any remains unverified, say so rather than
marking Phase 0 closed.

- [ ] **Step 2: Update the progress file**

In `docs/superpowers/progress.md`:

- Change **Current phase** to Phase 1 — trusted financial position, integration branch
  `feature/phase-1-trusted-financial-position`, linking the active plan
  (`plans/2026-09-06-phase-1-trusted-financial-position.md`) and its specification
  (`specs/2026-09-06-phase-1-trusted-financial-position.md`) as relative links from
  `progress.md`.
- Replace the old "branches from `develop`" sentences with one link to the
  [git workflow](../../git-workflow.md) (relative from `progress.md` that is
  `../git-workflow.md`); the progress file names the active phase branch and defers the model
  to that file.
- Record Phase 0 as closed on the owner's confirmed live identity checks, keeping the existing
  evidence table and the dated verification report link intact.
- Replace the "Later phases" row for Phase 1 with a task table for this plan — the eleven tasks,
  their branches and their evidence — and add the note that roadmap M1 is delivered as M1a and
  M1b and is complete only when both are.
- Record that the API import defect existed on `main` and `develop` and was fixed by Task 1, so
  the earlier "59 tests" evidence is understood as predating it.

- [ ] **Step 3: Point the master plan at this plan for M1a**

The master plan's branching constraint already defers to
[docs/git-workflow.md](../../git-workflow.md); do not restate the branch model there. In
`docs/superpowers/plans/implementation-plan.md` §2, task group A, replace "one dated plan each
for M1, M2, M3 and M4" with a pointer to
`docs/superpowers/plans/2026-09-06-phase-1-trusted-financial-position.md` for M1a and the
instruction that M1b, M2, M3 and M4 each still get their own dated plan.

- [ ] **Step 4: Run the documentation checks**

```bash
python3 scripts/check-docs.py
git diff --check
grep -rn "develop" docs/superpowers/progress.md docs/superpowers/plans/implementation-plan.md
```

Expected: docs check `ok` for all four checks with no broken links to the two new files; no
whitespace errors; the `grep` returning only historical references, not a live instruction to
branch from `develop` for Phase 1.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/progress.md docs/superpowers/plans/implementation-plan.md
git commit -m "docs(plan): the progress file records phase 1 active on its own branch

Phase 0 is closed on the owner's confirmed live identity checks, and Phase 1 integrates on
feature/phase-1-trusted-financial-position per docs/git-workflow.md. The progress file now
names that branch, defers the branch model to the workflow file, and carries the Phase 1
task table with the M1a/M1b split of roadmap M1.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## After Task 11: the rest of Phase 1

The master plan is explicit that each milestone gets its own dated plan written against the tree
at that time, and that later subsystem interfaces are not invented in advance. The table below is
the sequencing contract, not a substitute for those plans. **Write the milestone's dated plan
before implementing it**, in `docs/superpowers/plans/YYYY-MM-DD-<milestone>.md`, with a spec
beside it in `specs/` when the milestone settles new contracts.

| Order | Milestone | Entry criteria | Plan file to write |
|---|---|---|---|
| 1 | M1b — manual transactions, seed taxonomy, transaction list | Task 10 merged; roadmap M1's transaction-side *Done when* items still open | `<date>-m1b-manual-transactions.md` |
| 2 | M2 — importer and mapping UI, one layout end to end | M1b merged; FX backfill to 01 Jan 2024 verified; CI and `scripts/backup-db.sh` deferred in from Phase 0 land here | `<date>-m2-statement-import.md` |
| 3 | M3 — the remaining three layouts | M2's layout reconciling against real statements | `<date>-m3-remaining-layouts.md` |
| 4 | M4 — classification engine | M2 merged; may run beside M3 | `<date>-m4-classification.md` |
| 5 | M5 — transfers and household outflows | M3 and M4 merged | `<date>-m5-transfers.md` |
| 6 | M9a — early verified overview | M5 merged | `<date>-m9a-verified-overview.md` |
| 7 | M6 / M7 / M10 — cards, investments, documents and owner export | M9a merged; independent of one another, any order | one plan each |
| 8 | M9b — full history and attribution | M7 merged and valuation inputs available | `<date>-m9b-attribution.md` |

Three obligations carry forward and must appear in the plan that first encounters them:

1. **M2 unlocks the reporting-pair guard.** The first frozen `amount_base` makes
   `reporting_pair_is_locked()` answer true, and Settings › Household disables the pair from
   then on. M2's plan verifies that transition rather than discovering it.
2. **M5 turns on the static checks.** `v_transaction_reportable` and the checks that no report
   reads the base table and no query multiplies an amount by a rate become enforceable when the
   view exists. M5's plan wires them into the gate.
3. **M9b reconciles the schema before it migrates.** The unapplied `0015` attribution shape must
   be reconciled with F03, and `docs/superpowers/schema.md` and `docs/design/data-model.html`
   updated together, **before** its migration is written.

M8 (loans) stays descheduled. If a loan is confirmed it takes its own dated plan and blocks
nothing.

## Self-review

Run before handing this plan to an implementer; it was run when the plan was written.

**Spec coverage.** Spec §3 A1–A5 → Tasks 1, 2, 3, 4, 5. §4 B1–B5 → Task 6 (migration, routes,
guard, catalogue, `.env` and README) and Task 7 (UI). §5 C1–C3 → Tasks 8, 9, 10. §6 mockup
rulings → the mockup reference headers on Tasks 7 and 10 and the preflight rulings table. §7
milestone sequencing → the table above. §8 requirement coverage → recorded in Task 11's progress
update. §9 verification → each task's Step 2 and Step 4, plus the `verify` skill before each PR.

**Placeholder scan.** No "TBD", no "add appropriate error handling", no "similar to Task N", no
step describing an action without the content it needs. The milestone table after Task 11 is
deliberately sequencing rather than steps, and says so — inventing bite-sized steps for M2's
extraction engine before M1b exists is exactly what the master plan forbids.

**Type consistency.** `scope_to_owner(conn, user_id)` is defined in Task 6 and used in Tasks 6
and 9's review notes. `reporting_pair_is_locked()` and `update_household_configuration(...)` are
defined in Task 6's migration and called by Task 6's route and Task 6's SQL test with the same
five parameters in the same order. `HouseholdResponse.reportingPairLocked` is produced in Task 6
and consumed in Task 7. `HouseholdUpdate`'s five fields match the form body in Task 7's test.
`AccountRow`'s twenty-two fields are listed once in Task 9's Interfaces block, once in
`FIELDS`, and consumed by name in Task 10. `AccountGroupTotal` is `{accountClass, country,
currency, total}` in both tasks. `account_row(...)` is the mapper's name in the implementation
and in the test. Migration versions V6, V7 and V8 are assigned once each and reserved in Task 3's
documentation edit.
