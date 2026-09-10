-- Card cycles: schema.md `0011`, one row per statement cycle per card account.
--
-- A card statement carries facts the transaction ledger cannot: the cycle's boundaries, what
-- was due and by when, the limits, the charges and the points. One row per cycle per account,
-- and no way to hold two — `idx_card_stmt_period` is unique on (account_id, period_end).
--
-- Signs follow ADR-0010, from the household's side. `opening_balance` and `closing_balance` are
-- signed like every balance in this schema, so a card the owner owes on has a NEGATIVE closing
-- balance. `statement_balance` is the one positive figure here: what was due, as the statement
-- prints it. ADR-0010 names the danger — a sign error is internally consistent and therefore
-- invisible — and this table invites one in particular: storing the positive amount due as the
-- closing balance. That confusion is refused rather than trusted (`owed_is_negative`).

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

  -- Points are counted, not money.
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
  CONSTRAINT due_after_statement CHECK (payment_due_date >= statement_date),
  -- Two beyond schema.md's sketch, both ADR-0010 made refusable. What was due is never
  -- negative; and a card whose closing balance is positive — in credit — has nothing due, so a
  -- row holding both a positive closing balance and an amount due has had its sign confused.
  CONSTRAINT statement_balance_is_what_was_due CHECK (statement_balance >= 0),
  CONSTRAINT owed_is_negative CHECK (closing_balance <= 0 OR statement_balance = 0)
);

CREATE UNIQUE INDEX idx_card_stmt_period ON card_statement (account_id, period_end);

-- A cycle belongs on a credit card of the same household, stated in that card's currency. The
-- foreign key says only that the account exists: a cycle on a savings account is meaningless,
-- one on another household's card is a leak, and one in another currency would make its
-- balances incomparable with the account's. V10 answers the same question for a transaction
-- in the same shape.
CREATE FUNCTION assert_card_statement_account() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE v_type account_type; v_household UUID; v_currency CHAR(3);
BEGIN
  SELECT account_type, household_id, currency INTO v_type, v_household, v_currency
    FROM account WHERE id = NEW.account_id;
  IF v_household IS DISTINCT FROM NEW.household_id THEN
    RAISE EXCEPTION 'card statement household % does not own account %',
      NEW.household_id, NEW.account_id;
  END IF;
  IF v_type IS DISTINCT FROM 'credit_card' THEN
    RAISE EXCEPTION 'a card statement belongs on a credit_card account; account % is %',
      NEW.account_id, v_type;
  END IF;
  IF rtrim(NEW.currency) <> rtrim(v_currency) THEN
    RAISE EXCEPTION 'card statement currency % must match the account''s %',
      rtrim(NEW.currency), rtrim(v_currency);
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_card_statement_account
  BEFORE INSERT OR UPDATE OF account_id, household_id, currency ON card_statement
  FOR EACH ROW EXECUTE FUNCTION assert_card_statement_account();

-- V19's and V20's shape rather than V9's read/write split: V9 splits because its categories
-- have shared seed rows with a NULL household, and a cycle always has a household.
ALTER TABLE card_statement ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON card_statement
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- The foreign key schema.md `0018` reserves for the migration that creates the target,
-- verbatim: a transaction names the cycle it was billed in.
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_card_stmt FOREIGN KEY (card_statement_id) REFERENCES card_statement(id);
