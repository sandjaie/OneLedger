# Phase 1 specification — a trusted financial position

Date: 6 September 2026. Owner-authorized. Phase 1 is the ledger phase: accounts, statement
import, classification, transfers, the verified overview, cards, investments, documents and
consolidated wealth history. This specification defines the requirements, contracts and
acceptance for the phase, and it defines them in full detail only for the tranche that is
executable against the tree as it stands today.

Execution instructions are in the
[Phase 1 implementation plan](../plans/2026-09-06-phase-1-trusted-financial-position.md).
Scope and sequence remain owned by the [roadmap](../../product/roadmap.html) and the
[master implementation plan](../plans/implementation-plan.md). Execution status is maintained
only in [implementation progress](../progress.md).

## 1. Scope

Phase 1 covers milestones M1–M7, M9a, M9b and M10. M8 (loans) stays descheduled and conditional
on an actual loan; it never blocks unrelated work.

```text
Phase 0 → M1 → M2 → M3 + M4 → M5 → M9a → M6 / M7 / M10 → M9b
```

This document specifies three things at implementation depth, because their contracts are fixed
by the current tree:

- **Delivery A — correctness repairs.** Five defects in the delivered Phase 0 code, four of them
  raised by the Codex review of PR #20 and one found while writing this specification.
- **Delivery B — household configuration is application data**, held in the database and edited
  in the UI, not in `.env`.
- **Delivery C — M1a, the account inventory**: account records, opening-balance evidence and
  closed-account history.

Everything from M1b onward is specified here at the level of entry criteria, scope boundary and
already-fixed interfaces. Each of those milestones gets its own dated plan, written against the
tree at the time, as the master plan requires: *"Do not invent all later subsystem interfaces
while Phase 0 is unfinished."*

### Out of scope for this phase

No LLM calls anywhere in Phase 1 ([ADR-0003](../../adr/0003-defer-ai-to-v2.html)). No automatic
payments, trades, bank credentials or new shared access. No commitments, forecasts, goals,
reservations or protection records — those are Phase 2 and are gated on Phase 1's exit criteria
plus two months of maintained use.

## 2. Baseline

Applied schema is V1–V5: reference data and pegs, identity and RLS, accounts, FX rates, revocable
owner sessions. The service exposes `GET /health`, `GET /auth/login`, `GET /auth/callback`,
`POST /auth/logout`, `GET /whoami`, `GET /household`, `GET /fx/{date}` and `POST /fx/backfill`.
The web app has `/login`, `/unauthorized`, a protected shell, a currency toggle and a Settings FX
inspector. Phase 0's live Google identity checks are the owner's to record in the progress file;
they are not re-litigated here.

Two facts about the baseline change what Phase 1 must do first.

**The API package does not import.** `api/oneledger/routes/auth.py:74` reads
`except OAuthExchangeError, RuntimeError:` — Python 2 syntax, a `SyntaxError` in Python 3. The
same bytes are present on `main`, on `develop` and on this branch. Every Python check, the whole
pytest suite and the service itself fail at import. No Phase 1 work can be verified until it is
fixed, so it is the first task of the phase.

**Four Codex findings from the PR #20 review are open.** They are reproduced verbatim in §3.

## 3. Delivery A — correctness repairs

Each repair fixes the cause, not the symptom, and each leaves a runnable check that fails without
it.

### A1 — The API package must import (found while specifying)

`except OAuthExchangeError, RuntimeError:` becomes `except (OAuthExchangeError, RuntimeError):`.
`RuntimeError` is in that clause because `session_secret()` raises it when `SESSION_SECRET` is
unset; both failures must still yield the existing `503 sign-in unavailable` response with the
flow cookie cleared. The repair carries a test that exercises the clause, because the clause has
never executed.

### A2 — Stop requiring the example currency rows

> **P1, `db/tests/0001_reference.sql`** — "Stop requiring the example currency rows": the
> invariant test requires AED, INR and USD by name, violating the ADR-0012 / CLAUDE.md rule that
> no currency becomes a required seed row. Assert generic reference-data and peg properties
> instead.

Required behaviour. `db/tests/0001_reference.sql` asserts properties, not membership: that at
least two currencies are seeded; that every `number_format` is one of the two supported values;
that a peg is complete or absent; that every peg anchor is itself a seeded currency; that every
peg rate is positive; and that the shared institution seed set is present and household-neutral.
It names no currency.

The same violation exists in two sibling fixtures and is fixed with it, because otherwise the
requirement simply moves. `db/tests/0003_accounts.sql` and `db/tests/0005_sessions.sql` insert
`household(base_currency, secondary_currency)` as literal `'INR'`/`'AED'`, and those columns
carry a foreign key to `currency(code)` — so both files require those two rows to be seeded.
Both must select their fixture pair from the `currency` table instead. `db/tests/0004_fx.sql` is
already compliant: `fx_rate` has no foreign key to `currency`, so its currency literals are
fixture values rather than a seeding requirement, and it stays as it is.

Python and TypeScript tests already show the intended pattern and are the model: they use the
reserved-range synthetic codes `XIN` and `XAE` rather than real ones.

### A3 — Initialize current balance from the opening balance

> **P1, `db/migrations/V3__accounts.sql`** — "Initialize current balance from the opening
> balance": when an account is created with a nonzero `opening_balance` but omits
> `current_balance` (as inserts in `db/tests/0003_accounts.sql` do), the row starts with current
> balance zero even though the documented equation is `opening_balance + SUM(transactions)`.
> Initialize the field on insert or require callers to provide the derived value, and test the
> invariant.

Cause. `current_balance NUMERIC(20,4) NOT NULL DEFAULT 0` makes "the caller did not supply one"
indistinguishable from "the caller deliberately means zero", so no trigger can tell them apart.

Required behaviour, in a **new forward migration** — V3 is applied and Flyway checksums it, so it
is never edited ([ADR-0015](../../adr/0015-flyway-for-schema-migrations.html)):

- The `DEFAULT 0` is dropped, so an omitted `current_balance` arrives as `NULL`.
- A `BEFORE INSERT` trigger sets `current_balance := opening_balance` when it is `NULL`, and sets
  `current_balance_as_of := opening_balance_date` at the same time, so the materialised figure
  carries the date it is actually true for (Invariant I7 — nothing derived is presented as
  observed). A `BEFORE` row trigger runs before the `NOT NULL` check, so the column stays
  `NOT NULL`.
- An explicitly supplied `current_balance`, including `0`, is honoured unchanged.
- Existing rows are **not** silently backfilled: for a pre-existing row, `current_balance = 0`
  beside a nonzero opening balance is exactly the ambiguity `DEFAULT 0` created — an observed
  zero and an omitted value are the same stored bytes, and rewriting both would overwrite a
  possible observation (Invariant I7). No account write path shipped before this migration and
  no migration seeds account rows, so no legitimate database holds such a row; if one exists,
  the migration refuses with a message naming the rows, and the operator resolves each
  explicitly before re-running it.

`db/tests/0003_accounts.sql` gains assertions for all four behaviours, for an asset account and
for a liability account, so the signed-balance convention
([ADR-0010](../../adr/0010-signed-balance-convention.html)) is covered on both sides.

### A4 — Keep database errors distinct from authorization denials

> **P2, `api/oneledger/auth.py`** — "Keep database errors distinct from authorization denials": a
> broad handler converts DB unavailability/binding-query failure into `NotAuthorized`, so
> `/auth/callback` sends a correctly configured owner to `/unauthorized` instead of a retry path.
> Preserve database exceptions; reserve `NotAuthorized` for no-row or conflicting-subject cases.

Cause. `bind_google_owner()` wraps its `connect()` block in `except Exception: raise
NotAuthorized("owner binding failed")`. The callback maps `NotAuthorized` to `/unauthorized`,
which tells a correctly permitted owner that their account is not allowed.

Required behaviour. The broad handler is removed. Database exceptions propagate out of
`bind_google_owner()` unchanged; the callback's existing generic handler then routes them to
`/login?reason=authentication_failed`, the retry path, exactly as it already does for a database
failure inside `issue_session()`. `NotAuthorized` remains reserved for the address not matching
the permitted account, and for the query returning no row — no unique permitted owner, or a
conflicting `google_subject` binding. No token, code, address or query text appears in any log or
response on either path.

The neighbouring failure paths are audited with it and left alone where already correct:
`_store_session()` raises `NotAuthorized` only on a missing row and lets database errors
propagate; `verify_session()` catches only JWT, key, type and value errors, so a database outage
in the session lookup surfaces as a server error rather than a 403. That distinction is the point
of the finding and must not regress.

### A5 — Honor the configured database identity in Compose

> **P2, `docker-compose.yml`** — "Honor the configured database identity in Compose": the
> database is created as `oneledger`/`oneledger` and Flyway is hardcoded to that identity even
> when `.env` changes `POSTGRES_USER`/`POSTGRES_DB`. Interpolate the same variables consistently
> for database, API, Flyway URL and Flyway user.

Required behaviour. `POSTGRES_USER`, `POSTGRES_DB` and `POSTGRES_PASSWORD` are interpolated from
the environment with the same `oneledger` fallbacks everywhere they appear: the `db` service's
environment, its `pg_isready` healthcheck, and the Flyway service's `FLYWAY_URL`, `FLYWAY_USER`
and `FLYWAY_PASSWORD`. The `api` service already receives the parts through `env_file: .env` and
overrides only `POSTGRES_HOST` and `POSTGRES_PORT`; that stays, because restating the password
would create the second copy `.env.example` exists to prevent.

Verification is `docker compose config` with overridden values, asserting that every one of those
five places resolves to the overridden identity. There is no unit-test seam for a Compose file,
and adding one would be more machinery than the fix.

## 4. Delivery B — household configuration is application data

`.env.example` currently seeds `household` and `person` from `HOUSEHOLD_NAME`,
`OWNER_DISPLAY_NAME`, `OWNER_TAX_RESIDENCY`, `BASE_CURRENCY` and `SECONDARY_CURRENCY`. Those five
are user-editable application data, not deployment configuration. They move into the database and
become editable in the UI.

### B1 — Bootstrap

**A forward migration seeds one neutral household and person; the owner edits them in Settings.**
First-login onboarding is not available as a design: `require_owner()` resolves an `app_user` row,
`app_user` requires a `person`, and `person` requires a `household` — so all three must exist
before the first session can be issued. There is no authenticated moment before the household
exists in which an onboarding screen could run.

The migration therefore inserts, guarded by `WHERE NOT EXISTS`, so re-running it against a
populated database is a no-op rather than a second household the application has no concept of:

- `household.name = 'Household'` — a placeholder, immediately editable.
- `household.base_currency` and `secondary_currency` — the first two rows of `currency` by code.
  **No currency code appears in the migration** ([ADR-0012](../../adr/0012-statement-layouts-as-data.html)).
  A deployment seeding fewer than two currencies gets no household row and the service's existing
  "no household row" error, which is honest.
- `person.display_name = 'Owner'`, `is_self = true`, `tax_residency = NULL`.

`app_user` is not seeded by the migration: its `email` is `ALLOWED_USER_EMAIL`, which is genuinely
deployment configuration and is not available to Flyway. `scripts/seed-household.sh` remains the
one-shot owner seed, reduced to inserting the `app_user` row against the existing household and
person, and refusing loudly if either is missing or an owner already exists.

### B2 — Editable fields and the authorization boundary

`GET /household` and `PATCH /household` both depend on `require_owner()`
([ADR-0013](../../adr/0013-python-backend-service.html),
[ADR-0016](../../adr/0016-revocable-owner-sessions.html)). The existing
`test_every_current_non_bootstrap_route_depends_on_the_owner` test in `api/tests/test_health.py`
already fails if a new financial route is added without that dependency, and it must keep
passing.

`GET /household` grows `name`, `ownerDisplayName`, `ownerTaxResidency` and `reportingPairLocked`.
Its existing `currencies` array keeps its current meaning — exactly the configured pair, base
first, with display metadata — because the header toggle consumes it.

`PATCH /household` replaces every editable field in one request; the client always holds the
whole record, and a full replacement avoids the absent-versus-null ambiguity of a partial update.
Household name, owner display name and tax residency are freely editable. The reporting pair is
guarded by B3.

The write is a `SECURITY DEFINER` Postgres function that calls `assert_is_owner()` first, per
CLAUDE.md and [data model §3](../../design/data-model.html). It is a function rather than a
sequence of statements because the guard and the write must not race an import that freezes the
first amount between them ([API contracts §3](../../design/api-contracts.html)). The service
scopes the connection with `SELECT set_config('app.user_id', <owner uuid>, true)` inside the same
transaction — the mechanism the data model specifies and the first place Phase 1 needs it.

### B3 — The reporting-pair guard

[Multi-currency §4](../../design/multi-currency-and-fx.html) draws the line precisely:

> Because both values are frozen per row (§2), which of the two is shown is a display default,
> not a data asymmetry: swapping it changes one config value and no data. Changing which two
> currencies the pair contains is a different thing — the frozen columns are already populated,
> so it is a backfill from `fx_rate` history.

and ADR-0002 records the cost as *"Changing the pair after import is a backfill, not a settings
change… Choose the pair at setup; the UI states this where it is chosen."* The rule follows
directly:

1. **Which of the two currencies is displayed** is the header toggle. It is freely changeable at
   any time, is a browser preference, and touches no stored data. It is not part of this guard.
2. **While no frozen amount exists**, either member of `household.base_currency` /
   `secondary_currency` may be set to any two distinct rows of `currency`.
3. **Once any frozen amount exists**, changing either member is refused. Swapping the two is
   refused as well, and specifically: `amount_base` and `amount_secondary` are frozen against
   particular currencies, so exchanging the roles without rewriting every row would relabel every
   stored amount as a currency it is not. §4's "changes one config value and no data" describes
   the display default, not these columns.
4. The refusal is explanatory, not a bare rejection: it states that the pair is fixed because
   amounts are frozen, and that changing it is a backfill from FX history under ADR-0002 rather
   than a settings change. F06 requires exactly this — *"changing reporting currency pair follows
   ADR-0002 rather than relabelling stored amounts."*
5. **The guard and every frozen write serialize on the household row.** The guard alone cannot
   see a transaction that has read the old pair but not yet committed its first frozen row.
   `update_household_configuration(...)` therefore takes `SELECT … FOR UPDATE` on the household
   row before consulting the guard, and every writer of a frozen `amount_base` /
   `amount_secondary` row — M1b's manual entry first, M2's import after it — must take the same
   household row lock before writing. This is a standing contract on those plans, and the
   interleaving test (a pair change racing a concurrent first frozen insert) lands with M1b,
   where the second participant first exists.

"A frozen amount exists" is answered by a `SECURITY DEFINER` function that counts the household's
rows in every table in the schema carrying an `amount_base` column. Today that set is empty and
the answer is `false`; when M2 creates the transaction table the same function answers correctly
with no change, which is why it is written against the catalogue rather than against a hardcoded
table list. `GET /household` reports the answer as `reportingPairLocked` so the UI can explain
the state rather than discover it by failing.

Status codes carry the distinction: `409` for a locked pair, `422` for an invalid one — two
identical codes, or a code that is not a row in `currency`.

### B4 — Currency selection

The currency choices in the UI come from the `currency` table. `GET /currencies`, behind
`require_owner()`, returns every row as `code`, `name`, `symbol`, `decimals` and `numberFormat`.
No `"INR" | "AED"` union, no hardcoded option list, no currency-code branch anywhere in the
formatter or the form. The same payload supplies display metadata for account currencies outside
the reporting pair, which the account inventory needs.

*(The lazier alternative — embedding the catalogue in `GET /household` — was rejected because the
overview loads that payload on every visit and does not need it.)*

### B5 — What stays in `.env`

`POSTGRES_*`, `SESSION_SECRET`, `COOKIE_SECURE`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI`, `WEB_ORIGIN`, `ALLOWED_USER_EMAIL`, `ACCOUNT_HASH_SALT`, `DOCUMENTS_DIR`
and `NEXT_PUBLIC_API_URL`. All are genuinely environmental: credentials, endpoints, the
authorization pin and a hashing salt that is deliberately held outside the database
([security §4](../../operations/security-and-privacy.html)). The five household variables are
deleted from `.env.example`, from `scripts/seed-household.sh` and from the README's
"What you choose at setup" table, which must say that the reporting pair is chosen in
Settings › Household before the first import.

## 5. Delivery C — M1a, the account inventory

### C1 — Milestone split, recorded

Roadmap M1 is "Accounts and manual transactions" and its *Done when* list includes manual
transaction entry, the frozen-FX-on-save behaviour, a transaction list and the seed taxonomy. The
master plan's M1 bullet is narrower — *"account inventory, opening-balance evidence and
closed-account history"* — and that is the tranche the owner scheduled.

Resolution: roadmap M1 is delivered in two slices. **M1a** is this delivery. **M1b** is manual
transaction entry, the seed taxonomy, the category management screen and the transaction list;
it needs the transaction table and the category tree, whose shapes M2 and M4 settle. Roadmap
M1's *Done when* list is satisfied only when M1a **and** M1b are complete, and the progress file
records it that way. The roadmap's acceptance criteria are not weakened, only sequenced.

### C2 — Requirements

**Account inventory.** Create, read and edit accounts of every `account_type`, with institution,
currency, country, subtype, masked number, opened date and the `include_in_net_worth` /
`is_liquid` toggles. `account_class` is never sent by the client — the V3 trigger derives it, and
the existing test proving a hand-set value is overwritten stays. Accounts are never deleted; there
is no delete route.

**Opening-balance evidence.** F05 requires *"account inventory, acknowledged opening balances"*.
An opening balance typed from a bank app is a stated fact and must read as one until a statement
supersedes it (Invariant I7). Two columns carry it: a free-text note recording where the figure
came from, and a confirmation timestamp that is `NULL` until the owner acknowledges the figure.
The UI shows an unacknowledged opening balance as unacknowledged. The evidence document link is
deliberately not modelled here — `document` arrives with M2/M10, and a forward migration adds the
reference then.

**Closed-account history.** F06 requires that M1 preserves closed accounts in historical views.
Closing sets `closed_on` and clears `is_active`; the row, its opening balance and its ownership
context survive. A database constraint asserts that an inactive account has a closing date. The
API derives `is_active` from `closed_on` so the two can never disagree. Closed accounts appear in
the inventory in their own group and remain available to every later historical view.

**Balances.** `current_balance` is the materialised `opening_balance + SUM(transactions)` and, in
M1a, equals the opening balance, carrying `current_balance_as_of` as its date. Balances are
reported in their **native** currency only. Translating a balance uses the as-of-date rate
computed live ([multi-currency §6](../../design/multi-currency-and-fx.html)), and presenting a
dated, coverage-labelled position is M9a's contract; M1a must not pre-empt it with an untagged
converted total. Group subtotals are per currency and are computed in Postgres, never summed in
TypeScript.

**Account numbers.** M1a stores `account_number_masked` only. `account_number_hash` exists for
matching an incoming statement to an account and stays `NULL` until M2 designs that flow, which is
where its cost is paid. No full account number is accepted, transmitted or logged in this
delivery — the never-store list is absolute
([security §1](../../operations/security-and-privacy.html)).

### C3 — Contract

`GET /accounts` returns every account, active and closed, with per-group native subtotals.
`POST /accounts` creates one. `PATCH /accounts/{id}` replaces the editable fields of one.
`GET /institutions` returns the shared seed rows plus the household's own, for the picker. All
four depend on `require_owner()` and scope every statement to the resolved household id
explicitly — the service connects privileged and bypasses RLS, so the predicate in the query is
the boundary, and RLS is depth behind it.

Money crosses the wire as a decimal string in both directions — `"-4500.0000"`, never a JSON
number and never parsed with `Number()` or `parseFloat` on either side. Incoming money is
validated as a decimal string and converted to `Decimal` in the service. Dates are ISO-8601 date
strings.

## 6. UI: the mockups are the implementation reference

`docs/product/mockups/` is the reference for every Phase 1 screen:

| Artboard | Screens |
|---|---|
| `OneLedger.dc.html` | Overview, Accounts, Transactions, Investments, Loans, Statement import |
| `Review.dc.html` | Review queue |
| `NetWorth.dc.html` | Net worth and the attribution waterfall |
| `LayoutMapping.dc.html` | Statement layout mapping |

Each UI-bearing task in the plan names the artboard screen it implements. The design language is
the artboards': IBM Plex Sans/Serif/Mono, tabular figures, a restrained neutral ground, colour
reserved for meaning, cards with hairline borders, mono uppercase section labels, and a
persistent header carrying the currency toggle.

**Where a mockup conflicts with an ADR or the money rules, the ADR wins, and the mockup label is
example data.** Specifically:

- Every currency, bank, country and fund name in the artboards — the `AED`/`INR` toggle, "UAE ·
  INDIA", the institution names, "₹3,10,000" — is example data. None becomes an identifier, a
  union type, an option list or a required seed row (ADR-0012). Currency options come from
  `GET /currencies`; grouping and symbols come from the `currency` row.
- Every money figure in the artboards is invented, as the artboards themselves state. Money is
  formatted by `formatMoney` from a decimal string and a `currency` row; no arithmetic happens in
  TypeScript.
- The artboards show a "Loans" destination. M8 is descheduled; the destination stays disabled or
  absent until a loan exists.
- The Accounts artboard groups by country. [UI §6](../../product/ui-information-architecture.html)
  specifies "grouped by class, then country", and that document states it wins over the mockups
  where they disagree. Class first, then country.
- Settings is a nav destination in every artboard but is explicitly not drawn in mockup set 01
  (the "NOT IN MOCKUP SET 01" frame). Settings screens follow the artboards' design language and
  UI §6's content list.

Accessibility is not optional and is not a later pass: keyboard operation throughout with a
visible focus ring, WCAG AA contrast, semantic tables, and screen-reader labels that include
currency and sign. Empty states teach and offer the first action. Loading uses skeletons, never a
spinner where a number belongs. Errors are specific.

## 7. Milestones after M1a

Each writes its own dated plan in `docs/superpowers/plans/` immediately before implementation,
against the tree at that time. Interfaces are taken from the working tree and from the schema
reference, not invented in advance.

| Milestone | Entry criteria | Scope boundary | Fixed interfaces to build from |
|---|---|---|---|
| **M1b** — manual transactions and taxonomy | M1a merged | Manual entry with FX frozen on save — taking the §B3 household row lock before the first frozen write — transaction list with filter/sort/search and inline category edit, seed taxonomy, category management. No import. | `schema.md` `0006` categories/merchants, `0007` transaction and `v_transaction_reportable`; classification §3 seed taxonomy |
| **M2** — importer, one layout end-to-end | M1b merged; FX backfill reaching 01 Jan 2024 verified | Generic extraction engine plus the mapping UI, proven by mapping one layout. Dedupe, FX enrichment with fetch-on-missing, reconciliation blocking on mismatch (I8), staging, review screen, atomic commit. Deferred in from Phase 0: CI and `scripts/backup-db.sh`. | `schema.md` `0008` document/import_batch/statement_layout; API contracts §4 staging contract and error table; ADR-0012 |
| **M3** — the remaining layouts | M2 merged and its layout reconciling on real statements | A mapping exercise: three more layouts, password-protected PDFs, multi-line descriptions, redacted golden fixtures, diagnostics panel, scanned-PDF refusal. New quirks are additive and considered. | M2's saved profile shape; ADR-0012 quirks vocabulary |
| **M4** — classification | M2 merged; M3 may run in parallel | Full cascade, rule CRUD with live preview, seed rules, merchant aliases, learn-from-correction with affected count, confidence scoring, Settings › Rules. | `schema.md` `0009` classification_rule; classification §4; ADR-0008 |
| **M5** — transfers and household outflows | M3 and M4 merged | Matching engine, FX-aware tolerance, kind-specific windows, confirmation in the review queue with nothing auto-linked, pending one-sided transfers re-matched on later imports, FX spread and total cost, `v_transaction_reportable` enforced with its static check, and the external-household-outflow reporting contract kept separate from consumption. | transfers-and-reconciliation §3; multi-currency §5; `schema.md` `0010`; ADR-0007 |
| **M9a** — early verified overview | M5 merged | Balances for included accounts, spending, external outflows, retained income, review links. Every headline carries its as-of date and verified / partially covered / estimated status. Price, FX and statement dates stay separate. Dated manual valuations without double counting. Does not require M7 or M10. | multi-currency §6; `schema.md` `0014` valuations; UI §2; ADR-0006 |
| **M6** — credit cards | M5 merged | Statement records, dues, utilisation, paid-in-full detection, purchase spending separated from bill settlement. | `schema.md` `0011`; ADR-0009 |
| **M7** — investments | M5 merged | Lot-level holdings, XIRR with bisection fallback, allocation, and lifecycle continuity through dividends, reinvestment, partial sales, charges, splits and platform transfers for the holdings actually owned. Unrecognised corporate actions block that holding's return claim and raise a review item. | `schema.md` `0012`; ADR-0005 |
| **M10** — documents and owner export | M2 merged | Document library with bidirectional traceability, checksum duplicate prevention, deletion blocked while referenced, and the owner export of open tabular records plus selected originals with provenance and a coverage manifest. Owner-only access; temporary files protected and cleaned up. | `schema.md` `0008`; F12 |
| **M9b** — full history and attribution | M7 merged and valuation inputs available | Consolidated history, the explanatory waterfall, the FX line and a visible residual. Reconcile the unapplied `0015` attribution shape with F03 **before** writing its migration, updating `schema.md` and `data-model.html` together. Specify the frozen-flow-to-closing-rate FX bridge and prove it with dated fixtures. | multi-currency §7; data model §14; ADR-0002 |

M8 (loans) stays descheduled. If a loan is confirmed, it takes its own dated plan and blocks
nothing.

## 8. Requirement coverage

| Requirement | Owned by | This tranche |
|---|---|---|
| F01 household boundary | M1a, M5 | M1a records ownership and preserves it through closure |
| F02 metric definitions | M5, M9a | Not started |
| F03 net-worth attribution | M9b | Not started |
| F04 money in transit | M5, M9a | Not started |
| F05 freshness and progressive onboarding | M1a, M2, M9a | M1a delivers account inventory and acknowledged opening balances with visible dates |
| F06 account and investment lifecycle | M1a, M7 | M1a preserves closed accounts; B3 makes the pair change follow ADR-0002 |
| F12 owner export | M10 | Not started |
| P01 | M9b | Not gated here |
| P02 | M5 / M9a | Not gated here |
| P03 | M9a | Not gated here |
| P04 | M5 / M9a | Not gated here |
| P11 | M10 | Not gated here |
| P12 | M7 / M9b | Not gated here |

No product acceptance gate closes in Deliveries A–C. Saying otherwise would misrepresent an
early usability checkpoint as milestone completion, which F05 warns against explicitly.

## 9. Verification

Every task leaves a check that failed before its implementation and passes after it. The canonical
gate is the repository `verify` skill: `python3 scripts/check-docs.py`, `pre-commit run
--all-files`, `cd api && uv run ruff check . && uv run mypy . && uv run pytest -q`,
migrations-from-empty against a scratch Compose project with every `db/tests/*.sql`, and
`cd web && npm run api:types:check && npm run typecheck && npm run typecheck:compat && npx vitest
run && npm run build` with the bundle secret scan.

A suite that collects nothing and exits zero is a failure, not a pass. Nothing is made green by
editing a test, loosening an assertion, deleting a case or weakening an invariant; a genuinely
wrong invariant is a superseding ADR, not an edit.

Deliveries A–C are complete when:

- The API package imports, and the full pytest suite collects and passes.
- No `db/tests/*.sql` file requires any named currency to be seeded.
- An account created with a nonzero opening balance and no explicit current balance reports that
  opening balance, dated, for both account classes.
- A database failure during owner binding sends the browser to the retry path, and a
  non-permitted address still sends it to `/unauthorized`.
- `docker compose config` with an overridden `POSTGRES_USER` and `POSTGRES_DB` resolves the
  overridden identity in the database service, its healthcheck, and Flyway's URL and user.
- A fresh database seeds one neutral household and person naming no currency, and the owner
  edits name, display name, tax residency and reporting pair in Settings › Household.
- The reporting pair is editable while nothing is frozen and, with the guard exercised against a
  seeded frozen-amount fixture, refused with the ADR-0002 explanation once something is.
- `.env.example` no longer carries the five household variables and the README setup table says
  where the reporting pair is chosen.
- Accounts of both classes can be created, edited, acknowledged and closed; closed accounts stay
  visible with their history; balances show natively with their as-of dates.
- Every check is recorded honestly in each PR from `.github/PULL_REQUEST_TEMPLATE.md`, with
  failures, skips and reasons stated rather than pre-checked.

Live checks that need real credentials or real statements stay the owner's, are recorded only in
`docs/superpowers/progress.md`, and are never described as passing on synthetic evidence.
