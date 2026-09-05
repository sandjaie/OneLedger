# OneLedger Implementation Plan

> **For agentic workers:** this is the **master plan**. It sequences the work and defines the
> global constraints, but it is not itself executable task-by-task. Each milestone has its own
> plan in this directory, written just-in-time. Execute those with
> `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
>
> **Phase 0 is decomposed into seven pull requests** in
> [phase-0-pull-requests.md](./phase-0-pull-requests.md) — branch names, the review gate and
> the 500-line cap. Read it alongside `phase-0-foundations.md`: that one says what to type,
> this one says what to ship.

**Goal:** Build a single normalized ledger of every financial event, across however many countries
the household spans, with the consolidated cash-flow, portfolio and net-worth views that only
become possible once that ledger exists. The first deployment spans the UAE and India.

**Architecture:** Three pieces, one job each
([ADR-0013](../adr/0013-python-backend-service.html)). **React** renders. **Python (FastAPI)**
computes — parsing, normalization, classification, transfer matching, XIRR — and is the
authorization boundary. **Postgres** guarantees, through constraints, triggers and the reporting
views, because a backend is a convenient place for logic and a poor place for guarantees. The
service runs on `localhost` through Phase 1, so statements never transit a third-party host.

**Tech Stack:** Postgres 18 · Python 3.14 · FastAPI · Pydantic · `pdfplumber` · uv ·
ruff · mypy --strict · pytest · hypothesis · Next.js App Router · React · TypeScript · Tailwind ·
shadcn/ui · Recharts · `decimal.js` · vitest · Playwright

**Spec:** the whole of [`docs/`](../index.html). Per milestone, the governing documents are named
in that milestone's row below. Executors read the spec, not only the plan.

---

## Global Constraints

Project-wide. Every task's requirements implicitly include this section.

**Money**

- Every monetary value is `NUMERIC(20,4)` in Postgres. **Never** `float`, `real` or
  `double precision`. Units are `NUMERIC(28,8)`, prices `NUMERIC(20,6)`, FX rates `NUMERIC(20,10)`,
  percentages `NUMERIC(9,4)`.
- Percentages are stored and transmitted **human-readable**: `15.2500` means 15.25%, never `0.1525`.
- Money is `Decimal` in Python, `NUMERIC` in Postgres, and a **string** on the wire
  (`"-310.0000"`) — a JSON number is an IEEE-754 float before anyone can check it.
- TypeScript formats money and never computes on it. `parseFloat` / `Number()` on a money field is
  a lint failure.
- No arithmetic on money in React components. Formatting only. Aggregation happens in Postgres.

**Dates**

- A transaction happens on a **date**, not an instant. Business dates are `DATE`, never
  `TIMESTAMPTZ`. Audit timestamps are `TIMESTAMPTZ`, always UTC.
- Reporting year is the **calendar year** (Q20). History is anchored at **01 Jan 2024**
  ([ADR-0011](../adr/0011-36-month-history-horizon.html)).

**Correctness invariants** — the eight in [01-prd.md](../product/prd.html) §4. Each has a test in
[12-testing-and-data-quality.md](../operations/testing-and-data-quality.html) §1. Two are easy to
violate accidentally and are enforced statically:

- Every cash-flow, budget, spending and savings-rate query reads `v_transaction_reportable`,
  **never** the base `transaction` table.
- Reports read the frozen `amount_base` / `amount_secondary` columns. No report query multiplies `amount`
  by a rate.

**Security** — [09-security-and-privacy.md](../operations/security-and-privacy.html)

- The never-store list (§1) is absolute. No full account numbers, card numbers, CVVs, PINs, OTPs,
  passwords, or full PAN / Emirates ID. Masked display value + salted hash only.
- **No real financial data enters this repository, ever** — not in `main`, not in a branch, not in
  history. Test fixtures are redacted. `pdf-statements/`, `*.pdf` and `.env` are git-ignored.
- RLS enabled on every household-scoped table from the first migration, even with one user.
- No secret is ever prefixed `NEXT_PUBLIC_`. `NEXT_PUBLIC_API_URL` is the only variable that carries
  the prefix, and it holds a URL.

**Process**

- Migrations are plain, **forward-only** SQL run by Flyway (ADR-0015). No down-migrations.
- TDD: write the failing test, watch it fail, implement minimally, watch it pass, commit.
- Near-zero infrastructure spend: local while building, one small VPS once hosted (ADR-0014).
- **No LLM calls in Phase 1 or Phase 2** ([ADR-0003](../adr/0003-defer-ai-to-v2.html)).

---

## Decomposition

Ten milestones, each an independently testable subsystem with its own plan. Plans are written
just-in-time — decomposing M7 into steps before M2 has taught us how the parsers actually behave
would be writing fiction.

| Plan | Milestone | Deliverable | Governing spec |
|---|---|---|---|
| [`phase-0-foundations.md`](phase-0-foundations.md) | **Phase 0** | Schema, RLS, the API service, auth, FX backfill, UI shell | [02](../design/architecture.html), [03](../design/data-model.html), [ADR-0013](../adr/0013-python-backend-service.html) |
| `02-m1-accounts.md` | **M1** | Account CRUD, manual entry, signed balances | [01](../product/prd.html) §5.1–5.2, [03](../design/data-model.html) §5 |
| `03-m2-extraction-engine.md` | **M2** | Generic engine + layout mapping UI, proven on one layout | [05](../design/statement-ingestion.html), [ADR-0012](../adr/0012-statement-layouts-as-data.html) |
| `04-m3-layouts.md` | **M3** | The remaining three layouts, mapped not coded | [05](../design/statement-ingestion.html) §13 |
| `05-m4-classification.md` | **M4** | `match_classification_rules()`, seed rules, learn-from-correction | [06](../design/classification.html), [ADR-0013](../adr/0013-python-backend-service.html) |
| `06-m5-transfers.md` | **M5** | `score_transfer_candidates()`, review confirmation, FX cost | [07](../design/transfers-and-reconciliation.html) |
| `07-m6-cards.md` | **M6** | Statement cycles, utilisation, paid-in-full | [01](../product/prd.html) §5.7, [ADR-0009](../adr/0009-credit-card-statement-cycles.html) |
| `08-m7-investments.md` | **M7** | CAS import, holdings, XIRR, allocation | [01](../product/prd.html) §5.8, [ADR-0005](../adr/0005-transaction-level-investment-lots.html) |
| `09-m9-networth.md` | **M9** | Net worth, attribution, the dashboard | [04](../design/multi-currency-and-fx.html) §7, [11](../product/ui-information-architecture.html) §2 |
| `10-m10-documents.md` | **M10** | Document vault, bidirectional traceability | [01](../product/prd.html) §5.11 |

**M8 (Loans) has no plan and is not scheduled.** No loan appears in any of the 71 gathered
statements and Q18 is unanswered. The schema ships in Phase 0 regardless, so scheduling it later
costs no migration.

---

## Build order

```
Phase 0  Foundations
   │
   ▼
  M1  Accounts + manual transactions
   │
   ▼
  M2  Extraction engine + mapping UI         ◄── first genuinely useful point
   │
   ├──────────────┐
   ▼              ▼
  M3  Layouts    M4  Classification
   │              │
   └──────┬───────┘
          ▼
        M5  Transfers                        ◄── first point the numbers are trustworthy
          │
   ┌──────┼──────────┐
   ▼      ▼          ▼
  M6     M7         M10
   └──────┴──────────┘
          ▼
        M9  Net worth + dashboard            ◄── the morning screen
```

Two ordering decisions are worth stating, because both are counterintuitive:

**M2's deliverable is the engine, not a bank.** No institution is named in code
([ADR-0012](../adr/0012-statement-layouts-as-data.html)); M2 builds the generic extraction engine and
the layout mapping UI, then proves both by mapping one layout through the UI exactly as a future
bank would be. The card statements are that layout — they declare their own reconciliation anchors
in a header block, are not encrypted, and one profile covers both cards, which is 38 of the 71
files. The current account, despite being the more important account, has only a running balance
column and two physical lines per transaction; starting there to reach the more valuable data
sooner is the trade the roadmap's "usefulness early" principle explicitly warns against.

**Nothing is trustworthy before M5.** Between M2 and M5 every spending figure is inflated by the
full value of every remittance, card payment and SIP debit. The tool is *usable* at M2 and
*correct* at M5. Do not draw conclusions from a cash-flow number before then.

---

## Prerequisites — not code

These are the user's tasks, and two of them gate milestones.

| # | Task | Gates |
|---|---|---|
| P1 | Gather calendar-2024 statements for all seven accounts | Phase 1 exit criterion 1 |
| P2 | Request a consolidated CAS from CAMS or KFintech covering 01 Jan 2024 onward | **M7** |
| P3 | Move `pdf-statements/` out of the repo to `~/statements/`, on the FileVault volume, excluded from iCloud sync | Nothing, but do it now |
| P4 | Have the password to hand for the encrypted statements — typed in, never stored | **M3** |
| P5 | Answer Q17 (PPF/EPF/NPS), Q21 (property) | M7 and M9 *completeness*, not their start |
| P6 | Answer Q18/Q19 if any loan exists | M8 scheduling |

**P3 deserves emphasis.** The statements currently sit inside the git working tree. They are
git-ignored, and history is clean — but a directory that is one `git add -f` away from being
committed is the wrong place for 71 files with full card numbers in their names.

---

## Definition of done, per milestone

From [12-testing-and-data-quality.md](../operations/testing-and-data-quality.html) §8. A
milestone that is "done except for tests" is a milestone whose numbers cannot be trusted, which
defeats the purpose.

```
☐  Feature works against real data, not only fixtures
☐  Unit tests cover the calculation logic
☐  Any invariant the milestone touches has a test
☐  Settings › Data Quality reports no new warnings (`GET /doctor` — there is no CLI, ADR-0013)
☐  Migrations apply cleanly from an empty database
☐  Types regenerated; schema parity test passes
☐  Docs updated where behaviour changed
☐  Manual verification against a real statement or a bank app
☐  No secrets, no real financial data in the diff
```

---

## What would make this plan wrong

Recorded so the assumptions are checkable rather than implicit.

- **The four layouts are stable across 36 months.** Verified for Jan 2025 – Sep 2026. The 2024
  statements are unseen; if a layout changed, it costs a re-map through the UI and a profile
  version bump — not a code change. Reconciliation (I8) catches it as a hard failure rather than
  silent corruption, which is the entire reason that check is non-negotiable.
- **Auto-proposal works from the header row.** Verified: all four gathered layouts declare one.
  A layout with no header row would fall back to fully manual band-dragging, which the UI must
  therefore still support.
- **The quirk vocabulary stays small.** Two entries cover the four known layouts. If it grows
  per-bank, [ADR-0012](../adr/0012-statement-layouts-as-data.html) has failed and the honest response
  is a superseding ADR, not a longer list.
- **The CAS covers every folio.** If ETMoney holds anything outside CAMS/KFintech registrars, M7
  needs a second source and grows.
- **Matching in SQL is fast enough.** A set-based match over a ~250-row staged batch should be
  milliseconds. If it is not, the fallback is a materialised rule-match table, not a second
  implementation elsewhere.
- **Column banding resolves all four layouts.** Measured: one layout extracts with no space
  characters at all, and word grouping does not rescue that in any language — x-position banding
  does. If a real layout defeats it, the failure surfaces in M2/M3 as a batch that cannot
  reconcile, loudly, per Invariant I8.
- **The service being on the read path is acceptable while everything is local.** It is the first
  thing to reconsider when the cloud question is taken up
  ([architecture](../design/architecture.html) §11), and the reason that section now lists three
  options rather than asserting one.
- **Postgres functions are testable enough to keep confidence.** If database testing proves too
  painful to sustain, revisit [matching in Postgres](../adr/superseded.html#matching-in-postgres) — but
  revisit it with a superseding ADR, not by quietly reintroducing a second matcher in TypeScript.
