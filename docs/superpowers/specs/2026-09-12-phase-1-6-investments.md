# Phase 1.6 — investments

Owner-approved product and architecture direction, 12 September 2026.

Workspace: `/Users/sandjaieravi/dev/OneLedger`. First task branch:
`feature/p1-6a-investment-ledger`. Base and PR target: `develop`.

This specification defines the Phase 1.6 contract. The dated execution plan will declare every
task branch and verification command after the owner reviews this document. No implementation may
be dispatched until `/oneledger-plan-review` is green and its findings are applied.

## Product outcome

OneLedger becomes the owner's factual investment record. It imports supported investment
statements, preserves open and closed positions, links investment activity to the cash ledger,
refreshes trustworthy market prices, and adds current investment value to net worth exactly once.
It explains what was invested, what is currently held, what was received, and what return the
evidence supports.

Phase 1.6 is descriptive, not advisory. It does not recommend funds, trades, exits, allocation
changes or rebalancing. Long-horizon scenarios remain a later, explicitly assumption-labelled
capability and never appear as actual performance.

## Approved current evidence and future boundary

The first supported source is a text-readable detailed consolidated mutual-fund statement covering
the project's approved history anchor through its report date. It contains transaction history and
a current-position section. A companion summary statement supplies a point-in-time reconciliation
checkpoint; it is not a second transaction source.

The detailed statement is treated as complete for its requested period unless reconciliation
proves otherwise. Later monthly statements extend that history. Overlapping periods add evidence
to existing economic events rather than duplicating them.

No current retirement or pension account exists. Support for an opaque retirement holding remains
the ADR-0006 dated-valuation path, inactive until the owner supplies such an account and evidence.
Prior closed equity positions and future equities, exchange-traded funds, bonds and other
market-priced investments use the same domain model when a supported statement adapter is added.
Property and other assets without an unambiguous market price use dated valuation snapshots.

An unsupported document format is not a universal-parser failure. It is a safe, explicit
unsupported result. A focused later PR may add an adapter, synthetic fixture and regression tests
without redesigning portfolio accounting.

## Authority and truth hierarchy

Evidence has separate jobs:

1. A committed investment statement transaction establishes the event, date, units, price or NAV,
   amounts, charges, tax and source identity it actually states.
2. A summary or end-position section verifies ending units and any reported cost or value. It does
   not manufacture missing transactions.
3. A market-data adapter supplies a dated valuation price. A market price never creates units,
   acquisition dates, cash flows or cost basis.
4. A linked ledger transaction proves cash funding, proceeds or a paid distribution.
5. An owner-entered correction supplies an exact missing fact with `owner_entered` provenance. It
   never silently changes an imported original.

Conflicting evidence creates a review item. Source order, filename, nearest value, fuzzy name or
provider popularity never resolves a conflict.

## Architecture

Phase 1.6 adds five isolated capabilities around the existing document, account, ledger, FX,
authorization and dashboard foundations.

### Investment ledger

The investment ledger stores securities, holdings, transactions, acquisition lots, lot
allocations, funding links and source evidence. Transactions and lot allocations are the
historical source of truth. A holding is a current projection that can always be rebuilt and
reconciled from those events.

### Statement ingestion

An investment statement adapter extracts source evidence into typed staged investment events and
end-position checkpoints. It reuses immutable document storage but does not force a multi-holding
investment statement through the bank statement's single-account staged-row shape.

One document owns an investment import parent. Reconciliation groups contain all holdings joined
by one switch, transfer or other multi-holding event. Each group stages, reviews and commits
atomically. An unrecognized unit-affecting action blocks its group and its return claim while
unrelated, reconciled groups from the document may proceed.

### Valuation service

Asset-specific adapters fetch dated prices into one price history contract. The first adapter reads
the official daily mutual-fund NAV publication. Future adapters may cover listed securities or
other objectively priced instruments. Provider selection is data and configuration, never an
institution, country or currency embedded as a required code identifier.

### Portfolio calculations

One deterministic domain service calculates holdings, invested cost, current value, realised and
unrealised results, allocation and XIRR from exact database values. Python uses `Decimal`,
PostgreSQL uses `NUMERIC`, API values are strings, and TypeScript performs display-only formatting.

### Portfolio and review experience

Owner-only API routes and web screens expose portfolio facts, holding detail, activity, funding
provenance, import review and data-quality problems. They do not perform trades or publish private
source evidence.

## Data model contract

The implementation must reconcile the planned `0012` and `0014` sections in
`docs/superpowers/schema.md` with the actual migration head. The actual tree currently has no
investment, price or valuation migration; new migrations are forward-only and start after the
current highest applied version.

### Security

`security` identifies one instrument through a UUID. It records instrument type, name, currency,
country, asset class and optional public identifiers such as ISIN, exchange ticker or a
price-provider code. Shared public reference rows may have no household; owner-created instruments
are household-scoped. Public identifiers identify the instrument, never the owner or holding.

Exact public identifiers win during resolution. A name-only conflict does not guess; it creates a
review item. Provider mappings record source, version, effective dates and active state so a scheme
rename or code replacement does not rewrite history.

### Investment account and private identifiers

An existing `account` remains the ownership and platform container. Account types already include
brokerage and mutual-fund folio shapes. Extend the typed `account_identifier` boundary for folio,
brokerage-account or equivalent source identifiers rather than storing a private identifier on
`holding`.

Complete source spelling and a kind-normalized match value may exist in the owner's local database
under ADR-0021. Ordinary payloads expose only a safe mask, identifier UUID and reveal availability.
An explicit owner-only reveal uses no-store headers. Complete or normalized identifiers never enter
logs, errors, URLs, analytics, browser persistence, fixtures, screenshots or documentation.

### Holding and net-worth valuation mode

`holding` identifies a household, investment account and security. Current units, cost and value
are projections from committed events and dated prices; they are not independently editable.

Every asset account has exactly one net-worth valuation mode:

- `ledger_balance`: cash or liability value comes from its signed account balance;
- `holdings`: value is the sum of current holdings plus separately evidenced account cash; or
- `snapshot`: value is the most recent dated valuation at or before the requested date.

The dashboard must never add an investment account's placeholder balance and its holdings, or a
manual snapshot and structured holdings, for the same asset. Moving from snapshot to structured
history closes the overlapping snapshot contribution from the effective date without deleting its
historical evidence.

### Investment transaction

`investment_transaction` stores one committed event with household, account, holding, security,
event date and kind. Monetary fields include gross amount, charges, withheld tax and net amount in
the native currency plus frozen reporting amounts and FX provenance where a flow exists. Units and
price or NAV remain exact and nullable only for event kinds whose meaning requires no such value.

The initial vocabulary covers:

- purchase and recurring purchase;
- sale, redemption and maturity;
- cash distribution and interest;
- reinvested distribution;
- charges and withheld tax;
- switch or systematic transfer in/out;
- split, bonus and rights events;
- platform transfer in/out; and
- owner-entered correction or reversal provenance without a generic meaning-free adjustment.

Event invariants constrain unit direction, required cash values and permitted nulls by kind.
Imported events are immutable. A correction appends a superseding/reversal relation and the exact
replacement fact; it does not overwrite the original or edit aggregate holding units.

### Acquisition lots and disposal allocation

Every acquisition event creates an acquisition lot carrying its original date, units, evidenced
cost and origin transaction. Partial sales and redemptions may allocate units to source lots when
the statement or owner provides that evidence. The implementation does not silently impose FIFO,
average cost or a jurisdiction-specific tax rule.

A full platform transfer moves every open lot while preserving its origin date and cost. A partial
transfer without evidenced lot selection creates a review item. Splits transform open-lot units by
the evidenced ratio and preserve total basis. Bonus units become an evidenced zero-cash lot. An
unrecognized corporate action blocks affected realised-cost and return claims from its effective
date.

Phase 1.6 may report holding- and portfolio-level money-weighted returns when cash-flow history is
complete even if tax-lot disposal selection is unresolved. It must label realised cost/gain as
unavailable wherever that result depends on an unproved lot allocation.

### Cash and funding links

Use an allocation relation between investment transactions and ordinary ledger transactions rather
than assuming one bank row funds exactly one purchase. Each link records a role (`funding`,
`proceeds`, `cash_distribution`, `fee`), exact allocated amount and provenance.

Allocated amounts cannot exceed either side. Both rows must belong to the same household. A link is
confirmed atomically through an owner-authorized database operation; direct SQL is not an
acceptance path.

A cross-account or cross-currency remittance remains an existing confirmed transfer chain. The
final cash debit links to the purchase. Internal transfers remain outside income and spending; the
purchase remains an investment contribution, not consumption. The chain is visible as one
explainable economic path without becoming one denormalized transaction.

### Source evidence and deduplication

`investment_evidence` permits several documents or future email receipts to support one economic
event. It records source kind, document/page/row provenance, private source-event identity where
available, extraction version and created time.

A complete normalized source-event identifier is the strongest dedupe signal. Otherwise use a
versioned content fingerprint over typed, normalized source facts. A fingerprint is scoped by
household and source contract; it is not built from a filename. Conflicts stop. Reimporting the
same or overlapping statement is idempotent and attaches new evidence without changing a
previously confirmed fact silently.

### Prices and valuations

`price_snapshot` stores security, price date, exact price, currency, source and fetch time. Official
or licensed shared prices are read-only reference data. Owner-entered prices for a private
instrument are household-scoped and visibly manual.

On portfolio access the API returns cached values immediately and requests refresh when the latest
expected price is stale. Mutual-fund NAV is end-of-day: the UI says the effective date and never
labels it live. A successful refresh appends or idempotently confirms dated prices; it does not
rewrite a different source value without review.

Refresh failure preserves the last known value with a stale label. A holding with no trustworthy
price is unvalued; totals identify missing coverage rather than treating it as zero. Historical
price requests are bounded to dates required by transactions, reconciliation or an owner-selected
chart range.

Opaque assets use `valuation_snapshot`: exact native value, dated frozen FX values, method,
confidence, source evidence and visible age. They carry forward flat under ADR-0006 and do not gain
XIRR merely because two valuations exist.

### Review items

A household-scoped investment review item records a fixed reason code, affected import/event/
holding, safe context, status and resolution provenance. Reasons include unsupported format,
unknown security, ambiguous account or identifier, unrecognized event, duplicate conflict,
unit mismatch, cost mismatch, missing price, unmatched cash, incomplete lot allocation and stale
valuation.

Review payloads contain no source filename, complete private identifier, raw statement row or
unredacted source reference. Resolving an item invokes the same validated domain operation as an
ordinary manual entry or match; marking it resolved without fixing the invariant is refused.

## Reconciliation invariants

The database and domain tests must prove:

1. Current holding units equal committed acquisition, disposal and corporate-action unit effects.
2. Every summary end-position agrees with transaction-derived units within the source's declared
   precision before its reconciliation group commits.
3. Allocation value equals the exact sum of included holding values. Percentages are unavailable
   when the required denominator is incomplete; a complete allocation reconciles to the whole.
4. A funding, proceeds or distribution allocation is not claimed twice and does not exceed either
   linked event.
5. A purchase linked to cash and any earlier internal-transfer chain changes neither household
   income nor spending and does not create wealth.
6. A sale is proceeds and return, not salary or ordinary income. A cash dividend is investment
   income once. A reinvested dividend creates units but no external household cash flow.
7. Charges and withheld tax reduce net return once.
8. A platform transfer, split or bonus preserves explainable economic value and acquisition
   history without becoming a contribution or return.
9. An unknown unit-affecting event prevents the affected holding's return claim.
10. Closed holdings retain history with zero current units and are absent from current value only.
11. Structured holdings, account balance valuation and manual snapshots cannot count the same
    asset simultaneously.
12. Every financial row is owner-authorized, household-scoped and protected by RLS with
    same-household foreign-key enforcement.

## Returns and allocation

XIRR is calculated from dated external investment cash flows from the owner's perspective:
purchases and external contributions are negative; sale proceeds and cash distributions are
positive; an open position's current value is a terminal positive flow on the valuation date.
Internal platform transfers, splits and reinvested distributions are not external cash flows.
Charges and tax affect the net flow exactly once.

The solver uses Decimal inputs and deterministic bracketing/fallback; no binary float enters money,
price, units, rates or the cash-flow equation. It returns no result, with a fixed reason, when there
is no sign change, no terminal value, incomplete required history, an unvalued holding or a blocking
corporate action. A known independent cash-flow series must agree within 0.01 percentage points.

Holding, account, asset-class and portfolio calculations share one cash-flow service. Cached
results, if used, carry valuation date, computed time and an input digest and are invalidated by a
committed event, cash-link change, correction or applicable price update.

Allocation groups current value by asset class, security, account, source platform and native
currency. Group labels are data. API ratios and values are decimal strings; TypeScript only rounds
for display.

## API boundary

Every route requires `require_owner`, scopes queries explicitly to the session household, and
retains RLS as defence in depth. Proposed route groups are:

- securities and owner-created security resolution;
- investment accounts and masked source identifiers;
- owner-entered investment events and corrections;
- investment import parse, review, decision, commit and cancellation;
- holding, activity, lot and funding-link detail;
- price status, refresh and owner-entered fallback;
- portfolio summary, allocation and XIRR; and
- investment review items and resolution operations.

Mutation routes validate exact decimal strings, ISO dates, household ownership, expected prior
state and idempotency. Domain/database functions own multi-row invariants. A response never returns
a float for money, price, units, rate, gain, allocation ratio or XIRR input/result.

Reports continue to read `v_transaction_reportable`. Investment links may refine which ledger rows
that view excludes or labels, but no investment report bypasses the existing reportable-flow
boundary by reading base transactions as reportable consumption.

## Owner workflows

### First import

The owner uploads the detailed statement. The application identifies the supported investment
format from content, resolves or proposes investment accounts and securities, stages event groups,
shows unit/cost/funding reconciliation, and commits only approved reconciled groups. The companion
summary may then confirm end positions without duplicating events.

### Monthly update

The owner uploads the next statement. Existing events dedupe, new events stage, closing positions
reconcile, prices refresh, and portfolio/net-worth views advance. The completion screen states new,
duplicate, attached-evidence, review and blocked counts without exposing source identifiers.

### Manual correction

From a review item the owner adds or corrects the exact missing purchase, sale, distribution, fee,
unit event, price or source link. The UI asks only for fields required by that event kind. It records
`owner_entered` provenance, reruns reconciliation and clears the review item only when the conflict
is resolved.

### Daily portfolio

The portfolio opens with cached facts, a visible valuation date and refresh state. It shows current
value, invested cost, gain, XIRR, open holdings and allocation. A holding detail shows transactions,
units, price history, lots, distributions, charges, funding path and source provenance. Closed
holdings remain available in a separate historical view.

### Net worth

The existing overview adds exact investment value in both reporting currencies with its independent
price and FX dates. It distinguishes a stale price, a stale manual valuation and missing coverage.
Investment funding moves value between cash and investments; only market movement, external flows,
distributions, charges and valuations explain wealth change.

## User-interface presentation

The portfolio summary presents factual headline cards for current value, invested cost, gain and
XIRR, each with its applicable as-of or coverage label. It follows with allocation, open holdings,
activity and data-quality sections. No red/green judgement label implies buy, hold or sell.

Each holding row shows safe instrument name, units, latest price and date, current value, invested
cost, gain and XIRR when available. Missing or partial facts use typed states rather than zero or an
estimated number. The review queue is reachable from the portfolio and import completion views.

The UI includes no forecast on this phase's actual-performance screen. A future scenario feature
must use separate controls and make contribution, growth, inflation and horizon assumptions visible.

## Failure behavior

- A parse or reconciliation failure writes no committed investment events for its atomic group.
- Unknown securities, events and corporate actions abstain; they do not map to `other` merely to
  reach green.
- A price-source outage never deletes or overwrites the last accepted price.
- A missing price never becomes zero and never silently disappears from a portfolio denominator.
- A cash-link proposal never auto-confirms solely from approximate amount/date similarity.
- Cross-household security ownership, holding, event, identifier, evidence, price and review access
  is refused.
- Raw and serialized errors are redacted for ASCII, formatted and Unicode identifiers.
- Cancelled imports retain only the provenance the existing document policy permits and create no
  partial holdings, links or returns.

## Smallest useful vertical and delivery decomposition

The smallest useful vertical is an owner-entered market-priced investment: create or resolve an
investment account and security, record an exact purchase with a funding link, record or fetch a
dated price, reconcile units, show current value, and include it once in net worth. This proves the
domain before a real statement parser can write it.

The execution plan must split Phase 1.6 into reviewable branches, expected to cover:

1. forward-only core schema, RLS and invariants;
2. domain operations, owner entry and the smallest useful read model;
3. funding links, corrections and lifecycle/lot preservation;
4. detailed statement extraction and staging;
5. review, atomic commit and summary reconciliation;
6. official NAV history, refresh and stale/missing-price behavior;
7. portfolio, allocation, XIRR and net-worth APIs;
8. investment import/review web workflow; and
9. portfolio, holding, history and data-quality web workflow.

The plan-review gate may split these further before dispatch. No branch may exceed the repository's
production or production-plus-test diff budget. The plan lands with the first Phase 1.6 branch.

## Required failing-test evidence

Every implementation task begins with focused tests that fail for the intended missing behavior.
Across the phase, tests must cover at least:

- exact NUMERIC/Decimal/string round trips for money, prices, units, rates, gains and XIRR;
- RLS, owner authorization and same-household references on every investment relation and route;
- masked ordinary identifier payloads, explicit no-store reveal and raw/serialized Unicode leakage;
- acquisition, partial disposal, closed holding, distribution, reinvestment, charge, split, bonus,
  full platform transfer and refused ambiguous partial transfer;
- unit and end-position reconciliation, conflicting duplicate evidence and atomic group rollback;
- detailed-statement staging, overlapping monthly idempotency and summary-as-checkpoint behavior;
- funding-chain linkage and reportable-flow non-double-counting;
- official price parsing, idempotent history, stale cache, outage preservation, missing mapping and
  manual-price provenance;
- allocation totals and refusal of incomplete denominators;
- an independently verified XIRR series within 0.01 percentage points plus no-result reason cases;
- net worth counting each structured or snapshot asset once in both reporting currencies;
- owner-entered correction history and refusal to clear an unresolved review item; and
- unsupported formats and unrecognized corporate actions blocking only their atomic groups and
  affected return claims.

Real-statement acceptance runs only through supported authenticated application operations. It
publishes structural pass/fail outcomes and counts, never filenames, instrument/account identities,
statement text, dates, units, prices, amounts or inferred owner holdings.

## Acceptance

Phase 1.6 is accepted when:

- the supplied detailed consolidated statement imports through review and its supported holdings
  reconcile to transaction-derived units;
- the summary statement confirms end positions without creating duplicate events;
- recurring monthly overlap is idempotent;
- current prices refresh from the approved first source with visible date and provenance;
- portfolio value enters net worth exactly once in both reporting currencies;
- purchases expose their confirmed funding path without becoming spending or wealth creation;
- applicable distributions, reinvestments, partial sales, charges, splits and platform transfers
  preserve economic meaning and acquisition history;
- an unrecognized corporate action blocks the affected return claim and creates a review item;
- allocation reconciles exactly when complete and exposes missing coverage when incomplete;
- a known cash-flow series matches independent XIRR evidence within 0.01 percentage points;
- open and closed holdings, activity, funding, prices and data quality are usable from owner-only
  web workflows;
- owner-entered fallback resolves exact missing facts without editing imported originals; and
- no income, spending, contribution, value or return is double-counted.

## Explicit non-goals

Phase 1.6 does not include investment recommendations, rebalancing, benchmark comparison, tax
reports, capital-gains tax policy, automatic trading, email-account access, universal statement
parsing, property-price estimation, retirement forecasting or long-horizon scenario projections.
The schema and provenance contracts preserve the evidence those later approved outcomes will need.

## Documents the implementation must reconcile

Implementation updates the owning documents where the approved contract becomes real:

- `docs/design/data-model.html` for applied investment and valuation semantics;
- `docs/design/api-contracts.html` for owner routes and exact wire contracts;
- `docs/design/statement-ingestion.html` for investment document staging and reconciliation;
- `docs/operations/security-and-privacy.html` for private investment identifiers and evidence;
- `docs/operations/testing-and-data-quality.html` for investment invariants;
- `docs/superpowers/schema.md` for actual migration numbers and DDL; and
- `docs/superpowers/progress.md` for live state only.

The roadmap, master plan, README, this specification and the dated execution plan do not become
parallel implementation-status trackers.
