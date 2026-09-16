# Phase 4.1 subscriptions implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Numbering and status (16 September 2026).** This plan's Task 1 is **Phase 4.1 Task 4**,
delivered via PR #236. Its Tasks 2 and 3 are superseded by the
[subscription registry plan](2026-09-16-phase-4-1-subscription-registry.md), which adds billing
periods at the owner's request and continues as Phase 4.1 Tasks 5, 6 and 7.

**Goal:** Deliver the [subscriptions specification](../specs/2026-09-16-phase-4-1-subscriptions.md):
recurring payments detected automatically, each a subscription or not, corrected by the owner's one
"not a subscription" switch, and a Subscriptions screen with costs, renewals, history and the cards
they depend on.

**Architecture:** Task 1 turns the existing recurring engine opt-out — automatic creation in
`POST /recurring/refresh`, the subscription flag, amount clusters, the semiannual and custom
cadences — and moves recurring payments to their own Recurring screen (V57). Task 2 adds what the
owner sets and what a subscription reads — status, custom interval, payment channels, a single
charge added as a subscription, equivalents, summary, upcoming and detail (V58). Task 3 adds the
Subscriptions and subscription detail screens and the Transactions action.

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; two forward-only
migrations (V57, V58), no new dependency. This group is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` after the previous one merges, and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings; TypeScript formats only and never computes a monthly,
  annual or total figure. `scripts/check-identifiers.py` refuses `Number`/`parseFloat` on money.
- Every route uses `require_owner` and an explicit `owner.household_id` filter. Every GET opens
  `SET TRANSACTION READ ONLY` and writes nothing. Aggregates read `v_transaction_reportable`.
- `category`, `merchant` and the new seed `payment_channel` table are shared seed tables with a
  nullable `household_id` and no `UNIQUE (household_id, id)`: reference them with a plain
  `REFERENCES x(id)` and enforce the tenant boundary in the route, as `routes/recurring.py` already
  does for merchant and category. Composite tenant-scoped FKs apply to household-only targets.
- No code enumerates an institution, store or wallet name (ADR-0012): payment channels are seeded
  rows, and the subscription-category default is a seeded mapping, not a slug list in Python.
- The subscription flag's default is deterministic; no AI before Phase 5 (ADR-0003).
- An owner correction is final: once `subscription_set_by_owner` is true no refresh changes
  `is_subscription`; once `named_by_owner` is true no refresh changes `label`; a payment the owner
  removed is never re-created.
- A charge belongs to at most one series, and refresh never inserts a charge that already belongs to
  one.
- The original charge amount is the transaction's original pair when present (`original_amount`,
  `original_currency`), otherwise `amount`/`currency`, always taken as a magnitude with `abs()`.
  Clustering, price changes, stability and the subscription's own amount all read that pair.
- Tests cover major behaviour: money, authorization, household isolation, database writes, and one
  web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money, auth or
  migration findings, `scripts/verify.sh` once, push, PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | opt-out discovery, flag, clusters, cadences, Recurring screen | `feature/p4-1s-auto-discovery` | 1350 | 1000 | yearly fix |
| 2 | status, custom interval, channels, single-charge add, equivalents, detail | `feature/p4-1s-registry` | 1400 | 1000 | 1 |
| 3 | Subscriptions and detail screens, Transactions action | `feature/p4-1s-screens` | 1200 | 550 | 2 |

Task 1 and Task 2 sit close to the 1,500 production budget. If Task 1 overruns, move the Recurring
screen into Task 3 and keep the existing panel wired to the new routes instead.

## Task 1 — opt-out discovery and the Recurring screen

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1s-auto-discovery` / `develop` / `develop`.
**Depends:** PR #235 (26-month window) merged.
**Files:** create `db/migrations/V57__recurring_opt_out.sql`, `db/tests/0054_recurring_opt_out.sql`,
`web/app/recurring/page.tsx`, `web/app/recurring/recurring-screen.tsx` and its test; update
`api/oneledger/recurring.py`, `api/oneledger/routes/recurring.py`, `api/tests/test_recurring.py`,
`api/tests/test_recurring_live.py`, `web/app/workspace.tsx`, `web/app/spending/spending-screen.tsx`
and its test, `web/app/import/review-screen.tsx` and its test, `web/app/globals.css`; delete
`web/app/spending/recurring-panel.tsx` and its test; generated types; `docs/design/api-contracts.html`;
`docs/superpowers/schema.md`; the earlier
[budgets and recurring specification](../specs/2026-09-16-phase-4-1-budgets-recurring.md) (mark its
confirm and accept-price sections superseded); progress.
**Contract:**

- **V57 — enum.** `ALTER TYPE recurring_cadence ADD VALUE 'semiannual'` and
  `ADD VALUE 'custom'`, both here; nothing in V57 references either value (Postgres refuses a value
  used in the transaction that added it), so V58's `custom` CHECK is safe.
- **V57 — series columns.** `recurring_series` gains
  `is_subscription BOOLEAN NOT NULL DEFAULT false`,
  `subscription_set_by_owner BOOLEAN NOT NULL DEFAULT false`,
  `detection_method TEXT NOT NULL DEFAULT 'detected'` checked to `detected` or `owner`,
  `named_by_owner BOOLEAN NOT NULL DEFAULT false`, and `native_amount NUMERIC(20,4)` with
  `native_currency CHAR(3) REFERENCES currency(code)` (both null or both set, amount positive).
- **V57 — member uniqueness.** Before adding `uq_recurring_series_member_transaction UNIQUE
  (household_id, transaction_id)`, delete duplicate member rows, keeping for each transaction the row
  on the newest series (open before ended, then latest `created_at`, then id). Members are derived
  from the ledger, not evidence: V56's confirm, end and confirm again on one identity re-inserted the
  same charges into the new series. Report the deleted count with `RAISE NOTICE` only.
- **V57 — identity indexes.** Drop `uq_recurring_series_open_merchant` and
  `uq_recurring_series_open_match_key`: one identity may carry several open series.
- **V57 — dismissal.** `recurring_dismissal` gains nullable `anchor_amount NUMERIC(20,4)` and
  `anchor_currency CHAR(3)` (both null or both set). Its two unique indexes are recreated to include
  the anchor with `NULLS NOT DISTINCT`, and the dismiss write's `ON CONFLICT` target matches them. A
  null anchor keeps V56's meaning: the whole identity.
- **V57 — seed mapping.** `recurring_subscription_category (category_id UUID PRIMARY KEY REFERENCES
  category(id), is_subscription BOOLEAN NOT NULL)`, with RLS enabled and only a read policy for
  `authenticated`, as `currency` has. It is seeded by looking up seed categories with
  `slug = … AND household_id IS NULL`: the Subscriptions tree and Streaming true; Housing,
  Utilities, Insurance, Debt service, Financial and Taxes false. A child category with no row uses
  its parent's row.
- **Detection (`api/oneledger/recurring.py`).**
  - Adds the semiannual window (175–190 days) and `CADENCE_DAYS` entries `semiannual: 182`.
  - `SpendRow` gains `amount` and `currency`, the original pair defined in the global contract.
  - Clustering is deterministic. Per identity and per currency, process rows by `booked_date`, then
    `transaction_id`. A row joins the cluster whose current median `m` (the members'
    `_median_amount`) satisfies `abs(amount - m) <= 0.2 * m`, choosing the nearest median and then
    the earliest-created cluster; otherwise it starts a cluster.
  - Two clusters overlap when `first_a <= last_b AND first_b <= last_a` (a shared day overlaps).
    Walking clusters by first date, a cluster that overlaps no other merges into the earlier cluster
    whose last charge is latest, then nearest in amount — one series whose price changed. Overlapping
    clusters stay apart.
  - The existing occurrence, median-gap and two-thirds rules then run per resulting group.
  - **Fallback:** when no group on an identity passes, the whole identity (per currency) is
    evaluated as one group, exactly as today, so a bill whose amount varies more than 20% is still
    detected.
  - A candidate carries the latest charge's original `amount` and `currency`, its group's median
    amount, and `amounts_are_stable` (every original amount within 5% of the median).
- **`POST /recurring/refresh`** takes the household lock first and applies three rules in order.
  - **Extend.** A reportable charge on an identity and currency not yet in any series, dated after an
    open series' `last_seen_on`, joins the open series whose current `native_amount` is nearest
    within 20%. When none is within 20% and exactly one open series exists on that identity and
    currency, it joins that one (a larger price change). Otherwise it stays unassigned.
  - **Create.** Detection runs over charges not already in any series. Each resulting group becomes
    a series unless a dismissal on its identity has a null anchor, or an anchor in the same currency
    within 20% of the group's median. The series gets `detection_method = 'detected'`, the detected
    label, cadence and category, and its members.
  - **Sync.** For every open series: `native_amount`, `native_currency` and `expected_amount_base`
    move to the latest member (the price follows without an accept step), `last_seen_on` to its date;
    and where `subscription_set_by_owner` is false, `is_subscription` is recomputed — the effective
    category's mapping when it has one, otherwise `amounts_are_stable` over the series' members. This
    also gives series created before V57 their flag and amounts.
  - It never inserts a charge that already belongs to a series. It is idempotent: a second call
    creates, adds and changes nothing. It returns `seriesCreated`, `seriesTouched` and `membersAdded`.
- **`GET /recurring`** returns open series with `isSubscription`, `detectionMethod`, `needsName`
  (true only for a detected series identified by description text, not by merchant, whose label the
  owner has not set), `nativeAmount`, `nativeCurrency`, `priceChangedFrom`/`priceChangedOn` when the
  latest member's original amount differs from the previous member's in the same currency, and
  `status` `active` or `overdue`. It no longer returns `candidates`.
- **`PATCH /recurring/{seriesId}`** sets `isSubscription` (also setting
  `subscription_set_by_owner`) and `label` (also setting `named_by_owner`); unknown fields 422, a
  foreign series 404, an ended series 409.
- **`POST /recurring/{seriesId}/remove`** ends the series with `ended_reason = 'not_recurring'` and
  records a dismissal anchored on the series' latest `native_amount` and `native_currency`, in one
  transaction; 409 when already ended.
- **Removed:** `POST /recurring/confirm`, `POST /recurring/{seriesId}/accept-price` and
  `POST /recurring/{seriesId}/end`, with their tests and contract rows. `POST /recurring/dismiss`
  remains only as the remove path's write. Task 2's status replaces `/end`.
- **Web — Recurring screen.** A new **Recurring** section after Spending (`/recurring`, added in the
  five places `workspace.tsx` requires) runs `POST /recurring/refresh` when it opens, then lists every
  open series with its label, cadence, original amount and currency, last seen, status, price change
  with both amounts, **Not a subscription** / **A subscription**, rename, and **Remove** (which asks
  first). The Spending screen drops its recurring panel and links to Recurring.
- **Web — import.** `web/app/import/review-screen.tsx` runs `POST /recurring/refresh` after a
  successful commit and ignores its failure; the commit has already completed.

**Tests:** db — both enum values, the new columns and shapes, the member uniqueness after duplicate
removal (a fixture with one charge in an ended and an open series keeps the open one), the dismissal
indexes with a null anchor refusing a duplicate, the seed mapping looked up on seed rows only. Pure
— clustering is order-independent for the same rows; overlapping clusters stay apart; sequential
clusters merge; a 3% change stays one cluster; one odd charge does not split a subscription; a bill
varying 30% is still detected through the fallback; semiannual detected; `amounts_are_stable`. Live
— refresh creates a subscription from a Streaming category and a non-subscription from Utilities; an
uncategorised stable group is a subscription and a varying one is not; a second refresh changes
nothing; `GET /recurring` changes nothing; an owner flip and a rename survive refresh; a removed
payment is never re-created, including after its earliest charge ages out of the window; two
overlapping services on one descriptor become two series and later charges extend the nearest one; a
price change over 20% still extends the single series; a cancelled-then-resumed payment is detected
again from new charges only; a series created before V57 gets its flag and amounts on refresh; a
charge in a foreign currency keeps its original pair; foreign-household refusals on PATCH and remove.
Web — the flip posts `isSubscription`, remove asks first, refresh runs on open once, the import
commit triggers refresh and a refresh failure does not surface as a commit failure.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_recurring.py tests/test_recurring_live.py)` then
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`; shared types command;
`(cd web && npx vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** after an import, recurring payments appear on the Recurring screen without
confirmation, "not a subscription" sticks through another import, and a removed payment does not
return.

## Task 2 — registry: status, channels, single charges and figures

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p4-1s-registry` /
`develop` / `develop`.
**Depends:** Task 1.
**Files:** create `db/migrations/V58__subscription_registry.sql`,
`db/tests/0055_subscription_registry.sql`, `api/tests/test_subscriptions_live.py`; update
`api/oneledger/recurring.py`, `api/oneledger/routes/recurring.py`; generated types; API contracts;
`docs/superpowers/schema.md`; `docs/superpowers/project-register.md`; progress.
**Contract:**

- **V58 — status.** `recurring_series` gains `lifecycle TEXT NOT NULL DEFAULT 'active'` checked to
  `active`, `trial`, `paused` or `cancelled`, and `custom_interval_days SMALLINT` checked 7–730 and
  required exactly when `cadence = 'custom'`. The constraint on ended rows is
  `ended_on IS NULL OR lifecycle = 'cancelled' OR ended_reason = 'not_recurring'`. Before adding it,
  V58 sets `lifecycle = 'cancelled'` on existing ended rows whose reason is not `not_recurring`,
  with `trg_preserve_ended_recurring_series` disabled for that one statement and re-enabled after.
- **V58 — channels.** `payment_channel`: `id`, nullable `household_id`, `name` bounded 1–60,
  `sort_order`, `is_active`, with RLS policies copied from `category`'s exactly. Seed rows
  (household null) are named as data: a direct card, an app store, a play store, a wallet autopay, a
  bank autopay, a bank debit, other. `recurring_series` gains `payment_channel_id UUID REFERENCES
  payment_channel(id)`.
- **Cancel and reopen.** Setting `lifecycle = 'cancelled'` writes `ended_on` and
  `ended_reason = 'cancelled'` in the same UPDATE, which passes the preserve trigger because it
  checks the old row. A cancelled series is final: reopening it is 409.
- **Figures.** Rows returned by `GET /recurring` gain `lifecycle`, `customIntervalDays`,
  `paymentChannelId`/`Name`, the paying account (the latest member's account id and name) with its
  masked identifier, `nextExpectedOn` (last seen plus the cadence, or plus `custom_interval_days`),
  and `annualAmount`/`monthlyAmount` in the original currency with `annualAmountBase`/
  `monthlyAmountBase` from `expected_amount_base`.
  - Annual is `quantize(amount × charges_per_year)` rounded once at the end, where charges per year
    is 52, 12, 4, 2 or 1, or `365 / custom_interval_days` without intermediate rounding.
  - Monthly is `quantize(unrounded annual / 12)`.
  - Both quantize to 4 places half-even.
  - Paused and cancelled rows report their figures but are excluded from totals.
- **Masked identifier.** The paying account's identifier is chosen from `account_identifier` rows
  that are active: `card` before `bank_account` before `iban`, then `created_at`, then `id`. Only
  `masked_value` is ever selected, never a full or normalized value (ADR-0021).
- **`GET /recurring/summary`** over active and trial subscriptions returns `monthlyBase` and
  `annualBase` as the sums of the rows' rounded figures (so rows add up to the total, stated as
  estimates from the latest charges), `activeCount`, `renewingIn30Days` (next expected within 30
  days of today), `priceChangesIn90Days`, the base currency, and `upcoming` — each subscription due
  within 30 days with its date, label, original amount and currency, and paying account name.
- **`GET /recurring/{seriesId}`** returns the row plus `firstSeenOn`, `spentSoFarBase` (the sum of
  member `amount_base`), `last12MonthsBase`, `priceHistory` (member original amounts by date, one
  entry per change of amount or currency) and `charges` (every member with date, original amount and
  currency, base amount and account name). Foreign 404.
- **`GET /recurring/by-account`** groups active and trial subscriptions by paying account: account
  id, name, masked identifier, subscription count and labels, and `monthlyBase`.
- **`PATCH /recurring/{seriesId}`** also accepts `lifecycle`, `cadence` with `customIntervalDays`,
  `paymentChannelId` (a seed or own row; another household's 404, unknown 422), and `categoryId`.
- **`POST /recurring/from-transaction`** takes `transactionId`, `cadence` (or `custom` with
  `customIntervalDays`), `label`, optional `isSubscription` (default true) and `idempotencyKey`.
  - The transaction must be this household's reportable spending charge (another household's 404,
    income or a non-reportable row 422) and not already a member (409).
  - It creates a series with `detection_method = 'owner'`, `named_by_owner` and
    `subscription_set_by_owner` true, that one member, and its amounts from the charge.
  - Replay 200 on an identical body, 409 on a different one.
  - Task 1's Extend rule then grows it from later charges, so a yearly plan seen once is followed
    next year.
- **`GET /payment-channels`** lists seed and own active channels.
- **Project register** rows: a catalogue of known services per billing descriptor, and merging two
  series.

**Tests:** db — lifecycle and custom shapes, the ended-row backfill passing the new constraint, the
channel seed and its RLS matching `category`. Live — equivalents exact for weekly, monthly,
quarterly, semiannual, annual and a custom 45-day interval (rounded once), in original and base
currency; summary totals equal the sum of row figures and exclude paused and cancelled;
renewing-in-30-days and upcoming boundaries; next expected for custom; detail spent so far, last 12
months and a price history with one change; by-account grouping; the masked identifier chosen by
kind and never unmasked; a single charge added as annual with next expected a year on, extended by a
later charge, replay 200 and conflict 409, another household's or an income transaction refused, an
existing member 409; cancelling ends the series and reopening is 409; another household's channel
404; foreign-household refusals on every new route.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_subscriptions_live.py tests/test_recurring_live.py)` then
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`; shared types command;
`(cd web && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** every figure the Subscriptions screen needs is available from the API as exact
strings, and a yearly plan seen once can be added and followed.

## Task 3 — Subscriptions and detail screens

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p4-1s-screens` /
`develop` / `develop`.
**Depends:** Task 2.
**Files:** create `web/app/subscriptions/page.tsx`, `web/app/subscriptions/subscriptions-screen.tsx`,
`web/app/subscriptions/[seriesId]/page.tsx`, `web/app/subscriptions/subscription-detail.tsx` and
tests; update `web/app/workspace.tsx`, `web/app/recurring/recurring-screen.tsx` (status and channel
where relevant), `web/app/transactions/transactions-screen.tsx` (add a charge as a subscription),
`web/app/globals.css`; progress.
**Contract:**

- **Navigation.** A **Subscriptions** section after Spending, added in the five places
  `workspace.tsx` requires.
- **Detail route.** `web/app/subscriptions/[seriesId]/page.tsx` follows the existing Next 16 pattern
  of `web/app/investments/[holdingId]/page.tsx`: a client page reading `params` as a Promise with
  `use()` and rendering `<Workspace section="subscriptions-detail" seriesId={…} />`. Workspace gains
  that section and a `seriesId` prop, and `sectionFromPath()` checks the `/subscriptions/` prefix
  before the exact `/subscriptions` match.
- **Subscriptions** runs `POST /recurring/refresh` on open, then shows:
  - the summary line and cards — monthly equivalent, annual spend, active count, renewing in the
    next 30 days, price changes — labelled as estimates from the latest charges;
  - **Upcoming**: date, name, original amount and currency, paying account;
  - **All subscriptions**: name with a **Needs a name** marker where the server says so, category,
    original amount per cadence, paying account, last charged, next expected, annualised amount,
    status, and a price change note with both amounts; each links to its detail;
  - **By card**: each paying account with its masked identifier, subscription count, names and
    monthly figure;
  - per row, **Not a subscription**, rename and status.
- **Subscription detail** shows the amount per cadence, category, next expected, paying account and
  channel, started, spent so far, last 12 months, price history, and the charges with links to the
  ledger; it offers rename, status, channel, custom cadence and **Not a subscription**.
- **Transactions.** A spending charge not already in a series offers **Add as subscription**,
  asking for the cadence and a name and posting `POST /recurring/from-transaction` with one
  idempotency key per draft, reused on retry.
- Every amount is a server string; no monthly, annual or total is computed in the browser.

**Tests:** the summary, upcoming and list render from server strings; "not a subscription" patches
and the row leaves the list; by-card grouping renders with masked identifiers; the detail shows the
price history and charges; the detail route renders for a series id; adding a charge as a
subscription posts the exact body and reuses its key on retry; refresh runs on open once.
**Commands:** `(cd web && npx vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** the owner opens Subscriptions after an import and sees what they pay for, costs,
renewals and which card each depends on, corrects one with "not a subscription", and adds a yearly
plan seen once from the ledger.
