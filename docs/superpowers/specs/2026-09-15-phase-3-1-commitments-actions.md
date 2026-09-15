# Phase 3.1 — commitments and actions

Owner-approved outcome: [roadmap Phase 3.1](../../product/roadmap.html), planned at personal scale on
15 September 2026 together with [Phase 3.2](2026-09-15-phase-3-2-cash-forecast-review.md). The
[dated plan](../plans/2026-09-15-phase-3-1-commitments-actions.md) declares both branches. Workspace:
`/Users/sandjaieravi/dev/OneLedger`; every branch starts from and targets `develop`.

## Product outcome

A commitment — an owner-entered dated or recurring obligation — now carries a certainty
(confirmed or estimated), a source (entered by the owner, or planned from a card statement's due
amount) and a lifecycle over exactly one open occurrence: its next due date. The owner resolves
that occurrence by linking the ledger posting that paid or received it (in full, or as a part
payment), by marking it done without a posting, or by dismissing it with a reason. Closing an
occurrence advances the commitment to its next scheduled date, so a recurring obligation only ever
holds its next occurrence. A posting settles at most one occurrence, and a settled or dismissed
occurrence no longer moves the forecast, so actual settlement clears the obligation once.

One **Needs attention** list on Plans gathers every item that asks the owner to act: a commitment
occurrence due within seven days or overdue, a card statement with an amount due and no planned
payment, and owner-entered follow-ups. Each item has a reason, due date, linked account and one
lifecycle — open, snoozed, completed or dismissed with a reason. A commitment item's completion
and dismissal *are* its occurrence's resolution, not a second record. Overview shows the open
count and the first items. Nothing in this phase posts a transaction or changes a balance,
classification or report.

## Authority and decisions in force

- [ADR-0002](../../adr/0002-frozen-dual-currency-amounts.html),
  [ADR-0010](../../adr/0010-signed-balance-convention.html): commitment amounts are native and signed
  as a posting to their account; a payment posting must carry the same sign and currency.
- [ADR-0013](../../adr/0013-python-backend-service.html),
  [ADR-0016](../../adr/0016-revocable-owner-sessions.html): owner-only routes, explicit
  `household_id` filters (the API connects as table owner), RLS as defence in depth.
  [ADR-0015](../../adr/0015-flyway-for-schema-migrations.html): forward-only; V43 is not edited.
- [Phase 1.7 specification](2026-09-14-phase-1-7-salary-forecast.md#commitments-v43): the base this
  extends — `commitment` (V43), `routes/plans.py`, `forecast.py`, the Plans screen. Commitments stay
  owner assumptions with no `SECURITY DEFINER` write path; routes lock the household row.
- [Finance OS F07/F08](../../product/finance-os.html), scenarios P05 and P10: confirmed and
  estimated inputs, part payments, distinguishable cancelled/overdue/partly paid/fulfilled states,
  one action list, dismissal with a reason, the next occurrence independent of a dismissal.

## Data model contract

`develop` ends at V47 and SQL test `0044`. Task 1 adds V48 with `0045`; Task 2 adds V49 with `0046`.

### Commitment lifecycle (V48)

```sql
CREATE TYPE commitment_certainty AS ENUM ('confirmed', 'estimated');
CREATE TYPE commitment_resolution_kind AS ENUM ('payment', 'completed', 'dismissed');

ALTER TABLE commitment
  ADD COLUMN certainty commitment_certainty NOT NULL DEFAULT 'confirmed',
  ADD COLUMN next_due_date DATE,
  ADD CONSTRAINT uq_commitment_household_id UNIQUE (household_id, id),
  ADD CONSTRAINT commitment_next_due_in_schedule CHECK (
    next_due_date IS NULL OR (next_due_date >= first_date
                              AND (end_date IS NULL OR next_due_date <= end_date)));

CREATE TABLE commitment_resolution (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  commitment_id  UUID NOT NULL,
  due_date       DATE NOT NULL,
  kind           commitment_resolution_kind NOT NULL,
  closes         BOOLEAN NOT NULL,
  transaction_id UUID,
  reason         TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_commitment_resolution_commitment_scope FOREIGN KEY (household_id, commitment_id)
    REFERENCES commitment (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_commitment_resolution_transaction_scope FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT commitment_resolution_shape CHECK (
       (kind = 'payment'   AND transaction_id IS NOT NULL AND reason IS NULL)
    OR (kind = 'completed' AND transaction_id IS NULL AND closes AND reason IS NULL)
    OR (kind = 'dismissed' AND transaction_id IS NULL AND closes AND reason IS NOT NULL
        AND char_length(reason) BETWEEN 1 AND 200 AND reason = btrim(reason)))
);
CREATE UNIQUE INDEX uq_commitment_resolution_closing
  ON commitment_resolution (commitment_id, due_date) WHERE closes;
CREATE UNIQUE INDEX uq_commitment_resolution_transaction
  ON commitment_resolution (transaction_id) WHERE transaction_id IS NOT NULL;
CREATE INDEX idx_commitment_resolution_commitment
  ON commitment_resolution (household_id, commitment_id, due_date DESC);
```

- **Backfill.** Existing rows get `next_due_date` by the default rule below, evaluated at the
  migration's `current_date` against the commitment's account (occurrence `n` is
  `(first_date + make_interval(months => n * step))::date`, PostgreSQL's own month-end clamp, which
  equals `forecast._add_months(first_date, n * step)`), or `NULL` when no occurrence remains before
  `end_date`. This keeps every occurrence the Phase 1.7 forecast already projected; the owner can
  set a different next due date while the commitment has no resolution.
- **A commitment's lifecycle.** `archived_at` set is **cancelled**. Otherwise `next_due_date NULL`
  is **finished**; else the next occurrence is **overdue** (before `asOf`), **due** (`asOf` through
  `asOf + 7`) or **upcoming**. Payments recorded against the open occurrence make it **partly
  paid** alongside that state.
- `assert_commitment_payment()` runs before insert when `kind = 'payment'` and raises
  `commitment_payment_is_eligible` unless the transaction is a row of `v_transaction_posting` in the
  same household that is not `is_excluded`, whose `txn_type` is neither `opening_balance` nor
  `adjustment`, on the commitment's account, in its currency, with `sign(amount)` equal to
  `sign(commitment.amount)`, and not referenced by `payslip_line_posting` (those rows are deleted
  on unlink, which a resolution's `RESTRICT` key would block). Transfer legs are eligible: a card
  payment or planned transfer is a real cash movement on the paying account.
- `preserve_commitment_resolution()` refuses every `UPDATE`. `DELETE` is the undo path and is
  permitted only through the route rule below. Both trigger functions are revoked from `PUBLIC`.
- `commitment_resolution` enables RLS with `household_isolation FOR ALL TO authenticated` on
  `household_id = current_household_id()`.

### Card-statement source and actions (V49)

```sql
ALTER TABLE card_statement
  ADD CONSTRAINT uq_card_statement_household_id UNIQUE (household_id, id);

ALTER TABLE commitment
  ADD COLUMN source_card_statement_id UUID,
  ADD CONSTRAINT fk_commitment_card_statement_scope
    FOREIGN KEY (household_id, source_card_statement_id)
    REFERENCES card_statement (household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT commitment_card_source_is_one_payment CHECK (
    source_card_statement_id IS NULL OR (cadence = 'once' AND amount < 0));
CREATE UNIQUE INDEX uq_commitment_open_card_statement ON commitment (source_card_statement_id)
  WHERE source_card_statement_id IS NOT NULL AND archived_at IS NULL;

CREATE TYPE action_item_state AS ENUM ('open', 'completed', 'dismissed');

CREATE TABLE action_item (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL REFERENCES household(id),
  idempotency_key UUID NOT NULL,
  title           TEXT NOT NULL,
  due_date        DATE,
  account_id      UUID,
  state           action_item_state NOT NULL DEFAULT 'open',
  dismiss_reason  TEXT,
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_action_item_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_action_item_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT action_item_title_is_bounded CHECK (
    char_length(title) BETWEEN 1 AND 120 AND title = btrim(title)),
  CONSTRAINT action_item_resolution_agrees CHECK ((state = 'open') = (resolved_at IS NULL)),
  CONSTRAINT action_item_dismissal_has_reason CHECK (
    (state = 'dismissed') = (dismiss_reason IS NOT NULL)
    AND (dismiss_reason IS NULL OR (char_length(dismiss_reason) BETWEEN 1 AND 200
                                    AND dismiss_reason = btrim(dismiss_reason))))
);

CREATE TABLE action_mark (
  household_id   UUID NOT NULL REFERENCES household(id),
  action_key     TEXT NOT NULL,
  snoozed_until  DATE,
  dismiss_reason TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, action_key),
  CONSTRAINT action_mark_key_shape CHECK (action_key ~
    '^(follow-up\.[0-9a-f-]{36}|commitment\.[0-9a-f-]{36}\.[0-9]{4}-[0-9]{2}-[0-9]{2}|card\.[0-9a-f-]{36})$'),
  CONSTRAINT action_mark_is_one_mark CHECK (num_nonnulls(snoozed_until, dismiss_reason) = 1),
  CONSTRAINT action_mark_dismissal_is_card CHECK (dismiss_reason IS NULL OR action_key LIKE 'card.%'),
  CONSTRAINT action_mark_reason_is_bounded CHECK (dismiss_reason IS NULL OR (
    char_length(dismiss_reason) BETWEEN 1 AND 200 AND dismiss_reason = btrim(dismiss_reason)))
);
```

Both new tables enable household RLS as above. An action mark is an owner decision about a derived
item; a stale mark (its item gone) is harmless and never listed.

## Occurrence rules

`api/oneledger/forecast.py` keeps the occurrence math and gains two pure helpers beside
`occurrence_dates`, both unit-tested:

- `is_occurrence(cadence, first, end, candidate) -> bool` — `candidate` is `first` plus `n` (monthly)
  or `12n` (annual) clamped months for some `n ≥ 0`, not after `end`; `once` only `first`.
- `next_occurrence(cadence, first, end, due) -> date | None` — the scheduled occurrence strictly
  after `due`; `None` for `once` or past `end`. Clamping always restarts from `first` (31 January →
  28/29 February → 31 March).

The default next due date for a new or rescheduled commitment is `first_date` for `once`. For a
monthly or annual commitment it is the first occurrence strictly after the account's balance date
(`current_balance_as_of::date`) when that date is before today — so occurrences the forecast already
treats as unposted stay open — and otherwise the first occurrence on or after today; never after
`end_date`. If none remains the request is 422 `this schedule has no occurrence left`.

## API boundary

Every route uses `require_owner`, locks the household row before a write, filters by the session
household and answers 404 for an unknown or foreign id. Bodies forbid extra fields; dates are
`YYYY-MM-DD`; money is the existing `_money` string; labels, titles and reasons are NFKC-normalized
and whitespace-collapsed (the `_normalize_label` rule) with the bounds above. Refusals are fixed
sentences.

### Commitments (Task 1 changes `routes/plans.py`)

- `CommitmentCreate` adds `certainty` (`"confirmed" | "estimated"`, default `"confirmed"`) and
  optional `nextDueDate`; Task 2 adds optional `sourceCardStatementId`. `CommitmentUpdate` adds
  `certainty` and optional `nextDueDate`; the source is fixed at creation.
- A given `nextDueDate` must satisfy `is_occurrence` (422 `next due date is not on this commitment's
  schedule`). An identical create replay compares the new fields as sent; an omitted `nextDueDate`
  matches any stored value.
- **Update with no resolution:** every field is editable; an omitted `nextDueDate` keeps the stored
  one when it is still on the new schedule, else takes the default rule.
- **Update with any resolution:** `accountId`, `currency`, `cadence`, `firstDate` and the sign of
  `amount` must equal the stored values and a given `nextDueDate` must equal the stored one (409 `a
  commitment with resolved occurrences keeps its account and schedule; archive it and add a new
  one`); `endDate` must be null or on or after the stored `next_due_date` when that is set, and on
  or after the latest closed due date when the commitment is finished (422 `end date must not fall
  before the next due date`).
- `GET /commitments` accepts `?asOf=` (default today) for status.
- `CommitmentRow` adds `certainty`, `nextDueDate`, `status` (`upcoming | due | overdue | finished |
  cancelled`), `paidTowardNext` (sum of payment amounts recorded against the open occurrence,
  `"0.0000"` when none or none open), `partlyPaid` (a payment exists against the open occurrence),
  `remainingNext` (null when no open occurrence; else `amount − paidTowardNext` when
  `|paidTowardNext| < |amount|`, otherwise `"0.0000"` — never the opposite sign), and Task 2 adds
  `source` (`"owner" | "card_statement"`), `sourceCardStatementId`, `sourceCardAccountName`.
  All sums are Python `Decimal` from database `NUMERIC`.

### Resolutions (Task 1 creates `routes/commitment_resolutions.py`)

| Route | Contract |
| --- | --- |
| `GET /commitments/{commitmentId}/resolutions` | `{resolutions: ResolutionRow[]}`; `dueDate` descending, then `createdAt` descending |
| `GET /commitments/{commitmentId}/payment-candidates` | `{dueDate, candidates: PaymentCandidate[]}`; read-only; at most 20; empty with `dueDate: null` when cancelled or finished |
| `POST /commitments/{commitmentId}/resolutions` | `ResolutionCreate` → `CommitmentRow`; 201, 200 identical replay |
| `DELETE /commitments/{commitmentId}/resolutions/{resolutionId}` | undo → `CommitmentRow` |

- `ResolutionCreate`: `dueDate`, `kind`, `transactionId` (payment only), `closes` (payment only,
  required; completed and dismissed always close), `reason` (dismissed only, 1–200).
- `ResolutionRow`: `id`, `dueDate`, `kind`, `closes`, `reason`, `createdAt`, `undoable` (the undo
  rule below, decided by the server), and for a payment `transaction: {transactionId, bookedDate,
  amount, description, eligible}` — `description` through `_preview_redactor(None)` — where `eligible` re-reads
  the V48 rule (a posting later split or excluded reads `false`; the owner undoes it).
- `PaymentCandidate`: `transactionId`, `bookedDate`, `amount`, `currency`, `description` (through
  `routes/layouts.py` `_preview_redactor(None)`, as payslip deposit candidates use). Candidates satisfy
  the V48 eligibility rule, are linked to no resolution, and are booked within 45 days either side
  of the open occurrence's due date; ordered by distance from that date, then booked date, then id.
- **Create order:** lock household; 404 unknown commitment; replay check (below); 409 `an archived
  commitment is fixed`; 409 `this commitment has no open occurrence` when finished; 409 `this
  occurrence is not the commitment's next open occurrence` when `dueDate ≠ next_due_date`; insert
  (eligibility failure 422 `this posting cannot settle this commitment`; a posting already used 409
  `this posting already settles a commitment`); when the row closes, set `next_due_date =
  next_occurrence(...)` in the same transaction.
- **Replay:** a payment whose `transactionId` already resolves this commitment's `dueDate` with the
  same `closes` returns 200; a completed or dismissed request whose `dueDate` already has an equal
  closing row (same kind and reason) returns 200. Any other existing closing row for that date is
  409 `this occurrence is already resolved`.
- **Undo:** 404 when the resolution is not this commitment's; 409 `an archived commitment is fixed`.
  A non-closing payment on the open occurrence is deleted. A closing row is deleted only when its
  `due_date` is the commitment's latest closed due date and no payment exists for the current
  `next_due_date` (409 `remove the payments recorded against the next occurrence first`); its
  `due_date` becomes `next_due_date` again. Anything else is 409 `only the latest resolution can be
  undone`.

### Forecast (Task 1 changes `GET /forecast`)

Phase 1.7 rule 5 gains two conditions: an occurrence applies only when it is on or after the
commitment's `next_due_date` (a finished commitment applies none), and the occurrence equal to
`next_due_date` applies `remainingNext` rather than `amount`, omitted when that is zero.
`ForecastOccurrence` adds `certainty`. Nothing else in the forecast changes in Phase 3.1.

### Card dues and actions (Task 2 creates `routes/actions.py`)

`POST /commitments` with `sourceCardStatementId`: 404 unless the statement is the household's; 422
`nothing is due on this card statement` unless `statement_balance > 0`; 422 unless `cadence` is
`once` and `amount` is negative; 422 `choose the account that pays the card` when `accountId` is the
statement's own account or not an asset; 422 `the paying account must be in the card statement's
currency`; 409 `a payment is already planned for this card statement` on
`uq_commitment_open_card_statement`. `PUT /commitments/{id}` on a card-sourced commitment re-applies
the account, currency, cadence and sign rules. The amount is the owner's (the full balance, the minimum or
anything else); the due date defaults are the ordinary rules.

| Route | Contract |
| --- | --- |
| `GET /actions` | `?asOf=` default today → `ActionsResponse`; `SET TRANSACTION READ ONLY` |
| `POST /actions/follow-ups` | `{title, dueDate?, accountId?, idempotencyKey}` → `FollowUpRow`; 201, 200 replay, 409 conflicting key |
| `POST /actions/follow-ups/{actionItemId}/complete` | → `FollowUpRow`; idempotent; a dismissed item is 409 |
| `POST /actions/follow-ups/{actionItemId}/dismiss` | `{reason}` → `FollowUpRow`; same reason replay 200; a completed item is 409 |
| `POST /actions/follow-ups/{actionItemId}/reopen` | → `FollowUpRow`; clears state, reason and `resolved_at`; idempotent |
| `PUT /actions/marks/{actionKey}` | exactly one of `{until}` (after today, within 366 days) or `{reason}` (card keys only) → 204; replaces any mark; the key's object must exist in the household (404) |
| `DELETE /actions/marks/{actionKey}` | → 204; removes a snooze or dismissal; idempotent |

`ActionsResponse`: `asOf`, `items[]` and `snoozed[]` of `ActionRow`, `resolvedFollowUps[]` (the 20
most recently resolved `FollowUpRow`s). `ActionRow`: `actionKey`, `kind` (`commitment | card_due |
follow_up`), `reason` (`commitment_overdue | commitment_due | card_payment_unplanned |
follow_up`), `title`, `dueDate`, `overdue`, `snoozedUntil`, `accountId`, `accountName`, `amount`,
`remaining`, `partlyPaid`, `currency`, `certainty`, `commitmentId`, `cardStatementId`, `actionItemId` (null where
not applicable). Items are:

1. `commitment.<id>.<nextDueDate>` for each unarchived commitment with `next_due_date ≤ asOf + 7`:
   title the label, amount the commitment amount, `remaining` its `remainingNext`.
2. `card.<statementId>` for the latest `card_statement` per active credit-card account with
   `statement_date ≤ asOf`, `statement_balance > 0`, `is_paid_in_full IS NOT TRUE` and
   `payment_due_date ≥ asOf − 7`, with no unarchived commitment sourced from it and no dismissal
   mark: title the card account name, amount `statement_balance`, due date `payment_due_date`.
3. `follow-up.<id>` for each open `action_item`: title, due date, linked account name.

A snooze mark with `snoozed_until > asOf` moves an item to `snoozed[]`. Both lists order overdue
first, then `dueDate` ascending with no date last, then title. A commitment item is completed or
dismissed only through `POST /commitments/{id}/resolutions`; a card item is resolved by planning
its payment (it then becomes a commitment item on its due date window) or by dismissal. This is
the single action lifecycle: no item has two records of its own resolution.

## Web workflows

**Task 1 — Plans › Commitments.** The form adds **Certainty** and **Next due date** (optional; the
server fills the default). Each commitment shows its certainty, status (Upcoming, Due, Overdue,
Partly paid, Finished, Cancelled), next due date, and paid toward and remaining for the open
occurrence. **Settle** opens a panel for the open occurrence: payment candidates with date,
amount and description; selecting one offers **Record full payment** and **Record part payment**;
**Mark done** and **Dismiss** (with a required reason) close the occurrence without a posting.
**History** lists resolutions with each linked posting (and "no longer eligible" when so) and
offers **Undo** only where the server marks a row `undoable`. After any write the commitments list and
forecast refetch. The forecast shows each occurrence's certainty and the assumptions list adds that
a resolved occurrence is not projected and a part-paid occurrence projects its remainder.

**Task 2 — Plans › Needs attention**, first on the page (anchor `#actions`). Each item shows its
reason as a sentence, due date (overdue highlighted), account and amount or remainder. Commitment
items offer **Settle** (the Task 1 panel), **Mark done**, **Dismiss** and **Snooze**. Card items
offer **Plan payment** — the commitment form pre-filled with a label from the card name, `once`, the
negated statement balance, the due date and the source, with the account picker limited to active
asset accounts in the statement currency — plus **Dismiss** and **Snooze**. Follow-ups offer
**Complete**, **Dismiss** and **Snooze**; **Add follow-up** takes a title, optional due date and
optional account. Snoozed items sit in a collapsed list with **Wake**; resolved follow-ups with
**Reopen**. Commitment rows show their source ("From <card> statement due <date>").
**Overview** shows a **Needs attention** panel with the open count and the first five items linking
to `/plans#actions`.

All screens render server strings with `formatMoney`/`moneyText` and do no money arithmetic;
show loading, empty and error states; leave 401/403 to the workspace; one idempotency key per
create draft, reused on retry; no financial value in a URL or browser storage.

## Failure behaviour and privacy

A refused resolution, follow-up or mark leaves every row as it was; a resolution and its
`next_due_date` change commit together. Nothing here writes `transaction`, `account` balances,
`card_statement` facts, classifications or reports. Unsupported cases (a cross-currency card
payment, settling any occurrence but the next) are refused, not approximated. Tests and fixtures
are synthetic; descriptions reach the UI only through the existing preview redactor.

## Required test evidence

- V48/V49 SQL: owner/member/foreign/anonymous isolation on `commitment_resolution`, `action_item`
  and `action_mark`; cross-household commitment, transaction, account and card-statement references
  refused; the resolution shape, closing and transaction uniqueness; every
  `commitment_payment_is_eligible` refusal (account, currency, sign, excluded, opening balance,
  payslip pair posting, non-leaf parent) and a transfer leg accepted; update refused. An upgrade
  test migrates to V47, inserts synthetic once, month-end monthly, 29 February annual and ended
  commitments on accounts with past balance dates, applies V48 and asserts each `next_due_date`.
- Unit: `is_occurrence` and `next_occurrence` across month-end and 29 February clamps and end dates.
- Live API: create/update rules with and without resolutions (including a refused sign change); payment (full and part), completed,
  dismissed, replays and every 409; `next_due_date` advancing and finishing; undo rules; a posting
  used once only; `remainingNext` exact; the forecast dropping a settled occurrence and projecting a
  part-paid remainder exactly; `GET /forecast`, candidates and `GET /actions` changing no row; card
  source refusals and the one-open-plan rule; action list membership, window edges, snooze and
  dismissal marks, follow-up lifecycle and replay; member and foreign-household refusal on every
  write.
- Web: settlement panel sends the selected transaction and `closes`; undo offered only where
  allowed; Plan payment pre-fill; action controls post the right routes and refetch.

## Acceptance

Through the web application with synthetic data: a monthly commitment settled by linking its
posting advances to next month and its forecast occurrence disappears exactly once; a part payment
leaves the exact remainder projected; dismissing an occurrence records the reason and the next
occurrence still appears; a card statement due appears as an action, **Plan payment** turns it into
one commitment item, and linking the bank-side card payment clears it; snoozed items return after
their date; a follow-up completes and reopens; the overview count matches the list. Owner checks
on the real ledger record only structural counts.

## Explicit non-goals

Recurring discovery or commitments proposed from the ledger (Phase 4.1); automatic settlement
matching; per-currency projections, lowest balance, shortfalls, card-side settlement in the
forecast and monthly review (Phase 3.2); goal, reserve, renewal, maturity and claim actions (Phases
3.3–3.4); data-quality and review-queue items inside the action list (they remain in Settings ›
Data quality and Classify; the Phase 3.2 monthly review links to them); notifications; and any
posting, payment or bank action.

## Documents the implementation must reconcile

`docs/superpowers/schema.md` (V48, V49), `docs/design/data-model.html` (entity map, commitment
paragraph, migration list), `docs/design/api-contracts.html` (changed commitment rows, four
resolution routes, seven action routes), `docs/operations/testing-and-data-quality.html` (Phase 3.1
gate), `docs/product/ui-information-architecture.html` (Plans needs-attention and settlement),
`docs/reference/glossary.html` (Commitment, Action), and `docs/superpowers/progress.md` for live
state only.
