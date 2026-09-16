# Phase 4.3 implementation plan — investment analysis

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Deliver the
[Phase 4.3 specification](../specs/2026-09-16-phase-4-3-investment-analysis.md): class and account
XIRR, Modified Dietz period returns, contribution effects, allocation drift and optional
benchmarks, reconciled to holdings events, accepted dated prices and stored month-end positions.

**Architecture:** Task 1 adds allocation-target and benchmark tables (V61), a pure analysis module
and the read-only analysis plus correction routes. Task 2 adds the analysis to the Investments
screen.

**Stack:** Existing Python/FastAPI, PostgreSQL/Flyway and Next.js/React; one forward-only
migration (V61), no new dependency. This phase is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` after the previous one merges and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings. TypeScript formats only; `scripts/check-identifiers.py`
  refuses `Number`/`parseFloat` on money and any direct read of the `transaction` table.
- Every route uses `require_owner` and an explicit `owner.household_id` filter. Every GET opens
  `SET TRANSACTION READ ONLY` and writes nothing. Every write takes the household lock first.
- Holdings XIRR, Dietz and contribution at as-of use the population `_HOUSEHOLD_HOLDINGS_SQL` in
  `returns.py` already uses (active accounts in holdings mode). Month windows use every holding
  that has a holdings-mode `month_end_position` row at `t0` or `t1`, and only those holdings'
  events. Snapshot and ledger-balance counted accounts are coverage only.
- XIRR reuse: `classify_event_flows`, `xirr`, `group_allocation` in
  `api/oneledger/investments/returns.py`. Do not copy the solver. Class and account series follow
  `portfolio_flows` except: a switch, systematic-transfer or platform-transfer leg whose
  counterpart holding is outside the group is `incomplete_history`. A missing price still fails
  the group. `no_denominator` and `wealth_history_not_refreshed` are analysis reasons, not
  `XirrReason`.
- Month rows read stored `month_end_position` from the last wealth refresh; GET never refreshes.
- No currency, institution or country name is an identifier (ADR-0012). Asset-class labels are the
  enum values already stored on `security.asset_class`.
- Tests cover major behaviour: money, authorization, household isolation, database writes, and one
  web test per new flow.
- After affected tests: one `/oneledger-code-review`, one repair, confirmation for money, auth or
  migration findings, `scripts/verify.sh` once, push, PR, merge.

## Branch table

| Task | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | analysis API, targets, benchmarks | `feature/p4-3-t1-investment-analysis-api` | 1100 | 1100 | 4.2 |
| 2 | analysis on the Investments screen | `feature/p4-3-t2-investment-analysis-screen` | 700 | 350 | 1 |

## Files

Task 1 creates `db/migrations/V61__investment_analysis_rules.sql`,
`db/tests/0058_investment_analysis_rules.sql`, `api/oneledger/investment_analysis.py`,
`api/tests/test_investment_analysis.py`, `api/tests/test_investment_analysis_live.py`; updates
`api/oneledger/routes/portfolio.py`, `api/oneledger/main.py` if needed, generated types,
`docs/design/api-contracts.html`, `docs/superpowers/schema.md`,
`docs/superpowers/project-register.md`. Task 2 creates
`web/app/investments/investment-analysis.tsx` and its test; updates
`web/app/investments/portfolio-screen.tsx` and `web/app/globals.css` if a scroll or table rule is
required.

## Phase 4.3 Task 1 — investment analysis API

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-3-t1-investment-analysis-api` / `develop` / `develop`. **Depends:** Phase 4.2 Task 2
merged. **Contract:**

- **V61.** Two household-scoped tables, RLS like V60 `payslip_earning_rule`, written by the route
  under the household lock. The PUT that replaces targets runs in one transaction: a late failure
  leaves the previous set.
  - `investment_allocation_target`: `household_id UUID NOT NULL REFERENCES household(id)`, `label
    asset_class NOT NULL`, `ratio NUMERIC(20,10) NOT NULL` checked `>= 0 AND <= 1`, `created_at`,
    `updated_at`, `PRIMARY KEY (household_id, label)`. Application refuses a PUT whose ratios do
    not sum to exactly 1 at that scale, and refuses a ratio with more than ten decimal places
    rather than rounding.
  - `investment_benchmark`: `household_id UUID NOT NULL REFERENCES household(id)`, `scope_kind TEXT
    NOT NULL` checked to `portfolio` or `asset_class`, `asset_class asset_class` NULL when kind is
    `portfolio` and NOT NULL matching an enum value when kind is `asset_class`, `security_id UUID
    NOT NULL REFERENCES security(id)`, `created_at`, `updated_at`, `UNIQUE NULLS NOT DISTINCT
    (household_id, scope_kind, asset_class)`. CHECK: portfolio rows have `asset_class IS NULL`;
    class rows have `asset_class IS NOT NULL`.
  - Db test: checks, keys, RLS (including member), and that a foreign household row is invisible.
- **Pure `api/oneledger/investment_analysis.py`** (no database). Inputs: as-of date; holdings with
  account id, asset class, events `(kind, date, net_amount_base)`, units, latest accepted price and
  as-of base rate; month-end holdings-mode rows `(monthEnd, holdingId, assetClass, accountId,
  valueBase, status)`; stored targets; stored benchmarks with each security's accepted prices and
  rates at month ends; coverage accounts (snapshot/ledger) as names only. Output: the GET body.
  Rules:
  - **XIRR groups:** for each asset class and each account that has at least one as-of holdings-mode
    holding, build flows like `portfolio_flows`. Resolve each switch/systematic-transfer/platform-
    transfer counterpart through `counterpart_transaction_id`; outside the group →
    `incomplete_history`. Call `returns.xirr`.
  - **Dietz** (spec formula): `C_i = −amount` of classified flows in `(t0, t1]`; `w_i` in whole
    days; percent at 8 dp half-even; `no_denominator` when `BMV + W = 0`. Inception: `t0` = day
    before the earliest active event, `BMV = 0`, `EMV` from `read_positions` at as-of.
  - **Contribution:** month-window population is holdings with a holdings-mode month-end row at
    `t0` or `t1`; flows from exactly those holdings. `marketAndFx` is the plug. Prove class lines
    sum to the portfolio line; do not assert the plug identity.
  - **Months:** one row per stored month end that `GET /wealth-history` lists (not 31 December
    2023). Incomplete if any holdings-mode row that month or at the previous month end is not
    `available`. Incomplete rows carry status counts and null Dietz, contribution, ratios and
    drift.
  - **Years:** newest first. Sum contribution lines and report Dietz only when every month of that
    year present in the series is complete; otherwise `incompleteMonths` and null sums.
  - **Target:** stored rows if any; else inferred `group_allocation` ratios from the earliest
    complete month with remainder assigned to the largest class so the set sums to 1; else none.
    Drift per spec.
  - **Benchmark Dietz:** one-unit, `C = 0`, prices latest accepted on or before each endpoint,
    translated at that date's base rate. Excess = subject − benchmark, 8 dp. Computed for the
    portfolio and for each class that has an assignment.
- **`GET /portfolio/analysis?asOf=`** (default today). Read-only. 400 on a bad date. Response
  shape (all money, ratios, percents as strings):

```text
{
  asOf, wealthHistoryComputedAt | null, wealthHistoryReason | null,
  coverage: [{ accountId, accountName, valuationMode }],
  classes: [{ label, xirrPercent, xirrReason, valueBase, inception: EffectWindow }],
  accounts: [{ accountId, accountName, xirrPercent, xirrReason, valueBase }],
  inception: EffectWindow,
  years: [{ year, incompleteMonths, portfolio: EffectWindow,
    classes: [{ label, ...EffectWindow }] }],
  months: [{ monthEnd, complete, statusCounts, portfolio: EffectWindow,
    classes: [{ label, valueBase, ratio, targetRatio, drift, ...EffectWindow }] }],
  target: { source: "rule" | "inferred" | "none", groups: [{ label, ratio }] },
  benchmarks: [{ scopeKind, assetClass | null, securityId, securityName }]
}
EffectWindow = { dietzPercent, dietzReason, contributions, withdrawals, income, charges,
  marketAndFx, opening, closing, benchmarkDietzPercent, benchmarkDietzReason, excessPercent }
```

  Months newest first. `statusCounts` uses the existing month-end status keys. Unrefreshed wealth
  history: `wealthHistoryReason = "wealth_history_not_refreshed"`, empty `months`/`years`, live
  `classes`/`accounts`/`inception` still populated when prices exist. Incomplete `EffectWindow`
  fields are null.
- **`GET /portfolio/allocation-targets`**. **`PUT /portfolio/allocation-targets`** body
  `{ groups: [{ label, ratio }] }` replaces the household's rows in one transaction under the
  household lock: 422 if a label is not an `asset_class` value, if a ratio is out of range or has
  more than ten decimal places, or if the ratios do not sum to 1 at `NUMERIC(20,10)`. Replay of the
  same set is 200. Member 403. **`DELETE /portfolio/allocation-targets`** removes every row (204
  even when none existed). Member 403.
- **`GET /portfolio/benchmarks`**. **`PUT /portfolio/benchmarks/{scopeKind}`** with optional
  `?assetClass=` (required when kind is `asset_class`, refused when kind is `portfolio`) body
  `{ securityId }`: 422 if the security is not household or shared, 404 for another household's id
  indistinguishable from missing. **`DELETE`** same path, 404 when it removes no row. Household
  lock on writes. Member 403.
- **Project register:** close the "Account- and asset-class XIRR" half of that item; keep
  "platform and currency allocation groupings" open. Leave realised-gain, event document links and
  same-date snapshot correction open. Note that complete investment history remains an owner
  prerequisite.

**Tests:** db — V61 shape, checks, keys, RLS including member. Pure — Dietz with a mid-window
purchase of known days; `no_denominator`; class contribution lines sum to the portfolio line; class
XIRR fixture matching `xirr()`; in-class switch stays cash-neutral; cross-class switch and
cross-account platform transfer are `incomplete_history`; inferred target sums to 1; drift with a
class only on one side; incomplete month excluded from a year; class and portfolio benchmark
excess; missing benchmark price; inception `BMV = 0` independent of wealth history. Live — missing
price fails class XIRR; GET writes nothing; wealth unrefreshed reason; target PUT sum-to-1 422,
over-precise ratio 422, replay 200, and a mid-replace failure leaves the previous set; DELETE
restores inference on the next GET; member 403 on each write; benchmark 404/422; another
household's rows invisible.
**Commands:** `eval "$(scripts/test-db.sh up)"`; db tests as `scripts/verify.sh` runs them;
`(cd api && uv run pytest -q tests/test_investment_analysis.py \
tests/test_investment_analysis_live.py tests/test_investment_returns.py)` then
`uv run ruff check . && uv run ruff format --check . && uv run mypy .`; shared types command;
`(cd web && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** every analysis figure the screen needs is an exact string that reconciles to
events, accepted prices and stored month ends, with every incomplete month listed.

## Phase 4.3 Task 2 — investment analysis on the Investments screen

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p4-3-t2-investment-analysis-screen` / `develop` / `develop`. **Depends:** Task 1.
**Contract:**

- The Investments (portfolio) screen shows **Analysis** above the existing summary, loaded from
  `GET /portfolio/analysis`, and reloads it after a price refresh, a recorded event, or a target or
  benchmark change made on this screen.
- **Classes and accounts:** XIRR or the server reason, plus current value. Each class also shows
  its inception Dietz, contribution lines and benchmark excess when present.
- **Inception and years:** portfolio Dietz, contribution lines, benchmark excess; each year's class
  breakdown; a year with `incompleteMonths` says so and shows no sums.
- **Months:** complete rows with portfolio and per-class Dietz, contribution, allocation, target
  and drift; incomplete rows read like salary gap rows ("Incomplete month") and list status counts.
  Wide tables scroll on phones.
- **Target:** show source Inferred or the owner's set; a save PUTs the full group list; **Use
  inference** DELETEs, then reloads.
- **Benchmarks:** current assignments; a security picker from household securities (existing
  catalogue/positions list, no new fetch shape if `/portfolio/positions` already names them); save
  is PUT on `/portfolio/benchmarks/{scopeKind}` with `?assetClass=` when scoped to a class; remove
  is DELETE, then reload.
- **Coverage:** snapshot and ledger-balance accounts named as excluded.
- Loading, empty (no holdings), error and retry. Every amount, ratio and percent is a server
  string; the browser computes nothing.

**Tests:** class XIRR, an incomplete month row, contribution lines, drift, excess, a target PUT
and reload, a benchmark DELETE and reload, all from server strings. **Commands:** `(cd web && npx
vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`. **Acceptance:** the owner
reads class returns, a month's contribution split, drift against a target they can correct, and a
benchmark comparison on one screen.
