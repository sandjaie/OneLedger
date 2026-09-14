# Phase 2.4 — data quality and recovery

Owner-approved outcome: [roadmap Phase 2.4](../../product/roadmap.html), planned at personal scale on
14 September 2026 together with Phases 2.2 and 2.3. The
[dated plan](../plans/2026-09-14-phase-2-4-data-quality-recovery.md) declares the branch.

## Outcome

Settings › Data Quality identifies coverage gaps, reconciliation and attribution gaps, stale prices
and backup state in one place; each finding names its probable cause. Both the database and the
documents volume are backed up encrypted. A restore drill restores the newest backups into an
isolated database and document directory and proves they reproduce the structural evidence recorded
when the backup was taken; the result is recorded and shown.

## Authority

- [Security and privacy](../../operations/security-and-privacy.html) §6/§9: backups are encrypted
  wherever stored; restore into isolation only.
- [Testing and data quality](../../operations/testing-and-data-quality.html): "A backup that has
  never been restored is a hypothesis"; doctor findings name probable causes.
- Existing: `scripts/backup-db.sh` (encrypted `pg_dump`, `--if-due`, compose `backup` service),
  `GET /doctor` Backups, Account identities, Investments and Coverage sections, `scripts/test-db.sh`
  (isolated test server).

## One backup run, one stamp

`scripts/backup-db.sh` becomes one run that writes three files sharing one UTC stamp:
`<prefix>-<stamp>.dump.enc`, `<prefix>-<stamp>.documents.tar.enc` and
`<prefix>-<stamp>.manifest.json.enc`, encrypted with the existing passphrase and cipher, on the
existing daily `--if-due` schedule and pruning rule for all three. The compose `backup` service in
both compose files mounts the documents volume read-only (`documents:/oneledger/documents:ro`) and
sets `DOCUMENTS_DIR`; the script runs inside that service (or `docker compose run backup`).
`docs/operations/security-and-privacy.html` §9 is amended to this single daily cadence for database
and documents with the same retention, and a monthly restore drill.

## Structural evidence manifest

The manifest holds only structural facts, taken from the same database snapshot as the dump: a
`REPEATABLE READ` `psql` session (a bash coproc) calls `pg_export_snapshot()`, computes the manifest
and stays open while `pg_dump --snapshot=<id>` runs. It records the latest applied Flyway version;
row counts for every application table; exact `NUMERIC` sums as decimal strings for
`transaction.amount`, `investment_transaction.net_amount`, `payslip.net_pay`, `payslip.gross_earnings`
and `month_end_position.value_base`; and the documents archive's file count and the SHA-256 of its
sorted per-file SHA-256 list. No row content, identifier or filename is recorded.

## Restore drill

`scripts/restore-drill.sh`:

1. refuses any target that is not the isolated test server: it starts or reuses `db-test` through
   `scripts/test-db.sh` and runs `pg_restore` and `psql` inside it
   (`docker compose --profile test exec -T db-test …`) so the client matches the server; it never
   runs `down` on a server it did not start;
2. picks the newest stamp that has all three files, creates a temporary database and a temporary
   document directory;
3. decrypts and restores the dump and extracts the archive; any decrypt, `tar` or `pg_restore` error
   is a failed drill (the cipher has no integrity check, so errors are expected on tampering);
4. recomputes the manifest from the restored copy and compares it item by item;
5. always writes `<prefix>-<stamp>.drill.json` (stamp tested, drill time, pass/fail per item,
   overall result), removes the temporary database and directory, and exits non-zero on failure.

The script never prints row content. It is run by hand; it is not part of `scripts/verify.sh`.

## Diagnostics

`GET /doctor` gains:

- **Backups:** the newest stamp's age (warning over 48 hours), whether that stamp has all three
  files (checked by filename only), and the newest `drill.json` result and age (warning when none,
  failed, or older than 31 days). The API never decrypts and never needs `BACKUP_PASSPHRASE`.
- **Wealth history:** months with incomplete positions and complete months whose attribution
  residual is not zero in the base currency, counted with the latest such month (requires Phase 2.2).

Settings › Data Quality renders the new sections with existing components.

## Required test evidence

- Shell: a synthetic stack backup produces the dump, documents archive and manifest; the restore
  drill on it passes; tampering with the dump or a document produces a failed drill and non-zero
  exit; the drill refuses the working ledger connection.
- API: doctor backup section reads stamps and drill files; wealth-history findings for synthetic
  data.

## Acceptance

Owner-operated on the real stack: a documents and database backup is taken, the restore drill passes
into isolation, and Data Quality shows the backups, the drill result and any coverage, reconciliation
or attribution gaps. Record structural pass/fail only.

## Non-goals

Off-site upload automation (the owner copies encrypted files); point-in-time recovery; restoring
over the working ledger; scheduled drills.

## Documents to reconcile

`docs/operations/security-and-privacy.html` (§9 cadence and backup scope now include documents),
`docs/operations/testing-and-data-quality.html`, `docs/design/api-contracts.html` (doctor),
`README.md` backup and restore instructions, `docs/superpowers/progress.md`.
