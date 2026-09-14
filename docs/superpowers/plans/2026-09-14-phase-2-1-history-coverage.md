# Phase 2.1 history coverage implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** A month-by-month coverage grid from the history anchor for every statement-sourced account
and payslips, with explicit owner-acknowledged gaps.

**Architecture:** Coverage is computed on read from committed import batch periods and payslips; a
small acknowledgement table records owner-declared gaps; the doctor summarises gaps.

**Stack:** Existing PostgreSQL/Flyway, Python/FastAPI and Next.js/React; no new dependency. See the
[Phase 2.1 specification](../specs/2026-09-14-phase-2-1-history-coverage.md).

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees. The
  branch starts from current `develop` and opens one PR to `develop` titled `[P2.1] …`; this plan
  and its specification land with it.
- Tests cover major behaviour: coverage statuses, acknowledgement writes, owner and household
  isolation. Obvious screen code needs none.
- Routes use `require_owner` and household filters; the new table has RLS and a household-scoped
  foreign key. Ledger reads use existing views or import batch facts, never real data in fixtures.
- One test server: `eval "$(scripts/test-db.sh up)"`, reused with `eval "$(scripts/test-db.sh env)"`.
  After affected tests: one `/oneledger-code-review`, one repair, `scripts/verify.sh` once, push,
  PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | history coverage grid and acknowledged gaps | `feature/p2-1-history-coverage` | 1,100 | 700 | Phase 1.8 |

## Task 1 — history coverage grid and acknowledged gaps

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p2-1-history-coverage` / `develop` / `develop`.
**Depends:** Phase 1.8 merged. **Forecast:** 900–1,200 production, 500–800 tests.
**Files:** create `db/migrations/V45__history_coverage.sql`, `db/tests/0042_history_coverage.sql`,
`api/oneledger/coverage.py`, `api/oneledger/routes/coverage.py`, `api/tests/test_coverage.py`,
`api/tests/test_coverage_live.py`, `web/app/coverage/page.tsx`,
`web/app/coverage/coverage-screen.tsx` and its test; update `api/oneledger/main.py`,
`api/oneledger/routes/doctor.py`, `web/app/workspace.tsx` and the settings data-quality link;
generated types; the specification's documents.
**Contract:**

- V45 exactly as specified.
- `coverage.py` holds pure functions over dates and period intervals:
  `month_statuses(months: Sequence[date], life_start: date, life_end: date,
  periods: Sequence[tuple[date, date]], acknowledged: Mapping[date, str], as_of: date) ->
  list[str]` implementing the first-match status order; `HISTORY_ANCHOR = date(2024, 1, 1)`
  cites ADR-0011.
- `routes/coverage.py` serves the three routes, reading completed batches with non-null periods and
  active payslips in one query each, household-scoped.
- The doctor adds the Coverage section.
- The web grid follows the specification with an accessible table and a status legend.

**Tests:** unit status order and edge cases (split month, partial, outside life, current month);
live grid for a synthetic account with batches covering the listed cases, payslip row, anchor after
history, acknowledgement upsert/delete/covered refusal/foreign 404, doctor findings; SQL RLS; web
grid labels and acknowledge dialog.
**Commands:** `scripts/hooks/db-tests.sh`; `(cd api && uv run pytest -q tests/test_coverage.py
tests/test_coverage_live.py tests/test_doctor_live.py tests/test_health.py)`; shared types command;
`(cd web && npx vitest run app/coverage && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** the specification's acceptance, through authenticated operations.
