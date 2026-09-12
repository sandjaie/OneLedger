# Slice 1.3 — classification and transfers

> **Historical numbering:** This delivered plan retains the identifier used at review time. Use
> the [canonical crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)
> for current Phase N.M identifiers.

Execution map for Phase 1 slice 1.3, written against the tree at `6c0a853` on 10 September 2026.
The phase delivery plan
([current crosswalk](../../product/master-implementation-plan.html#historical-numbering-crosswalk)) owns the historical outcome mapping and acceptance;
this file owns how it is executed and is what implementers are dispatched from.

## What the survey found

Slice 1.2's plan found three of its four stated engine gaps already built. This slice's survey
found the opposite: **the schema anticipates both engines and neither engine exists.**

| Capability | State | Where |
|---|---|---|
| `classified_by` enum, with every cascade step already a member | **Exists** | V10 — `user_manual`, `user_rule`, `merchant_alias`, `recurring_series`, `prior_match`, `seed_rule`, `unclassified`, `ai` |
| `transaction.classified_by` / `classification_rule_id` / `classification_confidence` | **Exists, written** | V10; `PATCH /transactions/{id}` stamps `user_manual` at 1.000 and clears the rule id, `unclassified` at 0.000 when cleared |
| `category`, `merchant`, `merchant_alias`, `pattern_type`, `category_override` | **Exists** | V9, V11; 26 root and 104 child seed categories; `GET/POST/PATCH /categories` and Settings › Categories |
| `staged_transaction` proposal columns | **Exists, never written** | V13 — `proposed_category_id`, `proposed_merchant_id`, `proposed_payment_method`, `proposed_country`, `classified_by`, `classification_rule_id`, `classification_confidence` |
| `staged_transaction` transfer-candidacy columns | **Exists, never written** | V13 — `proposed_transfer_peer_staged_id`, `proposed_transfer_peer_txn_id`, `transfer_match_confidence`, `proposed_transfer_kind` (TEXT + CHECK; the enum is deferred) |
| `commit_import_batch` carries all three classification columns staged → committed | **Exists** | V14, re-issued by V18 |
| `v_transaction_reportable` excludes `transfer_id IS NOT NULL` and both transfer types | **Exists** | V10 — I3 and I4 are already structural; this slice only has to prove them live |
| `import_status` carries `classifying` and `matching_transfers` | **Exists** | V12 |
| `classification_rule` table | **Missing** | specified in `schema.md` `0009`; `classification_rule_id` is a bare `UUID` everywhere |
| `transfer` table, `transfer_kind`, `transfer_status` | **Missing** | specified in `schema.md` `0010`; `transaction.transfer_id` is a bare `UUID` |
| The cascade itself (`classify.py`) | **Missing** | `pipeline.py`'s `classify` import is **dedupe's**, not this one — a name collision to avoid |
| The matcher (`transfers.py`) | **Missing** | nothing scores a peer anywhere |
| Any seed rule set | **Missing** | `db/seeds/` holds four layout profiles and `institutions.sql`; no `rules/` |
| Rule or transfer routes | **Missing** | the route surface is accounts, auth, categories, currencies, fx, household, ingest, layouts, transactions |

So the slice writes two engines, two tables and their API and screens, onto columns that are
already the right shape. Nothing here needs a schema redesign; `schema.md` `0009` and `0010` are
the target and the migrations implement them as written, adding only the deferred foreign keys
that `schema.md` `0018` says arrive with the target table.

**Deviation recorded: nine branches rather than two, and no transfer proposal at staging.**
The phase plan's outcome and acceptance are unchanged. Two reasons for the split:

1. Two engines, two tables, four route groups and two screens do not fit one branch each — the
   1.1 precedent is nine branches for one slice, for the same reason.
2. **A transfer's two legs are almost never in the same batch.** A card payment's debit is on
   the bank statement and its credit on the card statement; a remittance's legs are two
   countries apart. Proposing peers *within* a staged batch therefore buys close to nothing,
   and confirming across a batch boundary is impossible anyway because a staged row is not yet
   a `transaction` and cannot carry a `transfer_id`. Candidates are computed **over committed
   transactions** and confirmed from the transactions screen, which is where the phase plan
   also puts manual link and unlink. ADR-0007 is satisfied exactly: candidates are scored,
   nothing links without a click. `staged_transaction`'s four candidacy columns stay unwritten,
   as they are today, and Phase 2 may fill them once pair history makes a staged proposal worth
   showing.

   **Settled, 10 September 2026.** ADR-0007's decision line put confirmation "in the review
   queue", which the two-batch fact above makes unsatisfiable, so it is **superseded by
   ADR-0017** rather than read around: same decision — scored candidates, pre-ticked,
   nothing links without a click — confirmed on the transactions screen. Task 3b1 carries
   ADR-0017; nothing in 3b depends on the wording of ADR-0007 any more.

## The seed rule set names no institution

ADR-0012 and `scripts/check-identifiers.py` are the constraint, and `classification.html` §3's
country packs (`regions/ae.yaml`, `regions/in.yaml`, ~120 rules of merchant names) are **Phase
2**, per the phase plan's own scope line for this slice. What 1.3 ships is **one pack of about
thirty rules matching banking narration, not merchants** — salary credits, card payments, ATM
withdrawals, interest credits, bank charges, reversals, and the domestic-transfer verbs. Those
are vocabulary every statement in the corpus uses and none of them is an institution, a
currency or a country.

Merchant rules are the owner's own, created through rule CRUD from what the ledger actually
contains. That is the honest scope of "a personal seed set", it removes the region-pack
question entirely, and it keeps `check-identifiers.py` green without an exemption.

The pack is **data**: a JSON file under `db/seeds/rules/`, loaded through `POST /rules` exactly
as a layout profile is loaded through `POST /layouts`, and validated by a generic test that
reads every file in that directory — the pattern `api/tests/test_seeded_layouts.py` already
sets. A rule file carries no id, no household id and no category id: it names categories by
**slug**, which is stable across databases, and the loader resolves them. Not a `psql` seed and
not a migration, for the same reason `db/seeds/README.md` gives for the layouts.

`check-diff-size.sh` skips `.json`, so the pack is free against the budget. It is not free
against review: keep it to about thirty rules and let the owner add the rest through the screen.

## What the acceptance run is, and where it happens

> A real remittance and a card payment are detected and excluded; monthly spending for one
> hand-tallied month matches exactly.

All three are **local**, on the owner's machine, against the ledger built from the git-ignored
`pdf-statements/` — 4,184 transactions across 7 accounts. A remote checkout has neither the
statements nor the ledger.

There is no reporting endpoint yet — `GET /dashboard` is slice 1.4 — so the spending figure for
the hand-tallied month is read with one query over `v_transaction_reportable`, summing
`amount_base` for the month, and compared against the month tallied by hand from the statement.
1.4b must reproduce the same number from the same view; if it does not, that is 1.4b's defect.

**No real figure, filename, extract or row enters this repository on any branch** — not in a
commit message, a PR body, a test fixture or a rule pattern. A fixture reproduces the
*arithmetic* with invented numbers; `db/seeds/README.md` sets the house style and the run is
recorded in `docs/superpowers/progress.md` as slices 1.1 and 1.2 were.

## Sequence and migrations

`3a1 → 3a2 → 3a3 → 3a4 → 3a5`, then `3b1 → 3b2 → 3b3 → 3b4`. The `3b` chain may start once
`3a1` has merged; `3b3` needs `3a3` for the rule-hint signal and `3b4` needs `3b3`.

Migrations are forward-only and Flyway-checksummed. Two are needed, in this order:

| Migration | Task | Does |
|---|---|---|
| `V19__classification_rules.sql` | 3a1 | `classification_rule`, its RLS, its priority index, and the deferred FKs on `transaction.classification_rule_id` and `staged_transaction.classification_rule_id` |
| `V20__transfers.sql` | 3b1 | `transfer_kind`, `transfer_status`, `transfer`, its two partial unique indexes, its RLS, `confirm_transfer()` and `unlink_transfer()`, the deferred FK on `transaction.transfer_id`, and `staged_transaction.proposed_transfer_kind` converted from `TEXT` + `CHECK` to the enum |

**V19 is the next free number at the time of writing.** If anything else merges first, take the
next free number — the file name is not a contract, the order is. Never edit an applied
migration. A table change edits the migration, `schema.md` and `data-model.html` in one commit.

---

## Task 3a1 — the rule store

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3a1-classification-rules`
**Base and PR target:** `develop`
**Migration:** `V19__classification_rules.sql` — creates `classification_rule` per `schema.md`
`0009`, its RLS policy, `idx_rule_priority`, and the two deferred foreign keys `schema.md`
`0018` says arrive with the target table.
**Depends on:** nothing.
**Forecast:** ~140 production, ~200 test.

**Outcome.** A household can hold rules, and every column the cascade and the API need exists
with the right type, isolation and constraints.

**Files.** Create `db/migrations/V19__classification_rules.sql` and `db/tests/0017_rules.sql`;
modify `docs/superpowers/schema.md` (move `0009` under an applied heading) and
`docs/design/data-model.html` in the same commit.

**Contracts.** `schema.md` `0009` verbatim, including `has_matcher` and `has_outcome`. Priority
ascending, user rules defaulting to 100 and seed rules to 500 so a user rule beats a seed rule
without either knowing about the other (`classification.html` §2). `origin` is
`seed | user_created | learned_from_correction`. Adding the FK to `transaction` and
`staged_transaction` is what `schema.md` `0018` reserves for this migration.

**Invariants / security.** RLS enabled with `household_id = current_household_id()`, following
V9's shape; there is no shared seed row here — a seeded rule belongs to the household that
loaded it, so `household_id` is `NOT NULL`. `set_category_id` and `set_merchant_id` must not be
able to name another household's row; V10's `assert_transaction_currency_matches_account`
already refuses a foreign category on the ledger, and the equivalent check belongs on the rule
at write time (3a3), not in the FK. No currency, institution or country name becomes an
identifier. No money arithmetic here; `match_amount_*` are `NUMERIC(20,4)`.

**Required test behaviours (failing first).**
- RLS is enabled and a second household's rules are invisible under a bound user.
- `has_matcher` refuses a rule with outcomes and no matcher; `has_outcome` refuses the reverse.
- A rule with only `set_is_transfer_candidate` satisfies `has_outcome`.
- The FK on `transaction.classification_rule_id` exists and refuses a rule id that does not.
- Deleting a rule that a transaction names is refused (or nulls, whichever the FK states —
  state it and test the stated one), so provenance never dangles.
- Migration from empty applies V1–V19 and every `db/tests/*.sql` is green.

**Validation.** `docker compose run --rm flyway migrate` from empty, every `db/tests/*.sql`,
`scripts/check-docs.py`, `scripts/check-identifiers.py`.

**Acceptance.** The rule table exists with `schema.md` `0009`'s shape, is household-isolated,
and `schema.md` and `data-model.html` agree with it.

---

## Task 3a2 — the cascade, as a function of values

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3a2-classification-cascade`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3a1 merged (for the rule shape it consumes; the module itself touches no
database).
**Forecast:** ~260 production, ~520 test.

**Outcome.** `classify(row, rules, aliases, priors) -> Classification` implements
`classification.html` §1's cascade deterministically, and is testable on values with no
database and no HTTP.

**Files.** Create `api/oneledger/ingest/classify.py` and `api/tests/test_ingest_classify.py`.

**Contracts.** The cascade in order: **explicit user override → user rule (priority ascending)
→ merchant alias → prior match, same account and text → seed rule → unclassified.** First match
wins; evaluation stops. `recurring_series` is Phase 2 and is absent, not stubbed. The returned
value carries `category_id`, `merchant_id`, `txn_type`, `payment_method`, `country`, `tags`,
`classified_by`, `classification_rule_id` and `classification_confidence`, plus the transfer
hint (`is_transfer_candidate`, `counterparty_account_id`) that 3b2 consumes.

Confidence is `classification.html` §1's table exactly: override 1.000, user rule 0.980, alias
0.950, prior match exact 0.900, prior match fuzzy 0.700, seed rule 0.800, unclassified 0.000.
The pre-tick threshold is 0.800.

A rule matches when **every non-null condition** matches; absent conditions are ignored.
`pattern_type` is `exact | prefix | contains | regex`; a regex is compiled once and a pattern
that can catastrophically backtrack is refused at creation (3a3), not silently at match time.

**Invariants / security.** `api/oneledger/ingest/` may not import from `routes/`
(`testing-and-data-quality.html` §2), so this module takes rules, aliases and priors as
arguments and issues no query. **Name it `classify.py` but do not export a bare `classify` —
`pipeline.py` already imports `classify` from `dedupe`,** and two `classify`s in one module is
how the wrong one gets called. Money comparisons are `Decimal`; `match_amount_min/max/exact`
compare against the row's signed `amount` with the direction matcher deciding the sign, and no
float appears. No currency, institution or country name becomes an identifier — the seed
patterns live in data, not in this module.

**Required test behaviours (failing first).**
- Each cascade step wins over every step below it, and stops evaluation — six tests, one per
  step, each asserting the `classified_by` and the confidence.
- Two user rules at priorities 10 and 100 both matching: the 10 wins.
- A seed rule at 500 and a user rule at 100 both matching: the user rule wins, with no
  knowledge of the other.
- A rule with a null matcher for a field matches a row of any value in that field.
- Amount, direction and account matchers each exclude a row that fails only them.
- A row matching nothing returns `unclassified` at 0.000 and no rule id.
- Prior match: the same account and cleaned description as a previously classified row returns
  that row's category at 0.900; a fuzzy match at ≥ 0.85 returns 0.700; below that, nothing.
- The engine abstains rather than guesses: no path returns a category below 0.700.

**Validation.** `ruff`, `mypy --strict`, `pytest -q`, `scripts/check-identifiers.py`.

**Acceptance.** The cascade is proved on values, in order, with no database in the test.

---

## Task 3a3 — rules through the app

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3a3-rules-api`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3a1 and 3a2 merged.
**Forecast:** ~400 production, ~620 test. **If it overruns**, split at the seam already in the
contract: `feature/p1-3a3a-rules-crud` (the five CRUD routes and their validation) then
`feature/p1-3a3b-preview-and-backfill` (`POST /rules/preview` and `POST /classify`). The second
needs the first; nothing else needs the second.

**Outcome.** The owner creates, edits, reorders, deactivates and deletes rules from the
application, sees what a candidate rule would match before creating it, and can run the cascade
over rows that have no classification yet.

**Files.** Create `api/oneledger/routes/rules.py`, `api/tests/test_rules.py` and
`api/tests/test_rules_live.py`; modify `api/oneledger/main.py` and
`docs/design/api-contracts.html`; regenerate `web/types/api/`.

**Contracts.** `GET /rules`, `POST /rules`, `PATCH /rules/{id}` (full replacement of the
editable fields, as `PATCH /accounts/{id}` is), `DELETE /rules/{id}`, and two read-then-act
routes:

- `POST /rules/preview` — a candidate rule body in, the **affected count and a bounded sample
  of the rows it would match** out. Reads the ledger over the base table, which is the
  deliberate non-report read `GET /transactions` already makes; it is not a spending figure.
- `POST /classify` — runs the cascade over the household's transactions **whose
  `classified_by` is `unclassified`**, and over nothing else. `preview=true` returns the count
  and a sample and writes nothing; without it, it applies and returns the count changed.

**Invariants / security.** Every route behind `require_owner()`, filtered by the owner's
household before anything else; another household's rule is 404, exactly as its siblings are.
**Rules never silently change past data** — this is the invariant the slice is judged on, and
`POST /classify` is how it is kept: it can only fill a row that has no answer, it can never
overwrite `user_manual`, `user_rule`, `merchant_alias`, `prior_match` or `seed_rule`, and it
never runs as a side effect of creating or editing a rule. **Retroactive re-classification of
rows that already have an answer is Phase 2's learn-from-correction and is out of scope here —
confirmed by the owner, 10 September 2026.** Backfilling the rows that have no answer is not
optional: the 4,184 transactions already in the ledger are all `unclassified`, and without
`POST /classify` they stay that way and this slice's acceptance cannot be measured at all.
`set_category_id` and `set_merchant_id` are checked against the
seed set and this household exactly as `_assert_category_allowed` checks them. A regex pattern
is compiled at creation and refused if it can catastrophically backtrack. No string-interpolated
SQL; a sort or filter key selects a static fragment from a dictionary. Money matchers arrive as
decimal strings and become `Decimal`.

**Required test behaviours (failing first).**
- Each route is refused without a session, and answers 404 for another household's rule.
- Creating a rule changes **no** existing transaction — asserted directly, on a ledger that
  already holds rows the rule matches.
- `POST /rules/preview` returns a count matching a hand-counted fixture, and writes nothing.
- `POST /classify` with `preview=true` writes nothing; without it, it fills only unclassified
  rows and leaves a `user_manual` row untouched even when a rule matches it.
- A rule whose category belongs to another household is 422.
- A catastrophically backtracking regex is refused at creation.
- Priority ordering decides between two matching rules, through the route, end to end.

**Validation.** `ruff`, `mypy --strict`, `pytest -q`, the live suite, `check-docs.py`,
`scripts/check-identifiers.py`, the generated-type check.

**Acceptance.** Rules are managed from the API with no `psql`, and creating one is provably
incapable of moving a figure.

---

## Task 3a4 — the seed pack, and the cascade at import

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3a4-seed-rules-and-import`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3a3 merged.
**Forecast:** ~220 production, ~440 test.

**Outcome.** A staged batch arrives pre-classified: every staged row carries the proposal the
cascade produced, its provenance and its confidence, and the review payload shows them.

**Files.** Create `db/seeds/rules/banking-narration.json` and
`api/tests/test_seeded_rules.py`; modify `db/seeds/README.md`,
`api/oneledger/ingest/pipeline.py` (the `_STAGED` insert and the analysis that feeds it),
`api/oneledger/routes/ingest.py` (`ReviewRow`, `_ROWS`, `_grouped`),
`api/tests/test_ingest_pipeline.py`, `api/tests/test_import_review_live.py` and
`docs/design/api-contracts.html`; regenerate `web/types/api/`.

**Contracts.** The cascade runs at `statement-ingestion.html` stage 6–7, **after** FX freezing
and **before** staging, and writes `proposed_category_id`, `proposed_merchant_id`,
`proposed_payment_method`, `proposed_country`, `classified_by`, `classification_rule_id` and
`classification_confidence` onto each staged row. Those columns exist (V13) and
`commit_import_batch` already carries all three provenance columns through to the ledger (V14,
re-issued by V18), so **no migration and no change to the commit function is needed** — verify
that rather than assume it.

The seed file is a list of `POST /rules` bodies naming categories by **slug**, carrying no id
and no institution name. `api/tests/test_seeded_rules.py` validates every file in
`db/seeds/rules/` through the create model on every run, refuses an id and refuses an unknown
slug, so a pack dropped in there needs no test of its own — the shape
`api/tests/test_seeded_layouts.py` already sets.

`ReviewRow` gains `proposedCategoryId`, `proposedCategoryName`, `classifiedBy` and
`classificationConfidence`. The `PATCH /import/{batch}/rows` vocabulary stays exactly `accepted`
and `rejected`; **categorising inside review is not in this slice** — the proposal is shown, and
a correction is made on the transactions screen, which already does it. Bulk categorise-in-review
with the keyboard interactions of `statement-ingestion.html` §10 is a later `web/` branch.

**Invariants / security.** The row records which rule fired (`classification_rule_id`) — that
is what makes "why is this Food & Dining?" answerable. Classification proposes only; nothing
commits without the review click. Redaction on the way out is unchanged: `_screen` still runs
over every narration field, and a category **name** now leaves the service, so confirm it is a
category name and never a merchant string lifted from a document. No institution name enters
the pack. Money is untouched by this task.

**Required test behaviours (failing first).**
- A staged row matching a seed rule carries that rule's category, `seed_rule` and 0.800.
- A staged row matching a user rule carries `user_rule` and 0.980, and beats the seed rule.
- A staged row matching nothing carries `unclassified`, 0.000 and a null rule id.
- The classification survives commit: the committed `transaction` carries the same
  `classified_by`, `classification_rule_id` and `classification_confidence`.
- The review payload exposes the proposal and its confidence, redaction unchanged.
- Every file in `db/seeds/rules/` validates, names no id, and names only resolvable slugs.
- `scripts/check-identifiers.py` is green with the pack in the tree.

**Validation.** `/verify` in full, including migrations from empty and every `db/tests/*.sql`.

**Acceptance.** A synthetic statement imports with its rows already classified, and the
provenance the ledger stores names the rule that decided.

---

## Task 3a5 — Settings › Rules, and the proposal on screen

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3a5-rules-screen`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3a4 merged.
**Forecast:** ~420 production, ~460 test.

**Outcome.** Rules are a screen, not a terminal. The owner lists, creates, edits, reorders by
priority, deactivates and deletes rules; sees the affected count before creating one; and runs
the unclassified pass explicitly. The review screen shows what the cascade proposed.

**Files.** Create `web/app/settings/rules-manager.tsx` and its test; modify
`web/app/settings/page.tsx`, `web/app/import/review-screen.tsx` and its test.

**Contracts.** The screen calls the 3a3 routes and nothing else. Creating a rule shows the
`POST /rules/preview` count **before** the create, with the sample rows expandable — the count
is the safeguard against an over-general pattern (`classification.html` §4). The unclassified
pass is an explicit button that states what it will do and what it cannot touch, and shows the
preview count first. The review screen renders the proposed category, `classifiedBy` and the
confidence per row, read-only.

**Invariants / security.** **TypeScript formats money and does no arithmetic on it** — the
amount matchers are decimal strings in and decimal strings out, and no count, sum or comparison
of a money value happens in the browser; `scripts/check-identifiers.py` fails the build on
`parseFloat`, `Number()` and arithmetic on a money-named property. The currency for any amount
matcher comes from the account or household row, never a hardcoded union (ADR-0012). The UI is
never the authorization boundary: it hides nothing the API would have allowed and allows nothing
the API would have refused.

**Required test behaviours (failing first).**
- The list renders in priority order and shows each rule's matcher and outcome in words.
- Creating a rule shows the affected count first and does not create until confirmed.
- Editing a rule sends a full replacement; deactivating does not delete.
- The unclassified pass states that it cannot change a row that already has a classification,
  shows the count, and only then applies.
- A refused create renders the service's own sentence, not a JSON envelope.
- The review screen shows the proposal and its confidence, and offers no categorisation control
  (that is deliberate, and the test says so).

**Validation.** `npm run lint`, `typecheck`, `typecheck:compat`, `vitest run`, `npm run build`,
the bundle secret scan, `scripts/check-identifiers.py`.

**Acceptance.** A rule is created, previewed and applied entirely from the application.

---

## Task 3b1 — the transfer store

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3b1-transfer-schema`
**Base and PR target:** `develop`
**Migration:** `V20__transfers.sql` — `transfer_kind`, `transfer_status`, `transfer` per
`schema.md` `0010`, its two partial unique indexes, RLS, `confirm_transfer()` and
`unlink_transfer()`, the deferred FK on `transaction.transfer_id`, and
`staged_transaction.proposed_transfer_kind` converted from `TEXT` + `CHECK` to `transfer_kind`.
**Depends on:** 3a1 merged (ordering only).
**Forecast:** ~280 production, ~300 test.

**Outcome.** A confirmed transfer is one atomic act: the `transfer` row and both legs'
`transfer_id` and `txn_type` move together or not at all, and unlinking reverses it cleanly.

**Files.** Create `db/migrations/V20__transfers.sql` and `db/tests/0018_transfers.sql`; modify
`docs/superpowers/schema.md` and `docs/design/data-model.html` in the same commit.

**Contracts.** `schema.md` `0010` verbatim, including `different_accounts`, `amounts_positive`,
`confirmed_has_legs` and the two partial unique indexes that make a transaction a leg of at most
one **confirmed** transfer. `confirm_transfer(p_transfer_id)` sets both legs' `transfer_id` and
`txn_type` (`transfer_out` on the source, `transfer_in` on the destination) and the transfer's
`status` in one statement set; `unlink_transfer(p_transfer_id)` clears both legs, restores each
leg's type from its sign (`amount > 0 → income`, `< 0 → expense`, the rule `normalize.py` already
uses) and sets `status = 'rejected'` so the matcher does not re-propose the pair.

`transaction.transfer_type_agrees` (V10) means `transfer_id` and `txn_type` **must** move in the
same `UPDATE`; that is why this is a function and not three route statements
(`api-contracts.html` §3).

**Invariants / security.** Both functions are `SECURITY DEFINER`, both call `assert_is_owner()`
first, and both declare `SET search_path = public, pg_temp` with `pg_temp` **explicitly last** —
V14 and V15 set that pattern and V18 kept it. RLS on `transfer` with
`household_id = current_household_id()`. Both legs must belong to the caller's household and to
the transfer's stated accounts; a leg from another household is refused inside the function, not
by the route. I3 needs no new work — `v_transaction_reportable` already excludes both a non-null
`transfer_id` and both transfer types — but I3 and I4 are **tested here**, because a view that
excludes them is not the same as a confirmation path that sets them. No money is recomputed: the
transfer's `source_amount` and `destination_amount` are `abs()` of the legs' stated amounts and
the legs themselves are never edited (I1/I5, enforced by V10's immutability trigger, which this
function must not trip — it changes only `txn_type`, `transfer_id` and `updated_at`).

**Required test behaviours (failing first).**
- **I3, as `testing-and-data-quality.html` §1 states it:** confirming a remittance drops the
  spending sum over `v_transaction_reportable` by exactly the source amount; unlinking restores
  it exactly.
- **I4:** a −310 purchase and a +3,900 payment confirmed as a transfer yield spending of
  exactly 310.
- A transaction cannot be a leg of two confirmed transfers — the partial unique index refuses it.
- `confirm_transfer` on legs from another household raises; a caller who is not the owner raises.
- Unlinking restores both legs' types from their signs and leaves the transfer `rejected`.
- The money-immutability trigger does not fire on either function.
- `proposed_transfer_kind` is the enum, and its previous seven values all still load.
- Migration from empty applies V1–V20 and every `db/tests/*.sql` is green.

**Validation.** `docker compose run --rm flyway migrate` from empty and onto the standing
ledger, every `db/tests/*.sql`, `check-docs.py`, `scripts/check-identifiers.py`.

**Acceptance.** Confirming and unlinking are atomic, and I3 and I4 are proved live rather than
assumed from the view's text.

---

## Task 3b2 — the matcher, as a function of values

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3b2-transfer-matching`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3b1 merged.
**Forecast:** ~320 production, ~560 test.

**Outcome.** `score_match(debit, credit, ...) -> float` implements
`transfers-and-reconciliation.html` §3's scoring, FX-aware and kind-aware, on values.

**Files.** Create `api/oneledger/ingest/transfers.py` and
`api/tests/test_ingest_transfers.py`.

**Contracts.** The weighted sum exactly as §3 states it: amount 0.35, date 0.25, description
0.20, pair history 0.15, rule hint 0.05, capped at 1.0. Zero when the accounts are the same, or
when the debit is not negative or the credit not positive.

- **Amount, FX-aware.** Same currency: exact scores 1.0, otherwise `max(0, 1 − diff·20)` so a
  deducted fee still scores. Cross-currency: convert the source at the **market rate for the
  debit's booked date** and score `max(0, 1 − diff/0.05)` — 5% is the tolerance, because an
  exchange house's spread is 0.5–3% and a strict match misses every genuine remittance.
- **Date, kind-specific windows.** Internal same bank 0–1 days, domestic cross-bank 0–3,
  cross-border remittance 0–7, card payment 0–5. Same day is 1.0, decaying to 0 at the window's
  edge.
- **Description.** Token overlap after cleaning, plus the direction keywords
  `transfers-and-reconciliation.html` §3 lists. A counterparty name or partial account number
  appearing in the other leg is strong evidence.
- **Pair history.** Confirmed transfers already recorded between the same ordered account pair.
- **Rule hint.** A 3a2 classification carrying `is_transfer_candidate` and a
  `counterparty_account_id` that names the peer's account.

Thresholds are presentation, not behaviour: ≥ 0.90 pre-ticked, 0.70–0.89 shown, 0.50–0.69 weak,
below 0.50 not shown. **No threshold auto-confirms** (ADR-0007).

**Invariants / security.** `ingest/` may not import from `routes/`, so the market rate, the pair
history and the rule hint arrive as arguments; this module issues no query. Every money value is
`Decimal`; the score is a float and is never money. The keyword list holds banking verbs and no
institution, currency or country name (ADR-0012). Nothing in this module writes anything —
scoring proposes, and only `confirm_transfer` links.

**Required test behaviours (failing first).**
- A cross-currency remittance with a 2% spread scores above 0.90; one at 8% scores 0 on amount.
- A same-currency pair differing by a deducted fee still scores well; one differing by 20% does
  not.
- Each kind's date window: same day 1.0, one day past the window 0.0, on each of the four kinds.
- Two similar-sized unrelated transactions on the same day, different accounts, no description
  overlap and no pair history, score **below 0.50** — the false-positive case ADR-0007 exists
  for.
- Same account, wrong signs, or a zero amount each score exactly 0.0.
- The rule hint moves a borderline card payment above 0.95, and its absence does not zero it.
- Pair history raises a repeat pair above a first-time pair, all else equal.

**Validation.** `ruff`, `mypy --strict`, `pytest -q`, `scripts/check-identifiers.py`.

**Acceptance.** The scoring is proved on values against every band in §3, including the case
that must **not** match.

---

## Task 3b3 — transfers through the app

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3b3-transfer-api`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3a3 and 3b2 merged, and ADR-0017 landed with 3b1 — this task is where
"candidates over committed transactions" becomes a contract.
**Forecast:** ~440 production, ~660 test. **If it overruns**, split read from write:
`feature/p1-3b3a-transfer-reads` (the three GET routes and the scorer's wire shape) then
`feature/p1-3b3b-transfer-writes` (`POST`, `DELETE` and the FX cost). The FX cost is the part most
likely to grow, and it is entirely inside the second.

**Outcome.** The owner sees scored candidate pairs across the whole ledger, confirms or rejects
each one, links a pair by hand, and unlinks one that was wrong. Confirming a cross-currency
transfer records what it cost.

**Files.** Create `api/oneledger/routes/transfers.py`, `api/tests/test_transfers.py` and
`api/tests/test_transfers_live.py`; modify `api/oneledger/main.py` and
`docs/design/api-contracts.html`; regenerate `web/types/api/`.

**Contracts.**

- `GET /transfers` — confirmed and rejected transfers, filtered and paged.
- `GET /transfers/candidates` — scored unlinked pairs across the household's committed
  transactions, ranked, banded by the §3 thresholds, paged, bounded by a date window so the
  scan is not the whole ledger against itself.
- `GET /transactions/{id}/transfer-candidates` — ranked peers for one transaction, for manual
  linking, from the same scorer (§6: "not a second implementation").
- `POST /transfers` — confirm a pair or create one by hand, through `confirm_transfer`.
  `match_method` is `auto` for a proposed candidate, `user_created` when the owner picked.
- `DELETE /transfers/{id}` — `unlink_transfer`; the row is retained as `rejected`.

**FX cost, on confirmation only, for a cross-currency transfer.** The five figures and the
formula that produces each are specified in
[multi-currency-and-fx.html](../../design/multi-currency-and-fx.html) §5, which owns them;
this task implements that section and does not restate it, because a formula written twice is
a formula that drifts. What this task adds: they are computed in **Postgres `NUMERIC`**, stored
on the transfer row at confirmation, and never recomputed on read.

**Invariants / security.** Every route behind `require_owner()`, household-filtered first;
another household's transfer or transaction is 404. Nothing auto-links: no code path calls
`confirm_transfer` without an explicit request naming the pair (ADR-0007). Where `market_rate`
is carried forward the derived cost is **flagged approximate** and the flag reaches the wire
(I7 — a cost derived from a carried rate is not a cost anyone observed). Money is `Decimal` in
Python, `NUMERIC(20,4)` in Postgres, a **string** on the wire; rates are `NUMERIC(20,10)`.
Aggregation is in Postgres. No string-interpolated SQL.

**Required test behaviours (failing first).**
- Listing candidates writes nothing at all — no `transfer` row, no `transaction` update.
- Confirming a remittance drops the spending sum by exactly the source amount, through the
  route; deleting the link restores it exactly and does **not** re-propose the rejected pair.
- A cross-currency confirmation stores implied rate, market rate, spread percentage, spread cost
  and total cost, matching a hand computation **exactly** (`testing-and-data-quality.html` §4).
- A confirmation whose market rate is carried forward is flagged approximate on the wire.
- Confirming a pair where one leg is already a confirmed leg is refused.
- Another household's transaction cannot be named as a peer — 404, not 422.
- Every money field on the wire is a string; no route returns a JSON number for money.

**Validation.** `ruff`, `mypy --strict`, `pytest -q`, the live suite, `check-docs.py`,
`scripts/check-identifiers.py`, the generated-type check.

**Acceptance.** A remittance and a card payment are each confirmed through the API and each
leaves spending exactly as much lower as the source amount.

---

## Task 3b4 — link and unlink from the ledger

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`
**Branch:** `feature/p1-3b4-transfer-screen`
**Base and PR target:** `develop`
**Migration:** none.
**Depends on:** 3b3 merged.
**Forecast:** ~420 production, ~440 test.

**Outcome.** The transactions screen is where a transfer is confirmed: a candidate queue grouped
by confidence band, a per-row "link as transfer" with ranked peers, and unlink.

**Files.** Modify `web/app/transactions/transactions-screen.tsx` and its test; create
`web/app/transactions/transfer-panel.tsx` and its test.

**Contracts.** The queue renders `GET /transfers/candidates` grouped by the §3 bands, with the
≥ 0.90 band pre-ticked and every band requiring a click. Selecting a transaction offers
`GET /transactions/{id}/transfer-candidates` ranked, with search. Confirming posts once;
unlinking deletes once. A confirmed cross-currency transfer shows what it cost — the
implied rate, the spread and the fee, as the API computed them — and says so when the market
rate was carried forward.

**Invariants / security.** **The browser formats money and computes none of it.** The spread,
the cost and every amount arrive as decimal strings and are rendered through `formatMoney` with
the currency row that owns them; `scripts/check-identifiers.py` fails the build on `parseFloat`,
`Number()` or arithmetic on a money-named property, and the rate is a money-named property too.
A leg denominated in the account's currency is formatted with **that account's** currency row and
the frozen base figure with the household's — the defect slice 1.1's run found (#67) and the
reason this is stated. Nothing pre-confirms: the pre-tick is a checkbox state, and the POST
happens on the owner's click.

**Required test behaviours (failing first).**
- The queue groups by band and pre-ticks only ≥ 0.90; nothing posts on render.
- Confirming posts once and re-renders both legs as a transfer.
- Unlinking posts once and returns both legs to their prior presentation.
- The approximate flag renders when the market rate was carried forward, and not otherwise.
- Each leg's amount is formatted with its own account's currency and the base figure with the
  household's.
- A refused confirmation renders the service's sentence.

**Validation.** `npm run lint`, `typecheck`, `typecheck:compat`, `vitest run`, `npm run build`,
the bundle secret scan, `scripts/check-identifiers.py`.

**Acceptance.** A remittance and a card payment are confirmed from the screen, with no `psql`
and no API client.

---

## The local acceptance run

Run on the owner's machine after 3b4 merges, against the standing ledger. It answers three
questions and is recorded in `docs/superpowers/progress.md` with no real figure, filename or row.

1. **Is a real remittance detected and excluded?** It appears in the candidate queue above 0.90,
   confirms, and the month's spending falls by exactly the source amount.
2. **Is a card payment detected and excluded?** The same, and spending falls by exactly the
   payment.
3. **Does monthly spending for one hand-tallied month match exactly?** One month is tallied by
   hand from the statements and compared against `SUM(amount_base)` over
   `v_transaction_reportable` for that month. Exact, not approximate.

If (3) does not match, the difference is the finding — a missed transfer, a wrong sign, a row
excluded that should not be — and it is diagnosed before the slice is accepted, not written off.

## What is not in this slice

The 120-rule merchant packs and the country-pack loader; learn-from-correction (the inline
"create a rule from this correction" prompt, the pattern suggester, rule-health analytics and
retroactive re-classification of rows that already have an answer) — all Phase 2. Categorising
inside the review screen, with `statement-ingestion.html` §10's keyboard interactions. Transfer
candidacy proposed on staged rows. Split transfers (one debit, several credits). The pending-transfer
ageing report, which belongs with `GET /doctor` in Phase 2. Cards, net worth and the dashboard
are slice 1.4.
