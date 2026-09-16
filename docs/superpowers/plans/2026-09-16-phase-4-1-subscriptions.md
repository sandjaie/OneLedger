# Phase 4.1 subscriptions implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Deliver the [subscriptions specification](../specs/2026-09-16-phase-4-1-subscriptions.md):
recurring payments detected automatically, each a subscription or not, corrected by the owner's one
"not a subscription" switch, and a Subscriptions screen with costs, renewals, history and the cards
they depend on.

**Architecture:** Task 1 turns the existing recurring engine opt-out — automatic creation in
`POST /recurring/refresh`, the subscription flag, amount clusters and semiannual cadence (V57).
Task 2 adds what the owner sets and what the screen reads — status, custom cadence, payment
channels, a single charge marked as a subscription, equivalents, summary, upcoming and detail
(V58). Task 3 adds the Subscriptions and Recurring screens.

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; two forward-only
migrations (V57, V58), no new dependency. This group is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` after the previous one merges, and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings; TypeScript formats only and never computes a monthly or
  annual figure. `scripts/check-identifiers.py` refuses `Number`/`parseFloat` on money.
- Every route uses `require_owner` and an explicit `owner.household_id` filter. Every GET opens
  `SET TRANSACTION READ ONLY` and writes nothing. Aggregates read `v_transaction_reportable`.
- `category`, `merchant` and the new seed `payment_channel` table are shared seed tables with a
  nullable `household_id` and no `UNIQUE (household_id, id)`: reference them with a plain
  `REFERENCES x(id)` and enforce the tenant boundary in the route, as `routes/recurring.py` already
  does for merchant and category. Composite tenant-scoped FKs apply to household-only targets.
- No code enumerates an institution, store or wallet name (ADR-0012): payment channels are seeded
  rows, and the subscription-category default is a seeded mapping, not a slug list in Python.
- The subscription flag's default is deterministic; no AI before Phase 5 (ADR-0003).
- An owner correction is final: once `subscription_set_by_owner` is true, no refresh changes
  `is_subscription`; a payment the owner removed is never re-created.
- Tests cover major behaviour: money, authorization, household isolation, database writes, and one
  web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money, auth or
  migration findings, `scripts/verify.sh` once, push, PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | opt-out discovery, subscription flag, clusters, semiannual | `feature/p4-1s-auto-discovery` | 1100 | 900 | yearly fix |
| 2 | status, custom cadence, channels, single-charge add, equivalents, detail | `feature/p4-1s-registry` | 1150 | 900 | 1 |
| 3 | Subscriptions and Recurring screens | `feature/p4-1s-screens` | 1100 | 500 | 2 |

## Task 1 — opt-out discovery

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1s-auto-discovery` / `develop` / `develop`.
**Depends:** PR #235 (26-month window) merged.
**Files:** create `db/migrations/V57__recurring_opt_out.sql`, `db/tests/0054_recurring_opt_out.sql`;
update `api/oneledger/recurring.py`, `api/oneledger/routes/recurring.py`,
`api/tests/test_recurring.py`, `api/tests/test_recurring_live.py`,
`web/app/spending/recurring-panel.tsx` and its test, `web/app/import/review-screen.tsx` and its test;
generated types; `docs/design/api-contracts.html`; `docs/superpowers/schema.md`; progress.
**Contract:**

- V57:
  - `ALTER TYPE recurring_cadence ADD VALUE 'semiannual'`. The new value is not used in the same
    migration (Postgres refuses a value added in the same transaction).
  - `recurring_series` gains `is_subscription BOOLEAN NOT NULL DEFAULT false`,
    `subscription_set_by_owner BOOLEAN NOT NULL DEFAULT false`, `detection_method TEXT NOT NULL
    DEFAULT 'detected'` checked to `detected` or `owner`, `named_by_owner BOOLEAN NOT NULL DEFAULT
    false`, `native_amount NUMERIC(20,4)` and `native_currency CHAR(3) REFERENCES currency(code)`
    (both set from the latest charge, both null or both set, amount positive), and
    `anchor_amount NUMERIC(20,4)` with `anchor_currency CHAR(3)` naming the cluster.
  - Drops `uq_recurring_series_open_merchant` and `uq_recurring_series_open_match_key`: one identity
    may now carry several open series. Adds `uq_recurring_series_member_transaction UNIQUE
    (household_id, transaction_id)` on `recurring_series_member`, so a charge belongs to at most one
    series. If existing rows would violate it, the migration fails with a named message rather than
    deleting evidence; the V56 data model allowed at most one open series per identity, so this is
    expected to be empty.
  - `recurring_dismissal` gains nullable `anchor_amount` and `anchor_currency`; a null anchor keeps
    its V56 meaning (the whole identity). Its unique indexes include the anchor.
  - `recurring_subscription_category`: a seed mapping `(category_id REFERENCES category(id) PRIMARY
    KEY, is_subscription BOOLEAN NOT NULL)`, readable by everyone, seeded by slug for the
    Subscriptions tree and Streaming (true) and Housing, Utilities, Insurance, Debt service,
    Financial and Taxes (false). A child category inherits its parent's row when it has none.
  - `preserve_ended_recurring_series()` is unchanged.
- `api/oneledger/recurring.py`:
  - adds the semiannual window (175–190 days) and its `CADENCE_DAYS` entry (182);
  - `SpendRow` gains `amount` and `currency` (the original);
  - `detect_candidates` clusters each identity's rows by original amount per currency (a row joins
    a cluster within 20% of its median), merges clusters whose date ranges do not overlap (one
    series, price changed), keeps overlapping clusters apart, and applies the existing occurrence,
    median-gap and two-thirds rules per resulting group;
  - a candidate carries `anchor_amount`, `anchor_currency` (the group's earliest charge), the latest
    charge's original `amount`/`currency`, and `amounts_are_stable` (every original amount within 5%
    of the median).
- `POST /recurring/refresh` becomes the only creator:
  - for each detected group with no open series holding any of its charges, not covered by a
    dismissal on its identity whose anchor is null or within 20% of the group's anchor, and not
    covered by an owner-ended series on that identity and anchor with `ended_on` at or after the
    group's last charge, it creates a series (`detection_method = 'detected'`, label from the
    detection, cadence, category, native and base amounts from the latest charge, `is_subscription`
    from `recurring_subscription_category` for the effective category, otherwise
    `amounts_are_stable`) and records every charge in the group as a member;
  - it keeps adding later charges to existing series as today, and moves `native_amount`,
    `native_currency` and `expected_amount_base` to the latest member — the price follows without an
    accept step;
  - it never writes `is_subscription` on a series with `subscription_set_by_owner`, and never writes
    `label` on a series with `named_by_owner`;
  - it takes the household lock first, is idempotent (a second call creates and adds nothing), and
    returns `seriesCreated`, `seriesTouched` and `membersAdded`.
- `GET /recurring` returns open series with `isSubscription`, `detectionMethod`, `needsName`
  (`named_by_owner` is false and the series was detected), `nativeAmount`, `nativeCurrency`,
  `priceChangedFrom`/`priceChangedOn` when the latest member's original amount differs from the
  previous member's in the same currency, and `status` `active` or `overdue`. It no longer returns
  `candidates`: detection is visible only as created series.
- `PATCH /recurring/{seriesId}` sets `isSubscription` (setting `subscription_set_by_owner`) and
  `label` (setting `named_by_owner`); unknown fields 422, a foreign series 404, an ended series 409.
- `POST /recurring/{seriesId}/remove` ends the series with reason `not_recurring` and records a
  dismissal for its identity and anchor in one transaction; 409 when already ended.
- `POST /recurring/confirm` and `POST /recurring/{seriesId}/accept-price` are removed, with their
  tests and contract rows. `POST /recurring/dismiss` stays only as the remove path's internal write.
- Web: the existing Recurring panel lists created series with **Not a subscription** / **A
  subscription** and **Remove**, runs `POST /recurring/refresh` when it opens, and shows the price
  change with both amounts. The import review screen runs the same refresh after a successful
  commit and ignores its failure (a commit never fails because discovery did).

**Tests:** db — cadence value, new columns and shapes, the member uniqueness, dismissal anchor
indexes, the seed mapping rows. Pure — clusters: overlapping clusters stay apart, sequential ones
merge, a 3% change stays one cluster; semiannual detected; `amounts_are_stable`. Live — refresh
creates a subscription from a Streaming category and a non-subscription from Utilities; an
uncategorised stable group becomes a subscription and a varying one does not; a second refresh
creates nothing; `GET /recurring` creates nothing; an owner flip survives refresh; a rename survives
refresh; a removed payment is never re-created; two overlapping services on one descriptor become
two series; a later charge moves the price and reports the change; foreign-household refusals on
PATCH and remove. Web — the flip posts `isSubscription`, remove confirms, refresh runs on open, the
import commit triggers refresh and a refresh failure does not surface as a commit failure.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_recurring.py tests/test_recurring_live.py)` then
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`; shared types command;
`(cd web && npx vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** after an import, recurring payments appear without confirmation, "not a
subscription" sticks through another import, and a removed payment does not return.

## Task 2 — registry: status, channels, single charges and figures

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p4-1s-registry` /
`develop` / `develop`.
**Depends:** Task 1.
**Files:** create `db/migrations/V58__subscription_registry.sql`,
`db/tests/0055_subscription_registry.sql`, `api/tests/test_subscriptions_live.py`; update
`api/oneledger/recurring.py`, `api/oneledger/routes/recurring.py`, `api/oneledger/money.py` only if
a shared quantize helper is needed; generated types; API contracts; `docs/superpowers/schema.md`;
`docs/superpowers/project-register.md`; progress.
**Contract:**

- V58:
  - `ALTER TYPE recurring_cadence ADD VALUE 'custom'`, unused in the same migration.
  - `recurring_series` gains `lifecycle TEXT NOT NULL DEFAULT 'active'` checked to `active`,
    `trial`, `paused` or `cancelled`; `custom_interval_days SMALLINT` checked 7–730 and required
    exactly when cadence is `custom`; `payment_channel_id UUID REFERENCES payment_channel(id)`.
    `cancelled` requires `ended_on`; an ended series is `cancelled` or removed.
  - `payment_channel`: `id`, nullable `household_id`, `name` bounded 1–60, `sort_order`,
    `is_active`, seeded with household-null rows for a direct card, an app store, a play store, a
    wallet autopay, a bank autopay and a bank debit, other, named as data. RLS lets a household read
    seed rows and its own.
  - `recurring_series.detection_method` also allows `owner` for a single-charge series (already in
    V57's check).
- `GET /recurring` rows gain `lifecycle`, `customIntervalDays`, `paymentChannelId`/`Name`,
  `paymentAccountId`/`Name`/`Masked` (the latest member's account and its card or account
  `account_identifier.masked_value`, masked only), `nextExpectedOn` (last seen plus the cadence, or
  plus `custom_interval_days`), `annualAmount`/`monthlyAmount` in the original currency and
  `annualAmountBase`/`monthlyAmountBase` from `expected_amount_base`. Annual is the amount times 52,
  12, 4, 2, 1 or `365 / custom_interval_days`; monthly is annual divided by 12; both `Decimal`,
  4 places half-even. Paused and cancelled rows report the figures but are excluded from totals.
- `GET /recurring/summary` returns, over active and trial subscriptions: `monthlyBase`, `annualBase`
  (estimates, stated as built from the latest charges), `activeCount`, `renewingIn30Days` (next
  expected within 30 days of today), `priceChangesIn90Days`, the household base currency, and
  `upcoming` — each subscription due within 30 days with its date, label, original amount and
  currency, and paying account name.
- `GET /recurring/{seriesId}` returns the row plus `firstSeenOn`, `spentSoFarBase` (sum of member
  `amount_base`), `last12MonthsBase`, `priceHistory` (member original amounts in date order, one
  entry per change of amount or currency) and `charges` (every member: date, original amount and
  currency, base amount, account name). Foreign 404.
- `GET /recurring/by-account` groups active and trial subscriptions by paying account: account id,
  name, masked identifier, subscription count and labels, and `monthlyBase`.
- `PATCH /recurring/{seriesId}` also accepts `lifecycle` (`cancelled` ends the series with that
  reason in the same statement), `cadence` with `customIntervalDays`, `paymentChannelId` (seed or
  own row, else 404/422 as merchant and category are), and `categoryId`.
- `POST /recurring/from-transaction` takes `transactionId`, `cadence` (or `custom` with
  `customIntervalDays`), `label`, optional `isSubscription` (default true) and `idempotencyKey`; the
  transaction must be this household's reportable spending charge (else 404/422) and not already a
  member (409). It creates a series with `detection_method = 'owner'`, `named_by_owner` and
  `subscription_set_by_owner` true, that one member, and native and base amounts from the charge.
  Replay 200 on an identical body, 409 on a different one.
- `GET /payment-channels` lists seed and own active channels.
- Project register rows: a catalogue of known services per billing descriptor, and merging two
  series.

**Tests:** db — lifecycle and custom shapes, the channel seed and its RLS. Live — equivalents exact
for weekly, monthly, quarterly, semiannual, annual and a custom 45-day interval, in original and
base currency; summary totals excluding paused and cancelled; renewing-in-30-days and upcoming
boundaries; next expected for custom; detail spent so far, last 12 months and a price history with
one change; by-account grouping; the masked identifier never unmasked; a single charge marked
annual with next expected a year on, replay 200 and conflict 409, a foreign or income transaction
refused, an existing member 409; cancelling ends the series; a channel of another household 404;
foreign-household refusals on every new route.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_subscriptions_live.py tests/test_recurring_live.py)` then
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`; shared types command;
`(cd web && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** every figure the Subscriptions screen needs is available from the API as exact
strings, and a yearly plan seen once can be added.

## Task 3 — Subscriptions and Recurring screens

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p4-1s-screens` /
`develop` / `develop`.
**Depends:** Task 2.
**Files:** create `web/app/subscriptions/page.tsx`, `web/app/subscriptions/subscriptions-screen.tsx`,
`web/app/subscriptions/[seriesId]/page.tsx`, `web/app/subscriptions/subscription-detail.tsx`,
`web/app/recurring/page.tsx`, `web/app/recurring/recurring-screen.tsx` and tests; update
`web/app/workspace.tsx`, `web/app/spending/spending-screen.tsx` (the Recurring panel moves out),
`web/app/transactions/transactions-screen.tsx` (mark a charge as a subscription),
`web/app/globals.css`; progress.
**Contract:**

- Navigation gains **Subscriptions** and **Recurring** after Spending, each a section added in the
  five places `workspace.tsx` requires. The Spending screen keeps budgets and drops the recurring
  panel, linking to the two new screens instead.
- **Subscriptions** runs `POST /recurring/refresh` on open, then shows:
  - the summary line and cards — monthly equivalent, annual spend, active count, renewing in the
    next 30 days, price changes — labelled as estimates from the latest charges;
  - **Upcoming**: date, name, original amount and currency, paying account;
  - **All subscriptions**: name (with a **Needs a name** marker), category, original amount per
    cadence, paying account, last charged, next expected, annualised amount, status, and a price
    change note with both amounts;
  - **By card**: each paying account with its subscription count, names and monthly figure;
  - per row, **Not a subscription**, rename, and status.
- **Subscription detail** (`/subscriptions/<id>`): amount per cadence, category, next expected,
  paying account and channel, started, spent so far, last 12 months, price history, and the charges
  with links to the ledger; rename, status, channel, custom cadence, and **Not a subscription**.
- **Recurring** runs the same refresh and lists every open recurring payment that is not a
  subscription, with **A subscription** and **Remove** (which asks first).
- The Transactions screen offers **Mark as subscription** on a spending charge that is not already
  in a series, asking for the cadence and a name, and posting `POST /recurring/from-transaction`
  with one idempotency key per draft.
- Every amount is a server string; no monthly, annual or total is computed in the browser.

**Tests:** subscriptions summary, upcoming and list render from server strings; "not a subscription"
patches and the row leaves the list; by-card grouping renders; detail shows price history and
charges; recurring lists non-subscriptions and "a subscription" moves one back; remove asks first;
mark a charge as a subscription posts the exact body and reuses its key on retry; refresh runs on
open without a loop.
**Commands:** `(cd web && npx vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** the owner opens Subscriptions after an import and sees what they pay for, costs,
renewals and which card each depends on, corrects one with "not a subscription", and adds a yearly
plan seen once from the ledger.
