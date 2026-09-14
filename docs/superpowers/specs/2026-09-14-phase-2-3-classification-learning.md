# Phase 2.3 — classification learning and review depth

Owner-approved outcome: [roadmap Phase 2.3](../../product/roadmap.html), planned at personal scale on
14 September 2026 together with Phases 2.2 and 2.4. The
[dated plan](../plans/2026-09-14-phase-2-3-classification-learning.md) declares the branch.

## Outcome

Corrections turn into narrow rule suggestions with accurate affected counts; every rule shows how
often it decides rows and where current rows disagree with it; overall classification coverage and
a prioritised review queue are visible; nothing already classified changes without an explicit,
previewed, undoable owner action.

## Authority and existing capabilities

- [Classification design](../../design/classification.html): explicit, never silent; retroactive
  application is opt-in; the engine abstains rather than guesses; Settings › Rules reports coverage,
  hit counts, override counts and top unclassified descriptions.
- [ADR-0020](../../adr/0020-assisted-ledger-decisions.html) and V23: previewed classification runs,
  immutable decisions and undo. Phase 2.3 reuses `POST /classification-runs/preview`,
  `POST /classification-runs/{runId}/apply` and `POST /classification-runs/{runId}/undo` (preview
  takes a `knowledgeVersionId`, a reason and 1–200 items with explicit per-row `changes`, and refuses
  reviewed `user_manual` rows and confirmed transfer legs); it adds no new write path for ledger
  rows.
- Existing: the transactions screen classify prompt proposes a pattern (`proposedPattern`) and
  previews affected rows through `POST /rules/preview`; rules are created through `POST /rules`.

No migration is needed.

## Rule analytics — `GET /rules/analytics`

Owner-only, read-only, over `v_transaction_posting` rows of the household:

- **Coverage:** counts and percentages of rows by `classified_by` (`user_manual`, `user_rule`,
  `merchant_alias`, `recurring_series`, `prior_match`, `seed_rule`, `unclassified`, `ai`), of
  reportable rows with no category, and counts by `classification_confidence` band (none, below
  0.5, 0.5–0.8, above 0.8), over the anchor window (`coverage.HISTORY_ANCHOR` to today); percentages
  are decimal strings with two places, absent when the denominator is zero.
- **Per active rule:** `ruleId`, `name`, `hits` (rows with `classification_rule_id` = the rule and
  `classified_by IN ('user_rule', 'seed_rule')`), `lastHitDate` (latest booked date of those rows),
  `disagreements` (rows for which the rule is the first matching active rule by priority, using the
  same `_first_matching` evaluation the classifier uses, whose current category or type differs from
  the rule's outcome and whose `classification_rule_id` is distinct from the rule) and up to 20
  redacted sample rows of those disagreements (date, redacted description, amount string).
- **Top unclassified descriptions:** at most 20 normalized descriptions (redacted with the ledger
  preview redactor) with row counts and total absolute base amount, ordered by count then amount, as
  the classification design's measurement section states.

## Suggestions — `GET /rules/suggestions`

A suggestion is a candidate rule the owner has not created, derived only from owner corrections:

- group reportable rows with `classified_by = 'user_manual'` by (proposed pattern, direction,
  category, txn type), where the proposed pattern is a Python twin of the web's `proposedPattern` in
  `api/oneledger/ingest/classify.py`, both proven by one shared set of test vectors (the web keeps its
  function; no new endpoint);
- keep groups with at least 3 rows where no active rule already produces that outcome for that
  pattern;
- return at most 20 suggestions ordered by row count: pattern, direction, category, txn type,
  `evidenceRows`, and `affected` and `unclassified` computed by the same matching function
  `POST /rules/preview` uses over the same row set, so the figures equal what the rule preview
  shows.

Creating a suggested rule uses `POST /rules` (future rows only). Applying it to past rows is a
separate owner step reusing the existing `POST /classify?ruleId=` cascade pass: the web previews
first (`?preview=true`, reporting `considered`/`changed` and a redacted sample) and only calls the
non-preview pass once the owner confirms. This reaches only rows still `classified_by =
'unclassified'`, not every row the rule matches, and is not undoable — the same scope and the same
guarantee `POST /classify?ruleId=` already gives the transactions screen's classify prompt.

## Review queue ordering

`GET /classification-review/groups` gains `sort=impact` (default unchanged): groups ordered by the
total absolute `amount_base` of their occurrences (the query selects `amount_base`), then occurrence
count. The review screen offers the sort.

## Web

Settings › Rules gains a **Coverage and analytics** panel (coverage and confidence figures, per-rule
hits, last hit, disagreements with their redacted sample rows, and top unclassified descriptions) and
a **Suggested rules** list (create for future rows; a separate "check past rows" preview followed by
an explicit "apply N changes" confirm, through `POST /classify?ruleId=`). Server strings only.

## Required test evidence

Coverage counts and percentages; rule hits, last hit and disagreements for a synthetic rule;
suggestions appear only from at least three owner corrections, never duplicate an existing rule, and
report affected counts equal to the rule preview; impact sort order; owner and household isolation;
creating a suggestion changes no existing row until an explicit apply.

## Acceptance

Through the application: the rules screen shows coverage, hits, disagreements and top unclassified
descriptions; repeated corrections produce a narrow suggestion with an accurate affected count; past
rows change only after a previewed confirm, reaching only currently-unclassified rows, with no undo.

## Non-goals

Machine-learned or AI classification (ADR-0003); automatic rule creation; confidence scores beyond
existing `classification_confidence`; new ledger write paths.

## Documents to reconcile

`docs/design/api-contracts.html`, `docs/design/classification.html` (measurement section: hit,
disagreement and confidence definitions),
`docs/operations/testing-and-data-quality.html`, `docs/superpowers/progress.md`.
