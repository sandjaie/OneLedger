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
  account_id           UUID NOT NULL,
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
  CHECK (fx_rate_date <= as_of_date)
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
  predecessor_lot_id    UUID REFERENCES acquisition_lot(id),  -- added by V40
  UNIQUE NULLS NOT DISTINCT (origin_transaction_id, predecessor_lot_id)  -- V40 replaces the
                                                                          -- single-row-per-origin
                                                                          -- uniqueness below
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
CREATE TABLE investment_import (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id       UUID NOT NULL REFERENCES household(id),
  document_id        UUID NOT NULL,
  source_contract    TEXT NOT NULL,
  extraction_version TEXT NOT NULL,
  status             investment_import_status NOT NULL DEFAULT 'staged',
  completed_at       TIMESTAMPTZ,
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
  committed_transaction_id UUID
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
  reconciliation_status investment_checkpoint_status NOT NULL DEFAULT 'pending'
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
  resolved_at               TIMESTAMPTZ
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

V39 serializes allocation and correction through an event advisory mutex followed by event,
holding, lot and review row locks. Every selected disposal and acquisition origin must still be an
active correction-chain leaf; retained allocation evidence makes either origin ineligible for
correction. A proposal includes the immutable disposal date in its expected state and refuses an
acquisition lot dated after that disposal. Remaining basis includes active acquisition origins and
allocations from active disposals only, applying `ROUND_HALF_UP` at four decimals per lot.

V40 adds the lot lineage read model ahead of any operation that creates lineage (Task 15.2's split
and Task 15.4's paired transfer). `acquisition_lot.predecessor_lot_id` is a nullable, household- and
holding-scoped self reference: a successor lot keeps its predecessor's `acquisition_date`, and
`(origin_transaction_id, predecessor_lot_id)` is unique with nulls not distinct so one lineage event
creates at most one successor per predecessor. `investment_transaction.counterpart_transaction_id`
is a nullable, household-scoped, unique self reference present exactly for the paired `switch_in`,
`systematic_transfer_in` and `platform_transfer_in` kinds; the future paired operation names the in
leg's out counterpart there. A `BEFORE INSERT` order guard on `investment_transaction` raises
`investment_unit_event_order` when a unit- or lot-affecting row (`units` present, or `event_kind =
'split'`) predates an active `split`/`platform_transfer_in`/`platform_transfer_out` event in its
holding, or corrects an event an active lineage event already fixed (excluding the corrected event
itself); cash-only kinds and exact dedupe replay are exempt. `investment_lot_lineage_available(
holding_id, as_of)` is false from the date of an active split with no successor lot.
`investment_open_lots(holding_id, as_of)` returns each lineage chain's tip lot, its preserved
acquisition date, open units and `round(basis * open / acquired, 4)`, counting only a lot whose
creating event is active and dated on or before `as_of` and an allocation from an active disposal
dated on or before `as_of`; it returns nothing while lineage is unavailable. `allocate_investment_lots_v33`
counts lot capacity from active disposals only, refuses a successor lot for a disposal dated before
its creating event, and refuses any lot once lineage is unavailable as of the disposal date;
`confirm_investment_lot_allocation` derives remaining basis from `investment_open_lots` so it
withholds under the same condition. `guard_correction_unfunded_event` exempts a corrected split's or
transfer out-leg's own predecessor consumption but still refuses once that event's own successor
lots are allocated or consumed by another active lineage event.

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

`transaction` references `transfer`, `card_statement`, `loan`,
`loan_schedule_entry` and `recurring_series`, every one of which is created after it — and
`transfer` references `transaction` back. Those columns are therefore created as bare `UUID` in
`0007` and constrained here, once every target exists.

```sql
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_transfer     FOREIGN KEY (transfer_id)       REFERENCES transfer(id),
  ADD CONSTRAINT fk_txn_card_stmt    FOREIGN KEY (card_statement_id) REFERENCES card_statement(id),
  ADD CONSTRAINT fk_txn_loan         FOREIGN KEY (loan_id)           REFERENCES loan(id),
  ADD CONSTRAINT fk_txn_loan_entry   FOREIGN KEY (loan_schedule_entry_id)
                                     REFERENCES loan_schedule_entry(id),
  ADD CONSTRAINT fk_txn_recurring    FOREIGN KEY (recurring_series_id)
                                     REFERENCES recurring_series(id);
```

The former deferred investment-event reference was never populated and V33 removed its column;
`investment_cash_allocation` is now the sole cash-to-investment link.
