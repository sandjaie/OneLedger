-- db/migrations/V10__transaction_ledger.sql
-- M1b Task 13: the transaction ledger (schema.md 0007) and its reporting surface.
--
-- Columns referencing tables from later milestones are bare UUIDs here, the same pattern
-- schema.md 0018 applies to transfer_id and its siblings: document, import batch, statement
-- layout and classification rule arrive with M2/M4, and the M4 migration that completes the
-- target set adds their foreign keys. Nothing dereferences them until then.
--
-- Money is immutable once written: amount and currency are the bank's (or the owner's) stated
-- fact (I1), and the frozen translations are computed once and never recomputed (I5). A split
-- parent is a container, not a posting — only leaf rows count, children sum exactly to the
-- parent, and reports read v_transaction_reportable, never the base table.

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

  -- frozen translations, computed once at write time (I5); the carried-forward label is I7
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

  -- links: any non-null link changes how this row is reported. Bare UUIDs until each
  -- target's own migration adds the foreign key (schema.md 0018).
  transfer_id               UUID,
  card_statement_id         UUID,
  investment_transaction_id UUID,
  loan_id                   UUID,
  loan_schedule_entry_id    UUID,
  recurring_series_id       UUID,
  parent_transaction_id     UUID REFERENCES transaction(id),

  -- provenance (I6). Document, batch and layout are bare UUIDs until M2/M4.
  source                    txn_source NOT NULL,
  source_document_id        UUID,
  source_page_number        SMALLINT,
  source_row_number         INTEGER,
  import_batch_id           UUID,
  statement_layout_id       UUID,
  layout_version            SMALLINT,

  -- dedupe
  dedupe_hash               TEXT NOT NULL,

  -- classification provenance. The rule reference is a bare UUID until M4.
  classified_by             classified_by NOT NULL DEFAULT 'unclassified',
  classification_rule_id    UUID,
  classification_confidence NUMERIC(4,3),
  is_reviewed               BOOLEAN NOT NULL DEFAULT false,

  -- user
  notes                     TEXT,
  tags                      TEXT[],
  receipt_document_id       UUID,
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
    source <> 'statement_import' OR source_document_id IS NOT NULL)
);

CREATE UNIQUE INDEX idx_txn_dedupe   ON transaction (household_id, account_id, dedupe_hash);
CREATE INDEX idx_txn_account_date    ON transaction (account_id, booked_date DESC);
CREATE INDEX idx_txn_household_date  ON transaction (household_id, booked_date DESC);
CREATE INDEX idx_txn_category        ON transaction (category_id, booked_date DESC);
CREATE INDEX idx_txn_merchant        ON transaction (merchant_id, booked_date DESC);
CREATE INDEX idx_txn_unreviewed      ON transaction (household_id, booked_date DESC)
                                     WHERE is_reviewed = false;
CREATE INDEX idx_txn_transfer        ON transaction (transfer_id) WHERE transfer_id IS NOT NULL;
CREATE INDEX idx_txn_parent          ON transaction (parent_transaction_id)
                                     WHERE parent_transaction_id IS NOT NULL;
CREATE INDEX idx_txn_search          ON transaction
                                     USING gin (to_tsvector('simple', description_raw));

-- I1 and I5: the stated facts and their frozen translations never change in place. A category
-- edit, a note, a review flag are fine; the money is not.
CREATE FUNCTION assert_transaction_money_immutable() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.amount            IS DISTINCT FROM OLD.amount
  OR NEW.currency          IS DISTINCT FROM OLD.currency
  OR NEW.original_amount   IS DISTINCT FROM OLD.original_amount
  OR NEW.original_currency IS DISTINCT FROM OLD.original_currency
  OR NEW.description_raw   IS DISTINCT FROM OLD.description_raw
  OR NEW.booked_date       IS DISTINCT FROM OLD.booked_date
  OR NEW.fx_rate_date      IS DISTINCT FROM OLD.fx_rate_date
  OR NEW.fx_rate_is_carried IS DISTINCT FROM OLD.fx_rate_is_carried
  OR NEW.fx_rate_to_base   IS DISTINCT FROM OLD.fx_rate_to_base
  OR NEW.fx_rate_to_secondary IS DISTINCT FROM OLD.fx_rate_to_secondary
  OR NEW.amount_base       IS DISTINCT FROM OLD.amount_base
  OR NEW.amount_secondary  IS DISTINCT FROM OLD.amount_secondary THEN
    RAISE EXCEPTION 'transaction % is immutable: originals (I1) and frozen translations (I5) are never edited or recomputed', OLD.id;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_transaction_money_immutable
  BEFORE UPDATE ON transaction
  FOR EACH ROW EXECUTE FUNCTION assert_transaction_money_immutable();

-- Splits are one level deep, in both directions, and a child recategorizes part of its
-- parent's movement — nothing else. The documented inherited facts (data-model, Splits) must
-- match the parent, or a child would be reported under attributes whose movement lives in
-- another account, date or denomination.
CREATE FUNCTION assert_split_one_level() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE parent transaction%ROWTYPE;
BEGIN
  IF NEW.parent_transaction_id IS NOT NULL THEN
    SELECT * INTO parent FROM transaction WHERE id = NEW.parent_transaction_id;
    IF parent.id IS NULL THEN
      RETURN NEW;  -- the foreign key reports a missing parent with the right error
    END IF;
    IF parent.parent_transaction_id IS NOT NULL THEN
      RAISE EXCEPTION 'splits are one level deep: the parent of % is itself a split child', NEW.id;
    END IF;
    IF EXISTS (SELECT FROM transaction c WHERE c.parent_transaction_id = NEW.id) THEN
      RAISE EXCEPTION 'splits are one level deep: % has children and cannot become a child', NEW.id;
    END IF;
    IF NEW.household_id          IS DISTINCT FROM parent.household_id
    OR NEW.account_id            IS DISTINCT FROM parent.account_id
    OR NEW.booked_date           IS DISTINCT FROM parent.booked_date
    OR NEW.currency              IS DISTINCT FROM parent.currency
    OR NEW.source_document_id    IS DISTINCT FROM parent.source_document_id
    OR NEW.fx_rate_date          IS DISTINCT FROM parent.fx_rate_date
    OR NEW.fx_rate_is_carried    IS DISTINCT FROM parent.fx_rate_is_carried
    OR NEW.fx_rate_to_base       IS DISTINCT FROM parent.fx_rate_to_base
    OR NEW.fx_rate_to_secondary  IS DISTINCT FROM parent.fx_rate_to_secondary THEN
      RAISE EXCEPTION 'a split child inherits its parent''s account, date, currency, provenance and frozen rates; % diverges from parent %', NEW.id, parent.id;
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_split_one_level
  BEFORE INSERT OR UPDATE OF parent_transaction_id ON transaction
  FOR EACH ROW EXECUTE FUNCTION assert_split_one_level();

-- SUM(child.amount) = parent.amount, enforced at statement end (deferrable, so a complete
-- split lands in one statement). A parent with no children is a plain posting; deleting every
-- child in one statement dissolves the split.
CREATE FUNCTION assert_split_sums_to_parent() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE pid UUID; parent_amount NUMERIC(20,4); child_sum NUMERIC(20,4);
BEGIN
  pid := COALESCE(NEW.parent_transaction_id, OLD.parent_transaction_id);
  IF pid IS NULL THEN RETURN NULL; END IF;
  SELECT amount INTO parent_amount FROM transaction WHERE id = pid;
  IF parent_amount IS NULL THEN RETURN NULL; END IF;
  SELECT sum(amount) INTO child_sum FROM transaction WHERE parent_transaction_id = pid;
  IF child_sum IS NOT NULL AND child_sum <> parent_amount THEN
    RAISE EXCEPTION 'split children of % sum to %, not the parent''s % — a split never changes a balance',
      pid, child_sum, parent_amount;
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_split_sums_to_parent
  AFTER INSERT OR UPDATE OR DELETE ON transaction
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION assert_split_sums_to_parent();

-- A row is a posting unless something points at it as a parent. security_invoker is
-- load-bearing: a view otherwise reads as its owner, and the owner bypasses RLS.
CREATE VIEW v_transaction_posting WITH (security_invoker = true) AS
SELECT t.* FROM transaction t
WHERE NOT EXISTS (SELECT 1 FROM transaction c WHERE c.parent_transaction_id = t.id);

-- The reporting guard (I3): every cash-flow, budget, spending and savings-rate query reads
-- this view, never the base table. A static check enforces that; the ledger endpoint is the
-- one deliberate non-report reader of the base table.
CREATE VIEW v_transaction_reportable WITH (security_invoker = true) AS
SELECT *
FROM   v_transaction_posting           -- split parents excluded; only leaf rows post
WHERE  transfer_id IS NULL             -- a transfer leg is never income or expense
  AND  is_excluded = false
  AND  txn_type NOT IN ('opening_balance','adjustment',
                        'transfer_in','transfer_out',
                        'investment_buy','investment_sell',
                        'loan_disbursement','loan_repayment');

ALTER TABLE transaction ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON transaction
  USING (household_id = current_household_id());

-- The account's materialised balance follows the ledger: opening_balance + the sum of its
-- posting rows. A split child recategorizes part of its parent's movement — it never moves
-- money twice — so only rows without a parent touch the balance. amount is immutable, so
-- INSERT and DELETE are the only events; the as-of only ever advances (I7: the figure carries
-- the date it is actually true for).
CREATE FUNCTION apply_transaction_to_balance() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' AND NEW.parent_transaction_id IS NULL THEN
    UPDATE account
       SET current_balance = current_balance + NEW.amount,
           current_balance_as_of = GREATEST(current_balance_as_of,
                                            NEW.booked_date::timestamptz)
     WHERE id = NEW.account_id;
  ELSIF TG_OP = 'DELETE' AND OLD.parent_transaction_id IS NULL THEN
    UPDATE account
       SET current_balance = current_balance - OLD.amount
     WHERE id = OLD.account_id;
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER trg_transaction_balance
  AFTER INSERT OR DELETE ON transaction
  FOR EACH ROW EXECUTE FUNCTION apply_transaction_to_balance();
