-- V39: complete evidenced lot allocation resolves its retained review through the owner boundary.
\set ON_ERROR_STOP on

DO $$ BEGIN
  ASSERT NOT has_table_privilege('authenticated', 'investment_lot_allocation_decision', 'INSERT'),
    'authenticated callers must not fabricate lot decisions';
  ASSERT EXISTS (SELECT FROM pg_trigger WHERE tgrelid =
    'investment_lot_allocation_decision'::regclass AND tgname = 'trg_lot_decision_is_immutable'),
    'lot decisions require an immutable direct-DML boundary';
END $$;

BEGIN;
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); household_id UUID; other_household_id UUID;
  person_id UUID; member_id UUID; other_person_id UUID;
  owner_id UUID; member_user_id UUID; other_owner_id UUID;
  account_id UUID; security_id UUID; lot_id UUID; holding_id UUID;
  purchase_id UUID; disposal_id UUID; review_id UUID; decision_key UUID := gen_random_uuid();
  first_resolution_time TIMESTAMPTZ; created BOOLEAN; cost NUMERIC; gain NUMERIC; remaining NUMERIC;
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
  SELECT result.created, result.realised_cost_native, result.realised_gain_native,
         result.remaining_basis_native INTO created, cost, gain, remaining
    FROM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[4.00000000], repeat('a', 64), decision_key) result;
  ASSERT created AND cost = 8.0000 AND gain = 4.0000 AND remaining = 12.0000,
    'owner operation retains exact allocation claims';
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
  SELECT result.created INTO created FROM confirm_investment_lot_allocation(
    disposal_id, ARRAY[lot_id], ARRAY[4.00000000], repeat('a', 64), decision_key) result;
  ASSERT NOT created, 'identical owner allocation replay created a second decision';
  ASSERT (SELECT decided_by = owner_id AND request_hash = investment_lot_request_hash(
      disposal_id, ARRAY[lot_id], ARRAY[4.00000000], repeat('a', 64))
    FROM investment_lot_allocation_decision WHERE idempotency_key = decision_key),
    'the database operation derives decision actor and request hash';

  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[4.000000001], repeat('a', 64), decision_key);
    RAISE EXCEPTION 'over-precision replay returned the retained decision';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[]::UUID[], ARRAY[]::NUMERIC[], repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'an empty lot decision was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[]::NUMERIC[], repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'mismatched lot decision arrays were accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id, lot_id], ARRAY[2.00000000, 2.00000000],
      repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'duplicate lot decision references were accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[0.00000000], repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'a non-positive lot decision was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[NULL::UUID], ARRAY[4.00000000], repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'a null lot reference was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[NULL::NUMERIC], repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'a null allocated unit value was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      NULL, ARRAY[lot_id], ARRAY[4.00000000], repeat('a', 64), gen_random_uuid());
    RAISE EXCEPTION 'a null disposal reference was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[4.00000000], NULL, gen_random_uuid());
    RAISE EXCEPTION 'a null expected state was accepted';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    PERFORM confirm_investment_lot_allocation(
      disposal_id, ARRAY[lot_id], ARRAY[4.00000000], repeat('a', 64), NULL);
    RAISE EXCEPTION 'a null idempotency key was accepted';
  EXCEPTION WHEN check_violation OR invalid_parameter_value THEN NULL;
  END;
  ASSERT (SELECT count(*) = 1 AND min(allocated_units) = 4.00000000
            FROM investment_lot_allocation WHERE disposal_transaction_id = disposal_id),
    'invalid lot decision input changed retained allocations';
  ASSERT (SELECT count(*) = 1 AND min(realised_cost_native) = 8.0000
                 AND min(realised_gain_native) = 4.0000
                 AND min(remaining_basis_native) = 12.0000
            FROM investment_lot_allocation_decision),
    'invalid lot decision input changed retained decision claims';
  ASSERT (SELECT status = 'resolved' AND resolution_kind = 'lot_allocated'
                 AND resolved_at = first_resolution_time
            FROM investment_review_item WHERE id = review_id),
    'invalid lot decision input changed retained review state';

  BEGIN
    INSERT INTO investment_lot_allocation_decision (
      household_id, idempotency_key, request_hash, disposal_transaction_id,
      realised_cost_native, realised_gain_native, remaining_basis_native,
      allocation_complete, decided_by)
    VALUES (household_id, gen_random_uuid(), repeat('b', 64), disposal_id,
            1, NULL, NULL, false, owner_id);
    RAISE EXCEPTION 'an invalid partial decision claim was stored';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    INSERT INTO investment_lot_allocation_decision (
      household_id, idempotency_key, request_hash, disposal_transaction_id,
      allocation_complete, decided_by)
    VALUES (household_id, gen_random_uuid(), repeat('b', 64), disposal_id, true, owner_id);
    RAISE EXCEPTION 'an invalid complete decision claim was stored';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  BEGIN
    UPDATE investment_lot_allocation_decision SET realised_gain_native = 5
     WHERE idempotency_key = decision_key;
    RAISE EXCEPTION 'a retained lot decision was updated directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;
  BEGIN
    DELETE FROM investment_lot_allocation_decision WHERE idempotency_key = decision_key;
    RAISE EXCEPTION 'a retained lot decision was deleted directly';
  EXCEPTION WHEN object_not_in_prerequisite_state THEN NULL;
  END;
  PERFORM set_config('app.user_id', member_user_id::TEXT, true);
  BEGIN
    SET LOCAL ROLE authenticated;
    INSERT INTO investment_lot_allocation_decision (
      household_id, idempotency_key, request_hash, disposal_transaction_id,
      realised_cost_native, realised_gain_native, allocation_complete, decided_by)
    VALUES (household_id, gen_random_uuid(), repeat('c', 64), disposal_id,
            8, 4, true, member_user_id);
    RESET ROLE;
    RAISE EXCEPTION 'a member inserted a fabricated lot decision';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  PERFORM set_config('app.user_id', other_owner_id::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM investment_lot_allocation_decision) = 0,
    'another household can see retained lot decisions';
  RESET ROLE;
  RAISE NOTICE 'PASS 0036 complete lot evidence resolves review once';
END $$;
ROLLBACK;
