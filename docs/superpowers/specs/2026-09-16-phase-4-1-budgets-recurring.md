# Phase 4.1 — budgets, spending analysis and recurring discovery

Owner-approved outcome: [roadmap Phase 4.1](../../product/roadmap.html), planned at personal scale on
16 September 2026. The [dated plan](../plans/2026-09-16-phase-4-1-budgets-recurring.md) declares the
branches.

## Outcome

Spending is legible by category and bucket, not only as one monthly total. A budget can be set per
category or per bucket, carried forward when the owner wants it, and read against actual spending
with an exact variance. Repeated payments are discovered from evidence already in the ledger and
stay proposals until the owner confirms them; a changed price or a series that stopped arriving is
surfaced the same way.

## Authority

- [Roadmap Phase 4.1](../../product/roadmap.html): "Budget versus actual supports category, bucket,
  rollover, and variance; discovered cadence or price changes remain proposals until confirmed."
- [Master plan](../../product/master-implementation-plan.html#phase-4-delivery): budgets, rollover
  and variance plus owner-confirmed recurring discovery, price changes, and inactive-subscription
  candidates.
- ADR-0002 (frozen dual-currency amounts), ADR-0008 (bucket-on-category), ADR-0012 (no name as an
  identifier), ADR-0013 (owner authorization boundary), ADR-0015 (forward-only migrations).
- [Testing and data quality](../../operations/testing-and-data-quality.html): reports read the
  approved reporting views.

## What spending means here

Every aggregate reads `v_transaction_reportable`, so transfers, card payments, investment legs,
opening balances and excluded rows are already out. Income is `txn_type = 'income'`; spending is
every other reportable row with its sign turned, so a refund lowers spending rather than becoming
income. That is the rule `GET /dashboard` already states, and this phase must not invent a second
one.

Amounts stay frozen: aggregates sum `amount_base` and `amount_secondary` as stored and never
multiply a flow by a rate. Variance, rollover and ratios are computed in `Decimal` on the server and
cross the wire as strings.

## Effective category and bucket

A household may re-bucket a seed category through `category_override`, so every category-aware read
must resolve `COALESCE(o.bucket, c.bucket)` and `COALESCE(o.is_active, c.is_active)`. Three copies of
that merge already exist (`routes/categories.py`, `routes/transactions.py`, `resources.py`). This
phase adds one shared SQL fragment and uses it for all new reads; consolidating the three existing
copies is recorded in the project register rather than done here.

Spending with no category is reported as its own row, never folded into a bucket.

## Budgets are owner assumptions, not postings

A budget line is an assumption in the household base currency. It posts nothing, changes no
transaction, and never becomes income or spending. A line is scoped to exactly one of a category or
a bucket, for one month, so a category line and a bucket line cannot silently double-count the same
row: a bucket line's actual excludes categories that carry their own line that month, and the
response states that exclusion.

Rollover is a per-line choice. When it is on, the unspent or overspent remainder of the previous
month's same line carries into this month's available amount. The carried figure is computed on
read from that month's own budget and actual, never stored, so editing an old month cannot leave a
stale carry behind. The chain is bounded: carry looks back at most twelve months and the response
says where it stopped.

Variance is `budget + carried - actual`, positive when under budget.

## Discovery proposes, the owner decides

> **Superseded on 16 September 2026.** The owner reversed this section's model: discovery is now
> opt-out, confirm and accept-price are removed, and "not a subscription" is the owner's one
> correction. See the [subscriptions specification](2026-09-16-phase-4-1-subscriptions.md).


Recurring discovery reads the ledger and proposes; it writes nothing on its own and it does not
touch the classification cascade. `ingest/classify.py` keeps no `recurring_series` step: a step that
cannot produce a confident answer is worse than an absent one, and its absence is asserted by a
test.

A candidate is a group of reportable spending rows that share a counterparty — `merchant_id` when
classification resolved one, otherwise the normalized description — with at least three occurrences
whose gaps fit a known cadence (weekly, monthly, quarterly or annual, each with a tolerance). The
proposal states the cadence, the occurrence count, the first and last dates, the last amount, and
the evidence rows it was derived from. Nothing is inferred beyond what those rows show: an
irregular group is simply not proposed.

Confirming a candidate writes a `recurring_series` row and records its member transactions in a
join table. The ledger rows themselves are not rewritten — `transaction` is immutable after
posting, and `transaction.recurring_series_id` stays the unused column it has always been.
Dismissing a candidate records the dismissal so it does not return. Later occurrences join a series
through an explicit refresh, never through a read: a GET in this codebase reads what the last
refresh stored, and this phase keeps that promise.

For a confirmed series:

- **A price change** is the latest occurrence differing from the series' expected amount. It is
  shown with both exact amounts and the dates they were observed, and it updates the expected amount
  only when the owner accepts it.
- **An inactive candidate** is a series whose next occurrence is overdue by more than half its
  cadence. It is offered for ending, never ended automatically.

A series is the owner's record of an obligation they have noticed. It does not create a commitment,
a forecast entry or a payment; Phase 3.1 already owns commitments, and linking the two is out of
scope here.

## Export

Spending can be exported as CSV: the filtered reportable rows with their date, account, description,
category, bucket, exact amount, currency and base amount. The export is the owner's own data in
their own file, bounded to a stated row cap, and it is generated from the same reportable view the
screens read, so a figure in the file always matches a figure on screen.

## Required test evidence

- Live: category and bucket totals against synthetic rows, including a refund lowering spending, an
  uncategorised row reported separately, an overridden bucket resolving to the override, and a
  foreign household seeing none of it.
- Live: budget variance with and without rollover, a bucket line whose actual excludes a
  category-lined child, the twelve-month carry bound, and member refusals.
- Live: a regular group proposed with its cadence, an irregular group not proposed, confirmation
  writing a series and its members, dismissal suppressing a candidate, a price change offered and
  accepted, an overdue series offered for ending.
- Web: the breakdown and chart render from server strings, drill-through reaches the filtered
  transactions, a budget line is set and its variance shown, a candidate is confirmed and dismissed.

## Acceptance

Owner-operated on real data: this month's spending is readable by category and bucket with a chart;
a budget for the two or three categories that matter shows variance; and the repeated payments the
owner recognizes appear as candidates, with at least one confirmed and one dismissed. Record
structural counts only.

## Non-goals

Forecasting budgets forward (Phase 3.2 owns projections); linking a series to a commitment or an
action; automatic ending or repricing of a series; merchant-level spending analysis beyond what a
candidate needs; investment or salary analysis (Phases 4.2 and 4.3).

## Documents to reconcile

`docs/design/api-contracts.html` (spending, budgets, recurring routes), `docs/superpowers/schema.md`
(§0017 and §0018 carry superseded budget and recurring shapes and mandate a foreign key on
`transaction.recurring_series_id` that this phase deliberately does not add),
`docs/superpowers/progress.md`, `docs/superpowers/project-register.md` (the three existing
effective-category merges).
