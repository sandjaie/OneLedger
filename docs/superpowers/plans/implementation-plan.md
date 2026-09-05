# OneLedger Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to execute a scoped task plan.
> Use `superpowers:subagent-driven-development` when parallel agent work is explicitly authorized.
> This is the master sequencing plan; each feature has its own detailed plan with checkbox steps,
> exact files/interfaces, failing checks, implementation and verification before execution.

**Goal:** Complete Phase 0, then build a trusted financial position, a view of upcoming cash needs,
and the goals, resilience and follow-up capabilities of a personal finance operating system.

**Architecture:** Preserve the existing three-service boundary: React renders, Python owns domain
logic, Postgres enforces invariants. Future planning records are separate from posted financial
history; a forecast, reservation or action never silently becomes a ledger posting.

**Tech Stack:** Existing Postgres 18, Python 3.14/FastAPI and Next.js/TypeScript stack. No new
infrastructure or dependency is selected by this documentation update.

**Spec:** [PRD](../../product/prd.html), [finance OS requirements F01–F12](../../product/finance-os.html),
[roadmap and current status](../../product/roadmap.html),
[UI requirements](../../product/ui-information-architecture.html),
[data model](../../design/data-model.html), [FX and attribution](../../design/multi-currency-and-fx.html),
[transfers](../../design/transfers-and-reconciliation.html),
[security](../../operations/security-and-privacy.html),
[testing](../../operations/testing-and-data-quality.html).

## Global constraints

- Current phase is **Phase 0**, on integration branch `feature/phase-0-foundations`.
- GitHub PRs **#3–#7 are merged** into that branch; do not repeat them or add product-expansion work to Phase 0.
- Postgres 18, Python 3.14 and Node 24 LTS remain the documented baseline.
- Money is `NUMERIC(20,4)` in Postgres, `Decimal` in Python and a string on the wire. FX rates are
  `NUMERIC(20,10)`; units `NUMERIC(28,8)`; prices `NUMERIC(20,6)`; percentages `NUMERIC(9,4)`
  in human-readable units (`15.2500` means 15.25%). No float money or frontend money arithmetic.
- Business dates are `DATE`; audit timestamps are `TIMESTAMPTZ` in UTC. Reporting year is calendar.
- Reporting currencies are configuration. Preserve frozen historical amounts and the
  **01 Jan 2024 history anchor** (ADR-0002 / ADR-0011).
- Consumption, budget and spending reports use `v_transaction_reportable` and frozen amounts.
  External household outflows have a separate guarded view; combining the two for retained income
  must not put internal transfers back into spending. Forecast and balance translation are distinct
  calculations and must identify their dates and assumptions.
- Every household-scoped table has household identity and RLS. The API owner check remains the
  authorization boundary. No financial data, passwords or secrets enter the repository or logs.
- Migrations are forward-only and applied by Flyway. **V1–V4 are completed work**; do not rewrite
  them. Design future schema changes with their feature and update the schema reference concurrently.
- No institution, country or currency becomes a hardcoded domain identifier. Reuse existing patterns.
- No LLM calls in Phase 1 or Phase 2. No automatic payments, trades or new shared access.
- Read ADRs as decisions, design/operations as specifications and plans as execution instructions.
  Resolve conflicting specifications explicitly before implementing the affected feature.
- Preserve the current repository review process: scoped branches, at most 500 lines of implementation
  and test code per PR (docs/config/generated excluded), required checks and Codex review before merge.
- New implementation plans go in `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`.

## 1. Baseline and the next authorized development step

The owner confirmed GitHub #7 merged on 6 September 2026. Local integration history contains the
FX feature (`fea3404`) and review fixes through `fd6b5db`, with `e122b61` at the checked HEAD.
The GitHub connector did not independently return PR metadata; merge status is owner-confirmed,
and code presence was checked locally. This is not a claim that the phase-exit suite was re-run.

| GitHub PR | Completed scope | Original task numbers | Original PR ordinal |
|---|---|---|---|
| #3 | Compose definition, environment template, secret hygiene | 1 | 1 |
| #4 | V1–V2 reference data, identity, RLS | 2–3 | 2 |
| #5 | V3–V4 accounts and FX rates | 4–5 | 3 |
| #6 | FastAPI, `require_owner`, `resolve_owner`, `connect` | 6 | 4 |
| #7 | FX source, peg derivation, carry-forward, backfill; three review rounds reported | 7–8 | 5 |

**GitHub #7 is not the original plan's “PR 7.”** The latter is the still-pending Google OIDC and
money-formatting work. Do not mark it complete because the numbers happen to match.

### Finish Phase 0

Files and runnable steps remain in the existing plans; do not duplicate their code in this master.

- [x] Record completed GitHub PRs #3–#7 and verify FX code is in the integration history.
- [ ] Execute [Task 9: web shell and typed client](../phase-0-foundations.md#task-9-nextjs-shell-with-a-typed-api-client)
  using [original PR 6](../phase-0-pull-requests.md#pr-6-nextjs-scaffold-and-the-typed-api-client).
  Deliverable: web scaffold/client, buildable against the current API, without secrets in its bundle.
- [ ] Execute [Task 10: Google OIDC](../phase-0-foundations.md#task-10-google-oidc-in-the-service-with-a-pinned-account)
  and [Task 11: money and navigation](../phase-0-foundations.md#task-11-money-formatting-currency-toggle-and-navigation)
  using [original PR 7](../phase-0-pull-requests.md#pr-7-google-oidc-the-seed-money-formatting-and-the-toggle).
  Deliverable: permitted sign-in, rejected unauthorized identity, seeded owner, currency display and navigation.
- [ ] Run the existing [Phase 0 exit criteria](../phase-0-foundations.md#phase-0-exit-criteria)
  and [closing review gate](../phase-0-pull-requests.md#closing-the-phase). Record actual results.
  The compose definition alone does not prove a working web service; merged code alone does not prove
  a real Google sign-in or a populated history range.
- [ ] Mark Phase 0 complete in the roadmap only after those checks and the required integration review.

This documentation task does not execute any of those unchecked development steps.

## 2. Phase 1 — trust my position

Build order:

```text
Phase 0 → M1 → M2 → M3 + M4 → M5 → M9a
                                      ↓
                              M6 / M7 / M10
                                      ↓
                                     M9b
M8 is conditional on an actual loan; it does not block unrelated work.
```

### Task group A: Accounts, imports and classification — M1–M4

**Read:** PRD §5.1–5.5, ingestion, classification, finance OS F01/F05/F06.
**Plan outputs:** one dated plan each for M1, M2, M3 and M4 in this directory.
**Files owned by those plans:** `api/oneledger/routes/`, extraction/normalization/classification
modules when created, relevant `web/` screens, forward migrations and existing schema/test references.
Choose exact new filenames and API contracts from the working tree when each plan is written.

- [ ] Plan and implement account inventory, opening-balance evidence and closed-account history.
- [ ] Deliver one PDF layout end-to-end, then the remaining three, with statement reconciliation.
- [ ] Deliver classification with the economic meaning of refunds, external support and investment funding.
- [ ] Test one complete recent month, explicitly listing missing accounts/periods. Keep the long-history
  collection task open; do not misrepresent this usability checkpoint as Phase 1 completion.

### Task group B: Transfers and household outflows — M5

**Read:** transfers specification, finance OS F01–F04 and P02/P04.
**Plan output:** a dated M5 plan, including reporting-view definitions and pending/settled semantics.

- [ ] Specify the household boundary and internal/external transfer confirmation before matching implementation.
- [ ] Implement both-leg and pending-transfer review; settlement clears in-transit presentation without income.
- [ ] Add the external-outflow reporting contract while preserving I3's consumption exclusion.
- [ ] Prove P02: income 10,000 minus consumption 3,000 minus family support 2,000 retains 5,000 (50%).
  A separate internal transfer changes neither figure. Prove P04 for different posting dates.

### Task group C: Early verified overview — M9a

**Read:** UI overview, finance OS F02/F05 and P03/P04. Depends on M5.
**Plan output:** a dated M9a plan with dated-position and coverage contracts.

- [ ] Deliver balances for included accounts, spending, external outflows, retained income and review links.
- [ ] Show statement, price and FX dates; label partial coverage and estimates individually.
- [ ] Allow dated manual valuations without double counting assets when detailed holdings arrive.
- [ ] Verify P03: refreshing September prices leaves August cash evidence visibly dated August.
- [ ] Demonstrate a recent-month review without requiring the full investment module or document library.

### Task group D: Cards, investments and owner documents — M6/M7/M10

**Read:** PRD §5.7–5.11, finance OS F06/F12 and P11/P12.
**Plan outputs:** separate dated M6, M7 and M10 plans; each stands on its own.

- [ ] M6: deliver dues and payment status with purchase spending separated from bill settlement.
- [ ] M7: preserve investment history through dividends, reinvestment, partial sales, charges, security
  splits and platform transfers for the holdings owned. Define unsupported-event review before showing returns.
- [ ] M10: deliver document retrieval and owner export with open tabular records, selected originals,
  provenance and coverage manifest. Enforce owner-only access and temporary-file cleanup.
- [ ] Verify lifecycle continuity (P12) and independently interpretable, authorized export (P11).

### Task group E: Full wealth history and attribution — M9b

**Read:** FX §7, data-model §14, finance OS F03 and P01/P12. Depends on M7 and valuation inputs;
card/debt results are included only when their evidence is available.
**Plan output:** a dated M9b plan with the complete attribution formula and independent worked examples.

- [ ] Reconcile the unapplied `0014` reference shape with the corrected consolidated attribution before
  writing a migration; update `docs/superpowers/schema.md` and `docs/design/data-model.html` together.
- [ ] Define cancellation of internal funding, investment-income treatment, and FX on intra-period flows.
- [ ] Deliver consolidated history and the explanatory waterfall, with other external changes and residuals visible.
- [ ] Prove P01: funding an investment or repaying principal from existing cash changes net worth by zero
  before costs. Add dated FX and dividend examples that cannot pass by hiding the difference in residuals.
- [ ] Reconcile every included native balance and surface real discrepancies separately from valuation uncertainty.
- [ ] Complete the history back to 01 Jan 2024 and the remaining Phase 1 roadmap exit gates.

## 3. Phase 2 — operate and plan

Entry gate: Phase 1 exit criteria met and at least two months of maintenance. These are new feature
plans, not additions to Phase 0's schema. Keep the existing analysis modules; sequence the usable
planning primitives first.

| Sequence / plan boundary | Scope and interfaces to settle in the detailed plan | Acceptance |
|---|---|---|
| 2A.1 Commitments and actions | Expected amount/currency/date/account, certainty, recurrence, partial/full settlement; one source-linked action lifecycle | P05/P10: an actual payment clears its future obligation; recurring actions do not duplicate |
| 2A.2 Cash forecast and review | Consumes dated balances and commitments; returns 30/60/90-day account/currency projections, lowest balance/date, gaps and assumptions | P05/P06: card spending counted once; a 500 shortage is visible in the paying account |
| 2B.1 Shared reservations, emergency reserve and goals | Shared resource-allocation rule; eligible resources, essential-spend baseline, goal currency/date/target, contributions and shortfall | P07/P08: no double reservation; 6,000 / 2,000 gives three months; unknown baseline is not infinite coverage |
| 2B.2 Protection and administration | Policy coverage/renewals, deposit maturity, claim/reimbursement balances and user-entered deadlines; reuse documents/actions | P09/P10: a 400 settlement leaves 600 of a 1,000 claim; renewal action resolves correctly |
| 2C Analysis modules | Separate plans for budgets, recurring discovery, salary, benchmarks/allocation drift, FX-provider analysis and optional email ingestion | Preserve roadmap acceptance; no automatic financial actions or AI |

- [ ] Write and execute the 2A.1 plan before the forecast plan; a manually confirmed commitment does not
  depend on recurring detection or payslip parsing.
- [ ] Write and execute 2A.2 with visible assumptions and cancellation/matching behavior.
- [ ] Design the shared reservation rules in 2B.1 before separate goal and emergency presentations.
  A reservation earmarks funds; paying the associated obligation consumes/releases the reservation so
  forecast obligations and earmarking never subtract the same purpose twice.
- [ ] Execute 2B.2 using the established actions/documents rather than separate notification systems.
- [ ] Write 2C plans only for the next needed analysis module. Loan simulation depends on M8 if a loan exists.

## 4. Deferred decisions and prerequisites

- Gather missing calendar-2024 statements; request CAS history back to 01 Jan 2024.
- Resolve the existing questions about retirement accounts, property and loans when their milestones need them.
- Choose actual goal targets, essential-spend assumptions, emergency resources and policy records during
  product use; never commit personal balances, identity numbers or passwords into a plan.
- Retirement/inflation scenarios require explicit assumptions. Tax calculations stay in V2;
  record organisation and owner exports do not wait for AI.
- Emergency handover is a recorded gap, not approved shared access. A separate product/privacy decision
  is required before offering a trusted person access. Advisor sharing and collaboration stay excluded.
- Bank integrations, AI, trading and automated payments are not introduced by this revision.

## 5. Detailed-plan contract and verification

Before implementation, each dated feature plan must identify exact files, data/API contracts, dependencies,
code-level steps and runnable checks against the current tree. Write the smallest failing scenario,
implement the behavior, run the check, then run the relevant existing suite and review the diff.
Do not invent all later subsystem interfaces while Phase 0 is unfinished.

Every finance OS requirement maps to a task group: F01/F02/F04 → A/B/C;
F03 → E; F05 → A/C; F06 → A/D/E; F07/F08 → 2A; F09/F10 → 2B.1;
F11 → 2B.2; F12 → D. P01–P12 are the product gates, with milestone ownership in the specification.

For documentation-only updates, run:

```bash
python3 scripts/check-docs.py
git diff --check
```

Expected: no broken links, mismatched ADR labels, malformed HTML or tracked real statements;
no whitespace errors. This does not prove the financial scenarios: add their runnable checks in the
feature that implements them, and record phase/milestone state only after verification.
