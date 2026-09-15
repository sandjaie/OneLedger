# Phase 3.2 cash forecast and monthly review implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Per-account and per-currency cash projections with lowest balances, confirmed-only
figures, card settlement counted once, shortfalls with funding suggestions and visible coverage
gaps; a monthly review the owner completes.

**Architecture:** `GET /forecast` keeps its route and read-only transaction and gains the
specification's fields from pure `forecast.py` functions; V50 adds `monthly_review`; the review
route composes existing coverage, transfer, wealth-history and action computations through extracted
helpers. See the [Phase 3.2 specification](../specs/2026-09-15-phase-3-2-cash-forecast-review.md).

**Stack:** Existing Flyway SQL, Python/FastAPI/psycopg and Next.js/React; no new dependency.

## Global execution contract

As the [Phase 3.1 plan](2026-09-15-phase-3-1-commitments-actions.md#global-execution-contract), with
PR titles `[P3.2] …`. Planned and reviewed once with Phase 3.1.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | cash forecast | `feature/p3-2-cash-forecast` | 800–1,000 | 700–900 | Phase 3.1 Task 2 |
| 2 | monthly review | `feature/p3-2-monthly-review` | 650–850 | 550–750 | Task 1 |

## Task 1 — cash forecast

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p3-2-cash-forecast` / `develop` / `develop`.
**Depends:** Phase 3.1 Task 2 merged.
**Owner sees and can do:** on Plans › Forecast, a currency summary and per-account lowest balances
and dates with confirmed-only figures; a shortfall callout naming accounts that could fund it; card
payments reducing the card on the same date; stale, coverage-gap, not-projected and
unplanned-card-due notices with **Settle** and **Plan payment**; an assumptions list stating what is
and is not projected.
**Files:** create `api/tests/test_cash_forecast_live.py`, `web/app/plans/cash-forecast.tsx`,
`web/app/plans/__tests__/cash-forecast.test.tsx`; update `api/oneledger/forecast.py`,
`api/oneledger/routes/plans.py`, `api/oneledger/routes/actions.py` (expose `action_list(...,
apply_marks=False)` only), `api/tests/test_forecast.py`, `api/tests/test_plans_live.py` where the
response shape grew, `web/app/plans/plans-screen.tsx`,
`web/app/plans/__tests__/plans-screen.test.tsx`, generated `web/types/api/`; the specification's
forecast documents.
**Contract:**

- `forecast.py` adds pure functions for rules 3–5 and 7 over `(date, Decimal, certainty)`
  occurrences — lowest and confirmed-lowest (earliest date on ties, start balance included),
  per-currency aggregation with its `asOf`-dated start, shortfall and funding selection — with no
  database access.
- `routes/plans.py` `GET /forecast` implements rules 1–9: mirrors only for card-sourced commitments
  to an included same-currency card account whose `balanceDate` is before the occurrence date;
  unapplied occurrences capped at 12 per commitment; `coverageGapMonths` from `coverage_rows()`;
  `unplannedCardDues` from `action_list(..., apply_marks=False)`; still `SET TRANSACTION READ ONLY`
  first.
- Web: the specification's Plans › Forecast workflow, moved out of `plans-screen.tsx` into
  `cash-forecast.tsx`; server strings only.

**Tests:**

- Unit `test_forecast.py`: lowest/confirmed-lowest ties and start-lowest, per-currency sums and
  pre-`asOf` occurrences, shortfall and funding selection, exact four-place results.
- Live `test_cash_forecast_live.py`: P05 as the specification states, including no mirror when the
  card's balance date is on or after the payment date; P06 exactly; card account excluded or in
  another currency → `card_settlement_not_projected` with the paying side applied; archived
  commitment and dismissed occurrence contribute nothing; unapplied occurrences (cap),
  `balanceAgeDays`, `coverageGapMonths`, currency members, `excludedAccountIds`, `staleAccountIds`;
  unplanned card dues including snoozed and dismissed ones; no row changed.
- Web: currency summary, shortfall and funding render from server strings; Settle and Plan payment
  open the Phase 3.1 flows.

**Commands:** shared DB up; `(cd api && uv run pytest -q tests/test_forecast.py
tests/test_plans_live.py tests/test_cash_forecast_live.py tests/test_actions_live.py)`; types; web
`app/plans`; `scripts/check-diff-size.sh`.
**Acceptance:** the specification's P05/P06, lowest-balance and named-gap sentences through `/plans`
with synthetic data.

## Task 2 — monthly review

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p3-2-monthly-review` / `develop` / `develop`.
**Depends:** Task 1 merged.
**Owner sees and can do:** on Plans › Monthly review, pick a month, follow six steps to where each
is resolved, add a follow-up inline, **Mark review complete** or **Reopen review**, and see the
remaining actions; Overview says whether last month's review is complete.
**Files:** create `db/migrations/V50__monthly_review.sql`, `db/tests/0047_monthly_review.sql`,
`api/oneledger/routes/monthly_review.py`, `api/tests/test_monthly_review_live.py`,
`web/app/plans/monthly-review.tsx`, `web/app/plans/__tests__/monthly-review.test.tsx`; update
`api/oneledger/routes/transfers.py` and `api/oneledger/routes/wealth_history.py` (extract shared
helpers, no behavior change), `api/oneledger/routes/actions.py` (`previousMonthReview`),
`api/oneledger/main.py`, `api/tests/test_actions_live.py`, `web/app/plans/plans-screen.tsx`,
`web/app/overview/overview-screen.tsx`, `web/app/overview/__tests__/overview-screen.test.tsx`,
`web/app/__tests__/workspace.test.tsx` (it pins the overview's requests), generated
`web/types/api/`; the specification's review documents; `docs/superpowers/project-register.md`
(deferred planned remittances with funding and receipt dates, and data-quality items in the action
list).
**Contract:**

- V50 is the specification's DDL and RLS, exactly.
- `routes/transfers.py`: extract the candidate computation behind `GET /transfers/candidates` into
  a function returning the ranked shown list; the route and `monthly_review.py` both call it.
  `routes/wealth_history.py`: extract the per-month totals read into a helper the route and review
  share. Existing tests for both routes pass unchanged.
- `routes/monthly_review.py`: the three routes, default month, future-month 422, `changeBase`
  computed in Python only when both months are complete, completion `INSERT … ON CONFLICT DO
  NOTHING` returning 201/200, delete idempotent.
- `GET /actions` adds `previousMonthReview` from one `monthly_review` primary-key read.
- Web: the specification's Monthly review workflow; Plans sections ordered Needs attention, Monthly
  review, Forecast, Commitments; no browser arithmetic.

**Tests:**

- SQL `0047`: owner/member/foreign/anonymous isolation and the first-day check.
- Live `test_monthly_review_live.py`: counts for a synthetic month with one coverage gap, one
  transfer candidate pair, one unclassified reportable row, two complete stored month totals and a
  known `changeBase`; an incomplete month gives null change; completion 201 then 200 with the same
  `completedAt`; reopen 204 twice; future month 422; member 403 and foreign isolation;
  `GET /monthly-review` changes no row.
- Live `test_actions_live.py`: `previousMonthReview` before and after completion.
- Existing `test_transfers_live.py` and `test_wealth_history_live.py` pass unchanged.
- Web: step counts and links; complete/reopen requests and refetch; overview review status.

**Commands:** shared DB up and `scripts/hooks/db-tests.sh`; `(cd api && uv run pytest -q
tests/test_monthly_review_live.py tests/test_actions_live.py tests/test_transfers_live.py
tests/test_wealth_history_live.py)`; types; web `app/plans app/overview
app/__tests__/workspace.test.tsx`; `scripts/check-diff-size.sh`.
**Acceptance:** the specification's monthly review sentence through `/plans` and `/overview` with
synthetic data; the full Phase 3.2 acceptance holds after this branch.
