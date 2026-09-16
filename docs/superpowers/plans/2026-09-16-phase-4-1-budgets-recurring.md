# Phase 4.1 implementation plan — budgets, spending analysis and recurring discovery

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Deliver the [Phase 4.1 specification](../specs/2026-09-16-phase-4-1-budgets-recurring.md):
spending readable by category and bucket, budgets with rollover and variance, and recurring payments
discovered as proposals the owner confirms.

**Architecture:** Task 1 adds read-only spending aggregation over `v_transaction_reportable` and a
new Spending screen with a server-scaled chart, drill-through and CSV export. Task 2 adds budget
lines (one migration) and budget-versus-actual on the same screen. Task 3 adds recurring discovery,
confirmation and price-change and inactive handling (one migration).

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; two forward-only
migrations (V55, V56), no new dependency. This group is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` after the previous one merges, and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings. TypeScript formats and compares dates only; `Number` and
  `parseFloat` on money or ratios are refused by `scripts/check-identifiers.py`.
- Every new route uses `require_owner` and an explicit `owner.household_id` filter (the API connects
  as the table owner). Aggregates read `v_transaction_reportable`, never `transaction`.
- Spending is `-sum(amount_base)` over reportable rows where `txn_type <> 'income'`, matching
  `routes/dashboard.py`. Income is `txn_type = 'income'`.
- New tables follow V53's shape: composite `(household_id, id)` uniqueness, tenant-scoped composite
  FKs with `ON DELETE RESTRICT`, bounded text and amount CHECKs, RLS `FOR ALL TO authenticated`, no
  table GRANT, and a `db/tests/00NN_*.sql` companion numbered migration minus three.
- Tests cover major behaviour: money, authorization, household isolation, database writes, and one
  web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money, auth or
  migration findings, `scripts/verify.sh` once, push, PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | spending by category and bucket, chart, drill-through, CSV | `feature/p4-1-spending-analysis` | 650 | 450 | Phase 3.4 |
| 2 | budget lines, rollover, variance | `feature/p4-1-budgets` | 750 | 550 | 1 |
| 3 | recurring discovery, price change, inactive candidates | `feature/p4-1-recurring-discovery` | 850 | 600 | 2 |

## Task 1 — spending by category and bucket

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1-spending-analysis` / `develop` / `develop`.
**Depends:** Phase 3.4 and the Phase 3 hygiene pass merged.
**Files:** create `api/oneledger/routes/spending.py`, `api/oneledger/categories_sql.py`,
`api/tests/test_spending_live.py`, `web/app/spending/page.tsx`,
`web/app/spending/spending-screen.tsx` and `web/app/spending/__tests__/spending-screen.test.tsx`;
update `api/oneledger/main.py`, `web/app/workspace.tsx`, `web/app/globals.css`,
`web/lib/api/client.ts`, `web/app/transactions/transactions-screen.tsx` (prefilled filters);
generated types; `docs/design/api-contracts.html`; `docs/superpowers/project-register.md`; progress.
**Contract:**

- `api/oneledger/categories_sql.py` holds one exported fragment, `EFFECTIVE_CATEGORY`, giving a
  category's id, name, parent id, effective bucket (`COALESCE(o.bucket, c.bucket)`) and effective
  active flag, parameterised by household. New reads use it; the three existing copies stay as they
  are and get a project-register row.
- `GET /spending?from=&to=&currency=` returns, for the closed date range (`from` and `to` inclusive,
  `to` not in the future, range at most 400 days, `from <= to`, else 422):
  - `byCategory`: one row per category with spending in range — `categoryId`, `categoryName`,
    `parentCategoryId`, `parentCategoryName`, `bucket`, `amountBase`, `amountSecondary`,
    `transactionCount`, `previousAmountBase` (the immediately preceding range of equal length, for
    change), and `chartRatio`;
  - one `categoryId: null` row named "Uncategorised" when uncategorised spending exists, with
    `bucket: null`;
  - `byBucket`: one row per effective bucket present, with the same amount fields and `chartRatio`;
  - `totalBase`, `totalSecondary`, `previousTotalBase`, `from`, `to`, `baseCurrency`,
    `secondaryCurrency`.
  - `chartRatio` is that row's amount divided by the largest absolute amount in its own list, as a
    `Decimal` string quantized to 4 places half-even, `"0.0000"` when that largest is zero, exactly
    as `wealth_history._chart_ratios` does; reuse that helper by importing it or move it to
    `api/oneledger/money.py` and have both call it.
  - Rows are ordered by `amountBase` descending, then category name.
- `GET /spending/export.csv?from=&to=` streams `text/csv` with
  `Content-Disposition: attachment; filename="spending-<from>-<to>.csv"`, header row
  `booked_date,account,description,category,bucket,amount,currency,amount_base`, one line per
  reportable spending row in range, ordered by date then id, capped at 10,000 rows; when the cap is
  reached the last line is a `# truncated at 10000 rows` comment. `description` is
  `description_clean` falling back to `description_raw`; values are RFC 4180 quoted. Household
  filtered; the same range validation as above.
- `web/lib/api/client.ts` gains `apiDownload(path, filename)`: same credentials, base URL and
  401/403 handling as `apiFetch`, returning the body as a `Blob` and saving it through an object URL.
  No money parsing.
- New **Spending** section: nav item after Transactions at `/spending`, the section union, the
  `sectionFromPath()` line, the render guard and `web/app/spending/page.tsx`. Workspace fetches
  `/categories` for it and passes `categories`, `currencies`, `currency` and `request`.
- The screen shows a month picker defaulting to the current month (with a custom range option), the
  bucket summary, the category table with amounts as server strings and a bar chart built from
  `chartRatio` using the `.wealth-chart-*` pattern with a spending variant whose bars rise from a
  zero baseline (`bottom: 0; height: 100%`), the change against the previous period as the server's
  own strings, a **Download CSV** button, and a per-row link to
  `/transactions?categoryId=<id>&dateFrom=<from>&dateTo=<to>`.
- `TransactionsScreen` accepts prefilled `categoryId`, `dateFrom` and `dateTo` the way it already
  accepts `initialSourceDocumentId`; Workspace reads them from `window.location.search` as the
  Accounts valuation deep link does, and ignores anything that is not a known category or a date.

**Tests:** live — totals by category and bucket over synthetic rows; a refund lowering spending; an
uncategorised row reported separately; an overridden bucket resolving to the override; transfers and
investment legs absent; previous-period amounts; chart ratios exact; range refusals; foreign
household empty; member refusal; CSV header, one exact row, quoting of a description containing a
comma, and the row cap. Web — breakdown and chart render from server strings, drill-through link
carries the category and dates, download calls the export path.
**Commands:** `eval "$(scripts/test-db.sh up)"`;
`(cd api && uv run pytest -q tests/test_spending_live.py tests/test_dashboard_live.py)` plus
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`;
shared types command; `(cd web && npx vitest run && npm run typecheck && npm run lint)`;
`scripts/verify.sh`.
**Acceptance:** this month's spending is readable by category and bucket, charted, drillable and
exportable from the application's navigation.

## Task 2 — budgets, rollover and variance

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p4-1-budgets` /
`develop` / `develop`.
**Depends:** Task 1.
**Files:** create `db/migrations/V55__budgets.sql`, `db/tests/0052_budgets.sql`,
`api/oneledger/routes/budgets.py`, `api/tests/test_budgets_live.py`,
`web/app/spending/budget-panel.tsx` and its test; update `api/oneledger/main.py`,
`web/app/spending/spending-screen.tsx`, `web/app/globals.css`; generated types; API contracts;
`docs/superpowers/schema.md`; progress.
**Contract:**

- V55 adds `budget_line`: `id`, `household_id`, `idempotency_key`, `period_month DATE` (the first of
  the month, enforced), exactly one of `category_id` or `bucket` (`budget_line_scope_shape`),
  `amount_base NUMERIC(20,4)` positive, `currency CHAR(3)` referencing `currency(code)`,
  `rollover_enabled BOOLEAN NOT NULL DEFAULT false`, `note` bounded to 200 characters, timestamps,
  `uq_budget_line_household_id UNIQUE (household_id, id)`,
  `uq_budget_line_idempotency UNIQUE (household_id, idempotency_key)`, a tenant-scoped composite FK
  to `category`, and two partial unique indexes so one household has at most one line per month per
  category and per bucket. RLS `household_isolation` as V53 does. No trigger is needed: a budget
  line is editable and deletable.
- `GET /budgets?month=YYYY-MM-01` returns each line with its scope, `amountBase`, `rolloverEnabled`,
  `note`, and the computed `actualBase`, `carriedBase`, `availableBase` (`amountBase + carriedBase`)
  and `varianceBase` (`availableBase - actualBase`), plus `totals` over all lines and the month's
  `from`/`to`.
  - A category line's actual is that category's spending in the month.
  - A bucket line's actual is that bucket's spending in the month **excluding** categories that have
    their own line in the same month; the row carries `excludedCategoryIds` so the screen can say so.
  - `carriedBase` is 0 when `rolloverEnabled` is false. Otherwise it is the previous month's
    `availableBase - actualBase` for the same scope, recursing while the previous month's line also
    has rollover enabled, at most 12 months back; the row reports `carryMonths` and
    `carryStoppedReason` (`"no_prior_line"`, `"rollover_off"` or `"bound_reached"`).
  - All amounts are `Decimal` on the server and strings on the wire.
- `POST /budgets` creates a line (`idempotencyKey`, `periodMonth`, one of `categoryId` or `bucket`,
  `amountBase` matching the money pattern, `rolloverEnabled`, optional `note`); an identical replay
  returns 200 with the stored row, a different body for the same key returns 409, a duplicate scope
  in the month returns 409, an unknown or inactive category returns 422, a foreign category 404.
- `PUT /budgets/{lineId}` updates amount, rollover and note only — never the scope or month;
  `DELETE /budgets/{lineId}` removes the line (204). Both are household filtered, 404 when foreign.
- `POST /budgets/copy` takes `fromMonth` and `toMonth` and one idempotency key, copying every line
  whose scope has no line in `toMonth`, returning the created rows and a count of skipped scopes.
- The Spending screen gains a **Budget** panel for the selected month: set or edit a line per
  category or bucket, the rollover toggle with a sentence naming the carried figure and where the
  chain stopped, variance shown with its sign, and **Copy last month**. The panel states that a
  bucket line's actual excludes separately budgeted categories, and that budgets post nothing.

**Tests:** db — the scope shape, both partial unique indexes, and the period-month constraint.
Live — variance without rollover; carry across two months; carry stopping at a month with
rollover off and at the 12-month bound; a bucket line excluding a category-lined child; replay 200
and conflict 409; duplicate scope 409; inactive and foreign category refusals; update refusing a
scope change; copy skipping existing scopes; member and foreign-household refusals. Web — set a
line and see variance, toggle rollover, copy last month.
**Commands:** `eval "$(scripts/test-db.sh up)"`; the db test as `scripts/verify.sh` runs it;
`(cd api && uv run pytest -q tests/test_budgets_live.py tests/test_spending_live.py)` plus
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`;
shared types command; `(cd web && npx vitest run && npm run typecheck && npm run lint)`;
`scripts/verify.sh`.
**Acceptance:** a budget set for a category and a bucket shows its variance for the month, and an
unspent remainder carries when the owner asks for it.

## Task 3 — recurring discovery, price changes and inactive candidates

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-1-recurring-discovery` / `develop` / `develop`.
**Depends:** Task 2.
**Files:** create `db/migrations/V56__recurring_series.sql`, `db/tests/0053_recurring_series.sql`,
`api/oneledger/recurring.py`, `api/oneledger/routes/recurring.py`,
`api/tests/test_recurring_live.py`, `web/app/spending/recurring-panel.tsx` and its test; update
`api/oneledger/main.py`, `web/app/spending/spending-screen.tsx`, `web/app/globals.css`; generated
types; API contracts; `docs/superpowers/schema.md`; progress.
**Contract:**

- V56 adds:
  - `recurring_series`: `id`, `household_id`, `idempotency_key`, `label` (bounded 1–120, trimmed),
    `merchant_id` nullable with a tenant-scoped FK, `match_key TEXT` (the normalized description used
    when there is no merchant), `cadence` (`weekly`, `monthly`, `quarterly`, `annual`),
    `expected_amount_base NUMERIC(20,4)` positive, `currency`, `category_id` nullable tenant-scoped
    FK, `first_seen_on`, `last_seen_on`, `ended_on` nullable, `ended_reason` bounded, timestamps,
    `uq_recurring_series_household_id`, `uq_recurring_series_idempotency`, a
    `recurring_series_identity_shape` CHECK requiring exactly one of `merchant_id` or `match_key`,
    and one partial unique index so a household has at most one open series per identity.
  - `recurring_series_member`: `household_id`, `series_id`, `transaction_id`, `observed_on`,
    `amount_base`, primary key `(household_id, series_id, transaction_id)`, tenant-scoped composite
    FKs to `recurring_series` and `transaction`, `ON DELETE RESTRICT`. Ledger rows are never
    rewritten and `transaction.recurring_series_id` stays unused.
  - `recurring_dismissal`: `household_id`, identity columns as above, `dismissed_at`, with a unique
    index on the identity, so a dismissed candidate does not return.
  - RLS `household_isolation` on all three, and `preserve_ended_recurring_series()` refusing any
    update to a series that already has `ended_on`, raising with `ERRCODE = '23514'` and the
    constraint name so the route maps it to 422.
- `api/oneledger/recurring.py` holds the detection, pure over rows the route reads:
  - group reportable spending rows (`txn_type <> 'income'`) in the last 18 months by identity —
    `merchant_id` when present, otherwise `lower(btrim(description_clean))` collapsed on whitespace;
  - a group is a candidate when it has at least 3 occurrences and the median gap fits one cadence
    within tolerance (weekly 5–9 days, monthly 26–35, quarterly 82–98, annual 350–380) with at least
    two thirds of gaps inside that window;
  - the candidate reports `identity`, `label` (merchant display name or the raw description of the
    latest row), `cadence`, `occurrences`, `firstSeenOn`, `lastSeenOn`, `lastAmountBase`,
    `typicalAmountBase` (the median, as a `Decimal` string), `categoryId` and `transactionIds`;
  - groups that fit no cadence are not returned; nothing is guessed from a single occurrence.
- `GET /recurring` returns `confirmed` (open series with `status` — `active`, `price_changed` or
  `overdue` — plus `expectedAmountBase`, `lastAmountBase`, `lastSeenOn`, `nextExpectedOn` and
  `memberCount`) and `candidates` (detected groups that are neither confirmed nor dismissed).
  - `price_changed` when the latest member's `amount_base` differs from `expected_amount_base`;
    both exact amounts and their dates are in the row.
  - `overdue` when `nextExpectedOn` (last seen plus the cadence) is more than half a cadence in the
    past.
- `POST /recurring/confirm` takes a candidate identity, `label`, `cadence`, `expectedAmountBase`,
  `categoryId` and `idempotencyKey`, writing the series and its members in one transaction; an
  identical replay returns 200 with the stored row, a different body 409, an already-confirmed
  identity 409.
- `POST /recurring/dismiss` records a dismissal for an identity (idempotent, 200 on replay).
- `POST /recurring/{seriesId}/accept-price` sets `expected_amount_base` to the latest member
  amount, taking the `expectedAmountBase` the owner saw so a changed observation refuses with 409.
- `POST /recurring/{seriesId}/end` sets `ended_on` and a bounded `ended_reason`; an already ended
  series returns 409 through the trigger's 422 mapping, whichever the route reaches first.
- Members are refreshed when a series is read: a reportable row matching the identity, dated after
  `last_seen_on` and before any `ended_on`, is inserted as a member. This is the only write a read
  performs, it is idempotent, and it is stated in the route docstring.
- The Spending screen gains a **Recurring** panel: confirmed series with status and next expected
  date, a price-change row offering **Accept new amount** with both figures, an overdue row offering
  **End series**, and candidates with **Confirm** (label, cadence, expected amount and category
  prefilled from the proposal) and **Dismiss**. Every amount is a server string.

**Tests:** db — identity shape, the open-series partial unique index, the ended-series refusal. Live
— a regular monthly group proposed with its cadence and median amount; an irregular group not
proposed; a two-occurrence group not proposed; confirmation writing the series and members; replay
200 and conflict 409; dismissal suppressing a candidate; a later matching row joining the series on
read; a price change reported and accepted, with a stale expected amount refused; an overdue series
reported and ended; member and foreign-household refusals. Web — confirm, dismiss, accept price, end.
**Commands:** `eval "$(scripts/test-db.sh up)"`; the db test as `scripts/verify.sh` runs it;
`(cd api && uv run pytest -q tests/test_recurring_live.py tests/test_ingest_classify.py)` plus
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`;
shared types command; `(cd web && npx vitest run && npm run typecheck && npm run lint)`;
`scripts/verify.sh`.
**Acceptance:** the repeated payments the owner recognizes appear as candidates; one can be
confirmed, one dismissed, a changed price accepted, and a stopped series ended.
