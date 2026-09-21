# Phase 4.4 — FX and remittance analysis

Roadmap outcome ([Phase 4.4](../../product/roadmap.html)): provider cost and effective-rate
history use evidenced amounts and dates; internal transfers remain outside spending. The
[master plan](../../product/master-implementation-plan.html#phase-4-delivery) lists evidenced
provider costs, effective-rate history and remittance comparison. This adds no second transfer or
FX model: every figure reads the `transfer` row's own FX fields, computed once at confirmation by
`routes/transfers.py::FX_COST` per
[`multi-currency-and-fx.html` §5](../../design/multi-currency-and-fx.html#5-actual-rates-beat-reference-rates)
and
[`transfers-and-reconciliation.html` §5](../../design/transfers-and-reconciliation.html#5-remittance-cost-analysis),
which this phase builds on rather than replaces. Plan:
[Phase 4.4 plan](../plans/2026-09-21-phase-4-4-fx-remittance-analysis.md).

## Outcome

The owner can name the provider that carried a confirmed remittance, record an explicit fee a
statement never itemises, and see — for each currency corridor they remit through — which provider
is cheapest, what each has cost this year and in total, and what the cheapest channel would have
cost had every remittance gone through it. Nothing is inferred: a remittance with no recorded
provider is shown as unattributed rather than guessed, and a cost that used a carried-forward FX
rate is marked approximate rather than presented as exact (Invariant I7).

## Evidence, not inference of money

- Every figure derives from a **confirmed** `transfer` row (`status = 'confirmed'`,
  `transfer_kind = 'remittance'`): its own `source_amount`, `destination_amount`, `implied_rate`,
  `market_rate`, `fx_spread_cost_source`, `explicit_fee_amount` and `total_cost_source`. Nothing
  here re-derives a rate or a cost from scratch — `FX_COST` (already delivered) owns that
  computation at confirmation time; this phase only aggregates and lets the owner correct the two
  fields no statement states.
- A remittance with no recorded provider is **unattributed** — its money counts in a corridor's
  totals but never in a per-provider comparison, because there is nothing to compare it against.
- A remittance whose cost is not yet known (no FX spread because it happens to be same-currency,
  and no fee recorded) contributes to a corridor's `Sent` total but not to `Fees`, `Spread`,
  `Total` or `Effective %` — those sum only the transfers that have a cost, and the response says
  how many of the bucket's transfers that is, so a partial total is never presented as complete.
- A cost computed from a carried-forward market rate (the `costIsApproximate` flag `GET /transfers`
  already exposes) counts in every sum but is also counted separately, so the owner can see when a
  year's total rests partly on a non-publication-day rate.
- Analysis is in the household base currency only where the design already commits to it — the
  transfer's own `source_amount`, `destination_amount` and cost fields are in the transfer's own
  currencies, mirroring `GET /transfers` today. `GET /transfers/analysis` never writes.

## Currency corridors

A remittance's rate, spread and effective cost only mean something next to another remittance in
the **same** currency pair. Every aggregate groups first by **corridor** — `(source_currency,
destination_currency)` — before grouping by provider or year. AED→INR and AED→USD remittances are
never summed or compared against each other. Corridors are listed by lifetime `Sent` descending,
then by currency codes.

## Per-provider, per-year cost comparison

For a household-scoped corridor and calendar year (`transfer_date`), over its confirmed remittance
transfers:

- **Named providers** — grouped by the transfer's own `provider` string, exactly as the owner
  entered it (no case-folding or fuzzy merging: two different spellings are two different rows
  until the owner corrects one, the same way an uncorrected category stays uncorrected).
- **Unattributed** — `provider IS NULL`, reported once per corridor-year, never split further.

For each group:

| Field | Definition |
| --- | --- |
| `transferCount` | Every confirmed remittance in the group. |
| `sentSource` | Sum of `source_amount` over every transfer in the group. |
| `costedSentSource` | Sum of `source_amount` over transfers whose `total_cost_source` is not null. |
| `spreadSource` | Sum of the stored `fx_spread_cost_source` over the costed subset. Null when `costedSentSource` is 0. |
| `totalCostSource` | Sum of the stored `total_cost_source` over the costed subset. Null when `costedSentSource` is 0. |
| `feesSource` | Sum of `total_cost_source − COALESCE(fx_spread_cost_source, 0)` over the costed subset — the fee **as it was already translated and stored**, never re-translated on read. `feesSource + spreadSource = totalCostSource` therefore holds by construction, so the row the owner reads always adds up. Null when `costedSentSource` is 0. |
| `effectiveCostPct` | `totalCostSource ÷ costedSentSource × 100`, 4 dp half-even. Null when `costedSentSource` is 0 — never a percentage of an unknown whole. |
| `avgRate` | `sum(destination_amount) ÷ sum(source_amount)` over transfers whose `implied_rate` is not null, 10 dp half-up (the rate convention `money.py::_fixed` applies to the stored rates this figure sits beside). Null when none. A weighted average, not a mean of per-transfer rates, so one large remittance is not drowned out by several small ones. |
| `approximateCount` | Transfers in the costed subset whose market rate was carried forward. |

**A fee is translated once, when it is recorded.** A fee entered in a currency other than the
transfer's source currency is translated at correction time with the same `fx_rate` lookup
`FX_COST` uses, for the transfer's own date, and only its effect on `total_cost_source` is stored;
a date with no rate for that pair refuses the correction (§ Owner corrections) rather than
translating with a guess. Reading never re-translates: an `fx_rate` row later corrected or
backfilled cannot make a stored cost disagree with the fee and spread it is the sum of (ADR-0002 —
amounts are frozen once, not converted on read).

**A favourable rate is reported, never turned into a saving.** `FX_COST` computes the spread as
`(market − implied) ÷ market`, which is negative when the achieved rate beat the mid-market rate
for that date — routine when `market_rate` was carried forward. Such a transfer keeps its negative
cost in its own group's `spreadSource`, `totalCostSource` and `effectiveCostPct`, because that is
what the evidence says. It is the cheapest-channel counterfactual below, not the reporting, that
refuses to build an opportunity on it.

## The corridor-year total row

The design's worked table ends in a Total row — Sent, Fees, Spread, Total, Effective % — so each
corridor-year also reports `totalSentSource` (every transfer, named and unattributed),
`totalCostedSentSource`, `totalFeesSource`, `totalSpreadSource`, `totalCostSource` (each over that
corridor-year's whole costed subset) and `totalEffectiveCostPct`
(`totalCostSource ÷ totalCostedSentSource × 100`, null when nothing is costed). Every one is
computed in Postgres or Python and sent as a string: TypeScript formats money and does no
arithmetic on it, so a total the screen needs is a total the server states
(`scripts/check-identifiers.py` Rule 2).

## The cheapest-channel comparison

For a corridor-year with at least two **named** providers that each have a `costedSentSource`
greater than zero:

- `cheapestProvider` is the named provider with the lowest `effectiveCostPct` (ties broken by
  provider name).
- `potentialSavingsSource` = `(sum of totalCostSource over every named provider)` minus
  `(sum of costedSentSource over every named provider) × max(0, cheapestProvider.effectiveCostPct)
  ÷ 100`, then **bounded to `[0, sum of totalCostSource over every named provider]`**. This is
  "what it actually cost, minus what it would have cost routed entirely through the cheapest
  channel" — the design's own worked example (`transfers-and-reconciliation.html` §5).
- The two bounds exist because a stated saving must never exceed money actually spent, and a
  favourable spread must never be projected onto other transfers as if it were repeatable. Clamping
  the cheapest provider's rate at zero says "at best this would have been free", not "at best it
  would have paid you"; capping the result at the cost incurred says the owner cannot be told they
  could have saved more than they spent.

Fewer than two costed named providers in a corridor-year yields no comparison (`cheapestProvider`
and `potentialSavingsSource` both null) rather than a comparison against a set of one. The
unattributed bucket never enters this comparison: money with no recorded channel cannot be
attributed a cheaper one.

## Owner corrections

| Field | Inference | Owner action |
| --- | --- | --- |
| `provider` | None — a confirmed remittance starts unattributed | Set or clear the provider name on that transfer |
| `explicitFeeAmount` / `explicitFeeCurrency` | None — no statement this ledger reads states a fee | Set or clear the fee; `totalCostSource` recomputes from the stored FX spread plus this fee |

Corrections apply to one confirmed `transfer_kind = 'remittance'` row and persist until the owner
changes them again. A correction is **last-write-wins** and states no expected prior state: the
same correction sent twice leaves the same stored figures, because `total_cost_source` is
recomputed from the stored spread and the submitted fee rather than adjusted by it. Clearing the
fee returns `totalCostSource` to the FX-spread-only figure
`FX_COST` computed at confirmation (null when that, too, is null — a same-currency remittance with
no fee has nothing to cost). A correction never touches `implied_rate`, `market_rate` or
`fx_spread_cost_source`: those remain owned by confirmation-time `FX_COST`, exactly as before this
phase.

## Coverage the owner must see

- The unattributed total per corridor-year, so unlabelled remittances are found rather than
  silently excluded from the comparison.
- `approximateCount` wherever it is non-zero, so a total resting partly on a carried-forward rate
  is never read as exact.
- A corridor-year with no cheapest-channel comparison states why (fewer than two costed named
  providers) rather than omitting the line silently.

## Non-goals

A remittance provider catalogue or seed table (unlike `payment_channel`, `provider` stays the
transfer's own free-text field — no institution name is ever enumerated in code, ADR-0012, and
adding a lookup table is not required to satisfy that rule here); cross-currency `investment_funding`
or `card_payment` cost tracking (the design's remittance-cost-analysis section, and this phase,
cover `transfer_kind = 'remittance'` only); a market-versus-parallel-rate feed beyond the household's
own `fx_rate` table; predictive "switch providers and save" advice beyond the one stated
opportunity-cost figure; charts beyond the existing table and summary patterns; AI.

## Required test evidence

- Live: a corridor groups only its own currency pair; a named provider's sums match a hand-computed
  fixture across two transfers, one of them using a carried-forward rate (`approximateCount = 1`);
  `feesSource + spreadSource = totalCostSource` on every group of a mixed fixture; an uncosted
  same-currency remittance counts in `sentSource` but not `costedSentSource`/`feesSource`
  /`spreadSource`/`totalCostSource`/`effectiveCostPct`; the unattributed bucket is reported and
  excluded from the cheapest-channel comparison; a single named provider yields no comparison; two
  named providers yield the documented `cheapestProvider` and a `potentialSavingsSource` matching
  the worked formula; a provider whose stored cost is negative keeps that figure in its own row but
  yields neither a saving above the cost incurred nor one projected from a negative rate; the
  corridor-year total row's own fields reconcile to its groups; `avgRate` is the weighted average,
  proven against a fixture where a simple mean would differ; a fee in a third currency is
  translated at the transfer's own date and refused when no rate exists for that date; an `fx_rate`
  row changed after a correction leaves the stored cost and the response's reconciliation
  untouched; `GET /transfers/analysis` writes nothing.
- Live, the financial write floor for `PATCH` (`docs/development-cycle.md`): owner success, and
  refusal for a session that is not the household's owner and for another household's transfer;
  refusal of a fee with excess precision before storage, and an exact round trip of one stored at
  full precision; an identical retry returning the original result, with `total_cost_source`
  recomputed rather than accumulated; no partially updated row after a late refusal (a bad fee
  currency leaves provider, fee and cost as they were); `implied_rate`, `market_rate`,
  `fx_spread_pct` and `fx_spread_cost_source` unchanged by any correction; a corrected remittance's
  two legs still absent from spending (the roadmap's second Phase 4.4 criterion). No stale-state or
  concurrency case applies: the contract exposes no `expectedState`.
- Web: a corridor's year table, the unattributed row, the cheapest-channel line (present and
  absent), and a provider/fee correction made from the transfer panel and reflected on reload, all
  from server strings.

## Acceptance

Owner-operated on real remittances: the corridor the owner actually uses lists every provider they
have sent through that year with a cost that reconciles to the underlying transfers, the cheapest
one is identified once two are recorded, and correcting a provider name or entering a fee updates
the comparison on the next load without the owner re-deriving anything by hand.
