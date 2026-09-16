# Schema Reference — the complete DDL

> **This is the table reference, not the migrations.** It carries every table, column, type and
> constraint, grouped by the migration that creates it. It deliberately does **not** carry triggers,
> functions, views or RLS policies — those live in `db/migrations/` and are specified in
> [data-model.html](../design/data-model.html), which is the readable description of what each
> entity is and why.
>
> So a migration having more than this file shows is expected. A migration *disagreeing* with it is
> not: a change to a table edits the migration and this file in the same commit.

Sections marked applied match a Flyway migration. Unnumbered `00NN` sections are reference
designs, not a claim that every accepted capability already has a table.
[Finance OS requirements](../product/finance-os.html) govern household outflows, consolidated
attribution, commitments, actions, reservations, goals and protection records; the unapplied `0015`
attribution shape must be reconciled with F03 before implementation. Update the relevant section
alongside each feature's forward migration.

Applied migrations use their Flyway names. V1–V5 are Phase 0. V6–V8 are Phase 1 M1a: the
current-balance derivation, household configuration, and the account inventory. V9–V11 are
M1b: categories, merchants and the seed taxonomy; the transaction ledger; and the household's
category overrides. The unapplied reference sequence in this file begins at `0010`;
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
```

V28 copied the three former identity columns into `account_identifier`; V29 moved every application
reader/writer to that relation and dropped the columns, their index and their transitional
constraints. No migration claims that a legacy digest contains recoverable plaintext.

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
`statement_layout`); `classification_rule_id` stayed bare until V19 created that table and added
its foreign key. The `REFERENCES` clauses below on those columns are the target shape, not yet
constraints.

```sql
CREATE TYPE txn_type AS ENUM (
  'income','expense','fee','interest','tax','refund',
  'transfer_in','transfer_out',
  'investment_buy','investment_sell','investment_income','investment_fee',
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

  -- frozen translations (see docs/design/multi-currency-and-fx.html §2)
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
  period_is_derived         BOOLEAN,               -- V17: true from the rows, false from the
                                                   -- statement, null for a pre-V17 batch
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
  account_type           account_type,
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

`classification_rule_id` stays a bare `UUID` here; its target table and its foreign key
arrive with V19.

---

## `V22` (applied)

Applied as `db/migrations/V22__statement_layout_usage_is_derived.sql`. **It creates no table.**
It drops the two columns V12 gave `statement_layout` and nothing ever wrote, so the V12 listing
above no longer shows them:

```sql
ALTER TABLE statement_layout
  DROP COLUMN times_used,
  DROP COLUMN last_used_at;
```

How often a layout was used is counted from `import_batch.statement_layout_id` and
`started_at`. That count is right for every batch ever staged, where a counter maintained from
here on would start short by every import before it. The M2 spec's §7 item 16 records the
decision.

---

## `V29` (applied)

Applied as `db/migrations/V29__retire_legacy_account_identity_columns.sql`. The account API and
statement resolver now use `account_identifier` exclusively, so V29 drops
`account.account_number_masked`, `account.account_number_hash`, `account.iban_masked`,
`idx_account_number_hash`, `account_legacy_identity_is_migratable` and
`card_has_no_account_number_hash`. A migrated `legacy_fingerprint` remains readable during the
planned backfill window; new and updated identities use the complete local source value,
normalized exact-match value and safe mask in `account_identifier`.

---

## `V28` (applied)

### Institution aliases and account identifiers

Applied as `db/migrations/V28__institution_and_account_identifiers.sql`. Institution and account
UUIDs remain canonical. The alias relation holds exact normalized PDF branding; the identifier
relation separates the full local source value, deterministic matching and safe display. V28 copies
legacy evidence into the new relation but cannot manufacture a full value from a digest or missing
digits from a mask.

```sql
CREATE TABLE institution_alias (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id     UUID REFERENCES household(id),
  institution_id   UUID NOT NULL REFERENCES institution(id) ON DELETE RESTRICT,
  alias            TEXT NOT NULL,
  alias_normalized TEXT NOT NULL,
  country          CHAR(2) NOT NULL,
  provenance       TEXT NOT NULL,       -- public_seed | owner_confirmed
  is_active        BOOLEAN NOT NULL DEFAULT true,
  deactivated_at   TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT institution_alias_text_is_bounded CHECK (
    length(alias) BETWEEN 1 AND 200 AND octet_length(alias) <= 800
    AND length(alias_normalized) BETWEEN 1 AND 200
    AND octet_length(alias_normalized) <= 800),
  CONSTRAINT institution_alias_provenance_is_scoped CHECK (
    (household_id IS NULL AND provenance = 'public_seed')
    OR (household_id IS NOT NULL AND provenance = 'owner_confirmed')),
  CONSTRAINT institution_alias_lifecycle_agrees CHECK (
    (is_active AND deactivated_at IS NULL)
    OR (NOT is_active AND deactivated_at IS NOT NULL))
);

CREATE UNIQUE INDEX idx_institution_alias_one_active_spelling
  ON institution_alias (household_id, institution_id, country, alias_normalized)
  NULLS NOT DISTINCT WHERE is_active;
CREATE INDEX idx_institution_alias_lookup
  ON institution_alias (country, alias_normalized) WHERE is_active;

CREATE TABLE account_identifier (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  account_id              UUID NOT NULL,
  kind                    TEXT NOT NULL,       -- bank_account | iban | card
  masked_value            TEXT NOT NULL,
  visible_suffix          TEXT,

  full_value                TEXT,
  normalized_value          TEXT,
  legacy_fingerprint        BYTEA,
  legacy_fingerprint_scheme TEXT,              -- legacy_sha256_v1

  provenance              TEXT NOT NULL,
                            -- legacy_migration | owner_entered | statement | statement_backfill
  source_document_id      UUID,
  source_sha256           TEXT,
  source_layout_id        UUID,
  source_layout_version   SMALLINT,
  source_capture_key      TEXT,

  is_active               BOOLEAN NOT NULL DEFAULT true,
  deactivated_at          TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_account_identifier_account_household
    FOREIGN KEY (household_id, account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_account_identifier_document_household
    FOREIGN KEY (household_id, source_document_id)
    REFERENCES document(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_account_identifier_layout_household
    FOREIGN KEY (household_id, source_layout_id)
    REFERENCES statement_layout(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT account_identifier_kind_is_known CHECK (
    kind IN ('bank_account', 'iban', 'card')),
  CONSTRAINT account_identifier_mask_is_bounded CHECK (
    length(masked_value) BETWEEN 1 AND 64 AND octet_length(masked_value) <= 256
    AND (visible_suffix IS NULL OR (
      length(visible_suffix) BETWEEN 1 AND 8 AND octet_length(visible_suffix) <= 32))),
  CONSTRAINT account_identifier_complete_value_is_complete CHECK (
    num_nonnulls(full_value, normalized_value) IN (0, 2)
    AND (full_value IS NULL OR (
      length(full_value) BETWEEN 1 AND 128 AND octet_length(full_value) <= 512
      AND normalized_value IS NOT NULL
      AND length(normalized_value) BETWEEN 1 AND 128
      AND octet_length(normalized_value) <= 512))),
  CONSTRAINT account_identifier_legacy_fingerprint_is_complete CHECK (
    num_nonnulls(legacy_fingerprint, legacy_fingerprint_scheme) IN (0, 2)
    AND (legacy_fingerprint IS NULL OR (
      octet_length(legacy_fingerprint) = 32
      AND legacy_fingerprint_scheme = 'legacy_sha256_v1'
      AND provenance = 'legacy_migration'))),
  CONSTRAINT account_identifier_identity_evidence_is_distinct CHECK (
    normalized_value IS NULL OR legacy_fingerprint IS NULL),
  CONSTRAINT account_identifier_provenance_is_complete CHECK (
    (provenance = 'legacy_migration'
      AND full_value IS NULL AND normalized_value IS NULL
      AND source_document_id IS NULL AND source_sha256 IS NULL
      AND source_layout_id IS NULL AND source_layout_version IS NULL
      AND source_capture_key IS NULL)
    OR (provenance = 'owner_entered'
      AND source_document_id IS NULL AND source_sha256 IS NULL
      AND source_layout_id IS NULL AND source_layout_version IS NULL
      AND source_capture_key IS NULL)
    OR (provenance = 'statement'
      AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[[:xdigit:]]{64}$'
      AND source_layout_id IS NOT NULL
      AND source_layout_version IS NOT NULL AND source_layout_version > 0
      AND source_capture_key IS NOT NULL
      AND length(source_capture_key) BETWEEN 1 AND 80)
    OR (provenance = 'statement_backfill'
      AND source_document_id IS NOT NULL
      AND source_sha256 IS NOT NULL AND source_sha256 ~ '^[[:xdigit:]]{64}$'
      AND source_layout_id IS NOT NULL
      AND source_layout_version IS NOT NULL AND source_layout_version > 0
      AND source_capture_key IS NOT NULL
      AND length(source_capture_key) BETWEEN 1 AND 80)),
  CONSTRAINT account_identifier_lifecycle_agrees CHECK (
    (is_active AND deactivated_at IS NULL)
    OR (NOT is_active AND deactivated_at IS NOT NULL))
);

CREATE UNIQUE INDEX idx_account_identifier_active_normalized_value
  ON account_identifier (household_id, kind, normalized_value)
  WHERE is_active AND normalized_value IS NOT NULL;
CREATE INDEX idx_account_identifier_account
  ON account_identifier (household_id, account_id, is_active);
CREATE INDEX idx_account_identifier_mask_fallback
  ON account_identifier (household_id, kind, visible_suffix) WHERE is_active;

ALTER TABLE account
  ADD CONSTRAINT account_legacy_identity_is_migratable CHECK (
    account_number_hash IS NULL OR (
      account_number_masked IS NOT NULL
      AND account_number_hash ~ '^[[:xdigit:]]{64}$'));
```

V28 also gives `document` and `statement_layout` household-leading candidate keys for the two
composite source foreign keys. `institution_alias` uses the same shared-read/private-write RLS
shape as `institution`; `account_identifier` uses ordinary household isolation. A trigger enforces
the shared/private institution alternative that a composite foreign key cannot express, and
institution tenancy becomes immutable so that scope cannot be invalidated later. The validated
legacy-account constraint makes V28 fail atomically rather than silently omit a pre-existing hash
whose display mask is absent or whose digest shape is unknown.

---

## `V27` (applied)

Applied as `db/migrations/V27__card_accounts_store_mask_only.sql`. Existing credit-card account
hashes are cleared and `card_has_no_account_number_hash` prevents every database writer from
storing another in the retired V3 columns. This historical constraint was superseded by V28 and
V29: current card identities live in `account_identifier`, where a complete statement source is
stored locally and revealable while a source-masked card remains mask-only.

---

## `V26` (applied)

Applied as `db/migrations/V26__restorable_schema_helpers.sql`. The recursive classification
payload guard and the two counterparty identifier helpers carry a function-level
`search_path = pg_catalog, public`. Their unqualified internal calls therefore resolve while
`pg_restore` recreates rows under its deliberately empty caller search path; their signatures and
stored-data rules are unchanged.

---

## `V25` (applied)

Applied as `db/migrations/V25__statement_layout_account_type.sql`. The nullable
`statement_layout.account_type` uses the existing `account_type` enum. It lets an unresolved
statement prefill its account product from the selected layout; null means the document did not
state enough and the owner must choose. Existing layout versions are unchanged.

---

## `V23` (applied)

### Classification evidence, exact previews and immutable decisions

Applied as `db/migrations/V23__classification_decisions.sql`. This is the durable boundary for
classification repair, not the planned general `audit_log`: it stores the evidence version used,
the exact bounded rows previewed, and an append-only outcome per transaction. Task 3 supplies the
authenticated operations that populate and apply it.

```sql
CREATE TABLE classification_knowledge (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, id)
);

CREATE TABLE classification_knowledge_version (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL,
  knowledge_id    UUID NOT NULL,
  version         INTEGER NOT NULL CHECK (version > 0),
  source_kind     TEXT NOT NULL,       -- owner|public_source|statement|model_proposal
  source_url      TEXT,                -- only public_source may carry one
  attribution     TEXT NOT NULL,       -- 1..200 characters
  evidence_note   TEXT NOT NULL,       -- 1..1000 characters
  matcher_version TEXT NOT NULL,       -- 1..100 characters
  scope           JSONB NOT NULL DEFAULT '{}', -- object, at most 4096 serialized bytes
  state           TEXT NOT NULL DEFAULT 'active', -- active|revoked
  verified_at     TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, knowledge_id)
    REFERENCES classification_knowledge(household_id, id) ON DELETE RESTRICT,
  UNIQUE (household_id, knowledge_id, version),
  UNIQUE (household_id, id)
);

CREATE UNIQUE INDEX idx_classification_knowledge_one_active_version
  ON classification_knowledge_version (household_id, knowledge_id) WHERE state = 'active';

CREATE TABLE classification_run (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL,
  knowledge_version_id UUID NOT NULL,
  actor_user_id        UUID NOT NULL,
  state                TEXT NOT NULL DEFAULT 'proposed',
                       -- proposed|applied|rejected|conflict|expired
  reason               TEXT NOT NULL,       -- 1..1000 characters
  result               JSONB NOT NULL DEFAULT '{}', -- object, at most 4096 serialized bytes
  result_reason        TEXT,                -- when present, 1..1000 characters
  expires_at           TIMESTAMPTZ NOT NULL,
  resolved_at          TIMESTAMPTZ,
  idempotency_key      TEXT,                -- when present, 1..200 characters
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, knowledge_version_id)
    REFERENCES classification_knowledge_version(household_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (household_id, actor_user_id)
    REFERENCES app_user(household_id, id) ON DELETE RESTRICT,
  UNIQUE (household_id, id),
  UNIQUE (household_id, id, knowledge_version_id, actor_user_id)
);

CREATE UNIQUE INDEX idx_classification_run_idempotency
  ON classification_run (household_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_classification_run_history
  ON classification_run (household_id, created_at DESC);

CREATE TABLE classification_run_item (
  household_id                    UUID NOT NULL,
  run_id                          UUID NOT NULL,
  transaction_id                  UUID NOT NULL,
  expected_transaction_updated_at TIMESTAMPTZ NOT NULL,
  expected_source_key             TEXT NOT NULL, -- 1..128 characters
  expected_state                  JSONB NOT NULL,
  proposed_state                  JSONB NOT NULL,
  reason                          TEXT NOT NULL, -- 1..1000 characters
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, transaction_id),
  FOREIGN KEY (household_id, run_id)
    REFERENCES classification_run(household_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction(household_id, id) ON DELETE RESTRICT,
  UNIQUE (household_id, run_id, transaction_id)
);

CREATE INDEX idx_classification_run_item_transaction
  ON classification_run_item (household_id, transaction_id, created_at DESC);

CREATE TABLE classification_decision (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL,
  run_id                 UUID NOT NULL,
  transaction_id         UUID NOT NULL,
  knowledge_version_id   UUID NOT NULL,
  actor_user_id          UUID NOT NULL,
  before_state           JSONB NOT NULL,
  after_state            JSONB NOT NULL,
  reason                 TEXT NOT NULL, -- 1..1000 characters
  supersedes_decision_id UUID,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, run_id, knowledge_version_id, actor_user_id)
    REFERENCES classification_run(household_id, id, knowledge_version_id, actor_user_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (household_id, run_id, transaction_id)
    REFERENCES classification_run_item(household_id, run_id, transaction_id) ON DELETE RESTRICT,
  UNIQUE (household_id, transaction_id, id),
  FOREIGN KEY (household_id, transaction_id, supersedes_decision_id)
    REFERENCES classification_decision(household_id, transaction_id, id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX idx_classification_decision_superseded_once
  ON classification_decision (supersedes_decision_id) WHERE supersedes_decision_id IS NOT NULL;
CREATE INDEX idx_classification_decision_history
  ON classification_decision (household_id, transaction_id, created_at DESC);
```

`expected_state`, `proposed_state`, `before_state` and `after_state` are complete JSON objects,
with explicit nulls, containing exactly these derived fields: `txn_type`, `category_id`,
`merchant_id`, `payment_method`, `classified_by`, `classification_rule_id`,
`classification_confidence`, `is_reviewed`, `country`, `tags` and `is_excluded`. A raw description,
reference, source provenance or money field is refused. Category and merchant references admit the
shared system set or the row's household; a rule reference must belong to the household.

The household-leading keys on `app_user` and `transaction` support the composite foreign keys
above. All five new tables carry RLS. A knowledge version's identity and evidence are immutable;
only `active` to `revoked` changes in place, and changed evidence appends a successor version.
Preview items and decisions are immutable; a decision must equal its run item's expected/proposed
state. A correction appends a new decision whose before state equals the prior event's after state
and names it in `supersedes_decision_id`.

---

## `V24` (applied)

### Counterparty identities and role aliases

```sql
CREATE TABLE counterparty (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  kind           TEXT NOT NULL,
                 -- merchant|person|own_account|institution|intermediary|unresolved
  display_name   TEXT NOT NULL, -- 1..200 chars and 800 bytes; no unmasked identifier
  merchant_id    UUID REFERENCES merchant(id) ON DELETE RESTRICT,
  own_account_id UUID REFERENCES account(id) ON DELETE RESTRICT,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  deactivated_at TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (household_id, id),
  FOREIGN KEY (household_id, own_account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT
);

CREATE TABLE counterparty_alias (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  counterparty_id       UUID NOT NULL,
  knowledge_version_id UUID NOT NULL,
  pattern              TEXT NOT NULL, -- 1..500 chars and 2000 bytes; no unmasked identifier
  pattern_kind         pattern_type NOT NULL DEFAULT 'contains',
  role                  TEXT NOT NULL,
                        -- purchase|wallet|processor|transfer|refund|own_account|
                        -- institution|unresolved|other
  priority              SMALLINT NOT NULL DEFAULT 100,
  is_active             BOOLEAN NOT NULL DEFAULT true,
  deactivated_at        TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, counterparty_id)
    REFERENCES counterparty(household_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (household_id, knowledge_version_id)
    REFERENCES classification_knowledge_version(household_id, id) ON DELETE RESTRICT
);
```

Merchant and intermediary identities reuse an existing merchant row; an own-account identity names
an existing account from the same household; people, institutions and unresolved entities do not
misuse either reference. Alias roles must agree with the entity kind. Equal highest-priority aliases
are allowed so the resolver can report ambiguity rather than choosing by UUID order. Both tables are
household-isolated and immutable except for one-way deactivation. Full identifiers are refused across
ASCII, arbitrary formatting and Unicode 17 decimal scripts; the household's alphanumeric tax
identifier shape is refused explicitly, while a masked last four remains valid. Merchant tenancy is
immutable, so a valid household reference cannot become foreign after insertion.

---

## `V21` (applied)

### Card cycles

Applied as `db/migrations/V21__card_statements.sql`, carrying `0011` verbatim — every column,
`period_valid`, `due_after_statement` and the unique `idx_card_stmt_period` on
`(account_id, period_end)`. What follows is what the migration adds beyond that sketch.

**The sign rule, made refusable.** `opening_balance` and `closing_balance` are signed from the
household's side like every balance here (ADR-0010), so a card the owner owes on stores a
negative closing balance; `statement_balance` is the one positive figure — what was due. The
table invites one confusion in particular, the amount due stored as the closing balance, and
ADR-0010 says why that is dangerous: a sign error is internally consistent and therefore
invisible. Two `CHECK`s make it visible:

```sql
CONSTRAINT statement_balance_is_what_was_due CHECK (statement_balance >= 0),
CONSTRAINT owed_is_negative CHECK (closing_balance <= 0 OR statement_balance = 0)
```

A card in credit has nothing due, so a row holding a positive closing balance *and* an amount
due has had its sign swapped.

**A cycle only on a credit card of this household, in its currency.** The foreign key says
only that the account exists. `assert_card_statement_account()` refuses a cycle on any other
account type, on another household's card, or in a currency other than the card's — V10's
shape for the same question on a transaction.

**Row-level security** is a single `household_isolation` policy, as V19 and V20 have. V9
splits reading from writing only because its categories have shared seed rows with no
household, and a cycle always has one.

### The foreign key this migration completes

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_card_stmt FOREIGN KEY (card_statement_id) REFERENCES card_statement(id);
```

Exactly as `0018` reserves it, with no `ON DELETE` clause: a transaction names the cycle it was
billed in.

---

## `V20` (applied)

### Transfers

Applied as `db/migrations/V20__transfers.sql`, and it carries `0010` unchanged — the enums, the
table, its three `CHECK`s and the two partial unique indexes. What follows is what the migration
adds beyond the DDL sketch, since a table reference carries no function, trigger or policy.

**Linking is a function, not three statements in a route.** `transaction.transfer_type_agrees`
(V10) says a row holding a `transfer_id` must also carry `transfer_in` or `transfer_out`, so the
id and the type move in the same `UPDATE` or neither moves. A route setting them separately
would be refused by its own constraint halfway through, leaving one leg linked and the other
not.

```sql
confirm_transfer(p_transfer_id UUID)   -- both legs and the status, one statement set
unlink_transfer(p_transfer_id UUID)    -- releases both legs, status becomes 'rejected'
```

Both are `SECURITY DEFINER`, both call `assert_is_owner()` first, and both declare
`search_path = public, pg_temp` with `pg_temp` explicitly last (V15's reason). `confirm_transfer`
refuses a leg that is not this household's, or not in the account the transfer names, before it
writes anything — the definer bypasses RLS, so that predicate is the isolation rather than a
second opinion on one. Confirming an already-confirmed transfer returns without writing.

`unlink_transfer` restores each leg's type **from its own sign** — `amount > 0 → income`,
otherwise `expense`, the rule `normalize.py` applies to a freshly parsed row — rather than
restoring a remembered type the ledger never stored. The status becomes `rejected`, not
`candidate`: the owner has said this pair is not a transfer, and the matcher must not propose
it again.

Neither function touches money. `assert_transaction_money_immutable()` (V10) would refuse them
if they did; they change only `txn_type`, `transfer_id` and `updated_at` (I1, I5).

### The invariant the two indexes do not carry

`data-model.html` §8: the partial unique indexes stop one *transfer* claiming the same
transaction twice in the same role, but nothing in them stops one *transaction* being the
source of transfer A and the destination of transfer B. Each index is satisfied independently
while `transaction.transfer_id` can point at only one of the two.

```sql
CREATE CONSTRAINT TRIGGER trg_one_transfer_per_leg
  AFTER INSERT OR UPDATE ON transfer
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.status = 'confirmed')
  EXECUTE FUNCTION assert_legs_unclaimed();
```

`assert_legs_unclaimed()` raises if either leg's `transaction.transfer_id` is non-null and
differs from `NEW.id` — the second half being what lets a confirmed transfer be edited over the
legs that name it. `WHEN (NEW.status = 'confirmed')` is what lets candidates overlap freely
while the matcher proposes alternatives.

### What this migration completes and converts

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_transfer
    FOREIGN KEY (transfer_id) REFERENCES transfer(id) ON DELETE SET NULL;
```

`SET NULL`, unlike V19's `RESTRICT` on `classification_rule_id`: deleting a transfer is
unlinking it, and a leg with no `transfer_id` is a complete, correct row that reports as income
or expense again. A rule's id is provenance and cannot be nulled away; a transfer's is a link.

`staged_transaction.proposed_transfer_kind` was `TEXT` with a `CHECK` listing the seven values,
because `transfer_kind` did not exist when V13 was written. The `CHECK` becomes the type; all
seven values still load.

---

## `V19` (applied)

### Classification rules

Applied as `db/migrations/V19__classification_rules.sql`. Step four of the classification
cascade, and the only step the owner writes directly.

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

Two `CHECK`s carry what a rule is: `has_matcher` refuses a rule that would apply to every row,
and `has_outcome` refuses one that would consume a row and leave it unclassified — worse than
not matching, because the cascade stops at the first match. The transfer flag alone satisfies
`has_outcome`; marking a row a transfer candidate is the decision.

Two more the migration adds beyond the sketch above, because both columns are written by more
than one path and a value outside the set would be unreadable rather than merely wrong:
`origin_is_known` (`seed | user_created | learned_from_correction`) and `direction_is_known`
(`debit | credit | any`).

`priority` is ascending and the defaults do the arbitration: a user rule starts at 100 and a
seeded one at 500, so a user rule beats a seed rule without either knowing the other exists.
Nothing reads `origin` to decide precedence.

**There is no shared seed row here**, unlike `category`. A seeded rule belongs to the household
that loaded it, so `household_id` is `NOT NULL` and the policy is a plain equality rather than
V9's `household_id IS NULL OR …`.

`times_applied`, `last_applied_at` and `updated_at` are maintained by the code that applies or
edits a rule, not by a trigger — this schema maintains `updated_at` by trigger on exactly one
table (`account`, inside `set_account_class`).

### The deferred foreign keys this migration completes

V10 and V13 left `classification_rule_id` a bare `UUID` because the target did not exist. Both
columns are entirely NULL on every existing row, so these are pure schema changes:

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_classification_rule
    FOREIGN KEY (classification_rule_id) REFERENCES classification_rule(id) ON DELETE RESTRICT;

ALTER TABLE staged_transaction
  ADD CONSTRAINT fk_staged_classification_rule
    FOREIGN KEY (classification_rule_id) REFERENCES classification_rule(id) ON DELETE RESTRICT;
```

`RESTRICT`, not `SET NULL`: a committed row names the rule that classified it, and nulling the
id on delete would leave `classified_by = 'user_rule'` beside no rule at all — a row asserting
a provenance it can no longer produce (I6). Retiring a rule is `is_active = false`.

---

## `V18` (applied)

Applied as `db/migrations/V18__closing_balance_is_verified_as_of_its_own_last_row.sql`. **It
creates no table** — it replaces `commit_import_batch()` with a body identical to V14's but for
the date it verifies against.

V14 checked the committed batch as of `import_batch.period_end`, on the assumption that a
statement's declared closing is the balance at the close of the period it declares. Slice 1.2's
fourth layout disproves it: that producer books year-end interest and its tax on **1 January**,
one day after the period the statement declares, values both on 31 December inside it, and
carries both in the closing balance it prints. Verified as of `period_end` those rows sit
outside the sum while the declared closing includes them, and the statement is refused — two of
the six statements held, each short by exactly its late rows' total.

V18 verifies as of `GREATEST(max(booked_date) of the batch's accepted rows, period_end)`: the
date the declared closing actually describes. `GREATEST` is what keeps the window from ever
narrowing, because excluding a row the statement lists is the same defect from the other side.
Where every row falls inside its period — the three layouts before this one — the as-of date is
`period_end` and nothing changes, which `db/tests/0016` asserts alongside the new behaviour.

## `V17` (applied)

Applied as `db/migrations/V17__import_batch_records_period_provenance.sql`. **It creates no
table** — it adds `import_batch.period_is_derived`.

V12 stored the period and not how it was arrived at. The pipeline has always known —
`normalize.StatementPeriod` carries `is_derived`, false where the layout captured a period
pattern and true where the bounds came from the rows' own dates — but the flag lived only as
long as the `POST /parse` response. `GET /import/{batch}` is where the period is actually read,
and it had no honest answer: `true` labels a declared period derived, `false` labels a computed
one stated, and Invariant I7 forbids inventing either, so it reported null.

The two are indistinguishable after the fact. A period derived from rows dated 1 and 2 March
looks exactly like one a statement stated as 1–2 March, which is why this is stored rather than
recomputed.

**Nullable on purpose**: a batch staged before V17 recorded no provenance, and a default would
assert one for it. Null reads back as "not recorded".

`period_provenance_needs_a_period` refuses the incoherent half: a batch may hold a period without
recorded provenance — that is the pre-V17 row — but not a provenance without a period, which is a
claim about how it arrived at something it does not have. The same shape as V13's `original_pair`.
`db/tests/0015` asserts both, and that stated, derived and unrecorded stay three distinguishable
answers.

## `V16` (applied)

Applied as `db/migrations/V16__staged_row_number_is_required.sql`. **It creates no table** — it
makes `staged_transaction.source_row_number` `NOT NULL`.

V13 left it nullable while every path that writes it types the value `int` and every path that
reads it requires one: reconciliation names its divergent row by it, and `GET /import/{batch}`
declares it a required integer, so a row carrying none would answer 500 rather than render. A
null was a row nothing could produce and nothing could review. The repair belongs on the column
rather than on the API model — widening the model would have the service promise to render a row
it cannot.

The migration fails loudly rather than backfilling: the only other way to satisfy it is to invent
a row number, and a fabricated provenance figure is what Invariant I6 exists to prevent. A staged
batch is a working copy, so the recovery is to cancel it and re-import the statement.

`transaction.source_row_number` stays nullable — a manually entered transaction comes from no
statement and has no row to name. `db/tests/0014` asserts both halves, so neither is "corrected"
into the other later.

## `V15` (applied)

Applied as `db/migrations/V15__definer_search_path_excludes_pg_temp.sql`. **It creates no table
and changes no behaviour** — it replaces four `SECURITY DEFINER` function definitions from V2, V5
and V7 with bodies identical to the originals, changing only `SET search_path = public` to
`SET search_path = public, pg_temp`.

That is a security fix, not a style one. Postgres searches the session's temporary schema
**first** precisely when `pg_temp` is absent from a function's path, so every unqualified relation
in those bodies was resolvable to a caller-created temp table — read with the definer's
privileges, which here bypass RLS. `current_household_id()` is the authorization boundary every
policy in this schema reads, and a temp `app_user` made it return a household of the caller's
choosing. `db/tests/0013` enforces the rule against `pg_catalog` rather than against a list, so a
definer function added later without `pg_temp` fails on the day it is added.

## `V14` (applied)

Applied as `db/migrations/V14__commit_import_batch.sql`. **It creates no table**, which is why it
has no section below and why diffing this file against the migration list shows nothing for it:
V14 adds two `SECURITY DEFINER` functions, and this document is the table reference.

It also adds three `CHECK` constraints to `staged_transaction`, mirroring the frozen-fact rules
`transaction` already enforces (`fx_rates_positive`, `frozen_amounts_derive`,
`carried_rate_dates`). V13's own comment argues for exactly that — "a row that can be accepted
but never committed is a dead end the database refuses to represent, rather than one the
all-or-nothing commit discovers" — and then covered only `txn_type` and the zero amount. V13 is
applied and Flyway-checksummed, so the rest arrive here.

**Both functions set `search_path = public, pg_temp`, naming `pg_temp` explicitly and last.**
That is load-bearing, not tidiness: Postgres searches the session's temporary schema *first*
precisely when `pg_temp` is absent from the path, so `SET search_path = public` alone leaves
every unqualified relation in a function body resolvable to a caller-created temp table. With
`CREATE TEMP TABLE transaction (LIKE public.transaction)` in the session, a `search_path = public`
version of `commit_import_batch` reports a successful import, marks the batch `completed`, and
writes every row into a temp table that vanishes at end of session — while the balance
verification passes, because `v_transaction_posting` bound its dependency on `public.transaction`
at `CREATE VIEW` time. `db/tests/0012` pins it.

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
  source_row_number    INTEGER NOT NULL,      -- V16: every writer types it, every reader needs it

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

  -- FX truth (see docs/design/multi-currency-and-fx.html §5)
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

## `V30`

### Investment instruments and dated valuations (applied)

`account_valuation_period` records exactly one effective-dated net-worth value source at a time:
`ledger_balance`, `holdings`, or `snapshot`. Liability accounts can use only `ledger_balance`.
Overlaps are rejected transactionally. Existing V29 accounts are backfilled as `ledger_balance`;
the migration never infers holdings or snapshots from an account type without supporting evidence.
New accounts receive a type-appropriate initial period, and later changes go through the
owner-authorized `transition_account_valuation_mode` operation so history is appended, not replaced.
Account edits are guarded in the database: a safe opening-anchor correction moves the initial
period in the same transaction, while a change that would strand a non-ledger mode, relabel a
retained valuation's currency, or place it outside the account lifetime or anchor is refused.
Snapshot insertion takes a conflicting account-row lock, so these checks also hold when an insert
and parent edit race in separate transactions.

`account_identifier.kind` now also admits `folio` and `brokerage_account`. These private kinds use
the canonical `****` plus four-character mask and, when complete evidence exists, a 5–64 character
ASCII-alphanumeric normalized value. The full source spelling remains in the existing local-only
identifier envelope; it is not a `holding` or `security` field.

```sql
CREATE TYPE account_valuation_mode AS ENUM ('ledger_balance', 'holdings', 'snapshot');
CREATE TYPE security_type AS ENUM (
  'mutual_fund', 'equity', 'exchange_traded_fund', 'bond', 'deposit',
  'commodity', 'real_asset', 'alternative', 'other'
);
CREATE TYPE asset_class AS ENUM (
  'equity', 'fixed_income', 'cash', 'commodity', 'real_asset', 'alternative', 'other'
);
CREATE TYPE valuation_method AS ENUM (
  'owner_entered', 'market_quote', 'indexed', 'statement'
);
CREATE TYPE valuation_confidence AS ENUM ('high', 'medium', 'low');

CREATE TABLE account_valuation_period (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  account_id     UUID NOT NULL,
  valuation_mode account_valuation_mode NOT NULL,
  effective_from DATE NOT NULL,
  effective_to   DATE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, account_id) REFERENCES account(household_id, id),
  EXCLUDE USING gist (
    account_id WITH =,
    daterange(effective_from, effective_to, '[]') WITH &&)
);

CREATE TABLE security (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID REFERENCES household(id), -- NULL = shared reference
  security_type   security_type NOT NULL,
  name            TEXT NOT NULL,
  currency        CHAR(3) NOT NULL REFERENCES currency(code),
  country         CHAR(2) NOT NULL,
  asset_class     asset_class NOT NULL,
  sub_asset_class TEXT,
  isin            TEXT,
  ticker          TEXT,
  exchange        TEXT,
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE security_price_source (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID REFERENCES household(id), -- matches security scope
  security_id          UUID NOT NULL REFERENCES security(id),
  source_key           TEXT NOT NULL,
  source_version       TEXT NOT NULL,
  source_security_code TEXT NOT NULL,
  effective_from       DATE NOT NULL,
  effective_to         DATE,
  is_active            BOOLEAN NOT NULL DEFAULT true,
  deactivated_at       TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE price_snapshot (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID REFERENCES household(id), -- matches security/source scope
  security_id       UUID NOT NULL REFERENCES security(id),
  source_mapping_id UUID NOT NULL REFERENCES security_price_source(id),
  price_date        DATE NOT NULL,
  price             NUMERIC(20,6) NOT NULL,
  currency          CHAR(3) NOT NULL REFERENCES currency(code),
  observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_accepted       BOOLEAN NOT NULL DEFAULT false,
  accepted_at       TIMESTAMPTZ
);

CREATE TABLE valuation_snapshot (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  account_id           UUID,  -- V31 drops NOT NULL: exactly one of account_id/holding_id
  holding_id           UUID,  -- added by V31; FOREIGN KEY (household_id, holding_id) → holding
  as_of_date           DATE NOT NULL,
  value_native         NUMERIC(20,4) NOT NULL,
  currency             CHAR(3) NOT NULL REFERENCES currency(code),
  fx_rate_date         DATE NOT NULL,
  fx_rate_to_base      NUMERIC(20,10) NOT NULL,
  fx_rate_to_secondary NUMERIC(20,10) NOT NULL,
  value_base           NUMERIC(20,4) NOT NULL,
  value_secondary      NUMERIC(20,4) NOT NULL,
  method               valuation_method NOT NULL,
  confidence           valuation_confidence NOT NULL,
  source_note          TEXT,
  source_document_id   UUID,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, source_document_id)
    REFERENCES document(household_id, id),
  CHECK (fx_rate_date <= as_of_date),
  CHECK (num_nonnulls(account_id, holding_id) = 1)  -- V31; V31 also replaces the account/date
                                                     -- unique constraint with partial unique
                                                     -- indexes per account and per holding
);
```

ISIN and exchange/ticker identities are unique public instrument keys. Source mappings are
effective-dated, non-overlapping per security and capability, and keep their identity when
deactivated. PostgreSQL exclusion constraints also ensure that one exact public
source/version/code resolves to only one security during any date range, including across
households; non-overlapping reassignment remains possible. Price insertion and mapping closure use
conflicting row locks, and a mapping cannot be shortened before any retained observation. Price
observations are unique by security, date, mapping, and exact price, so a conflicting value is
retained rather than overwritten. Only one observation per security/date may be accepted; an
explicit resolution may change the selection without changing either observation.

Account valuations require `snapshot` mode, the account's currency and lifetime, exact positive FX
rates dated no later than the valuation, and reporting values equal to the native value multiplied
and rounded to four places. Their optional document evidence uses a household-leading composite
foreign key. Price facts and valuation snapshots are immutable. All five tables have RLS:
authenticated owners read shared and household rows where applicable, never another household,
and anonymous reads are empty.

`holding`, investment events, acquisition lots and their materialized-unit invariants are not
present yet; they belong to V31. V30 therefore targets valuations at account level. V31 adds the
holding alternative and exactly-one-target constraint after the holding relation exists.

---

## `V31`

### Immutable investment events, holdings and acquisition lots (applied)

`holding` is the unique household/account/security position. Its exact `current_units`, opening
date and optional closing date are projections: callers cannot edit them, and
`investment_units_from_events` rebuilds the same units from immutable `investment_unit_effect`
rows. A zero-unit position remains as closed history with all events and lots retained.

`investment_transaction` stores the event-kind matrix and exact source facts. Unit direction,
price/cash requirements, frozen reporting amounts and the absence of a generic `adjustment` kind
are database constraints. Imported or owner-entered facts cannot be updated or deleted. A
correction is a new owner-entered event naming the exact event it replaces; rebuilds ignore the
replaced effect without removing either row or its lot.

The owner-authorized `append_investment_transaction` operation is the only insertion boundary. It
locks the account/holding, rejects excess decimal scale before PostgreSQL can round it, verifies
the holdings valuation period and household scope, and writes the event, unit effect, acquisition
lot and holding projection atomically. Task 2 enables purchase, recurring-purchase, sale,
redemption and maturity appends. V37 extends that same operation to cash distributions, interest,
reinvested distributions, standalone charges and withheld tax; later unit actions use the already
constrained vocabulary rather than accepting a meaning-free adjustment.

```sql
CREATE TYPE investment_event_kind AS ENUM (
  'purchase', 'recurring_purchase', 'sale', 'redemption', 'maturity',
  'cash_distribution', 'interest', 'reinvested_distribution',
  'charge', 'withheld_tax',
  'switch_in', 'switch_out', 'systematic_transfer_in', 'systematic_transfer_out',
  'split', 'bonus', 'rights', 'platform_transfer_in', 'platform_transfer_out'
);

CREATE TYPE investment_event_source AS ENUM ('statement', 'owner_entered');

CREATE TABLE holding (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id  UUID NOT NULL REFERENCES household(id),
  account_id    UUID NOT NULL,
  security_id   UUID NOT NULL REFERENCES security(id),
  current_units NUMERIC(28,8) NOT NULL,
  opened_on     DATE NOT NULL,
  closed_on     DATE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, account_id) REFERENCES account(household_id, id),
  UNIQUE (household_id, account_id, security_id),
  CHECK (current_units >= 0)
);

CREATE TABLE investment_transaction (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  account_id              UUID NOT NULL,
  holding_id              UUID NOT NULL,
  security_id             UUID NOT NULL REFERENCES security(id),
  event_date              DATE NOT NULL,
  event_kind              investment_event_kind NOT NULL,
  units                   NUMERIC(28,8),
  unit_ratio              NUMERIC(20,10),
  price                   NUMERIC(20,6),
  gross_amount            NUMERIC(20,4),
  charges                 NUMERIC(20,4) NOT NULL,
  withheld_tax            NUMERIC(20,4) NOT NULL,
  net_amount              NUMERIC(20,4) NOT NULL,
  currency                CHAR(3) NOT NULL REFERENCES currency(code),
  fx_rate_date            DATE,
  fx_rate_to_base         NUMERIC(20,10),
  fx_rate_to_secondary    NUMERIC(20,10),
  net_amount_base         NUMERIC(20,4),
  net_amount_secondary    NUMERIC(20,4),
  source_kind             investment_event_source NOT NULL,
  source_contract         TEXT NOT NULL,
  source_event_id         TEXT,
  dedupe_version          TEXT,
  dedupe_fingerprint      TEXT,
  corrects_transaction_id UUID,
  correction_reason       TEXT,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  counterpart_transaction_id UUID  -- present only for paired switch_in/systematic_transfer_in/
                                    -- platform_transfer_in kinds, added by V40
);

CREATE TABLE investment_unit_effect (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  holding_id                UUID NOT NULL,
  investment_transaction_id UUID NOT NULL,
  effect_date               DATE NOT NULL,
  signed_units              NUMERIC(28,8) NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (investment_transaction_id)
);

CREATE TABLE acquisition_lot (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  holding_id            UUID NOT NULL,
  origin_transaction_id UUID NOT NULL,
  acquisition_date      DATE NOT NULL,
  acquired_units        NUMERIC(28,8) NOT NULL,
  cost_basis_native     NUMERIC(20,4) NOT NULL,
  currency              CHAR(3) NOT NULL REFERENCES currency(code),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  predecessor_lot_id    UUID,  -- added by V40 and reserved: no operation writes it yet; household-
                                -- scoped, any holding (a transfer successor's destination holding)
  FOREIGN KEY (household_id, predecessor_lot_id) REFERENCES acquisition_lot(household_id, id),
  UNIQUE (household_id, id),  -- V40; backs the household-scoped self reference above
  UNIQUE NULLS NOT DISTINCT (origin_transaction_id, predecessor_lot_id)  -- V40 replaces V31's
                                                                          -- single-row-per-origin
                                                                          -- uniqueness
);
```

Every relation has household RLS and household-leading composite references. Source-event IDs or
versioned content fingerprints deduplicate within a household/source contract: an exact replay
returns the existing event, while different facts for the same identity fail without partial
rows. `valuation_snapshot` now has exactly one account or holding target; account snapshots retain
their V30 semantics and holding snapshots require the account's `holdings` period. Parent security
currency, account lifetime/anchor and valuation-period edits are refused whenever they would
relabel or strand retained investment events.

---

## `V32`

### Investment import groups, evidence, checkpoints and reviews (applied)

`investment_import` is the one import parent for an immutable investment document. Its document,
source contract, extraction version and creation identity cannot be relabelled after insertion.
`investment_import_group` partitions that document into independently reviewable atomic units, so
one conflicted switch or holding does not prevent unrelated reconciled groups from proceeding.
Typed event drafts and end-position checkpoints remain separate: a checkpoint carries only the
units, cost or value the source stated and never creates investment history.

```sql
CREATE TYPE investment_import_status AS ENUM (
  'staged', 'awaiting_review', 'partially_committed', 'completed', 'cancelled', 'failed'
);
CREATE TYPE investment_group_status AS ENUM (
  'draft', 'ready', 'blocked', 'committed', 'cancelled'
);
CREATE TYPE investment_draft_status AS ENUM (
  'pending', 'ready', 'duplicate', 'conflict', 'blocked', 'committed'
);
CREATE TYPE investment_checkpoint_status AS ENUM (
  'pending', 'matched', 'mismatched', 'blocked'
);
CREATE TYPE investment_review_reason AS ENUM (
  'unsupported_format', 'unknown_security', 'ambiguous_account',
  'ambiguous_identifier', 'unrecognized_event', 'duplicate_conflict',
  'unit_mismatch', 'cost_mismatch', 'missing_price', 'unmatched_cash',
  'incomplete_lot_allocation', 'stale_valuation'
);
CREATE TYPE investment_review_status AS ENUM ('open', 'resolved');
CREATE TYPE investment_review_resolution_kind AS ENUM (
  'account_resolved', 'security_resolved', 'event_corrected',
  'evidence_confirmed', 'checkpoint_reconciled', 'price_recorded',
  'funding_linked', 'lot_allocated', 'unsupported_acknowledged'
);

CREATE TABLE investment_import (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id       UUID NOT NULL REFERENCES household(id),
  document_id        UUID NOT NULL,
  source_contract    TEXT NOT NULL,
  extraction_version TEXT NOT NULL,
  status             investment_import_status NOT NULL DEFAULT 'staged',
  completed_at       TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, document_id) REFERENCES document(household_id, id),
  UNIQUE (document_id)
);

CREATE TABLE investment_import_group (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  import_id    UUID NOT NULL,
  group_number INTEGER NOT NULL,
  status       investment_group_status NOT NULL DEFAULT 'draft',
  committed_at TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (household_id, import_id)
    REFERENCES investment_import(household_id, id),
  UNIQUE (import_id, group_number)
);

CREATE TABLE investment_event_draft (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  group_id       UUID NOT NULL,
  source_order   INTEGER NOT NULL,
  account_id     UUID,
  security_id    UUID REFERENCES security(id),
  event_date     DATE,
  event_kind     investment_event_kind,
  units          NUMERIC(28,8),
  unit_ratio     NUMERIC(20,10),
  price          NUMERIC(20,6),
  gross_amount   NUMERIC(20,4),
  charges        NUMERIC(20,4),
  withheld_tax   NUMERIC(20,4),
  net_amount     NUMERIC(20,4),
  currency       CHAR(3),
  state          investment_draft_status NOT NULL DEFAULT 'pending',
  committed_transaction_id UUID,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE investment_position_checkpoint (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  group_id              UUID NOT NULL,
  source_order          INTEGER NOT NULL,
  account_id            UUID,
  security_id           UUID REFERENCES security(id),
  holding_id            UUID,
  as_of_date            DATE NOT NULL,
  reported_units        NUMERIC(28,8),
  reported_cost_native  NUMERIC(20,4),
  reported_value_native NUMERIC(20,4),
  currency              CHAR(3),
  declared_unit_scale   SMALLINT,
  reconciliation_status investment_checkpoint_status NOT NULL DEFAULT 'pending',
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE investment_evidence (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  group_id                  UUID NOT NULL,
  document_id               UUID NOT NULL,
  draft_id                  UUID,
  checkpoint_id             UUID,
  investment_transaction_id UUID,
  page_number               SMALLINT,
  row_number                INTEGER,
  source_contract           TEXT NOT NULL,
  extraction_version        TEXT NOT NULL,
  source_event_id           TEXT,
  fingerprint_version       TEXT NOT NULL,
  evidence_fingerprint      TEXT NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(draft_id, checkpoint_id, investment_transaction_id) = 1)
);

CREATE TABLE investment_review_item (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  import_id                 UUID,
  group_id                  UUID,
  draft_id                  UUID,
  checkpoint_id             UUID,
  investment_transaction_id UUID,
  holding_id                UUID,
  reason                    investment_review_reason NOT NULL,
  is_blocking               BOOLEAN GENERATED ALWAYS AS (...) STORED,
  status                    investment_review_status NOT NULL DEFAULT 'open',
  resolution_kind           investment_review_resolution_kind,
  resolution_reference_id   UUID,
  resolved_by               UUID,
  resolved_at               TIMESTAMPTZ,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

All six relations use household-leading foreign keys and household RLS. A draft or checkpoint
review must carry its group and import, so nullable composite keys cannot bypass scope. A matched
checkpoint's resolved account and security must agree with its retained holding. Evidence inserts go
through the owner-authorized `record_investment_evidence` operation; direct inserts, updates and
deletes are refused. A normalized source-event identity is preferred when present, otherwise a
versioned typed fingerprint is used, and neither key includes a filename. Exact replay returns the
existing attachment. Conflicting evidence writes no attachment, creates one append-only
`duplicate_conflict` review item and moves only its group to `blocked`.

Once evidence names a draft or checkpoint, its typed source facts are immutable. Later resolution
may attach resolved account, security or holding identity and advance lifecycle state, but it cannot
silently change the date, units, monetary values, event kind, source order or declared precision
authenticated by the retained fingerprint.

Summary-statement extraction creates checkpoint rows only. Owner-scoped reconciliation serializes
with investment event and lot-allocation writes, compares dated committed unit effects at
`declared_unit_scale`, and derives retained acquisition basis by proportionally deducting exact
allocations for active disposals through the checkpoint date with per-lot four-decimal half-up
rounding. If any relevant disposal is not fully allocated, cost stays pending with the advisory
`incomplete_lot_allocation` reason. A match attaches the holding and marks the checkpoint `matched`;
a discrepancy marks it `mismatched` and appends the checkpoint-scoped `unit_mismatch` or
`cost_mismatch` review. Reported value remains immutable source evidence and is never promoted to
`price_snapshot` or investment history.

The twelve review reasons have database-fixed severity. Eight data-integrity reasons block their
group; missing price, unmatched cash, incomplete lot allocation and stale valuation are advisory.
Review rows cannot be deleted or marked resolved directly. V38's validated operation may close an
event-scoped incomplete-lot review as `event_corrected` only after proving that its retained
resolution reference is the immutable replacement event. The same event/reason identity cannot be
reopened after resolution. Later domain operations must prove their own invariants before closing
other review kinds. A group with any open blocking item cannot be marked ready or committed.

---

## `V33` (applied)

### Investment cash and disposal-lot allocations

V33 replaces the retired one-to-one `transaction.investment_transaction_id` placeholder with two
household-scoped many-to-many relations. `investment_cash_allocation` links an investment event to
an ordinary ledger leaf with one of `funding`, `proceeds`, `cash_distribution` or `fee`, an exact
positive `NUMERIC(20,4)` magnitude, the leaf's prior `txn_type`, and the owner who confirmed it.
`investment_lot_allocation` links a unit disposal to an acquisition-origin lot in the same holding
with exact positive `NUMERIC(28,8)` units and owner provenance.

```sql
CREATE TYPE investment_cash_allocation_role AS ENUM (
  'funding', 'proceeds', 'cash_distribution', 'fee'
);

CREATE TABLE investment_cash_allocation (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  investment_transaction_id UUID NOT NULL,
  cash_transaction_id       UUID NOT NULL,
  role                      investment_cash_allocation_role NOT NULL,
  allocated_amount          NUMERIC(20,4) NOT NULL,
  prior_txn_type            txn_type NOT NULL,
  allocated_by              UUID NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (investment_transaction_id, cash_transaction_id),
  CHECK (allocated_amount > 0)
);

CREATE TABLE investment_lot_allocation (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id            UUID NOT NULL REFERENCES household(id),
  holding_id              UUID NOT NULL,
  disposal_transaction_id UUID NOT NULL,
  acquisition_lot_id      UUID NOT NULL,
  allocated_units         NUMERIC(28,8) NOT NULL,
  allocated_by            UUID NOT NULL,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (disposal_transaction_id, acquisition_lot_id),
  CHECK (allocated_units > 0)
);
```

Both tables have household RLS and household-leading event, ledger, holding, lot and actor foreign
keys. Direct DML is refused. `confirm_investment_cash_allocations` locks and validates the complete
batch atomically: arrays are exact, roles match event kind and cash direction, native currencies
match, no event side is over-allocated, no cash row mixes roles, and every cash leaf is fully
allocated. It then maps the leaf to `investment_buy`, `investment_sell`, `investment_income` or
`investment_fee`; all four stay outside `v_transaction_reportable`. An allocated split child may
carry that derived type while its ordinary sibling keeps the parent type, but a split parent is
never linkable, an allocated leaf must be unlinked before splitting, and every pre-existing split
invariant remains enforced. The child type exception requires both the owning operation context and
an allocation whose role maps exactly to that type; a caller-set flag alone grants nothing.

`unlink_investment_cash_allocations` removes every allocation for one leaf in the same operation and
restores the exact stored prior type. `allocate_investment_lots` accepts only an owner-visible unit
disposal and acquisition lots from its holding, locks both sides, and refuses disposal-side or
origin-lot over-allocation. A lot may precede or share the disposal date but may not postdate it; the
operation never chooses a lot or invents FIFO. V33 refuses a populated legacy
cash-link column before schema changes, recreates the split guard and invoker-security ledger views
without that column, then drops it. `classification_state_is_valid` now admits `investment_fee`.

---

## `V34` (applied)

### Investment funding and correction serialization

V34 makes only the active leaf of an acquisition-correction chain eligible for cash funding. An
allocation insert locks its event and refuses one that already has a correction; a correction insert
locks its original and refuses one that already has cash allocations. Both checks are database
triggers inside the existing atomic operations, so a concurrent correction and confirmation
serialize on the same event row and exactly one may commit. The active replacement remains eligible.

Unlink discovers and locks every referenced investment event in UUID order before it locks and
restores the cash leaf. This matches confirmation's event-then-cash protocol and retains V33's exact
prior-type restoration and owner/household boundary.

---

## `V35` (applied)

### Staged investment split ratios

V35 adds nullable `investment_event_draft.unit_ratio NUMERIC(20,10)`. Pending, blocked and conflict
split drafts may retain source units while review is unresolved. A split may enter `ready`,
`duplicate` or `committed` only after those units are cleared and a positive non-unity ratio is
present; every non-split draft must keep the ratio null. Once evidence is attached, the ratio joins
the draft's immutable typed source facts. Existing V34 rows upgrade with a null ratio.

---

## `V36` (applied)

### Source-scoped investment evidence replay

V36 keeps the owner-authorized `record_investment_evidence` signature unchanged while making a
fingerprint replay explicitly scoped by household, document and source contract. Supporting
household/source-contract indexes cover typed source-event identity and versioned fingerprint
overlap across documents. A typed source-event identity is authoritative when present; only
evidence without one uses the fingerprint fallback. Existing attachments remain unchanged.

---

## `V37` (applied)

### Investment cash lifecycle

V37 admits cash distributions, interest, reinvested distributions, standalone charges and withheld
tax through the existing owner-authorized atomic investment append operation. Cash distributions
and interest retain their exact positive external cash flow; charges and withheld tax retain their
exact negative external cash flow. A reinvested distribution instead creates its evidenced positive
unit effect and acquisition-origin lot with gross amount as cost basis, while all external cash-flow
fields remain absent. Evidence of tax withheld within that reinvestment stays on the same event;
Task 14.2 must surface it once and must not manufacture a second standalone tax event. Cash-only
history can retain a zero-unit holding without manufacturing a unit effect or acquisition lot. The
existing household scope, RLS, immutable facts, append-only
corrections, dedupe, precision and atomic-refusal boundaries remain unchanged.

---

## `V38` (applied)

### Validated investment-event review resolution

V38 adds the owner-authorized `resolve_investment_review_item` operation. Its first supported
transition closes an event-scoped `incomplete_lot_allocation` item as `event_corrected` only when
the referenced immutable event directly corrects the reviewed disposal. The operation retains the
review row and records its resolver, reference and timestamp; direct state mutation and deletion
remain refused. A historical unique event/reason identity prevents an exact event retry from
reopening a resolved question.

---

## `V39` (applied)

### Evidenced disposal-lot review resolution

V39 extends the existing owner-authorized review transition with `lot_allocated`. The reviewed
event itself is the retained resolution reference, and the transition is accepted only when the
active disposal has exact positive allocation rows totalling all disposed units. Missing, partial,
foreign or wrong-event evidence leaves the review open; identical resolution replay retains its
original resolver and timestamp. Allocation rows and event facts remain immutable.

Lot allocation confirmations retain one household-scoped `investment_lot_allocation_decision`
per UUID idempotency key. Only the owner-authorized confirmation operation may create one: the
database derives its actor, request hash, completeness and exact nullable claims, while direct
authenticated insert, update and delete are refused. An incomplete decision has no cost, gain or
remaining-basis claims; a complete decision has exact realised cost and gain, while remaining basis
stays null whenever other active disposal evidence is incomplete. This makes identical replay stable
and conflicting reuse atomic without permitting callers to fabricate a result. Non-empty parallel
allocation arrays, complete unique lot references and positive units at no more than eight decimal
places are validated before either canonical hashing or retained-decision replay.

```sql
CREATE TABLE investment_lot_allocation_decision (
  household_id             UUID NOT NULL REFERENCES household(id),
  idempotency_key          UUID NOT NULL,
  request_hash             TEXT NOT NULL,
  disposal_transaction_id  UUID NOT NULL,
  realised_cost_native     NUMERIC(20,4),
  realised_gain_native     NUMERIC(20,4),
  remaining_basis_native   NUMERIC(20,4),
  allocation_complete      BOOLEAN NOT NULL,
  decided_by               UUID NOT NULL,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, idempotency_key),
  FOREIGN KEY (household_id, disposal_transaction_id)
    REFERENCES investment_transaction(household_id, id),
  FOREIGN KEY (household_id, decided_by) REFERENCES app_user(household_id, id),
  CONSTRAINT investment_lot_decision_hash_is_sha256 CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT investment_lot_decision_claims_match_completion CHECK (
    (NOT allocation_complete AND realised_cost_native IS NULL
      AND realised_gain_native IS NULL AND remaining_basis_native IS NULL)
    OR (allocation_complete AND realised_cost_native IS NOT NULL
      AND realised_gain_native IS NOT NULL)),
  CONSTRAINT investment_lot_decision_basis_is_nonnegative CHECK (
    realised_cost_native >= 0 AND remaining_basis_native >= 0)
);
```

V39 serializes allocation and correction through an event advisory mutex followed by event,
holding, lot and review row locks. Every selected disposal and acquisition origin must still be an
active correction-chain leaf; retained allocation evidence makes either origin ineligible for
correction. A proposal includes the immutable disposal date in its expected state and refuses an
acquisition lot dated after that disposal. Remaining basis includes active acquisition origins and
allocations from active disposals only, applying `ROUND_HALF_UP` at four decimals per lot.

V40 adds the lot lineage read model. No operation writes lineage: Phase 1.6 was re-scoped to
holding-level corporate actions, so per-lot lineage is deferred in the
[project register](project-register.md); the column is reserved.
`acquisition_lot.predecessor_lot_id` is a nullable, household-scoped
self reference spanning any holding, because a platform transfer's successor lives in the destination
holding, not the source: a successor lot keeps its predecessor's `acquisition_date`, and
`(origin_transaction_id, predecessor_lot_id)` is unique with nulls not distinct so one lineage event
creates at most one successor per predecessor. `investment_transaction.counterpart_transaction_id`
is a nullable, household-scoped, unique self reference present exactly for the paired `switch_in`,
`systematic_transfer_in` and `platform_transfer_in` kinds; the future paired operation names the in
leg's out counterpart there. A `BEFORE INSERT` order guard on `investment_transaction` raises
`investment_unit_event_order` when a unit- or lot-affecting row (`units` present, or `event_kind =
'split'`) predates an active `split`/`platform_transfer_in`/`platform_transfer_out` event in its
holding, or corrects an event an active lineage event already fixed (excluding, from both
comparisons, the event a correction itself targets — it is still "active" during its own
replacement's insert). The cash-only exemption requires both the new row and any corrected event to
carry no units and not be a split; exact dedupe replay is exempt because it returns before any
insert. `investment_lot_lineage_available(holding_id, as_of)` is false from the date of an active
split with no successor lot. `investment_open_lots(holding_id, as_of)` returns each lineage chain's
tip lot, its preserved acquisition date, open units and open basis rounded from an exact wide
intermediate product (not PostgreSQL's truncated division-scale heuristic), counting only a lot
whose creating event is active and dated on or before `as_of` and an allocation from an active
disposal dated on or before `as_of`; tip detection spans every lot in the household so a
cross-holding successor is found, and only the destination `holding_id` reports it. It returns
nothing while lineage is unavailable; callers must check that explicitly. `allocate_investment_lots_v33`
counts lot capacity from active disposals only and refuses three cases before writing: a successor
lot for a disposal dated before its creating event, any lot once lineage is unavailable as of the
disposal date, and a predecessor already consumed by another active lineage event (dated on or
before the disposal) even when lineage itself remains available. `confirm_investment_lot_allocation`
keeps V39's unbounded remaining-basis semantics — a later acquisition in the same holding still
contributes — by reading `investment_open_lots` and `investment_lot_lineage_available` as of
`'infinity'::date` rather than the disposal's own date. `guard_correction_unfunded_event` exempts a
corrected split's or transfer out-leg's own predecessor consumption but still refuses once that
event's own successor lots are allocated or consumed by another active lineage event.

---

## `V41` (applied)

### Holding-level unit actions

V41 moves the V37 append body into revoked `append_investment_event_core` (the 23 existing
arguments plus `p_unit_ratio` and `p_counterpart_transaction_id`, both joining the exact-replay
identity tuple); ratio is checked at scale 10 before storage. The unchanged 23-argument
`append_investment_transaction` calls it, now additionally admitting `bonus` and `rights`, and
still refuses `split` and the six paired transfer kinds. `investment_event_kind_fields_agree` now
requires a split to carry a non-zero `units` delta and a positive non-unity `unit_ratio`, with no
price, gross or cash — the delta is a genuine holding-level unit effect, not a null placeholder.

Owner-only `append_investment_split` returns an exact replay before any check; locks the holding;
raises `investment_split_is_latest` when an active unit-affecting event in the holding is dated
after the split (or after the split it corrects, using the corrected event's own date); and writes
`units` as the holding's units excluding any corrected split, times `(ratio − 1)`, raising
`investment_split_units_are_exact` unless that product is exact at scale 8. It creates no lot.

Owner-only `append_investment_unit_transfer` accepts `switch_out`, `systematic_transfer_out` or
`platform_transfer_out`, returns an exact pair replay before any check, locks both accounts in UUID
order, and writes the out leg then the in leg naming it as counterpart, or neither. Switch and
systematic legs share an account, differ in security and carry equal gross value; platform legs
share a security, differ in account and carry opposite units; any other shape raises
`investment_unit_transfer_pair_agrees`. The function has no charges or withheld-tax argument, so a
switch carrying an exit load or stamp duty fails closed by construction. A correction replaces both
legs together, identified from the out leg's `corrects_transaction_id` and the retained in leg's
`counterpart_transaction_id`.

`investment_lot_lineage_available` now also treats an active `switch_in`, `switch_out`,
`systematic_transfer_in`, `systematic_transfer_out`, `platform_transfer_in` or
`platform_transfer_out` leg as unavailable from its date, alongside the existing split rule, so
`investment_open_lots`, `allocate_investment_lots_v33` and checkpoint cost withholding
(`staging.py`) agree on exactly the same holdings and dates. A new `BEFORE INSERT` trigger on
`investment_lot_allocation` independently raises `investment_lot_allocation_requires_cash_disposal`
for a switch/systematic/platform leg or whenever lineage is unavailable as of the disposal's date —
the same invariant `allocate_investment_lots_v33` already enforces before any insert, now also
reachable from a direct write.

`positions.py` widens the cumulative-contribution kinds to include `rights` (a funded acquisition)
and widens the gain-eligible kinds to `purchase`, `recurring_purchase`, `rights`, `bonus` and
`split`; any switch, systematic transfer or platform transfer leg — like any disposal or income
event before it — withholds the simple contribution-based gain estimate for that holding.
`staging.py` distinguishes two causes of checkpoint cost unavailability: a real disposal with
incomplete lot evidence still opens a non-blocking `incomplete_lot_allocation` review and stays
pending, while a holding-level corporate action withholds cost silently — the checkpoint still
matches once its reported units (if any) agree, with no review.

No Phase 1.6 action yet creates a successor lot or selects a transferred lot;
`acquisition_lot.predecessor_lot_id` stays null: per-lot lineage is deferred in the project register.

---

## `V42` (applied)

### Payslips

Phase 1.7 Task 1. A payslip covers one calendar month (stored as its first day; the supported
source prints no pay date) and is immutable except its deposit link.

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

`record_payslip(p_idempotency_key, p_supersedes_payslip_id, p_source_document_id, p_pay_month,
p_currency, p_gross_earnings, p_total_deductions, p_net_pay, p_line_kinds, p_line_labels,
p_line_amounts) RETURNS TABLE (recorded_payslip_id, created)` is the one write path for entry,
correction and import. `SECURITY DEFINER`; calls `assert_is_owner()` and locks the household row;
refuses amounts off scale 4 and line arrays of unequal length, with a null, or outside 1–40
entries; replays an identical idempotency key (supersession, month, currency, amounts and ordered
lines — the source document is not compared) and otherwise raises `payslip_idempotency_conflict`;
for a correction raises `no_data_found` outside the household and `payslip_is_linked` for a linked
payslip (a second correction fails on `uq_payslip_supersedes`); for a new payslip raises
`payslip_duplicate` when an active payslip already shares its month, currency and net pay.
`payslip_totals_agree` is one `DEFERRABLE INITIALLY DEFERRED` constraint trigger (both tables share
the name so one `SET CONSTRAINTS` governs both) that `record_payslip` defers before its inserts and
makes immediate after them, so several payslips recorded in one transaction — an import's per-month
savepoints — are each checked complete on their own. `preserve_payslip` refuses deletion and any
update except `deposit_transaction_id`; `preserve_payslip_line` refuses every update and delete.
`assert_payslip_deposit` runs before a non-null `deposit_transaction_id` is set: it raises
`payslip_is_superseded` for a superseded payslip and `payslip_deposit_is_eligible` unless
`v_transaction_reportable` holds that row in the household with `txn_type = 'income'`, the
payslip's currency and `amount = net_pay`; no trigger touches `transaction` itself, so a linked
credit later reclassified, excluded or confirmed as a transfer leg simply reads `eligible: false`.
Both tables carry `household_isolation FOR ALL TO authenticated`; every trigger function is
revoked from `PUBLIC`.

`api/oneledger/payslip_pdf.py` extracts `PayslipExtraction(supported, extraction_version, months)`
of `PayslipMonthDraft(pay_month, currency, gross_earnings, total_deductions, net_pay, lines,
reason)` from a content-selected pay-advice layout (a `PAY ADVICE FOR <month> <year>` marker, a
side-by-side `Payments Amount Deductions Amount` table ending at a `Total` row, a `Net Pay Amount`
column and an `All amounts in <code>` line); `routes/payslips.py` applies the `unknown_currency`
check against the currency table after extraction, since the adapter has no database access.

---

## `V43` (applied)

### Commitments

Phase 1.7 Task 2. A commitment is an owner assumption, not an evidenced fact: it carries no
`SECURITY DEFINER` write path, is replaced in place by an ordinary owner-scoped `UPDATE`, and
archiving is terminal.

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
```

`household_isolation FOR ALL TO authenticated` as on every other household-scoped table; no
function or trigger. `routes/plans.py` writes directly under explicit `household_id` filters — a
household-row lock precedes every write, `POST /commitments` replays an identical idempotency key
by comparing the stored row's fields and otherwise conflicts, and `PUT` refuses an archived row.
`GET /forecast` opens `SET TRANSACTION READ ONLY` before any other statement and calls
`api/oneledger/forecast.py`'s pure `occurrence_dates`/`project_balances` over each active,
ledger-valued account's `current_balance`/`current_balance_as_of` and its unarchived commitments,
reading the same valuation-period selection as `GET /dashboard`
(`routes/dashboard.VALUATION_PERIOD_FOR_ACCOUNT`, extracted there so both routes choose an
account's mode identically). It writes nothing and reads no other route's data.

---

## `V44` (applied)

### Payslip deduction spending

Phase 1.7 Task 3. A deduction the owner marks as spending becomes a balanced pair of ordinary
manual-posting-rule ledger rows (income Salary / expense chosen category) on the linked credit's
account and date; both tables are plain owner-scoped tables, no `SECURITY DEFINER` write path,
following V43's `commitment` convention.

```sql
ALTER TABLE payslip_line ADD CONSTRAINT uq_payslip_line_household_id UNIQUE (household_id, id);

CREATE TABLE payslip_deduction_rule (
  household_id UUID NOT NULL REFERENCES household(id),
  label        TEXT NOT NULL,
  category_id  UUID NOT NULL REFERENCES category(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, label)
);

CREATE TABLE payslip_line_posting (
  household_id            UUID NOT NULL REFERENCES household(id),
  payslip_line_id         UUID NOT NULL,
  income_transaction_id   UUID NOT NULL,
  expense_transaction_id  UUID NOT NULL,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, payslip_line_id),
  CONSTRAINT fk_payslip_line_posting_line_scope FOREIGN KEY (household_id, payslip_line_id)
    REFERENCES payslip_line (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_line_posting_income_scope FOREIGN KEY (household_id, income_transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_payslip_line_posting_expense_scope
    FOREIGN KEY (household_id, expense_transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT
);
```

A `BEFORE INSERT` trigger, `assert_payslip_line_posting_is_deduction`, refuses a posting whose
line is not a `deduction` line of the same household (the composite foreign key alone cannot reach
`payslip_line.kind`). Both tables carry `household_isolation FOR ALL TO authenticated`.

`routes/payslips.py` writes both legs through `routes/transactions.py::post_ledger_transaction` /
`delete_ledger_transaction` — the same manual-posting helper `create_transaction` itself now calls
— so the freeze rules (household lock, account lifetime/anchor, frozen FX, dedupe hash) apply
identically instead of through a second copy. `PUT /payslips/deduction-rules/{label}` saves a rule
(upsert); linking net pay auto-posts a pair for every deduction line with a matching rule;
`POST /payslips/{payslipId}/lines/{lineNo}/spending` counts one line (saving its rule) and posts
its pair when linked; `DELETE` on the same path stops counting it and removes its label's rule.
Unlinking and superseding a payslip remove every pair it posted; the rule survives so a later
payslip still auto-posts. Every
write is idempotent: a line already paired is left alone by a replayed `POST`, and a line with no
pair is left alone by a replayed `DELETE`.

---

## `V45` (applied)

### History coverage

Phase 2.1 Task 1. `coverage_gap_acknowledgement` is an owner assumption like `commitment` (V43):
no `SECURITY DEFINER` write path, just household-scoped RLS and a composite foreign key. The
history anchor itself (1 January 2024, ADR-0011) is not a column — `api/oneledger/coverage.py`
fixes `HISTORY_ANCHOR` in code so no setting can silently shorten history.

```sql
CREATE TYPE coverage_gap_reason AS ENUM (
  'not_issued', 'unobtainable', 'unsupported_format', 'account_not_in_use');

CREATE TABLE coverage_gap_acknowledgement (
  household_id UUID NOT NULL REFERENCES household(id),
  subject      TEXT NOT NULL CHECK (subject IN ('account', 'payslips')),
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

`household_isolation FOR ALL TO authenticated` as on every other household-scoped table; no
function or trigger. `GET /coverage` computes a month-by-month grid on read from committed
`import_batch` periods (`status = 'completed'`, a non-null period) and active payslips — nothing
is stored beyond the owner's acknowledgements. `PUT /coverage/acknowledgements` upserts one row
per `(subject, account_id, month)`, refusing a month the evidence already reconciles (409) and a
month outside the grid or the account's life (422); `DELETE` is idempotent. `GET /doctor` reads
the same computation for its Coverage section, so the two never drift apart.

---

## `V46` (applied)

### Month-end positions

Phase 2.2 Task 1. `month_end_position` is a recomputable cache of wealth-history evidence, not
history: `api/oneledger/wealth_history.py`'s `refresh` replaces a household's rows in one
transaction, so a late failure leaves the previous refresh's rows intact. No `SECURITY DEFINER`
write path, the same convention as `commitment` (V43) and `coverage_gap_acknowledgement` (V45):
household-scoped RLS and explicit `household_id` filters, enforced by `require_owner` in
`routes/wealth_history.py`.

```sql
CREATE TYPE month_end_position_status AS ENUM (
  'available', 'missing_price', 'missing_rate', 'no_snapshot', 'before_opening');

CREATE TABLE month_end_position (
  household_id        UUID NOT NULL REFERENCES household(id),
  month_end           DATE NOT NULL,
  account_id          UUID NOT NULL,
  holding_id          UUID,
  valuation_mode      account_valuation_mode NOT NULL,
  currency            CHAR(3) NOT NULL REFERENCES currency(code),
  native_value        NUMERIC(20,4),
  evidence_date       DATE,
  rate_to_base        NUMERIC(20,10),
  rate_to_base_date   DATE,
  rate_to_base_carried BOOLEAN,
  rate_to_secondary   NUMERIC(20,10),
  rate_to_secondary_date DATE,
  rate_to_secondary_carried BOOLEAN,
  value_base          NUMERIC(20,4),
  value_secondary     NUMERIC(20,4),
  status              month_end_position_status NOT NULL,
  computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_month_end_position_account FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_month_end_position_holding FOREIGN KEY (household_id, holding_id)
    REFERENCES holding (household_id, id) ON DELETE CASCADE,
  CONSTRAINT month_end_position_values_agree CHECK (
    (status = 'available' AND native_value IS NOT NULL AND value_base IS NOT NULL
      AND value_secondary IS NOT NULL)
    OR (status <> 'available' AND value_base IS NULL AND value_secondary IS NULL))
);
CREATE UNIQUE INDEX uq_month_end_position
  ON month_end_position (household_id, month_end, account_id, holding_id) NULLS NOT DISTINCT;

CREATE TABLE wealth_history_refresh (
  household_id     UUID PRIMARY KEY REFERENCES household(id),
  computed_through DATE NOT NULL,
  computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`household_isolation FOR ALL TO authenticated` on both tables, as on every other
household-scoped table; no function or trigger. A position is one counted account, or one
holding of a counted holdings-mode account, at a month end — wealth history's own lifetime
predicate (`include_in_net_worth AND (opened_on IS NULL OR opened_on <= t) AND (closed_on IS NULL
OR closed_on >= t)`), not the dashboard's `is_active` filter, so a closed account keeps the
months it was open. `wealth_history.refresh(cur, household_id, base, secondary, through)`
computes 31 December 2023 and every month end from `coverage.HISTORY_ANCHOR` through the last
month fully completed as of `through` (`wealth_history.stored_month_ends`), reusing
`investments.positions.read_positions` (a new `lifetime` keyword swaps its account filter for the
predicate above, and `include_closed=True` so a fully sold holding reports zero) and
`current_rate_pair`, and replaces the household's `month_end_position` rows and upserts its one
`wealth_history_refresh` row in the same transaction under the household lock.

`POST /wealth-history/refresh` is owner-only and returns counts. `GET /wealth-history` reads
`wealth_history_refresh` for the household — an empty list before any refresh — then lists every
month end it names, including one with no stored position row (totals `0.0000`, `complete` true,
empty status counts): totals are null on an incomplete month, never a partial sum (fail closed),
and every month in one response carries the same `computedAt`, the refresh's own timestamp, not a
per-month `max(computed_at)`. `GET /wealth-history/{monthEnd}` returns that month's stored
positions with account and security names, rates, dates, carried flags and status. Neither `GET`
route recomputes — both read exactly what the last refresh stored.

---

## `V47` (applied)

### Pending classification knowledge

Applied as `db/migrations/V47__pending_classification_knowledge.sql`. Extends
`classification_knowledge_version.state` from `active`/`revoked` to `active`/`pending`/`revoked`,
so a version created through `POST /merchant-knowledge?pending=true` (the grouped classification
review's own preview-time guess) can sit invisible to every classifier and to
`GET /merchant-knowledge` until a `classification-runs` apply activates it, without a schema change
to `classification_run`/`classification_run_item`/`classification_decision` — those tables already
accept an `active` **or** `pending` knowledge version's preview and apply. The one-active-version
partial unique index is untouched (`WHERE state = 'active'`), so a pending version never contends
with it. `assert_classification_knowledge_version_immutable()` widens to allow exactly three lifecycle
edges: `active`→`revoked` (unchanged), `pending`→`active` (`revoked_at` stays `NULL`) and
`pending`→`revoked`. `classification_evidence_revocation_agrees` widens the same way: `revoked_at` is
`NULL` for `active` or `pending`, and non-`NULL` only for `revoked`.

---

## `V48` (applied)

### Commitment lifecycle

Phase 3.1 Task 1. A commitment's lifecycle over exactly one open occurrence, resolved by linking
a ledger posting (in full or in part), marking it done, or dismissing it with a reason.
`commitment_resolution` follows `payslip`'s convention (V42): a plain owner-scoped table, one
trigger deciding eligibility, one trigger enforcing immutability, no `SECURITY DEFINER` write
path.

```sql
CREATE TYPE commitment_certainty AS ENUM ('confirmed', 'estimated');
CREATE TYPE commitment_resolution_kind AS ENUM ('payment', 'completed', 'dismissed');

ALTER TABLE commitment
  ADD COLUMN certainty commitment_certainty NOT NULL DEFAULT 'confirmed',
  ADD COLUMN next_due_date DATE,
  ADD CONSTRAINT uq_commitment_household_id UNIQUE (household_id, id),
  ADD CONSTRAINT commitment_next_due_in_schedule CHECK (
    next_due_date IS NULL OR (next_due_date >= first_date
                              AND (end_date IS NULL OR next_due_date <= end_date)));

CREATE TABLE commitment_resolution (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  commitment_id  UUID NOT NULL,
  due_date       DATE NOT NULL,
  kind           commitment_resolution_kind NOT NULL,
  closes         BOOLEAN NOT NULL,
  transaction_id UUID,
  reason         TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_commitment_resolution_commitment_scope FOREIGN KEY (household_id, commitment_id)
    REFERENCES commitment (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_commitment_resolution_transaction_scope FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT commitment_resolution_shape CHECK (
       (kind = 'payment'   AND transaction_id IS NOT NULL AND reason IS NULL)
    OR (kind = 'completed' AND transaction_id IS NULL AND closes AND reason IS NULL)
    OR (kind = 'dismissed' AND transaction_id IS NULL AND closes AND reason IS NOT NULL
        AND char_length(reason) BETWEEN 1 AND 200 AND reason = btrim(reason)))
);
CREATE UNIQUE INDEX uq_commitment_resolution_closing
  ON commitment_resolution (commitment_id, due_date) WHERE closes;
CREATE UNIQUE INDEX uq_commitment_resolution_transaction
  ON commitment_resolution (transaction_id) WHERE transaction_id IS NOT NULL;
CREATE INDEX idx_commitment_resolution_commitment
  ON commitment_resolution (household_id, commitment_id, due_date DESC);
```

**Backfill.** Every existing commitment gets `next_due_date` by the default next-due rule (spec
"Occurrence rules"), evaluated at the migration's `current_date` against the account's
`current_balance_as_of`: `first_date` for `once`; for `monthly`/`annual` the first occurrence
strictly after the balance date when that is before today, else the first on or after today, or
`NULL` when none remains before `end_date`. Occurrence `n` is `(first_date + make_interval(months
=> n * step))::date`, PostgreSQL's own month-end clamp, which equals `forecast._add_months(first_date,
n * step)`.

**Eligibility.** `assert_commitment_payment()` fires `BEFORE INSERT ... WHEN (NEW.kind =
'payment')` and raises `commitment_payment_is_eligible` unless the linked transaction is a leaf
row of `v_transaction_posting` in the same household, not `is_excluded`, not `opening_balance` or
`adjustment`, on the commitment's account and currency, signed like the commitment's amount, and
not one leg of a `payslip_line_posting` pair. A transfer leg is eligible — a card payment or
planned transfer is a real cash movement on the paying account. `routes/commitment_resolutions.py`
mirrors this predicate in SQL for its candidates read and its eligibility re-check, each naming the
other in a comment. `preserve_commitment_resolution()` refuses every `UPDATE`; `DELETE` is the undo
path, gated by the route rather than the database. Both trigger functions are revoked from
`PUBLIC`. `commitment_resolution` enables `household_isolation FOR ALL TO authenticated` as on
every other household-scoped table.

`commitments.py`'s `commitment_progress()` computes each commitment's lifecycle `status`
(`archivedAt` set is `cancelled`; `next_due_date NULL` is `finished`; else `overdue` before
`asOf`, `due` through `asOf + 7`, else `upcoming`), `paidTowardNext` (the sum of payment amounts
against the open occurrence), `partlyPaid` and `remainingNext` (`amount − paidTowardNext` when
`|paidTowardNext| < |amount|`, else `"0.0000"`) in one query over `commitment_resolution` joined
to `v_transaction_posting`, reused by `list_commitments`, the resolutions router after a write,
and — Task 2 — the action list. `routes/commitment_resolutions.py` (registered in `main.py`)
implements the four resolution routes (`docs/design/api-contracts.html`); a closing resolution
advances `commitment.next_due_date` to `forecast.next_occurrence(cadence, first_date, end_date,
due_date)` in the same transaction. `GET /forecast` gains a floor at `next_due_date` (a finished
commitment applies no occurrence) and substitutes `remainingNext` for the occurrence equal to
`next_due_date`, omitted when zero; each occurrence carries its commitment's `certainty`.

---

## `V49` (applied)

### Card-statement source and actions

Phase 3.1 Task 2. A commitment may be planned from a card statement's due amount, and one Needs
attention list gathers every item that asks the owner to act: a commitment occurrence, a card
statement with an amount due and no plan, and owner-entered follow-ups. `action_item` and
`action_mark` follow `commitment_resolution`'s own convention (V48): plain owner-scoped tables, no
`SECURITY DEFINER` write path.

```sql
ALTER TABLE card_statement
  ADD CONSTRAINT uq_card_statement_household_id UNIQUE (household_id, id);

ALTER TABLE commitment
  ADD COLUMN source_card_statement_id UUID,
  ADD CONSTRAINT fk_commitment_card_statement_scope
    FOREIGN KEY (household_id, source_card_statement_id)
    REFERENCES card_statement (household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT commitment_card_source_is_one_payment CHECK (
    source_card_statement_id IS NULL OR (cadence = 'once' AND amount < 0));
CREATE UNIQUE INDEX uq_commitment_open_card_statement ON commitment (source_card_statement_id)
  WHERE source_card_statement_id IS NOT NULL AND archived_at IS NULL;

CREATE TYPE action_item_state AS ENUM ('open', 'completed', 'dismissed');

CREATE TABLE action_item (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL REFERENCES household(id),
  idempotency_key UUID NOT NULL,
  title           TEXT NOT NULL,
  due_date        DATE,
  account_id      UUID,
  state           action_item_state NOT NULL DEFAULT 'open',
  dismiss_reason  TEXT,
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_action_item_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_action_item_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT action_item_title_is_bounded CHECK (
    char_length(title) BETWEEN 1 AND 120 AND title = btrim(title)),
  CONSTRAINT action_item_resolution_agrees CHECK ((state = 'open') = (resolved_at IS NULL)),
  CONSTRAINT action_item_dismissal_has_reason CHECK (
    (state = 'dismissed') = (dismiss_reason IS NOT NULL)
    AND (dismiss_reason IS NULL OR (char_length(dismiss_reason) BETWEEN 1 AND 200
                                    AND dismiss_reason = btrim(dismiss_reason))))
);

CREATE TABLE action_mark (
  household_id   UUID NOT NULL REFERENCES household(id),
  action_key     TEXT NOT NULL,
  snoozed_until  DATE,
  dismiss_reason TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, action_key),
  CONSTRAINT action_mark_key_shape CHECK (action_key ~
    '^(follow-up\.[0-9a-f-]{36}|commitment\.[0-9a-f-]{36}\.[0-9]{4}-[0-9]{2}-[0-9]{2}|card\.[0-9a-f-]{36})$'),
  CONSTRAINT action_mark_is_one_mark CHECK (num_nonnulls(snoozed_until, dismiss_reason) = 1),
  CONSTRAINT action_mark_dismissal_is_card CHECK (dismiss_reason IS NULL OR action_key LIKE 'card.%'),
  CONSTRAINT action_mark_reason_is_bounded CHECK (dismiss_reason IS NULL OR (
    char_length(dismiss_reason) BETWEEN 1 AND 200 AND dismiss_reason = btrim(dismiss_reason)))
);
```

Both new tables enable `household_isolation FOR ALL TO authenticated`. `routes/plans.py`'s
`POST`/`PUT /commitments` apply the card-source rules on create and re-apply the account,
currency, cadence and sign rules on every update of an already card-sourced commitment; the
`CommitmentRow` gains `source` (`"owner" | "card_statement"`), `sourceCardStatementId` and
`sourceCardAccountName` (read through a left join to `card_statement` and its own account).
`routes/actions.py` (registered in `main.py`) implements `GET /actions` and the six write routes
(`docs/design/api-contracts.html`) from one function, `action_list(cur, household_id, as_of, *,
apply_marks)`, gathering:

1. a `commitment.<id>.<nextDueDate>` item for each unarchived commitment with `next_due_date`
   within `commitments.DUE_WINDOW_DAYS` days or overdue, its `remaining` and `partlyPaid` from
   `commitment_progress()`;
2. a `card.<statementId>` item for the latest `card_statement` dated on or before `asOf` per
   active credit-card account, when that statement states an amount due, is not paid in full, is
   due within the window, has no unarchived commitment sourced from it and no dismissal mark;
3. a `follow-up.<id>` item for each open `action_item`.

A commitment item's completion and dismissal are always through `POST
/commitments/{id}/resolutions` (Task 1); no route here writes `commitment_resolution`. A snoozed
item (`action_mark.snoozed_until > asOf`) moves to the response's `snoozed[]`; a card item's
dismissal (`action_mark.dismiss_reason`, card keys only — the database's own
`action_mark_dismissal_is_card` check) hides it. `apply_marks=False` (Phase 3.2's unplanned card
dues) reads no mark at all: snoozed and dismissed items are both listed.

---

## `V50` (applied)

### Monthly review

Phase 3.2 Task 2. The owner walks one calendar month's coverage gaps, transfer candidates,
uncategorised postings, month-end position, upcoming commitments and open actions, then marks it
reviewed. Every step is computed on read from tables and views this schema already has;
`monthly_review` holds only the one fact none of them do — that the owner marked the month
reviewed, and when.

```sql
CREATE TABLE monthly_review (
  household_id UUID NOT NULL REFERENCES household(id),
  review_month DATE NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, review_month),
  CONSTRAINT monthly_review_month_is_first_day CHECK (
    review_month = date_trunc('month', review_month)::date)
);
```

`monthly_review` enables `household_isolation FOR ALL TO authenticated`, the same convention as
`action_item`/`action_mark` (V49) — no `SECURITY DEFINER` write path. `routes/monthly_review.py`
(registered in `main.py`) implements `GET /monthly-review` (`?month=YYYY-MM`, default the calendar
month before today, 422 for a month after the current one), `PUT
/monthly-review/{month}/completion` (`INSERT ... ON CONFLICT DO NOTHING RETURNING completed_at` —
201 the first time, 200 keeping the original `completedAt` on a replay) and `DELETE
/monthly-review/{month}/completion` (idempotent, 204). Every step reuses an existing computation
rather than a second copy: `routes/coverage.py`'s `coverage_rows()` for the month's own
missing/partial cells; a new `routes/transfers.py` `candidates_for_window()`, extracted from `GET
/transfers/candidates` with no behaviour change, for the month's candidate count; a new
`routes/wealth_history.py` `month_totals()`, extracted from `GET /wealth-history` with no
behaviour change, for the month's and the prior month's stored totals (`changeBase` is the
server's own `totalBase - previousTotalBase`, Python `Decimal`, only when both months are
complete); `routes/actions.py`'s `action_list()` for open/snoozed counts at today. `GET /actions`
gains `previousMonthReview: {month, completedAt}` from one `monthly_review` primary-key read, so
Overview states the prior month's review status without running the review's own computation —
both routes name that month through a shared `coverage.previous_month()`.

---

## `V51` (applied)

### Goals and reservations

Phase 3.3 Task 1. A reservation earmarks an amount of an asset account, or a whole account, for a
goal; it is a plan, not a ledger transaction, so it never changes a balance, net worth or report.

```sql
CREATE TYPE goal_state AS ENUM ('active', 'achieved', 'abandoned');
CREATE TYPE reservation_purpose AS ENUM ('goal', 'emergency');
CREATE TYPE reservation_kind AS ENUM ('amount', 'whole_account');

CREATE TABLE goal (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  idempotency_key      UUID NOT NULL,
  name                 TEXT NOT NULL,
  target_amount        NUMERIC(20,4) NOT NULL,
  target_currency      CHAR(3) NOT NULL REFERENCES currency(code),
  target_date          DATE NOT NULL,
  priority             SMALLINT NOT NULL,
  planned_monthly      NUMERIC(20,4),
  state                goal_state NOT NULL DEFAULT 'active',
  closed_at            TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_goal_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_goal_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT goal_name_is_bounded CHECK (char_length(name) BETWEEN 1 AND 80 AND name = btrim(name)),
  CONSTRAINT goal_target_is_positive CHECK (target_amount > 0),
  CONSTRAINT goal_priority_is_bounded CHECK (priority BETWEEN 1 AND 5),
  CONSTRAINT goal_planned_is_nonnegative CHECK (planned_monthly IS NULL OR planned_monthly >= 0),
  CONSTRAINT goal_closure_agrees CHECK ((state = 'active') = (closed_at IS NULL))
);

CREATE TABLE resource_reservation (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  idempotency_key        UUID NOT NULL,
  purpose                reservation_purpose NOT NULL,
  goal_id                UUID,
  account_id             UUID NOT NULL,
  kind                   reservation_kind NOT NULL,
  native_amount          NUMERIC(20,4) NOT NULL,
  currency               CHAR(3) NOT NULL REFERENCES currency(code),
  reserved_on            DATE NOT NULL,
  rate_currency          CHAR(3) NOT NULL REFERENCES currency(code),
  rate_at_reservation    NUMERIC(20,10) NOT NULL,
  released_at            TIMESTAMPTZ,
  release_reason         TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_reservation_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_reservation_goal_scope FOREIGN KEY (household_id, goal_id)
    REFERENCES goal (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_reservation_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT reservation_purpose_agrees CHECK ((purpose = 'goal') = (goal_id IS NOT NULL)),
  CONSTRAINT reservation_amount_is_positive CHECK (native_amount > 0),
  CONSTRAINT reservation_rate_is_positive CHECK (rate_at_reservation > 0),
  CONSTRAINT reservation_release_agrees CHECK (
    released_at IS NOT NULL OR release_reason IS NULL),
  CONSTRAINT reservation_reason_is_bounded CHECK (release_reason IS NULL OR (
    char_length(release_reason) BETWEEN 1 AND 200 AND release_reason = btrim(release_reason)))
);
CREATE UNIQUE INDEX uq_reservation_whole_account ON resource_reservation (account_id)
  WHERE kind = 'whole_account' AND released_at IS NULL;
CREATE INDEX idx_reservation_account_active ON resource_reservation (household_id, account_id)
  WHERE released_at IS NULL;
```

`preserve_resource_reservation()` refuses `DELETE` and every `UPDATE` except setting
`released_at` and `release_reason` once on a row whose `released_at` is null.
`preserve_closed_goal()` refuses every `UPDATE` once a goal's `state` is off `'active'` — the
close route's own state-and-`closed_at` write still fires because it runs while `OLD.state` is
still `'active'`. Both tables enable `household_isolation FOR ALL TO authenticated`, the same
plain owner-scoped convention as `commitment` (V43) and `action_item`/`action_mark` (V49) — no
`SECURITY DEFINER` write path; both trigger functions are revoked from `PUBLIC`.

`api/oneledger/resources.py` reads each active asset account's native value (ledger balance,
latest in-period snapshot, or summed holdings) and every reservation; `api/oneledger/goals.py` is
the pure Decimal math for a reservation's contributed/market/FX split and a goal's totals;
`routes/goals.py` implements `GET /goals`, `POST /goals`, `PUT /goals/{goalId}`,
`POST /goals/{goalId}/close`, `POST /reservations` and `POST /reservations/{id}/release`. An
emergency-purpose reservation is refused (422 `mark this account as an emergency resource first`)
unless V52's `emergency_resource_account` names the account eligible.

---

## `V52` (applied)

### Emergency reserve and action keys

Phase 3.3 Task 2. The emergency reserve's target, its baseline (chosen category buckets over a
reference period, or a manual monthly amount), and which asset accounts the owner marks eligible
to cover it. The action key shape widens to admit `goal.<goalId>` and `reserve.<householdId>`
(this task) and Phase 3.4's `record.` keys, so that phase needs no further constraint change.

```sql
CREATE TYPE baseline_method AS ENUM ('reference_period', 'manual');

CREATE TABLE emergency_reserve (
  household_id         UUID PRIMARY KEY REFERENCES household(id),
  target_months        NUMERIC(4,1) NOT NULL,
  baseline_method       baseline_method NOT NULL,
  reference_months      SMALLINT NOT NULL DEFAULT 6,
  buckets               category_bucket[] NOT NULL DEFAULT '{need,debt_service}',
  manual_monthly_base   NUMERIC(20,4),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT emergency_target_is_bounded CHECK (target_months > 0 AND target_months <= 60),
  CONSTRAINT emergency_reference_is_bounded CHECK (reference_months BETWEEN 1 AND 24),
  CONSTRAINT emergency_buckets_are_spending CHECK (
    cardinality(buckets) >= 1
    AND buckets <@ '{need,want,investment,debt_service}'::category_bucket[]),
  CONSTRAINT emergency_manual_agrees CHECK (
    (baseline_method = 'manual') = (manual_monthly_base IS NOT NULL)
    AND (manual_monthly_base IS NULL OR manual_monthly_base > 0))
);

CREATE TABLE emergency_resource_account (
  household_id     UUID NOT NULL REFERENCES household(id),
  account_id       UUID NOT NULL,
  access_days      SMALLINT NOT NULL DEFAULT 0,
  restriction_note TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, account_id),
  CONSTRAINT fk_emergency_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT emergency_access_is_bounded CHECK (access_days BETWEEN 0 AND 365),
  CONSTRAINT emergency_note_is_bounded CHECK (restriction_note IS NULL OR (
    char_length(restriction_note) BETWEEN 1 AND 200 AND restriction_note = btrim(restriction_note)))
);

ALTER TABLE action_mark DROP CONSTRAINT action_mark_key_shape;
ALTER TABLE action_mark ADD CONSTRAINT action_mark_key_shape CHECK (action_key ~ (
  '^((follow-up|card|goal|reserve)\.[0-9a-f-]{36}'
  || '|commitment\.[0-9a-f-]{36}\.[0-9]{4}-[0-9]{2}-[0-9]{2}'
  || '|record\.[0-9a-f-]{36}\.(review|(renewal|maturity|deadline)\.[0-9]{4}-[0-9]{2}-[0-9]{2}))$'));
```

Both new tables enable `household_isolation FOR ALL TO authenticated` — plain owner-scoped RLS,
no `SECURITY DEFINER` write path, the same convention as `goal` and `resource_reservation` (V51).
The widened key shape also admits Phase 3.4's `record.` keys so that phase needs no further
constraint change; `action_mark_dismissal_is_card` is unchanged — a `goal.`/`reserve.` mark is
snooze only, never dismissal.

`api/oneledger/emergency.py` is the pure Decimal baseline and coverage math (a bucketed
reference-period average or a manual monthly figure; eligible resources not reserved for a goal,
divided by the baseline); `api/oneledger/resources.py` adds the settings, spending-totals and
eligible-account reads both `routes/emergency_reserve.py` and `routes/actions.py` share, plus
`goal_shortfalls()` for the `goal.<goalId>` action item, so `routes/actions.py` still imports no
other route module. `routes/emergency_reserve.py` implements `GET /emergency-reserve`,
`PUT /emergency-reserve`, `PUT /emergency-reserve/accounts/{accountId}` and
`DELETE /emergency-reserve/accounts/{accountId}` (409 while an emergency reservation is active).
`routes/actions.py`'s `action_list()` adds `goal.<goalId>` (`goal_shortfall`, an active goal with
available progress and a positive shortfall) and one `reserve.<householdId>`
(`reserve_below_target`, when the reserve is configured, coverage is available and
`coverageMonths < target_months`) item; both accept a snooze mark and refuse a dismissal reason.

---

## `V53` (applied)

### Register

Phase 3.4 Task 1. A register record is an owner assumption like `commitment` and `goal` — no
`SECURITY DEFINER` write path, plain household-scoped RLS. Archiving is terminal.
`register_document` is created here so Task 2's V54 needs no further migration; its routes, links
and upload are Task 2 — nothing in Task 1 populates it.

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

Meanings by kind: `insurance_policy` — `counterparty` provider, `covered` person or asset,
`amount` coverage, `premium_amount`/`premium_cadence` the premium, `key_date` renewal date.
`deposit` —
`account_id` the deposit account (a `fixed_deposit` or `recurring_deposit` account, checked by the
route), `counterparty` institution, `amount` expected proceeds, `key_date` maturity date,
`instruction` renewal instruction. `claim` — `counterparty`, `amount` expected, `key_date`
submission date, `completed_at` closed. `nominee` — `account_id`, `nominee_recorded`.
`administration` — `key_date` deadline, `completed_at` done. `account.maturity_date` stays unused;
the deposit record owns the maturity.

Both tables enable `household_isolation FOR ALL TO authenticated`, the same convention as `goal`
and `resource_reservation` (V51). `preserve_archived_register_record()` refuses any `UPDATE` to a
record whose `archived_at` is set and refuses every `DELETE` — the same terminal shape as
`preserve_closed_goal()` (V51) without a one-time exception, since a register record has no closing
write that itself needs to pass.

`api/oneledger/register_records.py` holds the record reads `routes/actions.py`'s `action_list()`
needs (the renewal/maturity/deadline windows and the review-due rule), the same import-cycle
convention `resources.py` already follows for goals and the emergency reserve.
`routes/register.py` implements `GET /register`, `POST /register`,
`PUT /register/{recordId}`, and `POST /register/{recordId}/archive|reviewed|renewed|complete|reopen`
— kind-forbidden fields are refused in Pydantic before the database, and a deposit's account must
be an active `fixed_deposit` or `recurring_deposit` account. `action_list()` adds
`record.<id>.renewal.<keyDate>` (`renewal_due`, a policy due within 30 days), one
`record.<id>.maturity.<keyDate>` (`maturity_due`, a deposit due within 30 days),
`record.<id>.deadline.<keyDate>` (`deadline_due`, an incomplete administration record due within 14
days) and `record.<id>.review` (`review_due`, `coalesce(last_reviewed_on, created_at::date)` more
than 365 days before `asOf`) — every item accepts a snooze mark and refuses a dismissal reason.

---

## `V54` (applied)

### Register-sourced commitments

Phase 3.4 Task 2. A commitment may be planned from a register record's premium, expected proceeds
or expected reimbursement — the same one-source shape V49 gave a card statement, extended to
registers.

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

At most one source per commitment (`commitment_has_one_source`) and at most one open (unarchived)
commitment per record (`uq_commitment_open_register_record`, archiving frees the record for a new
plan) — no fixed CHECK constrains the per-kind schedule, sign, account or currency rules, since they
vary by the source record's kind; `routes/plans.py` carries all of them.
`CREATE OR REPLACE FUNCTION assert_commitment_payment()` keeps every V48 condition and adds one:
when the commitment's source record is a `claim` or `deposit`, the settling posting's `txn_type`
must not be `income` — a reimbursement or returned principal is never counted as newly earned
income. `routes/commitment_resolutions.py`'s candidates predicate mirrors this addition, keeping its
sync comment.

`routes/plans.py`'s `POST`/`PUT /commitments` accept `sourceRegisterRecordId` (not with
`sourceCardStatementId`, 422; fixed at creation, like the card source): an `insurance_policy` needs
a premium (422 `this policy has no premium`), the given cadence equal to `premium_cadence` and a
negative amount; a `deposit` needs expected proceeds and currency (422 `this deposit has no
expected proceeds`), `once`, a positive amount, and an active asset receiving account in the
record's currency that is not the deposit's own account (422 `choose the account that receives the
proceeds`); a `claim` needs `once` and a positive amount, and refuses a completed claim (409); a
`nominee` or `administration` record refuses with 422 `this record does not plan money`. A second
open plan for the same record is 409 `money is already planned for this record`; an archived record
is 409. `CommitmentRow.source` gains `register_record` with `sourceRegisterRecordId` and
`sourceRegisterTitle`. `RegisterRow.plannedCommitment` reuses `commitments.commitment_progress` — no
second remaining-amount computation; a claim's outstanding amount is its `remainingNext` and what
was received is `paidTowardNext`. `register_records.py`'s deposit maturity action item is omitted
once an unarchived commitment is sourced from the record.

### Document upload

`POST /documents` stores an owner PDF (`tax_document | insurance_policy | property_document |
fd_certificate | receipt | other`) through the same `ingest.storage.store` a statement import
uses — a bounded 20 MB read, a `%PDF-` header check, a fixed safe label per type (no uploaded
filename is retained), `parsed_status = 'not_applicable'`, and `ON CONFLICT (household_id, sha256)`
reuse: the same bytes already stored under the same type return that row with 200; under another
type, 409 `this file is already in the document library`. Owner decision (16 September 2026): an
upload is not an import — `GET /documents` rows gain `importKind: "upload"` for the six upload
types with no import, and `title`, non-null only for such a row (a statement, investment or payslip
row keeps `title: null`).

`PUT`/`DELETE /register/{recordId}/documents/{documentId}` link and unlink a household document of
an upload type (422 otherwise; a foreign or unknown document is 404); both are idempotent.
`RegisterRow.documents[]` is `{documentId, documentType, title, uploadedAt}`.

---

## `V55` (applied)

### Budgets

Phase 4.1 Task 2. A budget line is an owner assumption in the household base currency (spec
"Budgets are owner assumptions, not postings"): it posts nothing, changes no transaction, and
never becomes income or spending. `category` is a shared seed table with no
`UNIQUE (household_id, id)` (V10, V19, V44 already reference it the same way), so
`budget_line.category_id` is the plain `REFERENCES category(id)` below rather than a composite
tenant-scoped foreign key, and `routes/budgets.py` enforces the tenant boundary itself, reusing
`routes/transactions.py::_assert_category_allowed`. This replaces the `budget_period`/`budget_line`
shapes originally planned in `0017` — see that section.

```sql
CREATE TABLE budget_line (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id      UUID NOT NULL REFERENCES household(id),
  idempotency_key   UUID NOT NULL,
  period_month      DATE NOT NULL,
  category_id       UUID REFERENCES category(id),
  bucket            category_bucket,
  amount_base       NUMERIC(20,4) NOT NULL,
  currency          CHAR(3) NOT NULL REFERENCES currency(code),
  rollover_enabled  BOOLEAN NOT NULL DEFAULT false,
  note              TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_budget_line_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_budget_line_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT budget_line_scope_shape CHECK (num_nonnulls(category_id, bucket) = 1),
  CONSTRAINT budget_line_period_month_is_first_day CHECK (
    period_month = date_trunc('month', period_month)::date),
  CONSTRAINT budget_line_amount_is_positive CHECK (amount_base > 0),
  CONSTRAINT budget_line_note_is_bounded CHECK (
    note IS NULL OR char_length(note) BETWEEN 1 AND 200)
);

CREATE UNIQUE INDEX uq_budget_line_month_category
  ON budget_line (household_id, period_month, category_id) WHERE category_id IS NOT NULL;
CREATE UNIQUE INDEX uq_budget_line_month_bucket
  ON budget_line (household_id, period_month, bucket) WHERE bucket IS NOT NULL;
```

`household_isolation FOR ALL TO authenticated` as on every other household-scoped table; no table
GRANT, and no trigger — a budget line is editable and deletable, unlike a closed goal or an
archived register record, since it carries no ledger side effect to protect. `currency` always
carries the household's own base currency; the route sets it and the create body never accepts one.
`GET /budgets` computes `actualBase`, `carriedBase`, `availableBase` and `varianceBase` on read —
none of it is stored — and `routes/budgets.py`'s docstring records the rollover chain's exact
recursion (spec "Budgets are owner assumptions, not postings", `api-contracts.html` §2).

---

## `V56` (applied)

### Recurring series

Phase 4.1 Task 3. Discovery reads the ledger and proposes; it writes nothing on its own (spec
"Discovery proposes, the owner decides"). `merchant` and `category` are shared seed tables with
no `UNIQUE (household_id, id)` (V9, V10, V19, V44 and V55 already reference them the same way),
so `merchant_id` and `category_id` below are the plain `REFERENCES merchant(id)`/
`REFERENCES category(id)` per the plan review's non-negotiable ruling — the plan originally
drafted for this task had asked for a tenant-scoped foreign key on `merchant_id`, which a
composite unique on `merchant` cannot support. `routes/recurring.py` enforces the tenant boundary
itself, reusing `routes/transactions.py::_assert_category_allowed` for the category's
active/tenant check. This replaces the `recurring_series` shape originally planned in `0017` —
see that section.

```sql
CREATE TYPE recurring_cadence AS ENUM ('weekly', 'monthly', 'quarterly', 'annual');

CREATE TABLE recurring_series (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  idempotency_key       UUID NOT NULL,
  label                 TEXT NOT NULL,
  merchant_id           UUID REFERENCES merchant(id),
  match_key             TEXT,
  cadence               recurring_cadence NOT NULL,
  expected_amount_base  NUMERIC(20,4) NOT NULL,
  currency              CHAR(3) NOT NULL REFERENCES currency(code),
  category_id           UUID REFERENCES category(id),
  first_seen_on         DATE NOT NULL,
  last_seen_on          DATE NOT NULL,
  ended_on              DATE,
  ended_reason          TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_recurring_series_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_recurring_series_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT recurring_series_identity_shape CHECK (num_nonnulls(merchant_id, match_key) = 1),
  CONSTRAINT recurring_series_label_is_bounded CHECK (
    char_length(label) BETWEEN 1 AND 120 AND label = btrim(label)),
  CONSTRAINT recurring_series_amount_is_positive CHECK (expected_amount_base > 0),
  CONSTRAINT recurring_series_ended_reason_is_bounded CHECK (
    ended_reason IS NULL OR char_length(ended_reason) BETWEEN 1 AND 200),
  CONSTRAINT recurring_series_ended_shape CHECK ((ended_on IS NULL) = (ended_reason IS NULL)),
  CONSTRAINT recurring_series_dates_are_ordered CHECK (
    last_seen_on >= first_seen_on AND (ended_on IS NULL OR ended_on >= last_seen_on))
);

CREATE UNIQUE INDEX uq_recurring_series_open_merchant ON recurring_series (household_id, merchant_id)
  WHERE ended_on IS NULL AND merchant_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recurring_series_open_match_key ON recurring_series (household_id, match_key)
  WHERE ended_on IS NULL AND match_key IS NOT NULL;

CREATE TABLE recurring_series_member (
  household_id   UUID NOT NULL,
  series_id      UUID NOT NULL,
  transaction_id UUID NOT NULL,
  observed_on    DATE NOT NULL,
  amount_base    NUMERIC(20,4) NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, series_id, transaction_id),
  CONSTRAINT fk_recurring_series_member_series FOREIGN KEY (household_id, series_id)
    REFERENCES recurring_series (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_recurring_series_member_transaction FOREIGN KEY (household_id, transaction_id)
    REFERENCES transaction (household_id, id) ON DELETE RESTRICT
);

CREATE TABLE recurring_dismissal (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID NOT NULL REFERENCES household(id),
  merchant_id  UUID REFERENCES merchant(id),
  match_key    TEXT,
  dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT recurring_dismissal_identity_shape CHECK (num_nonnulls(merchant_id, match_key) = 1)
);

CREATE UNIQUE INDEX uq_recurring_dismissal_merchant
  ON recurring_dismissal (household_id, merchant_id) WHERE merchant_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recurring_dismissal_match_key
  ON recurring_dismissal (household_id, match_key) WHERE match_key IS NOT NULL;
```

`household_isolation FOR ALL TO authenticated` on all three tables, no table GRANT.
`preserve_ended_recurring_series()` (a `BEFORE UPDATE` trigger on `recurring_series`, the same
shape as `preserve_closed_goal()` in V51 and `preserve_archived_register_record()` in V53) refuses
any further `UPDATE` once `ended_on` is set, raising a bare `ERRCODE 23514` with a distinctive
first line; `routes/recurring.py` maps that line to **422** — not the 409 `routes/goals.py` and
`routes/register.py` map their own preserve triggers to — because the route itself already reads
`ended_on` and returns 409 before ever reaching the trigger, so a trigger hit only means a race the
route's own read did not see.

`transaction.recurring_series_id` (V10) stays an unconstrained, unwritten column: a posted ledger
row is immutable, so which series a transaction belongs to is recorded in
`recurring_series_member`'s own tenant- and transaction-scoped composite foreign key, never in a
rewrite of the transaction row it observes — see `0018`'s own note. `api/oneledger/recurring.py`
holds the pure cadence detection (no database): a candidate needs at least 3 occurrences whose
median gap fits one cadence window (weekly 5–9, monthly 26–35, quarterly 82–98, annual 350–380
days) with at least two thirds of all gaps inside that same window; identity is `merchant_id` when
present, otherwise `lower(regexp_replace(btrim(coalesce(description_clean, description_raw)),
'\s+', ' ', 'g'))` — the same expression `recurring_series_identity_shape` requires be non-null
whenever `merchant_id` is null.

---

## `V57` (applied)

### Recurring opt-out

Phase 4.1 Task 1 (spec "Recurring payments are simply just not a subscription"). Discovery is now
opt-out: `POST /recurring/refresh` creates series on its own, and the owner's one correction is
the subscription flag. `recurring_cadence` gains `semiannual` and `custom` — both added and
neither referenced anywhere else in this migration, since Postgres refuses a value used in the
same transaction that adds it.

```sql
ALTER TYPE recurring_cadence ADD VALUE 'semiannual';
ALTER TYPE recurring_cadence ADD VALUE 'custom';

ALTER TABLE recurring_series
  ADD COLUMN is_subscription           BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN subscription_set_by_owner BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN detection_method          TEXT NOT NULL DEFAULT 'detected',
  ADD COLUMN named_by_owner            BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN native_amount             NUMERIC(20,4),
  ADD COLUMN native_currency           CHAR(3) REFERENCES currency(code),
  ADD CONSTRAINT recurring_series_detection_method_is_known CHECK (
    detection_method IN ('detected', 'owner')),
  ADD CONSTRAINT recurring_series_native_pair CHECK (
    (native_amount IS NULL) = (native_currency IS NULL)),
  ADD CONSTRAINT recurring_series_native_amount_is_positive CHECK (
    native_amount IS NULL OR native_amount > 0);

ALTER TABLE recurring_series_member
  ADD CONSTRAINT uq_recurring_series_member_transaction UNIQUE (household_id, transaction_id);

DROP INDEX uq_recurring_series_open_merchant;
DROP INDEX uq_recurring_series_open_match_key;

ALTER TABLE recurring_dismissal
  ADD COLUMN anchor_amount   NUMERIC(20,4),
  ADD COLUMN anchor_currency CHAR(3) REFERENCES currency(code),
  ADD CONSTRAINT recurring_dismissal_anchor_pair CHECK (
    (anchor_amount IS NULL) = (anchor_currency IS NULL));

CREATE UNIQUE INDEX uq_recurring_dismissal_merchant
  ON recurring_dismissal (household_id, merchant_id, anchor_amount, anchor_currency)
  NULLS NOT DISTINCT WHERE merchant_id IS NOT NULL;
CREATE UNIQUE INDEX uq_recurring_dismissal_match_key
  ON recurring_dismissal (household_id, match_key, anchor_amount, anchor_currency)
  NULLS NOT DISTINCT WHERE match_key IS NOT NULL;

CREATE TABLE recurring_subscription_category (
  category_id     UUID PRIMARY KEY REFERENCES category(id),
  is_subscription BOOLEAN NOT NULL
);
```

Before adding `uq_recurring_series_member_transaction` — a charge belongs to at most one series,
from here on — V57 deletes any existing duplicate member row, keeping for each transaction the row
on the newest series (open before ended, then latest `created_at`, then `id`) and reporting the
deleted count with `RAISE NOTICE`. Duplicates could exist because V56 let confirm → end → confirm
re-insert the same charges into a fresh series once the identity's earlier one ended.

`uq_recurring_series_open_merchant`/`uq_recurring_series_open_match_key` are dropped: one identity
may now carry several open series at once (spec "one descriptor, several subscriptions" — two
amount clusters on one descriptor whose date ranges overlap are two concurrent subscriptions).

`recurring_dismissal`'s two unique indexes are recreated with the anchor pair and
`NULLS NOT DISTINCT`, so a repeated null-anchor (whole-identity) dismissal is still refused exactly
as before, while a *different* anchor on the same identity is its own, independent dismissal —
removing one subscription on a shared descriptor no longer suppresses its siblings. `routes/
recurring.py::_insert_dismissal`'s `ON CONFLICT` target matches these indexes exactly.

`recurring_subscription_category` is a seed reference table (RLS `reference_read FOR SELECT TO
authenticated USING (true)`, the same shape as `currency`'s own in V4 — no write policy, so only a
migration can change it) mapping a seed category to the subscription flag's deterministic default:
the Subscriptions tree and Streaming (`entertainment-streaming`) true; Housing, Utilities,
Insurance, Debt service, Financial and Taxes false. Seeded by joining seed rows on `slug` scoped
`household_id IS NULL` (V9's `category_system_slug` makes a seed slug unique only among seed rows),
so a household's own category never collides with the lookup even if it reuses the same slug text.
A child category with no row of its own falls back to its parent's row in
`routes/recurring.py::_subscription_category_mapping`.

`api/oneledger/recurring.py` adds deterministic amount clustering on top of its existing cadence
detection: within one identity and currency, each charge joins the cluster whose current median is
nearest and within 20%, walked in `booked_date` then `transaction_id` order for a result that does
not depend on the caller's own row order; two clusters whose date ranges overlap stay apart
(concurrent subscriptions), and a cluster that overlaps no other merges into the earlier,
non-overlapping cluster whose last charge is latest, then whose amount is nearest (one series whose
price changed). When nothing on an identity, in any of its currencies, clears the occurrence and
cadence bar, the whole identity is evaluated per currency as one group instead, so a bill varying
more than 20% is still detected. `SpendRow`/`Candidate` carry the charge's own original pair
(`amount`/`currency`) alongside the existing base-currency `amount_base`, and a new
`amounts_are_stable()` (every original amount within 5% of the median) feeds the subscription
flag's default both at candidate time and when `routes/recurring.py`'s Sync step recomputes it for
an existing series.

`routes/recurring.py::refresh_recurring` is the only writer, taking the household lock first and
running Extend (a charge not yet in any series joins the nearest open series on its own identity
and currency within 20%, or the sole open one on a larger price change), Create (`detect_candidates`
over what Extend left unassigned, within the 26-month lookback, skipping a group a dismissal
covers) and Sync (every open series' `native_amount`/`native_currency`/`expected_amount_base`/
`last_seen_on` move to its latest member; `is_subscription` is recomputed only while
`subscription_set_by_owner` is false — this is also what gives a series created before V57 its flag
and amounts on its first refresh since). `GET /recurring` reads only stored columns and opens
`SET TRANSACTION READ ONLY`; `POST /recurring/confirm`, `.../accept-price` and `.../end` are
removed, and `POST /recurring/{seriesId}/remove` replaces `.../end`, ending the series and
recording a dismissal anchored on its own latest `native_amount`/`native_currency` in one
transaction.

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

## `0014` (historical planned slot)

### Valuations

The first account-level `valuation_snapshot` implementation landed earlier as V30 so valuation
mode and double-count prevention exist before holdings. Its applied DDL is recorded in the V30
section above. V31 adds holding targets without creating a second valuation table.

---

## `0015` (historical planned slot)

### Net worth

Retired by Phase 2.2. The planned `net_worth_snapshot` table above this note is superseded by
V46's `month_end_position` — one row per counted account or holding, not one row per household
per date, so each account's own evidence date and rate survive rather than being reduced to a
single `fx_rates_used` blob. The planned `net_worth_attribution` table is retired outright:
`api/oneledger/attribution.py` (Phase 2.2 Task 2) computes the monthly waterfall on read from two
months of stored positions and the window's frozen flows, and nothing about it is stored.

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

```

Phase 1.7 Task 1 replaced the payslip shapes originally planned here with V42's exact,
reconciling design — see that section. Phase 4.1 Task 2 replaced the `budget_period`/`budget_line`
shapes originally planned here with V55's exact, reconciling design — see that section. Phase 4.1
Task 3 replaced the `recurring_series` shape originally planned here with V56's exact, reconciling
design — see that section.

---

## `0018` — Deferred foreign keys

`transaction` references `transfer`, `card_statement`, `loan` and
`loan_schedule_entry`, every one of which is created after it — and
`transfer` references `transaction` back. Those columns are therefore created as bare `UUID` in
`0007` and constrained here, once every target exists.

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_transfer     FOREIGN KEY (transfer_id)       REFERENCES transfer(id),
  ADD CONSTRAINT fk_txn_card_stmt    FOREIGN KEY (card_statement_id) REFERENCES card_statement(id),
  ADD CONSTRAINT fk_txn_loan         FOREIGN KEY (loan_id)           REFERENCES loan(id),
  ADD CONSTRAINT fk_txn_loan_entry   FOREIGN KEY (loan_schedule_entry_id)
                                     REFERENCES loan_schedule_entry(id);
```

The former deferred investment-event reference was never populated and V33 removed its column;
`investment_cash_allocation` is now the sole cash-to-investment link. This plan originally also
listed a deferred `fk_txn_recurring` on `transaction.recurring_series_id`; Phase 4.1 Task 3 (V56)
deliberately does not add it — a posted ledger row is immutable, so recording which series a
transaction belongs to is `recurring_series_member`'s own join (a household- and
transaction-scoped composite FK, `ON DELETE RESTRICT`), never a rewrite of the transaction row
that observes it. `transaction.recurring_series_id` (V10) stays the unconstrained, unwritten
column it has always been.
