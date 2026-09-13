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
- Unknown unit action/mismatch/ambiguity/conflict blocks its group. Funding or lot gaps create
  reviews; lot gaps withhold basis, not XIRR; summary mismatch creates no event.
- Keep contributions, open-lot basis and cost checkpoints distinct. Statement NAV is not terminal
  value without a separate valuation checkpoint.
- Update owning docs and only `progress.md` for status. Keep tasks below 800/1,600 counted lines.

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
After focused commands: full `/verify`, review, fix, reverify, confirm, first push, PR, merge/delete.

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
| 15 | corporate actions and transfers | `feature/p1-6o-investment-corporate-actions` | 540 | 760 | 4, 14.3 |
| 16 | review and atomic commit | `feature/p1-6p-investment-commit` | 620 | 820 | 12.3, 13, 14.3, 15 |
| 17 | upload web flow | `feature/p1-6q-investment-import-web` | 480 | 450 | 12.3, 13 |
| 18 | review/commit web flow | `feature/p1-6r-investment-review-web` | 620 | 580 | 16–17 |
| 19 | official prices and refresh | `feature/p1-6s-investment-prices` | 620 | 880 | 5, 8 |
| 20 | allocation and XIRR engine | `feature/p1-6t-investment-returns` | 500 | 850 | 15, 19 |
| 21 | portfolio analytics API | `feature/p1-6u-investment-analytics-api` | 500 | 680 | 20 |
| 22 | history/data-quality API | `feature/p1-6v-investment-history-api` | 520 | 680 | 16, 20–21 |
| 23 | portfolio web flow | `feature/p1-6w-investment-portfolio-web` | 580 | 520 | 18, 21 |
| 24 | history web and acceptance | `feature/p1-6x-investment-history-web` | 650 | 620 | 22–23 |

Task 11 was split at dispatch after a 2,150–2,800-line reforecast exposed the draft-ratio
contract below. Task 12 was split after its 2,350–3,310-line dispatch reforecast exposed the
evidence-scope correction below. Task 14 was split after its 2,100–2,750-line reforecast exposed
the missing database operation below. Tasks 15 and 19 still need dispatch-time reforecasting and
must split before exceeding 1,600 lines.

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

## Task 15 — splits, bonuses, switches and platform transfers

**Branch/base/target:** `feature/p1-6o-investment-corporate-actions` / `develop` / `develop`.
**Files:** `db/migrations/V40__investment_unit_lifecycle.sql`,
`db/tests/0037_investment_unit_lifecycle.sql`, `api/oneledger/investments/unit_lifecycle.py`,
`api/oneledger/routes/investment_events.py`, `api/tests/test_investment_unit_lifecycle.py`,
`api/tests/test_investment_unit_lifecycle_live.py`; shared API/type/schema/status paths.
**Contract/tests:** A forward owner-authorized database operation admits evidenced split, bonus,
rights, switch and platform-transfer kinds that V31 currently refuses. Cover switch atomicity, full
lot-preserving transfer, refused ambiguous partial transfer and unknown action block;
basis/value/history, corrections, review and no contribution/return RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_unit_lifecycle.py tests/test_investment_unit_lifecycle_live.py)`; shared types.
Acceptance: P12 transfer/split meaning is preserved before its later distribution calculation.

## Task 16 — review decisions and atomic import commit

**Branch/base/target:** `feature/p1-6p-investment-commit` / `develop` / `develop`.
**Files:** `api/oneledger/investments/commit.py`, `api/oneledger/routes/investment_imports.py`,
`api/tests/test_investment_commit.py`, `api/tests/test_investment_commit_live.py`; shared
API/type/ingestion/status paths.
**Contract/tests:** Expected-state decisions; re-resolve/reconcile/commit per group; manual fallback
calls existing commands; summary creates no event. Matrix, concurrency, rollback, monthly
overlap, review closure, cancel and unknown-action dependent-return RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_commit.py tests/test_investment_commit_live.py)`; shared types. Acceptance:
valid groups commit once and invalid groups remain safe/actionable independently.

## Task 17 — investment upload and identification web flow

**Branch/base/target:** `feature/p1-6q-investment-import-web` / `develop` / `develop`.
**Files:** `web/app/investments/import/page.tsx`,
`web/app/investments/import/investment-import-screen.tsx`,
`web/app/investments/import/__tests__/investment-import-screen.test.tsx`, `web/app/workspace.tsx`,
`web/app/globals.css`; shared status path.
**Contract/tests:** Detailed/summary selection, dry-run identification, safe counts, unsupported/
ambiguous/storage/auth/service/zero-file/accessibility states and no filename persistence RED cases.
**RED/GREEN:** `cd web && npx vitest run
app/investments/import/__tests__/investment-import-screen.test.tsx && npm run typecheck && npm run
typecheck:compat && npm run build`. Acceptance: upload reaches staged groups without committing.

## Task 18 — investment review and commit web flow

**Branch/base/target:** `feature/p1-6r-investment-review-web` / `develop` / `develop`.
**Files:** `web/app/investments/import/[importId]/page.tsx`,
`web/app/investments/import/investment-review-screen.tsx`,
`web/app/investments/import/__tests__/investment-review-screen.test.tsx`, `web/app/globals.css`,
shared status path.
**Contract/tests:** Group states follow severity matrix; exact manual fallback; stale decisions;
detailed/summary/overlap completion; valid-plus-blocked, cancel, auth/accessibility and private
render/error/storage leakage RED cases.
**RED/GREEN:** `cd web && npx vitest run
app/investments/import/__tests__/investment-review-screen.test.tsx && npm run typecheck && npm run
typecheck:compat && npm run build`. Acceptance: review/correction commits facts once with blockers.

## Task 19 — official price adapter, storage and refresh

**Branch/base/target:** `feature/p1-6s-investment-prices` / `develop` / `develop`.
**Files:** `api/oneledger/investments/price_adapters.py`, `api/oneledger/investments/prices.py`,
`api/oneledger/routes/investment_prices.py`, `api/oneledger/main.py`,
`api/tests/test_investment_prices.py`, `api/tests/test_investment_prices_live.py`, `.env.example`,
`docker-compose.yml`, `docker-compose.production.yml`; shared API/type/privacy/status paths.
**Contract/tests:** Configured complete-text source, synthetic network, Decimal parse,
effective mapping/identity/accepted source, 24-hour owner POST refresh. Replay/conflict, unchanged
non-published day, outage/cache/stale/missing mapping/manual choice/no fabricated units RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_prices.py tests/test_investment_prices_live.py)`; shared types. Acceptance:
published NAV is dated/sourced, outage retains stale cache, and missing never means zero.

## Task 20 — exact allocation and XIRR engine

**Branch/base/target:** `feature/p1-6t-investment-returns` / `develop` / `develop`.
**Files:** `api/oneledger/investments/allocation.py`, `api/oneledger/investments/returns.py`,
`api/tests/test_investment_allocation.py`, `api/tests/test_investment_returns.py`; shared
schema/testing/status paths.
**Contract/tests:** Shared algorithm; holding/account/class/portfolio flows; independent series
within 0.01 percentage points, roots near both boundaries and two roots separated by one grid
interval; unavailable codes, charges once, internal-event exclusion, incomplete denominator,
permutation/scale and no-float source RED cases.
**RED/GREEN:** `cd api && uv run pytest -q tests/test_investment_allocation.py
tests/test_investment_returns.py && uv run ruff check . && uv run mypy .`. Acceptance: allocation
reconciles and XIRR proves or explicitly refuses every claim.

## Task 21 — portfolio summary, allocation and return API

**Branch/base/target:** `feature/p1-6u-investment-analytics-api` / `develop` / `develop`.
**Files:** `api/oneledger/investments/read_models.py`, `api/oneledger/routes/portfolio.py`,
`api/tests/test_portfolio.py`, `api/tests/test_portfolio_live.py`; shared API/type/status paths.
**Contract/tests:** Serialize calculator only; safe scopes/coverage/as-of. Exact group totals,
blocked/missing propagation, closed exclusion from current, pagination/order, auth/RLS, string wire
types and no advisory field RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_portfolio.py tests/test_portfolio_live.py)`; shared types. Acceptance: portfolio headline,
allocation and XIRR reconcile with explicit incomplete states.

## Task 22 — holding history, provenance and data-quality API

**Branch/base/target:** `feature/p1-6v-investment-history-api` / `develop` / `develop`.
**Files:** `api/oneledger/routes/investment_history.py`,
`api/tests/test_investment_history.py`, `api/tests/test_investment_history_live.py`; shared
API/type/privacy/status paths.
**Contract/tests:** Open/closed holding, activity, lot, funding, price/review reads; safe evidence
kind/date only. Cross-household/auth, paging, basis-unavailable, blocked return, safe provenance,
identifier/error leakage and no-store separation RED cases.
**RED/GREEN:** `eval "$(scripts/test-db.sh up)" && (cd api && uv run pytest -q
tests/test_investment_history.py tests/test_investment_history_live.py)`; shared types. Acceptance:
every claim is drillable without returning raw/private evidence.

## Task 23 — portfolio summary and allocation web flow

**Branch/base/target:** `feature/p1-6w-investment-portfolio-web` / `develop` / `develop`.
**Files:** `web/app/investments/portfolio-screen.tsx`,
`web/app/investments/__tests__/portfolio-screen.test.tsx`, `web/app/investments/page.tsx`,
`web/app/workspace.tsx`, `web/app/globals.css`; shared status path.
**Contract/tests:** Value/cost/gain/XIRR with independent dates/coverage, allocation/open holdings,
refresh action and loading/empty/error/partial/stale/blocked/auth/accessibility/no-client-arithmetic
RED cases; no advice/forecast/rebalancing copy.
**RED/GREEN:** `cd web && npx vitest run app/investments/__tests__/portfolio-screen.test.tsx && npm
run typecheck && npm run typecheck:compat && npm run build`. Acceptance: the daily portfolio shows
only supported facts and truthful incomplete states.

## Task 24 — holding/history/data-quality web and acceptance

**Branch/base/target:** `feature/p1-6x-investment-history-web` / `develop` / `develop`.
**Files:** `web/app/investments/[holdingId]/page.tsx`,
`web/app/investments/holding-screen.tsx`, `web/app/investments/data-quality.tsx`,
`web/app/investments/__tests__/holding-screen.test.tsx`,
`web/app/investments/__tests__/data-quality.test.tsx`; all shared documentation/status paths.
**Contract/tests:** History/funding/lot/price/review details; unavailable/stale/blocked,
deep links, keyboard/auth and no private DOM/error/storage RED cases.
**RED/GREEN:** `cd web && npx vitest run app/investments/__tests__/holding-screen.test.tsx
app/investments/__tests__/data-quality.test.tsx`; full `/verify`.
**Acceptance:** Through authenticated operations only, supplied detailed and summary evidence and a
safe overlap fixture reconcile structurally; publish only pass/fail counts. All spec criteria are
green; run `/oneledger-code-hygiene` and ship findings separately before acceptance.

## Stop conditions

- Do not dispatch task 1 until `/oneledger-plan-review` has no blocking finding and the owner has
  approved every material correction.
- Stop instead of inventing source evidence, identifier meaning, cost basis, lot selection or an
  action. Future retirement evidence requires a new owner decision; none exists now.
- Stop before push when required verification is red/not run, code review blocks, or size exceeds
  budget. Never test private data by direct SQL or commit private filenames, identities, facts or
  inferred relationships.
