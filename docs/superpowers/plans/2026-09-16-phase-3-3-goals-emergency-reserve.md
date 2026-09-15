# Phase 3.3 goals and emergency reserve implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Exclusive reservations of asset resources for goals and the emergency reserve; goal
progress split into contributions, market and FX movement; an explicit essential-spend baseline and
coverage.

**Architecture:** V51 adds `goal` and `resource_reservation`; V52 adds the reserve settings,
eligible accounts and the widened action key shape. `resources.py` computes native account values
from the existing valuation rules; routes lock the household and filter by it; progress and coverage
are pure `Decimal` functions. See the [Phase 3.3
specification](../specs/2026-09-16-phase-3-3-goals-emergency-reserve.md).

**Stack:** Existing Flyway SQL, Python/FastAPI/psycopg and Next.js/React; no new dependency.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; branch
  from current `develop` (rebase on `origin/develop` before push); one PR per branch to `develop`
  titled `[P3.3] …`. Planned and reviewed once with Phase 3.4.
- Money is `NUMERIC`/`Decimal`/strings; no TypeScript money arithmetic or money-string comparison
  (the server returns flags such as `overReserved` and statuses). Every route: `require_owner`,
  household lock on writes, explicit `owner.household_id` filters. V50 and earlier are not edited.
- Synthetic data only. Foreign-household tests use real rows in the other household and assert them
  unchanged; member refusal is proven by `test_health.py`'s owner-dependency inventory.
- The implementer commits early and locally, runs affected tests including `uv run mypy .`, and does
  not push or run `scripts/verify.sh`. One Opus `/oneledger-code-review` in owner-authorized fix
  mode applies its repairs; the orchestrator confirms money, authorization and migration repairs;
  then `scripts/verify.sh` once, push, PR, merge.
- Shared commands: `eval "$(scripts/test-db.sh up)"`; `scripts/hooks/db-tests.sh`; types
  `(cd web && npm run api:types)`; API `(cd api && uv run pytest -q <files> && uv run mypy . &&
  uv run ruff check . && uv run ruff format --check .)`; web `(cd web && npx vitest run <paths> &&
  npm run typecheck && npm run lint && npm run api:types:check)`; `npx markdownlint-cli2` on changed
  Markdown; `scripts/check-diff-size.sh`.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | goals and reservations | `feature/p3-3-goals-reservations` | 1,350–1,650 | 1,600–2,000 | Phase 3.2 |
| 2 | emergency reserve and goal actions | `feature/p3-3-emergency-reserve` | 1,150–1,400 | 1,400–1,800 | Task 1 |

## Task 1 — goals and reservations

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p3-3-goals-reservations` / `develop` / `develop`.
**Depends:** Phase 3.2 merged (develop `0441d62` or newer).
**Owner sees and can do:** on Plans › Goals, add, edit, achieve or abandon a goal; reserve an amount
or a whole account for it from a resources table showing each account's value, reserved and
unreserved; release a reservation; see each goal's current value, percent, contributed, market and
FX movement, planned monthly, projected, and shortfall or surplus, or why progress is unavailable.
**Files:** create `db/migrations/V51__goals_and_reservations.sql`,
`db/tests/0048_goals_and_reservations.sql`, `api/oneledger/resources.py`, `api/oneledger/goals.py`
(pure progress math), `api/oneledger/routes/goals.py`, `api/tests/test_goals.py`,
`api/tests/test_goals_live.py`, `web/app/plans/goals.tsx`, `web/app/plans/__tests__/goals.test.tsx`;
update `api/oneledger/main.py`, `web/app/plans/plans-screen.tsx`, generated `web/types/api/`; the
specification's documents for V51 and the Task 1 routes.
**Contract:**

- V51 is the specification's DDL, `preserve_resource_reservation()`, `preserve_closed_goal()`, RLS
  and revokes, exactly.
- `resources.account_native_values` implements the account value rule, reusing
  `dashboard.VALUATION_PERIOD_FOR_ACCOUNT` and `read_positions`; a shared reserved/unreserved helper
  serves `GET /goals` accounts, reservation refusal and (Task 2) the reserve.
- `goals.py`: pure `reservation_progress(...)`, `goal_totals(...)`, `months_remaining(as_of,
  target_date)` per "Goal progress", no database access. The database reads that feed progress
  (goals, reservations, the holdings inflow query) live in `resources.py` as functions taking a
  cursor, not in `routes/`, so `routes/actions.py` can reuse them without importing another route
  module (the Phase 3.2 import-cycle deviation).
- `routes/goals.py`: the six routes; "Reservation rules" refusal order and sentences exactly;
  reservations for `purpose: "emergency"` are refused here until Task 2 wires eligibility (422
  `mark this account as an emergency resource first` holds because no account is yet eligible).
- Web per the Task 1 workflow; Plans section order becomes Needs attention, Monthly review, Goals,
  Forecast, Commitments.

**Tests:**

- SQL `0048`: isolation on both tables; scope keys; shape checks; whole-account unique index;
  reservation update refused except one release, delete refused; closed-goal update refused.
- Unit `test_goals.py`: amount vs whole-account decomposition, FX-only, market-only and both, exact
  four-place sums that add to current, months remaining at month boundaries, shortfall/surplus,
  percent.
- Live `test_goals_live.py`: P07's goal half (reserve 6,000 of 10,000, 4,001 refused, whole-account
  refused while an amount is active and vice versa, release 1,000 frees exactly 1,000) with
  dashboard net worth unchanged; each refusal; replay and conflict; close releases reservations;
  progress after a synthetic rate change and a synthetic price change; goal currency outside the
  reporting pair 422; isolation with real foreign rows; `GET /goals` changes no row.
- Web: goal form and reserve form bodies; figures and over-reserved warning from server strings.

**Commands:** shared; API `tests/test_goals.py tests/test_goals_live.py tests/test_dashboard_live.py
tests/test_health.py`; web `app/plans`.
**Acceptance:** the specification's goal and release sentences through `/plans` with synthetic data.

## Task 2 — emergency reserve and goal actions

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p3-3-emergency-reserve` / `develop` / `develop`.
**Depends:** Task 1 merged.
**Owner sees and can do:** on Plans › Emergency reserve, set target months and a baseline from
chosen buckets over a reference period or a manual amount; mark accounts as emergency resources with
access days and restriction notes; reserve or release emergency amounts; see coverage months,
target, gap, per-currency totals with rates, upcoming commitments on eligible accounts and "not
enough information" when spending is unknown. Needs attention lists goals with a shortfall and a
reserve below target.
**Files:** create `db/migrations/V52__emergency_reserve.sql`, `db/tests/0049_emergency_reserve.sql`,
`api/oneledger/emergency.py` (pure baseline and coverage math),
`api/oneledger/routes/emergency_reserve.py`, `api/tests/test_emergency.py`,
`api/tests/test_emergency_reserve_live.py`, `web/app/plans/emergency-reserve.tsx`,
`web/app/plans/__tests__/emergency-reserve.test.tsx`; update `api/oneledger/routes/goals.py`
(emergency reservations require eligibility), `api/oneledger/routes/actions.py`,
`api/oneledger/main.py`, `api/tests/test_actions_live.py`, `web/app/plans/plans-screen.tsx`,
`web/app/plans/needs-attention.tsx`, generated `web/types/api/`; the specification's documents for
V52, the reserve routes and the new action reasons.
**Contract:**

- V52 is the specification's DDL, RLS and the replaced `action_mark_key_shape`, exactly.
- `emergency.py`: pure baseline (`reference_period` and `manual`) and coverage per "Emergency
  coverage"; the route supplies spending totals from one `v_transaction_reportable` query joined to
  categories and `category_override` for the effective bucket.
- `routes/emergency_reserve.py`: the four routes; `GET` read-only; eligible-account removal 409
  while an emergency reservation is active.
- `routes/actions.py`: `goal.<id>` and `reserve.<householdId>` items per the specification, reusing
  `goals.py` progress and `emergency.py` coverage through `resources.py` readers; mark key parsing
  accepts the widened shapes, and a snooze key must name an active household goal (`goal.`) or the
  owner's own household id (`reserve.`), else 404 (Phase 3.4 `record.` keys are parsed but have no
  items yet, so a snooze on one is 404).
- Web per the Task 2 workflow; Plans order Needs attention, Monthly review, Goals, Emergency
  reserve, Forecast, Commitments; Needs attention shows the two new reasons as sentences with
  snooze.

**Tests:**

- SQL `0049`: isolation on both tables; target, reference, bucket and manual checks; the widened key
  shape accepting every documented key and refusing a malformed one.
- Unit `test_emergency.py`: bucket baseline over N months, manual, zero and negative baselines,
  coverage and gap at two and four places.
- Live `test_emergency_reserve_live.py`: P07 in full (a further reservation of either purpose
  refused after 6,000 goal and 4,000 emergency on 10,000; net worth unchanged); P08 (`3.00` months
  with 6,000 eligible and a 2,000 baseline; a goal reservation on the same account not counted);
  reference-period baseline by bucket with an uncategorised row reported and excluded;
  `not_enough_information`; a missing rate `unavailable`; eligibility add/remove and the 409;
  upcoming commitments listed; isolation with real foreign rows; `GET` changes no row.
- Live `test_actions_live.py`: goal shortfall and reserve gap items appear, disappear when resolved,
  snooze; dismissal refused.
- Web: settings and eligibility bodies; coverage figures and "not enough information" from the
  server.

**Commands:** shared; API `tests/test_emergency.py tests/test_emergency_reserve_live.py
tests/test_goals_live.py tests/test_actions_live.py tests/test_monthly_review_live.py
tests/test_health.py`; web `app/plans app/overview`.
**Acceptance:** the specification's acceptance in full through `/plans` with synthetic data.
