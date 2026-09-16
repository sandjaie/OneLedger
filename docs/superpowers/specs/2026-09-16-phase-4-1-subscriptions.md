# Phase 4.1 — subscriptions

Owner direction of 16 September 2026, extending [roadmap Phase 4.1](../../product/roadmap.html).
The [dated plan](../plans/2026-09-16-phase-4-1-subscriptions.md) declares the branches. This builds
on the recurring engine delivered by the
[Phase 4.1 budgets and recurring specification](2026-09-16-phase-4-1-budgets-recurring.md); it adds
no second recurrence system.

## Outcome

Recurring payments are found automatically and each is shown either as a subscription or not. The
owner never confirms a detection. The one correction is **"not a subscription"**, which keeps the
payment as an ordinary recurring payment, and its reverse. A Subscriptions screen answers what the
owner pays for: the monthly and yearly cost, what renews soon, what each subscription has cost so
far and how its price moved, and which subscriptions depend on a given card.

## The owner's decision and what it changes

On 16 September 2026 the owner reversed the Phase 4.1 roadmap wording "discovered cadence or price
changes remain proposals until confirmed": *"it should be the other way around, I can say this is
not a subscription"*, and *"recurring payments are simply just not a subscription."* The roadmap and
master plan were amended the same day.

So discovery is opt-out, and the model is binary. There is no bill, rent, loan or EMI taxonomy: a
recurring payment either is a subscription or is not. Price changes follow the latest charge without
an accept step. The confirm and accept-price flows of the earlier specification are replaced.

Three rules survive the reversal unchanged:

- **A GET never writes.** Automatic creation happens in `POST /recurring/refresh`, which the web runs
  when the Subscriptions or Recurring screen opens and after an import commits.
- **Posted ledger rows are never rewritten.** Charges join a series through the member table, as
  before.
- **An owner correction is final.** A later refresh never flips a payment the owner marked back, and
  never re-creates a payment the owner removed.

## Recurring and subscription

A **recurring payment** is a confirmed-by-evidence series: at least three charges on one identity at
a regular cadence (weekly, monthly, quarterly, semiannual or annual). Detection is the existing
engine, with semiannual added and yearly charges now reachable.

A **subscription** is a recurring payment whose subscription flag is on. When refresh creates a
series it sets that flag deterministically — there is no AI before Phase 5:

- on when the charges' category is a subscription category (seed data maps the Subscriptions tree
  and Streaming), or when no category decides it and every charge's amount is within a narrow band
  of the others (a fixed price is what a subscription looks like);
- off when the category is a non-subscription category (seed data maps Housing, Utilities,
  Insurance, Debt service, Financial and Taxes), or the amounts vary.

The owner flips it. Only the owner's flip is recorded as the owner's; a series the owner never
touched keeps following the rule, and one the owner did touch is never changed by refresh again.

Removing a recurring payment entirely ("this is not recurring at all") stays available on the
Recurring screen for a false detection, as the existing dismissal. It is not the headline action.

## One descriptor, several subscriptions

`APPLE.COM/BILL` or a Google billing descriptor can carry several services. A series is therefore an
identity plus an amount cluster, not an identity alone:

- within one identity and currency, charges are taken in date order and join the cluster whose
  current median is nearest and within 20%;
- two clusters on one identity whose date ranges **overlap** are two subscriptions (both are being
  charged at the same time);
- two clusters whose ranges do **not** overlap are one subscription whose price changed.

A charge belongs to at most one series. When no cluster on an identity is regular enough, the whole
identity is evaluated as one group, as before, so a bill whose amount varies widely is still found.
Later charges extend the open series whose amount is nearest; when only one series exists they
extend it even after a large price change. Two services charging the same amount on the same days
cannot be told apart from a statement; the owner renames them.

## Original currency is kept

A subscription keeps its own currency and amount — `₹1,499/year` stays rupees, `$20/month` stays
dollars — taken from the latest charge's original pair (`original_amount`/`original_currency`
when the statement recorded one, otherwise `amount`/`currency`), as a magnitude. Every figure in
the base currency comes from that charge's frozen `amount_base`, never from a fresh conversion, and
totals say they are estimates built from the latest charges.

Annual and monthly equivalents are computed on the server in `Decimal`: the annual figure is the
amount times its cadence's charges per year (52, 12, 4, 2, 1, or 365 divided by a custom interval),
and the monthly figure is that annual figure divided by 12, both to 4 places half-even.

## What the owner can set

Detection cannot see some facts, so the owner sets them:

- a **name** (a series created from `APPLE.COM/BILL` starts with that label and shows **Needs a
  name** until the owner renames it; the name persists because the series persists);
- the **subscription flag**;
- a **status** of active, trial, paused or cancelled — cancelled ends the series for good, and
  paused and cancelled subscriptions leave the totals;
- a **custom cadence** in days, for an interval detection does not recognise;
- a **payment channel** such as an app store, a wallet autopay or a direct card — a list stored as
  data and seeded, never a code enumeration, because institution names are never code identifiers
  (ADR-0012);
- a **single charge marked as a subscription**, with its cadence, for a yearly plan seen once.

The paying card is not set: it is the account of the latest charge, shown masked.

## Price changes and inactivity

A price change is the latest charge's original amount differing from the previous charge's in the
same currency. The subscription follows the new amount at once and shows the change with both
amounts and dates; the price history is the series' own charges, collapsed where the amount did not
move.

A subscription whose next expected charge is overdue by more than half its cadence is shown as
overdue. It is never ended automatically.

## Non-goals

Reopening a cancelled series; recurring income, investment purchases and transfers (the engine
reads spending only, by design of `v_transaction_reportable`); rent, loan and insurance obligations
the Phase 3.1 commitments and 3.4 register already model; a catalogue of known services to pick
from for a billing descriptor (recorded in the project register); usage-based "unused subscription"
detection; merging two series; a Reports screen.

## Required test evidence

- Live: refresh creating a series with the flag set from category and from amount stability; a
  refresh never reverting an owner flip or re-creating a removed payment; two overlapping clusters on
  one descriptor becoming two series and two sequential ones becoming one series with a price change;
  a charge never joining two series; a GET creating nothing; semiannual detected.
- Live: original currency and base estimate; annual and monthly equivalents exact for every cadence
  including custom; status leaving totals; a single charge marked as a yearly subscription with its
  next expected date a year on; the payment channel and paying card; foreign-household refusals.
- Web: the Subscriptions summary, upcoming renewals, all subscriptions and the detail page render
  from server strings; "not a subscription" moves a payment to Recurring; a card shows the
  subscriptions it pays for.

## Acceptance

Owner-operated on real data: after importing statements, the Subscriptions screen lists the
subscriptions the owner recognises without any confirmation, "not a subscription" moves a
non-subscription to Recurring and it stays there after another import, a yearly subscription is
present, and a card shows what depends on it. Record structural counts only.

## Documents to reconcile

`docs/product/roadmap.html` and `docs/product/master-implementation-plan.html` (amended 16 September
2026), `docs/design/api-contracts.html`, `docs/superpowers/schema.md`,
`docs/superpowers/progress.md`, `docs/superpowers/project-register.md` (the service catalogue and
series merging).
