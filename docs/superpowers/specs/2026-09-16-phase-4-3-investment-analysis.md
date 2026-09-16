# Phase 4.3 — investment analysis

Roadmap outcome ([Phase 4.3](../../product/roadmap.html)): returns, benchmarks, allocation drift
and contribution effects reconcile to transaction-level holdings and dated prices. The
[master plan](../../product/master-implementation-plan.html#phase-4-delivery) lists portfolio and
class returns, benchmarks, contribution effects and allocation drift. This builds on Phase 1.6
holdings, prices, XIRR and allocation
([investments specification](2026-09-12-phase-1-6-investments.md)) and Phase 2.2 month-end positions
([wealth history specification](2026-09-14-phase-2-2-wealth-history-attribution.md)). It adds no
second holdings model. Plan: [Phase 4.3
plan](../plans/2026-09-16-phase-4-3-investment-analysis.md).

## Outcome

The Investments screen answers, at a chosen as-of date: what each asset class and account returned
(the same XIRR engine as today's holding and portfolio figures); how complete months moved because
of money added or withdrawn versus market and FX; how allocation by asset class changed against a
target; and, when the owner has named a benchmark security, how a period return compares with that
security's own dated prices. Missing prices, incomplete months and snapshot-mode accounts are
listed, never filled.

## Evidence, not inference of money

- Every value is units rebuilt from active investment events, an **accepted** `price_snapshot` on
  or before the date (the same latest-at-or-before rule as `GET /portfolio/positions`), frozen
  `net_amount_base` on those events, or a stored `month_end_position` row from the last wealth
  history refresh. Nothing interpolates a price, fabricates an index, annualises a gap or treats an
  unvalued holding as zero in a denominator.
- A stored month end whose holdings-mode `month_end_position` rows are not all `available`, or
  whose previous month end is missing or incomplete, is **incomplete**. It is listed with its
  status counts and contributes no period return, contribution effect or allocation ratio. The
  current unclosed calendar month is absent until a wealth refresh stores it, matching
  `GET /wealth-history`.
- Analysis is in the household base currency. Secondary amounts are omitted rather than converted
  in the browser (ADR-0002). Snapshot-mode and ledger-balance accounts are out of holdings analysis
  and listed as coverage, not valued as lots (ADR-0005, ADR-0006).
- `GET /portfolio/analysis` never writes and never refreshes wealth history. An unrefreshed wealth
  history yields an empty month series with reason `wealth_history_not_refreshed`; live class and
  account XIRR still compute from events and prices, as `/portfolio/summary` already does.

## Class and account returns

Holding and portfolio XIRR stay on the existing routes. Phase 4.3 adds **asset-class** and
**account** XIRR from the same solver, grid, tolerances and reason codes as
`api/oneledger/investments/returns.py`.

A class (or account) series is the portfolio-flow construction restricted to holdings whose
security `asset_class` (or `account_id`) matches: `classify_event_flows` on those holdings' active
events on or before as-of, plus one terminal flow equal to the sum of those holdings' current base
values. Split and reinvested-distribution legs stay cash-neutral. A switch, systematic-transfer or
platform-transfer leg is cash-neutral only when its counterpart holding (via
`counterpart_transaction_id`) is in the same group; otherwise the group is `incomplete_history`.
A missing price or as-of base rate on any open holding in the group fails the whole group
(`missing_price`). An unknown event kind is `blocked_action`. Class labels are the `asset_class`
enum. Account labels are the account display name from the accounts read model, not the UUID
`group_allocation` currently stores.

## Period return (Modified Dietz)

XIRR remains the money-weighted figure from inception to as-of. Each **complete** month, each
calendar year whose every month in range is complete, and the inception-to-as-of window also report
a **Modified Dietz** period return so a month with large contributions is not compared to a
benchmark as if the money had been invested all month.

For a window `(t0, t1]` with opening value `BMV` at `t0` and closing value `EMV` at `t1` (both
base):

- **Calendar month:** `t1` is that stored month end; `t0` is the previous stored month end.
  `BMV`/`EMV` are the month-window population's `value_base` sums (below). 31 December 2023 is
  opening evidence for January 2024 only; it is not a month row (same as `GET /wealth-history`).
- **Inception to as-of:** `t0` is the calendar day before the household's earliest active
  investment event, `BMV` is exactly 0, and `EMV` is live `read_positions` at as-of. This does not
  depend on whether wealth history has been refreshed. Every active event on or before as-of falls
  in `(t0, t1]`.

- Window flows are `classify_event_flows` on active events booked in `(t0, t1]`. XIRR signs treat a
  purchase as negative. Dietz net contribution `C` is the **negation** of those amounts, so money
  added to the portfolio is positive.
- Weight `w_i = (t1 − d_i) / (t1 − t0)` in whole days; a flow on `t1` has weight 0. Weighted
  contribution `W = sum(C_i × w_i)`.
- `dietz = (EMV − BMV − C) / (BMV + W)` as a fraction, times 100, quantized to eight decimal places
  half-even (the XIRR percent scale). Null with reason `no_denominator` when `BMV + W` is zero;
  null with `missing_price` when either endpoint is incomplete. `no_denominator` and
  `wealth_history_not_refreshed` are analysis reasons, not `XirrReason` values.

Dietz, contribution lines and benchmark excess are computed for the portfolio and for each asset
class that has holdings in the window. Account rows report XIRR only.

The browser never computes this. Intra-month prices are not required and must not be interpolated.

## Contribution effects

Live class and account XIRR use the as-of holdings population already selected by
`_HOUSEHOLD_HOLDINGS_SQL` (active accounts in holdings mode). **Month windows use a different,
single population:** every holding that has a holdings-mode `month_end_position` row at `t0` or at
`t1`. That window's flows are exactly those holdings' active events in `(t0, t1]`. Mixing the two
populations is forbidden: a closed or net-worth-excluded account must not contribute values without
flows, or flows without values.

For each complete month, that month-window population only, in base:

| Line | Source |
| --- | --- |
| `opening` / `closing` | Holdings-mode `month_end_position.value_base` summed at `t0` / `t1` |
| `contributions` | Dietz-signed flows whose kind is `purchase`, `recurring_purchase` or `rights` |
| `withdrawals` | Dietz-signed `sale`, `redemption` or `maturity` (negative when money leaves) |
| `income` | Dietz-signed `cash_distribution` and `interest` |
| `charges` | Dietz-signed `charge` and `withheld_tax` |
| `marketAndFx` | `closing − opening − (contributions + withdrawals + income + charges)` |

`contributions + withdrawals + income + charges` equals Dietz `C`. `marketAndFx` is the plug, so
`opening + C + marketAndFx = closing` is definitional and is not a test. The testable identity is
that the class lines of one window sum to the portfolio line of that window (each money field).
`marketAndFx` is one line (price change and FX together). Splitting FX the way household wealth
attribution does is out of scope — that waterfall already exists on Wealth.

The same lines are reported per asset class (holding's security class) and for the portfolio, on
inception, each complete year and each complete month. A year sums complete months only; if any
month in that year is incomplete the year lists `incompleteMonths` and withholds the sums.

## Allocation drift

Actual allocation at each complete month end is `group_allocation` over that month's holdings-mode
`month_end_position` rows (asset class labels, ratios at ten decimal places, the existing
contract). Incomplete months have null ratios.

A **target** is a set of non-negative asset-class ratios that sum to exactly `1` at
`NUMERIC(20,10)`. **Inference:** take `group_allocation` ratios from the first complete month in
the stored history, then assign the rounding remainder (`1 − sum`) to the class with the largest
ratio (first label in enum order if tied) so the inferred set always sums to 1. The owner's stored
targets always win. Removing them returns to the inference. A refresh or price update never writes
or clears a stored target.

**Drift** at a complete month is `actualRatio − targetRatio` per class, as a fraction quantized to
ten decimal places, null when actual allocation is incomplete or no target exists (no complete
month yet and no owner set). Classes in the target with no actual value have actual ratio 0; a
class that appears only in actuals has target 0.

## Benchmarks

There is no index feed and no fabricated series. A **benchmark** is a security the household can
read (household-scoped or shared) that has accepted `price_snapshot` rows. The owner assigns one
security to the **portfolio** and optionally one per **asset class**. There is no inferred
benchmark: a missing assignment is `No benchmark`, never a zero-return cash proxy. An assignment is
never undone by a price refresh.

For each complete window that has a benchmark, the benchmark's period return is Modified Dietz on a
one-unit position: `BMV` and `EMV` are that security's latest accepted price on or before `t0` and
`t1` translated to base at those dates, `C = 0`. A missing price or rate at either end makes that
window's comparison unavailable (`missing_price`). **Excess** is subject Dietz minus benchmark
Dietz, eight decimal places, null when either side is unavailable.

## Owner corrections

| Object | Inference | Owner action |
| --- | --- | --- |
| Allocation target | First complete month's class ratios | Replace the set, or delete it |
| Benchmark | None | Assign or remove a security |

Corrections persist until the owner changes them.

## Coverage the owner must see

- Incomplete months, with the same status counts wealth history already stores.
- Snapshot-mode and ledger-balance counted accounts, named, excluded from holdings XIRR, Dietz,
  contribution effects and class allocation.
- Holdings whose class or account XIRR has a reason instead of a rate.
- Unrefreshed wealth history.

## Non-goals

Rebalancing advice; TWR linking; a realised-gain screen or lot-level gain after corporate actions
(project register); platform or currency allocation groupings (register; class and account XIRR
close the return half of that item); linking events to document page and row (register); same-date
valuation-snapshot correction (register); snapshot-mode accounts inside holdings XIRR; household
wealth attribution's FX split; charts beyond existing table and summary patterns; AI.

## Required test evidence

- Live: class and account XIRR match the engine on a two-holding fixture and fail closed on a
  missing price in the group; a switch whose counterpart is in the same class does not fail that
  class; a switch or platform transfer whose counterpart is outside the group is
  `incomplete_history`; Dietz for a month with a mid-month purchase agrees with the formula;
  `no_denominator` when opening and weighted contribution are zero; class contribution lines sum
  to the portfolio line; an incomplete month listed and excluded from yearly sums; inferred target
  sums to 1 after remainder assignment; owner targets override and DELETE restores inference;
  drift null on an incomplete month; class and portfolio benchmark Dietz from dated prices and
  excess; missing benchmark price withholds excess; snapshot-mode account listed as coverage only;
  member 403 on each new write; over-precise ratio refused; a failed target replace leaves the
  previous set; another household's holdings, targets and benchmarks never appear; a GET writes
  nothing.
- Web: years, incomplete month rows, class XIRR, contribution lines, drift, benchmark excess and a
  target or benchmark change render from server strings.

## Acceptance

Owner-operated on real holdings: class returns look right next to known XIRR, contribution effects
for a month with a purchase do not treat the purchase as market gain, allocation drift matches the
chosen target after at most one correction, and a named tracker used as benchmark compares without
invented prices. Record structural counts only.
