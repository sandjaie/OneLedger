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
| 15.1 | lot lineage read model and guards | `feature/p1-6o1-investment-lot-lineage` | 500 | 780 | 4, 14.3 |
| 15.2 | unit-action append and exact split | `feature/p1-6o2-investment-split-operation` | 465 | 680 | 15.1 |
| 15.3 | bonus, rights and split owner API | `feature/p1-6o3-investment-unit-actions` | 505 | 900 | 15.2 |
| 15.4 | paired switch and platform-transfer operation | `feature/p1-6o4-investment-transfer-operation` | 355 | 905 | 15.2 |
| 15.5 | switch and transfer owner API | `feature/p1-6o5-investment-transfers` | 405 | 880 | 15.3–15.4 |
| 16 | review and atomic commit | `feature/p1-6p-investment-commit` | 620 | 820 | 12.3, 13, 14.3, 15.5 |
| 17 | upload web flow | `feature/p1-6q-investment-import-web` | 480 | 450 | 12.3, 13 |
| 18 | review/commit web flow | `feature/p1-6r-investment-review-web` | 620 | 580 | 16–17 |
| 19 | official prices and refresh | `feature/p1-6s-investment-prices` | 620 | 880 | 5, 8 |
| 20 | allocation and XIRR engine | `feature/p1-6t-investment-returns` | 500 | 850 | 15.4, 19 |
| 21 | portfolio analytics API | `feature/p1-6u-investment-analytics-api` | 500 | 680 | 20 |
| 22 | history/data-quality API | `feature/p1-6v-investment-history-api` | 520 | 680 | 16, 20–21 |
| 23 | portfolio web flow | `feature/p1-6w-investment-portfolio-web` | 580 | 520 | 18, 21 |
| 24 | history web and acceptance | `feature/p1-6x-investment-history-web` | 650 | 620 | 22–23 |

Task 11 was split at dispatch after a 2,150–2,800-line reforecast exposed the draft-ratio
contract below. Task 12 was split after its 2,350–3,310-line dispatch reforecast exposed the
evidence-scope correction below. Task 14 was split after its 2,100–2,750-line reforecast exposed
the missing database operation below. Task 15 was split after its 4,050–5,250-line reforecast
exposed the lot-lineage and paired-leg contracts below. Task 19 still needs dispatch-time
reforecasting and must split before exceeding 1,600 lines.

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

Task 15 was split at dispatch, and plan review added the lineage guards and refusal matrix below.
The five tasks forecast 1,800–2,250 production lines and 3,300–4,150 test lines. V31 lots are
immutable and holding-bound, and the V37 append boundary carries neither a split ratio nor a paired
leg. Lineage reads and guards therefore land before any operation can create lineage, each
operation precedes the owner API that calls it, and splits land before paired transfers reuse them.

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

## Task 15.2 — unit-action append and exact split operation

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6o2-investment-split-operation` / `develop` / `develop`.
**Depends:** Task 15.1. **Forecast:** 375–465 production, 540–680 tests. **Size:** above 1,000
lines because the moved append body must ship with its first ratio-bearing caller.
**Files:** create `db/migrations/V41__investment_split_operation.sql` and
`db/tests/0038_investment_split_operation.sql`; update the enum-typed function teardown in
`db/tests/0027_investment_instruments_and_valuations.sql`; shared schema/data-model/status paths.
**Contract/tests:** The append body moves to revoked `append_investment_transaction_v40(<V37
arguments>, p_unit_ratio NUMERIC, p_counterpart_transaction_id UUID) RETURNS UUID`, which refuses
ratio precision beyond scale 10 before storage and includes ratio and counterpart in replay identity.
The unchanged 23-argument `append_investment_transaction` wraps it, adds `bonus` and `rights`, and
refuses split and paired kinds and their corrections. Owner-only `append_investment_split(
p_account_id UUID, p_security_id UUID, p_event_date DATE, p_unit_ratio NUMERIC, p_source_kind
investment_event_source, p_source_contract TEXT, p_source_event_id TEXT, p_dedupe_version TEXT,
p_dedupe_fingerprint TEXT, p_corrects_transaction_id UUID, p_correction_reason TEXT) RETURNS UUID`
takes event advisory locks, then account, holding, lot and review locks, so it serializes with
appends, allocations and corrections. It requires an open holding and raises
`investment_lineage_is_latest` when another active unit- or lot-affecting event is dated after it,
or on or after a corrected split. Holding units × (ratio − 1) must be exact at scale 8
(`investment_split_units_are_exact`). With available lineage, open-lot units equal to holding units
and every `open × ratio` exact, it consumes each open lot and creates successors under the
specification's basis rule; otherwise it commits only the exact unit effect and one
`incomplete_lot_allocation` review on the split. A replacement split keeps the original event,
consumption and successors as a corrected leaf and is refused once they are used; any failure after
lineage writes rolls back every row. RED cases: bonus zero-cost lot, fundable rights, excess ratio
precision, inexact holding effect, lineage withheld by an unallocated disposal and by one inexact
lot, later event, replay/conflict, concurrent split and allocation, late-failure rollback, correction
before and after use, member/cross-household and internal-function refusal, and no cash role or FX.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `scripts/hooks/db-tests.sh`; run `scripts/verify.sh` on
that same server, which tears it down.
Acceptance: an exact split preserves units, origin dates and total open basis; a lot-incomplete
split commits exact units and withholds only lot-dependent claims.

## Task 15.3 — bonus, rights and split owner API

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6o3-investment-unit-actions` / `develop` / `develop`.
**Depends:** Task 15.2. **Forecast:** 400–505 production, 720–900 tests. **Size:** above 1,000
lines because bonus, rights and split share one route pair, loader and live fixture.
**Files:** create `api/oneledger/investments/unit_lifecycle.py`,
`api/tests/test_investment_unit_lifecycle.py` and `api/tests/test_investment_unit_lifecycle_live.py`;
update `api/oneledger/investments/schemas.py`, `api/oneledger/investments/commands.py` and
`api/oneledger/routes/investment_events.py`; shared API/type/status paths.
**Contract/tests:** Owner-only `POST /investment-events/unit-actions` and
`POST /investment-events/{event_id}/unit-action-corrections` accept strict `bonus`, `rights` and
`split` bodies with exact decimal strings, canonical dates and an idempotency key; corrections add
`expectedCorrectionState` and a screened reason and keep the original kind. Bonus and rights call
the 23-argument append, with rights freezing its funded flow; split calls `append_investment_split`.
Reuse the cash-lifecycle idempotency, reporting-pair, frozen-flow, reason-screening and translation
helpers. Responses return the event, exact `unitRatio`, holding units, lots created by the event and
open review reasons, so a withheld split shows `incomplete_lot_allocation` and no lots; a replacement
resolves the corrected split's review as `event_corrected`. `investment_split_units_are_exact`,
`investment_unit_event_order` and `investment_lineage_is_latest` map to fixed 422 reasons
`inexact_split` and `event_order_conflict`; identity conflicts map to 409 and foreign references to
404. RED cases: field matrix and precision, bonus zero-cost lot, rights funding eligibility, exact
and withheld split, inexact split, event order, identical retry 200, conflicting key 409, stale
`expectedCorrectionState` 409, concurrent identical retries, concurrent split and sale, late
database-failure rollback, member/cross-household, Unicode reason leakage and unchanged reportable
flows.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `(cd api && uv run pytest -q
tests/test_investment_unit_lifecycle.py tests/test_investment_unit_lifecycle_live.py)` and the shared
types command; run `scripts/verify.sh` on that same server, which tears it down.
Acceptance: the owner records and corrects bonuses, rights and splits exactly and idempotently
without manufacturing cash, basis or contributions.

## Task 15.4 — paired switch and platform-transfer operation

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6o4-investment-transfer-operation` / `develop` / `develop`.
**Depends:** Task 15.2. **Forecast:** 295–355 production, 705–905 tests. **Size:** above 1,000
lines because both legs, lineage writes and the refusal matrix form one atomic operation.
**Files:** create `db/migrations/V42__investment_transfer_operation.sql`,
`db/tests/0039_investment_transfer_operation.sql` and
`api/tests/test_investment_transfer_operation_live.py`; update the `0027` function teardown; shared
schema/data-model/testing/status paths.
**Contract/tests:** Owner-only `append_investment_unit_transfer(p_out_kind investment_event_kind,
p_event_date DATE, p_out_account_id UUID, p_out_security_id UUID, p_out_units NUMERIC,
p_out_price NUMERIC, p_out_gross_amount NUMERIC, p_in_account_id UUID, p_in_security_id UUID,
p_in_units NUMERIC, p_in_price NUMERIC, p_in_gross_amount NUMERIC, p_currency CHAR(3),
p_lot_ids UUID[], p_lot_units NUMERIC[], p_source_kind investment_event_source,
p_source_contract TEXT, p_out_source_event_id TEXT, p_out_dedupe_version TEXT,
p_out_dedupe_fingerprint TEXT, p_in_source_event_id TEXT, p_in_dedupe_version TEXT,
p_in_dedupe_fingerprint TEXT, p_corrects_out_transaction_id UUID, p_correction_reason TEXT)
RETURNS TABLE (out_event_id UUID, in_event_id UUID)` writes both legs or raises with nothing
written; the in leg names its out counterpart. Units, price, gross and selected units pass scale
checks before any write. Switch and systematic legs share an account, differ in security and have
equal gross; platform legs share a security, differ in account and have opposite units; every
account and security is in the owner's household. After event advisory locks, accounts, holdings,
lots and reviews lock in UUID order, so opposite-direction transfers cannot deadlock. Selected lots
must be open source lots summing exactly to the out units. A switch without selection commits with
one out-leg `incomplete_lot_allocation` review, resolved as `event_corrected` by correcting the pair
while its switch-in lot is unused; with selection it calls `allocate_investment_lots`. A platform
transfer raises `investment_transfer_lineage_unavailable` unless both holdings have available
lineage and `investment_lineage_is_latest` when either holding has a later unit- or lot-affecting
event. It consumes selected lots, or every open lot for a full transfer from a lot-complete source,
and creates destination successors under the basis rule. Without selection, a partial transfer
raises `investment_transfer_requires_lot_selection` and a full transfer from a lot-incomplete source
raises `investment_transfer_source_lots_incomplete`. Corrections replace both legs; transfer
out-legs share the split consumption exemption. A pair is used once its switch-out leg has an
allocation or any in-leg lot is allocated or consumed by an active event. A trigger refuses V39
decisions for switch, systematic and platform out-legs. RED cases: precision, second-leg rollback,
value/unit/account mismatch, foreign in-leg account, replay and per-leg conflict, full-transfer
dates and cost, partial selection with the rounding residual kept by the source, each refusal
writing nothing, concurrent opposite-direction transfers, one-leg or used correction, direct DML on
successor lots, member/cross-household, and no cash role, contribution or realised decision.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `scripts/hooks/db-tests.sh` and
`(cd api && uv run pytest -q tests/test_investment_transfer_operation_live.py)`; run
`scripts/verify.sh` on that same server, which tears it down.
Acceptance: one database call moves units and lots across both legs atomically, preserves every
transferred lot's origin date and exact total open basis, and refuses each ambiguity distinctly.

## Task 15.5 — switch and transfer owner API

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p1-6o5-investment-transfers` / `develop` / `develop`.
**Depends:** Tasks 15.3 and 15.4. **Forecast:** 325–405 production, 700–880 tests. **Size:** above
1,000 lines because both leg shapes and every refusal share one route pair and live fixture.
**Files:** update `api/oneledger/investments/unit_lifecycle.py`,
`api/oneledger/investments/schemas.py`, `api/oneledger/investments/commands.py`,
`api/oneledger/routes/investment_events.py`, `api/tests/test_investment_unit_lifecycle.py` and
`api/tests/test_investment_unit_lifecycle_live.py`; shared API/type/status paths.
**Contract/tests:** Owner-only `POST /investment-events/unit-transfers` and
`POST /investment-events/{event_id}/unit-transfer-corrections` accept one kind family, event date,
strict out and in legs, optional `lotSelections` of `{acquisitionLotId, units}` and one idempotency
key from which both leg identities derive deterministically; corrections target the out leg with
`expectedCorrectionState` and a screened reason. Each request calls
`append_investment_unit_transfer` once. Responses return both legs, holding units, switch or
successor lots and review reasons. V42 refusals write nothing and map to fixed 422 reasons
`lot_selection_required`, `source_lots_incomplete`, `lot_lineage_unavailable` and
`event_order_conflict`; review creation for an imported ambiguous transfer belongs to Task 16. RED
cases: leg matrix and pairing, exact strings, switch with and without selection, full and partial
platform transfer, each refusal reason, retry 200, conflict 409, stale `expectedCorrectionState` 409,
one-leg correction, Unicode reason leakage, concurrent retries and opposite-direction transfers,
late database-failure rollback, member/cross-household and unchanged reportable flows.
**RED/GREEN:** start one server with `eval "$(scripts/test-db.sh up)"`; in every fresh shell use
`eval "$(scripts/test-db.sh env)"`, then run `(cd api && uv run pytest -q
tests/test_investment_unit_lifecycle.py tests/test_investment_unit_lifecycle_live.py)` and the shared
types command; run `scripts/verify.sh` on that same server, which tears it down.
Acceptance: the owner records and corrects switches and platform transfers through authenticated
operations, and P12 transfer/split meaning is preserved before later distribution calculations.

## Task 16 — review decisions and atomic import commit

**Branch/base/target:** `feature/p1-6p-investment-commit` / `develop` / `develop`.
**Files:** `api/oneledger/investments/commit.py`, `api/oneledger/routes/investment_imports.py`,
`api/tests/test_investment_commit.py`, `api/tests/test_investment_commit_live.py`,
`db/migrations/V43__investment_import_lot_review.sql`,
`db/tests/0040_investment_import_lot_review.sql`; shared API/type/ingestion/schema/status paths.
Reforecast at dispatch; the migration was added by the Task 15 plan review, and its version must be
rechecked if Task 19 lands a migration first.
**Contract/tests:** Expected-state decisions; re-resolve/reconcile/commit per group; manual fallback
calls existing commands; summary creates no event. A refused ambiguous or lot-incomplete transfer
rolls back every financial write of its group; afterwards, in the same owner transaction, one open
`incomplete_lot_allocation` review is recorded with `import_id`, `group_id` and the out-leg
`draft_id`, reusing an existing open review on retry. V43 extends `apply_investment_review_block` so
that review moves its group to `blocked` (held) even when the group is already `ready`, and extends
the group-transition guard so, while that open lot review exists, its group can leave `blocked` only
directly to `committed` or `cancelled`. With lot evidence, one transaction writes both legs and the
complete lot allocation, resolves the review as `lot_allocated` referencing that in-transaction
out-leg, then moves the group from `blocked` to `committed`. V43 adds resolution kind
`import_cancelled` (referenced only from function bodies in that migration), keeps V39's
event-scoped resolution branches unchanged, and
refuses any other resolution of a draft-scoped lot review; `cancel_staged_import` resolves it as
`import_cancelled` referencing the import in the cancellation transaction. Matrix, concurrency,
rollback, monthly overlap, review closure, cancel, held group starting `ready`, repeated refusal,
evidence commit and retry after late failure, ambiguous-transfer and unknown-action dependent-return
RED cases.
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
