# Classification knowledge and correction safety implementation plan

> Execute sequentially in the active checkout with `superpowers:executing-plans`.
> Task completion is tracked only in `../progress.md`, not in this execution map.

**Goal:** Turn statement descriptions into supported, reusable classifications with safe repair,
explanations and grouped exceptions; make a local-model evaluation an explicit later work item.

**Architecture:** Reuse the existing cascade and merchant/rule tables. Introduce evidence and
decision history before retrospective replacement. All writes use bounded authenticated previews;
transfer confirmation remains its own existing operation.

**Tech stack:** Existing Python API, Postgres/Flyway and web client; no new runtime dependency now.

**Spec:** [classification knowledge](../specs/2026-09-10-classification-knowledge.md).

## Global constraints and order

- Workspace for every task: `/Users/sandjaieravi/dev/OneLedger`; base and PR target: `develop`.
- One implementation writer; no worktrees. Check root and branch before every git write.
- TDD, `/verify`, local code review, hooks, PR, merge; never weaken existing financial invariants.
- Private research stays outside git. The clean production stack receives no working data.
- Decimal / NUMERIC(20,4) / string money. Reports use `v_transaction_reportable`.
- API operations require owner authentication and household-scoped reference validation.
- New schema is forward-only; select the next migration number from current develop at dispatch.
- Each task forecasts below 800 production / 1,600 production-plus-test added lines.
- The first repair is independent. Tasks 2–6 execute after that repair and spec review; later
  investment and local-model work retain the phase boundaries below.

## Task 1 — stop abbreviation collisions in the banking seed

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
**Branch:** `fix/p1-3-loan-rule-boundary`. **Base and PR target:** `develop`.
**Dependencies:** none. **Forecast:** 5 production/config, 80 test lines.
**Outcome:** A loan abbreviation inside another word no longer classifies that word as repayment;
credits do not match a debit repayment rule.
**Files:** `db/seeds/rules/banking-narration.json`, `api/tests/test_seeded_rules.py`.
**Contract:** Existing seed JSON and classifier interfaces; retain generic matcher semantics.
**Tests:** Exercise the real shipped rule and cascade on synthetic merchant/remittance words,
Unicode-adjacent letters, standalone repayment abbreviations, separators and both directions.
Demonstrate incorrect loan output before the fix; retain valid debit matches afterwards.
**Implementation:** Use token boundaries and a debit condition on the specific loan seed.
**Security/invariants:** No real fixture, migration, raw-row edit, or automatic historical repair.
**Validation:** `cd api && uv run pytest -q tests/test_seeded_rules.py tests/test_ingest_classify.py`,
then `/verify` and `/oneledger-code-review`.
**Acceptance:** Regression RED/GREEN; loaded-rule replacement and historical repair explicitly
remain pending, because changing a seed file cannot correct existing ledger state.

## Task 2 — persist decision evidence and preview scope

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
**Branch:** `feature/p1-3-classification-decisions`. **Base and PR target:** `develop`.
**Dependencies:** task 1 and spec review. **Forecast:** 300 production SQL, 450 SQL test lines.
**Outcome:** Knowledge evidence, exact preview items and immutable decision events have a durable
household-scoped store, with constrained states and same-household references.
**Files:** next `db/migrations/V*__classification_decisions.sql`, matching `db/tests/` suite,
`docs/superpowers/schema.md`, `docs/design/data-model.html`.
**Contract:** Spec's evidence/run/decision records; run IDs and versioned before/after derived
state support subsequent preview/apply. Audit existing `audit_log` first; reuse its actor/change
record where equivalent rather than duplicating a second audit system.
**Tests:** Invalid state, cross-household knowledge/transaction reference, original-field payload,
duplicate idempotency key and attempted decision deletion/update are refused; owner can read own
history, cannot read another household's, and correction appends a superseding event.
**Security/invariants:** RLS on every added table; all financial originals remain in their source
tables; bounded reason/evidence fields; no real research material in migrations.
**Validation:** Migrate from empty on the isolated test server; run all SQL invariant suites and
`scripts/check-docs.py`, then full gates.
**Acceptance:** A synthetic preview and correction round trip can be stored without losing prior
derived state or exposing another household.

## Task 3 — guarded preview, apply and undo

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
**Branch:** `feature/p1-3-classification-repair`. **Base and PR target:** `develop`.
**Dependencies:** task 2. **Forecast:** 450 production, 800 test lines.
**Outcome:** Selected defective classifications can be corrected safely through the application.
**Files:** new `api/oneledger/routes/classification_decisions.py`, router registration,
`api/tests/test_classification_decisions.py` and its live tests,
`docs/design/api-contracts.html` and regenerated web API types.
**Contract:** `POST /classification-runs/preview` accepts at most 200 explicit transaction IDs
and intended knowledge version; returns a persisted run ID and exact proposals. Apply takes that
run ID plus idempotency key, never a new selection. Undo takes a decision-run ID and expected
current state. Responses report changed/conflicted/unresolved counts and bounded item reasons.
**Tests:** Preview writes no transaction; apply preserves overrides/linked legs, checks all prior
state and rolls back on conflict; repeated apply is idempotent; undo refuses later corrections;
category and type restore together; selected financial deltas equal frozen values exactly.
**Security/invariants:** require_owner, household checks and one transaction for mutations/history;
no route accepts raw money/description replacement. No transfer linking through classification.
**Validation:** Focused unit/live suites against `scripts/test-db.sh`, full gates.
**Acceptance:** A synthetic wrongly excluded purchase returns to spending; unrelated rows and
historical FX values remain unchanged. After verified encrypted backup, repair only the private
reviewed historical selection and report its exact structural scope.

## Task 4 — merchant and counterparty knowledge

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
**Branch:** `feature/p1-3-counterparty-store`. **Base and PR target:** `develop`.
**Dependencies:** task 2. **Forecast:** 280 production SQL, 420 test lines.
**Outcome:** People, own accounts and intermediaries are distinguishable from merchants without
duplicating the existing business catalogue.
**Files:** next counterparty migration, matching SQL suite, `docs/superpowers/schema.md`,
`docs/design/data-model.html`.
**Contract:** Spec's entity kinds, existing-merchant reference, role-specific aliases and optional
own-account reference; evidence links use task 2. No new provider-specific identifier or enum.
**Tests:** Kind/reference agreement, own-account household validation, aliases across households,
ambiguous shared aliases and deactivation preserving historical decisions.
**Security/invariants:** RLS and never-store constraints; private knowledge is not a global seed.
**Validation:** Empty migration plus full SQL suites and standard gates.
**Acceptance:** One provider can have a wallet and a purchase service; neither becomes a person
or own account through display-name similarity alone.

## Task 5 — knowledge API and shared resolution

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
**Branch:** `feature/p1-3-merchant-knowledge`. **Base and PR target:** `develop`.
**Dependencies:** tasks 3–4. **Forecast:** 450 production, 850 test lines.
**Outcome:** Research and owner answers populate reusable merchant/service knowledge; import and
backfill resolve it identically and do not amplify defective prior classifications.
**Files:** new `api/oneledger/routes/merchant_knowledge.py`, shared ingest knowledge resolver,
`ingest/classify.py`, callers in `ingest/pipeline.py` and `routes/rules.py`, focused tests,
`docs/design/api-contracts.html`, regenerated web API types and `docs/design/classification.html`.
**Contract:** Household-scoped list/create/update/deactivate knowledge operations; explicit
evidence, direction, matcher and category/role outcomes. Pure resolver returns decision or
unresolved reason. Reuse existing rule validation; no public-network calls during import.
Reconcile the owning classification design's first-match contract with knowledge-conflict
abstention and trusted-prior rules in this task, before changing resolver behaviour.
**Tests:** Specific service outranks parent brand; wallet versus merchant; credit/refund distinction;
Unicode and concatenated narration ambiguity; conflicting aliases abstain; known-defective and
uncertain prior results cannot silently supply a report-excluding type.
**Security/invariants:** Public evidence URLs are stored as text, never fetched by arbitrary URL;
no SSRF path, raw export or report bypass. Existing manual decisions retain precedence.
**Validation:** Targeted import/backfill parity tests, owner-isolation live tests, full gates.
**Acceptance:** Import and preview give the same result for a held-out statement; researched
private knowledge can be applied through task 3 without committing it or copying it to production.

## Task 6 — grouped exceptions, explanations and remembered corrections

**Workspace:** `/Users/sandjaieravi/dev/OneLedger`.
**Branch:** `feature/p1-3-classification-review`. **Base and PR target:** `develop`.
**Dependencies:** task 5. **Forecast:** 550 production, 650 test lines.
**Outcome:** Recognised rows need no classification click; exceptions are grouped, answers can
be remembered narrowly, and changes show why and how to undo.
**Files:** transaction/review web components, dedicated classification review component, API
client and generated types, component tests; small bounded grouping read endpoint if required.
**Contract:** Task 3 preview/apply/undo; task 5 knowledge writes. One-row exceptions and reusable
answers are separate actions; remembered rules always show affected scope before retrospective use.
**Tests:** Empty/error/loading states, grouped ambiguity, broad category fallback, excluded
economic-role warning, stale preview conflict, preserved manual override and conditional undo.
**Security/invariants:** No financial arithmetic in JavaScript; no raw PDF text or personal
counterparties in analytics; existing authentication, keyboard and accessibility patterns.
**Validation:** Component tests, both type compilers, API type drift, build, full gates.
**Acceptance:** The owner answers a recurring pattern once; the next imported occurrence is
classified and explained, while an exception stays intact.

## Separate follow-on work (not dispatched by this plan)

**Investment source coverage — slice 1.5.** The local collection includes detailed and summary
investment statements. Extend that slice's import plan for transactions, holdings and valuation
reconciliation; hash/folio-period overlap must prevent double-counting summary and detailed
reports. Bank debits alone cannot fill missing units or valuation fields.

**Local-model evaluation — Phase 5.** Before selecting software, inventory local hardware and
licence constraints, build a private held-out benchmark from resolved exceptions, and compare
rules-only versus local-model proposals. Apply the spec's egress, logging, structured-output,
adversarial-input and no-write constraints. Record precision/coverage by economic role and the
abstention trade-off. Runtime implementation needs its own spec and superseding ADR; no model
download, cloud API call or automatic promotion is part of this plan.
