-- db/migrations/V12__documents_and_import.sql
-- M2 Task 2: document, import_batch and statement_layout (schema.md 0008), with RLS and the
-- four deferred foreign keys on transaction whose targets this migration creates. The 0018
-- rule is per target: an FK clause lands with its target table, not earlier and not later.
-- classification_rule_id stays a bare UUID here — its target, and its FK, arrive with M4.
--
-- statement_layout is created first because document.statement_layout_id and
-- import_batch.statement_layout_id both reference it; schema.md's display order (documents,
-- then batches, then layouts) is the target shape, not the creation order.

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
  columns                JSONB NOT NULL,          -- [{field, x_min, x_max, label}]

  -- semantics
  date_format            TEXT NOT NULL,           -- 'dd/MM/yyyy' | 'dd-MM-yyyy' | 'dd MMM yyyy'
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

  -- Statement filenames carry card numbers (security-and-privacy.html section 1): the service
  -- masks the value before it reaches this column, and the unmasked filename exists only as a
  -- local variable for the duration of one request (M2 Task 5).
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

  -- reconciliation (Invariant I8). 'matched' wins over statement-ingestion.html's 'reconciled'
  -- (schema.md documents the column's value set; plan section 7.3), extended with
  -- 'running_balance_break', which the design doc describes but the column comment omitted.
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

-- V12 completes the deferred foreign keys whose targets it creates (schema.md 0018's rule).
-- All four columns are entirely NULL on every existing row, so validation is trivial.
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_source_document  FOREIGN KEY (source_document_id)  REFERENCES document(id),
  ADD CONSTRAINT fk_txn_receipt_document FOREIGN KEY (receipt_document_id) REFERENCES document(id),
  ADD CONSTRAINT fk_txn_import_batch     FOREIGN KEY (import_batch_id)     REFERENCES import_batch(id),
  ADD CONSTRAINT fk_txn_statement_layout FOREIGN KEY (statement_layout_id) REFERENCES statement_layout(id);
