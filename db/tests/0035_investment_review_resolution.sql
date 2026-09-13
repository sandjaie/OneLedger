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
