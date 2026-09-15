# UI completion implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Close the P1 gaps found by the 15 September 2026 UI coverage audit of Phases 0.x–2.4, so
every important owner task for delivered outcomes can be completed in the web application.

**Architecture:** Branch 1 fixes wiring and adds small holding-page actions over existing routes.
Branch 2 adds thin owner routes over existing database operations (`valuation_snapshot` with
frozen FX, `transition_account_valuation_mode`) and investment correction and entry forms over
the existing investment event routes.

**Stack:** Existing Python/FastAPI and Next.js/React; no migration or dependency. Authority: the
delivered specifications for Phases 1.6, 2.2 and 2.3, ADR-0006 (manual valuation snapshots) and
ADR-0002 (frozen dual-currency amounts). This group is planned together and reviewed once.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; each
  branch starts from current `develop` and opens one PR to `develop`.
- Money stays `NUMERIC`/`Decimal`/strings; TypeScript formats only. Routes use `require_owner` and
  household filters. Tests cover major behaviour, including one Workspace-level test per changed
  flow so parent re-rendering is exercised.
- After affected tests: one `/oneledger-code-review`, one repair, `scripts/verify.sh` once, push,
  PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | suggested rules, holding prices, funding links and net-worth reasons | `fix/ui-completion-core` | 700 | 600 | Phase 2.4 |
| 2 | asset valuations and investment corrections | `feature/ui-valuations-and-corrections` | 1,200 | 900 | 1 |

## Task 1 — suggested rules, holding prices, funding links and net-worth reasons

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `fix/ui-completion-core` /
`develop` / `develop`.
**Depends:** Phase 2.4 merged. **Forecast:** 500–800 production, 400–700 tests.
**Files:** `web/app/workspace.tsx`, `web/app/settings/rules-manager.tsx`,
`web/app/settings/data-quality.tsx`, `web/app/investments/holding-screen.tsx`,
`web/app/investments/funding-panel.tsx`, `web/app/overview/overview-screen.tsx` and their tests;
`api/oneledger/investments/portfolio.py` and `api/oneledger/routes/portfolio.py` only if the
holding detail lacks what the funding panel needs; generated types; progress.
**Contract:**

- Workspace passes one stable `request` to sections (or sections keep the latest `request` in a ref
  as `overview-screen.tsx` does), so data loads run once per mount and on explicit refresh, never on
  unrelated parent renders.
- A suggested rule created from a card keeps its card mounted with "Check past rows" and "Apply N
  changes" after the suggestions list reloads.
- The holding page offers **Record a dated price** (date and price strings) through
  `POST /investment-catalogue/securities/{securityId}/manual-prices`, then reloads the holding.
- The holding page lists purchase events without a complete funding link and opens the existing
  funding proposal, confirmation and unlink flow for any of them; the holding detail exposes event
  ids, kinds, dates, net amounts and funding completeness as strings when missing.
- The overview shows each `netWorth.investmentCoverage` item that makes a total unavailable or stale
  (missing or future-only price, missing or carried snapshot, missing FX) with a link to the holding
  or account.

**Tests:** Workspace-level Settings test: create a suggestion, check past rows, apply; no reload
loop on unrelated renders. Holding page: record price posts exact strings and reloads; unfunded
purchase opens funding and confirm posts. Overview: coverage reasons and links render from server
strings. API live test only if the holding detail contract changes.
**Commands:** `(cd web && npx vitest run && npm run typecheck && npm run lint)`; if API changes,
`(cd api && uv run pytest -q tests/test_holding_detail_live.py tests/test_investment_funding_live.py)`;
`scripts/verify.sh`.
**Acceptance:** each flow above works from the application's navigation.

## Task 2 — asset valuations and investment corrections

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/ui-valuations-and-corrections` / `develop` / `develop`.
**Depends:** Task 1. **Forecast:** 1,000–1,400 production, 700–1,000 tests.
**Files:** create `api/oneledger/routes/valuations.py`, `api/tests/test_valuations_live.py`,
`web/app/accounts/valuation-panel.tsx`, `web/app/investments/event-correction-form.tsx`,
`web/app/investments/investment-event-form.tsx` and tests; update `api/oneledger/main.py`,
`web/app/accounts/accounts-screen.tsx`, `web/app/investments/holding-screen.tsx`,
`web/app/investments/portfolio-screen.tsx`; generated types; API contracts, data model and progress.
**Contract:**

- `GET /accounts/{accountId}/valuations` lists the account's valuation periods and snapshots
  (newest first, decimal strings). `POST /accounts/{accountId}/valuation-snapshots` with
  `asOfDate`, `valueNative`, `method`, `confidence`, optional `sourceNote` and an idempotency key
  inserts a household-scoped snapshot with frozen FX derived exactly as manual postings do
  (ADR-0002); database constraints (snapshot mode on that date, currency, lifetime, one per date)
  map to 422/409. `POST /accounts/{accountId}/valuation-mode` with `valuationMode` and
  `effectiveFrom` calls `transition_account_valuation_mode` and maps its refusals to 422.
- Accounts shows each account's current valuation mode, and for snapshot-mode accounts the latest
  snapshot and an **Add valuation** form; a mode change is offered with an explicit effective date.
- The holding page offers **Correct** on each active event, prefilled from the event and posting to
  the correction route that matches its kind (`/investment-events/{id}/corrections`,
  `/cash-lifecycle-corrections`, `/unit-action-corrections`) with `expectedCorrectionState` and a
  reason. The portfolio and holding pages offer **Record an event** for sales, redemptions,
  distributions, interest, charges, tax (`/investment-events/cash-lifecycle`) and bonus, rights,
  split, switch and transfers (`/investment-events/unit-actions`), with exact decimal strings and
  server refusals shown.

**Tests:** live valuation snapshot create, replay, wrong-mode 422, duplicate date 409, foreign 404,
frozen FX exact, and net worth becoming available once a snapshot exists; mode transition success
and refusals. Web: add valuation, mode change confirmation, event correction posts to the right
route, record-event forms send exact strings.
**Commands:** `(cd api && uv run pytest -q tests/test_valuations_live.py tests/test_dashboard_live.py
tests/test_investment_events_live.py)`; shared types command; `(cd web && npx vitest run &&
npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** a retirement or property account can be valued so net worth is stated, and a
mistyped or missing investment event can be corrected or recorded in the application.
