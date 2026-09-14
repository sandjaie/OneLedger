# Phase 2.4 data quality and recovery implementation plan

> Execute in the active checkout. Track completion only in `../progress.md`.

**Goal:** Encrypted database and document backups with a structural manifest, an isolated restore
drill that proves them, and a diagnostics view of every coverage, reconciliation and attribution gap.

**Architecture:** Shell scripts extend the existing encrypted backup; the doctor reads manifest and
drill files by stamp and adds reconciliation and wealth-history findings.

**Stack:** Existing Bash, PostgreSQL client tools, Python/FastAPI and Next.js/React; no migration or
dependency. See the [Phase 2.4 specification](../specs/2026-09-14-phase-2-4-data-quality-recovery.md).

## Global execution contract

- Workspace `/Users/sandjaieravi/dev/OneLedger`; active checkout, one writer, no worktrees; branch
  from current `develop`, one PR to `develop` titled `[P2.4] …`. Reviewed once with Phases 2.2 and
  2.3.
- Scripts never touch the working ledger or real backups in tests; synthetic stacks only; no row
  content, identifier or filename in manifests, drill files or output.
- After affected tests: one `/oneledger-code-review`, one repair, `scripts/verify.sh` once, push,
  PR, merge.

## Branch table

| # | Outcome | Branch | Prod | Test | Depends |
| -: | --- | --- | ---: | ---: | --- |
| 1 | backups, restore drill and diagnostics | `feature/p2-4-data-quality-recovery` | 700 | 600 | Phase 2.3 |

## Task 1 — backups, restore drill and diagnostics

**Workspace/branch/base/target:** `/Users/sandjaieravi/dev/OneLedger` /
`feature/p2-4-data-quality-recovery` / `develop` / `develop`.
**Depends:** Phase 2.3 merged. **Forecast:** 500–800 production, 400–700 tests.
**Files:** update `scripts/backup-db.sh` (one stamped run: dump, documents archive and snapshot-bound
manifest), `docker-compose.yml` and `docker-compose.production.yml` (backup service mounts documents
read-only and sets `DOCUMENTS_DIR`), `api/oneledger/routes/doctor.py`,
`api/tests/test_doctor_live.py`, `web/app/settings/data-quality.tsx`; create
`scripts/restore-drill.sh`, `scripts/backup-manifest.sql`,
`api/tests/test_backup_drill_live.py` (drives the scripts against the isolated test server with a
synthetic documents directory); `README.md` backup section; the specification's documents.
**Contract:** the specification's manifest, documents archive, drill steps and doctor sections
exactly; the drill refuses any connection that is not the isolated test server; manifests and drill
files hold structural facts only.
**Tests:** synthetic backup produces dump, archive and manifest; drill passes; tampered dump or
document fails with non-zero exit; working-ledger refusal; doctor reads stamps and reports ages,
drill result and wealth-history residual months.
**Commands:** `(cd api && uv run pytest -q tests/test_backup_drill_live.py tests/test_doctor_live.py
tests/test_health.py)`; `shellcheck scripts/backup-db.sh scripts/restore-drill.sh` if
available; `(cd web && npx vitest run app/settings && npm run typecheck)`; `scripts/verify.sh`.
**Acceptance:** the specification's acceptance, owner-operated on the real stack.
