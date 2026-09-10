-- db/migrations/V22__statement_layout_usage_is_derived.sql
-- `statement_layout` no longer carries a usage counter: how often a layout was used is
-- `import_batch`'s to say.
--
-- V12 created `times_used` and `last_used_at`, and no code in the tree ever wrote either. After
-- 67 real imports across four layouts, all four still reported `times_used = 0`. A counter
-- nobody maintains does not read as "unknown"; it reads as "never used". V19 names the same trap
-- for `classification_rule.times_applied`, whose counter the cascade does maintain.
--
-- Dropped rather than maintained (M2 spec §7 item 16). Nothing reads them: the import screen's
-- layout picker ranks by fingerprint score (`api-contracts.html` §4), and slice 1.7's mapper
-- ranks nothing. And the answer is already stored: every batch names the layout row it used in
-- `import_batch.statement_layout_id`, so a count and the latest `started_at` over those rows is
-- right for every batch ever staged, including the 67 that a counter starting now would never
-- see.
--
-- Nothing is lost: both columns hold their defaults on every row.

ALTER TABLE statement_layout
  DROP COLUMN times_used,
  DROP COLUMN last_used_at;
