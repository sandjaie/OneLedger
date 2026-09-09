-- db/migrations/V17__import_batch_records_period_provenance.sql
-- `import_batch` records whether its period was stated by the statement or derived from its rows.
--
-- V12 stored `period_start` and `period_end` and not how they were arrived at. The distinction is
-- real and the pipeline already knows it — `normalize.StatementPeriod` carries `is_derived`,
-- set false where the layout captured a period pattern and true where the bounds came from the
-- rows' own dates — but it lived only as long as the `POST /parse` response.
--
-- `GET /import/{batch}` is where the period is actually read, and it had no honest answer:
-- claiming `true` labels a declared period derived, claiming `false` labels a computed one
-- stated, and Invariant I7 forbids inventing either. It reported null, which was correct and
-- useless. The column is what makes an answer possible.
--
-- The two are indistinguishable after the fact: a period derived from rows dated 1 and 2 March
-- looks exactly like one a statement stated as 1-2 March. Nothing can recompute this later,
-- which is why it is stored rather than inferred.
--
-- **Nullable on purpose.** A batch staged before this migration genuinely has no recorded
-- provenance, and a default would assert one for it. Null means "not recorded" and reads back as
-- such; every batch staged from here carries a real value.

ALTER TABLE import_batch
  ADD COLUMN period_is_derived BOOLEAN;

COMMENT ON COLUMN import_batch.period_is_derived IS
  'True where the period came from the rows dates, false where the statement stated it, null '
  'for a batch staged before V17. Never inferred: the two are indistinguishable afterwards.';
