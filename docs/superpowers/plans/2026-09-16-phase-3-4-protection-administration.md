# Phase 3.4 protection and administration implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** A register of policies, deposits, claims, nominee records and administration deadlines
with linked documents and actions; premiums, maturity proceeds and reimbursements planned and
settled as commitments without creating income.

**Architecture:** V53 adds `register_record` and `register_document`; `action_list` gains record
items. Task 2 adds `POST /documents` for owner PDFs through the existing store, record document
links, and V54, which sources commitments from records and extends the payment eligibility trigger.
See the [Phase 3.4 specification](../specs/2026-09-16-phase-3-4-protection-administration.md).

**Stack:** Existing Flyway SQL, Python/FastAPI/psycopg (with the approved `python-multipart`) and
Next.js/React; no new dependency.

## Global execution contract

As the [Phase 3.3 plan](2026-09-16-phase-3-3-goals-emergency-reserve.md#global-execution-contract),
with PR titles `[P3.4] …`. Planned and reviewed once with Phase 3.3. At the end of Task 2 the
orchestrator runs `/oneledger-code-hygiene` for the Phase 3.x group and delivers accepted findings
on one `chore/` branch.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | register and record actions | `feature/p3-4-register` | 1,150–1,500 | 1,200–1,600 | Phase 3.3 Task 2 |
| 2 | documents and commitments planned from records | `feature/p3-4-register-commitments` | 1,150–1,400 | 1,400–1,700 | Task 1 |

## Task 1 — register and record actions

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` / `feature/p3-4-register` /
`develop` / `develop`.
**Depends:** Phase 3.3 Task 2 merged.
**Owner sees and can do:** on Plans › Register, add, edit and archive policies, deposits, claims,
nominee records and administration deadlines with only the fields each kind uses; mark a record
reviewed, record a renewal's next
date, and complete or reopen a claim or deadline. Needs attention lists renewals and maturities due
within 30 days, deadlines within 14 days and records unreviewed for a year, with the matching
resolve control and snooze.
**Files:** create `db/migrations/V53__register.sql`, `db/tests/0050_register.sql`,
`api/oneledger/routes/register.py`, `api/tests/test_register_live.py`, `web/app/plans/register.tsx`,
`web/app/plans/__tests__/register.test.tsx`; update `api/oneledger/routes/actions.py`,
`api/oneledger/main.py`, `api/tests/test_actions_live.py`, `web/app/plans/plans-screen.tsx`,
`web/app/plans/needs-attention.tsx`, generated `web/types/api/`; the specification's documents for
V53, the register routes and the record action reasons.
**Contract:**

- V53 is the specification's DDL, `preserve_archived_register_record()`, RLS and revokes, exactly.
- `routes/register.py`: the eight Task 1 routes; kind-forbidden fields refused in Pydantic before
  the database; deposit account type and household checks. Record readers that `action_list` needs
  live in a non-route module (e.g. `api/oneledger/register_records.py`) to avoid importing route
  modules from `routes/actions.py`.
- `routes/actions.py`: the four record item kinds with their windows; resolve controls map to the
  register routes; no action route writes a register row except through those routes.
- Web per the Task 1 workflow; Plans order appends Register after Emergency reserve.

**Tests:**

- SQL `0050`: isolation on `register_record`; foreign account scope key; each kind shape refusal;
  archived update and delete refused.
- Live `test_register_live.py`: create/update per kind, forbidden-field 422s, deposit account type
  422, reviewed idempotency, renewed (next date must be later), complete/reopen, archive terminal,
  isolation with real foreign rows, `GET` changes no row.
- Live `test_actions_live.py`: each record item at its window edge in and out, P10 (renewed clears
  the item and the next renewal returns inside its window, and a new policy yields one item, not a
  review item too), review due at 365/366 days after creation or last review, snooze accepted and
  dismissal refused.
- Web: kind-specific fields and request bodies; resolve controls.

**Commands:** shared; API `tests/test_register_live.py tests/test_actions_live.py
tests/test_health.py`; web `app/plans`.
**Acceptance:** the specification's renewal and review sentences through `/plans` with synthetic
data.

## Task 2 — documents and commitments planned from records

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p3-4-register-commitments` / `develop` / `develop`.
**Depends:** Task 1 merged.
**Owner sees and can do:** upload a PDF with a type and title, or pick an uploaded library document,
and attach or detach it on a record; see uploads in the Documents library by title; from a policy
**Plan premium**, from a deposit **Plan proceeds**, from a claim **Expect reimbursement**, each
opening the pre-filled commitment form; a record shows its planned commitment, and a claim its
expected, received and outstanding amounts with **Settle**; an income posting is refused for a claim
or deposit with a pointer to recategorise it.
**Files:** create `db/migrations/V54__register_commitments.sql`,
`db/tests/0051_register_commitments.sql`, `api/tests/test_register_commitments_live.py`,
`api/tests/test_document_upload_live.py`; update `api/oneledger/routes/documents.py` (upload route,
`importKind: "upload"`, `title`), `web/app/documents/documents-screen.tsx` and its tests,
`api/oneledger/routes/plans.py`, `api/oneledger/routes/commitment_resolutions.py` (candidates
predicate), `api/oneledger/routes/register.py` (`plannedCommitment`),
`api/oneledger/routes/actions.py` (maturity item omitted once planned),
`web/app/plans/register.tsx`, `web/app/plans/plans-screen.tsx`,
`web/app/plans/settlement-panel.tsx`, their tests, generated `web/types/api/`; the specification's
documents for V54, the upload and link routes, the commitment source, and the owner decision on
`importKind`/`title` in `api-contracts.html` and `security-and-privacy.html`.
**Contract:**

- `POST /documents` per "Document upload": content-type and PDF-header check and a bounded 20 MB
  read before storing, a fixed safe label per type, `ON CONFLICT (household_id, sha256)` reuse with
  the same-type 200 / other-type 409 rule, no filename retained. The two record document routes link
  only household documents of an upload type.
- V54 is the specification's DDL and the `CREATE OR REPLACE` of `assert_commitment_payment()`
  keeping every V48 condition, exactly; the candidates predicate mirrors it with its sync comment.
- `routes/plans.py`: `sourceRegisterRecordId` rules per kind on create and update, the two-source
  422, the one-open-commitment 409, `source: "register_record"` fields.
- `RegisterRow.plannedCommitment` reuses the Phase 3.1 progress helper; no second remaining-amount
  computation.
- Web per the Task 2 workflow.

**Tests:**

- SQL `0051`: `register_document` isolation and foreign document scope key; one source per
  commitment; one open commitment per record (an archived one allows
  another); the replaced trigger refusing `income` for claim- and deposit-sourced commitments,
  accepting it for a policy and an owner commitment, and every V48 refusal still holding.
- Live `test_document_upload_live.py`: a synthetic PDF stored and listed with `importKind: "upload"`
  and its title, same bytes and type 200 with the same id, same bytes as another type 409, non-PDF
  and oversized 422, statement types 422; link/unlink idempotency and a foreign document 404; a
  foreign household cannot see or link it.
- Live `test_register_commitments_live.py`: P09 exactly (1,000 claim, 400 refund part payment,
  outstanding `600.0000`, received `400.0000`, dashboard month income unchanged, income posting
  422); plan premium cadence/sign/currency rules; proceeds on the deposit account refused; maturity
  item omitted once planned and settled against a transfer leg; completed claim 409;
  nominee/administration 422; isolation with real foreign rows.
- Web: plan-from-record pre-fill and account filter; claim expected/received/outstanding from server
  strings; income-refusal hint in the settlement panel.

**Commands:** shared (DB tests included); API `tests/test_document_upload_live.py
tests/test_documents_live.py tests/test_register_commitments_live.py
tests/test_commitment_resolutions_live.py tests/test_plans_live.py tests/test_register_live.py
tests/test_actions_live.py tests/test_dashboard_live.py tests/test_health.py`; web `app/plans
app/documents`.
**Acceptance:** the specification's acceptance in full through `/plans` with synthetic data.
