# Guarded description reparse

Date: 11 September 2026

## Context

The centred-row parser correction can reproduce five already imported statements with the same
dates, amounts, currencies, row counts and reconciliation, but with descriptions assigned to the
proper rows. The ledger deliberately makes `transaction.description_raw` immutable with the other
statement facts. A repair must preserve that audit record and every money/provenance field while
allowing the corrected parser output to replace only the derived `description_clean` used by
classification and transaction presentation.

## Task 1 — preview and apply a structurally guarded description reparse

**Outcome:** an owner-only `POST /import/{batchId}/reparse-descriptions` endpoint reads the stored
original with a newer version of the same saved layout. It defaults to preview. Apply mode updates
only changed `transaction.description_clean` values and marks those rows unreviewed so existing
classification choices are visibly queued for reassessment.

| Workspace | Branch | Base and PR target | Forecast |
| --- | --- | --- | --- |
| `/Users/sandjaieravi/dev/OneLedger` | `fix/p1-2-guarded-description-reparse` | `develop` | about 180 production + 260 test lines; within budget |

**Affected modules:** a focused route under `api/oneledger/routes/`, route registration,
API tests, generated web API types, `docs/design/api-contracts.html`, and
`docs/superpowers/progress.md`. No migration is needed because `description_clean` is already the
derived description field and is not part of the immutable source trigger.

**Request:** `layoutId`, optional `password`, and `apply` (default false). Password exists only in
the request frame, is excluded from model representation, and is never stored, logged, or echoed.

**Guard contract:**

- require an active owner session and a completed batch in that household;
- require the target layout to be a newer version with the same layout name as the batch's layout;
- resolve the content-addressed document only beneath `DOCUMENTS_DIR`, reject links, and verify
  its SHA-256 before parsing;
- require zero normalization failures and a `matched` reconciliation;
- compare every reparsed row with the immutable staged audit row by source row number, page, booked
  and value dates, amount and currency, and original amount/currency; row counts and row-number sets
  must be identical;
- require every accepted staged row to map to exactly one top-level committed transaction and
  refuse a batch with split children;
- perform no writes in preview mode or after any failed guard; in apply mode lock the batch and
  target transactions, recheck the stored structure, then update only changed
  `description_clean`, `is_reviewed`, and `updated_at`; confirmed transfer legs keep their review
  state, while other changed rows, including previously manual classifications, enter the existing
  guarded review/preview/apply path without losing their current choice;
- make a repeated apply idempotent.

**Response:** only structural evidence: batch id, whether writes were requested, rows compared,
descriptions changed, and rows queued for classification review. No statement text, filename,
account identity, money, password, or filesystem path leaves the route.

**Required tests:** begin with a failing happy-path preview proving no update, then cover apply,
idempotent apply, wrong household/not-found equivalence, non-completed batch, unrelated or older
layout, hash mismatch, path escape/link, encrypted-file failure without echo, normalization failure,
reconciliation failure, structural row mismatch, accepted-row/transaction mismatch, and split
refusal. Assert a failed guard performs no update and that apply changes no field beyond the three
named derived/review fields.

**Validation:** run focused route tests, regenerate and verify API types, then `/verify` and one
local diff review against `develop`. Push hooks run the complete suite and database invariants.

**Local acceptance:** preview all five affected batches using the password held outside the
repository; require every response to report a complete structural match and at least one changed
description. After an encrypted backup, apply each once, repeat preview to prove zero remaining
changes, and confirm transaction count, native amounts, frozen amounts, transfers and reportable
monthly totals are unchanged. Report booleans/counts only.

**Acceptance criteria:** all guards and privacy assertions are green; preview is read-only; apply
is idempotent and limited to derived description/review state; the five live batches pass the
structural and no-money-change acceptance; `progress.md` records the correction without private
figures.
