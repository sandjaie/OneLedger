# Phase delivery plan

**The master delivery plan, 9 September 2026.** It supersedes the sequencing in
[implementation-plan.md](implementation-plan.md) and the undispatched task table (5d–9c) of the
[M2 plan](2026-09-07-m2-statement-importer.md); dispatched and merged work keeps its old numbering
as the historical record. Product scope is unchanged — everything the
[roadmap](../../product/roadmap.html) defines still ships. What changes is the shape: **breadth
first**. Phase 1 delivers every feature in its simplest working form, so the owner runs and uses
the complete application at its end; later phases add the depth and the hardening.

**One term: phase.** The old vocabulary (Phase 0/1/2, milestones M1–M10, V2) confused more than
it ordered. From here, the unit of delivery is the phase, sub-sliced as 1.1, 1.2, …; the mapping
to the old names is recorded below so old documents stay readable.

**The delivery cycle** is [git-workflow.md](../../git-workflow.md)'s:
plan → implement → local review → merge. External (Codex) review is disabled for this repository
by owner decision, 8 September 2026.

## Phases

| Phase | Delivers | Old name |
| --- | --- | --- |
| 0 — foundations and manual ledger | **Delivered.** Stack, auth, FX, accounts, manual transactions, import backend through document storage | Phase 0, M1a, M1b, M2 tasks 1–5c |
| 1 — the complete application | Every feature, simplest working form; the owner uses the app daily | M2 remainder, M3–M7, M9a thin, parts of 2A/2C pulled forward |
| 2 — trust and depth | Full 36-month history, attribution, learn-from-correction, snapshots, data-quality screen | M9b, M4/M9 remainder, doctor |
| 3 — planning and resilience | Commitments lifecycle, forecast with assumptions, goals, emergency reserve, protection | Phase 2A full, Phase 2B |
| 4 — analysis | Budgets, recurring discovery, salary analytics, benchmarks, FX-provider costs, email ingestion | Phase 2C |
| 5 — intelligence | Ask My Finances, AI classification fallback, insights, tax reporting, automated imports | V2 |

Rules that bind in every phase, unchanged: exact money (`Decimal` / `NUMERIC` / string on the
wire), no institution or currency identifiers, `require_owner()` as the boundary, reporting via
`v_transaction_reportable`, forward-only Flyway migrations, the never-store list — masking and
hashing are **already built** and stay — and no LLM call before Phase 5 (ADR-0003; V2 is now
Phase 5). Loans (old M8) stay descheduled pending an actual loan.

## Phase 1 — the complete application

Exit criteria: every section of the app exists and has its own import path; all 71 gathered
statements import and reconcile; classification and transfer matching make monthly spending
honest; the dashboard shows net worth in both currencies with a 12-month trend; investments,
payslips and a simple forecast are in; a new bank layout can be mapped through the UI. The owner
uses the application for real from slice 1.1 onward.

Slices ship in order. Each is one or more branches from `develop`, each branch one PR, titled
`[P1.<slice>] type(scope): …`. `Workspace` for every slice: the active checkout
(`git rev-parse --show-toplevel`). `Base and PR target` for every branch: `develop`. A slice's
detailed contracts live in the [M2 spec](../specs/2026-09-07-m2-statement-importer.md) and the
design documents it cites; the implementer inspects the tree and records deviations in the PR.

### 1.1 — statements land in the ledger

**Outcome.** A stored card statement runs extraction → normalization → dedupe → FX → reconciliation
into staged rows via `POST /parse`; reviewed rows commit atomically to the ledger; the owner
drives it from `/import` and a review screen.
**Covers (old numbering).** M2 5d, 5e, 6a–6d, 7a, 7b, 9a, plus the card layout profile seeded as
data (the mapper UI arrives in 1.7).
**Branches**, revised by `/oneledger-plan-review` on 9 September 2026 and by the owner decisions
it raised, then twice again in delivery where a branch met the diff budget's hard stop. All carry
the `[P1.1]` prefix and each bases off `develop` after the one before it merges:

1. `fix/p1-1-card-layout-fitness` — the balance-frame semantics the card needs before anything
   can reconcile, and the card profile as data.
2. `feature/p1-1a-parse-pipeline` — `ingest/pipeline.py`, identification and the staging
   transaction. Split from 3 during delivery: written as one branch the pair measured 1,233
   production and 2,545 counted lines, past the 1,200 / 2,400 hard stop.
3. `feature/p1-1a-parse-route` — `POST /parse` and its wire contract.
4. `feature/p1-1b-commit-functions` — V14's `commit_import_batch` and
   `assert_account_reconciles`, and `db/tests/0012`. Forecast ~250 production / ~350 test.
5. `feature/p1-1b-review-routes` — `GET /import/{batch}`, `PATCH /import/{batch}/rows` and
   `POST /import/{batch}/commit`. Forecast ~420 production / ~700 test. Split from 4 because the
   two together forecast ~670 / ~1,720 against the 800 / 1,600 budget, and 1.1a's own forecast
   was comfortable before its review round added a third again.
6. `feature/p1-1c-import-screen` · 7. `feature/p1-1d-review-screen` — the UI is two branches
   because one forecast at ~950–1,150 production lines against the 800 budget.
**Invariants.** Reconciliation blocks on mismatch (I8); commit is one transaction with
post-commit balance verification; re-import of a committed file imports zero rows; a second
staging is refused for an account and period that already has an unreviewed batch (spec §7
item 11).
**Security.** Every route behind `require_owner()`; no digit run of seven or more from the
document in any log line, error or response; the uploaded original is written under its
checksum, never under its own name.
**Tests.** Pipeline behaviours per module; SQL tests for V14; an induced mid-commit failure
leaves zero rows.
**Validation.** `/verify`, then `/oneledger-code-review`, then `/verify` again, per
[development-cycle.md](../../development-cycle.md).
**Accept when.** The 38 card statements import end-to-end with no manual correction; duplicates
name the earlier import. That run is **local and owner-driven** — the statements are git-ignored
and never enter the repository, so the committed evidence is the synthetic-fixture suite and the
run itself is recorded in the PR's Verification table.

### 1.2 — all four bank layouts

**Outcome.** The remaining three layouts import: password-protected PDFs (per-file prompt,
nothing persisted), multi-line descriptions as a profile rule, no-space extraction via column
bands, running-balance reconciliation. Profiles seeded as data; redacted fixtures with
golden-output tests.
**Branches.** `feature/p1-2a-engine-hardening`, `feature/p1-2b-remaining-layouts`.
**Accept when.** All 71 gathered files import and every account reconciles across its period; an
encrypted statement imports without its password touching disk.

### 1.3 — classification and transfers

**Outcome.** The classification cascade (override → user rule → merchant alias → prior match →
seed rule) with rule CRUD and a personal seed set (~30 rules; the 120-rule pack and
learn-from-correction analytics are Phase 2). The transfer matching engine with FX-aware amount
tolerance and kind-specific date windows; nothing auto-links — confirmation happens in review;
manual link/unlink from the transactions screen.
**Branches.** `feature/p1-3a-classification`, `feature/p1-3b-transfers`.
**Invariants.** Rules never silently change past data; a confirmed transfer appears in neither
spending nor income; reports read `v_transaction_reportable` only.
**Accept when.** A real remittance and a card payment are detected and excluded; monthly spending
for one hand-tallied month matches exactly.

### 1.4 — cards, net worth and trends

**Outcome.** Card statement facts captured at import (statement balance, minimum due, due date,
credit limit) with a dues view; the dashboard: signed-balance net worth in both currencies,
monthly income/spending/net, savings rate, 12-month trend.
**Branches.** `feature/p1-4a-cards`, `feature/p1-4b-dashboard`.
**Accept when.** Dues match the card statement; net worth equals the sum of signed balances in
both currencies; a payment moves no spending figure.

### 1.5 — investments

**Outcome.** `security`, `holding`, `investment_transaction`, `price_snapshot`; AMFI daily NAV
import for Indian mutual funds; CAS/broker-statement import where parseable with manual entry as
the fallback; XIRR (Newton–Raphson, bisection fallback); allocation across classes; the portfolio
screen; a purchase linked to its funding bank debit.
**Branches.** `feature/p1-5a-investment-schema-api`, `feature/p1-5b-nav-and-imports`,
`feature/p1-5c-portfolio-screen`.
**Accept when.** XIRR for a known SIP series matches a spreadsheet within 0.01%; Σ transaction
units equals holding units per holding; a USD holding prices correctly in both reporting
currencies.

### 1.6 — salary and a thin forecast

**Outcome.** Payslip import into income line items on the ledger (pulled forward from old 2C);
commitments CRUD (amount, currency, date, account, recurrence); a simple 30/60/90-day projection
per account from dated balances and commitments, its assumptions stated on screen. The full
planning lifecycle (settlement matching, actions) is Phase 3.
**Branches.** `feature/p1-6a-payslips`, `feature/p1-6b-commitments-forecast`.
**Accept when.** A real payslip imports and reconciles against the salary credit; a commitment
moves the projection; nothing here writes to the ledger (a forecast is never a posting).

### 1.7 — the layout mapper and documents

**Outcome.** The mapping UI (find header row, auto-propose column bands, live parsed preview,
drag and relabel, save) so any new bank is data, not code — the open-source story; the document
library (list, filter, open, transaction links); the import diagnostics panel.
**Branches.** `feature/p1-7a-layout-mapper`, `feature/p1-7b-documents-diagnostics`.
**Accept when.** A synthetic statement in an unseen format is mapped and imported without
touching the codebase (the ADR-0012 acceptance test); every imported transaction links back to
document, page and row.

## Phases 2–5 — planned when they start

Per the standing granularity rule, each later phase gets its dated plan written immediately
before its implementation, against the tree at that time. Their content, fixed now so nothing is
lost:

- **Phase 2 — trust and depth.** Import and reconcile the full 36 months across all layouts
  (ADR-0011 anchor); month-end snapshots and the net-worth history; the attribution waterfall
  with visible residuals (old M9b); learn-from-correction, confidence scoring and rules
  analytics; the diagnostics/data-quality screen (`GET /doctor`); backup restore drill.
- **Phase 3 — planning and resilience.** Full commitments lifecycle with settlement matching and
  actions; forecast with cancellation/matching behaviour; goals with exclusive earmarking;
  emergency reserve and essential-spend baseline; insurance, maturities, claims, renewals.
- **Phase 4 — analysis.** Budgets vs actual with rollover; recurring-expense discovery;
  salary analytics depth; benchmarks and allocation drift; FX and remittance provider analytics;
  email ingestion; the loan simulator if a loan ever schedules old M8.
- **Phase 5 — intelligence.** The privacy decision first, then Ask My Finances, the AI
  classification fallback, insights, the scenario simulator, tax reporting, automated imports
  (ADR-0003).

## Verification for this documentation change

```bash
python3 scripts/check-docs.py
git diff --check
```

Expected: no broken links, no malformed tables, no whitespace errors. Feature verification
belongs to each slice's own tests, recorded in [progress.md](../progress.md) at task boundaries.
