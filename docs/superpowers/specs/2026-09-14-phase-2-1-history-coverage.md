# Phase 2.1 — complete historical coverage

Owner-approved outcome: [roadmap Phase 2.1](../../product/roadmap.html), planned at personal scale on
14 September 2026. The [dated plan](../plans/2026-09-14-phase-2-1-history-coverage.md) declares the
branch. Workspace: `/Users/sandjaieravi/dev/OneLedger`; the branch starts from and targets
`develop`.

## Outcome

The owner imports every available statement back to the approved history anchor and sees, for each
statement-sourced account and for payslips, one row of months from the anchor to the current month.
Each month says whether it is covered by a reconciled import, imported but not reconciled, only
partly covered, missing, outside the account's life, or acknowledged by the owner as a gap with a
reason. Missing periods and unsupported evidence therefore stay explicit instead of silently
shortening history.

## Authority

- [ADR-0011](../../adr/0011-36-month-history-horizon.html) sets the anchor at 1 January 2024 and
  requires an unobtainable statement to be recorded as a visible gap rather than silently starting
  the account later.
- Invariant I7 (reconciliation and derived periods are visible) and the existing import batch period
  facts (`import_batch.period_start`, `period_end`, `period_is_derived`, `reconciliation_status`).
- The existing doctor report (`GET /doctor`) owns data-quality findings.

## Data (V45)

The anchor is not configurable: `api/oneledger/coverage.py` defines
`HISTORY_ANCHOR = date(2024, 1, 1)` citing ADR-0011, so no setting can silently shorten history.

```sql
CREATE TYPE coverage_gap_reason AS ENUM (
  'not_issued', 'unobtainable', 'unsupported_format', 'account_not_in_use');

CREATE TABLE coverage_gap_acknowledgement (
  household_id UUID NOT NULL REFERENCES household(id),
  subject      TEXT NOT NULL,
  account_id   UUID,
  month        DATE NOT NULL CHECK (month = date_trunc('month', month)::date),
  reason       coverage_gap_reason NOT NULL,
  note         TEXT CHECK (note IS NULL OR char_length(note) BETWEEN 1 AND 200),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT coverage_gap_subject_agrees CHECK (
    (subject = 'account' AND account_id IS NOT NULL)
    OR (subject = 'payslips' AND account_id IS NULL)),
  CONSTRAINT fk_coverage_gap_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_coverage_gap_month
  ON coverage_gap_acknowledgement (household_id, subject, account_id, month) NULLS NOT DISTINCT;
```

RLS on the new table follows V43: `household_isolation FOR ALL TO authenticated USING
(household_id = current_household_id()) WITH CHECK (household_id = current_household_id())`.

## Coverage computation

`GET /coverage?asOf=` (owner-only, read-only; `asOf` defaults to today) returns `historyAnchor`,
`months` (first days from the anchor through the month of `asOf`) and `rows`.

- **Account rows:** every active or closed account whose type is statement-sourced (`current`,
  `savings`, `salary`, `credit_card`) in `GET /accounts` order. The account's life runs from
  `opened_on` (the anchor when null) to `closed_on` (`asOf` when null). The opening-balance date
  does not shorten life: months before it are evidence gaps the owner can acknowledge, as ADR-0011
  requires.
- **Evidence:** `import_batch` rows for the account with `status = 'completed'` and a non-null
  period (never filtered on `completed_at`, which cancellation also sets). A day is covered when a
  batch period contains it; overlapping batches form a union. A completed batch is always
  reconciled, because V18 `commit_import_batch` refuses any other reconciliation status.
- **Month status**, first match wins:
  1. `outside_account_life` — the month lies wholly before the life start or after its end;
  2. `reconciled` — every in-life day of the month is covered;
  3. `acknowledged` — an acknowledgement exists for the month (returns `reason` and `note`);
  4. `partial` — some but not all in-life days are covered;
  5. `in_progress` — the month of `asOf` while `asOf` is before its last day;
  6. `missing` — no in-life day is covered.
- Each account row also returns `lifeStart`, `openingBalanceDate`, `anchorAfterHistory`
  (`opening_balance_date` later than the anchor, the ADR-0011 visible-gap case) and counts per
  status.
- **Payslip row:** subject `payslips`; a month is `reconciled` when an active (unsuperseded) payslip
  exists for it, otherwise `acknowledged`, `in_progress` or `missing` by the same order; months
  before the earliest payslip are still reported, because salary history starts at the anchor.

## API

| Route | Contract |
| --- | --- |
| `GET /coverage` | The grid above; statuses and reasons are fixed strings; no filename or identifier |
| `PUT /coverage/acknowledgements` | Body `{subject, accountId?, month, reason, note?}` upserts; month must be inside the grid and, for accounts, inside the account's life; covered months are refused (409) |
| `DELETE /coverage/acknowledgements` | Query `subject`, `accountId?`, `month`; idempotent |

All three use `require_owner` and household filters; a foreign account is 404. `GET /doctor` gains a
**Coverage** section with one warning finding per row that has missing or partial months ("N months
missing, M partial, earliest YYYY-MM", with the account name) and one `ok` finding when every row is
reconciled or acknowledged.

## Web

A **Coverage** screen at `/coverage`, linked from the workspace navigation and from Settings › Data
Quality:

- one row per account and a payslip row, one column per month, a cell per status with a text label
  and a legend (colour is never the only signal);
- clicking a `missing` or `partial` cell offers **Import statements** (the import screen) and
  **Acknowledge gap** (reason and optional note); an acknowledged cell offers **Remove
  acknowledgement**;
- the history anchor is shown read-only.

Server strings only; no browser storage.

## Required test evidence

- Coverage statuses for a synthetic account: reconciled month, month split across two batches,
  a cancelled batch that covers nothing, partial month, missing month, month outside account life,
  acknowledged month, a later import outranking an acknowledgement, current month in progress, and
  an account with a null `opened_on` and a 2025 opening balance whose 2024 months are missing and can
  be acknowledged as `unobtainable`.
- Payslip row covered, missing and acknowledged months.
- Acknowledgement upsert, idempotent delete, covered-month refusal, foreign account 404, RLS on the
  table (SQL test).
- Doctor coverage findings; web grid labels and the acknowledge flow.

## Acceptance

Through the application, every statement-sourced account and payslips show a month-by-month grid
back to 1 January 2024; every month is reconciled, explicitly acknowledged as a gap, or visibly
flagged as missing, partial or unreconciled. Owner-operated: after importing all available real
statements, record the structural counts per status only.

## Non-goals

Automatic statement retrieval; inferring coverage from transactions without an import batch;
investment-statement coverage (investment imports have no period); FX coverage (already reported);
wealth history (Phase 2.2).

## Documents to reconcile

`docs/superpowers/schema.md` (V45), `docs/design/data-model.html`, `docs/design/api-contracts.html`,
`docs/operations/testing-and-data-quality.html`, `docs/superpowers/progress.md`.
