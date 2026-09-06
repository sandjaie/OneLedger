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
  -- household_id and account_id are identity, not attributes: a transaction belongs to the
  -- account it happened in, and the balance trigger never has to move money between accounts.
  -- Reassignment is a delete and a recreate, both of which rebalance correctly.
  IF NEW.household_id      IS DISTINCT FROM OLD.household_id
  OR NEW.account_id        IS DISTINCT FROM OLD.account_id
  OR NEW.amount            IS DISTINCT FROM OLD.amount
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
  -- The parent side: while children exist, the facts they inherited are fixed. Without this,
  -- moving the parent to another account would leave every child reporting a movement that
  -- now lives elsewhere.
  IF TG_OP = 'UPDATE' AND EXISTS (SELECT FROM transaction c WHERE c.parent_transaction_id = NEW.id) THEN
    IF NEW.household_id         IS DISTINCT FROM OLD.household_id
    OR NEW.account_id           IS DISTINCT FROM OLD.account_id
    OR NEW.booked_date          IS DISTINCT FROM OLD.booked_date
    OR NEW.currency             IS DISTINCT FROM OLD.currency
    OR NEW.source_document_id   IS DISTINCT FROM OLD.source_document_id
    OR NEW.fx_rate_date         IS DISTINCT FROM OLD.fx_rate_date
    OR NEW.fx_rate_is_carried   IS DISTINCT FROM OLD.fx_rate_is_carried
    OR NEW.fx_rate_to_base      IS DISTINCT FROM OLD.fx_rate_to_base
    OR NEW.fx_rate_to_secondary IS DISTINCT FROM OLD.fx_rate_to_secondary
    OR NEW.is_excluded          IS DISTINCT FROM OLD.is_excluded
    OR NEW.txn_type             IS DISTINCT FROM OLD.txn_type
    OR NEW.transfer_id          IS DISTINCT FROM OLD.transfer_id
    OR NEW.card_statement_id    IS DISTINCT FROM OLD.card_statement_id
    OR NEW.investment_transaction_id IS DISTINCT FROM OLD.investment_transaction_id
    OR NEW.loan_id              IS DISTINCT FROM OLD.loan_id
    OR NEW.loan_schedule_entry_id IS DISTINCT FROM OLD.loan_schedule_entry_id
    OR NEW.recurring_series_id  IS DISTINCT FROM OLD.recurring_series_id
    OR NEW.merchant_id          IS DISTINCT FROM OLD.merchant_id
    OR NEW.country              IS DISTINCT FROM OLD.country
    OR NEW.payment_method       IS DISTINCT FROM OLD.payment_method
    OR NEW.source               IS DISTINCT FROM OLD.source
    OR NEW.source_page_number   IS DISTINCT FROM OLD.source_page_number
    OR NEW.source_row_number    IS DISTINCT FROM OLD.source_row_number
    OR NEW.import_batch_id      IS DISTINCT FROM OLD.import_batch_id
    OR NEW.statement_layout_id  IS DISTINCT FROM OLD.statement_layout_id
    OR NEW.layout_version       IS DISTINCT FROM OLD.layout_version
    OR NEW.value_date           IS DISTINCT FROM OLD.value_date
    OR NEW.description_clean    IS DISTINCT FROM OLD.description_clean
    OR NEW.reference_raw        IS DISTINCT FROM OLD.reference_raw
    OR NEW.counterparty_raw     IS DISTINCT FROM OLD.counterparty_raw
    OR NEW.receipt_document_id  IS DISTINCT FROM OLD.receipt_document_id THEN
      RAISE EXCEPTION 'a split parent''s inherited facts are fixed while children exist: %', NEW.id;
    END IF;
  END IF;
  IF NEW.parent_transaction_id IS NOT NULL THEN
    IF NEW.parent_transaction_id = NEW.id THEN
      RAISE EXCEPTION 'a transaction cannot be its own split parent: %', NEW.id;
    END IF;
    -- Locked: two overlapping reparents (A under B while B goes under C) must serialize, or
    -- both read pre-edit parents and commit a three-level chain. Whichever waits re-reads the
    -- committed row and is rejected by the depth checks below.
    SELECT * INTO parent FROM transaction WHERE id = NEW.parent_transaction_id FOR UPDATE;
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
    OR NEW.fx_rate_to_secondary  IS DISTINCT FROM parent.fx_rate_to_secondary
    -- Report-affecting fields too: a child that is excluded, retyped or linked while its
    -- siblings are not would drop or reinterpret part of a movement the balance still holds
    -- in full. Children differ only in amount, category and notes (data-model, Splits).
    OR NEW.is_excluded           IS DISTINCT FROM parent.is_excluded
    OR NEW.txn_type              IS DISTINCT FROM parent.txn_type
    OR NEW.transfer_id           IS DISTINCT FROM parent.transfer_id
    OR NEW.card_statement_id     IS DISTINCT FROM parent.card_statement_id
    OR NEW.investment_transaction_id IS DISTINCT FROM parent.investment_transaction_id
    OR NEW.loan_id               IS DISTINCT FROM parent.loan_id
    OR NEW.loan_schedule_entry_id IS DISTINCT FROM parent.loan_schedule_entry_id
    OR NEW.recurring_series_id   IS DISTINCT FROM parent.recurring_series_id
    -- Children differ only in amount, category and notes (data-model, Splits): merchant,
    -- context and every import-provenance fact stay the parent's.
    OR NEW.merchant_id           IS DISTINCT FROM parent.merchant_id
    OR NEW.country               IS DISTINCT FROM parent.country
    OR NEW.payment_method        IS DISTINCT FROM parent.payment_method
    OR NEW.source                IS DISTINCT FROM parent.source
    OR NEW.source_page_number    IS DISTINCT FROM parent.source_page_number
    OR NEW.source_row_number     IS DISTINCT FROM parent.source_row_number
    OR NEW.import_batch_id       IS DISTINCT FROM parent.import_batch_id
    OR NEW.statement_layout_id   IS DISTINCT FROM parent.statement_layout_id
    OR NEW.layout_version        IS DISTINCT FROM parent.layout_version
    -- Source identity too: one bank movement never presents its portions under different
    -- dates, descriptions, references or counterparties.
    OR NEW.value_date            IS DISTINCT FROM parent.value_date
    OR NEW.description_raw       IS DISTINCT FROM parent.description_raw
    OR NEW.description_clean     IS DISTINCT FROM parent.description_clean
    OR NEW.reference_raw         IS DISTINCT FROM parent.reference_raw
    OR NEW.counterparty_raw      IS DISTINCT FROM parent.counterparty_raw
    -- The bank's own conversion splits with the movement: same currency, and an amount on
    -- every child exactly when the parent has one (the sum check holds the totals equal).
    OR NEW.original_currency     IS DISTINCT FROM parent.original_currency
    OR (NEW.original_amount IS NULL) <> (parent.original_amount IS NULL)
    OR NEW.receipt_document_id   IS DISTINCT FROM parent.receipt_document_id THEN
      RAISE EXCEPTION 'a split child inherits everything but amount, category and notes; % diverges from parent %', NEW.id, parent.id;
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- On every update, not only parent changes: a child's inherited columns are mutable to the
-- owner connection, and an edit to any of them must revalidate against the parent.
CREATE TRIGGER trg_split_one_level
  BEFORE INSERT OR UPDATE ON transaction
  FOR EACH ROW EXECUTE FUNCTION assert_split_one_level();

-- SUM(child.amount) = parent.amount, enforced at statement end (deferrable, so a complete
-- split lands in one statement). A parent with no children is a plain posting; deleting every
-- child in one statement dissolves the split.
CREATE FUNCTION assert_split_sums_to_parent() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE pid UUID;
        parent_amount NUMERIC(20,4); parent_base NUMERIC(20,4); parent_secondary NUMERIC(20,4);
        parent_original NUMERIC(20,4);
        child_sum NUMERIC(20,4); child_base NUMERIC(20,4); child_secondary NUMERIC(20,4);
        child_original NUMERIC(20,4);
BEGIN
  -- A reparent involves two splits: the one gaining this child and the one losing it. Both
  -- must balance, or the losing parent's leaves diverge from the materialised balance.
  FOREACH pid IN ARRAY ARRAY(
    SELECT DISTINCT unnest(ARRAY[
      CASE WHEN TG_OP <> 'DELETE' THEN NEW.parent_transaction_id END,
      CASE WHEN TG_OP <> 'INSERT' THEN OLD.parent_transaction_id END
    ])
  ) LOOP
    IF pid IS NULL THEN CONTINUE; END IF;
    -- Serialize concurrent splitters of the same parent: without the lock, two sessions each
    -- see only their own children, both sums match, and the committed total is double.
    SELECT amount, amount_base, amount_secondary, original_amount
      INTO parent_amount, parent_base, parent_secondary, parent_original
      FROM transaction WHERE id = pid FOR UPDATE;
    IF parent_amount IS NULL THEN CONTINUE; END IF;
    SELECT sum(amount), sum(amount_base), sum(amount_secondary), sum(original_amount)
      INTO child_sum, child_base, child_secondary, child_original
      FROM transaction WHERE parent_transaction_id = pid;
    -- All three sums, not only the native one: the frozen columns are what reports read, and
    -- children replace their parent there. A splitter allocates any rounding residual
    -- explicitly so the equalities hold exactly.
    IF child_sum IS NOT NULL
       AND (child_sum <> parent_amount
            OR child_base <> parent_base
            OR child_secondary <> parent_secondary
            OR (parent_original IS NOT NULL AND child_original <> parent_original)) THEN
      RAISE EXCEPTION 'split children of % must sum to the parent in amount, amount_base and amount_secondary — a split never changes a balance or a report',
        pid;
    END IF;
  END LOOP;
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

-- A transaction is denominated in its account's currency: amount joins current_balance
-- directly, so a foreign-denominated row would corrupt the native balance. The bank's own
-- conversion belongs in original_amount/original_currency.
CREATE FUNCTION assert_transaction_currency_matches_account() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE account_currency CHAR(3);
BEGIN
  SELECT currency INTO account_currency FROM account WHERE id = NEW.account_id;
  IF account_currency IS NOT NULL AND rtrim(NEW.currency) <> rtrim(account_currency) THEN
    RAISE EXCEPTION 'transaction currency % must match the account''s %; the bank''s conversion belongs in original_amount',
      rtrim(NEW.currency), rtrim(account_currency);
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_transaction_currency
  BEFORE INSERT ON transaction
  FOR EACH ROW EXECUTE FUNCTION assert_transaction_currency_matches_account();

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
    -- The as-of rewinds with the balance: a deleted latest posting must not keep labelling
    -- the figure with a date no remaining entry supports (I7).
    UPDATE account
       SET current_balance = current_balance - OLD.amount,
           current_balance_as_of = GREATEST(
             opening_balance_date::timestamptz,
             COALESCE((SELECT max(t.booked_date)::timestamptz FROM transaction t
                        WHERE t.account_id = OLD.account_id
                          AND t.parent_transaction_id IS NULL AND t.id <> OLD.id),
                      opening_balance_date::timestamptz))
     WHERE id = OLD.account_id;
  ELSIF TG_OP = 'UPDATE' THEN
    -- A topology change moves a row across the posting boundary: adopted as a child it stops
    -- counting (its parent already carries the movement); released it counts again.
    IF OLD.parent_transaction_id IS NULL AND NEW.parent_transaction_id IS NOT NULL THEN
      UPDATE account SET current_balance = current_balance - NEW.amount
       WHERE id = NEW.account_id;
    ELSIF OLD.parent_transaction_id IS NOT NULL AND NEW.parent_transaction_id IS NULL THEN
      UPDATE account
         SET current_balance = current_balance + NEW.amount,
             current_balance_as_of = GREATEST(current_balance_as_of,
                                              NEW.booked_date::timestamptz)
       WHERE id = NEW.account_id;
    END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER trg_transaction_balance
  AFTER INSERT OR DELETE OR UPDATE OF parent_transaction_id ON transaction
  FOR EACH ROW EXECUTE FUNCTION apply_transaction_to_balance();
