# Schema Reference — the complete DDL

> **This is the table reference, not the migrations.** It carries every table, column, type and
> constraint, grouped by the migration that creates it. It deliberately does **not** carry triggers,
> functions, views or RLS policies — those live in `db/migrations/` and are specified in
> [data-model.html](../design/data-model.html), which is the readable description of what each
> entity is and why.
>
> So a migration having more than this file shows is expected. A migration *disagreeing* with it is
> not: a change to a table edits the migration and this file in the same commit.

**Product-review scope (2026-09-06):** V1–V5 are the completed Phase 0 schema work. Later
sections are reference designs, not a claim that every accepted capability already has a table.
[Finance OS requirements](../product/finance-os.html) govern household outflows, consolidated
attribution, commitments, actions, reservations, goals and protection records. In particular, the
unapplied `0015` attribution shape must be reconciled with F03 before implementation. Update the
relevant reference sections alongside each feature's forward migration; do not rewrite V1–V4.

Applied migrations use their Flyway names. V1–V5 are Phase 0. V6–V8 are Phase 1 M1a: the
current-balance derivation, household configuration, and the account inventory. V9–V11 are
M1b: categories, merchants and the seed taxonomy; the transaction ledger; and the household's
category overrides. The unapplied reference sequence in this file now begins at `0008`;
deferred foreign keys remain `0018`.

---

## `0001`

### Extensions and reference data

```sql
CREATE TABLE currency (
  code          CHAR(3) PRIMARY KEY,     -- 'AED' 'INR' 'USD' 'EUR' 'GBP'
  name          TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  decimals      SMALLINT NOT NULL DEFAULT 2,
  number_format TEXT NOT NULL DEFAULT 'international',  -- 'international' | 'indian'

  -- a pegged currency needs no rate feed of its own; see multi-currency-and-fx.html §3
  pegged_to     CHAR(3) REFERENCES currency(code),
  peg_rate      NUMERIC(20,10),          -- units of `code` per 1 `pegged_to`

  CONSTRAINT peg_is_complete CHECK ((pegged_to IS NULL) = (peg_rate IS NULL)),
  CONSTRAINT peg_is_not_self CHECK (pegged_to IS DISTINCT FROM code)
);

CREATE TABLE institution (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id     UUID REFERENCES household(id),   -- NULL = system seed
  name             TEXT NOT NULL,                   -- 'Emirates NBD'
  short_name       TEXT,                            -- 'ENBD'
  country          CHAR(2) NOT NULL,
  institution_type TEXT NOT NULL,   -- bank|card_issuer|broker|fund_house|lender|govt|insurer|employer|exchange_house|other
  website          TEXT,
  logo_url         TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## `0002`

### Identity and tenancy

```sql
CREATE TABLE household (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name               TEXT NOT NULL,
  base_currency      CHAR(3) NOT NULL REFERENCES currency(code),   -- every *_base column
  secondary_currency CHAR(3) NOT NULL REFERENCES currency(code),   -- every *_secondary column
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT reporting_pair_differs CHECK (base_currency <> secondary_currency)
);

-- V7 seeds one household named Household whose reporting pair is the first two seeded
-- currency codes (no code is written in the migration) and one person named Owner.
-- reporting_pair_is_locked() and update_household_configuration(...) live in V7.

CREATE TABLE person (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id        UUID NOT NULL REFERENCES household(id),
  display_name        TEXT NOT NULL,
  is_self             BOOLEAN NOT NULL DEFAULT false,
  tax_residency       CHAR(2),                    -- ISO country, e.g. 'AE' 'IN' 'DE'
  tax_id_last4        TEXT,                       -- reference only, never full: PAN, TIN, SSN…
  national_id_last4   TEXT,                       -- reference only, never full
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stable local identity and session subject. Exactly one row in Phase 1.
CREATE TABLE app_user (
  id            UUID PRIMARY KEY,
  household_id  UUID NOT NULL REFERENCES household(id),
  person_id     UUID NOT NULL REFERENCES person(id),
  email         TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'owner',    -- owner | member (future)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## `0003`

### Accounts

```sql
CREATE TYPE account_type AS ENUM (
  'cash','current','savings','salary','fixed_deposit','recurring_deposit',
  'credit_card',
  'personal_loan','home_loan','auto_loan','education_loan','gold_loan','loan_other',
  'brokerage','mutual_fund_folio','retirement',
  'property','precious_metal','crypto','other_asset',
  'receivable','payable'
);

CREATE TYPE account_class AS ENUM ('asset','liability');

CREATE TABLE account (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  owner_person_id        UUID NOT NULL REFERENCES person(id),
  joint_owner_person_id  UUID REFERENCES person(id),
  institution_id         UUID REFERENCES institution(id),

  name                   TEXT NOT NULL,           -- 'Emirates NBD Current'
  account_type           account_type NOT NULL,
  account_class          account_class NOT NULL,  -- derived at insert, stored for query speed
  account_subtype        TEXT,                    -- 'NRE'|'NRO'|'PPF'|'EPF'|'NPS_T1'|'FCNR'
  currency               CHAR(3) NOT NULL REFERENCES currency(code),
  country                CHAR(2) NOT NULL,
  tax_residency_context  CHAR(2),

  account_number_masked  TEXT,                    -- 'XXXX4821' — display only
  account_number_hash    TEXT,                    -- sha256(normalised full) — statement matching
  iban_masked            TEXT,

  opened_on              DATE,
  closed_on              DATE,
  is_active              BOOLEAN NOT NULL DEFAULT true,
  include_in_net_worth   BOOLEAN NOT NULL DEFAULT true,
  is_liquid              BOOLEAN NOT NULL DEFAULT true,   -- drives Liquid Net Worth

  opening_balance        NUMERIC(20,4) NOT NULL DEFAULT 0,
  opening_balance_date   DATE NOT NULL,
  opening_balance_note   TEXT,                    -- where the figure came from (V8)
  opening_balance_confirmed_at TIMESTAMPTZ,       -- NULL until the owner acknowledges it (V8)
  current_balance        NUMERIC(20,4) NOT NULL,  -- materialised; starts at opening_balance (V6)
  current_balance_as_of  TIMESTAMPTZ,

  -- credit card only
  credit_limit           NUMERIC(20,4),
  statement_day          SMALLINT,      -- day of month
  payment_due_day        SMALLINT,

  -- interest-bearing only
  interest_rate_pct      NUMERIC(9,4),
  maturity_date          DATE,

  display_order          SMALLINT NOT NULL DEFAULT 0,
  color                  TEXT,
  notes                  TEXT,
  tags                   TEXT[],
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT closed_after_opened CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on >= opened_on),
  CONSTRAINT closed_account_has_a_date CHECK (is_active OR closed_on IS NOT NULL),
  CONSTRAINT card_has_limit CHECK (account_type <> 'credit_card' OR credit_limit IS NOT NULL)
);

CREATE INDEX idx_account_household ON account (household_id, is_active);
CREATE UNIQUE INDEX idx_account_number_hash
  ON account (household_id, account_number_hash) WHERE account_number_hash IS NOT NULL;
```

---

## `0004`

### FX rates

```sql
CREATE TABLE fx_rate (
  rate_date          DATE    NOT NULL,
  base_currency      CHAR(3) NOT NULL,
  quote_currency     CHAR(3) NOT NULL,
  rate               NUMERIC(20,10) NOT NULL,   -- 1 base = <rate> quote
  source             TEXT    NOT NULL,          -- ecb|derived_peg|manual|actual_transfer
  is_carried_forward BOOLEAN NOT NULL DEFAULT false,
  carried_from       DATE,
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (rate_date, base_currency, quote_currency),
  CONSTRAINT rate_positive CHECK (rate > 0),
  CONSTRAINT carried_has_source CHECK (NOT is_carried_forward OR carried_from IS NOT NULL)
);

CREATE INDEX idx_fx_lookup ON fx_rate (base_currency, quote_currency, rate_date DESC);
```

---

## `V5` (applied)

### Owner identity and revocable sessions

```sql
ALTER TABLE app_user ADD COLUMN google_subject TEXT;
CREATE UNIQUE INDEX idx_app_user_google_subject
  ON app_user (google_subject) WHERE google_subject IS NOT NULL;

CREATE TABLE app_session (
  id          UUID PRIMARY KEY,
  app_user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  expires_at  TIMESTAMPTZ NOT NULL,
  revoked_at  TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT session_expires_after_creation CHECK (expires_at > created_at)
);
```

---

## `V9` (applied)

### Categories

```sql
CREATE TYPE category_kind   AS ENUM ('income','expense','transfer','investment','debt');
CREATE TYPE category_bucket AS ENUM ('need','want','investment','debt_service','income','ignore');

CREATE TABLE category (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID REFERENCES household(id),      -- NULL = system seed
  parent_id    UUID REFERENCES category(id),
  name         TEXT NOT NULL,
  slug         TEXT NOT NULL,
  kind         category_kind   NOT NULL,
  bucket       category_bucket NOT NULL,
  icon         TEXT,
  color        TEXT,
  sort_order   SMALLINT NOT NULL DEFAULT 0,
  is_system    BOOLEAN NOT NULL DEFAULT false,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  -- The two-level depth limit is a trigger in V9, not a CHECK: a subquery in a CHECK is not
  -- valid PostgreSQL (data-model.html records the same). It rejects a child of a child and
  -- re-parenting a category that has children.
);

CREATE UNIQUE INDEX category_system_slug    ON category (slug) WHERE household_id IS NULL;
CREATE UNIQUE INDEX category_household_slug ON category (household_id, slug)
  WHERE household_id IS NOT NULL;
```

V9 also seeds the classification §3 taxonomy as system rows (`household_id IS NULL`,
`is_system`), idempotently via the system-slug index; a child starts with its parent's kind and
bucket as the editable default (ADR-0008). Merchant seed rows arrive with M4's rule packs.

### Merchants

```sql
CREATE TABLE merchant (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id        UUID REFERENCES household(id),   -- NULL = system seed
  canonical_name      TEXT NOT NULL,                   -- 'Starbucks'
  display_name        TEXT,
  default_category_id UUID REFERENCES category(id),
  country             CHAR(2),
  merchant_type       TEXT,
  logo_url            TEXT,
  is_system           BOOLEAN NOT NULL DEFAULT false,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE pattern_type AS ENUM ('exact','prefix','contains','regex');

CREATE TABLE merchant_alias (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  merchant_id  UUID NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
  household_id UUID REFERENCES household(id),
  pattern      TEXT NOT NULL,          -- 'STARBUCKS'
  pattern_kind pattern_type NOT NULL DEFAULT 'contains',
  priority     SMALLINT NOT NULL DEFAULT 100,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  times_matched INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## `V10` (applied)

### The transaction ledger

Applied with one widening of the `0018` deferred-foreign-key pattern: besides `transfer_id` and
its siblings, the references to `document` (`source_document_id`, `receipt_document_id`),
`import_batch`, `statement_layout` and `classification_rule` are bare `UUID` columns in V10,
because those tables arrive with M2 and M4. The `0018` rule is per target, not per milestone: V12
adds the four foreign keys whose targets it creates (`document`, `import_batch`,
`statement_layout`); `classification_rule_id` stays bare until M4 creates that table and adds
its foreign key. The `REFERENCES` clauses below on those columns are the target shape, not yet
constraints.

```sql
CREATE TYPE txn_type AS ENUM (
  'income','expense','fee','interest','tax','refund',
  'transfer_in','transfer_out',
  'investment_buy','investment_sell','investment_income',
  'loan_disbursement','loan_repayment',
  'adjustment','opening_balance'
);

CREATE TYPE payment_method AS ENUM (
  'card','contactless','atm','cash','cheque','upi','neft','imps','rtgs','wire',
  'direct_debit','standing_instruction','wallet','online','other'
);

CREATE TYPE txn_source AS ENUM ('statement_import','manual','api','derived');

CREATE TYPE classified_by AS ENUM (
  'user_manual','user_rule','merchant_alias','recurring_series',
  'prior_match','seed_rule','unclassified','ai'   -- 'ai' reserved for V2, unused
);

CREATE TABLE transaction (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  account_id                UUID NOT NULL REFERENCES account(id),

  -- when
  booked_date               DATE NOT NULL,
  value_date                DATE,

  -- what the bank said, verbatim and never edited
  description_raw           TEXT NOT NULL,
  description_clean         TEXT,
  reference_raw             TEXT,

  -- money: immutable source of truth
  amount                    NUMERIC(20,4) NOT NULL,
  currency                  CHAR(3) NOT NULL REFERENCES currency(code),

  -- original foreign amount, when the bank converted for you
  original_amount           NUMERIC(20,4),
  original_currency         CHAR(3),

  -- frozen translations (see 04-multi-currency-and-fx.md §2)
  fx_rate_date              DATE NOT NULL,
  fx_rate_is_carried        BOOLEAN NOT NULL DEFAULT false,
  fx_rate_to_base           NUMERIC(20,10) NOT NULL,
  fx_rate_to_secondary      NUMERIC(20,10) NOT NULL,
  amount_base               NUMERIC(20,4) NOT NULL,
  amount_secondary          NUMERIC(20,4) NOT NULL,

  -- meaning
  txn_type                  txn_type NOT NULL,
  category_id               UUID REFERENCES category(id),
  merchant_id               UUID REFERENCES merchant(id),
  counterparty_raw          TEXT,

  -- context
  country                   CHAR(2),
  payment_method            payment_method,

  -- links: any non-null link changes how this row is reported
  transfer_id               UUID REFERENCES transfer(id),
  card_statement_id         UUID REFERENCES card_statement(id),
  investment_transaction_id UUID REFERENCES investment_transaction(id),
  loan_id                   UUID REFERENCES loan(id),
  loan_schedule_entry_id    UUID REFERENCES loan_schedule_entry(id),
  recurring_series_id       UUID REFERENCES recurring_series(id),
  parent_transaction_id     UUID REFERENCES transaction(id),   -- see "Splits" below

  -- provenance (Invariant I6)
  source                    txn_source NOT NULL,
  source_document_id        UUID REFERENCES document(id),
  source_page_number        SMALLINT,
  source_row_number         INTEGER,
  import_batch_id           UUID REFERENCES import_batch(id),
  statement_layout_id       UUID REFERENCES statement_layout(id),
  layout_version            SMALLINT,

  -- dedupe
  dedupe_hash               TEXT NOT NULL,

  -- classification provenance
  classified_by             classified_by NOT NULL DEFAULT 'unclassified',
  classification_rule_id    UUID REFERENCES classification_rule(id),
  classification_confidence NUMERIC(4,3),
  is_reviewed               BOOLEAN NOT NULL DEFAULT false,

  -- user
  notes                     TEXT,
  tags                      TEXT[],
  receipt_document_id       UUID REFERENCES document(id),
  is_excluded               BOOLEAN NOT NULL DEFAULT false,

  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT amount_not_zero      CHECK (amount <> 0 OR txn_type = 'adjustment'),
  CONSTRAINT fx_rates_positive    CHECK (fx_rate_to_base > 0 AND fx_rate_to_secondary > 0),
  CONSTRAINT original_pair        CHECK ((original_amount IS NULL) = (original_currency IS NULL)),
  CONSTRAINT transfer_type_agrees CHECK (
    transfer_id IS NULL OR txn_type IN ('transfer_in','transfer_out')),
  -- I6: nothing claims to come from a statement without naming the statement.
  CONSTRAINT import_has_document  CHECK (
    source <> 'statement_import' OR source_document_id IS NOT NULL),
  -- The frozen facts reproduce each other on every row: stored amounts derive from stored
  -- rates within one allocated cent, whoever the writer is.
  CONSTRAINT frozen_amounts_derive CHECK (
    abs(amount_base - round(amount * fx_rate_to_base, 4)) <= 0.0001
    AND abs(amount_secondary - round(amount * fx_rate_to_secondary, 4)) <= 0.0001),
  -- I7 as a shape: no rate from the future, and carried means exactly "from an earlier date".
  CONSTRAINT carried_rate_dates CHECK (
    fx_rate_date <= booked_date
    AND (fx_rate_is_carried = (fx_rate_date < booked_date)))
);

CREATE UNIQUE INDEX idx_txn_dedupe   ON transaction (household_id, account_id, dedupe_hash);
CREATE INDEX idx_txn_account_date    ON transaction (account_id, booked_date DESC);
CREATE INDEX idx_txn_household_date  ON transaction (household_id, booked_date DESC);
CREATE INDEX idx_txn_category        ON transaction (category_id, booked_date DESC);
CREATE INDEX idx_txn_merchant        ON transaction (merchant_id, booked_date DESC);
CREATE INDEX idx_txn_unreviewed      ON transaction (household_id, booked_date DESC)
                                     WHERE is_reviewed = false;
CREATE INDEX idx_txn_transfer        ON transaction (transfer_id) WHERE transfer_id IS NOT NULL;
CREATE INDEX idx_txn_search          ON transaction
                                     USING gin (to_tsvector('simple', description_raw));
```

---

## `V11` (applied)

### Category overrides

The seeded taxonomy is versioned shared data; the service connects privileged, so a bucket or
activity choice on a seed row is stored beside it and wins on read, never rewriting the seed.

```sql
CREATE TABLE category_override (
  household_id UUID NOT NULL REFERENCES household(id),
  category_id  UUID NOT NULL REFERENCES category(id) ON DELETE CASCADE,
  bucket       category_bucket,
  is_active    BOOLEAN,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, category_id),
  CONSTRAINT override_overrides_something CHECK (bucket IS NOT NULL OR is_active IS NOT NULL)
);
```

---

## `V12` (applied)

Applied as `db/migrations/V12__documents_and_import.sql`, physically creating `statement_layout`
first — `document.statement_layout_id` and `import_batch.statement_layout_id` both reference it,
so the DDL order below differs from the reading order (documents, then batches, then layouts is
the target shape, matching the order transaction's V10 comment lists them in).

### Documents

```sql
CREATE TYPE document_type AS ENUM (
  'bank_statement','card_statement','payslip','loan_statement','investment_statement',
  'cas','tax_document','insurance_policy','property_document','fd_certificate','receipt','other'
);

CREATE TYPE parse_status AS ENUM ('pending','parsing','parsed','failed','skipped','not_applicable');

CREATE TABLE document (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  institution_id       UUID REFERENCES institution(id),
  account_id           UUID REFERENCES account(id),

  document_type        document_type NOT NULL,
  title                TEXT,
  period_start         DATE,
  period_end           DATE,
  currency             CHAR(3),

  original_filename    TEXT NOT NULL,
  storage_bucket       TEXT NOT NULL,
  storage_path         TEXT NOT NULL,
  mime_type            TEXT NOT NULL,
  byte_size            BIGINT NOT NULL,
  sha256               TEXT NOT NULL,
  page_count           SMALLINT,
  is_password_protected BOOLEAN NOT NULL DEFAULT false,

  parsed_status        parse_status NOT NULL DEFAULT 'pending',
  statement_layout_id  UUID REFERENCES statement_layout(id),
  layout_version       SMALLINT,
  parse_error          TEXT,
  extracted_metadata   JSONB,

  uploaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  uploaded_by          UUID REFERENCES app_user(id),
  tags                 TEXT[],
  notes                TEXT
);

-- Invariant: the same file cannot be imported twice
CREATE UNIQUE INDEX idx_document_checksum ON document (household_id, sha256);
CREATE INDEX idx_document_period ON document (household_id, document_type, period_end DESC);

ALTER TABLE document ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON document
  USING (household_id = current_household_id());
```

`original_filename` is written **masked**: statement filenames carry card numbers
(`security-and-privacy.html` §1), and the unmasked value exists only as a local variable for the
duration of one request (M2 Task 5).

### Import batches

```sql
CREATE TYPE import_status AS ENUM (
  'uploaded','extracting','normalizing','deduping','classifying','matching_transfers',
  'reconciling','awaiting_review','importing','completed','failed','cancelled'
);

CREATE TABLE import_batch (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  document_id               UUID REFERENCES document(id),
  account_id                UUID NOT NULL REFERENCES account(id),

  status                    import_status NOT NULL DEFAULT 'uploaded',
  statement_layout_id       UUID REFERENCES statement_layout(id),
  layout_version            SMALLINT,

  rows_extracted            INTEGER NOT NULL DEFAULT 0,
  rows_new                  INTEGER NOT NULL DEFAULT 0,
  rows_duplicate            INTEGER NOT NULL DEFAULT 0,
  rows_needs_review         INTEGER NOT NULL DEFAULT 0,
  rows_imported             INTEGER NOT NULL DEFAULT 0,
  rows_rejected             INTEGER NOT NULL DEFAULT 0,

  -- reconciliation (Invariant I8)
  declared_opening_balance  NUMERIC(20,4),
  declared_closing_balance  NUMERIC(20,4),
  computed_closing_balance  NUMERIC(20,4),
  reconciliation_status     TEXT
                            CHECK (reconciliation_status IN
                              ('matched','mismatched','running_balance_break','unavailable')),
  reconciliation_delta      NUMERIC(20,4),

  period_start              DATE,
  period_end                DATE,
  started_at                TIMESTAMPTZ,
  completed_at              TIMESTAMPTZ,
  error                     TEXT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE import_batch ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON import_batch
  USING (household_id = current_household_id());
```

`reconciliation_status`'s value set is `matched|mismatched|running_balance_break|unavailable`.
`statement-ingestion.html`'s TypeScript sketch returns `reconciled`; this column's documented
value set wins, extended with `running_balance_break` (M2 plan §7.3).

### Statement layouts

```sql
CREATE TYPE sign_convention AS ENUM (
  'debit_credit_columns',   -- separate Debit and Credit columns
  'signed',                 -- one column, minus sign for debits
  'cr_suffix',              -- one column, 'CR' marks credits
  'balance_delta'           -- derived from consecutive running balances
);

CREATE TABLE statement_layout (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  institution_id         UUID REFERENCES institution(id),
  name                   TEXT NOT NULL,          -- user-chosen, e.g. 'Axis savings'
  version                SMALLINT NOT NULL DEFAULT 1,

  -- detection
  fingerprints           TEXT[] NOT NULL,        -- matched against page 1 text

  -- geometry
  header_row_pattern     TEXT,
  columns                JSONB NOT NULL,         -- [{field, x_min, x_max, label}]

  -- semantics
  date_format            TEXT NOT NULL,          -- 'dd/MM/yyyy' | 'dd-MM-yyyy' | 'dd MMM yyyy'
  sign_convention        sign_convention NOT NULL,
  inverts_sign           BOOLEAN NOT NULL DEFAULT false,   -- credit-card statements
  chronological_order    TEXT NOT NULL DEFAULT 'ascending'
                         CHECK (chronological_order IN ('ascending','descending')),

  -- row assembly
  row_start_rule         TEXT NOT NULL DEFAULT 'has_date_and_amount',
  continuation_rule      TEXT,

  -- exclusions: legend pages, footers, page furniture
  skip_line_patterns     TEXT[] NOT NULL DEFAULT '{}',

  -- balances (Invariant I8)
  opening_balance_pattern TEXT,
  closing_balance_pattern TEXT,
  has_running_balance     BOOLEAN NOT NULL DEFAULT false,

  -- the small, additive vocabulary. See ADR-0012.
  quirks                 TEXT[] NOT NULL DEFAULT '{}',

  -- credit-card header block: limit, statement date, due date, minimum due
  metadata_patterns      JSONB,

  times_used             INTEGER NOT NULL DEFAULT 0,
  last_used_at           TIMESTAMPTZ,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT has_a_balance_anchor CHECK (
    has_running_balance OR closing_balance_pattern IS NOT NULL),
  UNIQUE (household_id, name, version)
);

ALTER TABLE statement_layout ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON statement_layout
  USING (household_id = current_household_id());
```

### The deferred foreign keys this migration completes

V12 creates `document`, `import_batch` and `statement_layout`, so it also adds the `0018`-pattern
foreign keys whose targets are now real. All four columns are entirely NULL on every existing
row, so the `ALTER TABLE` is a pure schema change:

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_source_document  FOREIGN KEY (source_document_id)  REFERENCES document(id),
  ADD CONSTRAINT fk_txn_receipt_document FOREIGN KEY (receipt_document_id) REFERENCES document(id),
  ADD CONSTRAINT fk_txn_import_batch     FOREIGN KEY (import_batch_id)     REFERENCES import_batch(id),
  ADD CONSTRAINT fk_txn_statement_layout FOREIGN KEY (statement_layout_id) REFERENCES statement_layout(id);
```

`classification_rule_id` stays a bare `UUID`; its target table and its foreign key arrive with M4.

---

## `V14` (applied)

Applied as `db/migrations/V14__commit_import_batch.sql`. **It creates no table**, which is why it
has no section below and why diffing this file against the migration list shows nothing for it:
V14 adds two `SECURITY DEFINER` functions, and this document is the table reference.

`commit_import_batch(p_batch_id)` turns one reviewed batch into `transaction` rows and
`assert_account_reconciles(p_account_id, p_declared_closing, p_period_end)` verifies the
account's balance as of the statement's close. They live in the database rather than the service
because the insert and the verification have to succeed or fail together, which no sequence of
application statements can promise across a dropped connection (`api-contracts.html` §3). Both
call `assert_is_owner()` first, and `db/tests/0012` asserts that by the refusal's message rather
than by the fact that something was raised — without the assertion an unauthenticated caller is
still refused, by a NULL comparison two statements away, and refusal by accident is what
ADR-0013 exists to stop being load-bearing.

The balance reads `v_transaction_posting`, not the base table and not
`v_transaction_reportable`: the first would double every split, and the second drops transfers
and adjustments, which move money and so belong in a balance even though they belong in no
spending report.

## `V13` (applied)

Applied as `db/migrations/V13__staged_transactions.sql`. `staged_transaction` is an import's
working copy: rows normalized into the ledger's shape, carrying their proposals, their dedupe
verdict and the parser's original view of each line, none of it visible in any report until
review accepts it into `transaction`.

Two references are deferred exactly as V10 defers its own: `proposed_transfer_kind` is `TEXT`
with a `CHECK` because the `transfer_kind` enum arrives with M5, and `classification_rule_id`
is a bare `UUID` because its target table arrives with M4. Each gains its real type or foreign
key in the migration that creates the target — `0018`'s rule, per target.

`amount_base` beside `household_id` is what makes a staged, uncommitted batch lock the
household's reporting pair: `reporting_pair_is_locked()` (V7) discovers every table carrying
both, and these amounts are already frozen against the current pair. `db/tests/0011` asserts it.

### Staged transactions

```sql
CREATE TABLE staged_transaction (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  import_batch_id      UUID NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  household_id         UUID NOT NULL REFERENCES household(id),
  account_id           UUID NOT NULL REFERENCES account(id),

  raw_row              JSONB NOT NULL,        -- exactly what the parser saw
  source_page_number   SMALLINT,
  source_row_number    INTEGER,

  -- normalized, same shape as transaction
  booked_date          DATE NOT NULL,
  value_date           DATE,
  description_raw      TEXT NOT NULL,         -- never modified: this is the audit record
  description_clean    TEXT,
  reference_raw        TEXT,
  amount               NUMERIC(20,4) NOT NULL,
  currency             CHAR(3) NOT NULL REFERENCES currency(code),
  original_amount      NUMERIC(20,4),
  original_currency    CHAR(3),               -- bare, as transaction's is: a foreign statement
                                              -- currency need not be one this deployment seeded

  -- Frozen against the household's reporting pair, exactly as transaction freezes them. This
  -- is why a staged batch locks the pair: reporting_pair_is_locked() (V7) discovers every
  -- table carrying amount_base beside household_id, and these amounts are already frozen.
  fx_rate_date         DATE NOT NULL,
  fx_rate_is_carried   BOOLEAN NOT NULL DEFAULT false,
  fx_rate_to_base      NUMERIC(20,10) NOT NULL,
  fx_rate_to_secondary NUMERIC(20,10) NOT NULL,
  amount_base          NUMERIC(20,4) NOT NULL,
  amount_secondary     NUMERIC(20,4) NOT NULL,

  -- proposals, all overridable in review
  proposed_txn_type       txn_type,
  proposed_category_id    UUID REFERENCES category(id),
  proposed_merchant_id    UUID REFERENCES merchant(id),
  proposed_payment_method payment_method,
  proposed_country        CHAR(2),
  classified_by           classified_by NOT NULL DEFAULT 'unclassified',
  classification_rule_id  UUID,               -- target arrives with M4, and the FK with it
  classification_confidence NUMERIC(4,3),

  -- transfer candidacy
  proposed_transfer_peer_staged_id UUID REFERENCES staged_transaction(id) ON DELETE SET NULL,
  proposed_transfer_peer_txn_id    UUID REFERENCES transaction(id),
  transfer_match_confidence        NUMERIC(4,3),
  proposed_transfer_kind           TEXT       -- transfer_kind enum arrives with M5
                                   CHECK (proposed_transfer_kind IN
                                     ('internal_same_currency','remittance','card_payment',
                                      'loan_payment','investment_funding','cash_withdrawal',
                                      'other')),

  -- dedupe (statement-ingestion.html section 6)
  dedupe_hash              TEXT NOT NULL,
  duplicate_of_transaction_id UUID REFERENCES transaction(id),
  near_duplicate_of_id     UUID REFERENCES transaction(id),

  status               TEXT NOT NULL DEFAULT 'new'
                       CHECK (status IN ('new','duplicate','near_duplicate','needs_review',
                                         'accepted','rejected')),
  user_note            TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- transaction.txn_type is NOT NULL and its amount_not_zero permits a zero amount only for
  -- 'adjustment'. Review may override a proposed type, so an accepted row is held to both
  -- rules here: a row that can be accepted but never committed is a dead end the database
  -- refuses to represent, rather than one the all-or-nothing commit discovers.
  CONSTRAINT accepted_row_is_committable
    CHECK (
      status <> 'accepted'
      OR (proposed_txn_type IS NOT NULL
          AND (amount <> 0 OR proposed_txn_type = 'adjustment'))),

  -- The ledger's own original_pair rule (V10), enforced where the row is first written: an
  -- original amount without its currency is a number with no meaning, and staging must not
  -- hold a row the commit would reject.
  CONSTRAINT original_pair CHECK ((original_amount IS NULL) = (original_currency IS NULL))
);

CREATE INDEX idx_staged_batch ON staged_transaction (import_batch_id, status);

ALTER TABLE staged_transaction ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON staged_transaction
  USING (household_id = current_household_id());
```

---

## `0009`

### Classification rules

```sql
CREATE TABLE classification_rule (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  name                      TEXT,
  priority                  SMALLINT NOT NULL DEFAULT 100,   -- lower evaluated first
  is_active                 BOOLEAN NOT NULL DEFAULT true,

  -- matchers: all non-null conditions must match
  match_description_pattern TEXT,
  match_pattern_kind        pattern_type DEFAULT 'contains',
  match_account_id          UUID REFERENCES account(id),
  match_currency            CHAR(3),
  match_amount_min          NUMERIC(20,4),
  match_amount_max          NUMERIC(20,4),
  match_amount_exact        NUMERIC(20,4),
  match_direction           TEXT DEFAULT 'any',   -- debit|credit|any
  match_country             CHAR(2),
  match_reference_pattern   TEXT,

  -- outcomes
  set_category_id           UUID REFERENCES category(id),
  set_merchant_id           UUID REFERENCES merchant(id),
  set_txn_type              txn_type,
  set_payment_method        payment_method,
  set_country               CHAR(2),
  set_tags                  TEXT[],
  set_is_transfer_candidate BOOLEAN NOT NULL DEFAULT false,
  set_counterparty_account_id UUID REFERENCES account(id),

  origin                    TEXT NOT NULL,   -- seed|user_created|learned_from_correction
  times_applied             INTEGER NOT NULL DEFAULT 0,
  last_applied_at           TIMESTAMPTZ,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT has_matcher CHECK (num_nonnulls(
    match_description_pattern, match_account_id, match_amount_exact, match_reference_pattern) > 0),
  CONSTRAINT has_outcome CHECK (num_nonnulls(
    set_category_id, set_merchant_id, set_txn_type, set_payment_method) > 0
    OR set_is_transfer_candidate)
);

CREATE INDEX idx_rule_priority ON classification_rule (household_id, priority) WHERE is_active;
```

---

## `0010`

### Transfers

```sql
CREATE TYPE transfer_kind AS ENUM (
  'internal_same_currency','remittance','card_payment','loan_payment',
  'investment_funding','cash_withdrawal','other'
);

CREATE TYPE transfer_status AS ENUM ('candidate','confirmed','rejected');

CREATE TABLE transfer (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),

  source_account_id         UUID NOT NULL REFERENCES account(id),
  destination_account_id    UUID NOT NULL REFERENCES account(id),
  source_transaction_id     UUID REFERENCES transaction(id),
  destination_transaction_id UUID REFERENCES transaction(id),

  source_amount             NUMERIC(20,4) NOT NULL,     -- always positive here
  source_currency           CHAR(3) NOT NULL,
  destination_amount        NUMERIC(20,4) NOT NULL,     -- always positive here
  destination_currency      CHAR(3) NOT NULL,

  transfer_date             DATE NOT NULL,
  settlement_date           DATE,

  -- FX truth (see 04-multi-currency-and-fx.md §5)
  implied_rate              NUMERIC(20,10),
  market_rate               NUMERIC(20,10),
  fx_spread_pct             NUMERIC(9,4),
  fx_spread_cost_source     NUMERIC(20,4),
  explicit_fee_amount       NUMERIC(20,4),
  explicit_fee_currency     CHAR(3),
  total_cost_source         NUMERIC(20,4),

  transfer_kind             transfer_kind NOT NULL,
  provider                  TEXT,                        -- 'Wise' | 'LuLu Exchange' | 'ENBD wire'

  match_confidence          NUMERIC(4,3),
  match_method              TEXT NOT NULL,               -- auto|user_confirmed|user_created
  status                    transfer_status NOT NULL DEFAULT 'candidate',

  notes                     TEXT,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT different_accounts CHECK (source_account_id <> destination_account_id),
  CONSTRAINT amounts_positive   CHECK (source_amount > 0 AND destination_amount > 0),
  CONSTRAINT confirmed_has_legs CHECK (
    status <> 'confirmed'
    OR (source_transaction_id IS NOT NULL AND destination_transaction_id IS NOT NULL))
);

CREATE UNIQUE INDEX idx_transfer_source ON transfer (source_transaction_id)
  WHERE source_transaction_id IS NOT NULL AND status = 'confirmed';
CREATE UNIQUE INDEX idx_transfer_dest   ON transfer (destination_transaction_id)
  WHERE destination_transaction_id IS NOT NULL AND status = 'confirmed';
```

---

## `0011`

### Credit card statements

```sql
CREATE TABLE card_statement (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  account_id             UUID NOT NULL REFERENCES account(id),

  period_start           DATE NOT NULL,
  period_end             DATE NOT NULL,
  statement_date         DATE NOT NULL,
  payment_due_date       DATE NOT NULL,

  currency               CHAR(3) NOT NULL,
  opening_balance        NUMERIC(20,4) NOT NULL,   -- signed
  closing_balance        NUMERIC(20,4) NOT NULL,   -- signed
  statement_balance      NUMERIC(20,4) NOT NULL,   -- positive: what was due
  minimum_due            NUMERIC(20,4),
  total_debits           NUMERIC(20,4),
  total_credits          NUMERIC(20,4),

  credit_limit           NUMERIC(20,4),
  available_credit       NUMERIC(20,4),
  cash_limit             NUMERIC(20,4),

  finance_charges        NUMERIC(20,4) NOT NULL DEFAULT 0,
  late_fee               NUMERIC(20,4) NOT NULL DEFAULT 0,
  other_charges          NUMERIC(20,4) NOT NULL DEFAULT 0,

  reward_points_opening  INTEGER,
  reward_points_earned   INTEGER,
  reward_points_redeemed INTEGER,
  reward_points_closing  INTEGER,

  is_paid_in_full        BOOLEAN,
  paid_on                DATE,
  payment_transaction_id UUID REFERENCES transaction(id),
  amount_paid            NUMERIC(20,4),

  source_document_id     UUID REFERENCES document(id),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT period_valid CHECK (period_end >= period_start),
  CONSTRAINT due_after_statement CHECK (payment_due_date >= statement_date)
);

CREATE UNIQUE INDEX idx_card_stmt_period ON card_statement (account_id, period_end);
```

---

## `0012`

### Investments

```sql
CREATE TYPE security_type AS ENUM (
  'mutual_fund','stock','etf','bond','gold_etf','sovereign_gold_bond','physical_gold',
  'fixed_deposit','recurring_deposit','ppf','epf','nps','reit','crypto','other'
);

CREATE TYPE asset_class AS ENUM (
  'indian_equity','international_equity','debt','gold','cash','real_estate','other'
);

CREATE TABLE security (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID REFERENCES household(id),      -- NULL = shared reference data
  security_type     security_type NOT NULL,
  name              TEXT NOT NULL,
  isin              TEXT,
  provider_code     TEXT,                                -- key into the price feed, e.g. an AMFI scheme code
  ticker            TEXT,
  exchange          TEXT,                                -- NSE|BSE|NASDAQ|NYSE
  currency          CHAR(3) NOT NULL,
  country           CHAR(2) NOT NULL,
  asset_class       asset_class NOT NULL,
  sub_asset_class   TEXT,                                -- large_cap|mid_cap|flexi_cap|gilt...
  fund_house        TEXT,
  expense_ratio_pct NUMERIC(9,4),
  is_active         BOOLEAN NOT NULL DEFAULT true,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_security_isin ON security (isin) WHERE isin IS NOT NULL;
CREATE UNIQUE INDEX idx_security_provider ON security (provider_code) WHERE provider_code IS NOT NULL;

CREATE TABLE holding (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  account_id            UUID NOT NULL REFERENCES account(id),
  security_id           UUID NOT NULL REFERENCES security(id),
  owner_person_id       UUID NOT NULL REFERENCES person(id),
  folio_number          TEXT,

  -- all materialised from investment_transaction
  units                 NUMERIC(28,8) NOT NULL DEFAULT 0,
  average_cost          NUMERIC(20,6),
  invested_amount       NUMERIC(20,4) NOT NULL DEFAULT 0,
  realised_gain         NUMERIC(20,4) NOT NULL DEFAULT 0,
  currency              CHAR(3) NOT NULL,

  current_price         NUMERIC(20,6),
  current_price_date    DATE,
  current_value         NUMERIC(20,4),

  first_purchase_date   DATE,
  last_transaction_date DATE,
  xirr_pct              NUMERIC(9,4),
  xirr_computed_at      TIMESTAMPTZ,

  is_closed             BOOLEAN NOT NULL DEFAULT false,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_holding_unique
  ON holding (account_id, security_id, COALESCE(folio_number,''));

CREATE TYPE investment_txn_kind AS ENUM (
  'buy','sell','sip','switch_in','switch_out','stp_in','stp_out','swp',
  'dividend_payout','dividend_reinvest','bonus','split','rights',
  'redemption','maturity','contribution','interest_credit','fee'
);

CREATE TABLE investment_transaction (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  holding_id           UUID NOT NULL REFERENCES holding(id),
  security_id          UUID NOT NULL REFERENCES security(id),
  account_id           UUID NOT NULL REFERENCES account(id),

  txn_date             DATE NOT NULL,
  txn_kind             investment_txn_kind NOT NULL,

  units                NUMERIC(28,8),          -- signed: + acquire, − dispose
  nav_or_price         NUMERIC(20,6),
  gross_amount         NUMERIC(20,4) NOT NULL, -- signed
  charges              NUMERIC(20,4) NOT NULL DEFAULT 0,   -- brokerage, STT, stamp, exit load
  tax_withheld         NUMERIC(20,4) NOT NULL DEFAULT 0,   -- TDS / STCG at source
  net_amount           NUMERIC(20,4) NOT NULL, -- signed; what hit the bank

  currency             CHAR(3) NOT NULL,
  fx_rate_date         DATE NOT NULL,
  fx_rate_to_base      NUMERIC(20,10) NOT NULL,
  fx_rate_to_secondary NUMERIC(20,10) NOT NULL,
  amount_base          NUMERIC(20,4) NOT NULL,
  amount_secondary     NUMERIC(20,4) NOT NULL,

  cash_transaction_id  UUID REFERENCES transaction(id),   -- the bank debit/credit
  source_document_id   UUID REFERENCES document(id),
  dedupe_hash          TEXT NOT NULL,
  notes                TEXT,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT units_direction CHECK (
    units                IS NULL
    OR (txn_kind IN ('buy','sip','switch_in','stp_in','dividend_reinvest','bonus','contribution')
        AND units >= 0)
    OR (txn_kind IN ('sell','switch_out','stp_out','swp','redemption','maturity')
        AND units <= 0)
    OR txn_kind IN ('split','rights','fee','dividend_payout','interest_credit'))
);

CREATE UNIQUE INDEX idx_inv_txn_dedupe ON investment_transaction (household_id, dedupe_hash);
CREATE INDEX idx_inv_txn_holding ON investment_transaction (holding_id, txn_date);

CREATE TABLE price_snapshot (
  security_id UUID NOT NULL REFERENCES security(id),
  price_date  DATE NOT NULL,
  price       NUMERIC(20,6) NOT NULL,
  currency    CHAR(3) NOT NULL,
  source      TEXT NOT NULL,          -- price_feed id|manual|statement
  fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (security_id, price_date)
);
```

---

## `0013`

### Loans

```sql
CREATE TABLE loan (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  account_id              UUID NOT NULL REFERENCES account(id),   -- the liability account
  lender_institution_id   UUID REFERENCES institution(id),

  loan_type               TEXT NOT NULL,   -- personal|home|auto|education|gold|credit_card_emi
  loan_account_number_masked TEXT,

  original_principal      NUMERIC(20,4) NOT NULL,
  currency                CHAR(3) NOT NULL,
  interest_rate_pct       NUMERIC(9,4) NOT NULL,
  rate_type               TEXT NOT NULL DEFAULT 'fixed',   -- fixed|floating
  benchmark               TEXT,                             -- 'EIBOR 3M' | 'HDFC RPLR'
  spread_pct              NUMERIC(9,4),

  emi_amount              NUMERIC(20,4) NOT NULL,
  emi_day                 SMALLINT,
  start_date              DATE NOT NULL,
  original_end_date       DATE NOT NULL,
  current_end_date        DATE NOT NULL,
  tenure_months           SMALLINT NOT NULL,

  outstanding_principal   NUMERIC(20,4) NOT NULL,
  outstanding_as_of       DATE NOT NULL,
  total_principal_paid    NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_interest_paid     NUMERIC(20,4) NOT NULL DEFAULT 0,
  total_prepaid           NUMERIC(20,4) NOT NULL DEFAULT 0,

  prepayment_penalty_pct  NUMERIC(9,4),
  is_closed               BOOLEAN NOT NULL DEFAULT false,
  closed_on               DATE,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE loan_schedule_entry (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  loan_id               UUID NOT NULL REFERENCES loan(id) ON DELETE CASCADE,
  installment_no        SMALLINT NOT NULL,
  due_date              DATE NOT NULL,
  opening_principal     NUMERIC(20,4) NOT NULL,
  emi_amount            NUMERIC(20,4) NOT NULL,
  principal_component   NUMERIC(20,4) NOT NULL,
  interest_component    NUMERIC(20,4) NOT NULL,
  closing_principal     NUMERIC(20,4) NOT NULL,
  is_projected          BOOLEAN NOT NULL DEFAULT true,
  actual_transaction_id UUID REFERENCES transaction(id),
  actual_paid_date      DATE,
  schedule_version      SMALLINT NOT NULL DEFAULT 1,

  UNIQUE (loan_id, schedule_version, installment_no)
);

CREATE TABLE loan_event (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  loan_id        UUID NOT NULL REFERENCES loan(id) ON DELETE CASCADE,
  event_date     DATE NOT NULL,
  event_type     TEXT NOT NULL,   -- prepayment|rate_change|emi_change|moratorium|restructure|closure
  amount         NUMERIC(20,4),
  new_rate_pct   NUMERIC(9,4),
  new_emi        NUMERIC(20,4),
  transaction_id UUID REFERENCES transaction(id),
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## `0014`

### Valuations

```sql
CREATE TABLE valuation_snapshot (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  account_id           UUID REFERENCES account(id),
  holding_id           UUID REFERENCES holding(id),

  as_of_date           DATE NOT NULL,
  value_native         NUMERIC(20,4) NOT NULL,
  currency             CHAR(3) NOT NULL,

  fx_rate_to_base      NUMERIC(20,10) NOT NULL,      -- frozen at as_of_date
  fx_rate_to_secondary NUMERIC(20,10) NOT NULL,
  value_base           NUMERIC(20,4) NOT NULL,
  value_secondary      NUMERIC(20,4) NOT NULL,

  method               TEXT NOT NULL,   -- manual|market_quote|indexed|statement
  confidence           TEXT NOT NULL DEFAULT 'medium',   -- high|medium|low
  source_note          TEXT,
  source_document_id   UUID REFERENCES document(id),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT one_target CHECK (num_nonnulls(account_id, holding_id) = 1)
);

CREATE UNIQUE INDEX idx_valuation_unique
  ON valuation_snapshot (COALESCE(account_id, holding_id), as_of_date);
```

---

## `0015`

### Net worth

```sql
CREATE TABLE net_worth_snapshot (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  as_of_date             DATE NOT NULL,

  net_worth_base         NUMERIC(20,4) NOT NULL,
  net_worth_secondary    NUMERIC(20,4) NOT NULL,
  total_assets_base      NUMERIC(20,4) NOT NULL,
  total_liabilities_base NUMERIC(20,4) NOT NULL,
  liquid_net_worth_base  NUMERIC(20,4) NOT NULL,

  cash_base              NUMERIC(20,4) NOT NULL DEFAULT 0,
  investments_base       NUMERIC(20,4) NOT NULL DEFAULT 0,
  property_base          NUMERIC(20,4) NOT NULL DEFAULT 0,
  other_assets_base      NUMERIC(20,4) NOT NULL DEFAULT 0,
  loans_base             NUMERIC(20,4) NOT NULL DEFAULT 0,
  credit_card_debt_base  NUMERIC(20,4) NOT NULL DEFAULT 0,

  fx_rates_used          JSONB NOT NULL,   -- {"AED": 0.0441, "USD": 83.3196} → base

  has_stale_valuations   BOOLEAN NOT NULL DEFAULT false,
  is_month_end           BOOLEAN NOT NULL DEFAULT false,
  computed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (household_id, as_of_date)
);

CREATE TABLE net_worth_attribution (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id             UUID NOT NULL REFERENCES household(id),
  period_start             DATE NOT NULL,
  period_end               DATE NOT NULL,

  opening_net_worth_base   NUMERIC(20,4) NOT NULL,
  closing_net_worth_base   NUMERIC(20,4) NOT NULL,
  actual_change_base       NUMERIC(20,4) NOT NULL,

  net_cash_saved_base      NUMERIC(20,4) NOT NULL DEFAULT 0,
  investment_contrib_base  NUMERIC(20,4) NOT NULL DEFAULT 0,
  market_appreciation_base NUMERIC(20,4) NOT NULL DEFAULT 0,
  principal_reduction_base NUMERIC(20,4) NOT NULL DEFAULT 0,
  asset_revaluation_base   NUMERIC(20,4) NOT NULL DEFAULT 0,
  fx_translation_base      NUMERIC(20,4) NOT NULL DEFAULT 0,
  unexplained_base         NUMERIC(20,4) NOT NULL DEFAULT 0,   -- shown, never hidden

  computed_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, period_start, period_end)
);
```

---

## `0016`

### Audit log

```sql
CREATE TABLE audit_log (
  id           BIGSERIAL PRIMARY KEY,
  household_id UUID NOT NULL,
  actor_user_id UUID,
  action       TEXT NOT NULL,        -- create|update|delete|import|export|login|rule_apply
  entity_type  TEXT NOT NULL,
  entity_id    UUID,
  before       JSONB,
  after        JSONB,
  context      JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_entity ON audit_log (entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_time   ON audit_log (household_id, created_at DESC);
```

---

## `0017`

### Phase 2 tables

```sql
CREATE TABLE recurring_series (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID NOT NULL REFERENCES household(id),
  label             TEXT NOT NULL,
  merchant_id       UUID REFERENCES merchant(id),
  category_id       UUID REFERENCES category(id),
  account_id        UUID REFERENCES account(id),
  expected_amount   NUMERIC(20,4),
  currency          CHAR(3),
  cadence           TEXT NOT NULL,   -- weekly|monthly|quarterly|half_yearly|annual|custom
  day_of_month      SMALLINT,
  next_expected_date DATE,
  amount_tolerance_pct NUMERIC(9,4) NOT NULL DEFAULT 10,
  date_tolerance_days  SMALLINT NOT NULL DEFAULT 5,
  status            TEXT NOT NULL DEFAULT 'candidate',   -- candidate|active|paused|ended
  detected_from     TEXT NOT NULL DEFAULT 'auto',
  confidence        NUMERIC(4,3),
  first_seen        DATE,
  last_seen         DATE,
  occurrence_count  SMALLINT NOT NULL DEFAULT 0,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE budget_period (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  period_start DATE NOT NULL,
  period_end   DATE NOT NULL,
  currency     CHAR(3) NOT NULL,
  UNIQUE (household_id, period_start, period_end)
);

CREATE TABLE budget_line (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id     UUID NOT NULL REFERENCES household(id),
  budget_period_id UUID NOT NULL REFERENCES budget_period(id) ON DELETE CASCADE,
  category_id      UUID REFERENCES category(id),
  bucket           category_bucket,
  amount           NUMERIC(20,4) NOT NULL,
  rollover         BOOLEAN NOT NULL DEFAULT false,
  CONSTRAINT one_target CHECK (num_nonnulls(category_id, bucket) = 1)
);

CREATE TABLE payslip (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  person_id              UUID NOT NULL REFERENCES person(id),
  employer_institution_id UUID REFERENCES institution(id),
  pay_period_start       DATE NOT NULL,
  pay_period_end         DATE NOT NULL,
  pay_date               DATE NOT NULL,
  currency               CHAR(3) NOT NULL,
  gross_earnings         NUMERIC(20,4) NOT NULL,
  total_deductions       NUMERIC(20,4) NOT NULL DEFAULT 0,
  net_pay                NUMERIC(20,4) NOT NULL,
  source_document_id     UUID REFERENCES document(id),
  deposit_transaction_id UUID REFERENCES transaction(id),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (person_id, pay_period_start, pay_period_end)
);

-- Line items, not fixed columns: UAE and Indian payslips differ too much for a fixed schema
CREATE TABLE payslip_line (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  payslip_id   UUID NOT NULL REFERENCES payslip(id) ON DELETE CASCADE,
  line_type    TEXT NOT NULL,   -- earning|deduction|employer_contribution|reimbursement
  code         TEXT,
  label        TEXT NOT NULL,   -- 'Basic'|'Housing Allowance'|'PF Employee'|'Gratuity Accrual'
  amount       NUMERIC(20,4) NOT NULL,
  is_recurring BOOLEAN NOT NULL DEFAULT true,
  sort_order   SMALLINT NOT NULL DEFAULT 0
);
```

---

## `0018` — Deferred foreign keys

`transaction` references `transfer`, `card_statement`, `investment_transaction`, `loan`,
`loan_schedule_entry` and `recurring_series`, every one of which is created after it — and
`transfer` references `transaction` back. Those columns are therefore created as bare `UUID` in
`0007` and constrained here, once every target exists.

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_transfer     FOREIGN KEY (transfer_id)       REFERENCES transfer(id),
  ADD CONSTRAINT fk_txn_card_stmt    FOREIGN KEY (card_statement_id) REFERENCES card_statement(id),
  ADD CONSTRAINT fk_txn_inv_txn      FOREIGN KEY (investment_transaction_id)
                                     REFERENCES investment_transaction(id),
  ADD CONSTRAINT fk_txn_loan         FOREIGN KEY (loan_id)           REFERENCES loan(id),
  ADD CONSTRAINT fk_txn_loan_entry   FOREIGN KEY (loan_schedule_entry_id)
                                     REFERENCES loan_schedule_entry(id),
  ADD CONSTRAINT fk_txn_recurring    FOREIGN KEY (recurring_series_id)
                                     REFERENCES recurring_series(id);
```
