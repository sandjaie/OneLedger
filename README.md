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

```text
Every financial event
  → normalized into one ledger
  → categorized
  → connected to accounts / assets / liabilities
  → reflected in cash flow, budget and net worth
```

---

## The screen this is being built for

```bash
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

## Documentation

- [Specification index](docs/index.html) — product, design and operational documentation.
- [Product roadmap](docs/product/roadmap.html) — approved outcomes and acceptance criteria.
- [Master implementation plan](docs/product/master-implementation-plan.html) — the canonical
  Phase N.M order and dependencies.
- [Implementation progress](docs/superpowers/progress.md) — the single maintained execution
  tracker and next action.
- [Project register](docs/superpowers/project-register.md) — deferred work, discoveries,
  decisions, and risks awaiting review.
- [Finance OS requirements](docs/product/finance-os.html) — verified positions, cash needs,
  resilience, goals, protection, actions and owner export.

**Statements are never committed.** `pdf-statements/`, `*.pdf` and `.env` are git-ignored. Real
financial data does not enter this repository, and test fixtures are redacted — see
[security-and-privacy.html](docs/operations/security-and-privacy.html) §2.

---

## The shape of it

```text
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
                   explicit docker compose projects
                   development and clean local production today
```

Run the API locally without raw access logging so an OIDC callback code is not copied from the
request target into terminal output or captured logs:

```bash
cd api
uv run uvicorn oneledger.main:app --reload --port 8000 --no-access-log
```

### Run the development stack

Use the development launcher for the working ledger:

```bash
scripts/dev-stack.sh up
scripts/dev-stack.sh status
scripts/dev-stack.sh logs web
scripts/dev-stack.sh down
```

`up` rebuilds the application, waits for healthchecked services to become healthy and other
services to start, then applies pending migrations to the existing development database. It fails
closed instead of creating a new ledger when either persistent volume is missing. `down` stops the
containers without deleting `oneledger_pgdata` or `oneledger_documents`. Run `logs` without a
service name to show the latest output from every container.

### Run the clean local-production stack

The existing `docker-compose.yml` remains the development and acceptance environment, including
its working ledger. A separate production-built stack uses its own Compose project, database and
documents volume:

```bash
scripts/production-stack.sh init
scripts/production-stack.sh up
scripts/production-stack.sh status
```

Open <http://localhost:3100>. Its API is on <http://localhost:8100> and its database is exposed
only on loopback port `55432`. The first `init` writes a private, mode-600 `.env.production` with
generated secrets, runs every migration and creates only the configured owner; no mock, imported or
financial row is seeded. Local login still creates the normal persisted, revocable owner session.

`scripts/production-stack.sh down` stops only this project and preserves
`oneledger-production_pgdata` and `oneledger-production_documents`. Starting it again resumes the
same clean ledger. Erasure is deliberately separate and exact:

```bash
scripts/production-stack.sh reset --confirm oneledger-production
```

That command removes only those two production volumes. It is not part of normal operation. The
future public deployment will explicitly select Google authentication and add its public-origin/TLS
configuration; this local stack is not exposed beyond loopback. See
[ADR-0018](docs/adr/0018-local-owner-sessions.html) and
[ADR-0019](docs/adr/0019-separate-local-production-stack.html).

Three jobs, cleanly split: **React renders, Python computes, Postgres guarantees.** The invariants
live in the database rather than the service, because a backend is a convenient place to put logic
and a poor place to put guarantees.

Statements are parsed by software running on a machine you control, and reach no third party.
Rationale in [ADR-0013](docs/adr/0013-python-backend-service.html) and
[ADR-0014](docs/adr/0014-one-vps-one-compose-file.html).

### Backups and the restore drill

The compose `backup` service runs `scripts/backup-db.sh --if-due` hourly, without anyone
remembering. One run writes three encrypted files sharing one UTC stamp: the database dump, an
archive of the documents volume, and a structural manifest taken from the same database snapshot
the dump reads.

Run it by hand **inside the `backup` service**, not directly on the host — that container already
has `BACKUP_PASSPHRASE`, `DATABASE_URL` and, critically, `DOCUMENTS_DIR` pointed at the real
`documents` Docker volume (`/oneledger/documents`, mounted read-only). Set `DOCUMENTS_DIR` to a
plain host folder instead and the archive backs up whatever happens to be there — not the
originals the app actually stores, and not the same files `GET /doctor` and the restore drill
believe they are proving:

```bash
docker compose run --rm --entrypoint bash backup scripts/backup-db.sh
```

It always runs this way — `--if-due` or not.

Prove a backup restores, rather than trusting that it would, with `scripts/restore-drill.sh`. It
restores the newest complete stamp into the isolated test server (`scripts/test-db.sh`) — a
temporary database and document directory, never the working ledger — and compares a recomputed
manifest against the one recorded at backup time item by item, always writing
`<prefix>-<stamp>.drill.json`. This one runs on the host, since it talks to `db-test` directly:

```bash
scripts/restore-drill.sh
```

Settings › Data Quality shows the newest backup's age and file completeness and the newest
drill's result and age. See [security-and-privacy.html §9](docs/operations/security-and-privacy.html#9-backups)
for the retention schedule and cadence.

---

## Reading and publishing it

Start here → **[docs/index.html](docs/index.html)** (index and suggested reading order)

Published at **<https://sandjaie.github.io/OneLedger/>** — `docs/` is deployed by
[`.github/workflows/docs.yml`](.github/workflows/docs.yml) on every push to the default branch.
[`scripts/check-docs.py`](scripts/check-docs.py) — no broken links, no malformed HTML, no
statement tracked by git — runs in the pre-commit hook rather than in CI, over the whole tree
each time. A commit cannot introduce a broken link, so nothing is left for a workflow to find.

Documents you read are HTML; plans that get executed are Markdown.

| Doc | What it settles |
|---|---|
| [PRD](docs/product/prd.html) | What the product does, module by module, with scope boundaries |
| [Finance OS requirements](docs/product/finance-os.html) | Product coverage, financial definitions, planning capabilities and acceptance scenarios |
| [Architecture](docs/design/architecture.html) | The three services, the compose stack, and why |
| [Data Model](docs/design/data-model.html) | Entities, sign conventions, invariants, RLS |
| [Multi-Currency & FX](docs/design/multi-currency-and-fx.html) | The two-rate model; pegs, gaps, and what an FX gain actually is |
| [Statement Ingestion](docs/design/statement-ingestion.html) | Upload → parse → normalize → dedupe → reconcile → review → import |
| [Classification](docs/design/classification.html) | Deterministic rule engine; the category taxonomy |
| [Transfers & Reconciliation](docs/design/transfers-and-reconciliation.html) | Why transfers are first-class, and how they're matched |
| [Roadmap](docs/product/roadmap.html) | Approved product outcomes and their success criteria |
| [Master implementation plan](docs/product/master-implementation-plan.html) | Canonical Phase N.M order, dependencies, and delivery boundaries |
| [Implementation progress](docs/superpowers/progress.md) | Live status, current outcome, next action, gates, and evidence |
| [Project register](docs/superpowers/project-register.md) | Deferred or unapproved information and its review condition |
| [Security & Privacy](docs/operations/security-and-privacy.html) | Threat model, the never-store list, the authorization boundary |
| [API Contracts](docs/design/api-contracts.html) | The route surface, typed by Pydantic through OpenAPI |
| [UI & Information Architecture](docs/product/ui-information-architecture.html) | Screens, navigation, key interactions |
| [Testing & Data Quality](docs/operations/testing-and-data-quality.html) | How correctness is proven, not assumed |
| [Glossary](docs/reference/glossary.html) | Domain vocabulary used consistently everywhere |
| [ADRs](docs/adr/index.html) | 19 decisions in force, each with the alternatives actually considered |
| [Decisions we changed](docs/adr/superseded.html) | The four replaced during specification, and why |
| [Questions](docs/superpowers/questions.html) | Scoping questions — **17 of 24 answered** |
| [Phase 0 Pull Requests](docs/superpowers/phase-0-pull-requests.md) | Historical PR grouping and review workflow |
| [Schema Reference](docs/superpowers/schema.md) | The complete DDL, by migration |
| [Test Suite Reference](docs/superpowers/test-suite.md) | The invariant suite and static checks |

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
| All intelligent features require the Phase 5 privacy and safety gate | [0003](docs/adr/0003-defer-ai-to-v2.html) |

**The app is institution- and region-agnostic by construction.** Accounts, institutions,
categories, merchants, rules, securities, currency pegs and statement layouts are all rows.
Opening a bank account next year, or importing a statement from one closed years ago, needs no code
change and no deploy. Bank and currency names appear in these documents as *examples*; they never
appear as column names, code identifiers, enum values or required seed rows.

---

## What you choose at setup

The permitted Google address is chosen in `.env`. The household name, the owner's display
name and tax residency, and the reporting pair are chosen in the app. Two more that look like
decisions answer themselves. Everything else is editable in the app later.

If the configured permitted address was corrected after this database's one-time owner seed, run
`scripts/seed-household.sh --repair-owner-email`. The explicit repair changes only the sole
existing owner's email; the ordinary command still refuses a second run.

| Choice | Notes |
|---|---|
| **Reporting pair** — chosen in **Settings › Household** before the first import | The one that is expensive to change afterwards: both amounts are frozen per row, so switching the *pair* later is a backfill. Denominate the base where the money will eventually be spent ([§4](docs/design/multi-currency-and-fx.html)) |
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

The boundaries are set in the [roadmap](docs/product/roadmap.html), and their implementation order
is fixed in the [master implementation plan](docs/product/master-implementation-plan.html). New
ideas enter the [project register](docs/superpowers/project-register.md) and are argued with before
they become approved scope, not during implementation.

**Intelligence begins only at Phase 5.1.** Earlier phases contain no model calls. Classification is
deterministic and inspectable; it must be good on its own before an optional fallback is considered
([ADR-0003](docs/adr/0003-defer-ai-to-v2.html)).

**Usefulness arrives in canonical order.** Phase 1.x completes the daily-use financial core,
Phase 2.x establishes historical depth and data quality, Phase 3.x adds planning and resilience,
and Phase 4.x adds deeper analysis. The approved history anchor remains unchanged. Current status
and the next action live only in [implementation progress](docs/superpowers/progress.md).
