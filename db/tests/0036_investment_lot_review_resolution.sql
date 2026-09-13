-- V39: complete evidenced lot allocation resolves its retained review through the owner boundary.
\set ON_ERROR_STOP on

BEGIN;
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); household_id UUID; other_household_id UUID;
  person_id UUID; member_id UUID; other_person_id UUID;
  owner_id UUID; member_user_id UUID; other_owner_id UUID;
  account_id UUID; security_id UUID; lot_id UUID; holding_id UUID;
  purchase_id UUID; disposal_id UUID; review_id UUID; first_resolution_time TIMESTAMPTZ;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('lot review household', base, secondary) RETURNING id INTO household_id;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('other lot review household', base, secondary) RETURNING id INTO other_household_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household_id, 'lot review owner', true) RETURNING id INTO person_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household_id, 'lot review member', false) RETURNING id INTO member_id;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (other_household_id, 'other lot review owner', true) RETURNING id INTO other_person_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), household_id, person_id, 'lot-review-owner@example.invalid', 'owner')
  RETURNING id INTO owner_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), household_id, member_id, 'lot-review-member@example.invalid', 'member')
  RETURNING id INTO member_user_id;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), other_household_id, other_person_id,
          'other-lot-review-owner@example.invalid', 'owner')
  RETURNING id INTO other_owner_id;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    household_id, person_id, 'Synthetic lot review account', 'brokerage', base, 'XZ', 0,
    '2026-01-01'
  ) RETURNING id INTO account_id;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (household_id, 'mutual_fund', 'Synthetic lot review security', base, 'XZ', 'equity')
  RETURNING id INTO security_id;

  PERFORM set_config('app.user_id', owner_id::TEXT, true);
  SELECT append_investment_transaction(
    account_id, security_id, '2026-02-01', 'purchase', 10, 2, 20, 0, 0, -20, base,
    '2026-02-01', 1, 1, -20, -20, 'owner_entered', 'lot_review_v1', 'purchase',
    NULL, NULL, NULL, NULL
  ) INTO purchase_id;
  SELECT append_investment_transaction(
    account_id, security_id, '2026-02-02', 'sale', -4, 3, 12, 0, 0, 12, base,
    '2026-02-02', 1, 1, 12, 12, 'owner_entered', 'lot_review_v1', 'sale',
    NULL, NULL, NULL, NULL
  ) INTO disposal_id;
  SELECT lot.id, lot.holding_id INTO lot_id, holding_id FROM acquisition_lot lot
   WHERE lot.origin_transaction_id = purchase_id;
  INSERT INTO investment_review_item (
    household_id, investment_transaction_id, holding_id, reason
  ) VALUES (
    household_id, disposal_id, holding_id, 'incomplete_lot_allocation'
  ) RETURNING id INTO review_id;

  BEGIN
    PERFORM resolve_investment_review_item(review_id, 'lot_allocated', disposal_id);
    RAISE EXCEPTION 'missing lot evidence resolved the review';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  ASSERT (SELECT status = 'open' FROM investment_review_item WHERE id = review_id),
    'missing-evidence refusal changed retained review state';
  PERFORM allocate_investment_lots(disposal_id, ARRAY[lot_id], ARRAY[4.00000000]);

  BEGIN
    PERFORM resolve_investment_review_item(review_id, 'lot_allocated', purchase_id);
    RAISE EXCEPTION 'lot allocation resolved against the wrong event';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  ASSERT (SELECT status = 'open' FROM investment_review_item WHERE id = review_id),
    'wrong-reference refusal changed retained review state';

  PERFORM set_config('app.user_id', member_user_id::TEXT, true);
  BEGIN
    PERFORM resolve_investment_review_item(review_id, 'lot_allocated', disposal_id);
    RAISE EXCEPTION 'a member resolved evidenced lot allocation';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  ASSERT (SELECT status = 'open' FROM investment_review_item WHERE id = review_id),
    'member refusal changed retained review state';

  PERFORM set_config('app.user_id', other_owner_id::TEXT, true);
  BEGIN
    PERFORM resolve_investment_review_item(review_id, 'lot_allocated', disposal_id);
    RAISE EXCEPTION 'another household resolved evidenced lot allocation';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  ASSERT (SELECT status = 'open' FROM investment_review_item WHERE id = review_id),
    'foreign refusal changed retained review state';

  PERFORM set_config('app.user_id', owner_id::TEXT, true);
  ASSERT resolve_investment_review_item(review_id, 'lot_allocated', disposal_id) = review_id,
    'owner resolution returns the retained review identity';
  SELECT review.resolved_at INTO first_resolution_time FROM investment_review_item review
   WHERE review.id = review_id;
  ASSERT (SELECT status = 'resolved' AND resolution_kind = 'lot_allocated'
                 AND resolution_reference_id = disposal_id AND resolved_by = owner_id
            FROM investment_review_item WHERE id = review_id),
    'complete lot evidence retains exact resolution provenance';
  ASSERT resolve_investment_review_item(review_id, 'lot_allocated', disposal_id) = review_id,
    'identical lot-resolution replay is idempotent';
  ASSERT (SELECT investment_review_item.resolved_at = first_resolution_time
            FROM investment_review_item WHERE id = review_id),
    'identical replay changed retained resolution time';
  RAISE NOTICE 'PASS 0036 complete lot evidence resolves review once';
END $$;
ROLLBACK;
