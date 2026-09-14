# Phase 2.2 — wealth history and attribution

Owner-approved outcome: [roadmap Phase 2.2](../../product/roadmap.html), planned at personal scale on
14 September 2026. The [dated plan](../plans/2026-09-14-phase-2-2-wealth-history-attribution.md)
declares both branches. Workspace: `/Users/sandjaieravi/dev/OneLedger`; every branch starts from and
targets `develop`.

## Outcome

The owner sees net worth at every month end from the history anchor, in both reporting currencies,
with each account's native value, evidence date and translation rate stored so a past figure is
reproducible. For any month, a waterfall explains the change: retained income, investment income,
internal movements and costs, other external changes, market movement, asset revaluation, FX
translation and a visible residual. Components plus residual equal the actual change exactly.

## Authority

- [Multi-currency and FX §7](../../design/multi-currency-and-fx.html) owns the decomposition: native
  change valued at the closing rate plus translation of the opening value, with FX on intra-period
  flows moved from their flow line into FX translation. Consolidation comes before attribution, and
  the residual is displayed (Invariant I7).
- [ADR-0002](../../adr/0002-frozen-dual-currency-amounts.html): flows keep their frozen booked-date
  reporting amounts; the bridge to closing rates is an explicit FX component.
- [ADR-0006](../../adr/0006-manual-valuation-snapshots.html): a snapshot carries forward flat.
- [ADR-0010](../../adr/0010-signed-balance-convention.html): liabilities are signed values.
- [ADR-0011](../../adr/0011-36-month-history-horizon.html): history starts at 1 January 2024
  (`coverage.HISTORY_ANCHOR`).

## Month-end positions

A position is one counted account, or one holding of a counted holdings-mode account, at a month end
`t` (the last calendar day). Wealth history uses its own counted-account predicate, not the
dashboard's `is_active` filter: `include_in_net_worth AND (opened_on IS NULL OR opened_on <= t) AND
(closed_on IS NULL OR closed_on >= t)`, so closed accounts keep their history. The valuation mode is
the account's valuation period active at `t`, using the dashboard's shared
`VALUATION_PERIOD_FOR_ACCOUNT` selection; the dashboard itself is unchanged. `read_positions` gains
a keyword that replaces its `a.is_active` filter with the same lifetime predicate, and wealth
history calls it with `include_closed=True` so a fully sold holding reports zero value.

| Mode | Native value at `t` | Evidence date |
| --- | --- | --- |
| `ledger_balance` | for `t ≥ opening_balance_date`: `opening_balance` + sum of `v_transaction_posting.amount` for the account booked on or before `t` (the same figure as V14 `assert_account_reconciles` and the V10 balance trigger, including rows booked on the opening date) | latest counted booked date, else `opening_balance_date` |
| `holdings` | per holding: `read_positions(as_of=t)` units × accepted price on or before `t` | the price date |
| `snapshot` | latest `valuation_snapshot.value_native` on or before `t` | the snapshot `as_of_date` |

A month end before an account's `opening_balance_date` has no ledger position (`status =
before_opening`). Each position translates with `current_rate_pair(currency, base, secondary, t)`
and stores `rate_to_base`, `rate_to_secondary`, their real dates and carried flags, and
`value_base = round(native × rate_to_base, 4)` (secondary likewise). Status is `available`,
`missing_price`, `missing_rate`, `no_snapshot` or `before_opening`; an unavailable position has null
values and makes that month's totals `complete = false` without being counted as zero;
`before_opening` also makes the month incomplete (fail closed, like the dashboard), and attribution
for such a month is `unavailable` naming the account. A snapshot position is translated at the
month-end rate, so it can differ from the dashboard's frozen snapshot-date figure for the same day;
`api-contracts.html` records that difference.

### Data (V46)

```sql
CREATE TYPE month_end_position_status AS ENUM (
  'available', 'missing_price', 'missing_rate', 'no_snapshot', 'before_opening');

CREATE TABLE month_end_position (
  household_id        UUID NOT NULL REFERENCES household(id),
  month_end           DATE NOT NULL,
  account_id          UUID NOT NULL,
  holding_id          UUID,
  valuation_mode      account_valuation_mode NOT NULL,
  currency            CHAR(3) NOT NULL REFERENCES currency(code),
  native_value        NUMERIC(20,4),
  evidence_date       DATE,
  rate_to_base        NUMERIC(20,10),
  rate_to_base_date   DATE,
  rate_to_base_carried BOOLEAN,
  rate_to_secondary   NUMERIC(20,10),
  rate_to_secondary_date DATE,
  rate_to_secondary_carried BOOLEAN,
  value_base          NUMERIC(20,4),
  value_secondary     NUMERIC(20,4),
  status              month_end_position_status NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_month_end_position_account FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_month_end_position_holding FOREIGN KEY (household_id, holding_id)
    REFERENCES holding (household_id, id) ON DELETE CASCADE,
  CONSTRAINT month_end_position_values_agree CHECK (
    (status = 'available' AND native_value IS NOT NULL AND value_base IS NOT NULL
      AND value_secondary IS NOT NULL)
    OR (status <> 'available' AND value_base IS NULL AND value_secondary IS NULL))
);
CREATE UNIQUE INDEX uq_month_end_position
  ON month_end_position (household_id, month_end, account_id, holding_id) NULLS NOT DISTINCT;
```

RLS as V43. Rows are a recomputable cache of evidence, not history: a refresh replaces a month's
rows in one transaction.

## Attribution for one month

For month `m` with opening month end `t0` (the previous month end; 31 December 2023 is computed as
the opening point for January 2024) and closing month end `t1`, per reporting currency `C` (base and
secondary independently), quantities are exact `Decimal` and rounded to 4 places only where named.
A position absent at one end has `V_nat = 0` at that end. `r1` is the rate to `C` at `t1` for the
position's or flow's currency; a missing `r1` for any held or flowing currency makes the month
`unavailable`.

- **Actual change** = closing total − opening total, where totals sum `value_C` of positions. The
  month is `unavailable` with reasons unless both month ends are complete.
- **Ledger positions.** Each `v_transaction_posting` row on a counted ledger-mode account booked in
  `(t0, t1]` is a flow `f` and goes to exactly one line, first match wins:
  1. the row is in `v_transaction_reportable` → **retained income**;
  2. `is_excluded` → **other external changes**;
  3. `txn_type = 'investment_income'` → **investment income**;
  4. `transfer_in`, `transfer_out`, `investment_buy`, `investment_sell`, `investment_fee`,
     `loan_disbursement`, `loan_repayment` → **internal movements and costs**;
  5. `opening_balance`, `adjustment` → **other external changes**.

  Each line takes the flow's frozen `amount_C`. The position then adds
  `round((V1_nat − V0_nat − Σf_nat) × r1, 4)` to **other external changes** (zero when the position
  exists at both ends; the opening balance when it appears; the remaining balance when it closes).
- **Holding positions** (read with `include_closed=True`). `F` is the holding's active events in the
  window whose kind moves cash with the holding (`purchase`, `recurring_purchase`, `rights`, `sale`,
  `redemption`, `maturity`), `F_nat = −Σ net_amount`. **Internal movements and costs** takes
  `−Σ net_amount_C` (frozen); **market movement** takes `round((V1_nat − V0_nat − F_nat) × r1, 4)`.
  Cash distributions, interest, charges and tax never enter `F`; a reinvested distribution has a zero
  net amount, so its units appear in market movement.
- **Snapshot positions.** **Asset revaluation** takes `round((V1_nat − V0_nat) × r1, 4)`.
- Postings on holdings-mode or snapshot-mode accounts are never flows, so nothing is counted both as
  a flow and as market movement or revaluation.
- **FX translation** = for every position with `V0_nat ≠ 0`, `round(V0_nat × r1, 4) − value_C(t0)`;
  plus, for every ledger flow and every holding funding amount, its closing-rate value minus its
  frozen value: `round(x_nat × r1, 4) − x_C`.
- **Residual** = actual change − sum of all lines, displayed. For complete synthetic data it is
  exactly zero apart from 4-place rounding differences, which stay inside the residual.

`api/oneledger/attribution.py` multiplies flows by closing rates on purpose (the FX bridge), so it is
added to `scripts/check-identifiers.py`'s `RATE_MULTIPLIERS` allowlist with that reason.

## API

| Route | Contract |
| --- | --- |
| `POST /wealth-history/refresh` | Owner-only; recomputes positions for 31 December 2023 and every month end from the anchor through the last completed month, replacing rows atomically (a late failure leaves the previous rows intact); returns counts |
| `GET /wealth-history` | Months with totals in both currencies, `complete`, counts by status and the last `computed_at`; `GET` never recomputes |
| `GET /wealth-history/{monthEnd}` | That month's positions: account name, holding security name, mode, currency, native value, evidence date, rates with dates and carried flags, values, status |
| `GET /wealth-history/{monthEnd}/attribution` | Per currency: opening, closing, actual change, retained income, investment income, internal movements and costs, other external changes, market movement, asset revaluation, FX translation and residual, or `unavailable` with reasons |

Decimal strings on the wire; no identifier beyond the account name and ordinary mask.

## Web

A **Wealth** screen: a month table (and simple bar or line chart built from server strings only) of
net worth in the chosen reporting currency with incomplete months labelled and a **Refresh
history** action; selecting a month shows its positions and the attribution waterfall as a labelled
list with the residual always visible. No client arithmetic.

## Required test evidence

- Unit (pure attribution, dated cash-flow fixtures per the FX design's bridge requirement): a
  two-currency month with income, spending, a cross-currency transfer with spread, a funded purchase
  of a new holding bought cross-currency, a full sale, market movement, a snapshot revaluation, a
  liability, a base-currency account, a mid-window account opening and a rate move sums to the actual
  change with residual exactly zero (4-place rounding aside); a missing closing rate or a
  `before_opening` month makes attribution unavailable.
- Live: refresh stores month-end ledger balances equal to `assert_account_reconciles`' figure
  including rows booked on the opening date, holdings
  valued with the price and rate at the month end, snapshot carry-forward, before-opening and missing
  rate statuses; refresh twice is idempotent; a late refresh failure leaves previous rows intact;
  member refusal on all four routes and foreign household isolation; attribution API matches the
  unit result for a seeded month; GET does not recompute.

## Acceptance

Through the application, month-end net worth from January 2024 is stored with evidence dates and
rates, and each complete month's waterfall reconciles to its actual change with the residual shown.
Owner-operated: after real imports, record structural counts of complete months and months with a
non-zero residual only.

## Non-goals

Daily history; per-lot realised gains; attributing charges separately from market movement inside
holdings; forecasting; storing attribution rows (computed on read).

## Documents to reconcile

`docs/superpowers/schema.md` (V46), `docs/design/data-model.html` (retire the planned
`net_worth_snapshot` and `net_worth_attribution` shapes), `docs/design/api-contracts.html` (replace
the planned `/networth` rows; note snapshot translation at month-end rates),
`docs/design/multi-currency-and-fx.html` (§6 materialisation and §7 bridge now implemented),
`docs/operations/testing-and-data-quality.html`, `docs/superpowers/progress.md`.
