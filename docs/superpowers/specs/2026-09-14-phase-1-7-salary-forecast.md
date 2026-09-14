# Phase 1.7 — salary and a thin forecast

Owner-approved outcome: [roadmap Phase 1.7](../../product/roadmap.html#phase-1), planned at personal
scale under the 14 September 2026 re-baseline. The
[dated plan](../plans/2026-09-14-phase-1-7-salary-forecast.md) declares both branches. Workspace:
`/Users/sandjaieravi/dev/OneLedger`; every branch starts from and targets `develop`.

## Product outcome

The owner records payslips by entering them or by importing a supported text PDF that may hold many
consecutive monthly pay advices. A payslip is stored only when its payment lines equal the stated
gross, its deduction lines equal the stated deductions and net pay equals gross less deductions,
exactly. Re-importing a month creates nothing twice. The owner links a payslip's net pay to the one
ledger credit that received it. A mistake is corrected by a superseding payslip; nothing is edited
or deleted.

The owner enters commitments — one-off, monthly or annual signed amounts expected in an account —
and sees every ledger-valued account projected 30, 60 and 90 days ahead with the balance's evidence
date, each applied occurrence, what could not be projected and why, and the assumptions. Payslips,
links, commitments and forecasts change no transaction, balance, classification or report, and a
forecast writes nothing.

## Authority and decisions in force

- [ADR-0002](../../adr/0002-frozen-dual-currency-amounts.html): payslips hold native amounts only;
  cash received is the linked transaction's frozen facts. Forecast balances are never translated.
- [ADR-0010](../../adr/0010-signed-balance-convention.html): commitment amounts and projected
  balances carry the sign a posting to that account would carry.
- [ADR-0012](../../adr/0012-statement-layouts-as-data.html): the payslip adapter is
  capability-neutral, as Phase 1.6's investment adapters are. Its markers are generic pay-advice
  vocabulary; line labels and the currency are data; no employer, institution, country or currency
  name becomes an identifier.
- [ADR-0013](../../adr/0013-python-backend-service.html),
  [ADR-0016](../../adr/0016-revocable-owner-sessions.html): owner-only routes, explicit household
  filters, RLS as defence in depth. [ADR-0015](../../adr/0015-flyway-for-schema-migrations.html):
  forward-only migrations. [ADR-0003](../../adr/0003-defer-ai-to-v2.html): no AI.
- [API contracts §3](../../design/api-contracts.html#atomicity): the multi-row payslip write is one
  PostgreSQL function, shared by entry and import. Holdings and ADR-0006 snapshot accounts are
  excluded from the forecast.

## Data model contract

`develop` ends at V41 and SQL test `0038`; Task 1 adds V42 with `0039` and Task 2 V43 with `0040`.

### Payslips (V42)

```sql
CREATE TYPE payslip_line_kind AS ENUM ('earning', 'deduction');

CREATE TABLE payslip (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  idempotency_key        UUID NOT NULL,
  supersedes_payslip_id  UUID,
  source_document_id     UUID,
  pay_month              DATE NOT NULL,
  currency               CHAR(3) NOT NULL REFERENCES currency(code),
  gross_earnings         NUMERIC(20,4) NOT NULL,
  total_deductions       NUMERIC(20,4) NOT NULL,
  net_pay                NUMERIC(20,4) NOT NULL,
  deposit_transaction_id UUID,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_payslip_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_payslip_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT uq_payslip_supersedes UNIQUE (supersedes_payslip_id),
  CONSTRAINT fk_payslip_supersedes_scope FOREIGN KEY (household_id, supersedes_payslip_id)
    REFERENCES payslip (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_document_scope FOREIGN KEY (household_id, source_document_id)
    REFERENCES document (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_deposit_scope FOREIGN KEY (household_id, deposit_transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT payslip_month_is_first_day CHECK (pay_month = date_trunc('month', pay_month)::date),
  CONSTRAINT payslip_amounts_are_valid CHECK (
    gross_earnings > 0 AND total_deductions >= 0 AND net_pay >= 0),
  CONSTRAINT payslip_net_is_gross_less_deductions CHECK (
    net_pay = gross_earnings - total_deductions)
);
CREATE UNIQUE INDEX uq_payslip_deposit ON payslip (deposit_transaction_id)
  WHERE deposit_transaction_id IS NOT NULL;
CREATE INDEX idx_payslip_household_month ON payslip (household_id, pay_month DESC);

CREATE TABLE payslip_line (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  payslip_id   UUID NOT NULL,
  line_no      SMALLINT NOT NULL,
  kind         payslip_line_kind NOT NULL,
  label        TEXT NOT NULL,
  amount       NUMERIC(20,4) NOT NULL,
  CONSTRAINT fk_payslip_line_payslip_scope FOREIGN KEY (household_id, payslip_id)
    REFERENCES payslip (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_payslip_line_no UNIQUE (payslip_id, line_no),
  CONSTRAINT payslip_line_no_is_bounded CHECK (line_no BETWEEN 1 AND 40),
  CONSTRAINT payslip_line_amount_is_positive CHECK (amount > 0),
  CONSTRAINT payslip_line_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 80 AND label = btrim(label))
);
```

- A payslip covers one calendar month, stored as its first day; no pay date is recorded because
  the supported source prints none.
- `record_payslip(p_idempotency_key UUID, p_supersedes_payslip_id UUID, p_source_document_id UUID,
  p_pay_month DATE, p_currency CHAR(3), p_gross_earnings NUMERIC, p_total_deductions NUMERIC,
  p_net_pay NUMERIC, p_line_kinds payslip_line_kind[], p_line_labels TEXT[],
  p_line_amounts NUMERIC[]) RETURNS TABLE (recorded_payslip_id UUID, created BOOLEAN)` is
  `SECURITY DEFINER` with `search_path = public, pg_temp`. In order it: calls `assert_is_owner()`
  and locks the household row; refuses any amount unequal to `round(value, 4)` and line arrays of
  unequal length, with a null, or outside 1–40 entries; returns `(id, false)` when the household
  holds the key with equal supersession, month, currency, amounts and ordered lines (the source
  document is not compared; the first provenance stays), else raises
  `payslip_idempotency_conflict`; for a correction raises `no_data_found` outside the household and
  `payslip_is_linked` for a linked payslip (a second correction fails on `uq_payslip_supersedes`);
  for a new payslip raises `payslip_duplicate` when an active payslip has the same month, currency
  and net pay; then inserts the header and lines numbered in array order.
- A payslip is active when no payslip supersedes it.
- `payslip_totals_agree`: a `DEFERRABLE INITIALLY DEFERRED` constraint trigger after insert on both
  tables. It raises unless earning lines sum to `gross_earnings` and deduction lines (zero when
  none) sum to `total_deductions`. Each call runs `SET CONSTRAINTS payslip_totals_agree DEFERRED`
  before its inserts and `SET CONSTRAINTS payslip_totals_agree IMMEDIATE` after them, so several
  payslips recorded in one transaction are each checked complete. The function is revoked from
  `PUBLIC` and granted to `authenticated`.
- `preserve_payslip()` refuses deletion and any update except `deposit_transaction_id`;
  `preserve_payslip_line()` refuses every update and delete.
- `assert_payslip_deposit()` runs before insert or update of a non-null `deposit_transaction_id`:
  it raises `payslip_is_superseded` for a superseded payslip, and `payslip_deposit_is_eligible`
  unless `v_transaction_reportable` holds that row in the household with `txn_type = 'income'`, the
  payslip's currency and `amount = net_pay`.
- Both tables enable RLS with `household_isolation FOR ALL TO authenticated` on
  `household_id = current_household_id()`; trigger functions are revoked from `PUBLIC`.
- No trigger is added to `transaction`. A linked credit later reclassified, excluded or confirmed as
  a transfer leg reads as `eligible: false`, and the owner unlinks it.

### Commitments (V43)

```sql
CREATE TYPE commitment_cadence AS ENUM ('once', 'monthly', 'annual');

CREATE TABLE commitment (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL REFERENCES household(id),
  idempotency_key UUID NOT NULL,
  account_id      UUID NOT NULL,
  label           TEXT NOT NULL,
  amount          NUMERIC(20,4) NOT NULL,
  currency        CHAR(3) NOT NULL REFERENCES currency(code),
  cadence         commitment_cadence NOT NULL,
  first_date      DATE NOT NULL,
  end_date        DATE,
  archived_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_commitment_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT uq_commitment_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT commitment_amount_is_nonzero CHECK (amount <> 0),
  CONSTRAINT commitment_end_date_agrees CHECK (
    end_date IS NULL OR (cadence <> 'once' AND end_date >= first_date)),
  CONSTRAINT commitment_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 80 AND label = btrim(label))
);
CREATE INDEX idx_commitment_household_account ON commitment (household_id, account_id)
  WHERE archived_at IS NULL;
```

RLS as for payslips; no function or trigger. A commitment is an owner assumption, not an evidenced
fact: it is replaced in place and archiving is terminal. Phase 3.1 may extend this table.

## Payslip PDF import

`api/oneledger/payslip_pdf.py` reads the existing `ingest.words.Page` boundary and is selected by
content only: a `PAY ADVICE FOR <month> <year>` line, a `Payments Amount Deductions Amount` table
header and an `All amounts in <code>` line. Anything else is unsupported. It returns
`PayslipExtraction(supported, extraction_version, months)` of immutable `PayslipMonthDraft(pay_month,
currency, gross_earnings, total_deductions, net_pay, lines, reason)` with
`PayslipLineDraft(kind, label, amount)` and exact `Decimal` values.

1. Every `PAY ADVICE FOR <month> <year>` line starts a month block that runs, across pages, to the
   next such line or the end of the file. The month is that calendar month.
2. Lines between the first table header and the next line beginning `Total` are table lines. Words
   left of the header's `Deductions` word form the payment side and the rest the deduction side.
   Each non-empty side is a label followed by one amount: its last word must be a positive amount
   with at most four decimals (thousands commas allowed); the other words, joined by single spaces,
   are a 1–80 character label.
3. The `Total` line supplies the stated payments total (left side) and deductions total (right).
4. The first line after the `Net Pay Amount` header with a word at or right of that header's `Net`
   word supplies net pay from that column only. Bank, branch and account words are never parsed or
   retained, nor are employee, position or organization lines.
5. `All amounts in <code>` supplies the currency, which must exist in the currency table.
6. Everything else in a block — including the repeated `Element Remarks Amount` deduction detail —
   is ignored and never counted.
7. A month reconciles when payment lines sum to the payments total, deduction lines to the
   deductions total, and payments total less deductions total equals net pay. Otherwise it carries
   one fixed reason: `incomplete_month` (missing month, table, total, net pay or currency),
   `unreadable_line` (a side without one trailing amount, an over-long label, or a repeated header
   inside the table), `too_many_lines`, `unknown_currency`, `payments_total_disagrees`,
   `deductions_total_disagrees` or `net_pay_disagrees`.

Import records each reconciling month through `record_payslip` with
`idempotency_key = uuid5(PAYSLIP_IMPORT_NAMESPACE, canonical)`, where `canonical` joins, with `|`,
the extraction version, household id, pay month, currency, net pay at four places and each ordered
line as `kind:label:amount`. Each month runs in its own savepoint. When at least one month
reconciles, the file is stored through `ingest.storage.store` and one `document` row
(`document_type = 'payslip'`, `parsed_status = 'parsed'`, reused on the same `sha256`) becomes
`source_document_id`, reusing the investment import's document pattern. The password is used for
the request only.

## API boundary

Every route uses `require_owner`, filters by the session household and answers 404 for an unknown
or foreign id. JSON bodies forbid extra fields; dates are canonical `YYYY-MM-DD` and months
`YYYY-MM`; money is a decimal string with at most 16 integer and 4 fractional digits; labels are
NFKC-normalized, whitespace-collapsed and 1–80 characters. Refusals are fixed sentences. Ledger
rows are read through `v_transaction_reportable` or `v_transaction_posting`, never the base table.

### Payslips

| Route | Contract |
| --- | --- |
| `GET /payslips` | `{payslips: PayslipRow[], total}`; newest `payMonth`, then `createdAt`; at most 200; superseded included |
| `POST /payslips` | `PayslipCreate` → `PayslipRow`; 201 created, 200 identical replay |
| `POST /payslips/{payslipId}/corrections` | `PayslipCreate` → replacement `PayslipRow`; 201 or 200 replay |
| `POST /payslips/import` | multipart `file`, optional `password`, `commit` (default false) → `PayslipImportResponse` |
| `GET /payslips/{payslipId}/deposit-candidates` | `{candidates: DepositCandidate[]}`; at most 20; empty when superseded |
| `PUT /payslips/{payslipId}/deposit` | `{transactionId}` → `PayslipRow`; relinking the same credit is 200 |
| `DELETE /payslips/{payslipId}/deposit` | → `PayslipRow` with `deposit: null`; idempotent |

- `PayslipCreate`: `payMonth`, `currency` (`^[A-Z]{3}$`), unsigned `grossEarnings`,
  `totalDeductions`, `netPay`, `lines` (1–40 `{kind: "earning" | "deduction", label, amount}`,
  positive amounts, at least one earning) and `idempotencyKey` (UUID).
- `PayslipRow`: `id`, the create fields except `idempotencyKey`, `lines[]` with `lineNo`,
  `supersedesPayslipId`, `supersededByPayslipId`, `imported` (a source document exists),
  `createdAt` and `deposit: {transactionId, accountId, accountName, bookedDate, amount, eligible} |
  null`.
- `PayslipImportResponse`: `supported` and `months[]` of `{payMonth, currency, grossEarnings,
  totalDeductions, netPay, lines, reason, status, payslipId}`. A preview writes nothing and returns
  `status: null`. A commit returns `imported`, `already_imported` (identical replay),
  `already_recorded` (`payslip_duplicate`) or `not_reconciled` per month; an unsupported file
  returns `supported: false` and no months; an unreadable or wrongly unlocked PDF is 422.
- `DepositCandidate`: `transactionId`, `accountId`, `accountName`, `bookedDate`, `amount`,
  `currency`, `description` (the ledger listing's `_preview_redactor(None)`). Candidates are
  reportable `income` rows in the payslip currency for exactly its net pay, booked from the first
  day of the pay month through the 10th day of the next month, linked to no payslip, oldest first.
  No pay date is printed; a month's salary normally arrives within that month or, when payroll runs
  at month end or slips past a weekend or holiday, in the first days of the next, and the 10th
  stops before a normal following-month credit. A payer who always pays early in the next month can
  surface the previous month's credit too; exact amount narrows the list, linked credits drop out
  and the owner confirms. Linking itself does not require the window.
- 409: key reused with a different request; payslip already corrected; linked payslip corrected;
  exact duplicate; superseded payslip linked; credit linked to another payslip; payslip already
  linked to a different credit. 422: shape, precision or month; lines that do not reconcile;
  ineligible credit.

### Commitments and forecast

| Route | Contract |
| --- | --- |
| `GET /commitments` | `{commitments: CommitmentRow[]}`; `?includeArchived=false`; account order, then `firstDate`, `label` |
| `POST /commitments` | `CommitmentCreate` → `CommitmentRow`; 201, 200 identical replay, 409 conflicting key |
| `PUT /commitments/{commitmentId}` | `CommitmentUpdate` → `CommitmentRow`; full replacement; archived is 409 |
| `POST /commitments/{commitmentId}/archive` | → `CommitmentRow`; sets `archivedAt` once; idempotent |
| `GET /forecast` | `?asOf=` defaults to today → `ForecastResponse`; read-only transaction |

- `CommitmentCreate`: `accountId`, `label`, signed non-zero `amount`, `currency`,
  `cadence: "once" | "monthly" | "annual"`, `firstDate`, `endDate` (null, or on or after `firstDate`
  and never for `once`), `idempotencyKey`. `CommitmentUpdate` omits the key. The account must be
  active and `currency` must equal its currency (422).
- `CommitmentRow`: `id`, `accountId`, `accountName`, `label`, `amount`, `currency`, `cadence`,
  `firstDate`, `endDate`, `archivedAt`, `createdAt`, `updatedAt`.
- `ForecastResponse`: `asOf`; `accounts[]` of `{accountId, accountName, accountClass, currency,
  startBalance, balanceDate, points: [{days, date, balance}], occurrences: [{commitmentId, label,
  date, amount, dueByAsOf}]}`; `excludedAccounts[]` of `{accountId, accountName, reason}`;
  `excludedCommitments[]` of `{commitmentId, label, accountId, reason}`.

### Forecast computation

1. Accounts are the household's active accounts in `GET /accounts` order. One whose valuation
   period at `asOf` (the `GET /dashboard` selection rule) is not `ledger_balance` is excluded as
   `not_ledger_valued`.
2. `startBalance` is `account.current_balance`; `balanceDate` is the date of
   `current_balance_as_of` (the later of the opening date and the latest posting). A null
   `current_balance_as_of` excludes the account as `no_balance_date`, and a `balanceDate` after
   `asOf` excludes it as `balance_after_as_of`.
3. Unarchived commitments apply to included accounts; otherwise one is excluded as `account_closed`
   (inactive account), `account_excluded`, or `currency_mismatch` (currency differs from account's).
4. Occurrence `n ≥ 0` is `firstDate` plus `n` months (monthly) or `12n` months (annual) on
   `firstDate`'s day clamped to the month's last day; `once` has only `firstDate`. Occurrences stop
   after `endDate`, inclusive.
5. An occurrence applies when `balanceDate < date ≤ asOf + 90 days`; postings on the balance date
   are taken to be in the balance. `dueByAsOf` is `date ≤ asOf`.
6. Points are days 30, 60 and 90: `date = asOf + days`; `balance` is `startBalance` plus every
   applied amount dated on or before `date`, exact `Decimal` at four places.
7. No rate is read and no row is written.

## Web workflows

**Salary** (`/salary`) lists payslips and offers entry and import. Entry takes the month, currency,
the stated gross, deductions and net as printed, and payment and deduction lines; it sends the
typed strings with one idempotency key per entry, reused on retry, and never adds amounts. Import
takes a PDF and an optional password, previews each month with its figures and reconciliation
result, and imports the reconciling months on confirmation, then shows each month's outcome. Each
active payslip shows its lines and deposit state: not linked, linked (account and date), or linked
but no longer eligible. **Find net-pay credit** lists candidates; the owner selects one and confirms
**Link**. **Unlink** releases it. **Correct** pre-fills the form and posts a correction; it is
disabled while linked and says to unlink first. Superseded payslips sit in a read-only history.

**Plans** (`/plans`) replaces the disabled navigation placeholder. The forecast shows `asOf` and,
per account, the start balance with its ledger date, the 30/60/90-day balances with dates and the
applied occurrences, marking those due by today as not yet in the ledger; excluded accounts and
commitments show their reasons. An always-visible assumptions list states that balances start from
each account's latest ledger balance and its date; every active commitment occurs in full on its
scheduled date and is unposted when dated after that balance date; nothing else changes (no other
income or spending, interest, card settlement, prices or exchange rates); balances stay in account
currency; and a forecast posts nothing. The commitments manager beneath lists, creates, edits and
archives commitments and refetches the forecast after each write.

Both screens render server strings with `formatMoney`, show loading, empty and error states, leave
401/403 to the workspace, and put no financial value, password or filename in a URL or browser
storage.

## Failure behaviour and privacy

- A refused payslip or correction leaves no header or line; a month that does not reconcile is not
  stored and does not stop other months; a refused link leaves the link as it was. Nothing in this
  phase changes a transaction, balance, classification, transfer or report.
- An account or commitment the forecast cannot apply is listed with its reason, never projected as
  zero or converted. Unsupported cases are absent, not approximated.
- Labels are stored as the owner or payslip states them, bounded only by length; the owner chose
  not to mask them. The import never reads employee, position, organization, bank, branch or
  account text. Code, fixtures, tests, commits, logs and errors use synthetic payslips only; no
  real payslip, filename, amount or label enters the repository or an agent message.

## Required test evidence

Major behavior has tests, per [development cycle](../../development-cycle.md):

- owner success, member refusal and foreign-household isolation with RLS on all three tables;
- excess precision refused and exact string round trips;
- non-reconciling payslips refused with nothing stored, identical replay and conflicting keys;
- a synthetic multi-month PDF (with a detail page and one non-reconciling month) previewing every
  month, importing only the reconciling ones, and re-importing the same file creating nothing;
- deposit eligibility (amount, currency, type, transfer leg, superseded, linked elsewhere),
  candidate window edges and `eligible: false` on read, with dashboard flows unchanged by linking;
- forecast month-end and 29 February clamping, inclusive end date, exclusive balance-date window,
  exact 30/60/90-day sums, every exclusion reason, and no row changed by `GET /forecast`.

## Acceptance

Through the web application: a reconciling payslip stores exactly and a non-reconciling one stores
nothing; a multi-month payslip PDF imports its reconciling months once however often it is
imported; net pay links to one eligible credit and unlinks with income, spending and net worth
unchanged; a correction supersedes without editing the original; adding, editing or archiving a
commitment moves its account's 30/60/90-day balances exactly; exclusions, balance dates and
assumptions are visible; and the forecast posts nothing. Owner checks with real payslips run through
the application and record only structural pass/fail counts.

## Explicit non-goals

Scanned payslips and payslip layouts other than the supported monthly pay advice (project register);
pay dates; employer-contribution, reimbursement, code or recurrence line attributes; withdrawing a
payslip without a replacement; compensation versus cash, bonuses and increments (Phase 4.2);
recurring discovery or commitments inferred from ledger or payslips (Phase 4.1); commitment
certainty, source, settlement and next-occurrence lifecycle (Phase 3.1); lowest balance, shortfalls,
card settlement and monthly review (Phase 3.2); per-currency or cross-currency projection; growth,
inflation or scenarios (Phase 5.5); valuing holdings or snapshots in a forecast; and any posting,
payment or automatic ledger write.

## Documents the implementation must reconcile

`docs/superpowers/schema.md` (V42, V43; retire the planned `0017` payslip shapes),
`docs/design/data-model.html` (entity map, semantics, migration list),
`docs/design/api-contracts.html` (twelve routes), `docs/design/statement-ingestion.html` (the payslip
adapter), `docs/operations/security-and-privacy.html` (payslip content read and never read),
`docs/operations/testing-and-data-quality.html` (Phase 1.7 gate),
`docs/product/ui-information-architecture.html` (Plans in Task 2), `docs/product/prd.html` §5.16
(payslip extraction moves from Phase 4.2 to 1.7), and
`docs/superpowers/progress.md` for live state only.
