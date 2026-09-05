-- db/migrations/V3__accounts.sql
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
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id          UUID NOT NULL REFERENCES household(id),
  owner_person_id       UUID NOT NULL REFERENCES person(id),
  joint_owner_person_id UUID REFERENCES person(id),   -- unused in Phase 1; spouse is Phase 2
  institution_id        UUID REFERENCES institution(id),

  name                  TEXT NOT NULL,
  account_type          account_type NOT NULL,
  account_class         account_class NOT NULL DEFAULT 'asset',   -- set by trigger, never by hand
  account_subtype       TEXT,
  currency              CHAR(3) NOT NULL REFERENCES currency(code),
  country               CHAR(2) NOT NULL,
  tax_residency_context CHAR(2),

  account_number_masked TEXT,
  account_number_hash   TEXT,
  iban_masked           TEXT,

  opened_on             DATE,
  closed_on             DATE,
  is_active             BOOLEAN NOT NULL DEFAULT true,
  include_in_net_worth  BOOLEAN NOT NULL DEFAULT true,
  is_liquid             BOOLEAN NOT NULL DEFAULT true,   -- Q22: default on, user de-selects

  opening_balance       NUMERIC(20,4) NOT NULL DEFAULT 0,
  opening_balance_date  DATE NOT NULL,
  current_balance       NUMERIC(20,4) NOT NULL DEFAULT 0,
  current_balance_as_of TIMESTAMPTZ,

  credit_limit          NUMERIC(20,4),
  statement_day         SMALLINT CHECK (statement_day BETWEEN 1 AND 31),
  payment_due_day       SMALLINT CHECK (payment_due_day BETWEEN 1 AND 31),

  interest_rate_pct     NUMERIC(9,4),
  maturity_date         DATE,

  display_order         SMALLINT NOT NULL DEFAULT 0,
  color                 TEXT,
  notes                 TEXT,
  tags                  TEXT[],
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT closed_after_opened
    CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on >= opened_on),
  CONSTRAINT card_has_limit
    CHECK (account_type <> 'credit_card' OR credit_limit IS NOT NULL)
);

-- account_class is derivable from account_type but stored, because net-worth queries filter on
-- it constantly. The trigger is what keeps the denormalisation honest.
CREATE FUNCTION set_account_class() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.account_class := CASE
    WHEN NEW.account_type IN ('credit_card','personal_loan','home_loan','auto_loan',
                              'education_loan','gold_loan','loan_other','payable')
    THEN 'liability'::account_class
    ELSE 'asset'::account_class
  END;
  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE TRIGGER trg_account_class
  BEFORE INSERT OR UPDATE ON account
  FOR EACH ROW EXECUTE FUNCTION set_account_class();

CREATE INDEX idx_account_household ON account (household_id, is_active);
CREATE UNIQUE INDEX idx_account_number_hash
  ON account (household_id, account_number_hash) WHERE account_number_hash IS NOT NULL;

ALTER TABLE account ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON account USING (household_id = current_household_id());
