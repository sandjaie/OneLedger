# Phase 3.2 — cash forecast and monthly review

Owner-approved outcome: [roadmap Phase 3.2](../../product/roadmap.html), planned at personal scale
on 15 September 2026 together with [Phase 3.1](2026-09-15-phase-3-1-commitments-actions.md), on
which it depends. The [dated plan](../plans/2026-09-15-phase-3-2-cash-forecast-review.md) declares
the two branches. Workspace: `/Users/sandjaieravi/dev/OneLedger`; each branch starts from and
targets `develop`.

## Product outcome

The Plans forecast becomes a cash forecast. Each account and each currency shows its 30/60/90-day
balances, its lowest projected balance and date, and the same figures from confirmed inputs alone.
A card payment planned from a card statement is counted once: it leaves the paying account and
reduces the card's debt on the same date, so a currency's total is unchanged by it, while a
shortage shows in the paying account even when another account holds enough — with those accounts
listed as possible funding, never moved. Incomplete coverage is visible: excluded and stale
accounts, open occurrences dated inside the ledger balance that were never settled, and card
statements with no planned payment. Cancelled commitments and dismissed occurrences contribute
nothing.

A **Monthly review** on Plans walks one month: coverage gaps, transfer candidates and uncategorised
postings (each linking to where they are resolved), the month-end position against the month
before, upcoming commitments and open actions, and adding a follow-up. The owner marks the month's
review complete and still sees the actions that remain.

## Authority and decisions in force

- Everything in force for [Phase
  3.1](2026-09-15-phase-3-1-commitments-actions.md#authority-and-decisions-in-force).
- [Master plan boundary](../../product/master-implementation-plan.html): projections are never
  postings or bank-confirmed cash; every result shows evidence dates, incomplete coverage and
  owner-controlled assumptions. Balances stay in account currency; no rate is read.
- [Finance OS F07/F08](../../product/finance-os.html), scenarios P05, P06, P10.
- Existing reads reused, not copied: `routes/coverage.py` `coverage_rows()`, `routes/transfers.py`
  candidate scoring, `routes/wealth_history.py` month totals, `routes/actions.py` from Phase 3.1.

## Data model contract

Phase 3.1 ends at V49 and SQL test `0046`; this phase adds V50 with `0047`.

```sql
CREATE TABLE monthly_review (
  household_id UUID NOT NULL REFERENCES household(id),
  review_month DATE NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, review_month),
  CONSTRAINT monthly_review_month_is_first_day CHECK (
    review_month = date_trunc('month', review_month)::date)
);
```

Household RLS as for Phase 3.1 tables. No other schema change: the forecast is computed on read.

## Forecast computation (`GET /forecast`)

Phase 1.7 rules 1–7 and the Phase 3.1 next-occurrence rule stand. Additions, all Python `Decimal`
at four places in `forecast.py`'s pure functions (unit-tested), with `routes/plans.py` supplying
facts:

1. **Card-side settlement.** An applied occurrence of a commitment with `source_card_statement_id`
   adds a mirror occurrence — the negated applied amount, same date, certainty and commitment id,
   `kind: "card_settlement"` — to the statement's card account when that account is included, in
   the same currency, and the date is after the card account's own `balanceDate` (a payment already
   in the card's imported balance is not reduced again). Otherwise the commitment is also listed in
   `excludedCommitments` with reason `card_settlement_not_projected` while its paying-side
   occurrence still applies.
2. **Unapplied open occurrences.** Occurrences on or after `next_due_date` and on or before the
   account's `balanceDate` are not applied (they may already be in the balance) and are listed per
   account in `unappliedOccurrences` — at most 12 per commitment — so the owner settles or
   dismisses them.
3. **Confirmed balances.** Each point adds `confirmedBalance`: the start balance plus confirmed
   occurrences only.
4. **Lowest balance.** Over the start balance at `balanceDate` and the running balance after each
   applied occurrence date through `asOf + 90`, the minimum and its earliest date: `lowest:
   {balance, date}` and `lowestConfirmed: {balance, date}` (the same over confirmed occurrences).
5. **Shortfall.** An asset account whose `lowest.balance < 0` has `shortfall: {amount, date,
   fundingAccounts[]}` with `amount = −lowest.balance` and `date = lowest.date`; `fundingAccounts`
   are the other included liquid asset accounts in the same currency whose own `lowest.balance ≥
   amount`, as `{accountId, accountName, lowestBalance}` in account order. Liabilities have none.
   Nothing is proposed as a transfer or posted.
6. **Coverage.** Each account adds `balanceAgeDays` (`asOf − balanceDate`) and `coverageGapMonths`:
   the count of `missing` or `partial` cells in its `coverage_rows()` row for the three calendar
   months ending with `asOf`'s month (null when the account has no row).
7. **Per currency.** `currencies[]`, one per currency with at least one member, where members are
   included accounts that are active liquid assets (`account_class = 'asset' AND is_liquid`) or
   credit cards (`account_type = 'credit_card'`): `{currency, accountIds, startBalance, points
   [{days, date, balance, confirmedBalance}], lowest, lowestConfirmed, excludedAccountIds,
   staleAccountIds}` — sums of member start balances and occurrences (mirrors included); the
   currency's start is dated `asOf`, occurrences dated on or before `asOf` count at `asOf`, and the
   lowest is taken over that start and the union of later member occurrence dates;
   `excludedAccountIds` are excluded accounts that would otherwise be members; `staleAccountIds` are
   members with `balanceAgeDays > 35`. Currencies are ordered by code; no conversion is made.
8. **Unplanned card dues.** `unplannedCardDues[]` lists every card statement that meets the Phase
   3.1 `card_payment_unplanned` rule regardless of snooze or dismissal marks (a snoozed or dismissed
   due is still not projected) and has `paymentDueDate ≤ asOf + 90`, as `{cardStatementId,
   accountId, accountName, currency, statementBalance, paymentDueDate}`. The rule is one shared
   query in `routes/actions.py` with a flag for marks, not a second copy.
9. `ForecastOccurrence` adds `kind` (`"commitment" | "card_settlement"`). The route stays
   `SET TRANSACTION READ ONLY` and writes nothing.

## Monthly review API (`routes/monthly_review.py`)

| Route | Contract |
| --- | --- |
| `GET /monthly-review` | `?month=YYYY-MM`, default the calendar month before today; a month after the current month is 422 → `MonthlyReviewResponse`; read-only |
| `PUT /monthly-review/{month}/completion` | → `MonthlyReviewResponse`; 201 first time, 200 when already complete (keeps `completedAt`); a future month is 422 |
| `DELETE /monthly-review/{month}/completion` | → 204; idempotent |

`MonthlyReviewResponse`: `month`, `completedAt`, and `steps`:

- `coverage: {gapCells, rowsWithGaps}` — `missing` or `partial` cells for that month across
  `coverage_rows(cur, household, asOf)` with `asOf` the month's last day capped at today.
- `transfers: {candidateCount}` — the `GET /transfers/candidates` total for `dateFrom`/`dateTo` the
  month's bounds, from its shared candidate computation (extract a helper; no second scorer).
- `classification: {unclassifiedCount}` — `v_transaction_reportable` rows booked in the month with
  `classified_by = 'unclassified'`, the Transactions `classifiedBy=unclassified` predicate.
- `position: {monthEnd, totalBase, complete, previousMonthEnd, previousTotalBase, previousComplete,
  changeBase, baseCurrency}` — the month's and prior month's stored wealth-history totals read
  through the query `GET /wealth-history` uses (extract a helper); nulls when not stored.
  `changeBase` is the server's `totalBase − previousTotalBase` when both months are complete, else
  null; the browser computes no difference.
- `commitments: {dueNext30Days, overdue}` — unarchived commitments with `next_due_date` in `[today,
  today + 30]`, and before today.
- `actions: {open, snoozed}` — counts of `GET /actions` `items` and `snoozed` at today, from its
  shared list computation.

This phase adds `previousMonthReview: {month, completedAt}` to `GET /actions` — one primary-key read
— so Overview shows the review state without running the review's counts.

## Web workflows

**Plans › Forecast.** A currency summary first: per currency the 30/60/90-day totals, lowest and
date, confirmed-only lowest, member count, and which accounts are excluded or stale (named). Per
account the existing points plus confirmed balances, the lowest balance and date, a **Shortfall**
callout on an asset account with the amount, date and "could be funded from" accounts, stale and
coverage-gap badges, card-settlement occurrences labelled as such, and **Not projected** occurrences
with **Settle**. **Card statements with no planned payment** lists `unplannedCardDues` with **Plan
payment**. The assumptions list adds: confirmed and estimated inputs are shown separately; a planned
card payment reduces the card balance on the same date; an unplanned card due, an unsettled
occurrence inside the balance date, and excluded accounts are not projected; funding accounts are
suggestions and nothing moves money; currency totals cover liquid asset accounts and credit cards.

**Plans › Monthly review** (anchor `#review`). A month picker (default last month) and six steps,
each with its count and a link: **Coverage** (`/coverage`), **Transfers** and **Categories**
(`/transactions`, `/review`), **Position** (month-end total, previous, server change, or "not yet
refreshed" linking `/wealth`), **Commitments** (`#actions`), **Follow-up** (the Phase 3.1 add form
inline). **Mark review complete** / **Reopen review**; once complete it shows the completion date
and the open action count. Overview's Needs attention panel states whether last month's review is
complete from `previousMonthReview`, linking `/plans#review`.

No money arithmetic in TypeScript; server strings only; loading, empty and error states.

## Failure behaviour and privacy

The forecast and review reads write nothing; an account, commitment or card due that cannot be
projected is listed with its reason rather than projected as zero or converted. Funding accounts
are informational. Fixtures are synthetic; no description or identifier enters the review response.

## Required test evidence

- Unit: lowest and confirmed-lowest (ties take the earliest date, start balance can be lowest),
  mirror occurrences, per-currency sums across members, shortfall and funding selection, the 12-cap
  on unapplied occurrences.
- Live API: P05 — a card-statement commitment moves the paying account down and the card up by the
  same amount on the same date, the currency total unchanged by it, linking the payment (Phase
  3.1) removes both, and a card account whose balance date is on or after the payment date gets no
  mirror; P06 — a paying account with 1,000 and a 1,500 commitment before a salary
  commitment shows a 500 shortfall while a second same-currency account with enough is listed as
  funding and unchanged; archived commitments and dismissed occurrences contribute nothing;
  unapplied occurrences, stale and gap counts, excluded members and unplanned card dues; the card
  account excluded path; `GET /forecast` and `GET /monthly-review` change no row.
- V50 SQL: owner/member/foreign/anonymous isolation and the first-day check.
- Live API: monthly review counts against a synthetic month (one coverage gap, one transfer pair,
  one unclassified row, two stored month totals), completion idempotency, reopen, future-month
  refusal, member and foreign refusal.
- Web: shortfall and funding render; complete/reopen posts and refetches.

## Acceptance

With synthetic data through the web application: P05 and P06 hold as above; each currency and
account shows its lowest balance and date and confirmed-only figures; stale, excluded and unplanned
inputs are named; the owner completes a monthly review, sees the remaining actions, and reopens it.
Owner checks on the real ledger record only structural counts.

## Explicit non-goals

Cross-currency consolidation, planned remittances with funding and receipt dates (a later owner
decision; model them as two commitments), daily balance series or charts, reservations and goals
(Phase 3.3), budgets and recurring discovery (Phase 4.1), interest, growth or scenarios (Phase 5.5),
review-time notes, and any posting or funding transfer.

## Documents the implementation must reconcile

`docs/superpowers/schema.md` (V50), `docs/design/data-model.html` (monthly review, migration list),
`docs/design/api-contracts.html` (`GET /forecast` response, three review routes),
`docs/operations/testing-and-data-quality.html` (Phase 3.2 gate),
`docs/product/ui-information-architecture.html` (Planning view and monthly workflow), and
`docs/superpowers/progress.md` for live state only.
