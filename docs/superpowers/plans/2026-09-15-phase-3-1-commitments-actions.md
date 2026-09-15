# Phase 3.1 commitments and actions implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Commitments with certainty, a next-occurrence lifecycle and settlement by linked postings;
card statement dues planned as commitments; one Needs attention list with a single action lifecycle.

**Architecture:** V48 extends `commitment` and adds `commitment_resolution`; V49 adds the card
source, `action_item` and `action_mark`. Occurrence math stays pure in `forecast.py`; routes lock the
household and filter by it; the action list is derived on read. See the
[Phase 3.1 specification](../specs/2026-09-15-phase-3-1-commitments-actions.md).

**Stack:** Existing Flyway SQL, Python/FastAPI/psycopg and Next.js/React; no new dependency.

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; branch
  from current `develop` (rebase on `origin/develop` before push); one PR per branch to `develop`
  titled `[P3.1] …`. Planned and reviewed once with Phase 3.2.
- Money is `NUMERIC`/`Decimal`/strings; no TypeScript money arithmetic (the web shows server
  `remainingNext`, `paidTowardNext`, amounts). Every route: `require_owner`, household lock on
  writes, explicit `owner.household_id` filters. V43–V47 are not edited.
- Synthetic data only in migrations, tests and fixtures; descriptions only through
  `_preview_redactor(None)`.
- The implementer commits early and locally, runs affected tests, and does not push or run
  `scripts/verify.sh`. The orchestrator runs one `/oneledger-code-review`, applies repairs (a
  confirmation pass only for repaired money, authorization or migration findings), then
  `scripts/verify.sh` once, push, PR, merge.
- Shared commands: `eval "$(scripts/test-db.sh up)"`; `scripts/hooks/db-tests.sh`; types
  `(cd web && npm run api:types)`; web `(cd web && npx vitest run <files> && npm run typecheck && npm run lint)`.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | commitment lifecycle and settlement | `feature/p3-1-commitment-lifecycle` | 1,250–1,450 | 1,300–1,550 | Phase 2.4 |
| 2 | card dues and the action list | `feature/p3-1-actions` | 1,100–1,350 | 1,000–1,250 | Task 1 |

## Task 1 — commitment lifecycle and settlement

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p3-1-commitment-lifecycle` / `develop` / `develop`.
**Depends:** Phase 2.4 merged (develop `471473b` or newer).
**Owner sees and can do:** on Plans › Commitments, set certainty and next due date; see each
commitment's status, paid toward and remaining; **Settle** an occurrence by linking a candidate
posting as a full or part payment, **Mark done**, or **Dismiss** with a reason; open **History** and
**Undo** the latest resolution; watch a settled occurrence leave the forecast and a part payment
leave its remainder.
**Files:** create `db/migrations/V48__commitment_lifecycle.sql`,
`db/tests/0045_commitment_lifecycle.sql`, `db/tests/0045_commitment_lifecycle_upgrade.sh` (modelled
on `db/tests/0030_investment_allocations_upgrade.sh`), `api/oneledger/routes/commitment_resolutions.py`,
`api/tests/test_commitment_resolutions_live.py`, `web/app/plans/settlement-panel.tsx`,
`web/app/plans/__tests__/settlement-panel.test.tsx`; update `api/oneledger/forecast.py`,
`api/oneledger/routes/plans.py`, `api/oneledger/main.py` (register the router),
`api/tests/test_forecast.py`, `api/tests/test_plans_live.py`, `web/app/plans/plans-screen.tsx`,
`web/app/plans/__tests__/plans-screen.test.tsx`, generated `web/types/api/`; the specification's
documents for V48 and the Task 1 routes.
**Contract:**

- V48 is the specification's DDL, backfill, `assert_commitment_payment()` (raising with the message
  prefix `commitment_payment_is_eligible`), `preserve_commitment_resolution()`, RLS and revokes,
  exactly.
- `forecast.py` adds pure `is_occurrence(cadence, first, end, candidate) -> bool` and
  `next_occurrence(cadence, first, end, due) -> date | None`, built on the existing `_add_months`;
  `occurrence_dates` and `project_balances` keep their signatures.
- `routes/plans.py`: create/update/list follow "Commitments (Task 1 changes)"; a shared helper
  computes `status`, `partlyPaid`, `paidTowardNext` and `remainingNext` from one query over
  `commitment_resolution` joined to `transaction` (reused by the resolutions router and, in Task 2,
  the action list). `GET /forecast` applies the specification's next-occurrence and remainder rule
  and adds `certainty` to occurrences; it stays `READ ONLY`.
- `routes/commitment_resolutions.py`: the four routes, create order, replay and undo rules, and the
  error sentences exactly. Eligibility is decided by the V48 trigger; the route maps its message to
  422 and `uq_commitment_resolution_transaction` to 409. Candidates are one read query reusing the
  trigger's predicate in SQL (keep both in sync with a comment naming the other).
- The default next due date reads the account's `current_balance_as_of` (spec "Occurrence rules");
  the V48 backfill applies the same rule in SQL.
- Web: the specification's Task 1 workflow. The settlement panel sends `transactionId`, `dueDate`
  and `closes`; Mark done and Dismiss send their kinds; Undo appears only where the server's
  `undoable` is true; Partly paid shows from `partlyPaid`; every write refetches commitments and the
  forecast.

**Tests:**

- SQL `0045`: the specification's V48 list (isolation on `commitment_resolution`; scope keys; shape;
  closing and transaction uniqueness; each eligibility refusal and a transfer leg accepted; update
  refused). Upgrade script: migrate to V47, insert synthetic once, month-end monthly, 29 February
  annual and ended commitments on accounts with past balance dates, apply V48, assert each
  `next_due_date`.
- Unit `test_forecast.py`: `is_occurrence`/`next_occurrence` for 31 January monthly, 29 February
  annual, end dates inclusive, once.
- Live `test_commitment_resolutions_live.py`: full payment advances `nextDueDate`; part payment then
  closing payment with exact `paidTowardNext`/`remainingNext`; completed and dismissed close; a
  once commitment finishes; replays 200; stale `dueDate`, archived, finished, already resolved and
  reused posting 409; wrong sign/account/currency/excluded 422 with no row; undo of a part payment,
  of the latest closing row, refusal of an older one and of a closing row while the next occurrence
  has a payment, with `undoable` agreeing each time; overpayment leaves `remainingNext` `"0.0000"`; candidates window edges and read-only; member 403 and foreign household 404 on
  every route.
- Live `test_plans_live.py` (existing forecast tests keep their expected balances; send an explicit
  `nextDueDate` only where the new default differs from what a test intends): certainty and `nextDueDate` round trip; default next due date for a
  past-start monthly commitment; schedule and sign edits refused once resolved; the forecast omits a settled
  occurrence and projects a part-paid remainder exactly; `GET /forecast` still changes no row.
- Web: status and remainder strings rendered from the server; panel request bodies; undo gating.

**Commands:** shared DB up and `scripts/hooks/db-tests.sh`;
`(cd api && uv run pytest -q tests/test_forecast.py tests/test_plans_live.py tests/test_commitment_resolutions_live.py)`;
types; web `app/plans`; `scripts/check-diff-size.sh`.
**Acceptance:** the specification's first three acceptance sentences through `/plans` with
synthetic data.

## Task 2 — card dues and the action list

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p3-1-actions` /
`develop` / `develop`.
**Depends:** Task 1 merged.
**Owner sees and can do:** Plans opens with **Needs attention**: overdue and due commitments to
settle, mark done, dismiss or snooze; card statements with an amount due and no plan, which they
**Plan payment** from (choosing the paying account and amount), dismiss or snooze; follow-ups they
add, complete, dismiss, snooze and reopen. Commitment rows name their card-statement source.
Overview shows the open count and first five items.
**Files:** create `db/migrations/V49__card_source_and_actions.sql`,
`db/tests/0046_card_source_and_actions.sql`, `api/oneledger/routes/actions.py`,
`api/tests/test_actions_live.py`, `web/app/plans/needs-attention.tsx`,
`web/app/plans/__tests__/needs-attention.test.tsx`; update `api/oneledger/routes/plans.py`
(card source on create, `source*` row fields), `api/oneledger/main.py`, `api/tests/test_plans_live.py`,
`web/app/plans/plans-screen.tsx` (form pre-fill entry point, source label),
`web/app/overview/overview-screen.tsx`, `web/app/overview/__tests__/` and
`web/app/__tests__/workspace.test.tsx` where request sequences are pinned, generated
`web/types/api/`; the specification's documents for V49 and the Task 2 routes.
**Contract:**

- V49 is the specification's DDL, partial unique index, RLS and revokes, exactly.
- `routes/plans.py` card-source validation order and sentences as specified, on create and again
  on update of a card-sourced commitment; replay compares `sourceCardStatementId`.
- `routes/actions.py`: the seven routes. `GET /actions` builds the three item kinds from one
  function `action_list(cur, household_id, as_of, *, apply_marks: bool)` (Phase 3.2 reuses it for
  unplanned card dues and counts), `SET TRANSACTION READ ONLY` first. Snooze `PUT` validates the key
  shape and the one-of `until`/`reason` body (reason on card keys only, 422 otherwise) in Python
  before touching the database and 404s unless the named follow-up, commitment (with
  that date as its current `next_due_date`) or card statement exists in the household. Follow-up
  create replays by idempotency key with field equality.
- A commitment item's Mark done and Dismiss call the Task 1 resolutions route; no action route
  writes `commitment_resolution`.
- Web: the specification's Task 2 workflow; the overview fetch is added without reordering existing
  pinned requests (append after them) and its tests are updated.

**Tests:**

- SQL `0046`: isolation on `action_item` and `action_mark`; cross-household account and
  card-statement references refused; the card-source check and one-open-plan index (an archived plan
  allows another); title, reason and mark shape checks.
- Live `test_actions_live.py`: commitment items at `asOf + 7` in and `+ 8` out, overdue ordering;
  card item membership (paid-in-full, zero balance, older cycle, due more than 7 days past, planned,
  dismissed all absent; archived plan present again); snooze moves an item to `snoozed[]` and back
  after its date; snooze 404 for a foreign or stale key, 422 for a bad date or a reason on a non-card key; follow-up create,
  replay, conflict, complete, dismiss, reopen and the 409 transitions; `GET /actions` changes no
  row; member 403 and foreign 404 on every write.
- Live `test_plans_live.py`: every card-source refusal on create and update and the 409 second plan; planning then
  settling via the bank-side transfer leg clears the card item and the commitment item.
- Web: item controls post the right routes and refetch; Plan payment pre-fill and account filter;
  overview count and links.

**Commands:** shared DB up and `scripts/hooks/db-tests.sh`;
`(cd api && uv run pytest -q tests/test_actions_live.py tests/test_plans_live.py tests/test_commitment_resolutions_live.py)`;
types; web `app/plans app/overview app/__tests__/workspace.test.tsx`; `scripts/check-diff-size.sh`.
**Acceptance:** the specification's acceptance in full through `/plans` and `/overview` with
synthetic data.
