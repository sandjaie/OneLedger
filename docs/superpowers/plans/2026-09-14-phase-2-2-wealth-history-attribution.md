# Phase 2.2 wealth history and attribution implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Stored month-end positions with evidence dates and rates, and an exact monthly attribution
waterfall with a visible residual.

**Architecture:** A refresh computes month-end positions from existing ledger, holdings and snapshot
reads and stores them; attribution is a pure `Decimal` function over two months of positions and the
window's frozen flows, served on read.

**Stack:** Existing PostgreSQL/Flyway, Python/FastAPI and Next.js/React; no new dependency. See the
[Phase 2.2 specification](../specs/2026-09-14-phase-2-2-wealth-history-attribution.md).

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees. Each
  branch starts from current `develop` and opens one PR to `develop` titled `[P2.2] …`; this plan
  and its specification land with Task 1.
- Money is `NUMERIC`/`Decimal`/strings; tests cover major behaviour (exact values, exact
  reconciliation, household isolation). Routes use `require_owner` and household filters.
- One test server: `eval "$(scripts/test-db.sh up)"`, reused with `eval "$(scripts/test-db.sh env)"`.
  After affected tests: one `/oneledger-code-review` (money branch: confirmation after repairs),
  `scripts/verify.sh` once, push, PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | month-end positions and wealth history | `feature/p2-2a-month-end-positions` | 1,000 | 700 | Phase 2.1 |
| 2 | monthly attribution waterfall | `feature/p2-2b-attribution` | 900 | 800 | 1 |

## Task 1 — month-end positions and wealth history

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p2-2a-month-end-positions` / `develop` / `develop`.
**Depends:** Phase 2.1 merged. **Forecast:** 800–1,100 production, 500–800 tests.
**Files:** create `db/migrations/V46__month_end_positions.sql`,
`db/tests/0043_month_end_positions.sql`, `api/oneledger/wealth_history.py`,
`api/oneledger/routes/wealth_history.py`, `api/tests/test_wealth_history_live.py`,
`web/app/wealth/page.tsx`, `web/app/wealth/wealth-screen.tsx` and its test; update
`api/oneledger/main.py`, `api/oneledger/routes/dashboard.py` (share only
`VALUATION_PERIOD_FOR_ACCOUNT`, no behaviour change), `api/oneledger/investments/positions.py`
(lifetime-predicate keyword for `read_positions`), `web/app/workspace.tsx`; generated types; the
specification's schema, data-model and API documents; progress.
**Contract:** V46 exactly; `wealth_history.refresh(cur, household_id, base, secondary, through)`
computes every month end from `HISTORY_ANCHOR` per the Month-end positions table, reusing
`read_positions` and `current_rate_pair`, and replaces rows in one transaction under the household
lock; `POST /wealth-history/refresh`, `GET /wealth-history`, `GET /wealth-history/{monthEnd}`; the
Wealth screen lists months and positions with the Refresh action.
**Tests:** live ledger balance at month ends equals `assert_account_reconciles`' figure including
rows on the opening date; closed account keeps history; fully sold holding is zero; holding valued
with month-end price and rate; snapshot carry-forward; before-opening (incomplete month),
missing-price and missing-rate statuses; idempotent refresh; late failure keeps previous rows; member
refusal and foreign isolation; GET never recomputes; SQL RLS.
**Commands:** `scripts/hooks/db-tests.sh`; `(cd api && uv run pytest -q
tests/test_wealth_history_live.py tests/test_dashboard_live.py tests/test_health.py)`; shared types
command; `(cd web && npx vitest run app/wealth && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** month-end net worth from the anchor is stored with evidence dates and rates in both
currencies.

## Task 2 — monthly attribution waterfall

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p2-2b-attribution` / `develop` / `develop`.
**Depends:** Task 1. **Forecast:** 700–1,000 production, 600–900 tests.
**Files:** create `api/oneledger/attribution.py`, `api/tests/test_attribution.py`,
`api/tests/test_attribution_live.py`; update `api/oneledger/routes/wealth_history.py`,
`scripts/check-identifiers.py` (`RATE_MULTIPLIERS` entry),
`web/app/wealth/wealth-screen.tsx` and its test; generated types; API contracts,
multi-currency-and-fx link, testing document and progress.
**Contract:** `attribution.attribute(opening, closing, flows, holding_funding, currency) ->
Attribution` is pure and implements the specification's lines exactly; the route loads the two
months' stored positions, the window's frozen postings on counted ledger-mode accounts and active
investment events, and serves `GET /wealth-history/{monthEnd}/attribution` per currency; the Wealth
screen shows the waterfall with the residual always visible.
**Tests:** the specification's dated cash-flow fixtures reconcile with residual exactly zero
(rounding aside), including a new cross-currency holding, a full sale, a liability, a base-currency
account and a mid-window account opening; unavailable reasons; live seeded month equals the unit
result in both currencies.
**Commands:** `(cd api && uv run pytest -q tests/test_attribution.py tests/test_attribution_live.py
tests/test_wealth_history_live.py)`; shared types command; `(cd web && npx vitest run app/wealth &&
npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** each complete month's waterfall reconciles to its actual change with the residual
shown, in both reporting currencies.
