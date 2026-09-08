-- db/migrations/V13__staged_transactions.sql
-- M2 Task 4: staged_transaction (statement-ingestion.html section 9), with RLS.
--
-- Staging is the working copy of an import: rows normalized into the ledger's shape, carrying
-- their proposals, their dedupe verdict and the parser's original view, none of it visible in
-- any report until review accepts it into transaction.
--
-- Two references are deferred exactly as V10 defers its own: proposed_transfer_kind is TEXT
-- with a CHECK because the transfer_kind enum arrives with M5, and classification_rule_id is a
-- bare UUID because its target table arrives with M4. The 0018 rule is per target — each gains
-- its real type or foreign key in the migration that creates the target, not before.

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
