# Phase 4.1 Tasks 5–7 implementation plan — subscription registry and screens

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Finish the [subscriptions specification](../specs/2026-09-16-phase-4-1-subscriptions.md)
on top of Phase 4.1 Task 4 (opt-out discovery, PR #236): a subscription is one record for its whole
life made of billing periods, so cancelling and resuming never loses the owner's corrections; the
owner sets what a statement cannot show; and a Subscriptions screen shows current and past
subscriptions, their costs, renewals and history, and the cards they depend on.

**Numbering:** Phase 4.1 Tasks 1–3 are spending, budgets and recurring discovery (PRs #232–#234);
Task 4 is opt-out discovery and the Recurring screen, planned as "Task 1" in the
[subscriptions plan](2026-09-16-phase-4-1-subscriptions.md) and delivered via PR #236. This plan
supersedes that plan's Tasks 2 and 3 and continues as Tasks 5, 6 and 7.

**Architecture:** Task 5 adds billing periods, the owner's status and custom cadence, cancel and
resume, automatic lapsing, and a subscription added from one charge (V58). Task 6 adds payment
channels, the paying card, and every figure the screens read — equivalents, summary, upcoming,
detail and by-card (V59). Task 7 adds the Subscriptions and subscription detail screens and the
Transactions action.

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; two forward-only
migrations (V58, V59), no new dependency. This group is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` after the previous one merges, and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings; TypeScript formats only and never computes a monthly,
  annual or total figure. `scripts/check-identifiers.py` refuses `Number`/`parseFloat` on money.
- Every route uses `require_owner` and an explicit `owner.household_id` filter. Every GET opens
  `SET TRANSACTION READ ONLY` and writes nothing. Aggregates read `v_transaction_reportable`.
- `category`, `merchant` and `payment_channel` are shared seed tables with a nullable
  `household_id` and no `UNIQUE (household_id, id)`: reference them with a plain `REFERENCES x(id)`
  and enforce the tenant boundary in the route. Composite tenant-scoped FKs apply to household-only
  targets such as `recurring_series`.
- No code enumerates an institution, store or wallet name (ADR-0012): payment channels are seeded
  rows.
- Task 4's guarantees stay intact: refresh is idempotent and is the only creator; an owner's flag
  (`subscription_set_by_owner`) and name (`named_by_owner`) are never overwritten; a removed series
  is never re-created; a charge belongs to at most one series.
- `recurring_series.ended_on` now means only **removed as not recurring** (final, with its
  dismissal). Cancellation and lapsing are the state of a billing period, never of the series.
- A subscription is **current** when its latest period is open. Totals, renewals and "Current"
  count current subscriptions whose `lifecycle` is `active` or `trial`.
- The original charge amount is `abs(coalesce(original_amount, amount))` with
  `coalesce(original_currency, currency)`, as Task 4 reads it.
- Tests cover major behaviour: money, authorization, household isolation, database writes, and one
  web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money, auth or
  migration findings, `scripts/verify.sh` once, push, PR, merge.

## Branch table

| Task | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 5 | billing periods, status, custom cadence, cancel, resume, lapse, add from one charge | `feature/p4-1-t5-subscription-periods` | 1100 | 900 | Task 4 |
| 6 | payment channels, paying card, equivalents, summary, upcoming, detail, by card | `feature/p4-1-t6-subscription-figures` | 1000 | 850 | 5 |
| 7 | Subscriptions and detail screens, Recurring additions, Transactions action | `feature/p4-1-t7-subscription-screens` | 1200 | 550 | 6 |

## Phase 4.1 Task 5 — billing periods and the owner's lifecycle

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1-t5-subscription-periods` / `develop` / `develop`.
**Depends:** Phase 4.1 Task 4 (PR #236) merged.
**Files:** create `db/migrations/V58__subscription_periods.sql`,
`db/tests/0055_subscription_periods.sql`, `api/tests/test_subscription_periods_live.py`; update
`api/oneledger/recurring.py`, `api/oneledger/routes/recurring.py`, `api/tests/test_recurring.py`,
`api/tests/test_recurring_live.py`, `web/app/recurring/recurring-screen.tsx` and its test; generated
types; `docs/design/api-contracts.html`; `docs/superpowers/schema.md`; progress.
**Contract:**

- **V58 — periods.** `recurring_series_period`: `id UUID PRIMARY KEY`, `household_id UUID NOT NULL`,
  `series_id UUID NOT NULL`, `started_on DATE NOT NULL`, `ended_on DATE`, `end_reason TEXT` checked
  to `cancelled` or `lapsed`, `created_at`.
  - `uq_recurring_series_period_household_id UNIQUE (household_id, id)`; a composite FK
    `(household_id, series_id)` to `recurring_series (household_id, id)` `ON DELETE RESTRICT`.
  - `recurring_series_period_end_shape CHECK ((ended_on IS NULL) = (end_reason IS NULL))` and
    `recurring_series_period_dates_are_ordered CHECK (ended_on IS NULL OR ended_on >= started_on)`.
  - `uq_recurring_series_period_open` — a partial unique index on `(household_id, series_id)` where
    `ended_on IS NULL`: at most one open period per series.
  - RLS `household_isolation` as V53 does; no table GRANT.
- **V58 — series.** `recurring_series` gains `lifecycle TEXT NOT NULL DEFAULT 'active'` checked to
  `active`, `trial` or `paused`, and `custom_interval_days SMALLINT` checked 7–730 with
  `recurring_series_custom_interval_shape CHECK ((cadence = 'custom') = (custom_interval_days IS
  NOT NULL))`. `custom` was added to `recurring_cadence` by V57 and is committed, so V58 may use it.
- **V58 — backfill.**
  - Every series with `ended_on IS NULL` gets one open period starting at `first_seen_on`.
  - Every series ended with a reason other than `not_recurring` (V56's former `/end`) gets one
    period from `first_seen_on` to its `ended_on` with `end_reason = 'cancelled'`, and its
    `ended_on`/`ended_reason` are cleared, with `trg_preserve_ended_recurring_series` disabled for
    that one statement and re-enabled after.
  - A series removed as `not_recurring` gets no period.
- **Cadence arithmetic** (`api/oneledger/recurring.py`). `next_expected_on` and `is_overdue` take
  the interval in days (`CADENCE_DAYS[cadence]`, or `custom_interval_days` for `custom`) instead of
  looking `cadence` up themselves. Every caller passes it.
- **Refresh (`POST /recurring/refresh`)** keeps Task 4's Sync, Extend, Create, Sync order.
  - **Create** also inserts one open period for each new series, starting at its first charge.
  - **Extend.** When a charge joins a series whose latest period is closed and the charge is dated
    after that period's `ended_on`, it opens a new period starting on the charge date. A charge
    dated on or before a closed period's `ended_on` joins without reopening. A `paused` series whose
    latest period is open is extended normally.
  - **Lapse** (in Sync, after the amount sync). A series whose latest period is open and whose
    `lifecycle` is not `paused` has that period closed when today is later than
    `last_seen_on + 2 × interval`: `end_reason = 'lapsed'`, `ended_on = max(started_on,
    last_seen_on + interval)` — the date the last charge paid through. Closing is never destructive;
    the next charge reopens it through Extend.
  - Refresh stays idempotent: a second call creates, adds, opens and closes nothing.
- **`GET /recurring`** rows gain `lifecycle`, `customIntervalDays`, `isCurrent` (the latest period
  is open), `currentPeriodStartedOn`, `lastPeriodEndedOn` and `lastEndReason` (from the latest
  closed period, null when none), `periodCount`, `resumed` (current and `periodCount > 1`), and
  `nextExpectedOn` (null when not current). `status` becomes `active`, `overdue` or `ended` —
  `ended` when not current, `overdue` when current and `is_overdue`.
- **`PATCH /recurring/{seriesId}`** also accepts `lifecycle` (`active`, `trial`, `paused`),
  `cadence` with `customIntervalDays` (both required together for `custom`, 422 otherwise), and
  `categoryId` (a seed or own category through `routes/transactions.py::_assert_category_allowed`;
  another household's 404, unknown or inactive 422). A removed series is 409.
- **`POST /recurring/{seriesId}/cancel`** takes an optional `cancelledOn` (default today, never
  after today). It closes the open period with `end_reason = 'cancelled'` and
  `ended_on = max(cancelledOn, the latest member's observed_on, started_on)`. No open period is 409;
  a removed series 409; another household's 404.
- **`POST /recurring/{seriesId}/resume`** takes an optional `resumedOn` (default today, never after
  today, never before the latest period's `ended_on`). It opens a period on that date. An open
  period is 409; a removed series 409.
- **`POST /recurring/from-transaction`** takes `transactionId`, `cadence` (or `custom` with
  `customIntervalDays`), `label`, optional `isSubscription` (default true) and `idempotencyKey`.
  - The transaction must be this household's reportable spending charge: another household's 404;
    income, a refund or a non-reportable row 422; already a member 409.
  - It writes, in one transaction, a series with `detection_method = 'owner'`, `named_by_owner` and
    `subscription_set_by_owner` true, its native and base amounts from the charge, that one member,
    and an open period starting on the charge date.
  - An identical replay returns 200 with the stored row; a different body for the key 409.
  - Task 4's Extend then follows it: next year's charge joins it as a yearly plan.
- **Recurring screen.** Each row shows its status and, when ended, when and why; it offers
  **Cancel** (asks first), **Resume**, a status choice and a custom cadence, alongside Task 4's
  subscription switch, rename and remove. Every amount is a server string.

**Tests:** db — the period shapes and the open-period uniqueness; the lifecycle and custom-interval
checks; the backfill of an open series, a V56-ended series and a removed series. Pure —
`next_expected_on` and `is_overdue` with a custom interval. Live — cancel closes the period and a
later charge reopens the **same** series with its owner flag and name intact and two periods; a
series that stops charging lapses with its paid-through date and a later charge reopens it; a paused
series never lapses; resume opens a period and refuses while one is open; cancel refuses a date in
the future and never ends before the last charge; a second refresh after a lapse and a reopen
changes nothing; a single charge added as a yearly subscription reports next expected a year on and
is extended by next year's charge; its replay is 200 and a conflicting body 409; another
household's, an income and an already-member transaction are refused; PATCH sets `lifecycle`,
`custom` with its interval and a category, and refuses another household's category; every new
route refuses another household. Web — cancel asks first and posts, resume posts, an ended row shows
its date and reason.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_subscription_periods_live.py tests/test_recurring_live.py
tests/test_recurring.py)` then `uv run ruff check . && uv run ruff format --check . &&
uv run mypy .`; shared types command; `(cd web && npx vitest run && npm run typecheck &&
npm run lint)`; `scripts/verify.sh`.
**Acceptance:** a subscription cancelled and later resumed is one subscription with two periods and
keeps the owner's corrections; one that stops charging ends on its own and returns when charged
again; a yearly plan seen once can be added.

## Phase 4.1 Task 6 — payment channels and subscription figures

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1-t6-subscription-figures` / `develop` / `develop`.
**Depends:** Task 5.
**Files:** create `db/migrations/V59__payment_channels.sql`, `db/tests/0056_payment_channels.sql`,
`api/tests/test_subscription_figures_live.py`; update `api/oneledger/recurring.py`,
`api/oneledger/routes/recurring.py`; generated types; `docs/design/api-contracts.html`;
`docs/superpowers/schema.md`; `docs/superpowers/project-register.md`; progress.
**Contract:**

- **V59.** `payment_channel`: `id`, nullable `household_id`, `name` bounded 1–60 and trimmed,
  `sort_order SMALLINT`, `is_active BOOLEAN NOT NULL DEFAULT true`, timestamps, with RLS policies
  copied exactly from `category`'s. Seed rows (household null) are data: a direct card, an app
  store, a play store, a wallet autopay, a bank autopay, a bank debit, and other.
  `recurring_series` gains `payment_channel_id UUID REFERENCES payment_channel(id)`.
- **Paying account.** A series' paying account is its latest member's transaction `account_id`,
  with the account's name and one masked identifier: from `account_identifier` rows with
  `is_active = true`, `kind` `card` before `bank_account` before `iban`, then `created_at`, then
  `id`. Only `masked_value` is ever selected — never `full_value` or `normalized_value` (ADR-0021).
- **Equivalents.** Annual is `quantize(amount × charges_per_year)`, rounded once at the end, where
  charges per year is 52, 12, 4, 2 or 1, or `365 / custom_interval_days` computed without
  intermediate rounding. Monthly is `quantize(unrounded annual / 12)`. Both use `Decimal`, 4 places,
  half-even. They are given in the original currency from `native_amount` and in the base currency
  from `expected_amount_base` (frozen from the latest charge, ADR-0002), and are labelled estimates.
- **`GET /recurring`** rows gain `paymentChannelId`/`paymentChannelName`,
  `payingAccountId`/`payingAccountName`/`payingAccountMasked`, and
  `annualAmount`/`monthlyAmount`/`annualAmountBase`/`monthlyAmountBase`.
- **`GET /recurring/summary`** over current subscriptions with `lifecycle` `active` or `trial` and
  `is_subscription` true: `monthlyBase` and `annualBase` as the sums of the rows' rounded figures
  (so the rows add up to the total), `currentCount`, `renewingIn30Days` (next expected within 30
  days of today), `priceChangesIn90Days`, `baseCurrency`, and `upcoming` — each such subscription due
  within 30 days with `nextExpectedOn`, label, original amount and currency, and paying account name,
  ordered by date then label.
- **`GET /recurring/{seriesId}`** returns the row plus `firstSeenOn`, `spentSoFarBase` (the sum of
  member `amount_base` across every period), `last12MonthsBase`, `periods` (oldest first: started,
  ended, end reason, charge count, and spent base within the period), `priceHistory` (member original
  amounts by date, one entry per change of amount or currency, across periods) and `charges` (every
  member with date, original amount and currency, base amount, account name and the period it
  belongs to). Another household's 404.
- **`GET /recurring/by-account`** groups current subscriptions (`lifecycle` `active` or `trial`) by
  paying account: account id, name, masked identifier, count, labels, and `monthlyBase`.
- **`PATCH /recurring/{seriesId}`** also accepts `paymentChannelId` (a seed or own active row;
  another household's 404, unknown 422).
- **`GET /payment-channels`** lists seed and own active channels by `sort_order`, then name.
- **Project register.** A catalogue of known services to choose from for a billing descriptor, and
  merging two series.

**Tests:** db — the channel shape, its seed rows, and RLS matching `category`. Live — equivalents
exact for weekly, monthly, quarterly, semiannual, annual and a custom 45-day interval (rounded once)
in original and base currency; summary totals equal the sum of row figures and exclude paused,
ended and not-a-subscription rows; renewing-in-30-days and upcoming boundaries; detail spent so far
across two periods, per-period spend, last 12 months, and a price history with one change; each
charge reports its period; by-account grouping; the masked identifier chosen by kind and an unmasked
value never returned; a channel of another household refused; every new route refuses another
household.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_subscription_figures_live.py
tests/test_subscription_periods_live.py)` then `uv run ruff check . && uv run ruff format --check .
&& uv run mypy .`; shared types command; `(cd web && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** every figure the Subscriptions screen needs is available as exact strings, including
a subscription's full history across its billing periods.

## Phase 4.1 Task 7 — Subscriptions and detail screens

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1-t7-subscription-screens` / `develop` / `develop`.
**Depends:** Task 6.
**Files:** create `web/app/subscriptions/page.tsx`, `web/app/subscriptions/subscriptions-screen.tsx`,
`web/app/subscriptions/[seriesId]/page.tsx`, `web/app/subscriptions/subscription-detail.tsx` and
tests; update `web/app/workspace.tsx`, `web/app/recurring/recurring-screen.tsx`,
`web/app/transactions/transactions-screen.tsx`, `web/app/globals.css`; progress.
**Contract:**

- **Navigation.** A **Subscriptions** section after Spending and before Recurring, added in the five
  places `web/app/workspace.tsx` requires.
- **Detail route.** `web/app/subscriptions/[seriesId]/page.tsx` follows the Next 16 pattern of
  `web/app/investments/[holdingId]/page.tsx`: a client page reading `params` as a Promise with
  `use()` and rendering `<Workspace section="subscriptions-detail" seriesId={…} />`. Workspace gains
  that section and a `seriesId` prop, and `sectionFromPath()` checks the `/subscriptions/` prefix
  before the exact `/subscriptions` match.
- **Subscriptions** runs `POST /recurring/refresh` once on open, then shows:
  - a summary line and cards — monthly equivalent, annual spend, current count, renewing in the next
    30 days, price changes — labelled as estimates from the latest charges;
  - **Upcoming**: date, name, original amount and currency, paying account;
  - **Current**: each current subscription with name (and a **Needs a name** marker where the
    server says so), category, original amount per cadence, paying account with its masked
    identifier, last charged, next expected, annualised amount, status, a **Resumed** marker, and a
    price change note with both amounts; each links to its detail;
  - **Past**: each subscription that is not current, with when it ended and whether it was
    cancelled or stopped charging;
  - **By card**: each paying account with its masked identifier, count, names and monthly figure;
  - per row, **Not a subscription**, rename, status, **Cancel** and **Resume**.
- **Subscription detail** shows the amount per cadence, category, next expected, paying account and
  channel, started, spent so far, last 12 months, a **timeline of billing periods** ("3 Jan –
  3 Mar 2026 · cancelled", "20 Mar 2026 – now · active, resumed") with spend per period, the price
  history, and the charges grouped by period with links to the ledger. It offers rename, status,
  channel, custom cadence, cancel, resume and **Not a subscription**.
- **Recurring** keeps listing recurring payments that are not subscriptions, and gains the channel
  and the paying account where Task 6 provides them.
- **Transactions.** A spending charge that is not already in a series offers **Add as
  subscription**, asking for the cadence (with a custom interval) and a name, and posting
  `POST /recurring/from-transaction` with one idempotency key per draft, reused on retry.
- Every amount is a server string; no monthly, annual or total is computed in the browser.

**Tests:** the summary, upcoming, current and past lists render from server strings; "not a
subscription" patches and the row leaves the list; cancel asks first and moves the row to Past;
resume moves it back; by-card grouping renders masked identifiers; the detail timeline shows two
periods with their spend and the charges grouped by period; the detail route renders for a series
id; adding a charge as a subscription posts the exact body and reuses its key on retry; refresh runs
once on open.
**Commands:** `(cd web && npx vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** after importing statements the owner sees their current subscriptions and past ones,
what they cost, what renews soon and which card each depends on; a subscription's page shows every
period it was active and what it cost in each; a correction and a cancellation stick.
