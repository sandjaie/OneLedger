# Phase 2.3 classification learning implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Rule analytics, correction-driven suggestions with accurate affected counts, and an
impact-ordered review queue, without silent retroactive change.

**Architecture:** Read-only analytics and suggestion routes over the ledger posting view reuse the
existing matcher, rule preview and classification decision runs; the web extends Settings › Rules
and the review screen.

**Stack:** Existing Python/FastAPI and Next.js/React; no migration or dependency. See the
[Phase 2.3 specification](../specs/2026-09-14-phase-2-3-classification-learning.md).

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; branch
  from current `develop`, one PR to `develop` titled `[P2.3] …`. Reviewed once with Phases 2.2 and
  2.4.
- Tests cover major behaviour; routes use `require_owner` and household filters; ledger reads use
  the posting and reportable views; description text shown only through the ledger redactor.
- After affected tests: one `/oneledger-code-review`, one repair, `scripts/verify.sh` once, push,
  PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | rule analytics, suggestions and impact review order | `feature/p2-3-classification-learning` | 900 | 700 | Phase 2.2 |

## Task 1 — rule analytics, suggestions and impact review order

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p2-3-classification-learning` / `develop` / `develop`.
**Depends:** Phase 2.2 merged. **Forecast:** 700–1,000 production, 500–800 tests.
**Files:** update `api/oneledger/ingest/classify.py` (Python twin of the pattern proposal with shared
test vectors), `api/oneledger/routes/rules.py` (analytics, suggestions),
`api/oneledger/routes/classification_review.py` (impact sort), `web/app/settings/rules-manager.tsx`
and its test, `web/app/review/classification-review-screen.tsx`;
create `api/tests/test_rule_analytics_live.py`; generated types; the specification's documents.
**Contract:** the specification's routes and fields exactly; `disagreements` reuse `_first_matching`
and `affected`/`unclassified` reuse the rule preview's matching function over the same row set rather
than new SQL matching; suggestions never create or apply rules; past-row application uses
`/classification-runs` in chunks of 200.
**Tests:** coverage figures; hits, last hit and disagreements; suggestions from three corrections,
none from two, none duplicating an active rule, affected equal to preview; impact sort; foreign
isolation; no row changes from analytics, suggestions or rule creation without apply.
**Commands:** `(cd api && uv run pytest -q tests/test_rule_analytics_live.py tests/test_rules_live.py
tests/test_classification_review_live.py tests/test_health.py)`; shared types command;
`(cd web && npx vitest run app/settings app/transactions app/review && npm run typecheck)`;
`scripts/verify.sh`.
**Acceptance:** the specification's acceptance through the application.
