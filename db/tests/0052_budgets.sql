-- V55: budget lines — the scope shape, both partial unique indexes, the period-month constraint,
-- amount and note bounds, and household isolation. The plain `REFERENCES category(id)` foreign
-- key carries no tenant scope by design (see V55's own comment); the tenant boundary is
-- `routes/budgets.py`'s job and is proven in `api/tests/test_budgets_live.py`, not here.
-- All names, identifiers and amounts in tests and fixtures are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_tables
           WHERE schemaname = 'public' AND tablename = 'budget_line') = 1,
    'the budget_line table must exist';
  ASSERT (SELECT relrowsecurity FROM pg_class WHERE oid = 'budget_line'::regclass),
    'budget_line must have row level security enabled';
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'budget_line' AND column_name = 'period_month') = 1,
    'budget_line must carry period_month';
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'budget_line' AND column_name = 'rollover_enabled') = 1,
    'budget_line must carry rollover_enabled';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'uq_budget_line_household_id') = 1,
    'uq_budget_line_household_id must exist';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'uq_budget_line_idempotency') = 1,
    'uq_budget_line_idempotency must exist';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'budget_line_scope_shape') = 1,
    'budget_line_scope_shape must exist';
  ASSERT (SELECT count(*) FROM pg_constraint
           WHERE conname = 'budget_line_period_month_is_first_day') = 1,
    'budget_line_period_month_is_first_day must exist';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'budget_line_amount_is_positive') = 1,
    'budget_line_amount_is_positive must exist';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'budget_line_note_is_bounded') = 1,
    'budget_line_note_is_bounded must exist';
  ASSERT (SELECT count(*) FROM pg_indexes
           WHERE indexname = 'uq_budget_line_month_category') = 1,
    'uq_budget_line_month_category must exist';
  ASSERT (SELECT count(*) FROM pg_indexes
           WHERE indexname = 'uq_budget_line_month_bucket') = 1,
    'uq_budget_line_month_bucket must exist';
  RAISE NOTICE 'PASS 0052 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0052_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    cat1 UUID, cat2 UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('budgets household one', base, (SELECT max(code) FROM currency)) RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('budgets household two', base, (SELECT max(code) FROM currency)) RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'budgets owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'budgets member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other budgets owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'budgets-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'budgets-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-budgets-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h1, 'Synthetic budgets category one', 'synthetic-budgets-category-one', 'expense', 'need')
  RETURNING id INTO cat1;
  INSERT INTO category (household_id, name, slug, kind, bucket)
  VALUES (h1, 'Synthetic budgets category two', 'synthetic-budgets-category-two', 'expense', 'want')
  RETURNING id INTO cat2;
  RETURN NEXT;
END $$;

-- The scope shape: neither a category nor a bucket, or both, is refused.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0052_fixture();

  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, amount_base, currency)
    VALUES (f.h1, gen_random_uuid(), '2027-01-01', 100, f.base);
    RAISE EXCEPTION 'a line with neither a category nor a bucket must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%budget_line_scope_shape%', refusal;
  END;
  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, bucket,
                              amount_base, currency)
    VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 'need', 100, f.base);
    RAISE EXCEPTION 'a line with both a category and a bucket must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%budget_line_scope_shape%', refusal;
  END;

  INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 100, f.base);
  INSERT INTO budget_line (household_id, idempotency_key, period_month, bucket, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-01-01', 'want', 100, f.base);
  ASSERT (SELECT count(*) FROM budget_line WHERE household_id = f.h1) = 2,
    'exactly the two valid rows must be accepted';

  RAISE NOTICE 'PASS 0052 scope shape';
END $$;
ROLLBACK;

-- One line per household per month per category, and per bucket.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0052_fixture();

  INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 100, f.base);
  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id,
                              amount_base, currency)
    VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 200, f.base);
    RAISE EXCEPTION 'a second line for the same category and month must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_budget_line_month_category%', refusal;
  END;
  -- A different category, or the same category in a different month, is unaffected.
  INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat2, 100, f.base);
  INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-02-01', f.cat1, 100, f.base);

  INSERT INTO budget_line (household_id, idempotency_key, period_month, bucket, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-01-01', 'need', 100, f.base);
  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, bucket, amount_base,
                              currency)
    VALUES (f.h1, gen_random_uuid(), '2027-01-01', 'need', 200, f.base);
    RAISE EXCEPTION 'a second line for the same bucket and month must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_budget_line_month_bucket%', refusal;
  END;
  -- A second household may budget the same category and month freely.
  INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, amount_base,
                            currency)
  VALUES (f.h2, gen_random_uuid(), '2027-01-01', f.cat1, 100, f.base);

  RAISE NOTICE 'PASS 0052 one line per month per category and per bucket';
END $$;
ROLLBACK;

-- The period-month constraint, the positive-amount bound and the note bound.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0052_fixture();

  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id,
                              amount_base, currency)
    VALUES (f.h1, gen_random_uuid(), '2027-01-15', f.cat1, 100, f.base);
    RAISE EXCEPTION 'a period_month that is not the first of the month must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%budget_line_period_month_is_first_day%', refusal;
  END;
  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id,
                              amount_base, currency)
    VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 0, f.base);
    RAISE EXCEPTION 'a non-positive amount must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%budget_line_amount_is_positive%', refusal;
  END;
  BEGIN
    INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id,
                              amount_base, currency, note)
    VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 100, f.base, repeat('x', 201));
    RAISE EXCEPTION 'a note over 200 characters must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%budget_line_note_is_bounded%', refusal;
  END;

  RAISE NOTICE 'PASS 0052 period month, amount and note bounds';
END $$;
ROLLBACK;

-- Owner, member, foreign and anonymous RLS.
BEGIN;
DO $$
DECLARE f RECORD;
BEGIN
  SELECT * INTO f FROM pg_temp.t0052_fixture();
  INSERT INTO budget_line (household_id, idempotency_key, period_month, category_id, amount_base,
                            currency)
  VALUES (f.h1, gen_random_uuid(), '2027-01-01', f.cat1, 100, f.base);
  INSERT INTO budget_line (household_id, idempotency_key, period_month, bucket, amount_base,
                            currency)
  VALUES (f.h2, gen_random_uuid(), '2027-01-01', 'need', 100, f.base);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM budget_line) = 1, 'owner sees only its own household''s lines';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM budget_line) = 0, 'a household member reads no line';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM budget_line) = 1, 'the other household sees only its own line';
  ASSERT (SELECT count(*) FROM budget_line WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s lines';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM budget_line) = 0, 'anonymous budget_line reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0052 owner, member, foreign and anonymous RLS';
END $$;
ROLLBACK;
