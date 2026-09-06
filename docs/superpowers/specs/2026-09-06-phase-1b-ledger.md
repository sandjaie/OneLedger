# Phase 1b specification — the ledger, from manual entry to consolidated position

Date: 6 September 2026. Phase 1b is the remainder of Phase 1 after milestone M1a: everything
that puts real transactions into the ledger and turns them into a position the owner can trust.
It continues the [Phase 1 specification](2026-09-06-phase-1-trusted-financial-position.md),
which owns Deliveries A, B and C and states each later milestone's entry criteria in its §7.
Scope and sequence remain owned by the [roadmap](../../product/roadmap.html) and the
[master implementation plan](../plans/implementation-plan.md).

This document is authoritative for Phase 1b: contracts, scope boundaries, required behaviours and
acceptance criteria, and no production or test implementation. A companion plan, written
separately, is the intended execution path; the repository at dispatch time determines the exact
implementation.

**The design documents are the specification of record for their subsystems.** This document cites
them by file and section rather than restating them, and where it appears to disagree with one the
design document wins — except where a resolution is recorded explicitly, as in §4.6.

## 1. Scope and sequence

Phase 1b covers M1b, M2, M3, M4, M5, M9a, M6, M7, M10 and M9b, in this order:

```text
M1a → M1b → M2 → M3 + M4 → M5 → M9a → M6 / M7 / M10 → M9b
```

M3 and M4 may run in parallel once M2 has merged. M6, M7 and M10 may run in parallel once M5
has merged; M10 needs only M2. M8 (loans) stays descheduled and conditional on an actual loan
existing; it never blocks anything in this chain.

**Entry criterion for the phase**: Task 11 of the
[Phase 1 plan](../plans/2026-09-06-phase-1-trusted-financial-position.md) is complete and M1a is
merged to `feature/phase-1-trusted-financial-position`. Nothing in Phase 1b starts before that.

Each milestone writes its own dated plan in `docs/superpowers/plans/` immediately before
implementation, against the tree at that time. Interfaces come from the working tree and
[`schema.md`](../schema.md), not from invention in advance. Implementation status lives only in
[implementation progress](../progress.md).

### Out of scope for Phase 1b

No LLM calls anywhere ([ADR-0003](../../adr/0003-defer-ai-to-v2.html)). No automatic payments,
trades, bank credentials or new shared access. No commitments, forecasts, goals, reservations or
protection records — Phase 2, gated on Phase 1's exit criteria plus two months of maintained use.
M8 loan amortisation and schedules. Any recurring-transaction detection, which classification
§1 reserves for Phase 2.

## 2. Baseline

Phase 1 Tasks 1–11 deliver: the correctness repairs of Delivery A (the API package imports, no
test requires a named currency to be seeded, opening balance initializes the current balance,
database failures stay distinct from authorization denials, Compose honors the configured
database identity); Delivery B, which moves household configuration into the database behind
`require_owner()`, adds `GET /currencies`, and installs the reporting-pair guard with its
`reportingPairLocked` flag and household-row lock; and Delivery C — M1a, the account inventory
with opening-balance evidence, acknowledged dates and preserved closed accounts. Applied schema at
entry is V1–V8. The Phase 1 specification §§2–5 has the detail; it is not restated here.

Phase 1b adds no baseline assumption beyond that, except one: FX backfill reaching the
01 Jan 2024 anchor ([ADR-0011](../../adr/0011-36-month-history-horizon.html)) is verified before
M2 begins.

## 3. Milestones

Entry criteria and scope boundaries below are taken from Phase 1 spec §7; acceptance criteria are
the roadmap's "Done when" clauses, which each milestone's own plan converts into runnable checks.

Migration numbering: [`schema.md`](../schema.md) numbers the reference sequence from `0006`, and
the Phase 1 plan's Tasks 1–11 consumed V6–V8, so Phase 1b migrations begin at `V9` — M1b's
categories and merchants are `V9__categories_and_merchants.sql`, its transaction ledger `V10`.
Because Flyway applies versions in order and the milestone sequence is not the reference-section
order (M9a's `0014` lands before M6's `0011`), each later migration takes the next unapplied
version at dispatch and names the reference section it implements; the plan records the intended
sequence. Migrations are forward-only and never edited
once applied ([ADR-0015](../../adr/0015-flyway-for-schema-migrations.html)); a table change edits
the migration, `schema.md` and `docs/design/data-model.html` in one commit.

### 3.1 M1b — Manual transaction entry

**Entry criteria.** M1a merged.

**Scope.** Manual transaction entry with FX frozen on save; the transaction list with filter,
sort, search and inline category edit; the seed taxonomy; category management. No import.

**Contracts.** `schema.md` `0006` (`category`, `merchant`, `merchant_alias`) and `0007`
(`transaction`, and the `v_transaction_reportable` view). The seed taxonomy is
[classification §3](../../design/classification.html) — two levels, every category carrying a
bucket from [ADR-0008](../../adr/0008-seed-taxonomy-with-buckets.html). Manual entry is **the first
writer of frozen `amount_base` / `amount_secondary` values**, so it is where the household-row
lock contract of [Phase 1 spec §B3 point 5](2026-09-06-phase-1-trusted-financial-position.md)
acquires its second participant: every frozen writer takes `SELECT … FOR UPDATE` on the household
row before writing, and the interleaving test — a reporting-pair change racing a concurrent first
frozen insert — lands here. `v_transaction_reportable` is created here and is from this point the
only surface any reporting query may read (§4.2).

**Acceptance.** Every account type lands in the correct net-worth bucket. A liability moves in the
correct direction for both a purchase and a payment. For both account classes, balance equals
opening balance plus the sum of transaction amounts, to four decimal places. A manual transaction
in a currency outside the reporting pair shows the correct frozen base value. One hundred manual
transactions can be entered without the list becoming sluggish.

**Out of scope.** Any file parsing, staging, dedupe or reconciliation; classification beyond
choosing a category by hand; transfer detection.

### 3.2 M2 — Importer, one layout end to end

**Entry criteria.** M1b merged, and FX backfill reaching the 01 Jan 2024 anchor
([ADR-0011](../../adr/0011-36-month-history-horizon.html)) verified.

**Scope.** The generic extraction engine and the layout-mapping UI, proven by mapping exactly one
real layout. Dedupe, FX enrichment with fetch-on-missing, reconciliation blocking on mismatch
(Invariant I8), staging, the review screen, and the atomic commit. Deferred in from Phase 0: CI
and `scripts/backup-db.sh`.

**Contracts.** The twelve-stage pipeline of
[statement-ingestion §1](../../design/statement-ingestion.html) is the specification; every stage
runs in the Python service and **the web app parses nothing**. A layout is a `statement_layout`
row created through the mapping UI — fingerprints, header pattern, column x-bands, date format,
sign convention, quirks — and no institution or format name appears in code
([ADR-0012](../../adr/0012-statement-layouts-as-data.html), ingestion §3–§4). Every imported
transaction records `statement_layout_id` and `layout_version`. Dedupe has three layers
(ingestion §2, §6): L1 document `sha256` unique per household; L2 `dedupe_hash` unique on
`(household_id, account_id, dedupe_hash)`, whose disambiguator is the **occurrence index within
the day, not the row ordinal** — ordinal double-imports whenever statements overlap; L3 fuzzy
±3 days, warn-only. Staging is `staged_transaction` with `raw_row JSONB` and `proposed_*` columns
and status ∈ `new | duplicate | near_duplicate | needs_review | accepted | rejected`
(ingestion §9). The review screen orders most-attention-first and **nothing imports without a
click** — confidence pre-sorts and pre-ticks, never decides (ingestion §10). Commit is one
database transaction ending with `assert_account_reconciles(...)`, which raises and rolls the
whole batch back (ingestion §11). Reconciliation verdicts are `reconciled`,
`mismatched` → ImportBlocked, `running_balance_break` → ImportBlocked naming the offending row,
and `unavailable` → warn and flag (ingestion §8, Invariant I8). PDF passwords are prompted per
file and held only in a local variable — never disk, database, log or "remember this"
(ingestion §2, §4). Schema: `0008` (`document`, `import_batch`, `statement_layout`);
[api-contracts §4](../../design/api-contracts.html) for the staging contract and the error table.

**Acceptance.** A layout is mappable through the UI, saved, and reused on the next statement with
no code change. Auto-proposal finds the header row and the column bands. All 38 statements of the
chosen layout import with no manual correction. Reconciliation passes for each. Re-importing the
same file yields zero new rows and names the earlier import. An overlapping statement imports only
the rows not already present. A corrupted amount blocks the batch and names the offending row. A
failure induced mid-import leaves zero rows behind. A backup is taken and restored into a scratch
database.

**Out of scope.** Additional layouts (M3), classification rules beyond what stage 7 needs to run
(M4), transfer detection (M5).

### 3.3 M3 — The remaining layouts

**Entry criteria.** M2 merged and its layout reconciling against real statements.

**Scope.** A mapping exercise, not an engine change: three further layouts, password-protected
PDFs, multi-line descriptions, redacted golden fixtures, the diagnostics panel, and refusal of
scanned PDFs. New quirks are additive to the vocabulary and are considered, not accreted silently.

**Contracts.** M2's saved profile shape, unchanged; the ADR-0012 quirks vocabulary. Scanned PDFs
are detected and refused with a report, never mis-parsed (ingestion §4). The diagnostics panel
(ingestion §12) is the whole diagnostic surface — there is no CLI
([ADR-0013](../../adr/0013-python-backend-service.html), and the owner rule that recurring
user-facing work lives in the app). Golden fixtures are redacted and layout-preserving, each with
a README naming the structure it reproduces; real statements never enter the repository
(ingestion §13). Ingestion §14 is a nine-item acceptance checklist that this milestone must close.

**Acceptance.** All four layouts import real statements and reconcile, and none of them is named
in code. Per-layout fixture regression tests exist. An encrypted statement imports without its
password touching disk. All 71 gathered statements import and reconcile across the full period. A
synthetic, previously unseen format is mappable and importable with no code change — the ADR-0012
acceptance test.

**Out of scope.** Engine redesign to accommodate a quirk that the vocabulary can express;
classification and transfers.

### 3.4 M4 — Classification

**Entry criteria.** M2 merged. May run in parallel with M3.

**Scope.** The full cascade, rule CRUD with live preview, seed rules, merchant aliases,
learn-from-correction with an affected count, confidence scoring, and Settings › Rules.

**Contracts.** [Classification §1](../../design/classification.html) fixes the cascade and it is
deterministic, first match wins: user override → user rule (priority ascending, default 100) →
merchant alias → *(recurring — Phase 2, not built here)* → prior match on the same account and
text → seed rule (priority 500) → unclassified, to review. Each transaction stores
`classified_by` and `classification_rule_id`. There is exactly one implementation of the cascade
(`classify.py`) with one caller. The confidence table is fixed — override 1.0, user rule 0.98,
alias 0.95, prior exact 0.90, seed 0.80, fuzzy 0.70, unclassified 0.0 — and the pre-tick threshold
is 0.80, which matches the review screen's rule that confidence pre-ticks but never decides. A
rule matches only when all its non-null conditions match, and every regex is validated against
catastrophic backtracking (§2). Seed packs are `db/seeds/rules/global.yaml` plus
`regions/<cc>.yaml`, selected by the distinct countries present in `account`, roughly 120 rules,
loaded by migration and versioned (§3) — region files are data, not named-institution code.
Learn-from-correction prompts explicitly, shows the affected count and a preview before commit,
and makes retroactivity opt-in; it never quietly changes past classifications (§4). Merchant
aliases are created implicitly on review (§5). The special classifications of §6 — card payment,
remittance, SIP, EMI principal/interest split, ATM to cash, salary, interest, reversal to refund,
bounced cheque, self-transfer — are part of this milestone's behaviour. Schema: `0009`
(`classification_rule`). Taxonomy buckets: ADR-0008.

**Acceptance.** Seed rules classify at least 70% of a real first import. Every classified
transaction names the rule that classified it. Creating a rule from a correction shows an accurate
affected count. Rules never silently change past data.

**Quality targets** (classification §7, tracked rather than gating this milestone): ≥70% on the
first import, ≥90% after a month, ≥95% after three; misclassification under 2%. The system
abstains rather than guesses — an unclassified transaction is a review item, not a wrong answer.

**Out of scope.** Recurring-series detection (Phase 2). Any LLM call (ADR-0003).

### 3.5 M5 — Transfers and household outflows

**Entry criteria.** M3 and M4 merged.

**Scope.** The matching engine, FX-aware tolerance, kind-specific time windows, confirmation
inside the review queue with **nothing auto-linked**, pending one-sided transfers re-matched when
a later import supplies the other side, FX spread and total cost, `v_transaction_reportable`
enforced together with its static check, and the external-household-outflow reporting contract
kept distinct from consumption.

**Contracts.** [Transfers and reconciliation §3](../../design/transfers-and-reconciliation.html)
owns the matching rules; [multi-currency §5](../../design/multi-currency-and-fx.html) owns the
FX-aware tolerance and cost decomposition;
[ADR-0007](../../adr/0007-transfer-auto-detection-with-review.html) fixes that detection proposes
and the owner confirms. Schema: `0010` (`transfer`). Invariant I3 becomes enforceable here: every
reporting query reads `v_transaction_reportable`, and the static check that no reporting query
reads the base `transaction` table lands with this milestone. An external household outflow — a
remittance to someone outside the household — is neither spending nor income and is reported on
its own line; it is not folded into consumption.

**Acceptance.** A real remittance is detected with confidence above 0.90 and appears in neither
spending nor income. A credit-card payment is detected and excluded. Two unrelated same-day
transactions of similar amount are not linked. Unlinking a transfer raises spending by exactly the
source amount. Monthly spending matches a hand-tallied month.

**Out of scope.** Card cycle and utilisation reporting (M6); investment flows (M7).

### 3.6 M9a — Early verified overview

**Entry criteria.** M5 merged. Does not require M7 or M10.

**Scope.** The basic verified overview: balances for included accounts, monthly spending, external
outflows, retained income, and links into review. Every headline carries its as-of date and its
status — verified, partially covered, or estimated. Price dates, FX dates and statement dates stay
separate and are never conflated. Manually valued assets carry explicit valuation dates and are
counted once.

**Contracts.** [Multi-currency §6](../../design/multi-currency-and-fx.html) for as-of dates and
display; `schema.md` `0014` (`valuation_snapshot`);
[UI information architecture §2](../../product/ui-information-architecture.html) for the Overview
screen; [ADR-0006](../../adr/0006-manual-valuation-snapshots.html) for dated manual valuations.
Invariant I7 governs every derived figure: a carried-forward rate, an interpolated valuation and
an unexplained residual are stored as labelled facts and shown as such. Missing coverage is shown
as missing coverage, never as zero.

**Acceptance.** The usability checkpoint for this milestone is **one complete recent month, with
opening balances acknowledged**, presented as a verified overview: monthly spending, external
outflows, as-of dates on every headline, visible missing coverage, and review and data-quality
items reachable from it. Manually valued assets show their valuation dates.

**Out of scope.** Consolidated history, allocation and attribution — all M9b. Card-due items,
which M6 supplies to this screen once it exists.

### 3.7 M6 — Credit cards

**Entry criteria.** M5 merged. May run in parallel with M7 and M10.

**Scope.** Statement records, dues, utilisation, paid-in-full detection, and purchase spending
separated from bill settlement.

**Contracts.** `schema.md` `0011` (`card_statement`);
[ADR-0009](../../adr/0009-credit-card-statement-cycles.html) for cycle definition. Invariant I4 is
this milestone's spine: a card payment must not be double-counted — the purchase is the spending
event and the settlement is a transfer. There is no dedicated Cards screen; the card surfaces are
fragments inside Overview, Review and Transactions
([UI information architecture](../../product/ui-information-architecture.html)). Card-due items
feed the M9a overview once available.

**Acceptance.** Utilisation matches the statement. A card payment appears in neither spending nor
income. Cycle spending equals the statement's declared total debits. A revolving card is visually
distinct from one paid in full.

**Out of scope.** Interest projections, minimum-payment planning and reward tracking.

### 3.8 M7 — Investments

**Entry criteria.** M5 merged. May run in parallel with M6 and M10.

**Scope.** Lot-level holdings, XIRR with a bisection fallback, allocation, and lifecycle
continuity through dividends, reinvestment, partial sales, charges, splits and platform transfers
— for the holdings actually owned, not a general corporate-actions engine. An unrecognised
corporate action blocks that holding's return claim and raises a review item rather than producing
a number nobody can defend.

**Contracts.** `schema.md` `0012` (`security`, `holding`, `investment_transaction`,
`price_snapshot`); [ADR-0005](../../adr/0005-transaction-level-investment-lots.html) for
lot-level accounting. Prices carry their own as-of dates, separate from FX and statement dates
(multi-currency §6). Invariant I7 applies to interpolated valuations and any residual.

**Acceptance.** XIRR matches a spreadsheet within 0.01%. Allocation sums to 100% and reconciles to
the portfolio value. A holding denominated outside the reporting pair is correct in both reporting
views. The sum of transaction units equals the holding's units. A fund purchase appears as one
linked event, not as scattered rows.

**Out of scope.** Automated price feeds beyond what the design specifies; tax-lot optimisation;
any trading action.

### 3.9 M10 — Documents and owner export

**Entry criteria.** M2 merged. May run in parallel with M6 and M7.

**Scope.** The document library with bidirectional traceability, checksum duplicate prevention,
deletion blocked while a document is referenced, and the owner export of open tabular records plus
selected originals with provenance and a coverage manifest. Owner-only access; temporary files
protected and cleaned up.

**Contracts.** `schema.md` `0008` (`document`) and requirement F12. Invariant I6 is the point of
the milestone: every transaction whose `source` is `statement_import` carries a
`source_document_id`, and resolves from there to a document, a page and a row. The L1 checksum
of ingestion §2 is the duplicate detector, and it fires **before** parsing. The never-store list
([security and privacy §1](../../operations/security-and-privacy.html)) is absolute for anything
extracted from a document.

**Acceptance.** Every document is retrievable. Every imported transaction links to its document,
page and row. Re-uploading a document is detected before parsing. Disk usage is visible to the
owner.

**Out of scope.** OCR of scanned documents — M3 refuses them, and that refusal stands.

### 3.10 M9b — Full consolidated position and attribution

**Entry criteria.** M7 merged and valuation inputs available.

**Scope.** Consolidated history, allocation, the explanatory waterfall, the FX line and a visible
residual.

**Contracts.** [Multi-currency §7](../../design/multi-currency-and-fx.html) and
[data model §14](../../design/data-model.html);
[ADR-0002](../../adr/0002-frozen-dual-currency-amounts.html). **Before writing its migration**,
this milestone reconciles the unapplied `0015` attribution shape (`net_worth_snapshot`,
`net_worth_attribution`) with requirement F03, and updates `schema.md` and `data-model.html`
together with it. It specifies the frozen-flow-to-closing-rate FX bridge and proves it with dated
fixtures. Reports never multiply a stored amount by a rate (Invariant I5, and its static check).

**Acceptance.** Net worth on any past date equals the sum of signed balances, in both reporting
currencies. Attribution components plus the residual equal the actual change, exactly. Synthetic
scenarios drive the residual to zero. A month's spending equals the sum of its reportable
transactions, exactly. The dashboard renders in under two seconds cold.

**Out of scope.** Forecasts and goals (Phase 2).

## 4. Cross-milestone contracts

These hold across every milestone above. A milestone plan may not restate them differently.

### 4.1 The household row lock

`update_household_configuration(...)` takes `SELECT … FOR UPDATE` on the household row before
consulting the reporting-pair guard, and **every writer of a frozen `amount_base` /
`amount_secondary` row takes the same lock before writing** — M1b's manual entry first, M2's
import commit next, and M6, M7 and M9b after them. Without it the guard cannot see a transaction
that has read the old pair but has not yet committed its first frozen row. The interleaving test
lands with M1b (Phase 1 spec §B3 point 5); later frozen writers inherit the contract and each
must demonstrate it holds for their own write path.

### 4.2 `v_transaction_reportable` is the only reporting surface

Cash-flow, budget, spending and savings-rate queries read `v_transaction_reportable`. No reporting
query reads the base `transaction` table. This is Invariant I3 plus a static check, not a
convention; the check itself lands with M5, where transfers first make the distinction meaningful,
and it guards every milestone after it. The external-household-outflow line is reported separately
from consumption and is neither spending nor income.

### 4.3 Frozen amounts

`amount_base` and `amount_secondary` are computed once, at the moment the row is written, and are
never recomputed ([ADR-0002](../../adr/0002-frozen-dual-currency-amounts.html), Invariant I5).
Reports read those columns; **no report multiplies a stored amount by a rate**, and a static check
enforces it. Which of the two currencies is displayed is a browser preference and touches no data;
changing which two currencies the pair contains is a backfill and is refused once any frozen
amount exists, with the explanatory refusal Phase 1 spec §B3 specifies.

### 4.4 The three dedupe layers

L1 is the document `sha256`, unique per household, checked before parsing. L2 is `dedupe_hash`,
unique on `(household_id, account_id, dedupe_hash)` — and its disambiguator is the **occurrence
index within the day, not the row ordinal**. This is load-bearing: an ordinal changes when a
statement's window shifts, so an ordinal-based hash double-imports every overlapping statement.
L3 is a fuzzy ±3-day check that warns and never blocks. See
[statement-ingestion §2 and §6](../../design/statement-ingestion.html). `investment_transaction`
carries its own `dedupe_hash`, unique per household, and M7 applies the same reasoning to it.

### 4.5 Provenance: document → page → row

Every imported transaction records `source_document_id`, `source_page_number`,
`source_row_number`, `statement_layout_id` and `layout_version`. M2 populates them; M10 makes the
path navigable in both directions and blocks deletion of a document that is still referenced. This
is Invariant I6, and it is what makes "reconciles to evidence" in the exit criteria a checkable
claim rather than a slogan.

### 4.6 The classification stage number — a documented inconsistency

[Classification §1](../../design/classification.html) says `classify.py` runs at *stage 10* of the
ingestion pipeline. [Statement-ingestion §1 and §7](../../design/statement-ingestion.html) place
classification at *stage 7*, with stage 10 being Review. Both cannot be right.

**Resolution: statement-ingestion.html owns the pipeline ordering.** Classification runs at stage
7, before staging and review, so that the review screen can display proposed categories and their
confidence. Classification.html owns the cascade, the confidence table and the rule semantics, and
its stage number is the error. Under the repository rule for disagreeing documents, this is a bug
in classification.html: M4 fixes the stage number there, in the same commit as its own work, and
does not work around it.

### 4.7 UI authority

Navigation order for Phase 1 is fixed: Overview, Accounts, Transactions, Cash Flow, Investments,
Loans, Net Worth, Documents, then Review (with its badge) and Settings. The ASCII sketches in
[ui-information-architecture.html](../../product/ui-information-architecture.html) are the
specification and the mockups in `docs/product/mockups/` are a reading of it: **where the two
disagree the IA document wins**. `Review.dc.html`, `NetWorth.dc.html` and `LayoutMapping.dc.html`
carry the three screens the IA treats as decisive; `OneLedger.dc.html` carries the rest. Cards
have no screen of their own — they are fragments in Overview, Review and Transactions. Recurring
user-facing work belongs in the app: `GET /doctor` backs Settings › Data Quality, the ingestion
diagnostics panel replaces any import CLI, and there is no `oneledger` command.

### 4.8 Standing constraints

Money is exact end to end: `Decimal` in Python, `NUMERIC(20,4)` in Postgres, a **string** on the
wire. TypeScript formats money and does no arithmetic on it; aggregation happens in Postgres.
Percentages are human-readable — `15.2500` is 15.25%. No currency, institution or country name is
ever an identifier, in any position (ADR-0012); fixtures use the reserved codes `XIN` and `XAE`.
The two static checks behind these are not to be silenced.

`require_owner()` is the authorization boundary for every financial route added in this phase;
every `SECURITY DEFINER` function calls `assert_is_owner()` first; every query carries an explicit
`household_id` predicate. RLS is depth, not the wall, and the UI is never the boundary. Nothing on
the never-store list ([security and privacy §1](../../operations/security-and-privacy.html)) is
persisted anywhere — masked value plus salted hash only — PDF passwords live in a local variable
for one parse, and no real financial data enters the repository on any branch. Implementation
status is maintained only in [`docs/superpowers/progress.md`](../progress.md).

## 5. Requirement coverage

Extending Phase 1 spec §8, which recorded every one of these as "not started" for the M1a
tranche. This table says which milestone closes each, not that any is closed.

| Requirement | Closed by | What closing it means here |
|---|---|---|
| F01 household boundary | M5 | Transfers respect the boundary; an outflow leaving the household reports as an outflow, not as consumption |
| F02 metric definitions | M5, M9a | Spending, income and outflow have one definition each, read through `v_transaction_reportable` |
| F03 net-worth attribution | M9b | Components plus residual equal the actual change; the `0015` shape is reconciled with F03 before its migration |
| F04 money in transit | M5, M9a | A one-sided transfer is visible as in-transit, not as a loss, and re-matches on later import |
| F05 freshness and progressive onboarding | M2, M9a | Every headline carries its as-of date and its verified / partially covered / estimated status |
| F06 account and investment lifecycle | M7 | Lot-level continuity through dividends, splits, partial sales and platform transfers |
| F12 owner export | M10 | Open tabular records plus selected originals, with provenance and a coverage manifest |
| P01 | M9b | Dashboard cold render under two seconds |
| P02 | M5, M9a | Monthly spending agrees with a hand-tallied month |
| P03 | M9a | One complete recent month presented as a verified overview |
| P04 | M5, M9a | External outflows reported separately from consumption |
| P11 | M10 | Every document retrievable; every imported transaction traceable to document, page and row |
| P12 | M7, M9b | XIRR and allocation defensible against a spreadsheet; residual visible |

Invariant coverage: I1 and I2 hold from M1b; I3 becomes enforceable with M5 and its static check;
I4 with M6; I5 from M1b onward with its static check landing by M9b; I6 with M2 and navigable with
M10; I7 from M9a onward; I8 with M2. Their tests are
[testing and data quality §1](../../operations/testing-and-data-quality.html). Any invariant
failure halts feature work until it is fixed.

## 6. Verification and exit gates

The canonical per-task gate is the repository `verify` skill, unchanged from Phase 1 spec §9,
which enumerates its commands. Every task leaves a check that failed before its implementation and
passed after it. A suite that collects nothing and exits zero is a failure, not a pass. Nothing is
made green by editing a test, loosening an assertion, deleting a case or weakening an invariant; a
genuinely wrong invariant is a superseding ADR, not an edit.

Each milestone is complete when its §3 acceptance criteria pass on real data, recorded honestly in
a PR made from `.github/PULL_REQUEST_TEMPLATE.md`, with failures and skipped checks stated rather
than pre-checked.

**Phase 1 exit criteria** — these close the phase, not any single milestone:

- All scheduled milestones are done. M8 remains descheduled and does not block this gate.
- 36 months of real statements across all four layouts are imported and reconciled back to the
  01 Jan 2024 anchor (ADR-0011).
- A month's import and review takes under 20 minutes.
- Every included balance reconciles to its evidence, with discrepancies disclosed separately —
  no tolerance band, and specifically no absorbing a 1% difference into a total.
- Monthly spending agrees exactly with a hand-checked month.
- Infrastructure spend while running locally is zero.
- Restore from backup is verified, not assumed.
