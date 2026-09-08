# M2 delivery evidence

Dated verification evidence for the M2 statement importer, moved out of
[the tracker](../progress.md) on 8 September 2026: these are the results of runs that happened,
retained as a record. They are **not** refreshed when this file is edited, and they are not a
claim about the tree as it stands today — the tracker's Evidence column and each PR carry that.

Task 3 is merged. [PR #34](https://github.com/sandjaie/OneLedger/pull/34) landed on `develop` as
`30e6783` after eight GitHub Codex review rounds fixing 17 privacy, proposal, header, grammar and
validation findings; the branch is deleted. The seven-round closeout gates passed on
2026-09-08: API `266 passed, 2 warnings` against the live Compose database with no skips;
web `97 passed` across 8 Vitest files with lint/typecheck clean; generated API types were unchanged;
and the documentation/whitespace checks passed. The eighth round was fixed in an environment with
no reachable database, so its API run is `261 passed, 10 skipped` — the ten skips are the
database-backed account and transaction tests, which no eighth-round change touches; the web gate,
the API type drift check, lint, strict mypy and the documentation checks all passed there. Original
RED evidence for the recovered first three implementation steps is unavailable and is not claimed.

Task 4a gate evidence, 2026-09-08, in an environment with no Docker and therefore no Flyway: the
migrations were applied from empty in version order with `psql` against a scratch PostgreSQL 16
cluster — 13 migrations, then the seeds, then `db/tests/*.sql`, 11 passed. Flyway's own checksum
and versioning path was not exercised and is not claimed. API `358 passed, 2 skipped` against
that live database, the two skips being the Phase 0 live-identity gates that remain open; Ruff,
Ruff format and strict mypy clean over 40 files; web `97 passed` across 8 Vitest files with
typecheck clean and generated API types unchanged; documentation and whitespace checks passed.
`gitleaks` is not installed in this environment and was not run.

Review-fix verification on 2026-09-07: the covering account/layout/engine/word/fixture run passed
`135` tests against local PostgreSQL, with no skips and the same two upstream warnings. Targeted
Ruff, formatting and strict mypy passed over 13 files. Regenerated API types passed the drift
check and web typecheck; docs checked 125 documents and `git diff --check` passed. Full API/web
suites are reserved for the fix re-review boundary; no new full-suite result is claimed here.
