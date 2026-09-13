-- V34: funding attaches only to the active acquisition-correction leaf.
-- All identities and values are synthetic.
\set ON_ERROR_STOP on

DO $$
DECLARE definition TEXT;
BEGIN
  ASSERT EXISTS (
    SELECT FROM pg_trigger WHERE tgname = 'trg_cash_allocation_requires_active_event'
  ), 'cash allocations need an active-event guard';
  ASSERT EXISTS (
    SELECT FROM pg_trigger WHERE tgname = 'trg_correction_requires_unfunded_event'
  ), 'corrections need an unfunded-original guard';
  SELECT pg_get_functiondef('unlink_investment_cash_allocations(uuid)'::regprocedure)
    INTO definition;
  ASSERT position('ORDER BY event.id FOR UPDATE' IN definition) > 0,
    'unlink must lock referenced events deterministically';
  ASSERT position('ORDER BY event.id FOR UPDATE' IN definition)
       < position('FROM transaction cash' IN definition),
    'unlink must lock events before its cash leaf';
END $$;

CREATE FUNCTION pg_temp.t0031_event(
  account_id UUID, security_id UUID, event_key TEXT, corrected_id UUID DEFAULT NULL
) RETURNS UUID LANGUAGE sql AS $$
  SELECT append_investment_transaction(
    account_id, security_id, '2026-04-01', 'purchase', 1.00000000, 100.000000,
    100.0000, 0.0000, 0.0000, -100.0000, (SELECT currency FROM security WHERE id = security_id),
    '2026-04-01', 1, 1, -100.0000, -100.0000, 'owner_entered', 'funding_guard_v1',
    event_key, NULL, NULL, corrected_id,
    CASE WHEN corrected_id IS NULL THEN NULL ELSE 'Synthetic correction' END)
$$;

CREATE FUNCTION pg_temp.t0031_cash(
  household_id UUID, account_id UUID, currency CHAR(3), cash_key TEXT
) RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE result UUID;
BEGIN
  INSERT INTO transaction (
    household_id, account_id, booked_date, description_raw, amount, currency,
    fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
    amount_secondary, txn_type, source, dedupe_hash
  ) VALUES (
    household_id, account_id, '2026-04-01', 'SYNTHETIC FUNDING GUARD', -100.0000,
    currency, '2026-04-01', 1, 1, -100.0000, -100.0000, 'expense', 'manual', cash_key
  ) RETURNING id INTO result;
  RETURN result;
END $$;

BEGIN;
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); household_id UUID; person_id UUID; user_id UUID;
  investment_account UUID; cash_account UUID; security_id UUID;
  original UUID; replacement UUID; funded UUID; cash_one UUID; cash_two UUID; refusal TEXT;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('funding guard household', base, secondary) RETURNING id INTO household_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household_id, 'funding guard owner', true) RETURNING id INTO person_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), household_id, person_id, 'funding-guard@example.invalid', 'owner')
  RETURNING id INTO user_id;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (household_id, person_id, 'Synthetic investment', 'brokerage', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO investment_account;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (household_id, person_id, 'Synthetic cash', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO cash_account;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (household_id, 'equity', 'Synthetic funding guard', base, 'XZ', 'equity')
  RETURNING id INTO security_id;
  PERFORM set_config('app.user_id', user_id::TEXT, true);

  original := pg_temp.t0031_event(investment_account, security_id, 'corrected-original');
  replacement := pg_temp.t0031_event(
    investment_account, security_id, 'active-replacement', original);
  cash_one := pg_temp.t0031_cash(household_id, cash_account, base, 'replacement-cash');
  BEGIN
    PERFORM confirm_investment_cash_allocations(
      ARRAY[original], ARRAY[cash_one], ARRAY['funding']::investment_cash_allocation_role[],
      ARRAY[100.0000]);
    RAISE EXCEPTION 'a corrected original accepted funding';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'cash funding requires the active investment event', refusal;
  END;
  PERFORM confirm_investment_cash_allocations(
    ARRAY[replacement], ARRAY[cash_one], ARRAY['funding']::investment_cash_allocation_role[],
    ARRAY[100.0000]);
  ASSERT (SELECT investment_transaction_id = replacement FROM investment_cash_allocation
           WHERE cash_transaction_id = cash_one),
    'the active replacement must accept funding';

  funded := pg_temp.t0031_event(investment_account, security_id, 'funded-original');
  cash_two := pg_temp.t0031_cash(household_id, cash_account, base, 'funded-cash');
  PERFORM confirm_investment_cash_allocations(
    ARRAY[funded], ARRAY[cash_two], ARRAY['funding']::investment_cash_allocation_role[],
    ARRAY[100.0000]);
  BEGIN
    PERFORM pg_temp.t0031_event(investment_account, security_id, 'blocked-correction', funded);
    RAISE EXCEPTION 'a funded acquisition accepted a correction';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal = 'a funded investment event cannot be corrected', refusal;
  END;
  ASSERT (SELECT count(*) = 0 FROM investment_transaction WHERE corrects_transaction_id = funded),
    'the refused correction must append no event';
  ASSERT (SELECT count(*) = 1 FROM investment_cash_allocation
           WHERE investment_transaction_id = funded),
    'the refused correction must preserve funding';
  PERFORM unlink_investment_cash_allocations(cash_one);
  ASSERT (SELECT count(*) = 0 FROM investment_cash_allocation
           WHERE cash_transaction_id = cash_one),
    'event-first unlink must still remove every selected cash allocation';
END $$;
ROLLBACK;
