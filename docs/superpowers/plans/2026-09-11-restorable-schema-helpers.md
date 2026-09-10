# Restorable schema helpers implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Make a PostgreSQL custom-format backup containing V23/V24 rows restorable under
`pg_restore`'s deliberately empty caller search path.

**Architecture:** A forward-only V26 migration pins the three restore-time helper functions to
`pg_catalog, public`; no applied migration is edited. A SQL invariant executes the recursive and
delegating helpers after clearing the caller search path, which reproduces the failure without a
private backup. The production restore drill remains Phase 2 work.

**Tech Stack:** PostgreSQL 18, Flyway SQL migrations, pgTAP-free transactional SQL invariants.

**Spec:** [Security and privacy §9](../../operations/security-and-privacy.html#9-backups) and
[testing and data quality §7](../../operations/testing-and-data-quality.html#7-manual-verification),
with the observed defect recorded in [progress](../progress.md#raised-and-not-yet-actioned).

## Global constraints

- Workspace: `/Users/sandjaieravi/dev/OneLedger`.
- Branch: `fix/restorable-schema-helpers`.
- Base and PR target: `develop`.
- Use the next free migration, V26; never edit applied V23 or V24.
- No standing-ledger data, backup content, account identity or real figure enters the repository.
- Validate against an empty V1–V26 database and a disposable copy of the standing ledger.

---

### Task 1: Pin restore-time helper resolution

**Files:**

- Create: `db/migrations/V26__restorable_schema_helpers.sql`
- Create: `db/tests/0023_restorable_schema_helpers.sql`
- Modify: `docs/superpowers/schema.md`
- Modify: `docs/superpowers/progress.md`

**Interfaces:**

- Consumes: `classification_payload_has_originals(jsonb)`,
  `counterparty_character_is_decimal(text)` and
  `counterparty_text_has_private_identifier(text)` from V23/V24.
- Produces: those same signatures with function-level
  `search_path = pg_catalog, public`; callers and constraints do not change.

- [ ] **Step 1: Write the failing invariant**

  Add a transactional SQL test that sets `search_path` to the empty string and invokes each
  helper through its `public.`-qualified entry point. Assert that nested classification originals
  are detected, safe nested content is not, Unicode decimal runs are detected, and ordinary
  merchant text is not.

- [ ] **Step 2: Prove the invariant fails on V25**

  Run the new SQL file against the isolated migrated test database before V26 exists. Expected:
  PostgreSQL cannot resolve the recursive classification helper or the delegated decimal helper.

- [ ] **Step 3: Add the forward-only migration**

  Add exactly:

  ```sql
  ALTER FUNCTION classification_payload_has_originals(JSONB)
    SET search_path = pg_catalog, public;
  ALTER FUNCTION counterparty_character_is_decimal(TEXT)
    SET search_path = pg_catalog, public;
  ALTER FUNCTION counterparty_text_has_private_identifier(TEXT)
    SET search_path = pg_catalog, public;
  ```

  Explain that `pg_restore` clears the caller search path and restores rows while checks invoke
  these helpers.

- [ ] **Step 4: Record the schema and task status**

  Add V26 to `schema.md`. In `progress.md`, mark the raised restore-helper item delivered by this
  branch while leaving the complete Phase 2 restore drill explicitly pending.

- [ ] **Step 5: Run focused and full database verification**

  Run the new invariant on the isolated test database, then apply V1–V26 from empty and run every
  `db/tests/*.sql` file. Expected: 26 migrations and 23 invariant files pass.

- [ ] **Step 6: Re-run the standing-copy restore**

  Create a disposable compose project and volumes, dump the development database without printing
  its rows, create the cluster roles required by the archive grants, restore pre-data/data/post-data
  with `--exit-on-error`, then run Flyway. Expected: the archive restores directly with no temporary
  function alterations and Flyway reports V26. Destroy the scratch database and unencrypted dump on
  both success and failure.

- [ ] **Step 7: Verify, review once, and deliver**

  Run `/verify`, then one `/oneledger-code-review`, apply only its findings, and use a narrow
  confirmation pass for any fixes. Commit as `fix(db): backups restore with an empty search path`,
  rebase onto `origin/develop`, push, open a PR titled
  `[P1] fix(db): backups restore with an empty search path`, and merge into `develop`.
