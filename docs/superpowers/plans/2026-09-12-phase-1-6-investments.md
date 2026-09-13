# Phase 1.6 investments implementation plan

> Execute sequentially in the active checkout with `superpowers:executing-plans` and
> `superpowers:test-driven-development`. Track completion only in `../progress.md`.

**Goal:** Factual investment history, valuation, funding, allocation and XIRR without advice.

**Architecture:** A household investment ledger keeps immutable events/lots and projected holdings.
Atomic imports, prices and one Decimal calculator sit beside the reportable cash ledger.

**Stack:** Existing PostgreSQL/Flyway, Python/FastAPI and Next.js/React; no new dependency. See the
[Phase 1.6 specification](../specs/2026-09-12-phase-1-6-investments.md).

## Global execution contract

- Workspace: `/Users/sandjaieravi/dev/OneLedger`. Active checkout, one writer, no worktrees; every
  branch and PR uses `develop`. Merge/fast-forward each dependency before creating the next branch.
- TDD means focused RED, smallest implementation, then GREEN; never weaken or infer evidence.
- Scales: money `NUMERIC(20,4)`, units `NUMERIC(28,8)`, price `NUMERIC(20,6)`, FX and allocation
  `NUMERIC(20,10)`; XIRR is `NUMERIC(20,8)`. Python uses `Decimal`, JSON strings, TS display.
- Financial routes use `require_owner`; queries, RLS and FKs enforce household scope.
- Complete identifiers stay in typed owner-local stores. Ordinary responses mask; reveal is explicit
  and `no-store`; full values never reach logs, errors, URLs, browser storage or artifacts.
- Cash roles map to four excluded investment ledger types. Links fully allocate a same-currency
  leaf; a cross-currency chain links at its final same-currency debit.
- Unknown unit action/mismatch/ambiguity/conflict blocks its group. Lot gaps create non-blocking
  reviews and withhold basis, not XIRR; funding gaps create no review; summary mismatch creates no
  event. Corporate actions are holding-level unit effects that withhold basis and realised claims.
- Keep contributions, open-lot basis and cost checkpoints distinct. Statement NAV is not terminal
  value without a separate valuation checkpoint.
- Update owning docs and only `progress.md` for status. Size branches to the diff budget in
  `docs/git-workflow.md`.

## Shared interfaces and algorithms

- `SecurityResolver` accepts an exact ID or owner choice; ambiguity reviews. Adapters output pure
  drafts/checkpoints/prices. The calculator alone turns committed facts into strings/reasons.
- V33 makes allocations the sole cash link: reject non-null legacy links, recreate the split guard,
  drop the old column, store/restore prior type, and reject mixed or partial roles.
- `official_nav_text_v1` reads configured `https://portal.amfiindia.com/spages/NAVAll.txt`, latest
  only; observations accumulate, GET stays read-only, and owner POST refreshes after 24 hours.
- Price identity is security/date/source/version; conflicts review, mapping selects, manual values
  require dated owner choice, and outage retains stale cache.
- XIRR uses the spec's 365-day equation, Decimal-50 log scan over `(-0.999999999999,1000000]`,
  refinement to `1e-12`/`1e-24`, eight-decimal percent and explicit multi/no-root reasons.

## Verification and delivery

Shared paths: schema = `docs/superpowers/schema.md` and `docs/design/data-model.html`; API/type =
`docs/design/api-contracts.html` and `web/types/api/types.gen.ts`; ingestion, privacy and testing =
`docs/design/statement-ingestion.html`, `docs/operations/security-and-privacy.html` and
`docs/operations/testing-and-data-quality.html`; status = `docs/superpowers/progress.md`.
Shared commands: docs = `python3 scripts/check-docs.py`; Python =
`cd api && uv run ruff check . && uv run mypy .`; types =
`cd web && npm run api:types && npm run api:types:check`. Live DB setup is
`eval "$(scripts/test-db.sh up)"`; teardown is only `scripts/test-db.sh down`.
After focused commands: one review, one repair, `scripts/verify.sh` once, first push, PR,
merge/delete.

## Proposed task and branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | instruments and dated values | `feature/p1-6a-investment-ledger` | 540 | 760 | — |
| 2 | events, holdings and lots | `feature/p1-6b-investment-events` | 700 | 820 | 1 |
| 3 | groups, evidence and reviews | `feature/p1-6c-investment-evidence` | 500 | 650 | 2 |
| 4 | allocations and legacy-link removal | `feature/p1-6d-investment-allocations` | 420 | 600 | 2–3 |
| 5 | catalogue and manual-price API | `feature/p1-6e-investment-catalogue-api` | 500 | 700 | 1, 3 |
| 6 | acquisition entry and corrections | `feature/p1-6f-investment-entry-api` | 460 | 700 | 2, 5 |
| 7 | purchase funding confirmation | `feature/p1-6g-investment-funding` | 420 | 650 | 4, 6 |
| 8 | acquisition positions and net worth | `feature/p1-6h-investment-position-api` | 560 | 820 | 5–7 |
| 9 | owner-entry web flow | `feature/p1-6i-investment-entry-web` | 560 | 500 | 8 |
| 10 | funding web flow | `feature/p1-6j-investment-funding-web` | 360 | 360 | 7, 9 |
| 11.1 | core detailed extraction | `feature/p1-6k1-investment-extraction-core` | 560 | 700 | 3, 5 |
| 11.2 | income, checkpoints and hostile evidence | `feature/p1-6k2-investment-extraction-coverage` | 240 | 500 | 11.1 |
| 11.3 | grouped unit actions and draft ratios | `feature/p1-6k3-investment-extraction-actions` | 350 | 700 | 11.2 |
| 12.1 | preparation and safe preview | `feature/p1-6l1-investment-staging-preview` | 270 | 430 | 11.3 |
| 12.2 | idempotent atomic persistence | `feature/p1-6l2-investment-staging-persistence` | 460 | 690 | 4, 12.1 |
| 12.3 | owner API and cancellation | `feature/p1-6l3-investment-staging-api` | 340 | 560 | 12.2 |
| 13 | summary checkpoints | `feature/p1-6m-investment-checkpoints` | 400 | 580 | 11.3, 12.3 |
| 14.1 | cash-lifecycle database operation | `feature/p1-6n1-investment-cash-event-operation` | 300 | 420 | 4, 6 |
| 14.2 | disposal and income owner API | `feature/p1-6n2-investment-cash-events` | 560 | 800 | 14.1 |
| 14.3 | evidenced lot and cash allocation | `feature/p1-6n3-investment-cash-allocations` | 650 | 1150 | 7, 14.2 |
| 15.1 | lot lineage read model and guards | `feature/p1-6o1-investment-lot-lineage` | 500 | 780 | 4, 14.3 |
| 15.2 | holding-level unit actions | `feature/p1-6o2-investment-unit-actions` | 1,100 | 1,300 | 15.1 |
| 16 | import review, security catalogue and commit | `feature/p1-6p-investment-import-commit` | 800 | 1,100 | 13, 14.2, 15.2 |
| 17 | investment import web flow | `feature/p1-6q-investment-import-web` | 900 | 700 | 16 |
| 18 | official prices and returns engine | `feature/p1-6r-investment-prices-returns` | 950 | 1,300 | 8, 15.2 |
| 19 | portfolio, holding and data-quality API | `feature/p1-6s-investment-portfolio-api` | 800 | 1,000 | 16, 18 |
| 20 | portfolio web and acceptance | `feature/p1-6t-investment-portfolio-web` | 1,000 | 700 | 17, 19 |

On 14 September 2026 the owner re-scoped the undispatched remainder to personal scale. Corporate
actions become holding-level unit effects; per-lot lineage, lot-level realised gain after a
corporate action, and account or asset-class returns move to the project register. Old Tasks
15.2–15.5 become 15.2; 16 stays 16; 17–18 become 17; 19–20 become 18; 21–22 become 19; 23–24
become 20. Task 15.1 and V40 remain the foundation; Tasks 1–15.1 are unchanged history.

## Task 1 — instruments and dated values

**Branch/base/target:** `feature/p1-6a-investment-ledger` / `develop` / `develop`.
**Files:** `db/migrations/V30__investment_instruments_and_valuations.sql`,
`db/tests/0027_investment_instruments_and_valuations.sql`; shared schema/privacy/status paths.
**Contract/tests:** Security/source mapping, valuation mode, price observation, snapshot target,
RLS/shared-row/same-household/scale constraints and `folio`/`brokerage_account` kinds; forward V29
upgrade, invalid/cross-household/double-mode RED cases; no seeded owner/retirement data.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && scripts/hooks/db-tests.sh`; shared docs command.
Acceptance: exact dated value works, identity masks, and modes cannot double count.

## Task 2 — immutable events, holdings and acquisition lots

**Branch/base/target:** `feature/p1-6b-investment-events` / `develop` / `develop`.
**Files:** `db/migrations/V31__investment_events_and_lots.sql`,
`db/tests/0028_investment_events_and_lots.sql`; shared schema/status paths.
**Contract/tests:** Event-kind field matrix, immutable imported facts, append-only correction,
rebuildable units, origin lots, closed history, dedupe and household FKs; direction, precision,
mutation, cross-household, rollback and unsupported-adjustment RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && scripts/hooks/db-tests.sh`; shared docs command.
Acceptance: acquisition/redemption reconstruct units/lots and forbidden edits fail atomically.

## Task 3 — import groups, evidence, checkpoints and reviews

**Branch/base/target:** `feature/p1-6c-investment-evidence` / `develop` / `develop`.
**Files:** `db/migrations/V32__investment_evidence_and_reviews.sql`,
`db/tests/0029_investment_evidence_and_reviews.sql`; shared schema/ingestion/status paths.
**Contract/tests:** Import parent, atomic groups, drafts, checkpoints, evidence fingerprints and
review states; RLS, filename-free dedupe, summary-no-event, conflict, block matrix, unresolved-close
and group rollback RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && scripts/hooks/db-tests.sh`; shared docs command.
Acceptance: repeated evidence attaches once and conflict/block affects only its group.

## Task 4 — allocations and legacy-link removal

**Branch/base/target:** `feature/p1-6d-investment-allocations` / `develop` / `develop`.
**Files:** `db/migrations/V33__investment_allocations.sql`,
`db/tests/0030_investment_allocations.sql`; shared schema/testing/status paths.
**Contract/tests:** Cash/lot allocations, roles, full leaf allocation, same native currency, prior
type restore, `investment_fee`, reportable-view exclusion, split-guard recreation and fail-before-
drop legacy column; over-allocation, mixed/partial, upgrade and unlink RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && scripts/hooks/db-tests.sh`; shared docs command.
Acceptance: many-to-many allocations are the sole link and ordinary flow totals stay unchanged.

## Task 5 — catalogue, private identity and manual-price API

**Branch/base/target:** `feature/p1-6e-investment-catalogue-api` / `develop` / `develop`.
**Files:** `api/oneledger/identifiers.py`, `api/oneledger/routes/investment_catalogue.py`,
`api/oneledger/main.py`, `api/tests/test_investment_catalogue.py`,
`api/tests/test_investment_catalogue_live.py`; shared API/type/privacy/status paths.
**Contract/tests:** Exact/ambiguous security resolution; specified 5–64-character identifier
normalization/masking/reveal; append manual price. Auth/RLS, Decimal strings, Unicode/raw serialized
leakage, shared-row mutation and price conflict RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_catalogue.py tests/test_investment_catalogue_live.py)`;
shared types command. Acceptance: synthetic instrument/account/price exposes only a mask.

## Task 6 — acquisition entry and correction framework

**Branch/base/target:** `feature/p1-6f-investment-entry-api` / `develop` / `develop`.
**Files:** `api/oneledger/investments/commands.py`, `api/oneledger/investments/schemas.py`,
`api/oneledger/routes/investment_events.py`, `api/oneledger/main.py`,
`api/tests/test_investment_events.py`, `api/tests/test_investment_events_live.py`; shared
API/type/status paths.
**Contract/tests:** Acquisition entry plus idempotent append correction/reversal; one transaction
creates event/effect/lot. Field matrix, stale prior state, exact strings, cross-household, duplicate
retry and rollback RED cases. Sales/distributions/charges wait for task 14.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_events.py tests/test_investment_events_live.py)`; shared types. Acceptance:
purchase/correction preserve originals and reconcile acquisition units/basis.

## Task 7 — purchase funding confirmation

**Branch/base/target:** `feature/p1-6g-investment-funding` / `develop` / `develop`.
**Files:** `api/oneledger/investments/funding.py`, `api/oneledger/routes/investment_funding.py`,
`api/oneledger/main.py`, `api/tests/test_investment_funding.py`,
`api/tests/test_investment_funding_live.py`; shared API/type/testing/status paths.
**Contract/tests:** Proposal never confirms; full same-currency leaf allocation; final debit of
transfer chain; exact prior-type unlink. Over/mixed/partial, concurrent, split rows, one-to-many,
many-to-one, cross-household/currency and unchanged flow RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_funding.py tests/test_investment_funding_live.py
tests/test_dashboard_flows_live.py tests/test_transfers_live.py)`; shared types.
Acceptance: one path creates no spending, income or wealth duplication.

## Task 8 — acquisition-only positions and one-count net worth

**Branch/base/target:** `feature/p1-6h-investment-position-api` / `develop` / `develop`.
**Files:** `api/oneledger/investments/positions.py`, `api/oneledger/routes/portfolio.py`,
`api/oneledger/routes/dashboard.py`, `api/oneledger/main.py`, `api/tests/test_positions.py`,
`api/tests/test_portfolio_live.py`, `api/tests/test_dashboard_live.py`; shared API/type/schema/status.
**Contract/tests:** Acquisition units, cumulative contributions/current value/gain, price at/before
as-of and valuation-mode replacement. Missing/stale/future price, FX, snapshot carry, transition and
both-currency double-count RED cases; no disposal-basis claim yet.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_positions.py tests/test_portfolio_live.py tests/test_dashboard_live.py)`. Acceptance:
the smallest API vertical values an acquisition and net worth exactly once.

## Task 9 — catalogue, purchase, price and holding-entry web flow

**Branch/base/target:** `feature/p1-6i-investment-entry-web` / `develop` / `develop`.
**Files:** `web/app/investments/page.tsx`, `web/app/investments/entry-screen.tsx`,
`web/app/investments/__tests__/entry-screen.test.tsx`, `web/app/workspace.tsx`,
`web/app/globals.css`; shared status path.
**Contract/tests:** Server strings only; masked identity, explicit confirmation, auth/loading/empty/
error/missing states, labels/keyboard and no advisory copy/client arithmetic RED cases.
**RED/GREEN:** `cd web && npx vitest run app/investments/__tests__/entry-screen.test.tsx`.
Acceptance: entry creates one visible holding.

## Task 10 — funding confirmation web flow

**Branch/base/target:** `feature/p1-6j-investment-funding-web` / `develop` / `develop`.
**Files:** `web/app/investments/funding-panel.tsx`,
`web/app/investments/__tests__/funding-panel.test.tsx`, `web/app/investments/entry-screen.tsx`,
shared status path.
**Contract/tests:** Candidate/proposal/confirmation/unlink states, full-allocation requirement,
transfer-chain explanation; stale, partial, many-to-many and accessibility RED cases.
**RED/GREEN:** `cd web && npx vitest run app/investments/__tests__/funding-panel.test.tsx`.
Acceptance: confirmation/removal never
auto-matches or computes money in the UI.

## Task 11.1 — adapter contract and core detailed extraction

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6k1-investment-extraction-core` / `develop` / `develop`.
**Depends:** Tasks 3 and 5. **Forecast:** 450–560 production, 560–700 tests.
**Files:** create `api/oneledger/investments/adapters.py`,
`api/oneledger/investments/detailed_fund_statement.py`, `api/tests/fixtures/investment_pdf.py` and
`api/tests/test_investment_extraction.py`; shared ingestion/privacy/status paths only as needed.
**Contract/tests:** A pure immutable adapter consumes the existing `api.oneledger.ingest.words.Page`
boundary and is selected only from document content. Filename, path, institution identity and
provider popularity are never evidence. Results contain ordered groups of drafts, detailed-statement
checkpoints and fixed safe reasons, with source-contract and extraction versions. Evidence carries
page/row, optional typed private source-event identity and a versioned fingerprint over normalized
typed facts—never filename or raw text. Draft facts mirror V32 plus nullable `unit_ratio`: event date,
kind, signed units, ratio, price, gross, charges, tax, net and currency, using `Decimal` and exact
typed values. Account/security source evidence stays transient for Task 12; extraction invents no
UUID and resolves no ambiguity. Result representations and errors expose no complete identifier,
unredacted reference or raw statement row. Synthetic tests reuse `api/tests/fixtures/synthetic_pdf.py`
and cover content selection, purchase, recurring purchase, sale, redemption, maturity, page breaks,
exact date/direction/precision and safe unsupported-format abstention. This capability-neutral
investment semantic adapter does not reintroduce per-bank modules under ADR-0012 and uses no AI or
network access.
**RED/GREEN:** `cd api && uv run pytest -q tests/test_investment_extraction.py`.
Acceptance: supported synthetic content produces exact core drafts; unsupported content abstains,
and no checkpoint capability beyond the frozen result shape is claimed yet.

## Task 11.2 — income, checkpoints and hostile evidence

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6k2-investment-extraction-coverage` / `develop` / `develop`.
**Depends:** Task 11.1. **Forecast:** 170–240 production, 360–500 tests.
**Files:** extend `api/oneledger/investments/detailed_fund_statement.py` and
`api/tests/fixtures/investment_pdf.py`; create `api/tests/test_investment_extraction_coverage.py`;
update shared ingestion/privacy/status paths only as needed. The 11.1 adapter interface is frozen.
**Contract/tests:** Add cash distribution, interest, reinvested distribution, charge and withheld-tax
drafts plus detailed-statement end-position units/cost/value checkpoints. A checkpoint remains
reconciliation evidence and statement NAV never becomes a terminal market price. Cover repeated
headers and section rows across page breaks, maximum supported precision and atomic excess-precision
refusal. Hostile ASCII, formatted and Unicode identity evidence stays absent from representations,
safe reasons and fingerprints. Unknown unit-affecting rows emit fixed `unrecognized_event` blockers
only for their affected group without guessing or suppressing unrelated groups.
**RED/GREEN:** `cd api && uv run pytest -q tests/test_investment_extraction.py
tests/test_investment_extraction_coverage.py`.
Acceptance: all single-holding detailed facts and checkpoints extract exactly; ambiguous and unknown
facts produce only fixed safe reasons.

## Task 11.3 — grouped unit actions and staged split ratios

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6k3-investment-extraction-actions` / `develop` / `develop`.
**Depends:** Task 11.2. **Forecast:** 260–350 production, 490–700 tests.
**Files:** create `api/oneledger/investments/detailed_fund_actions.py`,
`api/tests/test_investment_extraction_actions.py`,
`db/migrations/V35__investment_draft_unit_ratio.sql` and
`db/tests/0032_investment_draft_unit_ratio.sql`; extend the detailed adapter and synthetic fixture
only at their frozen action boundaries; update shared schema/ingestion/privacy/status paths.
**Contract/tests:** Extract split, bonus, rights, switch, systematic-transfer and platform-transfer
drafts. Pair/group only from explicit source evidence, never proximity or approximate amount.
Missing counterparts, contradictory pairs and unknown unit actions emit an `unrecognized_event`
blocker for that group while unrelated groups remain usable. Extraction preserves source facts and
does not implement Task 15 lot/value transformations. Forward-only V35 adds nullable
`investment_event_draft.unit_ratio NUMERIC(20,10)`; positive non-unity ratios only. A
ready/duplicate/committed split draft requires a ratio and no units; non-split drafts forbid ratios.
Replace the evidence-backed preservation function forward so attached-evidence drafts cannot change
ratio. Include ratio in the typed fingerprint. SQL tests prove scale, shapes, ready-state rules,
immutability and safe upgrade of existing rows without editing V31 or V32.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`, reuse it through
`eval "$(scripts/test-db.sh env)"` for `scripts/hooks/db-tests.sh` and
`cd api && uv run pytest -q tests/test_investment_extraction_actions.py`, then tear down once with
`scripts/test-db.sh down`.
Acceptance: split/switch/transfer evidence is exact, group-atomic and stageable; ambiguous partial
transfers abstain without guessing.

## Task 12.1 — preparation and safe preview contract

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6l1-investment-staging-preview` / `develop` / `develop`.
**Depends:** Task 11.3. **Forecast:** 200–270 production, 320–430 tests and fixtures.
**Files:** create `api/oneledger/investments/staging.py` and
`api/tests/test_investment_staging.py`; reuse the existing investment PDF fixture without changing
the extraction contract; update shared privacy/status paths only as needed.
**Contract/tests:** Immutable `TargetResolution`, `PreparedTarget`, `PreparedGroup` and
`PreparedImport` contracts consume the frozen Task 11 extraction result. Resolution requests use
only extraction group numbers and explicit account/security UUIDs; no private source value appears
in a request, response or representation. Legs carrying one opaque source correlation become one
prepared atomic group while each leg retains its own target. A blocker on either correlated leg
blocks the pair; unrelated groups remain usable. Exact dates and `Decimal` values remain unchanged.
Preview returns at most 50 groups plus total group/draft/blocker counts and `preview_truncated`.
Unresolved targets cannot be stageable. Preparation performs no storage, database, route, filename,
raw-row or source-reference work.
**RED/GREEN:** `cd api && uv run pytest -q tests/test_investment_staging.py`.
Acceptance: preparation is deterministic, immutable, privacy-safe and correlation-aware, and a
resolved preview contains enough typed information for persistence without reinterpreting source
rows.

## Task 12.2 — idempotent atomic persistence

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6l2-investment-staging-persistence` / `develop` / `develop`.
**Depends:** Tasks 4 and 12.1. **Forecast:** 350–460 production, 520–690 tests and fixtures.
**Files:** extend `api/oneledger/investments/staging.py` and
`api/tests/test_investment_staging.py`; create `api/tests/test_investment_staging_live.py`,
`db/migrations/V36__investment_evidence_source_scope.sql` and
`db/tests/0033_investment_evidence_source_scope.sql`; update schema, data-model and progress
documentation.
**Contract/tests:** Forward-only V36 includes `source_contract` in fingerprint replay scope and adds
supporting household/source-contract overlap indexes without changing the existing evidence
operation signature. Stage under one household lock and transaction. Validate every explicit UUID
against owner household, investment-account kind, security scope and native currency. A unique exact
active complete account identifier may be reused; masked-only evidence never auto-selects. Security
names and untyped references never auto-select; explicit security UUIDs are verified. Identical
active retry returns the existing import and rows. Across documents, typed source identity wins over
fingerprint; exact committed overlap attaches new evidence to the existing immutable transaction
without a new draft, while conflict attaches nothing and creates one group-local
`duplicate_conflict`. Unknown actions persist as group-local blockers. Correlated legs share one
database group. Cancellation is idempotent and terminal, retains staged provenance and never creates
or changes a holding, transaction, lot, allocation or valuation; retry does not silently reopen it.
Storage/database failure rolls back all database rows; a later database failure may leave only the
inert content-addressed blob.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`, reuse it through
`eval "$(scripts/test-db.sh env)"` for `scripts/hooks/db-tests.sh` and
`cd api && uv run pytest -q tests/test_investment_staging.py
tests/test_investment_staging_live.py`, then tear down once with `scripts/test-db.sh down`.
Acceptance: resolved extraction stages atomically and idempotently, overlapping committed evidence
does not duplicate financial facts, and holdings/net worth remain unchanged.

## Task 12.3 — owner API and cancellation surface

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6l3-investment-staging-api` / `develop` / `develop`.
**Depends:** Task 12.2. **Forecast:** 250–340 production, 420–560 tests and fixtures.
**Files:** create `api/oneledger/routes/investment_imports.py`; update
`api/oneledger/main.py`, staging tests and shared API types/contracts; optionally create the shared
`api/oneledger/ingest/uploads.py` boundary and update `api/oneledger/routes/ingest.py` mechanically;
update statement-ingestion, privacy and progress documentation.
**Contract/tests:** `POST /investment-imports/parse` accepts multipart PDF, optional password,
strict resolution JSON and `stage=false` by default. Dry run performs no storage or database write.
A stage request re-extracts and verifies resolutions rather than trusting previewed facts. Responses
use exact decimal strings, fixed reason/status codes, safe masks, UUIDs, at most 50 groups, total
counts and `previewTruncated`; they never expose filename, raw row, complete identifier, security
reference, correlation identity or source-event identity. `POST /investment-imports/{id}/cancel` is
owner-scoped and idempotent. Cross-household, malformed resolution, stale retry, concurrent
stage/cancel, storage failure and Unicode identifier leakage fail atomically.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`, reuse it through
`eval "$(scripts/test-db.sh env)"` for `scripts/hooks/db-tests.sh` and
`cd api && uv run pytest -q tests/test_investment_staging.py
tests/test_investment_staging_live.py tests/test_ingest_route_live.py`, then tear down once with
`scripts/test-db.sh down`; regenerate shared API types.
Acceptance: the owner can safely preview, explicitly resolve, stage, retry and cancel an investment
import through authenticated application operations.

## Task 13 — summary checkpoints

**Branch/base/target:** `feature/p1-6m-investment-checkpoints` / `develop` / `develop`.
**Files:** `api/oneledger/investments/summary_fund_statement.py`,
`api/oneledger/investments/staging.py`, `api/tests/test_investment_checkpoints.py`,
`api/tests/test_investment_checkpoints_live.py`; shared ingestion/status paths.
**Contract/tests:** Content-selected summary adapter emits position/cost/value checkpoints only;
matched/mismatched detailed+summary, duplicate/overlap, unknown security and no-event RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_checkpoints.py tests/test_investment_checkpoints_live.py)`. Acceptance: a
summary confirms positions without becoming transaction history or terminal price.

Task 14 was split at dispatch. Its database, domain/API and allocation surfaces plus SQL and Python
suites reforecast to 900–1,150 production lines and 1,200–1,600 test lines, beyond one reviewable
branch. The split establishes the authorized event operation before an API can call it, then keeps
allocation evidence independently reviewable before it can make basis or funding claims.

## Task 14.1 — cash-lifecycle database operation

**Branch/base/target:** `feature/p1-6n1-investment-cash-event-operation` / `develop` / `develop`.
**Files:** `db/migrations/V37__investment_cash_lifecycle.sql`,
`db/tests/0034_investment_cash_lifecycle.sql`; shared schema/status paths.
**Contract/tests:** Extend the owner-authorized atomic investment append boundary with exact
event-kind fields for cash distributions, interest, reinvested distributions, standalone charges
and withheld tax while preserving sale/redemption/maturity behavior. A reinvested distribution has
evidenced positive units and gross basis, creates its acquisition-origin lot and produces no
external cash flow. Imported facts remain immutable; corrections append; household scope, RLS,
dedupe, direction, precision and atomic refusal remain enforced. No basis, FIFO, average-cost or
jurisdiction rule is inferred.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `scripts/hooks/db-tests.sh`; run full verification on
that same server and tear it down once with `scripts/test-db.sh down`. Acceptance: V37 admits every
supported cash-lifecycle kind through one sanctioned operation and reconstructs the correct
holding/lot effects without manufacturing external cash.

## Task 14.2 — disposal and income owner API

**Branch/base/target:** `feature/p1-6n2-investment-cash-events` / `develop` / `develop`.
**Files:** `api/oneledger/investments/cash_lifecycle.py`,
`api/oneledger/investments/schemas.py`, `api/oneledger/routes/investment_events.py`,
`api/tests/test_investment_cash_lifecycle.py`,
`api/tests/test_investment_cash_lifecycle_live.py`,
`db/migrations/V38__investment_review_resolution.sql`,
`db/tests/0035_investment_review_resolution.sql`; shared API/type/schema/status paths.
**Contract/tests:** Owner-authorized sale, redemption and maturity events change units and cash;
cash distributions, interest, reinvested distributions, standalone charges and withheld tax retain
their distinct economic meaning. Partial sale, closed/reopened history, exact event-kind fields,
append-only corrections, one-count charges/tax and external-flow boundary RED cases. A reinvested
distribution creates evidenced units/basis but no external cash. A disposal without evidenced lot
selection commits units and cash, creates one `incomplete_lot_allocation` review and exposes neither
realised cost/gain nor remaining basis.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `(cd api && uv run pytest -q
tests/test_investment_cash_lifecycle.py tests/test_investment_cash_lifecycle_live.py)`; run full
verification on that same server and tear it down once with `scripts/test-db.sh down`. Acceptance:
every cash-lifecycle event is exact, reconstructs units, preserves closed history and never counts
proceeds, income, reinvestment, charges or tax twice.

## Task 14.3 — evidenced lot and cash allocation

**Branch/base/target:** `feature/p1-6n3-investment-cash-allocations` / `develop` / `develop`.
**Files:** `db/migrations/V39__investment_lot_review_resolution.sql`,
`db/tests/0036_investment_lot_review_resolution.sql`, `api/oneledger/investments/cash_lifecycle.py`,
`api/oneledger/investments/funding.py`, `api/oneledger/routes/investment_funding.py`,
`api/tests/test_investment_cash_lifecycle.py`,
`api/tests/test_investment_cash_lifecycle_live.py`, `api/tests/test_investment_funding.py`,
`api/tests/test_investment_funding_live.py`; shared API/type/schema/testing/status paths.
**Contract/tests:** Explicit owner evidence allocates disposal units to acquisition lots and cash
transactions to purchase funding, sale proceeds, distributions, charges and tax using the fixed V33
roles. Cover full and partial sale evidence, missing allocation availability, many-to-many cash
roles, over-allocation, cross-household and wrong-currency refusal, correction/unlink restoration,
and one-count flow totals. Lot confirmations carry bounded idempotency and exact expected state;
only active disposal and acquisition leaves participate, allocation/correction share the database's
event-to-holding-to-lot/review lock order, and retained evidence must be removed before correction.
The database alone derives and immutably retains the allocation decision and its exact nullable
claims. Proposal state includes the disposal date and refuses any acquisition lot dated after it.
No FIFO, average-cost or jurisdiction rule is inferred.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `(cd api && uv run pytest -q
tests/test_investment_cash_lifecycle.py tests/test_investment_cash_lifecycle_live.py
tests/test_investment_funding.py tests/test_investment_funding_live.py)`; run full verification on
that same server and tear it down once with `scripts/test-db.sh down`. Acceptance: confirmed lot and
cash evidence makes only its supported basis/funding claims available; unresolved evidence withholds
only the dependent claim.

Task 15 was split into 15.1–15.5 at dispatch; the 14 September re-baseline kept 15.1 and replaced
the rest with Task 15.2.

## Task 15.1 — lot lineage read model and guards

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6o1-investment-lot-lineage` / `develop` / `develop`.
**Depends:** Tasks 4 and 14.3. **Forecast:** 430–500 production, 620–780 tests. **Size:** above
1,000 lines because every lot read, capacity check and correction guard must change together.
**Files:** create `db/migrations/V40__investment_lot_lineage.sql` and
`db/tests/0037_investment_lot_lineage.sql`; update `api/oneledger/investments/staging.py`,
`api/oneledger/investments/funding.py`, `api/tests/test_investment_checkpoints_live.py` and
`api/tests/test_investment_funding_live.py`; shared schema/data-model/testing/status paths.
**Contract/tests:** V40 adds nullable `acquisition_lot.predecessor_lot_id` (household-scoped self FK;
`(origin_transaction_id, predecessor_lot_id)` unique, nulls not distinct) and
`investment_transaction.counterpart_transaction_id` (household-scoped FK, unique, present exactly
for paired `*_in` kinds). Lineage kinds are `split`, `platform_transfer_in` and
`platform_transfer_out`. A BEFORE INSERT order guard on unit- or lot-affecting rows (`units` present
or `split`) raises `investment_unit_event_order` when a row is dated before an active lineage event
in its holding or corrects an event dated on or before one, excluding the corrected event itself.
Same-day rows committed after a lineage event follow it; cash-only kinds are exempt; exact dedupe
replay returns before the guard. `investment_lot_lineage_available(p_holding_id UUID,
p_as_of DATE) RETURNS BOOLEAN` is false from the date of an active split with no successor lot.
`investment_open_lots(p_holding_id UUID, p_as_of DATE)` returns lot, acquisition date, open units
and `round(basis × open / acquired, 4)`; a lot or allocation counts only when its creating event is
active and dated on or before `p_as_of`, and nothing returns while lineage is unavailable. Lot
capacity in `allocate_investment_lots_v33`, `guard_correction_unfunded_event`, lot proposal state in
`funding.py` and staging checkpoint basis count allocations from active events only. Allocation
refuses a successor lot for a disposal dated before its creating event and any lot for a disposal
dated on or after an unavailable-lineage split; `confirm_investment_lot_allocation` then withholds
remaining basis. The correction guard exempts a corrected split's or transfer out-leg's own
consumption and refuses once its successor lots are allocated or consumed by an active event. SQL
fixtures create lineage rows only under the append guard. RED cases: capacity restored after a
corrected consumer, as-of successor and consumption exclusion, backdated, same-day and cash-only
order, replay overlap, withheld-lineage allocation, proposal and basis, direct DML on new columns
and lots, and member/cross-household reads.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `scripts/hooks/db-tests.sh` and `(cd api && uv run
pytest -q tests/test_investment_checkpoints_live.py tests/test_investment_funding_live.py)`; run
`scripts/verify.sh` on that same server, which tears it down.
Acceptance: lot reads, capacity, proposals, decisions and correction guards agree on active lineage,
and existing acquisition, disposal and allocation results are unchanged.

## Task 15.2 — holding-level unit actions

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6o2-investment-unit-actions` / `develop` / `develop`.
**Depends:** Task 15.1. **Forecast:** 900–1,100 production, 1,000–1,300 tests.
**Files:** create `db/migrations/V41__investment_unit_actions.sql`,
`db/tests/0038_investment_unit_actions.sql`, `api/oneledger/investments/unit_actions.py`,
`api/tests/test_investment_unit_actions.py` and `api/tests/test_investment_unit_actions_live.py`;
update the function teardown in `db/tests/0027_investment_instruments_and_valuations.sql`,
`api/oneledger/investments/schemas.py`, `api/oneledger/investments/positions.py`,
`api/oneledger/investments/staging.py` and `api/oneledger/routes/investment_events.py`; shared
schema/data-model/API/type/testing/status paths.
**Contract:**

- V41 moves the V37 append body into revoked `append_investment_event_core(<the 23 V37 arguments>,
  p_unit_ratio NUMERIC, p_counterpart_transaction_id UUID) RETURNS UUID`; ratio scale 10 is checked
  before storage, and ratio and counterpart join exact replay identity. The unchanged 23-argument
  `append_investment_transaction` calls it, additionally admits `bonus` and `rights`, and still
  refuses `split` and paired kinds.
- V41 asserts no `split` row exists, then replaces `investment_event_kind_fields_agree` so a split
  carries a non-zero `units` delta and a positive non-unity `unit_ratio`, with no price, gross or
  cash.
- Owner-only `append_investment_split(p_account_id UUID, p_security_id UUID, p_event_date DATE,
  p_unit_ratio NUMERIC, p_source_kind investment_event_source, p_source_contract TEXT,
  p_source_event_id TEXT, p_dedupe_version TEXT, p_dedupe_fingerprint TEXT,
  p_corrects_transaction_id UUID, p_correction_reason TEXT) RETURNS UUID` returns an exact replay
  first; locks the holding; raises `investment_split_is_latest` when an active unit-affecting event
  in the holding is dated after the split (or after the split it corrects); and writes units =
  holding units excluding any corrected split × (ratio − 1), raising
  `investment_split_units_are_exact` unless exact at scale 8. It creates no lot.
- Owner-only `append_investment_unit_transfer(p_out_kind investment_event_kind, p_event_date DATE,
  p_out_account_id UUID, p_out_security_id UUID, p_out_units NUMERIC, p_out_price NUMERIC,
  p_out_gross_amount NUMERIC, p_in_account_id UUID, p_in_security_id UUID, p_in_units NUMERIC,
  p_in_price NUMERIC, p_in_gross_amount NUMERIC, p_currency CHAR(3),
  p_source_kind investment_event_source, p_source_contract TEXT, p_out_source_event_id TEXT,
  p_out_dedupe_version TEXT, p_out_dedupe_fingerprint TEXT, p_in_source_event_id TEXT,
  p_in_dedupe_version TEXT, p_in_dedupe_fingerprint TEXT, p_corrects_out_transaction_id UUID,
  p_correction_reason TEXT) RETURNS TABLE (out_event_id UUID, in_event_id UUID)` accepts
  `switch_out`, `systematic_transfer_out` or `platform_transfer_out`, locks both accounts in UUID
  order and writes the out leg then the in leg naming it as counterpart, or nothing. Switch and
  systematic legs share an account, differ in security and have equal gross; platform legs share a
  security, differ in account and have opposite units; otherwise it raises
  `investment_unit_transfer_pair_agrees`. An exact pair replay returns both ids; a correction
  replaces both legs.
- A V41 BEFORE INSERT trigger on `investment_lot_allocation` raises
  `investment_lot_allocation_requires_cash_disposal` for a switch, systematic or platform leg, or
  for a disposal dated on or after an active split, switch, systematic or platform leg in its
  holding.
- Owner-only `POST /investment-events/unit-actions` accepts a strict body discriminated by `kind`
  (`bonus`, `rights`, `split`, `switch`, `systematic_transfer`, `platform_transfer`) with exact
  decimal strings and one idempotency key from which paired leg identities derive;
  `POST /investment-events/{event_id}/unit-action-corrections` adds `expectedCorrectionState` and a
  screened reason. Reuse the cash-lifecycle idempotency, frozen-flow (rights only),
  reason-screening and error-translation helpers. `inexact_split`, `event_order_conflict` (V40
  order guard or split-is-latest) and `invalid_transfer_pair` are fixed 422 reasons; identity
  conflict is 409 and a foreign reference 404. Responses return the event or both legs, holding
  units and lots created.
- `positions.py`: `rights` joins contributions; gain stays available for purchase, rights, bonus
  and split history and is withheld by any switch, systematic or platform leg.
- `staging.py`: checkpoint cost is withheld (not compared, no review) for a holding with an active
  split, switch, systematic or platform leg dated on or before the checkpoint.
- No successor lot, lot selection or realised claim; `acquisition_lot.predecessor_lot_id` stays
  null.

**Tests:** the financial write floor for both functions and both routes; exact, inexact, reverse,
later-event and corrected split; bonus zero-cost lot; fundable rights; pair all-or-nothing on a
second-leg failure; each pair mismatch; replay and conflict; lot allocation refused for legs and
later disposals; reportable flows unchanged; positions contribution and gain rules; checkpoint cost
withheld.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)"`; `scripts/hooks/db-tests.sh`;
`(cd api && uv run pytest -q tests/test_investment_unit_actions.py
tests/test_investment_unit_actions_live.py tests/test_positions.py tests/test_portfolio_live.py
tests/test_investment_checkpoints_live.py)`; shared types command; `scripts/verify.sh`.
Acceptance: each corporate action changes holding units exactly through one owner operation,
manufactures no cash, contribution (except rights), lot lineage or realised claim, and withholds
basis and realised claims from its date.

## Task 16 — import review, security catalogue and commit

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6p-investment-import-commit` / `develop` / `develop`.
**Depends:** Tasks 13, 14.2 and 15.2. **Forecast:** 650–800 production, 900–1,100 tests.
**Files:** create `api/oneledger/investments/commit.py`, `api/tests/test_investment_commit.py` and
`api/tests/test_investment_commit_live.py`; update `api/oneledger/routes/investment_imports.py`,
`api/oneledger/routes/investment_catalogue.py`, `api/oneledger/investments/staging.py` and
`api/tests/test_investment_catalogue_live.py`; shared API/type/ingestion/privacy/status paths. No
migration.
**Contract:**

- `POST /investment-catalogue/securities` creates a household-scoped security from owner-confirmed
  name, security type, asset class, currency and optional ISIN (country from the ISIN prefix or
  owner input); an existing ISIN returns 409 with the existing UUID.
- Preview and `GET /investment-imports/{importId}` return per target a bounded `securityLabel`
  passed through the existing identifier redaction, an optional well-formed `isin`, and at most
  five `securityCandidates` from exact ISIN match (suggestions only), plus group status, drafts,
  checkpoints, open review reasons, totals and an opaque `expectedState`.
- `POST /investment-imports/{importId}/commit` with `expectedState`: under the household lock every
  `ready` group commits in its own savepoint through `append_investment_transaction`,
  `append_investment_split` or `append_investment_unit_transfer` (correlated legs), with statement
  provenance, the draft's dedupe identity and derived frozen FX. Drafts become `committed` with
  `committed_transaction_id`, groups `committed`, and the import `partially_committed` or
  `completed`. A database refusal or missing FX rolls back only that group with a fixed reason;
  blocked groups never commit. Identical replay returns the prior result; changed state is 409; a
  foreign import is 404.
- Committed disposals without lot evidence open the existing non-blocking
  `incomplete_lot_allocation` review; no `unmatched_cash` review is created. Cancel keeps committed
  groups and cancels the rest.
- After commit, checkpoints reconcile; units matched with cost withheld is `matched`, with no
  review.

**Tests:** the financial write floor for commit and security creation; core, split and pair
commits; monthly overlap commits nothing twice; an inexact split and an unequal-gross switch roll
back only their groups; a blocked group stays uncommitted; replay and stale state; cancel after a
partial commit; hostile Unicode identifiers in labels; holdings and net worth change once;
checkpoint matched with withheld cost.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)"`; `(cd api && uv run pytest -q
tests/test_investment_commit.py tests/test_investment_commit_live.py
tests/test_investment_staging_live.py tests/test_investment_checkpoints_live.py
tests/test_investment_catalogue_live.py)`; shared types command; `scripts/verify.sh`.
Acceptance: a staged detailed statement commits its ready groups once, independently of blocked
groups, and a re-imported overlap adds no event.

## Task 17 — investment import web flow

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6q-investment-import-web` / `develop` / `develop`.
**Depends:** Task 16. **Forecast:** 700–900 production, 500–700 tests.
**Files:** create `web/app/investments/import/page.tsx`,
`web/app/investments/import/[importId]/page.tsx`,
`web/app/investments/import/investment-import-screen.tsx`,
`web/app/investments/import/__tests__/investment-import-screen.test.tsx` and
`web/app/investments/security-form.tsx`; update `web/app/investments/entry-screen.tsx`,
`web/app/workspace.tsx` and `web/app/globals.css`; status path.
**Contract:** Upload (PDF, request-only password) → dry-run counts → per-group account and security
choice prefilled only from `securityCandidates`, with inline security creation → stage → review
(group status, fixed reason text) → commit ready groups or cancel → completion counts.
`/investments/import/{importId}` resumes review. The entry screen reuses the security form. Server
strings only; no money arithmetic; loading, empty, error, 401 and 409-refresh states; no filename,
password or identifier in storage or URL. Follow the `web/app/import/import-screen.tsx` patterns.
**Tests:** happy path to commit with a mocked API; unsupported format; a prefill still needs
confirmation; a blocked group is not committable; 409 refresh; cancel; no storage writes.
**RED/GREEN:** `cd web && npx vitest run
app/investments/import/__tests__/investment-import-screen.test.tsx && npm run typecheck`;
`scripts/verify.sh`.
Acceptance: the owner imports a detailed statement from upload to committed holdings in the app.

## Task 18 — official prices and returns engine

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6r-investment-prices-returns` / `develop` / `develop`.
**Depends:** Tasks 8 and 15.2. **Forecast:** 750–950 production, 1,000–1,300 tests.
**Files:** create `api/oneledger/investments/price_adapters.py`,
`api/oneledger/investments/prices.py`, `api/oneledger/investments/returns.py`,
`api/oneledger/routes/investment_prices.py`, `api/tests/test_investment_prices.py`,
`api/tests/test_investment_prices_live.py` and `api/tests/test_investment_returns.py`; update
`api/oneledger/main.py`, `.env.example`, `docker-compose.yml` and `docker-compose.production.yml`;
shared API/type/privacy/testing/status paths.
**Contract:**

- `official_nav_text_v1` fetches the configured URL with the existing `httpx` dependency (timeout
  and size cap) and parses the latest publication into `Decimal` prices; its currency is
  configuration.
- A household security with an ISIN and no active mapping for that source gains one when exactly
  one publication code carries that ISIN and the currencies agree; otherwise nothing is mapped.
- Refresh appends or idempotently confirms accepted `price_snapshot` rows for mapped securities; a
  different value for an existing identity is retained unaccepted.
- `GET /investment-prices/status` is read-only. Owner-only `POST /investment-prices/refresh` is a
  no-op within 24 hours of a successful check (held in process memory; a restart refetches once);
  an outage returns fixed `source_unavailable` and keeps cached prices. Tests never use the
  network.
- `xirr(flows: Sequence[CashFlow]) -> XirrResult`, with `CashFlow(on: date, amount: Decimal)` and
  `XirrResult(rate_percent: Decimal | None, reason: XirrReason | None)`, implements the
  specification's equation, Decimal context and quantization. `holding_flows` and
  `portfolio_flows` read active events: purchase, recurring purchase, rights, charge and withheld
  tax are net outflows; sale, redemption, maturity, cash distribution and interest are net inflows;
  current value is the terminal inflow. Reinvested distributions, bonus, split and transfer legs
  add no flow; a holding with a switch, systematic or platform leg returns `incomplete_history`; no
  price returns `missing_price`.
- `allocation` groups current value by asset class and by account with ten-decimal ratio strings,
  unavailable when an included holding is unvalued.

**Tests:** parse precision and malformed rows; ISIN mapping unique, ambiguous and currency
mismatch; replay, retained conflict, outage, 24-hour no-op and owner-only refresh; an independently
verified series within 0.01 percentage points; `no_sign_change`; `multiple_roots`; charges counted
once and internal events excluded; allocation exact and incomplete.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)"`; `(cd api && uv run pytest -q
tests/test_investment_prices.py tests/test_investment_prices_live.py
tests/test_investment_returns.py)`; shared types command; `scripts/verify.sh`.
Acceptance: mapped holdings value at the latest dated official price, missing never means zero, and
XIRR proves or refuses each claim.

## Task 19 — portfolio, holding and data-quality API

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6s-investment-portfolio-api` / `develop` / `develop`.
**Depends:** Tasks 16 and 18. **Forecast:** 600–800 production, 800–1,000 tests.
**Files:** create `api/oneledger/investments/portfolio.py`,
`api/tests/test_portfolio_summary_live.py` and `api/tests/test_holding_detail_live.py`; update
`api/oneledger/investments/positions.py`, `api/oneledger/routes/portfolio.py`,
`api/oneledger/routes/doctor.py` and `api/tests/test_doctor_live.py`; shared API/type/status paths.
**Contract:** `GET /portfolio/summary?asOf=` returns value, contributions and gain in both
reporting currencies (gain only when every included holding's gain is available), portfolio XIRR or
its reason, allocation by asset class and by account, valued/unvalued/stale counts and price
status. `GET /portfolio/positions` adds holding XIRR or reason and `includeClosed`; closed holdings
never count in current value. `GET /portfolio/holdings/{holdingId}` returns the holding, its active
and corrected events newest first (limit 200 plus total), cash links (role, cash transaction UUID,
amount), the last 30 accepted prices and open review reasons — no lots, evidence or identifiers.
`GET /doctor` adds an Investments section: blocking import reviews, unvalued holdings, prices older
than seven days, unfunded purchases and disposals whose realised gain is unavailable. Owner-only;
a foreign holding is 404; decimal strings only; no advisory field.
**Tests:** totals reconcile to positions in both currencies; gain null when one holding withholds;
allocation exact and incomplete; API XIRR equals the engine; closed holdings; paging and corrected
events; foreign and member refusal; doctor findings.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)"`; `(cd api && uv run pytest -q
tests/test_portfolio_summary_live.py tests/test_holding_detail_live.py tests/test_portfolio_live.py
tests/test_positions.py tests/test_doctor.py tests/test_doctor_live.py)`; shared types command;
`scripts/verify.sh`.
Acceptance: every portfolio figure reconciles to holdings or states why it is unavailable.

## Task 20 — portfolio web and Phase 1.6 acceptance

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6t-investment-portfolio-web` / `develop` / `develop`.
**Depends:** Tasks 17 and 19. **Forecast:** 800–1,000 production, 500–700 tests.
**Files:** create `web/app/investments/portfolio-screen.tsx`,
`web/app/investments/holding-screen.tsx`, `web/app/investments/[holdingId]/page.tsx`,
`web/app/investments/__tests__/portfolio-screen.test.tsx` and
`web/app/investments/__tests__/holding-screen.test.tsx`; update `web/app/investments/page.tsx`,
`web/app/workspace.tsx` and `web/app/globals.css`; status path.
**Contract:** The investments page opens on the portfolio: value, contributions, gain and XIRR each
with its as-of date or unavailable reason; refresh posts once when status says due and labels stale
prices; allocation by asset class and account; open holdings (units, price and date, value, gain,
XIRR) with a closed-holdings toggle; entry and import links. Holding detail shows events, funding
links, prices and review reasons. Investment data quality appears in the existing Settings › Data
Quality screen. No client arithmetic, advice, forecast or judgement colours.
**Tests:** unavailable reasons; refresh due and not due; stale and missing labels; incomplete
allocation; holding detail; 401.
**RED/GREEN:** `cd web && npx vitest run app/investments/__tests__/portfolio-screen.test.tsx
app/investments/__tests__/holding-screen.test.tsx && npm run typecheck`; `scripts/verify.sh`.
**Acceptance:** the specification's acceptance criteria pass through the web app; the owner runs
the real detailed and summary statements through authenticated operations and records only
structural pass/fail counts.

## Stop conditions

- Do not dispatch task 1 until `/oneledger-plan-review` has no blocking finding and the owner has
  approved every material correction.
- Stop instead of inventing source evidence, identifier meaning, cost basis, lot selection or an
  action. Future retirement evidence requires a new owner decision; none exists now.
- Stop before push when required verification is red/not run, code review blocks, or size exceeds
  budget. Never test private data by direct SQL or commit private filenames, identities, facts or
  inferred relationships.
