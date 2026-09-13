-- V38: validated investment-event review resolution.
\set ON_ERROR_STOP on

DO $$
DECLARE review_index RECORD;
BEGIN
  ASSERT (
    SELECT prosecdef FROM pg_proc
     WHERE oid =
       'resolve_investment_review_item(uuid,investment_review_resolution_kind,uuid)'::regprocedure
  ), 'review resolution must stay behind an owner-authorized definer operation';
  ASSERT has_function_privilege(
    'authenticated',
    'resolve_investment_review_item(uuid,investment_review_resolution_kind,uuid)',
    'EXECUTE'
  ), 'authenticated owners need the review-resolution operation';
  ASSERT NOT has_function_privilege(
    'public',
    'resolve_investment_review_item(uuid,investment_review_resolution_kind,uuid)',
    'EXECUTE'
  ), 'review resolution must not remain executable by public';

  SELECT indexrelid::regclass AS name, indisunique, pg_get_expr(indpred, indrelid) AS predicate
    INTO review_index FROM pg_index
   WHERE indexrelid = 'idx_investment_review_one_event_reason'::regclass;
  ASSERT review_index.indisunique
     AND review_index.predicate = '(investment_transaction_id IS NOT NULL)',
    'one event/reason review identity must remain unique across open and resolved history';
  RAISE NOTICE 'PASS 0035 review resolution operation and historical uniqueness';
END $$;

CREATE FUNCTION pg_temp.t0035_event(
  p_account UUID, p_security UUID, p_currency CHAR(3), p_date DATE,
  p_kind investment_event_kind, p_source TEXT, p_corrects UUID DEFAULT NULL
) RETURNS UUID LANGUAGE sql AS $$
  SELECT append_investment_transaction(
    p_account, p_security, p_date, p_kind,
    CASE WHEN p_kind = 'purchase' THEN 10.00000000 ELSE -1.00000000 END,
    1.000000,
    CASE WHEN p_kind = 'purchase' THEN 10.0000 ELSE 1.0000 END,
    0.0000, 0.0000,
    CASE WHEN p_kind = 'purchase' THEN -10.0000 ELSE 1.0000 END,
    p_currency, p_date, 1.0000000000, 1.0000000000,
    CASE WHEN p_kind = 'purchase' THEN -10.0000 ELSE 1.0000 END,
    CASE WHEN p_kind = 'purchase' THEN -10.0000 ELSE 1.0000 END,
    'owner_entered', 'review_resolution_v1', p_source, NULL, NULL,
    p_corrects, CASE WHEN p_corrects IS NULL THEN NULL ELSE 'Synthetic correction' END
  )
$$;

CREATE FUNCTION pg_temp.t0035_is_open(p_review UUID) RETURNS BOOLEAN
  LANGUAGE sql STABLE AS $$
  SELECT status = 'open' AND resolution_kind IS NULL
     AND resolution_reference_id IS NULL AND resolved_by IS NULL AND resolved_at IS NULL
    FROM investment_review_item WHERE id = p_review
$$;

BEGIN;
DO $$
DECLARE
  base CHAR(3); secondary CHAR(3); h1 UUID; h2 UUID; p1 UUID; pm UUID; p2 UUID;
  u1 UUID; um UUID; u2 UUID; v_account_id UUID; v_security_id UUID; holding_id UUID;
  original_1 UUID; replacement_1 UUID; original_2 UUID; replacement_2 UUID;
  review_1 UUID; review_2 UUID; wrong_reason UUID; first_resolved_at TIMESTAMPTZ;
BEGIN
  SELECT min(code), max(code) INTO base, secondary FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('review household one', base, secondary) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('review household two', base, secondary) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'review owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'review member', false) RETURNING id INTO pm;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other review owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'review-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, pm, 'review-member@example.invalid', 'member')
  RETURNING id INTO um;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-review-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (
    household_id, owner_person_id, name, account_type, currency, country,
    opening_balance, opening_balance_date
  ) VALUES (
    h1, p1, 'Synthetic review account', 'brokerage', base, 'XZ', 0, '2026-01-01'
  ) RETURNING id INTO v_account_id;
  INSERT INTO security (household_id, security_type, name, currency, country, asset_class)
  VALUES (h1, 'mutual_fund', 'Synthetic review security', base, 'XZ', 'equity')
  RETURNING id INTO v_security_id;

  PERFORM set_config('app.user_id', u1::TEXT, true);
  PERFORM pg_temp.t0035_event(
    v_account_id, v_security_id, base, '2026-02-01', 'purchase', 'review-seed');
  original_1 := pg_temp.t0035_event(
    v_account_id, v_security_id, base, '2026-02-02', 'sale', 'review-original-one');
  replacement_1 := pg_temp.t0035_event(
    v_account_id, v_security_id, base, '2026-02-03', 'sale', 'review-replacement-one', original_1);
  original_2 := pg_temp.t0035_event(
    v_account_id, v_security_id, base, '2026-02-04', 'sale', 'review-original-two');
  replacement_2 := pg_temp.t0035_event(
    v_account_id, v_security_id, base, '2026-02-05', 'sale', 'review-replacement-two', original_2);
  SELECT id INTO holding_id FROM holding
   WHERE household_id = h1 AND account_id = v_account_id AND security_id = v_security_id;
  INSERT INTO investment_review_item (
    household_id, investment_transaction_id, holding_id, reason
  ) VALUES (h1, original_1, holding_id, 'incomplete_lot_allocation') RETURNING id INTO review_1;
  INSERT INTO investment_review_item (
    household_id, investment_transaction_id, holding_id, reason
  ) VALUES (h1, original_2, holding_id, 'incomplete_lot_allocation') RETURNING id INTO review_2;
  INSERT INTO investment_review_item (
    household_id, investment_transaction_id, holding_id, reason
  ) VALUES (h1, original_1, holding_id, 'duplicate_conflict') RETURNING id INTO wrong_reason;

  PERFORM set_config('app.user_id', um::TEXT, true);
  BEGIN
    PERFORM resolve_investment_review_item(review_2, 'event_corrected', replacement_2);
    RAISE EXCEPTION 'a member resolved an investment review';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  ASSERT pg_temp.t0035_is_open(review_2), 'member refusal changed review state';

  PERFORM set_config('app.user_id', u2::TEXT, true);
  BEGIN
    PERFORM resolve_investment_review_item(review_2, 'event_corrected', replacement_2);
    RAISE EXCEPTION 'another household resolved an investment review';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
  ASSERT pg_temp.t0035_is_open(review_2), 'foreign-household refusal changed review state';

  PERFORM set_config('app.user_id', u1::TEXT, true);
  BEGIN
    PERFORM resolve_investment_review_item(wrong_reason, 'event_corrected', replacement_1);
    RAISE EXCEPTION 'an unrelated review reason was resolved as an event correction';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  ASSERT pg_temp.t0035_is_open(wrong_reason), 'wrong-reason refusal changed review state';
  BEGIN
    PERFORM resolve_investment_review_item(review_2, 'event_corrected', replacement_1);
    RAISE EXCEPTION 'a review accepted the wrong replacement event';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  ASSERT pg_temp.t0035_is_open(review_2), 'wrong-reference refusal changed review state';
  BEGIN
    PERFORM resolve_investment_review_item(review_2, 'lot_allocated', replacement_2);
    RAISE EXCEPTION 'an unsupported resolution kind closed the review';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
  ASSERT pg_temp.t0035_is_open(review_2), 'wrong-kind refusal changed review state';

  ASSERT resolve_investment_review_item(
    review_1, 'event_corrected', replacement_1) = review_1,
    'owner resolution returns the retained review identity';
  SELECT resolved_at INTO first_resolved_at FROM investment_review_item WHERE id = review_1;
  ASSERT (SELECT status = 'resolved' AND resolution_kind = 'event_corrected'
                 AND resolution_reference_id = replacement_1 AND resolved_by = u1
                 AND resolved_at IS NOT NULL
            FROM investment_review_item WHERE id = review_1),
    'owner resolution retains exact resolver and replacement facts';
  ASSERT resolve_investment_review_item(
    review_1, 'event_corrected', replacement_1) = review_1,
    'identical resolution replay returns the same review';
  ASSERT (SELECT resolved_at = first_resolved_at FROM investment_review_item WHERE id = review_1),
    'identical resolution replay cannot rewrite resolved history';
  BEGIN
    PERFORM resolve_investment_review_item(review_1, 'event_corrected', replacement_2);
    RAISE EXCEPTION 'a resolved review accepted a conflicting reference';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
  ASSERT (SELECT status = 'resolved' AND resolution_kind = 'event_corrected'
                 AND resolution_reference_id = replacement_1 AND resolved_by = u1
                 AND resolved_at = first_resolved_at
            FROM investment_review_item WHERE id = review_1),
    'conflicting resolution refusal changed retained state';
  RAISE NOTICE 'PASS 0035 owner resolution, replay and scoped refusals';
END $$;
ROLLBACK;
