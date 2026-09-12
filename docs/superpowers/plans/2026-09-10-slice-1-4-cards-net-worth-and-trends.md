# Slice 1.4 — cards, net worth and trends

> **Historical numbering:** This delivered plan retains the identifier used at review time. Use
> the [canonical crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)
> for current Phase N.M identifiers.

Execution map for Phase 1 slice 1.4, written against the tree at `6c0a853` on 10 September 2026.
The phase delivery plan
([current crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)) owns the historical outcome mapping and acceptance;
this file owns how it is executed and is what implementers are dispatched from.

## What the survey found

Slice 1.3 found a schema that anticipated its engines. This slice found **less than that: no
card cycle entity, no reporting reader of any kind, and an Overview screen still labelled
"Phase 0 overview"** — but two useful facts already true.

| Capability | State | Where |
|---|---|---|
| A card statement is already recognised as one | **Exists** | `pipeline._document_type` derives `document.document_type = 'card_statement'` from `account.account_type = 'credit_card'` — a stored fact, never a fingerprint heuristic, which is what ADR-0012 requires |
| The card's **total payment due** is already captured | **Exists** | `db/seeds/card-layout.json`'s `closingBalancePattern` captures the fifth of the six run-together summary figures, which `db/seeds/README.md` records as the total payment due; it lands in `import_batch.declared_closing_balance` and is what I8 reconciles against |
| `transaction.card_statement_id` | **Exists as a bare `UUID`** | V10; `schema.md` `0018` reserves its FK for the migration that creates the target |
| `account.credit_limit`, `statement_day`, `payment_due_day` | **Exists** | V3; `card_has_limit` makes the limit mandatory on a card account. These are the *account's* standing values, not the statement's at statement date, which is what utilisation needs |
| Signed balances, `account.current_balance` maintained by trigger | **Exists** | V6 and V10's `apply_transaction_to_balance`; ADR-0010's "net worth is one SUM" is already true of the column |
| `_frozen()` — a rate pair for a date, carried-forward aware, refusing a missing rate with the cure named | **Exists** | `api/oneledger/money.py`; written for freezing a flow, and exactly the lookup a balance translation needs |
| `card_statement` table | **Missing** | specified in `schema.md` `0011` |
| Minimum due, due date, statement date, credit limit at statement date | **Not captured** | `db/seeds/card-layout.json` has `metadataPatterns: null`; `RESERVED_METADATA_KEYS` is `{account_number, period_start, period_end}` and nothing else is typed |
| Any reporting query | **Missing** | `GET /transactions` reads the base table and says so — it is the one deliberate non-report reader. **Nothing in the tree reads `v_transaction_reportable`** except `db/tests/0009` |
| `GET /dashboard`, `GET /networth` | **Missing** | not in the route surface |
| The Overview screen | **A Phase 0 placeholder** | `web/app/workspace.tsx` renders "Ledger import and financial views arrive in a later phase" and a formatting preview |
| The two reporting **static checks** `CLAUDE.md` and `testing-and-data-quality.html` §2 both say exist | **Missing** | `scripts/check-identifiers.py` implements the ADR-0012 identifier rule and the TypeScript money rule, and nothing else. There is no check that a report reads `v_transaction_reportable`, and none that a query does not multiply an amount by a rate |

That last row is the finding that shapes the slice. Two documents state as fact that those
checks fail the build, and they do not exist — which has cost nothing so far only because this
slice writes the **first** reporting reader in the repository. Task 4b1 builds them before 4b2
writes the code they guard, so the dashboard is born compliant rather than audited afterwards.

**Deviation recorded: six branches rather than two.** The phase plan's outcome and acceptance
are unchanged.

## 1.4a depends on layout data this repository does not have

`db/seeds/card-layout.json` sets `metadataPatterns: null`, so the statement date, the payment
due date, the minimum due and the credit limit at statement date **are not extracted today**.
Three things follow.

**Obtaining them is a local measurement, not a guess.** The card's producer writes no space
between cells, so its summary block extracts as a run of figures — `db/seeds/README.md` records
that the balance anchors match `\d[\d,]*\.\d{2}` segmentation of a six-figure line rather than a
label, and that each of its three non-obvious values was measured rather than assumed. The
metadata patterns are found the same way: run `POST /layouts/propose` (or the parse preview)
against a real statement on the owner's machine, read the page text the engine actually sees,
and write patterns against it. Task 4a3 is that measurement and it cannot run in a remote
checkout.

**Changing a seeded profile is a layout-data change, not code.** `POST /layouts` re-saving an
existing name inserts version + 1 and never mutates an earlier version, so the card profile
becomes version 2 and every already-imported document keeps pointing at version 1 — its
provenance stays true (I6). `db/seeds/card-layout.json` is a `POST /layouts` body, not a `psql`
seed, and `api/tests/test_seeded_layouts.py` validates every file in that directory on every
run. `check-diff-size.sh` skips `.json`. **Do not add `period_start` / `period_end` to this
profile:** `db/seeds/README.md` records why the card deliberately derives its period — its
installment block prints each plan's original purchase date, so capturing the period would fail
rows the declared total legitimately includes — and `LayoutCreate` requires both or neither, so
adding the card facts alone is valid.

**A typed vocabulary is code, and it grows deliberately.** A non-reserved metadata key already
flows into `document.extracted_metadata` as a raw captured string. Money and dates cannot stay
strings, so 4a2 adds `statement_date`, `payment_due_date`, `minimum_due` and `credit_limit` to
`RESERVED_METADATA_KEYS` and normalises each at staging — the same small-and-additive discipline
`QUIRKS_VOCABULARY` and the row-start vocabulary already follow. None of those four names an
institution, a currency or a country.

**Statement balance is already captured and is not re-extracted.** It is
`import_batch.declared_closing_balance`, the figure I8 already reconciles the whole statement
against. Capturing it twice would create two sources for one number and a way for them to
disagree.

## Where net worth's rate multiplication is allowed, and where it is not

`CLAUDE.md`: *reports never read the base table and never multiply by a rate.* That rule is
about **flows**. ADR-0002 and `multi-currency-and-fx.html` §§1 and 6 draw the line explicitly:

- **A flow translates at its own booked-date rate and is frozen forever.** Cash flow, spending,
  income, savings rate and the 12-month trend read `v_transaction_reportable` and sum the frozen
  `amount_base` / `amount_secondary`. They never multiply, never re-translate, and never touch
  the base table. That is I5, and 4b1's static check enforces it.
- **A balance translates at the as-of-date rate, computed live.** "A balance is a snapshot of
  value held now. Snapshots use spot." Net worth is `SUM(current_balance × rate(currency → base,
  as_of))`, which *is* a rate multiplication and is the correct one. 4b1's check is written to
  permit it and to say so in its own message, so nobody later reads the message and deletes the
  right code.

Where a rate for a held currency is carried forward, the figure is **labelled carried** and the
rate's real date travels with it (I7). Where no rate exists at or before the as-of date for a
currency the household holds, the net-worth figure is **null with the currency and the date
named** — never a partial sum presented as complete. `_frozen()` already refuses with that
sentence and already reports the carried flag; reuse it rather than writing a second lookup.

## What the 12-month trend is

**Twelve months of income, spending, net and savings rate** from frozen amounts. Not a
net-worth history: month-end snapshots and the net-worth history are Phase 2 in the phase plan's
own words, `net_worth_snapshot` (`schema.md` `0015`) does not exist, and re-deriving every
account's balance at twelve past dates against twelve days of rates is that table's job, not a
live query's. Net worth in this slice is one as-of figure in both currencies.

## What the acceptance run is, and where it happens

> Dues match the card statement; net worth equals the sum of signed balances in both currencies;
> a payment moves no spending figure.

All three are **local**, on the owner's machine, against the ledger built from the git-ignored
`pdf-statements/` — 4,184 transactions across 7 accounts, two of them card accounts. A remote
checkout has neither the statements nor the ledger. The third depends on slice 1.3: a card
payment is excluded because it was **confirmed as a transfer**, so **1.4 does not begin until
1.3 is accepted**.

**No real figure, filename, extract or row enters this repository on any branch** — not in a
commit message, a PR body, a test fixture or a layout pattern. A fixture reproduces the
*arithmetic* with invented numbers, exactly as `api/tests/test_ingest_card_frame.py` does; the
run itself is recorded in `docs/superpowers/progress.md` as slices 1.1 and 1.2 were.

## Sequence and migrations

`4a1 → 4a2 → 4a3`, and `4b1 → 4b2 → 4b3`. The two chains are independent except at the end:
4b2's dues section reads what 4a2 writes, so 4b2 bases off `develop` after 4a2 has merged.
4a3 is local and may run last.

One migration is needed:

| Migration | Task | Does |
|---|---|---|
| `V21__card_statements.sql` | 4a1 | `card_statement` per `schema.md` `0011`, its unique index on `(account_id, period_end)`, its RLS, and the deferred FK on `transaction.card_statement_id` |

Numbered assuming slice 1.3's V19 and V20 have merged. **Take the next free number at dispatch**
— the file name is not a contract, the order is. Migrations are forward-only and
Flyway-checksummed; never edit an applied one. A table change edits the migration, `schema.md`
and `data-model.html` in one commit.

---

## Task 4a1 — the card cycle entity

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-4a1-card-statement-schema`
**Base and PR target:** `develop`
**Migration:** `V21__card_statements.sql` — creates `card_statement` per `schema.md` `0011`,
`idx_card_stmt_period`, its RLS policy, and the deferred FK on `transaction.card_statement_id`
that `schema.md` `0018` reserves for this migration.
**Depends on:** nothing in this slice; 1.3 accepted.
**Forecast:** ~140 production, ~200 test.

**Outcome.** A card's cycle is an entity: period, statement date, due date, statement balance,
minimum due, limits and charges, with one row per cycle per account and no way to hold two.

**Files.** Create `db/migrations/V21__card_statements.sql` and
`db/tests/0019_card_statements.sql`; modify `docs/superpowers/schema.md` and
`docs/design/data-model.html` in the same commit.

**Contracts.** `schema.md` `0011` verbatim, including `period_valid`, `due_after_statement` and
the unique `(account_id, period_end)`. Every money column is `NUMERIC(20,4)`; `opening_balance`
and `closing_balance` are **signed** (ADR-0010) and `statement_balance` is positive — what was
due. Reward points are integers and are not money.

**Invariants / security.** RLS with `household_id = current_household_id()`, following V9's
shape. Add a `CHECK` or trigger that the account is a `credit_card` account of the same
household — a cycle on a savings account is meaningless and the FK cannot say so. ADR-0010's
sign convention is what makes `closing_balance` negative on a card the owner owes on; state that
in the migration comment, because it is the sign that is "internally consistent and therefore
invisible" when wrong. No currency, institution or country name becomes an identifier.

**Required test behaviours (failing first).**
- RLS is enabled and a second household's cycles are invisible under a bound user.
- Two cycles with the same `(account_id, period_end)` are refused.
- `period_valid` and `due_after_statement` each refuse their inverted case.
- A cycle on a non-card account is refused.
- The FK on `transaction.card_statement_id` exists and refuses a cycle id that does not.
- A card the owner owes on stores a **negative** `closing_balance` and a **positive**
  `statement_balance`, and the two are not confused — the sign test ADR-0010 asks for per card
  surface.
- Migration from empty applies every migration and every `db/tests/*.sql` is green.

**Validation.** `docker compose run --rm flyway migrate` from empty and onto the standing
ledger, every `db/tests/*.sql`, `check-docs.py`, `scripts/check-identifiers.py`.

**Acceptance.** The cycle table exists with `schema.md` `0011`'s shape, is household-isolated,
and `schema.md` and `data-model.html` agree with it.

---

## Task 4a2 — the statement's own facts, captured and committed

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-4a2-card-statement-facts`
**Base and PR target:** `develop`
**Migration:** none — `document.extracted_metadata` (V12) already holds captured metadata, and
V14/V18's `commit_import_batch` is not changed.
**Depends on:** 4a1 merged.
**Forecast:** ~340 production, ~540 test.

**Outcome.** A card statement's metadata block is extracted, typed and stored, and committing
its batch writes the `card_statement` row in the same transaction as the ledger rows.

**Files.** Modify `api/oneledger/routes/layouts.py` (`RESERVED_METADATA_KEYS` and its
validators), `api/oneledger/ingest/pipeline.py` (normalise the captured metadata at staging),
`api/oneledger/routes/ingest.py` (write the cycle inside the commit transaction),
`api/tests/test_layouts.py`, `api/tests/test_ingest_pipeline.py`,
`api/tests/test_import_review_live.py` and `docs/design/statement-ingestion.html`; regenerate
`web/types/api/`.

**Contracts.** Four reserved metadata keys are added: `statement_date`, `payment_due_date`,
`minimum_due`, `credit_limit`. Each must have exactly one capture group, as the existing
reserved keys must. At **staging**, each captured string is normalised once — dates through the
profile's own `dateFormat`, money through the existing money parser into `Decimal` — and stored
in `document.extracted_metadata` in **canonical form** (`"2026-09-15"`, `"310.0000"`), so nothing
downstream has to re-parse a producer's formatting. A capture that will not normalise is a
**parse failure surfaced in the summary**, never a silently dropped fact (I7).

At **commit**, `POST /import/{batch}/commit` writes one `card_statement` row when
`document.document_type = 'card_statement'`, **inside the same `with connect()` transaction** as
`commit_import_batch`, so the cycle and the rows land together or not at all
(`statement-ingestion.html` §11 puts `INSERT INTO card_statement` in exactly that block).
`statement_balance` is `import_batch.declared_closing_balance` — the figure I8 already
reconciled — and is **not** re-extracted. `opening_balance` and `closing_balance` come from the
batch's declared anchors. Period comes from the batch. A card statement whose metadata block did
not extract still commits its rows: the cycle row is written with the facts that did extract and
the absent ones null, because refusing the import would lose the transactions over a due date.

**Invariants / security.** Money is `Decimal` in Python and `NUMERIC(20,4)` in Postgres, a
**string** on the wire; no captured figure ever becomes a float. Nothing about money is invented:
a fact that did not extract is null and shows as absent, never as zero (I7 — what makes a missing
minimum due different from a minimum due of nothing). Spec §5.6 still holds on the way out: no
digit run of seven or more from a document reaches any response, log or message, and an account
number in the metadata block must not survive. The commit route re-implements none of
`commit_import_batch`'s rules; it adds one insert after it and lets one transaction carry both.

**Required test behaviours (failing first).**
- A layout declaring the four new keys validates; one whose pattern has two capture groups is
  refused.
- A statement carrying the metadata block stages with the four facts normalised and canonical
  in `extracted_metadata`; a capture that will not parse is a reported parse failure and is
  absent, not zero.
- Committing a card batch writes exactly one `card_statement`, with `statement_balance` equal
  to the batch's declared closing; committing a **bank** batch writes none.
- A failure in the cycle insert rolls the whole commit back — no ledger rows, no cycle.
- Re-committing the same period is refused by the unique index rather than duplicating a cycle.
- A card statement with no metadata block still commits its rows, with the optional facts null.

**Validation.** `/verify` in full, including migrations from empty and every `db/tests/*.sql`.

**Acceptance.** A synthetic card statement with a metadata block imports and leaves one cycle
row whose facts match the fixture exactly.

---

## Task 4a3 — the card profile learns to read its own summary (local)

**Workspace:** `/Users/sandjaieravi/dev/OneLedger` — the owner's machine, where
`pdf-statements/` exists.
**Branch:** `feature/p1-4a3-card-layout-metadata`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 4a2 merged.
**Forecast:** ~0–80 production, ~320 test.

**Outcome.** The card layout captures its statement date, due date, minimum due and credit
limit, measured against the real statements, and a redacted fixture proves it.

**Files.** Modify `db/seeds/card-layout.json` (its `metadataPatterns`) and `db/seeds/README.md`
(a measured-values section per pattern, in the house style the file already sets); create the
redacted fixture and its golden-output test under `api/tests/`. Modify
`api/oneledger/ingest/engine.py` **only where a real file proves a gap** — a change needs the
failing file behind it and ships with the fixture that failed.

**Contracts.** Patterns are measured against the page text the engine actually sees, not against
what the PDF looks like: this producer writes no space between cells, so a pattern anchored on a
label may not split the run and `\s*` is used, never `\s+` — the trap `db/seeds/README.md`
already documents from both sides. Do **not** add `period_start` / `period_end`; the card
derives its period deliberately, and `LayoutCreate` requires both or neither. Re-saving the
profile through `POST /layouts` makes it version 2 and leaves version 1 and every document
pointing at it untouched.

**Invariants / security.** **No real statement, figure, extract or filename enters this
repository** — the fixture reproduces the geometry and the arithmetic with invented numbers, as
`api/tests/test_ingest_card_frame.py` does. A pattern is a *shape*, not a value: a regex that
only matches this issuer's particular figure would be a real figure in the repository by another
route. I6: a re-imported statement names layout version 2 and the earlier ones still name 1.
I8 is unchanged — the closing anchor is the same fifth figure it always was, and adding metadata
capture must not move it. ADR-0012: nothing about the profile names its bank.

**Required test behaviours (failing first).**
- A golden-output test: the redacted fixture in, the four normalised facts and the unchanged
  reconciliation verdict out, asserted exactly.
- The profile still validates through `LayoutCreate` — already covered generically by
  `api/tests/test_seeded_layouts.py`, so no branch writes this test again.
- The closing anchor still captures the fifth figure and the statement still reconciles
  `matched`: adding metadata capture changes no verdict.
- A statement whose metadata block is absent parses, reconciles and reports the facts as absent.
- Any engine change carries the fixture that failed before it.

**Validation.** `/verify` in full. Then, locally: re-post the profile, re-import one statement
per card account, and confirm the cycle rows against the PDFs by eye.

**Acceptance.** The card's statement date, due date, minimum due and credit limit are captured
from the real statements, and every card statement still reconciles.

---

## Task 4b1 — the two static checks the documents already claim exist

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-4b1-reporting-static-checks`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** nothing.
**Forecast:** ~140 production, ~220 test.

**Outcome.** The two rules `CLAUDE.md` and `testing-and-data-quality.html` §2 both describe as
build failures actually fail the build, before the first reporting query is written.

**Files.** Modify `scripts/check-identifiers.py` and `api/tests/test_check_identifiers.py`.

**Contracts.** Extend the existing script rather than adding a second one — one command, one
hook entry, one place to read the rules.

- **The reporting reader.** A file reading the base `transaction` table (`FROM transaction`,
  `JOIN transaction`, `UPDATE transaction`, `INSERT INTO transaction`) must be on a short,
  named allowlist: `api/oneledger/routes/transactions.py` (the deliberate non-report reader,
  and it says so in its own docstring), `api/oneledger/routes/ingest.py`,
  `api/oneledger/ingest/`, `db/migrations/` and `db/tests/`. Any **new** reader fails until it
  is added deliberately, with a comment saying why. The failure message names
  `v_transaction_reportable` and I3.
- **Rate multiplication on a flow.** A multiplication whose operands are a transaction amount
  (`amount`, `t.amount`, `s.amount`, `amountBase`, `amount_base`, `amount_secondary`) and a rate
  (`rate`, `fx_rate_to_base`, `fx_rate_to_secondary`) fails outside `db/migrations/`
  (V10's `frozen_amounts_derive` derives them by definition) and `api/oneledger/money.py` and
  `api/oneledger/ingest/` (the freeze site). The failure message names I5.

**Invariants / security.** **The message must say what is allowed, not only what is not.**
`account.current_balance × rate` is the correct as-of translation (ADR-0002,
`multi-currency-and-fx.html` §6) and is deliberately outside both rules; the check's docstring
and its failure text say so, or the next reader deletes working code to make a check pass. The
allowlist is a literal in the script, not a comment pragma a caller can write for itself —
`CLAUDE.md`: do not silence this check. Existing violations must be zero on `develop` before
this merges; if the scan finds one, that is a finding, not a reason to widen the allowlist.

**Required test behaviours (failing first).**
- A synthetic file reading `FROM transaction` outside the allowlist fails, with I3 named.
- The same read inside `routes/transactions.py` passes.
- A synthetic `SUM(amount * fx_rate_to_base)` outside the allowed paths fails, with I5 named.
- `round(amount * fx_rate_to_base, 4)` inside `db/migrations/` passes.
- `SUM(current_balance * rate)` passes anywhere, and a test says explicitly that it is meant to.
- The whole current tree scans clean.

**Validation.** `python3 scripts/check-identifiers.py`, `pytest -q`, `ruff`, `mypy --strict`.

**Acceptance.** Both rules fail the build on a violation and neither fires on the tree as it
stands.

---

## Task 4b2 — one payload: net worth, the month, the trend, the dues

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-4b2-dashboard-api`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 4a2 and 4b1 merged.
**Forecast:** ~460 production, ~700 test. **If it overruns**, the payload's four sections split
cleanly in two: `feature/p1-4b2a-dashboard-position` (net worth and cards due — the balance-sheet
half, which reads `account` and `card_statement`) then `feature/p1-4b2b-dashboard-flows` (this
month, the trend — the half that reads `v_transaction_reportable`). The screen in 4b3 tolerates
either arriving first.

**Outcome.** `GET /dashboard` answers, in one payload and in both reporting currencies: what the
household is worth, what this month earned and spent, what it saved, how the last twelve months
compare, and what is due on which card and when.

**Files.** Create `api/oneledger/routes/dashboard.py`, `api/tests/test_dashboard.py` and
`api/tests/test_dashboard_live.py`; modify `api/oneledger/main.py` and
`docs/design/api-contracts.html`; regenerate `web/types/api/`.

**Contracts.** One route, four sections.

- **Net worth**, as of a date defaulting to today: `SUM(current_balance × rate(currency → base))`
  and the same to secondary, over accounts with `include_in_net_worth`, plus the asset and
  liability subtotals and liquid net worth (`is_liquid`). One `SUM` with no `CASE` and no
  subtraction — ADR-0010 is the whole point. Rates come from `_frozen()`, one call per distinct
  held currency; the multiplication and the aggregation happen in **Postgres**, with the rate
  pairs passed as parameters.
- **This month**: income, spending, net and savings rate, from `v_transaction_reportable`,
  summing frozen `amount_base` and `amount_secondary`. Never the base table, never a rate.
- **Trend**: the same four figures per month for the last twelve, one grouped query.
- **Cards due**: per card account, the latest `card_statement` — statement balance, minimum
  due, due date, days remaining, whether it is paid in full, and utilisation at statement date
  (`statement_balance ÷ credit_limit`, computed in Postgres, absent when the limit is null).

**Invariants / security.** `require_owner()`, household-filtered first. I3 and I5: the flow
sections read the view and the frozen columns only, and 4b1's check now enforces both. I7: a
carried rate is **labelled**, with the rate's real date; a currency the household holds with no
rate at or before the as-of date makes net worth **null with the currency and date named**,
never a partial sum. Savings rate is a percentage in the human-readable convention `15.2500` is
15.25%, not `0.1525`, and it is `NUMERIC`; a month with zero income yields null, not a division
by zero and not zero. Money is a **string** on the wire, every figure. Aggregation is in
Postgres; the route shapes the response and reduces nothing. No string-interpolated SQL.

**Required test behaviours (failing first).**
- **The acceptance identity:** net worth equals the hand-summed signed balances translated at
  the as-of rate, in **both** currencies, on a fixture holding an asset, a card and a loan —
  `testing-and-data-quality.html` §1's I2 case, through the route.
- A liability lowers net worth with no `CASE` anywhere in the query.
- **A confirmed card payment moves no spending figure**: the same fixture with and without the
  transfer confirmed produces identical spending, and removing the link raises it by exactly the
  payment (I3 and I4, through the report rather than the view).
- Spending for a hand-tallied month matches exactly, and is byte-identical after the rate table
  is moved by a large step (I5).
- A carried rate is labelled with its real date; a missing rate nulls net worth and names the
  currency and the date.
- Savings rate is the human-readable percentage, and a zero-income month is null.
- The trend returns twelve months including empty ones, in order.
- Dues: the latest cycle per card, days remaining computed in Postgres, utilisation absent when
  the limit is null.
- Every money field on the wire is a string.

**Validation.** `ruff`, `mypy --strict`, `pytest -q`, the live suite, `check-docs.py`,
`scripts/check-identifiers.py`, the generated-type check.

**Acceptance.** One payload answers all four questions, and I2, I3, I4, I5 and I7 each have a
test that would fail if it stopped being true.

---

## Task 4b3 — the Overview screen stops saying "Phase 0"

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-4b3-overview-screen`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 4b2 merged.
**Forecast:** ~420 production, ~440 test.

**Outcome.** The Overview screen shows net worth in both currencies, this month's income,
spending, net and savings rate, the twelve-month trend and the cards due — and nothing it
computed itself.

**Files.** Create `web/app/overview/overview-screen.tsx` and its test; modify
`web/app/workspace.tsx` and `web/app/__tests__/workspace.test.tsx`.

**Contracts.** The screen renders `GET /dashboard` and calls nothing else. The trend is twelve
labelled months with their four figures; a chart is optional and a table is sufficient — the
figures are the deliverable. A card's dues show the amount, the due date and the days remaining
as the API stated them.

**Invariants / security.** **TypeScript formats money and does no arithmetic on it.** Every
figure — net worth, the subtotals, the month's four numbers, the trend's forty-eight, the spread
of each due — arrives as a decimal string and is rendered through `formatMoney` with the
currency row that owns it. No `parseFloat`, no `Number()`, no comparison, no percentage
computed in the browser, no chart library handed a money string to coerce; `scripts/check-identifiers.py`
fails the build on all of it, and `.balance`, `.currentBalance`, `.creditLimit`, `.rate` and
`.total` are already money-named properties to it. **A liability displays flipped** — "You owe"
— and the flip lives in one formatting helper, never scattered (ADR-0010's enforcement clause);
the database stays signed. **A carried rate and a null net worth are shown as such, not hidden
or defaulted** (I7): "as of the rate for that date, carried forward" and, for a missing rate, the
sentence the API returned. A card figure is formatted with **that card's** currency row and a
base figure with the household's — the defect slice 1.1's run found (#67).

**Required test behaviours (failing first).**
- Net worth renders in both currencies, with the liability flipped in presentation only.
- A carried rate renders its label and its real date; a null net worth renders the API's
  sentence and no number.
- The month's four figures and the twelve-month trend render from the payload with no
  arithmetic — asserted by the static check, and by a test that a changed payload changes every
  figure.
- The dues section renders each card's amount in that card's currency, its due date and its
  days remaining.
- An empty ledger renders an empty state rather than zeros presented as facts.
- A failed request renders the service's own sentence, not a JSON envelope.

**Validation.** `npm run lint`, `typecheck`, `typecheck:compat`, `vitest run`, `npm run build`,
the bundle secret scan, `scripts/check-identifiers.py`.

**Acceptance.** The Overview screen is the application's front page and every figure on it came
from Postgres.

---

## The local acceptance run

Run on the owner's machine after 4b3 merges, against the standing ledger. It answers three
questions and is recorded in `docs/superpowers/progress.md` with no real figure, filename or row.

1. **Do the dues match the card statement?** For each card account's latest cycle, the statement
   balance, minimum due and due date on screen are compared against the PDF by eye. Exact.
2. **Does net worth equal the sum of signed balances in both currencies?** The seven accounts'
   `current_balance` figures are translated by hand at the as-of rates and compared against the
   screen, in base and in secondary. Exact.
3. **Does a payment move no spending figure?** A confirmed card payment is unlinked, the month's
   spending is read again, and it rises by exactly the payment; re-linking restores it. The
   figure must move by the payment and by nothing else.

If (2) does not match, the difference is the finding — an account wrongly included, a sign, a
rate from the wrong date — and it is diagnosed before the slice is accepted.

## What is not in this slice

`net_worth_snapshot`, month-end snapshots and net-worth history; the attribution waterfall and
its residual (`net_worth_attribution`) — Phase 2, and the reason the trend here is flows rather
than net worth. Payment reminders and revolving-balance alerts (ADR-0009 defers them
explicitly). Card statement coverage gaps and the rest of `GET /doctor`. Reward-point tracking
beyond the columns V21 creates. Investments and the portfolio, which are 1.5, so the dashboard's
`portfolio` section named in `api-contracts.html` §2 is absent here and arrives with them.
