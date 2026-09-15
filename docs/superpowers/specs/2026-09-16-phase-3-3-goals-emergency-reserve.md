# Phase 3.3 — goals and emergency reserve

Owner-approved outcome: [roadmap Phase 3.3](../../product/roadmap.html), planned at personal scale
on 16 September 2026 together with [Phase 3.4](2026-09-16-phase-3-4-protection-administration.md).
The [dated plan](../plans/2026-09-16-phase-3-3-goals-emergency-reserve.md) declares both branches.
Workspace: `/Users/sandjaieravi/dev/OneLedger`; every branch starts from and targets `develop`.

## Product outcome

The owner earmarks resources — an amount of an asset account, or a whole account — for a goal or
for the emergency reserve. A reservation is a plan, not a ledger transaction: it never changes a
balance, net worth or report, and the same resource can never be reserved twice. Releasing a
reservation frees the resource and leaves wealth unchanged. Each goal has a target amount in a
reporting currency, a target date, a priority and an optional planned monthly contribution, and
shows its current earmarked value split into what was contributed, market movement and FX
movement, with a projected shortfall or surplus.

The emergency reserve shows which accounts the owner has marked eligible (with access delay and
restriction notes), an explicit essential-spend baseline (computed from chosen category buckets over
a reference period, or entered manually), and estimated months of coverage: eligible resources not
reserved for a goal, divided by that baseline. Zero or unknown spending reads "not enough
information", never infinite runway. Each currency's resources and the rates used are shown. A goal
with a shortfall and a reserve below its target appear in Needs attention.

## Authority and decisions in force

- [ADR-0002](../../adr/0002-frozen-dual-currency-amounts.html): a reservation freezes its native
  amount and the rate into its goal's currency on the day it is made; current values use current
  rates and never rewrite the frozen facts.
  [ADR-0010](../../adr/0010-signed-balance-convention.html): only asset accounts are reservable.
- [ADR-0013](../../adr/0013-python-backend-service.html),
  [ADR-0016](../../adr/0016-revocable-owner-sessions.html): owner-only routes, household lock on
  writes, explicit `household_id` filters, RLS as defence in depth.
  [ADR-0015](../../adr/0015-flyway-for-schema-migrations.html): forward-only.
- [Finance OS F09/F10](../../product/finance-os.html), scenarios P07 and P08; the master plan
  boundary that projections and plans are never postings.
- Existing reads reused, not copied: `dashboard.VALUATION_PERIOD_FOR_ACCOUNT`,
  `investments.positions.read_positions` and `current_rate_pair`, `money.translate`,
  `routes/coverage.py` `coverage_rows()`, `routes/actions.py` `action_list()`.

## Data model contract

Phase 3.2 ends at V50 and SQL test `0047`. Task 1 adds V51 with `0048`; Task 2 adds V52 with `0049`.

### Goals and reservations (V51)

```sql
CREATE TYPE goal_state AS ENUM ('active', 'achieved', 'abandoned');
CREATE TYPE reservation_purpose AS ENUM ('goal', 'emergency');
CREATE TYPE reservation_kind AS ENUM ('amount', 'whole_account');

CREATE TABLE goal (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  idempotency_key      UUID NOT NULL,
  name                 TEXT NOT NULL,
  target_amount        NUMERIC(20,4) NOT NULL,
  target_currency      CHAR(3) NOT NULL REFERENCES currency(code),
  target_date          DATE NOT NULL,
  priority             SMALLINT NOT NULL,
  planned_monthly      NUMERIC(20,4),
  state                goal_state NOT NULL DEFAULT 'active',
  closed_at            TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_goal_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_goal_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT goal_name_is_bounded CHECK (char_length(name) BETWEEN 1 AND 80 AND name = btrim(name)),
  CONSTRAINT goal_target_is_positive CHECK (target_amount > 0),
  CONSTRAINT goal_priority_is_bounded CHECK (priority BETWEEN 1 AND 5),
  CONSTRAINT goal_planned_is_nonnegative CHECK (planned_monthly IS NULL OR planned_monthly >= 0),
  CONSTRAINT goal_closure_agrees CHECK ((state = 'active') = (closed_at IS NULL))
);

CREATE TABLE resource_reservation (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  idempotency_key        UUID NOT NULL,
  purpose                reservation_purpose NOT NULL,
  goal_id                UUID,
  account_id             UUID NOT NULL,
  kind                   reservation_kind NOT NULL,
  native_amount          NUMERIC(20,4) NOT NULL,
  currency               CHAR(3) NOT NULL REFERENCES currency(code),
  reserved_on            DATE NOT NULL,
  rate_currency          CHAR(3) NOT NULL REFERENCES currency(code),
  rate_at_reservation    NUMERIC(20,10) NOT NULL,
  released_at            TIMESTAMPTZ,
  release_reason         TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_reservation_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_reservation_goal_scope FOREIGN KEY (household_id, goal_id)
    REFERENCES goal (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_reservation_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT reservation_purpose_agrees CHECK ((purpose = 'goal') = (goal_id IS NOT NULL)),
  CONSTRAINT reservation_amount_is_positive CHECK (native_amount > 0),
  CONSTRAINT reservation_rate_is_positive CHECK (rate_at_reservation > 0),
  CONSTRAINT reservation_release_agrees CHECK (
    released_at IS NOT NULL OR release_reason IS NULL),
  CONSTRAINT reservation_reason_is_bounded CHECK (release_reason IS NULL OR (
    char_length(release_reason) BETWEEN 1 AND 200 AND release_reason = btrim(release_reason)))
);
CREATE UNIQUE INDEX uq_reservation_whole_account ON resource_reservation (account_id)
  WHERE kind = 'whole_account' AND released_at IS NULL;
CREATE INDEX idx_reservation_account_active ON resource_reservation (household_id, account_id)
  WHERE released_at IS NULL;
```

- `preserve_resource_reservation()` refuses `DELETE` and every `UPDATE` except setting
  `released_at` and `release_reason` once on a row whose `released_at` is null. A goal is edited in
  place until it closes; a closed goal refuses every update (`preserve_closed_goal()`).
- For an emergency reservation, `rate_currency` is the household base currency; for a goal
  reservation it is the goal's `target_currency`. `rate_at_reservation` is the rate from `currency`
  into `rate_currency` on `reserved_on` (1 when equal), and `native_amount` is the amount reserved
  (`amount`) or the account's native value on `reserved_on` (`whole_account`); `currency` is always
  the account's currency.
- Both tables enable RLS `household_isolation FOR ALL TO authenticated`; trigger functions are
  revoked from `PUBLIC`.

### Emergency reserve and action keys (V52)

```sql
CREATE TYPE baseline_method AS ENUM ('reference_period', 'manual');

CREATE TABLE emergency_reserve (
  household_id         UUID PRIMARY KEY REFERENCES household(id),
  target_months        NUMERIC(4,1) NOT NULL,
  baseline_method      baseline_method NOT NULL,
  reference_months     SMALLINT NOT NULL DEFAULT 6,
  buckets              category_bucket[] NOT NULL DEFAULT '{need,debt_service}',
  manual_monthly_base  NUMERIC(20,4),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT emergency_target_is_bounded CHECK (target_months > 0 AND target_months <= 60),
  CONSTRAINT emergency_reference_is_bounded CHECK (reference_months BETWEEN 1 AND 24),
  CONSTRAINT emergency_buckets_are_spending CHECK (
    cardinality(buckets) >= 1
    AND buckets <@ '{need,want,investment,debt_service}'::category_bucket[]),
  CONSTRAINT emergency_manual_agrees CHECK (
    (baseline_method = 'manual') = (manual_monthly_base IS NOT NULL)
    AND (manual_monthly_base IS NULL OR manual_monthly_base > 0))
);

CREATE TABLE emergency_resource_account (
  household_id     UUID NOT NULL REFERENCES household(id),
  account_id       UUID NOT NULL,
  access_days      SMALLINT NOT NULL DEFAULT 0,
  restriction_note TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, account_id),
  CONSTRAINT fk_emergency_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT emergency_access_is_bounded CHECK (access_days BETWEEN 0 AND 365),
  CONSTRAINT emergency_note_is_bounded CHECK (restriction_note IS NULL OR (
    char_length(restriction_note) BETWEEN 1 AND 200 AND restriction_note = btrim(restriction_note)))
);

ALTER TABLE action_mark DROP CONSTRAINT action_mark_key_shape;
ALTER TABLE action_mark ADD CONSTRAINT action_mark_key_shape CHECK (action_key ~ (
  '^((follow-up|card|goal|reserve)\.[0-9a-f-]{36}'
  || '|commitment\.[0-9a-f-]{36}\.[0-9]{4}-[0-9]{2}-[0-9]{2}'
  || '|record\.[0-9a-f-]{36}\.(review|(renewal|maturity|deadline)\.[0-9]{4}-[0-9]{2}-[0-9]{2}))$'));
```

Both tables enable household RLS. The widened key shape also admits the Phase 3.4 `record.` keys so
that phase needs no further constraint change; `action_mark_dismissal_is_card` is unchanged.

## Account value rule

`api/oneledger/resources.py` exposes `account_native_values(cur, household_id, base, secondary,
as_of) -> dict[UUID, NativeValue]` for the household's active asset accounts, where `NativeValue`
is `{value: Decimal | None, currency, valuationMode, valueDate, reason}`:

- `ledger_balance`: `current_balance`, dated `current_balance_as_of::date`; `reason` null.
- `snapshot`: the latest `valuation_snapshot.value_native` in the account's current valuation period
  on or before `as_of` (the dashboard's selection); none is `missing_snapshot`.
- `holdings`: the sum of `read_positions(..., included_only=False)` `currentValueNative` for the
  account; any null is `missing_price`, and any position in a currency other than the account's is
  `mixed_currency`, both with `value` null.

The valuation-period selection reuses `dashboard.VALUATION_PERIOD_FOR_ACCOUNT`; the dashboard's own
behaviour does not change.

## Reservation rules

For an account, **reserved** is the sum of active `amount` reservations' `native_amount`, or the
whole value when an active `whole_account` reservation exists; **unreserved** is `value − reserved`
(never below zero); **overReserved** is `reserved > value`.

- Creating a reservation locks the household row, then refuses: an inactive, liability or foreign
  account (404 foreign, 422 `only an active asset account can be reserved`); an account whose value
  is null, or zero or below for a `whole_account` reservation (422 `this account has no current
  value to reserve`); an `amount` above unreserved (422 `this reservation is more than the account's
  unreserved value`); any reservation while a `whole_account` one is active, and a `whole_account`
  one while any reservation is active on the account (409 `this account is already reserved`); a
  goal that is closed (409 `a closed goal takes no reservation`); an emergency reservation on an
  account not marked eligible (422 `mark this account as an emergency resource first`); a missing
  rate into `rate_currency` on `reserved_on` (422 `no exchange rate is available for this
  reservation`).
- `reserved_on` is the server's today. Replay by idempotency key returns 200 with field equality
  (purpose, goal, account, kind, and `amount` when given), else 409.
- Release sets `released_at` and an optional reason once; a repeat is 200. Closing a goal releases
  its active reservations with reason `goal closed` in the same transaction.

## Goal progress

For each active reservation of a goal, with `rate_now` the current rate from its currency into the
goal currency (`current_rate_pair`; a goal currency is the household base or secondary currency,
422 otherwise) and `native_now` the reserved native amount (`amount`) or the account's current value
(`whole_account`):

- `change = native_now − native_amount` (zero for `amount`);
- `inflow` — the part of `change` that is new money, by the account's valuation mode:
  `ledger_balance` all of `change` (a balance moves only by postings); `holdings` the native
  `−net_amount` sum of the account's uncorrected `investment_transaction` rows of kinds `purchase`,
  `recurring_purchase`, `rights`, `sale` and `redemption` with `event_date` after `reserved_on`
  (purchases add, sales and redemptions subtract, as stored); `snapshot` zero, stated as an
  assumption that a snapshot's change is valuation movement;
- `contributed = translate(native_amount, rate_at_reservation) + translate(inflow, rate_now)`;
- `market = translate(change − inflow, rate_now)`;
- `current = translate(native_now, rate_now)`; `fx = current − contributed − market`.

Every term is in the goal currency, which is why a goal's `targetCurrency` is fixed once it has any
reservation. An account whose reserved total exceeds its value adds reason `over_reserved` to each
goal reserving it, and those goals report `current` from the reserved amounts with that reason
shown.

Goal totals sum these. `monthsRemaining` is the count of month-starts after `asOf` up to and
including `target_date` (0 once due); `projected = current + planned_monthly × monthsRemaining`;
`shortfall = max(target − projected, 0)`, `surplus = max(projected − target, 0)`; `percent =
current ÷ target × 100` at two places, half-even. All `Decimal`; money at four places. A missing
rate or value makes the goal `progressStatus: "unavailable"` with reasons and null figures, never a
partial total.

## Emergency coverage

- **Baseline.** `reference_period`: the `reference_months` complete calendar months before `asOf`'s
  month; spending is `−sum(amount_base)` of `v_transaction_reportable` rows with `txn_type <>
  'income'` whose category's effective bucket (`coalesce(category_override.bucket, category.bucket)`
  for the household, as `routes/categories.py` lists it) is in `buckets`, divided by
  `reference_months` at four places. Uncategorised spending in the period is reported separately and
  not counted. `manual`: `manual_monthly_base`. A baseline that is not positive is
  `not_enough_information`.
- **Resources.** For each eligible account: value, currency, `goalReserved` (active goal
  reservations), `emergencyReserved`, `eligible = value − goalReserved` (not below zero),
  `access_days`, restriction note, and its unarchived commitments with `next_due_date ≤ asOf + 30`
  (label, date, `remainingNext`). A null value is listed with its reason and counted nowhere.
- **Coverage.** `eligibleBase` sums each account's `eligible` translated at the current rate to base
  (a missing rate makes coverage unavailable); `coverageMonths = eligibleBase ÷ baseline` at two
  places; `targetBase = baseline × target_months`; `gapBase = max(targetBase − eligibleBase, 0)`.
  `reservedMonths` uses emergency reservations only. `currencies[]` lists native eligible totals and
  the rate and rate date used per currency. `coverageGapMonths` counts `missing`/`partial` coverage
  cells in the reference period.

## API boundary

Every route uses `require_owner`, locks the household row on writes, filters by household, and 404s
foreign ids. Bodies forbid extra fields; money is `_money` strings; names, notes and reasons are
NFKC-normalized and bounded as above.

### Task 1 — `routes/goals.py`

| Route | Contract |
| --- | --- |
| `GET /goals` | `?asOf=&includeClosed=false` → `{asOf, goals: GoalRow[], accounts: ResourceAccountRow[]}`; read-only |
| `POST /goals` | `{name, targetAmount, targetCurrency, targetDate, priority, plannedMonthly?, idempotencyKey}` → `GoalRow`; 201, 200 replay, 409 conflict |
| `PUT /goals/{goalId}` | same without key → `GoalRow`; closed 409; a changed `targetCurrency` once any reservation exists (active or released) is 409 `a goal with reservations keeps its currency` |
| `POST /goals/{goalId}/close` | `{state: "achieved" \| "abandoned"}` → `GoalRow`; releases its reservations; same state replay 200 |
| `POST /reservations` | `{purpose, goalId?, accountId, kind, amount?, idempotencyKey}` → `ReservationRow`; 201/200/409 |
| `POST /reservations/{reservationId}/release` | `{reason?}` → `ReservationRow`; idempotent |

`GoalRow`: fields, `state`, `closedAt`, `progressStatus`, `reasons`, `current`, `contributed`,
`market`, `fx`, `projected`, `shortfall`, `surplus`, `percent`, `monthsRemaining`, and
`reservations[]` (active first, then released) of `ReservationRow` (`id`, `purpose`, `goalId`,
`accountId`, `accountName`, `kind`, `nativeAmount`, `currency`, `reservedOn`, `rateAtReservation`,
`nativeNow`, `currentValue`, `releasedAt`, `releaseReason`). `ResourceAccountRow`: `accountId`,
`accountName`, `currency`, `valuationMode`, `value`, `valueDate`, `reason`, `reserved`,
`unreserved`, `overReserved`, `wholeAccountReserved`.

### Task 2 — `routes/emergency_reserve.py` and actions

| Route | Contract |
| --- | --- |
| `GET /emergency-reserve` | `?asOf=` → `EmergencyReserveResponse`; read-only; unconfigured returns `configured: false` with accounts |
| `PUT /emergency-reserve` | `{targetMonths, baselineMethod, referenceMonths, buckets, manualMonthlyBase?}` → response |
| `PUT /emergency-reserve/accounts/{accountId}` | `{accessDays, restrictionNote?}` → response; active asset account only |
| `DELETE /emergency-reserve/accounts/{accountId}` | → 204; 409 `release this account's emergency reservations first` while any is active |

`EmergencyReserveResponse`: `asOf`, `configured`, settings, `baseline: {method, monthlyBase,
referenceStart, referenceEnd, uncategorisedBase, status}`, `accounts[]`, `eligibleBase`,
`coverageMonths`, `reservedMonths`, `targetBase`, `gapBase`, `coverageStatus` (`available |
not_enough_information | unavailable`), `reasons`, `currencies[]`, `coverageGapMonths`,
`baseCurrency`, and emergency `reservations[]`.

`action_list` adds `goal.<goalId>` items (`reason: goal_shortfall`) for active goals with an
available progress and `shortfall > 0`, due date the target date, amount the shortfall in the goal
currency; and one `reserve.<householdId>` item (`reason: reserve_below_target`) when the reserve is
configured, coverage is available and `coverageMonths < target_months`, amount `gapBase`. Both
accept snooze marks; neither accepts dismissal. `GET /actions` and the monthly review counts pick
them up unchanged.

## Web workflows

**Task 1 — Plans › Goals** (anchor `#goals`). Each goal shows target, date, priority, current value,
percent, contributed / market / FX, planned monthly and projected, and a **Shortfall** or
**Surplus** line; unavailable progress names its reasons. **Add goal** and **Edit**; **Mark
achieved** and
**Abandon** (confirming that its reservations are released). **Reserve** opens a form: account
picker showing each account's value, reserved and unreserved; amount or whole account. Each
reservation lists its account, reserved date, contributed and current value and offers **Release**.
A resources table lists every reservable account's value, reserved, unreserved and an over-reserved
warning.

**Task 2 — Plans › Emergency reserve** (anchor `#reserve`). Settings (target months, baseline
method, reference months and buckets, or a manual monthly amount); the baseline with its period, the
uncategorised spending it leaves out, and "not enough information" when so; eligible accounts with
access days, restriction note, value, reserved for goals, eligible amount, upcoming commitments, and
**Reserve for emergencies** / **Release**; **Mark as emergency resource** for other asset accounts;
coverage months, target, gap, per-currency totals with rates, and an assumptions list (resources
reserved for a goal are not counted; commitments due in the next 30 days are shown but not
subtracted; coverage uses current rates; a reservation moves no money).

Server strings only; loading, empty, error states; no money arithmetic or money-string comparison in
TypeScript; one idempotency key per create draft.

## Failure behaviour and privacy

A refused reservation, goal or setting leaves every row as it was; closing a goal and releasing its
reservations commit together. Nothing here writes a transaction, balance or classification. A value
or rate that is unavailable is named, never approximated. Fixtures are synthetic.

## Required test evidence

- V51/V52 SQL: owner/member/foreign/anonymous isolation on all four tables; scope keys; shape
  checks; whole-account uniqueness; the reservation preservation trigger (update refused, single
  release allowed, delete refused); closed-goal preservation; the widened action key shape.
- Unit: goal progress decomposition (amount; whole-account on a ledger, holdings and snapshot
  account with a later deposit or purchase counted as contribution; FX only, market only, both),
  months remaining at month boundaries, shortfall/surplus, coverage and baseline arithmetic.
- Live API: P07 — 10,000 on one account, 6,000 for a goal and 4,000 for the emergency reserve, any
  further reservation refused, releasing 1,000 frees exactly 1,000, dashboard net worth unchanged
  throughout; whole-account exclusivity both ways; each 422/409; replay and conflicts; goal close
  releasing reservations; a currency change refused once reserved; progress with a synthetic rate
  change, a price change and a later purchase; P08 — 6,000
  eligible with 2,000 baseline is `3.00` months and a goal reservation on the same account is not
  counted again; reference-period baseline by bucket with an uncategorised row excluded; manual
  baseline; zero baseline `not_enough_information`; goal and reserve action items and snooze;
  household isolation with real foreign rows; `GET` routes change no row.
- Web: goal, reservation and reserve screens render server strings and post the right bodies.

## Acceptance

Through the web application with synthetic data: P07 and P08 hold as above; a goal shows
contributions separately from market and FX movement after a rate or price change; releasing a
reservation changes unreserved value and leaves net worth unchanged; the reserve shows its baseline
and period and reads "not enough information" with no spending; goal shortfall and reserve gap
appear in Needs attention. Owner checks on the real ledger record only structural counts.

## Explicit non-goals

Retirement, inflation and return scenarios (Phase 5.5); goal contributions as ledger transfers or
automatic sweeps; reservations on liabilities; a goal in a currency other than the reporting pair;
partial release (release and reserve again); budget-based baselines (Phase 4.1).

## Documents the implementation must reconcile

`docs/superpowers/schema.md` (V51, V52), `docs/design/data-model.html`,
`docs/design/api-contracts.html`, `docs/operations/testing-and-data-quality.html` (Phase 3.3 gate),
`docs/product/ui-information-architecture.html` (Planning view goals and reserve),
`docs/reference/glossary.html` (Reservation, Emergency reserve), and `docs/superpowers/progress.md`
for live state only.
