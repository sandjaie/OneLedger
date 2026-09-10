# Implementation progress

The single maintained implementation-status file: what is done, what is next, and where the
evidence for each sits. **It is tables.** The reasoning does not live here — a decision taken
while building a task belongs to the milestone's spec, dated run evidence to `reports/`, and
scope and sequence to the roadmap and the plans. Keeping this file to status is what lets it be
read at a glance and updated at a task boundary without an argument about what to write.

Updated: 10 September 2026.

## Where the work stands

Delivery follows the [phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) — six
phases, one term; the plan maps the old phase/milestone names. Sections further down record
delivered work under its old numbering and are not renamed.

| Phase | Status | Plan |
| --- | --- | --- |
| 0 — foundations and manual ledger (old Phase 0, M1a, M1b, M2 tasks 1–5c) | Delivered; live Google identity gates still open | [phase plan](plans/2026-09-09-phase-delivery-plan.md) |
| 1 — the complete application | **Active** — the current delivery | [phase plan](plans/2026-09-09-phase-delivery-plan.md) |
| 2 — trust and depth | Not started | planned when it starts |
| 3 — planning and resilience | Not started | planned when it starts |
| 4 — analysis | Not started | planned when it starts |
| 5 — intelligence | Not started | planned when it starts |

Each phase ships as branches and PRs into `develop` under the cycle plan → implement → local
review → merge ([git-workflow](../git-workflow.md)); any path to `main` needs a separate owner
request.

## Local operation

| Capability | Branch | Status | Evidence |
| --- | --- | --- | --- |
| Isolated local-production stack | `feature/local-production-stack` | Delivered — production-built web and API, a migrated owner-only database, generated private secrets, persistent production-only volumes and loopback-only local sessions run beside the untouched development ledger | [ADR-0018](../adr/0018-local-owner-sessions.html) · [ADR-0019](../adr/0019-separate-local-production-stack.html) |

## Phase 1 — the complete application

| Slice | Branches | Status | Evidence |
| --- | --- | --- | --- |
| 1.1 — statements land in the ledger | `fix/p1-1-card-layout-fitness` · `feature/p1-1a-parse-pipeline` · `feature/p1-1a-parse-route` · `feature/p1-1b-commit-functions` · `feature/p1-1b-review-routes` · `feature/p1-1c-import-screen` · `feature/p1-1b-duplicate-override` · `feature/p1-1d-review-screen` | **Accepted** — the run was made on 9 September 2026; all 38 card statements were handled unaided | [acceptance run](#phase-1--slice-11--the-acceptance-run) |
| 1.2 — all four bank layouts | `feature/p1-2a-encrypted-statements` · `feature/p1-2b-batch-cancellation` · `feature/p1-2c1-layout-two` · `feature/p1-2c2-layout-three` · `feature/p1-2c3-layout-four` | **Accepted** — the run was made on 10 September 2026; 67 of the 71 gathered files imported and every account reconciles across its period | [acceptance run](#phase-1--slice-12--the-acceptance-run) |
| 1.3 — classification and transfers | `feature/p1-3a1-classification-rules` … `feature/p1-3a5-rules-screen` · `feature/p1-3b1-transfer-schema` … `feature/p1-3b5-stated-remittance` | In progress — every task is delivered; the acceptance run remains, and waits on the owner confirming a remittance and a card payment | [slice 1.3](#phase-1--slice-13--classification-and-transfers) |
| 1.4 — cards, net worth and trends | `feature/p1-4a1-card-statement-schema` … `feature/p1-4b3-overview-screen` | In progress — every task is delivered; acceptance question 2 passes, questions 1 and 3 wait on the owner | [slice 1.4](#phase-1--slice-14--cards-net-worth-and-trends) |
| 1.5 — investments | `feature/p1-5a-investment-schema-api` · `feature/p1-5b-nav-and-imports` · `feature/p1-5c-portfolio-screen` | Not started | — |
| 1.6 — salary and a thin forecast | `feature/p1-6a-payslips` · `feature/p1-6b-commitments-forecast` | Not started | — |
| 1.7 — the layout mapper and documents | `feature/p1-7a-layout-mapper` · `feature/p1-7b-documents-diagnostics` | Not started | — |

## Phase 1 · slice 1.4 — cards, net worth and trends

| Task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 4a1 — the card cycle entity | `feature/p1-4a1-card-statement-schema` | Delivered | [#99](https://github.com/sandjaie/OneLedger/pull/99) |
| 4a2 — the statement's own facts, captured and committed | `feature/p1-4a2-card-statement-facts` | Delivered | [#100](https://github.com/sandjaie/OneLedger/pull/100) |
| 4a3 — the card profile learns to read its own summary | `feature/p1-4a3-card-layout-metadata` | Delivered | [#114](https://github.com/sandjaie/OneLedger/pull/114) |
| 4a4 — card cycles for statements committed before the layout read them | `feature/p1-4a4-card-cycle-backfill` | Delivered | [#115](https://github.com/sandjaie/OneLedger/pull/115) |
| 4b1 — the two static checks the documents claim exist | `feature/p1-4b1-reporting-static-checks` | Delivered | [#101](https://github.com/sandjaie/OneLedger/pull/101) · the same gap in the other two tree-walking gates, [#102](https://github.com/sandjaie/OneLedger/pull/102) · #102's tests no longer reach the repository a push hook is for, [#106](https://github.com/sandjaie/OneLedger/pull/106) |
| 4b2a — the dashboard's position: net worth and cards due | `feature/p1-4b2a-dashboard-position` | Delivered | [#104](https://github.com/sandjaie/OneLedger/pull/104) |
| 4b2b — the dashboard's flows: this month and the trend | `feature/p1-4b2b-dashboard-flows` | Delivered | [#105](https://github.com/sandjaie/OneLedger/pull/105) |
| 4b3 — the Overview screen | `feature/p1-4b3-overview-screen` | Delivered | [#113](https://github.com/sandjaie/OneLedger/pull/113) |
| the acceptance run | — | Partly run, 10 Sep 2026: the working ledger migrated to V22, the card layout re-posted as version 2 and 35 cycles backfilled; **question 2 passes**, net worth recomputed independently at the as-of rates matches the dashboard exactly in both currencies. Question 1 (dues against the PDFs, by eye) and question 3 (unlink and re-link a confirmed card payment) wait on the owner | [#115](https://github.com/sandjaie/OneLedger/pull/115) |

**4a4's deviations, recorded:**

- **4a4 is an addition to the plan.** 4a3's local validation re-imports one statement per card, but
  a statement already committed is refused as a duplicate, so every card statement imported before
  the layout learned its facts would never have a cycle, and the acceptance run's first question —
  do the dues match the statement — could not be asked of the ledger. `POST
  /card-statements/backfill` re-reads each one's stored original with the newest version of its
  layout and writes the cycle through the commit's own `_write_card_cycle`, naming any it cannot
  complete.

**4a3's deviations, recorded:**

- **The engine changed, because a real file proved the gap.** The card's summary block writes its
  five values with no space between cells, and the credit limit, a whole number with no decimals,
  fuses onto the available credit in the raw run metadata was matched against; no pattern can find
  that seam. A new quirk, `metadata_text:words`, points a layout's metadata patterns at the words
  joined by single spaces, and the balance anchors keep reading the raw run. The fixture that fails
  without it ships with it.
- **The re-post and the re-import wait.** The plan's local validation re-posts the profile as
  version 2 and re-imports one statement per card, but the working ledger is still at V20, with no
  `card_statement` table to write a cycle into, and V21 there follows the owner's transfer
  acceptance. The patterns were measured through the engine against all 35 distinct card statements
  instead, and `db/seeds/README.md` records what was found.

**4b3's deviations, recorded:**

- **The workspace loads the currency catalogue on the Overview.** A card due can be in a currency
  outside the household's pair, and it is formatted with its own row, so the test that said the
  Overview never fetches `/currencies` now says it fetches exactly `/household`, `/currencies` and
  `/dashboard`.
- **The empty state reads "no held currency and no card".** The payload has no account count, and
  `netWorth.rates` lists every currency an account that counts toward net worth is held in, so an
  empty list means nothing counts yet. A household whose only accounts are excluded from net worth
  sees the empty state even when it has flows.
- **One liability flip, not two.** `formatOwed` in `web/lib/money/format.ts` is the flip, and the
  accounts screen's per-account "You owe" now calls it. That screen's group totals still write "₹X
  owed" through their own `unsigned()`; unifying the wording changes that screen and its tests, so
  it is left for its own change.

**4b2's deviations, recorded:**

- **4b2 ships as the plan's own named split,** 4b2a the position (net worth, cards due) then 4b2b the
  flows (this month, the trend): forecast near the 1,600 soft budget once tests are counted at this
  repository's usual 1.5–2×.
- **Net worth takes the date's own rate where the table holds one, and fills forward past the last
  fetch.** `multi-currency-and-fx.html` §6 joins `fx_rate` on the exact as-of date, and the plan
  names `_frozen()`, which does the same; but the rate table is filled forward only as far as its
  last fetch, so an exact join makes net worth *today* null every day until someone backfills. The
  plan asks for "a carried rate labelled, with the rate's real date", so past the last row the
  dashboard fills forward the way the table fills its own weekends — never interpolating — and
  labels the rate carried with the date it is really from. Within the fetched range it is §6
  exactly.
- **Utilisation reads the cycle's own `credit_limit`**, the limit at statement date, and is absent when
  the cycle states none — which, until 4a3 teaches the card layout to capture it, is every real card.
- **Spending is one figure.** F02 separates consumption spending from external household outflows;
  nothing in Phase 1's classification tells the two apart, so both are in spending. That leaves net
  equal to F02's retained income, and the savings rate F02's own.
- **The savings rate is null at negative income, not only at zero.** The plan names the zero-income
  month; F02 says "not applicable" when income is zero or negative, and the product document wins.

**4b1's deviations, recorded:**

- **Three readers were added to the allowlist, each with its reason, none of them a report.** The
  plan's list predates slice 1.3: `routes/rules.py` (classification preview and backfill must see
  rows that already carry an answer), `routes/transfers.py` (matching looks for unlinked legs, which
  the reportable view drops) and `routes/accounts.py` (a balance and the guards on an account's
  anchor must include every movement, transfers too). A report reading the base table would have
  been a finding; none of these is one.
- **The rate rule's allowed paths are exactly the plan's, and the tree was brought to them rather
  than the list widened.** The manual route and the staging validator each multiplied a flow by a
  rate themselves — a second and third copy of the freeze that `money.py`'s own docstring said did
  not exist. All three now call `money.translate()`; the full suite proves the change is
  behaviour-preserving.
- **Both rules guard product code and read SQL inside strings, keyword upper-case only.** Tests
  build ledgers and re-derive frozen figures on purpose; and case-insensitive keywords would refuse
  English that says a figure is read "from transaction rows". Lower-case SQL would slip past, and
  the script's docstring says so.

**4a2's deviations, recorded:**

- **A cycle is written only when both its dates were captured.** The plan says a statement with no
  metadata block still gets a cycle "with the absent ones null", but `statement_date` and
  `payment_due_date` are `NOT NULL` in `schema.md` `0011`, and defaulting either would invent a fact
  (I7). So the rows always commit, the cycle is skipped, and the commit response names what was
  missing (`cardStatementMissing`). The design document wins over the plan.
- **A fact that will not read is reported apart from row failures** (`metadataFailures`) and never
  gates staging. `blocks_staging` refuses a statement on any row failure; a due date is not worth
  the statement's transactions.
- **The import screen does not show `metadataFailures` yet.** 4a2 asks only that they reach the parse
  summary. Showing them is a small follow-up, not folded in.
- **The live tests are their own file**, `api/tests/test_card_cycle_live.py`, rather than
  `test_import_review_live.py`, which is past twelve hundred lines.
- **`test_layouts.py`'s example of an unreserved key changed** from `credit_limit` to `note`, because
  `credit_limit` is now reserved. The assertion's intent — a non-reserved key may carry any pattern
  —
  is unchanged.

**Two things 4a2 must not read literally**, found in 4a1's review:

- **`statement_balance` is the magnitude of what was due, not the batch's declared closing.** The
  plan's 4a2 contract says it "is `import_batch.declared_closing_balance`", but that figure is
  signed — negative on a card the owner owes on — and `schema.md` `0011` defines
  `statement_balance` as positive. V21 refuses the literal reading
  (`statement_balance_is_what_was_due`): `closing_balance` takes the signed declared closing and
  `statement_balance` its magnitude when owed, zero when in credit. The plan is imprecise here;
  `schema.md` is right.
- **Whoever writes `payment_transaction_id` or `source_document_id` asserts the household.** V21
  checks the account and nothing else; V10 checks a transaction's category and merchant for the
  same reason — a foreign reference leaks another household's data. 4a2 writes
  `source_document_id` from the household's own batch, safe by construction; nothing writes
  `payment_transaction_id` yet.

**4a1 adds two `CHECK`s beyond `schema.md` `0011`**, both ADR-0010 made refusable: an amount due
is never negative, and a card in credit has nothing due — so the amount due stored as the
closing balance, the one sign swap this table invites, is refused rather than trusted. It also
refuses a cycle in a currency other than its card's, which the task block did not list: a cycle
in another currency would make its balances incomparable with the account's.

## Phase 1 · slice 1.3 — classification and transfers

| Task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 3a1 — the rules table | `feature/p1-3a1-classification-rules` | Delivered | [#81](https://github.com/sandjaie/OneLedger/pull/81) |
| 3a2 — the cascade | `feature/p1-3a2-classification-cascade` | Delivered | [#82](https://github.com/sandjaie/OneLedger/pull/82) |
| 3a3a — rules through the app | `feature/p1-3a3a-rules-crud` | Delivered | [#83](https://github.com/sandjaie/OneLedger/pull/83) |
| 3a3b — preview and backfill | `feature/p1-3a3b-preview-and-backfill` | Delivered | [#84](https://github.com/sandjaie/OneLedger/pull/84) |
| 3a4 — the seed pack, and import classifies | `feature/p1-3a4-seed-rules-and-import` | Delivered | [#85](https://github.com/sandjaie/OneLedger/pull/85) |
| 3a4 — a pack loads as seed rules | `fix/p1-3a4-a-seed-pack-loads-as-seed` | Delivered | [#88](https://github.com/sandjaie/OneLedger/pull/88) |
| 3a5 — the rules screen | `feature/p1-3a5-rules-screen` | Delivered | [#86](https://github.com/sandjaie/OneLedger/pull/86) |
| 3b1 — the transfer store | `feature/p1-3b1-transfer-schema` | Delivered | [#90](https://github.com/sandjaie/OneLedger/pull/90) |
| 3b2 — the matcher, as a function of values | `feature/p1-3b2-transfer-matching` | Delivered | [#93](https://github.com/sandjaie/OneLedger/pull/93) |
| 3b3a — transfer reads: candidates, peers, the decided list | `feature/p1-3b3a-transfer-reads` | Delivered | [#94](https://github.com/sandjaie/OneLedger/pull/94) |
| 3b3b — transfer writes: confirm, link by hand, unlink, FX cost | `feature/p1-3b3b-transfer-writes` | Delivered | [#95](https://github.com/sandjaie/OneLedger/pull/95) |
| 3b4 — link and unlink from the ledger | `feature/p1-3b4-transfer-screen` | Delivered | [#96](https://github.com/sandjaie/OneLedger/pull/96) |
| 3b5 — a remittance's own narration as a matching signal | `feature/p1-3b5-stated-remittance` | Delivered | [#107](https://github.com/sandjaie/OneLedger/pull/107) |
| the acceptance run | — | Not run | — |

Two decisions from this slice sit outside it. **ADR-0017** supersedes ADR-0007: a transfer is
confirmed on the transactions screen rather than at review, because a transfer's two legs are
almost never in the same import batch. And `CLAUDE.md` gave up its restatement of the rules for
a pointer table ([#79](https://github.com/sandjaie/OneLedger/pull/79)) — every rule it dropped
was checked into a document first.

### Deviations recorded

- **3b1 carries `assert_legs_unclaimed()`**, which the task block did not list and
  `data-model.html` §8 specifies: the two partial unique indexes do not stop one transaction
  being the source of one confirmed transfer and the destination of another. The design
  document wins over the plan.
- **3b2's pair-history signal saturates at three confirmations.** §3's "confirmed eleven times"
  is an illustration of the effect rather than a threshold; read as one, the signal would
  contribute almost nothing for a pair's first ten transfers.
- **3b3 ships as the plan's own split**, `3b3a` reads then `3b3b` writes: forecast at about
  590 production lines and tests running 1.5–1.8× that, the whole task sat on the 1,600 soft
  budget before a line was written.
- **The candidate list's default window ends at the ledger's latest row, not today.** Anchored
  to today, a ledger whose statements lag three months would report nothing to confirm.
- **Manual-linking peers look 31 days out**, where the candidate scan stops at §3's widest
  window of 7: the owner is choosing, and a remittance that took ten days is still theirs to
  link after the scorer's date signal has given up on it.
- **3b4 loads the transfer queue when the owner asks, not when the screen renders.** The
  scan is the ledger joined against itself; running it on every visit to Transactions would
  make that screen as slow as its heaviest query, and would renumber every existing test that
  mocks the screen's requests in sequence. "Find transfers" is the click; nothing posts until
  "Confirm selected" is the next one.
- **3b3b derives each FX figure from the stored one before it.** The spread cost is the source
  amount times the *stored* four-place spread, not the unrounded one, so every stored figure
  can be recomputed from its neighbours — the rule `money._frozen` applies to frozen amounts.
  On §5's own example that stores 77.6050, where the unrounded chain gives 77.6053.
- **`costIsApproximate` is derived on read**, from the rate row's `is_carried_forward`, rather
  than stored: 3b3 carries no migration, and a derived flag cannot drift from the rate it
  describes.
- **No fee is taken or stored yet.** No statement this ledger reads itemises a transfer fee, so
  `explicit_fee_amount` stays null and the total cost is the spread. A fee input is its own
  task once a source for one exists.
- **3b2 adds `propose_kind()` and `band()`** beside `score_match()`, so 3b3 takes the kind and
  the presentation band from the same module rather than answering either a second way.
- **3b5 takes `multi-currency-and-fx.html` §5's narration signal for matching only** (owner
  decision, 10 September 2026). The current-account layout reads `remit_currency`,
  `remit_amount` and `remit_rate` per row into `raw_row`, and the scorer compares a credit with
  the stated amount in its own currency, lifting one that arrives as stated into the high band.
  `implied_rate` is still derived from the two legs, and a one-sided remittance still yields no
  cost figure: the owner chose not to take the per-transfer cost analysis §5 also describes.
- **3b5's lift is bounded at a 1% receiving charge, not §3's 5%.** It is the one signal that
  reaches "high" before any pair history exists, and the transactions screen pre-ticks that band.
- **3b5's matcher reads each leg's narration, not its staged `raw_row`.** Every current-account
  statement in the ledger was committed before the layout learned the captures, and `raw_row` is
  frozen at parse, so reading it would have reached none of them. The matcher applies the same
  capture to `description_raw` — the text the engine captures from — under the newest version of
  the layout that imported the row. A household's stored profile learns the keys only when
  `db/seeds/account-layout.json` is posted again; from then on every row it imported is read.

### Raised, not fixed here

- **`db/seeds/README.md` documented a step nothing could perform.** `POST /rules` stamps
  `origin` as `user_created` and refuses to be told otherwise, so the shipped pack loaded as
  thirty-seven of the owner's own rules — ahead of themselves in the cascade. `POST /rules/pack`
  is the writer that means `seed`; the fix branch above carries it.
- **`statement_layout.times_used` was never written.** V22 drops it and `last_used_at`, and
  usage is counted from `import_batch` instead (M2 spec §7 item 16). `fix/layout-usage-counter`
  carries it.
- **`api-contracts.html` §2 lists two routes twice**, with contracts that differ between the
  two entries.
- **`scripts/seed-household.sh` has no repair path** for the owner it seeded: correcting the
  address it wrote takes an `UPDATE`.

### Twice in one day, the working ledger was destroyed

**First, by the live test suite.** Exporting `.env` so the live tests would run pointed their
fixtures at the working database. Those fixtures empty `transaction`, `import_batch`,
`document`, `statement_layout` and `classification_rule`; 4,184 transactions, 67 batches, 67
documents and four layout profiles went with them.

**Then, by a teardown that looked scoped and was not.** `docker compose --profile test down -v`
removed `oneledger_pgdata` and `oneledger_documents` — the whole database and every original
statement it traces back to. `--profile` selects which services *start*; it does not scope a
teardown.

Both fixes are structural rather than habits, and each closes its own door:

| | |
|---|---|
| the suite cannot reach the working database | `db-test` is its own container, port and volume behind the `test` profile; `scripts/test-db.sh up` starts and exports it in one step; `api/tests/live_database.py` refuses a database holding anything the fixtures would empty, and refuses a split where the fixtures and the routes point at different databases ([#87](https://github.com/sandjaie/OneLedger/pull/87)) |
| a teardown cannot remove the working data | `pgdata` and `documents` are external volumes, which `down -v` is refused on in any project ([#91](https://github.com/sandjaie/OneLedger/pull/91)) |

A side effect of the first worth recording: with the suite finally reaching a database of its
own, the six `test_auth`/`test_oidc` failures and two `test_ingest_pipeline_live` failures long
treated as unavoidable turned out to be the same symptom, and the suite is green for the first
time.

### What the second rebuild cost, and what it proved

The 7 September backups predate the accounts, so the seven `account` rows were the one thing no
artifact held. They were reconstructed from the statements themselves — account number,
currency, account type, credit limit and opening balance are each stated on page one — and the
rebuild then reproduced **4,184 transactions across 67 batches, every account matching its
previous row count, and all 67 reconciling**. That last part is the evidence: a wrong opening
anchor makes I8 block its batches, so 67 matched reconciliations verify the reconstructed
anchors rather than merely being consistent with them.

Two things were derived rather than recovered and are worth an owner's eye: the account
**names** are the ones the tracker recorded, not something a statement states, and **Card A's
credit limit** is the first figure on its statement's limit row. Neither moves money.

Three defects surfaced during the rebuild, each raised as its own task rather than folded in:
**cancelling a batch leaves its document**, so the duplicate check refuses that file forever
and a cancelled statement can never be re-imported; **an import cannot create the account it
just read**, though the statement states everything an account row needs; and an
**unclassified row stays silent** rather than asking the owner how to mark it.

### Backups do not cover what changed most

Four backups existed and all four were from 7 September, before a single statement had been
imported. They held the household, currencies, categories and FX rates — and not one account,
transaction, document or layout. `scripts/backup-db.sh` is manual and nothing calls it. A
backup taken only when someone remembers is a backup of whatever the project looked like when
they last remembered.

**Now nobody has to.** The compose `backup` service runs `scripts/backup-db.sh --if-due` hourly
whenever the app's stack is up — a dump once the newest is over a day old, retention at thirty
days, never below the thirty newest — and Settings › Data Quality (`GET /doctor`) warns when
two days pass without one, naming how many transactions the newest backup does not hold
(`fix/backups-run-without-anyone-remembering`). The documents volume is still not backed up:
architecture §10's weekly copy was never written, and is its own `scripts/` branch.

## Phase 1 · slice 1.2 — all four bank layouts

| Task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 2a — a password reaches the parse | `feature/p1-2a-encrypted-statements` | Delivered | [#71](https://github.com/sandjaie/OneLedger/pull/71) |
| 2b — a staged batch can be discarded | `feature/p1-2b-batch-cancellation` | Delivered | [#72](https://github.com/sandjaie/OneLedger/pull/72) |
| 2c1–2c3 — the three remaining layouts | `feature/p1-2c1-layout-two` · `feature/p1-2c2-layout-three` · `feature/p1-2c3-layout-four` | Delivered | [#75](https://github.com/sandjaie/OneLedger/pull/75) · [#76](https://github.com/sandjaie/OneLedger/pull/76) · [#77](https://github.com/sandjaie/OneLedger/pull/77) |
| 2b — a cancelled batch releases its file | `fix/cancelled-batch-releases-its-file` | Delivered — layer 1 does not count a cancelled batch, and a re-import reuses the file's row | [#110](https://github.com/sandjaie/OneLedger/pull/110) |

The slice's survey found three of its four stated engine capabilities already delivered with M2
and exercised by slice 1.1; the plan records that and the five-branch split that followed. What
remained was the browser having no way to supply a password, and a staged batch having no way to
be abandoned. Both are merged.

`2c1`–`2c3` cannot run in a remote checkout: they need the statements, and `pdf-statements/` is
git-ignored and exists on the owner's machine only. Each carries that bank's profile, a redacted
fixture and a golden-output test; the last also carries the 71-file acceptance run.

## Phase 1 · slice 1.1 — statements land in the ledger

`/oneledger-plan-review` ran on the phase delivery plan on 9 September 2026, before the first
dispatch, and split the slice into five branches; the owner took the two decisions it raised.
Delivery split it three times more: branch 1a passed the diff budget's hard stop written as one
branch, 1b split on the same forecast, and the confirmed-distinct override (M2 6c, which the
slice covers) was found missing while building the review screen. With the `pg_temp` repair the
review gate raised on #54 — taken as its own fix branch because every remaining branch builds on
that boundary — the slice ran to nine. The decisions themselves are contract and live in the
[M2 spec §8](specs/2026-09-07-m2-statement-importer.md).

| Branch | Delivers | Status | Evidence |
| --- | --- | --- | --- |
| `fix/p1-1-card-layout-fitness` | `inverts_sign` reframes whatever stated no direction, so a card's bare anchors reach the ledger's frame; the card profile as data | Merged | [#51](https://github.com/sandjaie/OneLedger/pull/51) |
| `feature/p1-1a-parse-pipeline` | `ingest/pipeline.py`: identification, the staging transaction, the second-staging refusal | Merged | [#52](https://github.com/sandjaie/OneLedger/pull/52) |
| `feature/p1-1a-parse-route` | `POST /parse` and its wire contract | Merged | [#53](https://github.com/sandjaie/OneLedger/pull/53) |
| `feature/p1-1b-commit-functions` | V14's `commit_import_batch` and `assert_account_reconciles`, and `db/tests/0012` | Merged | [#54](https://github.com/sandjaie/OneLedger/pull/54) |
| `fix/security-definer-search-path` | V15: every `SECURITY DEFINER` function names `pg_temp` last, so `current_household_id()` cannot be answered by a caller's temporary table; `db/tests/0013` | Merged | [#55](https://github.com/sandjaie/OneLedger/pull/55) |
| `feature/p1-1b-review-routes` | `GET /import/{batch}`, `PATCH /import/{batch}/rows`, `POST /import/{batch}/commit` | Merged | [#56](https://github.com/sandjaie/OneLedger/pull/56) |
| `feature/p1-1c-import-screen` | `/import`: picker, per-file status, parse summary, stage | Merged | [#57](https://github.com/sandjaie/OneLedger/pull/57) |
| `feature/p1-1b-duplicate-override` | Accepting a row marked `duplicate` as a confirmed-distinct override: the dedupe hash is recomputed with the occurrence index advanced (M2 spec §4.7). Added in delivery — the slice covers M2 6a–6d and 6c was not in the review-routes branch | Merged | [#58](https://github.com/sandjaie/OneLedger/pull/58) |
| `feature/p1-1d-review-screen` | grouped review, row decisions, commit | Merged | [#59](https://github.com/sandjaie/OneLedger/pull/59) |

## Phase 1 · slice 1.1 — the acceptance run

Run locally on 9 September 2026 against the 38 card statements in the git-ignored
`pdf-statements/`. No figure, filename or row from any of them appears in this repository.

| Question the run asked | Answer |
| --- | --- |
| Do all 38 import end-to-end with no manual correction? | Yes. 35 are distinct statements and every one imported: layout identified, period derived, reconciliation `matched`, no row failing to normalize, batch committed. The other 3 are byte-identical re-downloads of files already in the set, and each was refused. |
| Do duplicates name the earlier import? | Yes, both kinds. The same file is refused by checksum, naming the date it first arrived and the batch that imported it. The same transactions in a different file stage as `duplicate`, each row naming the transaction it collides with. |
| Was anything corrected by hand? | No layout was edited, no row was hand-fixed, and no `UPDATE` was written to make a batch reconcile. |

The corpus covers two card accounts — 15 monthly statements on one, 20 on the other — across
June 2025 to August 2026 and December 2024 to August 2026 respectively. 1,974 transactions
committed across 35 batches, every batch `completed` and `matched`.

**Flyway.** `flyway migrate` ran for the first time against this tree and applied V14–V17 onto a
ledger standing at V13, validating all seventeen checksums first. A second `migrate` was a no-op.
Migrating from empty on a scratch stack, seeds and all fifteen SQL suites are green. No checksum
problem, no finding.

**Account setup is not statement correction, and one part of it is worth stating.** A card
statement prints its installment plans under each plan's *original purchase date*, so a statement
legitimately carries rows dated before its own period. `commit_import_batch` refuses a row dated
before the account's `opening_balance_date`, and `assert_account_reconciles` needs
`opening_balance` to be the balance carried into the earliest statement held. The two do not refer
to the same instant: the anchor is dated at or before the oldest installment row, while its value
is the balance at the first statement's period start. That is the only sound setting for an
account whose history starts mid-life, and it is what the Accounts screen asks for.

| Finding | Fix |
| --- | --- |
| `db/tests/0005` asserted one active owner session **across the whole table**, so the push hook refused every push on every branch once the owner had signed in | [#65](https://github.com/sandjaie/OneLedger/pull/65) |
| `apiFetch` handed the whole `{"detail": …}` body to `ApiError.detail`, so all nine screens that display it printed the JSON envelope instead of the sentence written for the owner | [#66](https://github.com/sandjaie/OneLedger/pull/66) |
| The review screen formatted a row's frozen `amountBase` with the **account's** currency, printing a base-currency figure under the card's symbol and grouping | [#67](https://github.com/sandjaie/OneLedger/pull/67) |

Each was invisible to the existing suite for the same reason: a fixture that agreed with itself
where reality does not — an empty session table, a bare-text error body, a household reporting in
the currency its card is denominated in.

## Phase 1 · slice 1.2 — the acceptance run

Run locally on 10 September 2026 against all 71 statements in the git-ignored
`pdf-statements/`, across four producers and seven accounts. No figure, filename or row from
any of them appears in this repository.

| Question the run asked | Answer |
| --- | --- |
| Do all 71 gathered files import? | **67 of 71.** The four that did not are all refused by design, and each is named below. 4,184 transactions across 67 batches, every one `completed` and `matched`. |
| Does every account reconcile across its period? | Yes, all seven. Every commit runs `assert_account_reconciles`, so a batch that reached `completed` proved it. |
| Does an encrypted statement import without its password touching disk? | Yes. Six documents are recorded `is_password_protected`; the stored blob is the encrypted original, and the password appears in no filename, no storage path, no description, no reference and no log line. |

| Layout | Files | Imported | Rows |
| --- | --- | --- | --- |
| Card (slice 1.1) | 38 | 35 | 1,974 |
| Current account | 21 | 21 | 307 |
| Passbook | 6 | 5 | 1,292 |
| Ledger (encrypted) | 6 | 6 | 611 |

**The four that did not import, and why each is right.** Three are byte-identical re-downloads
of card statements already in the set, refused by checksum, each naming the earlier import — the
slice 1.1 criterion, unchanged. The fourth is a passbook statement whose *own text layer* is
missing the integer part of one balance figure: the characters are literally spaces, confirmed
against the page's character layer, so no band and no tolerance can recover them. Invariant I8
refuses a statement it cannot verify rather than walking past the row, and that account is
anchored at its earliest importable statement instead.

**Period provenance is now split, as it should be.** 32 of the 67 batches record a period the
statement stated and 35 one derived from the rows — the card layout deliberately derives, because
its installment block prints purchase dates outside the period; the three account layouts state.

| Finding | Fix |
| --- | --- |
| `continuation_rule` could not tell wrapped narration from a closing legal block: 246 prose lines reached 6 of 1,532 rows, and every statement still reconciled | [#76](https://github.com/sandjaie/OneLedger/pull/76) — `blank_date_and_money_columns`, M2 spec §7 item 13 |
| The period gate failed rows a statement books after its period and values inside it, dropping money-bearing rows from the end where the remainder still reconciled | [#77](https://github.com/sandjaie/OneLedger/pull/77) — §7 item 14 |
| `commit_import_batch` verified as of `period_end`, which excludes those same rows while the declared closing includes them | [#77](https://github.com/sandjaie/OneLedger/pull/77) — V18, §7 item 15 |

The first two were invisible to every other check: the statements reconciled `matched` either
way. That is what a run against real files buys.

## Raised and not yet actioned

The four items slice 1.1 raised during delivery were shipped as their own branches:
[#61](https://github.com/sandjaie/OneLedger/pull/61) the identifier and money static check,
[#62](https://github.com/sandjaie/OneLedger/pull/62) `source_row_number NOT NULL`,
[#63](https://github.com/sandjaie/OneLedger/pull/63) the review skills' stale diff budget, and
[#64](https://github.com/sandjaie/OneLedger/pull/64)'s `period_is_derived`. The three the
acceptance run raised were shipped the same way, above. The fourth — a staged batch that could
not be cancelled through the app, so an abandoned one blocked its period for good — was slice
1.2's task 2b, and is delivered.

These are what the run raised and did not action — each in code the fixes it produced do not
touch, recorded here rather than folded in past a diff budget.

| Raised | What it is | Where it belongs |
| --- | --- | --- |
| The review screen renders no period | `import_batch.period_is_derived` reaches the payload and stops there; the import screen does show it. | A small `web/` branch |
| A repeated header line reaches the last row of each page on the encrypted layout | The producer runs whole lines together, so the account holder's locality is a single word centred in the narration column — no band separates it from real narration, and the only pattern that would match it is the owner's own address. 47 of 611 rows gain that one word; no figure, no balance. | A `web/` or layout-mapper branch, once 1.7 can edit a profile |
| `scripts/seed-household.sh` cannot correct the owner it seeded | It refuses a second run once an owner exists, which is right, but an owner seeded before `.env` was filled in carries the example address and no supported path fixes it. Hit during this run's setup. | A `scripts/` branch, or Settings › Household once that screen owns the field |

## Where the rest is written down

| What | Where |
| --- | --- |
| Decisions and deviations taken while building M2 | [M2 spec §8](specs/2026-09-07-m2-statement-importer.md) |
| Dated gate and test-run evidence for M2 | [M2 delivery evidence](reports/2026-09-08-m2-delivery-evidence.md) |
| Dated Phase 0 verification and dependency evidence | [Phase 0 verification](reports/2026-09-06-phase-0-verification.md) |
| Slice 1.1 state, decisions and layout field notes | [slice 1.1 handoff](reports/2026-09-09-slice-1.1-handoff.md) |
| Scope, sequence and acceptance criteria | [product roadmap](../product/roadmap.html) |
| Task blocks, branches and forecasts | the milestone's plan in `plans/` |

## Phase 1 · M2 — the statement importer

Entry criterion satisfied: the FX backfill reaches the 01 Jan 2024 anchor (ADR-0011), verified
2026-09-07 against the live compose database — `fx_rate` spans `2024-01-01` to `2026-09-06`,
1,960 rows across 2 pairs, complete daily coverage.

| M2 task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 1 — CI, backup, and the plan | `chore/m2-ci-and-backup` | Complete | [PR #32](https://github.com/sandjaie/OneLedger/pull/32), merged as `37d52cc` after five Codex review rounds |
| 2 — documents, import batches and layout profiles (V12) | `feature/m2-documents-and-layouts` | Complete | `db/tests/0010_documents_and_layouts.sql`; `api/tests/test_layouts.py` |
| 3 — extraction engine, quirks, account identification | `feature/m2-extraction-engine` | Complete | [PR #34](https://github.com/sandjaie/OneLedger/pull/34), merged as `30e6783` after eight Codex review rounds; branch deleted |
| 4a — staged rows and normalization (V13) | `feature/m2-staged-rows-and-normalization` | Complete | [PR #35](https://github.com/sandjaie/OneLedger/pull/35), merged; `db/tests/0011_staged_transactions.sql`; `api/tests/test_ingest_normalize.py` |
| 4b — dedupe: fingerprint, occurrence index | `feature/m2-t4b-dedupe` | Complete | [PR #38](https://github.com/sandjaie/OneLedger/pull/38), merged after one Codex round; `api/tests/test_ingest_dedupe.py` (37 tests); branch deleted |
| 4c — reconciliation: the four verdicts | `feature/m2-t4c-reconcile` | Complete | [PR #42](https://github.com/sandjaie/OneLedger/pull/42), merged after one Codex round and five local review rounds; `api/tests/test_ingest_reconcile.py` (36 tests); branch deleted |
| 4d — a balance states its own direction | `fix/m2-balance-direction` | Complete | [PR #45](https://github.com/sandjaie/OneLedger/pull/45), merged after one Codex round and five local rounds; split out of 4c on the hard stop; branch deleted |
| 5a — money helpers extracted to `money.py` | `refactor/m2-t5a-money-helpers` | Complete | [PR #47](https://github.com/sandjaie/OneLedger/pull/47), merged as `28ff07d`; branch deleted |
| 5b — FX fetch-on-missing | `feature/m2-t5b-fx-fetch-on-missing` | Complete | [PR #48](https://github.com/sandjaie/OneLedger/pull/48), merged as `1f1aa18`; `api/tests/test_fx_range.py`; branch deleted |
| 5c — document storage | `feature/m2-t5c-document-storage` | Complete | [PR #49](https://github.com/sandjaie/OneLedger/pull/49), merged as `953faf6`; branch deleted |

Tasks 5d–9c were re-planned before dispatch into Phase 1 slices 1.1–1.7 of the
[phase delivery plan](plans/2026-09-09-phase-delivery-plan.md) (9 September 2026); their scope
ships there, none of it dropped.

## Phase 1 · M1b — manual transactions and taxonomy

| M1b task | Status | Evidence |
| --- | --- | --- |
| 12 — categories, merchants, seed taxonomy (V9) | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `db/tests/0008_categories.sql` |
| 13 — transaction ledger and reporting views (V10) | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `db/tests/0009_transactions.sql` |
| 14 — manual transaction API, B3.5 lock | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); `api/tests/test_transactions*.py` |
| 15 — transactions screen, category management | Complete | [PR #31](https://github.com/sandjaie/OneLedger/pull/31); component tests |

## Phase 1 · M1a — trusted financial position

| Phase 1 task | Branch | Status | Evidence |
| --- | --- | --- | --- |
| 1 — restore API package import | `fix/task-1-api-import-syntax` | Complete | [PR #23](https://github.com/sandjaie/OneLedger/pull/23) |
| 2 — currency-agnostic SQL fixtures | `fix/task-2-db-tests-currency-agnostic` | Complete | [PR #25](https://github.com/sandjaie/OneLedger/pull/25) |
| 3 — V6 opening-balance current_balance | `fix/task-3-account-opening-balance` | Complete | [PR #26](https://github.com/sandjaie/OneLedger/pull/26) (with Task 4) |
| 4 — distinct DB vs auth failures | `fix/task-3-account-opening-balance` | Complete | [PR #26](https://github.com/sandjaie/OneLedger/pull/26) (with Task 3) |
| 5 — Compose DB identity | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 6–8) |
| 6 — household configuration API | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 5, 7, 8) |
| 7 — Settings › Household UI | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 5, 6, 8) |
| 8 — account inventory schema | `feature/task-5-8-compose-household-inventory` | Complete | [PR #27](https://github.com/sandjaie/OneLedger/pull/27) (with Tasks 5–7) |
| 9 — account inventory API | `feature/task-9-11-account-inventory` | Complete | [PR #29](https://github.com/sandjaie/OneLedger/pull/29); follow-up `1d7aab5` |
| 10 — account inventory UI | `feature/task-9-11-account-inventory` | Complete | [PR #29](https://github.com/sandjaie/OneLedger/pull/29); follow-up `1d7aab5` |
| 11 — progress evidence and M1a/M1b split | `feature/task-9-11-account-inventory` | Complete | [PR #29](https://github.com/sandjaie/OneLedger/pull/29) |

## Phase 0 — foundations

Implementation is delivered to `main` as [PR #20](https://github.com/sandjaie/OneLedger/pull/20).
Live Google identity verification is the one thing still open.

| Completion-plan task | Status | Evidence |
| --- | --- | --- |
| 1 — web scaffold and API client | Complete | PR #8; timeout follow-up #14 |
| 2 — Google OIDC and revocable sessions | Implementation complete | PRs #9–#11 and #16; live identity gate below |
| 3 — exact money, configured shell and FX inspector | Complete | PRs #12, #13 and #15 |
| 4 — phase exit verification | Partially complete | Automated/synthetic checks and review passed; live identity gate pending |

### Delivered work

| GitHub PR | Completed scope | Original task numbers | Original PR ordinal |
|---|---|---|---|
| #3 | Compose definition, environment template, secret hygiene | 1 | 1 |
| #4 | V1–V2 reference data, identity, RLS | 2–3 | 2 |
| #5 | V3–V4 accounts and FX rates | 4–5 | 3 |
| #6 | FastAPI, `require_owner`, `resolve_owner`, `connect` | 6 | 4 |
| #7 | FX source, peg derivation, carry-forward, backfill; three review rounds reported | 7–8 | 5 |
| #8 | Reviewed Next.js foundation and cookie-aware typed API client | 9 | 6 |
| #9 | Revocable owner-session persistence and strict session verification | 10 partial | 7 partial |
| #10 | Protected logout, CSRF origin checks and household configuration | 10 partial | 7 partial |
| #11 | Google OIDC, state/nonce/PKCE and pinned subject binding | 10 | 7 partial |
| #12 | Exact money formatting and generated API types with drift enforcement | 11 partial | 7 partial |
| #14 | Bounded API requests and compatible caller cancellation | 9 follow-up | 6 follow-up |
| #13 | Authenticated shell, configured currency preference and recovery | 11 partial | 7 partial |
| #15 | Protected Settings FX inspector, exact rates and provenance | 11 | 7 partial |
| #16 | Fixed account chooser on sign-in retry | 10 follow-up | 7 follow-up |
| #17 | Explicit dual-compiler verification and current API boundary comments | Final review follow-up | Closeout |
| #18 | Sequential current-checkout workflow and generic PR template | Workflow | Documentation |
| #21 | Phase 1 plan, spec and git workflow | Phase 1 planning | Documentation |

PR #14 merged before #13 as a prerequisite; original plan PR ordinals are not GitHub PR numbers.
The Phase 0 "59 tests" API evidence predates the import `SyntaxError` fixed by Phase 1 Task 1.

### Verified gates

| Gate | Verified |
| --- | --- |
| API — 59 tests, lint, strict type checks | Yes |
| Web — 33 tests, both TypeScript compilers, lint, generated API contract drift | Yes |
| Dependencies — strict installation, zero reported npm audit vulnerabilities | Yes |
| Database — five migrations, reference seeds and five SQL suites from empty; RLS negative control | Yes |
| Public FX reference history — 980 days, 1,960 rows, complete peg pairs, zero invariant violations | Yes |
| Production images, cached offline startup, synthetic-session browser checks | Yes |
| Local and current-head Cloud review of each delivery; final whole-phase review | Yes |

These describe the verified integration, not a new run whenever this file is edited.

### Remaining Phase 0 exit gates

| Gate | Status |
| --- | --- |
| Configure the Google client credentials and real permitted email privately, matching the seeded owner | Open |
| Verify real permitted-account sign-in and protected application access | Open |
| Verify rejection of a real second Google account, and account-choice recovery | Open |
| Record those live checks here, then close Phase 0 | Open |

The last configuration check found missing Google client credentials and a placeholder permitted
email; no credential value belongs in this repository. Synthetic protocol, session and browser
tests do not substitute for a live Google identity check.

## Later phases

| Phase | Status |
| --- | --- |
| Phase 1 — trusted financial position | Active (this file) |
| Phase 2A — next month | Not started |
| Phase 2B — resilience | Not started |
| Phase 2C — deeper analysis | Not started |
| V2 | Not started |

Scope and sequencing remain in the [master plan](plans/implementation-plan.md).
