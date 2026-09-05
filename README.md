# OneLedger

A self-hosted personal finance ledger for money that lives in **more than one country**.

Import statements from banks with no API, keep one normalized ledger across currencies, and get net
worth and cash flow that are correct — because remittances aren't spending, and last March's numbers
don't change.

One ledger. Not a module per country — a single normalized transaction ledger where `country`,
`currency`, `institution`, `account_type` and `tax_residency` are *attributes*, not separate
systems.

The first deployment is a UAE + India household, and the worked examples in these documents come
from it. **Nothing in the design does.** The reporting currency pair, the countries, the
institutions, the categories and the statement layouts are all configuration or rows — see
[what you choose at setup](#what-you-choose-at-setup).

```
Every financial event
  → normalized into one ledger
  → categorized
  → connected to accounts / assets / liabilities
  → reflected in cash flow, budget and net worth
```

---

## The screen this is being built for

```
Net Worth        base / secondary  X
Liquid Net Worth                   X
Investments                        X
Debt                               X
This Month Income                  X
This Month Spending                X
Savings Rate                       X %
Investment Rate                    X %
12-month Cash Flow                 ▁▃▅▂▇▄▆▃▅▇▄▆
```

---

## Status

**Pre-implementation.** This repository contains the specification set and the build plan. No
application code has been written yet.

- **[docs/plan/phase-0-foundations.md](docs/plan/phase-0-foundations.md)** — the next thing to
  execute. 11 tasks, 73 steps, each with real code and a command whose output you can check.
- [docs/plan/implementation-plan.md](docs/plan/implementation-plan.md) — the master plan: global
  constraints, build order, and what each milestone delivers.
- [docs/product/roadmap.html](docs/product/roadmap.html) — the milestones it sequences.

**Statements are never committed.** `pdf-statements/`, `*.pdf` and `.env` are git-ignored. Real
financial data does not enter this repository, and test fixtures are redacted — see
[security-and-privacy.html](docs/operations/security-and-privacy.html) §2.

---

## The shape of it

```
                 ┌─────────────────────────────────────┐
                 │  caddy      TLS                     │   (hosted only)
                 ├─────────────────────────────────────┤
                 │  web        Next.js · React         │   renders. no business logic
                 ├─────────────────────────────────────┤
                 │  api        FastAPI · Python 3.14   │   parse · classify · match
                 │             pdfplumber              │   XIRR · the auth boundary
                 ├─────────────────────────────────────┤
                 │  db         Postgres 18             │   constraints · triggers · views
                 └─────────────────────────────────────┘
                   one docker compose file
                   your laptop now, one small VPS later
```

Three jobs, cleanly split: **React renders, Python computes, Postgres guarantees.** The invariants
live in the database rather than the service, because a backend is a convenient place to put logic
and a poor place to put guarantees.

Statements are parsed by software running on a machine you control, and reach no third party.
Rationale in [ADR-0013](docs/adr/0013-python-backend-service.html) and
[ADR-0014](docs/adr/0014-one-vps-one-compose-file.html).

---

## Documentation

Start here → **[docs/index.html](docs/index.html)** (index and suggested reading order)

Published at **https://sandjaie.github.io/OneLedger/** — `docs/` is deployed by
[`.github/workflows/docs.yml`](.github/workflows/docs.yml) on every push to the default branch.
Every push on any branch runs [`scripts/check-docs.py`](scripts/check-docs.py) first: no broken
links, no malformed HTML, no statement tracked by git.

Documents you read are HTML; plans that get executed are Markdown.

| Doc | What it settles |
|---|---|
| [PRD](docs/product/prd.html) | What the product does, module by module, with scope boundaries |
| [Architecture](docs/design/architecture.html) | The three services, the compose stack, and why |
| [Data Model](docs/design/data-model.html) | Entities, sign conventions, invariants, RLS |
| [Multi-Currency & FX](docs/design/multi-currency-and-fx.html) | The two-rate model; pegs, gaps, and what an FX gain actually is |
| [Statement Ingestion](docs/design/statement-ingestion.html) | Upload → parse → normalize → dedupe → reconcile → review → import |
| [Classification](docs/design/classification.html) | Deterministic rule engine; the category taxonomy |
| [Transfers & Reconciliation](docs/design/transfers-and-reconciliation.html) | Why transfers are first-class, and how they're matched |
| [Roadmap](docs/product/roadmap.html) | Phase 0 → Phase 1 → Phase 2 → V2 |
| [Security & Privacy](docs/operations/security-and-privacy.html) | Threat model, the never-store list, the authorization boundary |
| [API Contracts](docs/design/api-contracts.html) | The route surface, typed by Pydantic through OpenAPI |
| [UI & Information Architecture](docs/product/ui-information-architecture.html) | Screens, navigation, key interactions |
| [Testing & Data Quality](docs/operations/testing-and-data-quality.html) | How correctness is proven, not assumed |
| [Glossary](docs/reference/glossary.html) | Domain vocabulary used consistently everywhere |
| [ADRs](docs/adr/index.html) | 14 decisions in force, each with the alternatives actually considered |
| [Decisions we changed](docs/adr/superseded.html) | The four replaced during specification, and why |
| [Questions](docs/plan/questions.html) | Scoping questions — **17 of 24 answered** |
| **[Implementation Plan](docs/plan/implementation-plan.md)** | **The build order. Start here to write code** |
| [Phase 0 Pull Requests](docs/plan/phase-0-pull-requests.md) | How the 11 tasks become seven reviewable PRs, and what gates each |
| [Schema Reference](docs/plan/schema.md) | The complete DDL, by migration |
| [Test Suite Reference](docs/plan/test-suite.md) | The invariant suite and static checks |

---

## Non-negotiable invariants

Each is enforced in the database and asserted by a test — not merely described. See
[testing-and-data-quality.html](docs/operations/testing-and-data-quality.html) §1.

A financial tool rarely crashes. It produces a number that is quietly wrong, and you believe it.
These exist to make that impossible.

1. **The original never changes.** `amount` and `currency` are written once at import and are
   never overwritten, recomputed or "corrected" by a later FX rate.
2. **Net worth is one SUM.** Every balance is signed from the household's perspective, so
   `net_worth = SUM(balance_in_base)` across all accounts. Liabilities are negative.
3. **Transfers are never income or expense.** A transaction with a `transfer_id` is excluded from
   every cash-flow, budget and spending figure — enforced in a view, so no future query can forget.
4. **A credit card payment is a transfer.** The expense happened at purchase. Counting the payment
   too would double-count spending.
5. **Historical flows are frozen.** Last March's spending is the same number today as it was last
   March. Reports never re-translate history at today's rate.
6. **Every imported row traces back to a document.** Statement → page → row → transaction.
7. **Nothing is silently invented.** Carried-forward FX rates, interpolated valuations and
   unexplained net-worth deltas are labelled as such in the UI.
8. **A statement that doesn't balance doesn't import.** If `opening + Σ rows ≠ declared closing`,
   the batch is blocked, not warned about. This one check catches most parser bugs, and wrong
   financial data is worse than absent financial data — absent data announces itself.

---

## Decisions worth knowing before reading further

The full set is in [docs/adr/](docs/adr/index.html), each with the alternatives genuinely
considered. The ones that shape the most code:

| Decision | ADR |
|---|---|
| Both reporting currencies frozen per transaction; flows at booked-date rate, balances at as-of date | [0002](docs/adr/0002-frozen-dual-currency-amounts.html) |
| Investments tracked at lot level — XIRR cannot be backfilled later | [0005](docs/adr/0005-transaction-level-investment-lots.html) |
| Signed balances, so net worth is one aggregate with no `CASE` | [0010](docs/adr/0010-signed-balance-convention.html) |
| 36 months of history, anchored 01 Jan 2024 | [0011](docs/adr/0011-36-month-history-horizon.html) |
| **Statement layouts are data, not code** — no bank name appears in the codebase | [0012](docs/adr/0012-statement-layouts-as-data.html) |
| **A Python backend service** owns all business logic | [0013](docs/adr/0013-python-backend-service.html) |
| **One VPS, one compose file** — no managed services | [0014](docs/adr/0014-one-vps-one-compose-file.html) |
| All AI deferred to V2, with four stated prerequisites | [0003](docs/adr/0003-defer-ai-to-v2.html) |

**The app is institution- and region-agnostic by construction.** Accounts, institutions,
categories, merchants, rules, securities, currency pegs and statement layouts are all rows.
Opening a bank account next year, or importing a statement from one closed years ago, needs no code
change and no deploy. Bank and currency names appear in these documents as *examples*; they never
appear as column names, code identifiers, enum values or required seed rows.

---

## What you choose at setup

Three decisions, made in `.env` and applied by the Phase 0 seed. Two more that look like
decisions answer themselves. Everything else is editable in the app later.

| Choice | Notes |
|---|---|
| **Reporting pair** — `BASE_CURRENCY`, `SECONDARY_CURRENCY` | The one that is expensive to change afterwards: both amounts are frozen per row, so switching the *pair* later is a backfill. Denominate the base where the money will eventually be spent ([§4](docs/design/multi-currency-and-fx.html)) |
| **The Google account allowed to sign in** | One address. Every other is rejected even if Google authenticates it ([ADR-0004](docs/adr/0004-google-auth-single-user.html)) |
| **History anchor date** | 36 months back by default ([ADR-0011](docs/adr/0011-36-month-history-horizon.html)) |
| **Countries you bank in** | Not asked — derived from your accounts, and it selects the merchant rule packs ([classification §3](docs/design/classification.html)) |
| **Statement layouts** | Not asked — mapped in the UI on first import of an unfamiliar statement ([ADR-0012](docs/adr/0012-statement-layouts-as-data.html)) |

A currency pegged to another (AED to USD, DKK to EUR) is a row in `currency`, so the rate pipeline
fetches one side and derives the other. A deployment with no peg simply fetches both.

---

## Cost

Zero while local. Roughly **$5–12 a month** once hosted — one small VPS running the whole stack.
No per-seat, per-service or usage-based pricing: the bill does not grow with the ledger.

---

## Scope discipline

Built in Phase 1 · deferred to Phase 2 · deferred to V2 — the boundaries are set in
[roadmap.html](docs/product/roadmap.html) and are meant to be argued with *before* coding starts,
not during.

**AI is entirely a V2 concern.** Phase 1 and Phase 2 contain no LLM calls. The classification
engine is deterministic and inspectable; it has to be good on its own before anything smarter is
layered on top ([ADR-0003](docs/adr/0003-defer-ai-to-v2.html)).

**Two milestones are worth knowing about in advance.** The tool becomes genuinely useful at **M2**,
not M10 — a personal project that only pays off at the end usually doesn't reach the end. And the
numbers are not trustworthy until **M5**, because before transfers are linked every spending figure
is inflated by the full value of every remittance and card payment.
