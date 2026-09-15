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
| 2a | asset valuations and valuation mode | `feature/ui-asset-valuations` | 600 | 500 | 1 |
| 2b | investment corrections and event entry | `feature/ui-investment-corrections` | 750 | 500 | 2a |

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

## Task 2a — asset valuations and valuation mode

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/ui-asset-valuations` / `develop` / `develop`.
**Depends:** Task 1. **Forecast:** 500–700 production, 400–600 tests. Split from the original Task 2
by the group plan review.
**Files:** create `api/oneledger/routes/valuations.py`, `api/tests/test_valuations_live.py`,
`web/app/accounts/valuation-panel.tsx` and its test; update `api/oneledger/main.py`,
`web/app/accounts/accounts-screen.tsx`; generated types; API contracts, register and progress.
**Contract:**

- `GET /accounts/{accountId}/valuations` lists the account's valuation periods and snapshots newest
  first as decimal strings, filtered explicitly by `owner.household_id` (the API connects as the
  table owner, so RLS does not protect these routes).
- `POST /accounts/{accountId}/valuation-snapshots` takes `asOfDate` (not in the future),
  `valueNative` (`^\d{1,16}(\.\d{1,4})?$`), `method` (`owner_entered` or `statement` only),
  `confidence` and an optional `sourceNote` screened like correction reasons (`CONTEXTUAL_SECRET`
  and `counterparty_text_has_private_identifier`, 422 on a hit). Frozen FX is
  `money._frozen(cur, asOfDate, currency, base, secondary)` plus `translate`, with `fx_rate_date` the
  date `_frozen` returns; a missing rate is its existing 422. The replay identity is account plus
  `asOfDate`: an identical value, method, confidence and note returns 200 with the stored row and its
  stored frozen amounts; any difference returns 409. A foreign account is 404; the snapshot-mode,
  currency and lifetime constraints are 422.
- Snapshots are immutable (`preserve_valuation_snapshot`): there is no correction route. The web shows
  a confirmation with the exact native value, date, FX rate date and base value before posting, and
  says a later snapshot supersedes it only from its own date. Same-date correction is recorded in the
  project register as deferred work needing a migration.
- `POST /accounts/{accountId}/valuation-mode` takes `valuationMode` and `effectiveFrom`. If the open
  period already has that mode and start, it returns 200 without calling
  `transition_account_valuation_mode`; any other same-mode request is 409. Refusals map 42501 → 404,
  each 23514 case (before the balance anchor, liability account, not after the current period start,
  would strand snapshots, would strand investment events) → 422, and 55000 → 409. A backdated change
  alters past net worth: the web tells the owner to refresh wealth history.
- Accounts shows each account's current valuation mode; asset accounts offer a mode change with an
  effective date after the current period's start; snapshot-mode accounts show the latest snapshot
  and **Add valuation**, and one with no snapshot links straight to it.

**Tests:** live snapshot create, identical replay 200, different value 409, wrong mode 422, future date
422, excess precision 422, screened note 422, foreign 404, member refusal, frozen FX exact, and
dashboard net worth becoming available once a snapshot exists; mode transition success, identical
retry 200, each refusal mapping. Web: add valuation with confirmation, mode change.
**Commands:** `(cd api && uv run pytest -q tests/test_valuations_live.py tests/test_dashboard_live.py)`;
shared types command; `(cd web && npx vitest run && npm run typecheck && npm run lint)`;
`scripts/verify.sh`.
**Acceptance:** a retirement or property account can be valued so net worth is stated.

## Task 2b — investment corrections and event entry

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/ui-investment-corrections` / `develop` / `develop`.
**Depends:** Task 2a (both change screens around holdings). **Forecast:** 600–850 production,
400–600 tests.
**Files:** create `web/app/investments/event-correction-form.tsx`,
`web/app/investments/investment-event-form.tsx` and tests; update
`web/app/investments/holding-screen.tsx`, `web/app/investments/portfolio-screen.tsx`,
`api/oneledger/investments/portfolio.py`, `api/tests/test_holding_detail_live.py`; generated types;
API contracts and progress.
**Contract:**

- `HoldingEventRow` adds, for paired legs, the counterpart leg's account, security, units, price and
  gross amount as strings.
- **Correct** is offered only on `correctionState: "active"` events, sending
  `expectedCorrectionState: "uncorrected"` and a reason. Acquisitions use
  `/investment-events/{id}/corrections`; cash lifecycle kinds use
  `/{id}/cash-lifecycle-corrections`; bonus, rights and split use `/{id}/unit-action-corrections`;
  `switch_*`, `systematic_transfer_*` and `platform_transfer_*` legs map to the pair kinds `switch`,
  `systematic_transfer` and `platform_transfer`, are corrected through the out leg's id with the full
  pair body. A funded event says to unlink its funding first (the server refuses otherwise).
- **Record an event** on the portfolio and holding pages covers every cash lifecycle kind (sale,
  redemption, maturity, cash distribution, interest, reinvested distribution, charge, withheld tax)
  and unit action kind (bonus, rights, split, switch, systematic transfer, platform transfer); each
  form shows only the fields its kind allows and sends exact decimal strings.
- Each draft creates one idempotency key and reuses it when the owner resubmits after an unclear
  outcome.

**Tests:** live holding detail includes counterpart leg fields; web: correction posts to the route for
its kind with the right body, paired correction uses the out leg, record-event forms send exact
strings, a resubmit reuses the idempotency key.
**Commands:** `(cd api && uv run pytest -q tests/test_holding_detail_live.py
tests/test_investment_unit_actions_live.py tests/test_investment_cash_lifecycle_live.py)`; shared types
command; `(cd web && npx vitest run && npm run typecheck && npm run lint)`; `scripts/verify.sh`.
**Acceptance:** a mistyped or missing investment event can be corrected or recorded in the application.
