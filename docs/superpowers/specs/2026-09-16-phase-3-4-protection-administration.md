# Phase 3.4 — protection and administration

Owner-approved outcome: [roadmap Phase 3.4](../../product/roadmap.html), planned at personal scale
on 16 September 2026 together with [Phase 3.3](2026-09-16-phase-3-3-goals-emergency-reserve.md), on
which it depends. The [dated plan](../plans/2026-09-16-phase-3-4-protection-administration.md)
declares both branches. Workspace: `/Users/sandjaieravi/dev/OneLedger`; every branch starts from and
targets `develop`.

## Product outcome

A small register on Plans holds insurance policies, deposits, claims and reimbursements, nominee
records and administration deadlines. Each record links to documents in the existing vault — the
owner can now upload a PDF such as a policy schedule or deposit certificate directly — and feeds
the one Needs attention list: a renewal, a maturity or a deadline coming due, and a record not
reviewed for a year. Completing a renewal moves the record to its next renewal date, so the next
recurrence is preserved.

The money side reuses commitments. A policy's premium becomes a recurring commitment on the paying
account; a deposit's expected proceeds, or a claim's expected reimbursement, becomes a one-off
commitment on the receiving account. Each is settled with the Phase 3.1 settlement flow, so a
partial reimbursement leaves the correct outstanding amount, and a claim or maturity settles only
against a posting that is not income — a reimbursement or returned principal is never counted as
newly earned income. No adequacy recommendation, legal deadline or filing advice is inferred.

## Authority and decisions in force

- Everything in force for [Phase
  3.1](2026-09-15-phase-3-1-commitments-actions.md#authority-and-decisions-in-force) and [Phase
  3.3](2026-09-16-phase-3-3-goals-emergency-reserve.md#authority-and-decisions-in-force).
- [Security and privacy](../../operations/security-and-privacy.html): the vault's storage, masking
  and never-store rules apply to uploaded documents; no credential or complete identity number is
  stored (a nominee record holds only whether a nominee is recorded, a review date and documents).
- [Finance OS F11](../../product/finance-os.html), scenarios P09 and P10.
- Existing code reused: `ingest.storage.store`, the `document` table and `GET /documents` row,
  `routes/plans.py` commitment create/update and card-source pattern,
  `routes/commitment_resolutions.py` and `assert_commitment_payment()`, `routes/actions.py`
  `action_list()` and marks.

## Data model contract

Phase 3.3 ends at V52 and SQL test `0049`. Task 1 adds V53 with `0050`; Task 2 adds V54 with `0051`.

### Register (V53)

```sql
CREATE TYPE register_kind AS ENUM (
  'insurance_policy', 'deposit', 'claim', 'nominee', 'administration');

CREATE TABLE register_record (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID NOT NULL REFERENCES household(id),
  idempotency_key   UUID NOT NULL,
  kind              register_kind NOT NULL,
  title             TEXT NOT NULL,
  counterparty      TEXT,
  covered           TEXT,
  amount            NUMERIC(20,4),
  currency          CHAR(3) REFERENCES currency(code),
  premium_amount    NUMERIC(20,4),
  premium_cadence   commitment_cadence,
  key_date          DATE,
  account_id        UUID,
  instruction       TEXT,
  nominee_recorded  BOOLEAN,
  last_reviewed_on  DATE,
  completed_at      TIMESTAMPTZ,
  archived_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_register_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_register_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_register_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT register_texts_are_bounded CHECK (
    char_length(title) BETWEEN 1 AND 120 AND title = btrim(title)
    AND (counterparty IS NULL OR char_length(counterparty) BETWEEN 1 AND 120)
    AND (covered IS NULL OR char_length(covered) BETWEEN 1 AND 120)
    AND (instruction IS NULL OR char_length(instruction) BETWEEN 1 AND 200)),
  CONSTRAINT register_amounts_are_valid CHECK (
    (amount IS NULL OR amount > 0) AND (premium_amount IS NULL OR premium_amount > 0)
    AND ((amount IS NULL AND premium_amount IS NULL) OR currency IS NOT NULL)
    AND ((premium_amount IS NULL) = (premium_cadence IS NULL))
    AND (premium_cadence IS NULL OR premium_cadence <> 'once')),
  CONSTRAINT register_kind_shape CHECK (
       (kind = 'insurance_policy' AND key_date IS NOT NULL AND account_id IS NULL
        AND instruction IS NULL AND nominee_recorded IS NULL AND completed_at IS NULL)
    OR (kind = 'deposit' AND key_date IS NOT NULL AND account_id IS NOT NULL
        AND covered IS NULL AND premium_amount IS NULL AND nominee_recorded IS NULL
        AND completed_at IS NULL)
    OR (kind = 'claim' AND key_date IS NOT NULL AND counterparty IS NOT NULL
        AND amount IS NOT NULL AND covered IS NULL AND premium_amount IS NULL
        AND account_id IS NULL AND instruction IS NULL AND nominee_recorded IS NULL)
    OR (kind = 'nominee' AND account_id IS NOT NULL AND nominee_recorded IS NOT NULL
        AND amount IS NULL AND premium_amount IS NULL AND covered IS NULL
        AND instruction IS NULL AND key_date IS NULL AND completed_at IS NULL)
    OR (kind = 'administration' AND key_date IS NOT NULL AND amount IS NULL
        AND premium_amount IS NULL AND covered IS NULL AND account_id IS NULL
        AND instruction IS NULL AND nominee_recorded IS NULL))
);

CREATE TABLE register_document (
  household_id UUID NOT NULL REFERENCES household(id),
  record_id    UUID NOT NULL,
  document_id  UUID NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, record_id, document_id),
  CONSTRAINT fk_register_document_record FOREIGN KEY (household_id, record_id)
    REFERENCES register_record (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_register_document_document FOREIGN KEY (household_id, document_id)
    REFERENCES document (household_id, id) ON DELETE RESTRICT
);
```

- Meanings by kind: `insurance_policy` — `counterparty` provider, `covered` person or asset,
  `amount` coverage, premium, `key_date` renewal date. `deposit` — `account_id` the deposit account
  (a `fixed_deposit` or `recurring_deposit` account, checked by the route), `counterparty`
  institution, `amount` expected proceeds, `key_date` maturity date, `instruction` renewal
  instruction. `claim` — `counterparty`, `amount` expected, `key_date` submission date,
  `completed_at` closed. `nominee` — `account_id`, `nominee_recorded`. `administration` — `key_date`
  deadline, `completed_at` done. `account.maturity_date` stays unused; the deposit record owns the
  maturity.
- Both tables enable household RLS. Archiving is terminal: `preserve_archived_register_record()`
  refuses any update to a record whose `archived_at` is set, and refuses deleting a record.

### Register-sourced commitments (V54)

```sql
ALTER TABLE commitment
  ADD COLUMN source_register_record_id UUID,
  ADD CONSTRAINT fk_commitment_register_scope FOREIGN KEY (household_id, source_register_record_id)
    REFERENCES register_record (household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT commitment_has_one_source CHECK (
    num_nonnulls(source_card_statement_id, source_register_record_id) <= 1);
CREATE UNIQUE INDEX uq_commitment_open_register_record ON commitment (source_register_record_id)
  WHERE source_register_record_id IS NOT NULL AND archived_at IS NULL;
```

`CREATE OR REPLACE FUNCTION assert_commitment_payment()` keeps every V48 condition and adds: when
the commitment's source record is a `claim` or `deposit`, the posting's `txn_type` must not be
`income` (same `commitment_payment_is_eligible` refusal). The candidates predicate in
`routes/commitment_resolutions.py` gains the same condition, keeping its sync comment.

## Document upload (Task 2)

`POST /documents` (multipart): `file` (a PDF of at most 20 MB read with a bounded read, else 422;
the repository has no shared upload limit to reuse), `documentType` in `tax_document |
insurance_policy | property_document | fd_certificate | receipt | other`, `title` (1–120,
normalized). It stores through `ingest.storage.store` with a fixed safe label per type (no uploaded
filename is retained, as payslip import does), inserts a `document` row with `parsed_status =
'not_applicable'`, and returns the `GET /documents` row shape; the same bytes in the household
already stored with the same `document_type` return that row with 200, and with another type (a
statement included) 409 `this file is already in the document library`. A non-PDF or unreadable file
is 422. `document.title` holds the owner's title.

Owner decision recorded here (planning, 16 September 2026): an uploaded document is not an import.
`GET /documents` rows gain `importKind: "upload"` for documents whose type is one of the six upload
types and that no import produced, with no import link shown in the library; the row also gains
`title`, non-null only for such uploads — the owner's own words, never a source filename. Statement,
investment and payslip rows keep `title: null`. `api-contracts.html` and `security-and-privacy.html`
record both.

## API boundary

Owner-only, household lock on writes, explicit household filters, 404 for foreign ids, bodies forbid
extra fields, texts NFKC-normalized and bounded, money `_money` strings.

### Task 1 — `routes/register.py`

| Route | Contract |
| --- | --- |
| `GET /register` | `?kind=&includeArchived=false&asOf=` → `{records: RegisterRow[]}`; key date then title |
| `POST /register` | `RegisterCreate` → `RegisterRow`; 201, 200 replay, 409 conflict |
| `PUT /register/{recordId}` | `RegisterUpdate` (kind fixed) → `RegisterRow`; archived 409 |
| `POST /register/{recordId}/archive` | → `RegisterRow`; idempotent |
| `POST /register/{recordId}/reviewed` | → `RegisterRow`; `last_reviewed_on = today`; idempotent |
| `POST /register/{recordId}/renewed` | `{nextRenewalDate}` → `RegisterRow`; policy only; must be after the current renewal date (422) |
| `POST /register/{recordId}/complete` | → `RegisterRow`; claim or administration only; idempotent |
| `POST /register/{recordId}/reopen` | → `RegisterRow`; clears `completed_at`; idempotent |

`RegisterCreate` carries `kind`, `title`, and the fields its kind allows (`counterparty`, `covered`,
`amount`, `currency`, `premiumAmount`, `premiumCadence`, `keyDate`, `accountId`, `instruction`,
`nomineeRecorded`) plus `idempotencyKey`; a field its kind forbids is 422, before the database. A
deposit's account must be an active `fixed_deposit` or `recurring_deposit` account; a nominee's any
account of the household. `RegisterRow`: fields, `lastReviewedOn`, `reviewDue` (a policy, deposit or
nominee whose `coalesce(last_reviewed_on, created_at::date)` is more than 365 days before `asOf`, so
a new record is not due at once), `completedAt`, `archivedAt`, `accountName`, and Task 2 adds
`documents[]` and `plannedCommitment`.

`action_list` adds, for unarchived records (`asOf` as today):

1. `record.<id>.renewal.<keyDate>` (`renewal_due`) for a policy with `key_date ≤ asOf + 30`;
   resolved by `renewed`.
2. `record.<id>.maturity.<keyDate>` (`maturity_due`) for a deposit with `key_date ≤ asOf + 30`; Task
   2 omits it once an unarchived commitment is sourced from the record; resolved by planning the
   proceeds or archiving.
3. `record.<id>.deadline.<keyDate>` (`deadline_due`) for an administration record not completed with
   `key_date ≤ asOf + 14`; resolved by `complete`.
4. `record.<id>.review` (`review_due`) where `reviewDue`; resolved by `reviewed`.

Each carries title, due date (the key date; none for review), `overdue`, the linked account and, for
a policy, the premium as amount. All accept snooze marks; none accepts dismissal. Completing a
renewal moves `key_date`, so the item for that date disappears and the next renewal returns inside
its window (P10).

### Task 2 — documents on records and commitments from records

| Route | Contract |
| --- | --- |
| `POST /documents` | "Document upload" above |
| `PUT /register/{recordId}/documents/{documentId}` | → `RegisterRow`; idempotent link; only a household document of an upload type (422 otherwise) |
| `DELETE /register/{recordId}/documents/{documentId}` | → `RegisterRow`; idempotent unlink |

`RegisterRow.documents[]` is `{documentId, documentType, title, uploadedAt}`.

`POST /commitments` accepts `sourceRegisterRecordId` (not with `sourceCardStatementId`, 422). The
record must be the household's and unarchived (404/409), and:

- `insurance_policy`: premium required (422 `this policy has no premium`); `cadence` equals
  `premium_cadence`; `amount` negative; the paying account any active account (a card included) in
  the record currency.
- `deposit`: expected proceeds and currency required (422 `this deposit has no expected proceeds`);
  `cadence` `once`, `amount` positive; the receiving account an active asset in the record currency
  and not the deposit account (422 `choose the account that receives the proceeds`).
- `claim`: `cadence` `once`, `amount` positive; the receiving account an active asset in the record
  currency; a completed claim is 409.
- `nominee`, `administration`: 422 `this record does not plan money`.

A second unarchived commitment for a record is 409 `money is already planned for this record`.
`PUT` re-applies these rules. Refusal sentences for schedule and sign follow the card-source ones.
`CommitmentRow.source` gains `register_record` with `sourceRegisterRecordId` and
`sourceRegisterTitle`. `RegisterRow.plannedCommitment` is `{commitmentId, cadence, amount, currency,
accountName, nextDueDate, status, paidTowardNext, remainingNext, partlyPaid} | null` from the Phase
3.1 progress helper — a claim's outstanding amount is its `remainingNext`, and what was received is
`paidTowardNext`.

## Web workflows

**Task 1 — Plans › Register** (anchor `#register`), grouped by kind. Each record shows its key facts
and key date, review state, documents (open through the existing original route) and actions:
**Edit**, **Mark reviewed**, **Renewed** (policy: next renewal date), **Complete** / **Reopen**
(claim, administration), **Archive**. **Add record** chooses a
kind and shows only that kind's fields, with a note that no identity numbers or credentials belong
in a record. Needs attention shows the new items with **Renewed**, **Mark reviewed**, **Complete**
and **Snooze**.

**Task 2.** Each record lists its documents (opened through the existing original route) with
**Attach document** — pick from the library's upload-type documents by title, or **Upload PDF** with
type and title — and **Detach**; the Documents library shows uploads with their title and no import
link. A policy offers **Plan premium**, a deposit **Plan proceeds** and a claim **Expect
reimbursement**, each opening the commitment form pre-filled (label from the title, cadence, the
negated premium or the expected amount, first date the renewal or maturity date — for a claim,
today — and the source) with the account
picker limited to eligible accounts. A record with a planned commitment shows it with status, next
due date, and for a claim **Expected**, **Received** and **Outstanding**, plus **Settle** (the Phase
3.1 panel). The settlement panel explains that an income posting cannot settle a claim or deposit
and links to the transaction to recategorise it as a refund or transfer.

Server strings only; no money arithmetic or money-string comparison in TypeScript.

## Failure behaviour and privacy

A refused record, link, upload or commitment leaves every row as it was; an upload that fails after
storing bytes leaves no `document` row (the stored file is content-addressed and reused on retry).
Nothing here writes a transaction, balance or classification. Uploaded filenames are never retained;
fixtures are synthetic PDFs; no record text is logged.

## Required test evidence

- V53/V54 SQL: owner/member/foreign/anonymous isolation on both tables; scope keys; every kind shape
  refusal; archived preservation; one source per commitment and one open commitment per record; the
  replaced payment trigger refusing an income posting for claim- and deposit-sourced commitments and
  accepting it for a policy or owner commitment, with every V48 refusal still holding.
- Live API (Task 2): document upload (PDF stored and listed with `importKind: "upload"` and its
  title, same bytes and type 200, same bytes as another type 409, non-PDF and oversized 422,
  statement types refused); record create/update per kind with forbidden-field 422s; deposit account
  type refusal; reviewed, renewed (P10: the renewal item disappears and returns for the next date),
  complete/reopen, archive; (Task 2) document link/unlink with a foreign document 404; each new
  action item's window edges (a new record not review-due; review due at 365/366 days after creation
  or last review) and snooze; P09 — a 1,000 claim with a planned reimbursement receives a 400 refund
  posting as a part payment: outstanding `600.0000`, received `400.0000`, the dashboard's month
  income unchanged, and an income-typed posting refused 422; plan premium and proceeds rules and 409
  second plan; maturity item omitted once proceeds are planned; household isolation with real
  foreign rows; `GET` routes change no row.
- Web: kind-specific form fields and bodies; upload request; plan-from-record pre-fill; claim
  figures from server strings.

## Acceptance

Through the web application with synthetic data: a policy with a renewal due appears once in Needs
attention and **Renewed** clears it while preserving the next renewal (P10); a claim of 1,000
receiving 400 shows 600 outstanding with no new income (P09); a deposit maturing within 30 days asks
for its proceeds to be planned, and settling them against the transfer leg clears it; an uploaded
policy PDF opens from its record; a record unreviewed for a year asks for review. Owner checks on
the real ledger record only structural counts.

## Explicit non-goals

Insurance adequacy recommendations, inferred legal deadlines or filing advice, tax calculations
(Phase 5.6), nominee personal details or identity numbers, OCR or parsing of uploaded documents,
non-PDF uploads, automatic claim or maturity matching, and any posting or payment.

## Documents the implementation must reconcile

`docs/superpowers/schema.md` (V53, V54), `docs/design/data-model.html`,
`docs/design/api-contracts.html`, `docs/operations/testing-and-data-quality.html` (Phase 3.4 gate),
`docs/operations/security-and-privacy.html` (owner document upload, nominee boundary),
`docs/product/ui-information-architecture.html` (register), `docs/reference/glossary.html`, and
`docs/superpowers/progress.md` for live state only.
