-- db/tests/0012_commit_import_batch.sql
-- V14: the commit path. `commit_import_batch` turns a reviewed batch into ledger rows in one
-- transaction, and `assert_account_reconciles` verifies the account's balance against the
-- statement's own closing figure inside that same transaction — so a mismatch rolls the whole
-- batch back rather than leaving half a statement in the ledger (spec section 4.9, Invariant I8).
--
-- Both are SECURITY DEFINER and both call assert_is_owner() first (ADR-0013, ADR-0016). The
-- service connects privileged and bypasses RLS, so these assertions are the boundary, not a
-- convenience over one.
--
-- Fixtures use whatever currencies this deployment seeded and the ISO 3166-1 user-assigned
-- country range, exactly as db/tests/0010 and 0011 do (ADR-0012).
\set ON_ERROR_STOP on

DROP FUNCTION IF EXISTS _t0012_fixture();

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM pg_proc WHERE proname = 'commit_import_batch') = 1,
    'commit_import_batch must exist';
  ASSERT (SELECT count(*) FROM pg_proc WHERE proname = 'assert_account_reconciles') = 1,
    'assert_account_reconciles must exist';
  ASSERT (SELECT prosecdef FROM pg_proc WHERE proname = 'commit_import_batch'),
    'commit_import_batch must be SECURITY DEFINER';
  ASSERT (SELECT prosecdef FROM pg_proc WHERE proname = 'assert_account_reconciles'),
    'assert_account_reconciles must be SECURITY DEFINER';
  RAISE NOTICE 'PASS 0012 structure';
END $$;

-- A batch that reconciles, reviewed and ready. Every later block builds on this shape.
CREATE OR REPLACE FUNCTION _t0012_fixture(
  OUT household UUID, OUT account UUID, OUT batch UUID, OUT owner_user UUID
) LANGUAGE plpgsql AS $$
DECLARE p UUID; base CHAR(3); secondary CHAR(3); layout UUID; doc UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  SELECT min(code) INTO secondary FROM currency WHERE code > base;
  ASSERT base IS NOT NULL AND secondary IS NOT NULL, 'this fixture needs two seeded currencies';

  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('commit test', base, secondary) RETURNING id INTO household;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (household, 'self', true) RETURNING id INTO p;
  -- app_user.id is the identity provider's subject, so it carries no default of its own.
  owner_user := gen_random_uuid();
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (owner_user, household, p, 'commit-test-' || owner_user || '@example.invalid', 'owner');

  -- Opening 100, and the statement below closes at 190.
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
  VALUES (household, p, 'Commit Savings', 'savings', base, 'XB', 100, '2026-01-01')
  RETURNING id INTO account;

  INSERT INTO statement_layout (household_id, name, fingerprints, columns, date_format,
                                sign_convention, has_running_balance)
  VALUES (household, 'Commit Layout', ARRAY['FINGERPRINT'], '[]'::jsonb, 'dd/MM/yyyy',
          'signed', true)
  RETURNING id INTO layout;

  INSERT INTO document (household_id, account_id, document_type, original_filename,
                        storage_bucket, storage_path, mime_type, byte_size, sha256)
  VALUES (household, account, 'bank_statement', 'commit-…0000.pdf', 'documents',
          'aa/bb/commit.pdf', 'application/pdf', 2048, 'commitfixture')
  RETURNING id INTO doc;

  INSERT INTO import_batch (household_id, document_id, account_id, status, statement_layout_id,
                            layout_version, rows_extracted, rows_new,
                            declared_opening_balance, declared_closing_balance,
                            computed_closing_balance, reconciliation_status,
                            period_start, period_end)
  VALUES (household, doc, account, 'awaiting_review', layout, 1, 3, 3,
          100, 190, 190, 'matched', '2026-03-01', '2026-03-31')
  RETURNING id INTO batch;

  -- Two accepted rows summing to +90, and one rejected row that must not reach the ledger.
  INSERT INTO staged_transaction (import_batch_id, household_id, account_id, raw_row,
                                  source_page_number, source_row_number, booked_date,
                                  description_raw, amount, currency, fx_rate_date,
                                  fx_rate_is_carried, fx_rate_to_base, fx_rate_to_secondary,
                                  amount_base, amount_secondary, proposed_txn_type,
                                  dedupe_hash, status)
  VALUES
    (batch, household, account, '{}'::jsonb, 1, 1, '2026-03-02', 'SALARY', 100, base,
     '2026-03-02', false, 1, 1, 100, 100, 'income', repeat('a', 64), 'accepted'),
    (batch, household, account, '{}'::jsonb, 1, 2, '2026-03-05', 'COFFEE', -10, base,
     '2026-03-05', false, 1, 1, -10, -10, 'expense', repeat('b', 64), 'accepted'),
    (batch, household, account, '{}'::jsonb, 1, 3, '2026-03-06', 'DUPLICATE', -25, base,
     '2026-03-06', false, 1, 1, -25, -25, 'expense', repeat('c', 64), 'rejected');
END $$;

-- ---------------------------------------------------------------------------------------
-- The authorization boundary. Every SECURITY DEFINER function asserts it first, and the
-- service bypasses RLS, so a function that trusted its caller would be the whole wall gone.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', '', true);   -- nobody

  -- The *message* is asserted, not just that something was raised. Without assert_is_owner()
  -- an unauthenticated caller is still refused — current_household_id() returns NULL, the
  -- batch lookup's household predicate matches nothing, and the miss raises. That is refusal
  -- by accident, from a NULL comparison two statements away, and it is exactly what ADR-0013's
  -- "every SECURITY DEFINER function calls this first" exists to stop being load-bearing.
  refusal := NULL;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'not authorized',
    format('commit_import_batch must assert the owner first, got: %s', refusal);

  refusal := NULL;
  BEGIN
    PERFORM assert_account_reconciles(f.account, 190::NUMERIC, '2026-03-31'::DATE);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'not authorized',
    format('assert_account_reconciles must assert the owner first, got: %s', refusal);
  RAISE NOTICE 'PASS 0012 both functions assert the owner';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- A null argument is never a passed verification. Comparing against NULL yields NULL, which
-- is not false — a check written the obvious way would silently verify nothing.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; raised BOOLEAN;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  raised := false;
  BEGIN
    PERFORM assert_account_reconciles(f.account, NULL::NUMERIC, '2026-03-31'::DATE);
  EXCEPTION WHEN OTHERS THEN raised := true;
  END;
  ASSERT raised, 'a null declared closing must raise, never verify vacuously';

  raised := false;
  BEGIN
    PERFORM assert_account_reconciles(f.account, 190::NUMERIC, NULL::DATE);
  EXCEPTION WHEN OTHERS THEN raised := true;
  END;
  ASSERT raised, 'a null period end must raise, never verify vacuously';
  RAISE NOTICE 'PASS 0012 null arguments raise';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The happy path: accepted rows become ledger rows carrying their provenance, and nothing
-- else does.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; imported INTEGER; committed RECORD; b RECORD;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  imported := commit_import_batch(f.batch);
  ASSERT imported = 2, format('two accepted rows commit, got %s', imported);

  ASSERT (SELECT count(*) FROM transaction WHERE import_batch_id = f.batch) = 2,
    'only the accepted rows reach the ledger';
  ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE dedupe_hash = repeat('c', 64)),
    'a rejected row must never commit';

  SELECT * INTO committed FROM transaction WHERE dedupe_hash = repeat('a', 64);
  -- Invariant I6: the row names where it came from, and the mapping that produced it.
  ASSERT committed.source = 'statement_import', 'source is statement_import';
  ASSERT committed.source_document_id
         = (SELECT document_id FROM import_batch WHERE id = f.batch),
    'the document named is the batch''s own, not merely some document';
  ASSERT committed.source_page_number = 1 AND committed.source_row_number = 1,
    'page and row travel through unchanged';
  ASSERT committed.statement_layout_id IS NOT NULL AND committed.layout_version = 1,
    'the layout and its version travel through unchanged';
  ASSERT committed.txn_type = 'income', 'txn_type comes from proposed_txn_type';
  ASSERT committed.amount = 100 AND committed.amount_base = 100,
    'the frozen amounts are carried, never recomputed';
  ASSERT committed.household_id = f.household AND committed.account_id = f.account,
    'the row belongs to the batch''s household and account';

  SELECT * INTO b FROM import_batch WHERE id = f.batch;
  ASSERT b.status = 'completed', format('the batch completes, got %s', b.status);
  ASSERT b.rows_imported = 2, 'rows_imported counts what committed';
  ASSERT b.completed_at IS NOT NULL, 'completed_at is stamped';
  RAISE NOTICE 'PASS 0012 accepted rows commit with their provenance';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- I8's other half: the post-commit verification. The batch's own closing figure is compared
-- against the balance the ledger now holds, inside the same transaction, so a disagreement
-- takes the whole batch with it.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; raised BOOLEAN;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  -- The statement says it closes at 500; opening 100 plus the accepted rows makes 190.
  UPDATE import_batch SET declared_closing_balance = 500 WHERE id = f.batch;

  raised := false;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN raised := true;
  END;
  ASSERT raised, 'a closing balance the ledger does not reach must raise';
END $$;
ROLLBACK;

-- The raise above aborted its transaction, so the rollback is Postgres''s. This block proves
-- the consequence that matters: after the failure, nothing of the batch is in the ledger.
BEGIN;
DO $$
DECLARE f RECORD; raised BOOLEAN;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  UPDATE import_batch SET declared_closing_balance = 500 WHERE id = f.batch;

  raised := false;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN
    raised := true;
    -- Inside the handler the statement's own effects are already undone; what this asserts is
    -- that the insert and the verification shared one statement-level scope.
    ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE import_batch_id = f.batch),
      'a failed verification leaves zero ledger rows';
    ASSERT (SELECT status FROM import_batch WHERE id = f.batch) = 'awaiting_review',
      'and the batch is not marked completed';
  END;
  ASSERT raised, 'the mismatch raised';
  RAISE NOTICE 'PASS 0012 a failed verification leaves nothing behind';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The batch must belong to the caller's household, and must have reconciled. Neither is
-- assumed from the fact that staging refused everything else (spec section 4.9 step 3).
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; other RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  SELECT * INTO other FROM _t0012_fixture();
  -- Signed in as the second household's owner, reaching for the first household's batch.
  PERFORM set_config('app.user_id', other.owner_user::TEXT, true);

  -- Again the message, not merely a raise: the batch lookup's own household predicate must be
  -- what refuses this. Drop it and the row is found, the rows insert, and only the balance
  -- verification's account check refuses — a rollback rather than a refusal, reached after the
  -- ledger has already been written to, and turning on a guard that is not looking for this.
  refusal := NULL;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'no such import batch',
    format('another household''s batch is not visible to this one, got: %s', refusal);
  ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE import_batch_id = f.batch),
    'and nothing of it committed';
END $$;
ROLLBACK;

BEGIN;
DO $$
DECLARE f RECORD; refused BOOLEAN; verdict TEXT;
BEGIN
  FOREACH verdict IN ARRAY ARRAY['mismatched', 'running_balance_break', 'unavailable'] LOOP
    SELECT * INTO f FROM _t0012_fixture();
    PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
    UPDATE import_batch SET reconciliation_status = verdict WHERE id = f.batch;

    refused := false;
    BEGIN
      PERFORM commit_import_batch(f.batch);
    EXCEPTION WHEN OTHERS THEN refused := true;
    END;
    ASSERT refused, format('a %s batch must not commit', verdict);
  END LOOP;
  RAISE NOTICE 'PASS 0012 only a matched batch belonging to the household commits';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- `assert_account_reconciles` has its own authorization, exercised on its own. Reached only
-- through the unauthenticated path it looked covered: signed in as somebody, it would happily
-- read and report another household's balance, and a missing account left every comparison
-- against NULL — a clean return having verified nothing, which is the vacuous pass the null
-- guard four lines above exists to prevent, arriving through a second door.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; other RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  SELECT * INTO other FROM _t0012_fixture();
  PERFORM set_config('app.user_id', other.owner_user::TEXT, true);

  refusal := NULL;
  BEGIN
    PERFORM assert_account_reconciles(f.account, 100::NUMERIC, '2026-03-31'::DATE);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'not authorized',
    format('another household''s account is not readable, got: %s', refusal);

  refusal := NULL;
  BEGIN
    PERFORM assert_account_reconciles(gen_random_uuid(), 100::NUMERIC, '2026-03-31'::DATE);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'not authorized',
    format('a missing account must raise, not verify nothing; and must not say it is missing, got: %s',
           refusal);

  -- The account exists and is the caller's, but the date precedes its balance anchor: the sum
  -- is empty and the opening balance would be returned as though it were the answer.
  refusal := NULL;
  BEGIN
    PERFORM assert_account_reconciles(other.account, 100::NUMERIC, '2025-12-31'::DATE);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal LIKE '%anchored at%',
    format('a date before the balance anchor is not a fact the ledger holds, got: %s', refusal);
  RAISE NOTICE 'PASS 0012 the balance check refuses what it cannot answer';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Whose money moves is decided by the batch, never by the staged row. The definer bypasses
-- RLS, so `staged_transaction`'s isolation policy does not run inside these functions and the
-- predicate has to be written out. Without it, rows re-pointed at another account commit
-- there while the verification checks the batch's account and passes on an empty change.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; other RECORD; refusal TEXT; second_account UUID; p UUID;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  SELECT owner_person_id INTO p FROM account WHERE id = f.account;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                       country, opening_balance, opening_balance_date)
  SELECT f.household, p, 'Second Savings', 'savings', currency, 'XB', 0, '2026-01-01'
  FROM account WHERE id = f.account
  RETURNING id INTO second_account;

  UPDATE staged_transaction SET account_id = second_account
  WHERE import_batch_id = f.batch AND status = 'accepted';

  refusal := NULL;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal LIKE '%belonging to another account%',
    format('a row pointing at another account must refuse, got: %s', refusal);
  ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE account_id = second_account),
    'and nothing reached that account';
END $$;
ROLLBACK;

BEGIN;
DO $$
DECLARE f RECORD; other RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  SELECT * INTO other FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  -- One accepted row re-pointed at a second household entirely.
  UPDATE staged_transaction SET household_id = other.household, account_id = other.account
  WHERE import_batch_id = f.batch AND dedupe_hash = repeat('a', 64);

  refusal := NULL;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal LIKE '%belonging to another account%',
    format('a row pointing at another household must refuse, got: %s', refusal);
  ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE household_id = other.household),
    'and the other household''s ledger is untouched';
  RAISE NOTICE 'PASS 0012 the batch decides whose ledger is written';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The batch's own rows, and only those. Without `import_batch_id = p_batch_id` on the source
-- select, every accepted row in the database commits into this batch — a regression that
-- would have shipped green.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; other RECORD; imported INTEGER;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  SELECT * INTO other FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  imported := commit_import_batch(f.batch);
  ASSERT imported = 2, format('only this batch''s accepted rows commit, got %s', imported);
  ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE household_id = other.household),
    'the other batch is untouched';
  RAISE NOTICE 'PASS 0012 a commit takes only its own batch';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- A batch that is not awaiting review does not commit. `reconciliation_status` is written at
-- staging and outlives the batch's own state, so refusing only `completed` would let a
-- cancelled or failed batch through — and `ingest/pipeline.py` allows a period to be re-staged
-- once its earlier batch is cancelled, on the stated grounds that neither can still commit.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; state TEXT;
BEGIN
  FOREACH state IN ARRAY ARRAY['cancelled', 'failed', 'uploaded'] LOOP
    SELECT * INTO f FROM _t0012_fixture();
    PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
    UPDATE import_batch SET status = state::import_status WHERE id = f.batch;

    refusal := NULL;
    BEGIN
      PERFORM commit_import_batch(f.batch);
    EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
    END;
    ASSERT refusal LIKE format('a batch in state %s cannot be imported', state),
      format('a %s batch must not commit, got: %s', state, refusal);
    ASSERT NOT EXISTS (SELECT 1 FROM transaction WHERE import_batch_id = f.batch),
      format('and a %s batch commits nothing', state);
  END LOOP;
  RAISE NOTICE 'PASS 0012 only a batch awaiting review commits';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- A row the ledger would refuse gets a sentence naming it, not a constraint name for the whole
-- statement. Section 5.3 lets review accept a row staging flagged `duplicate`, so this is the
-- ordinary path, not an exotic one.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; base CHAR(3);
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  SELECT currency INTO base FROM account WHERE id = f.account;

  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_is_carried, fx_rate_to_base,
                           fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
                           source, source_document_id, dedupe_hash)
  VALUES (f.household, f.account, '2026-03-02', 'SALARY', 100, base, '2026-03-02', false,
          1, 1, 100, 100, 'income', 'statement_import',
          (SELECT document_id FROM import_batch WHERE id = f.batch), repeat('a', 64));

  refusal := NULL;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'row 1 is already in the ledger; reject it rather than accepting it',
    format('an accepted duplicate names its row, got: %s', refusal);
  RAISE NOTICE 'PASS 0012 a row the ledger refuses is named';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The hardening that is not tidiness: pg_temp is searched first when it is absent from the
-- path, so a caller-created temp table captures every unqualified name in these functions.
-- Without `pg_temp` named last, this block reports a successful import of rows that went to a
-- temporary table and vanish at end of session, with the batch marked completed.
-- ---------------------------------------------------------------------------------------
BEGIN;
CREATE TEMP TABLE transaction (LIKE public.transaction INCLUDING DEFAULTS);
DO $$
DECLARE f RECORD; imported INTEGER;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);

  imported := commit_import_batch(f.batch);
  ASSERT imported = 2, 'the commit still runs';
  ASSERT (SELECT count(*) FROM public.transaction WHERE import_batch_id = f.batch) = 2,
    'the rows are in the real ledger, not a temp table shadowing it';
  ASSERT (SELECT count(*) FROM pg_temp.transaction) = 0,
    'and nothing was captured by the temp table';
  RAISE NOTICE 'PASS 0012 a temp table cannot capture the ledger';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- Committing twice imports once. The route is a network call away from a double submit, and
-- without this the second run inserts every accepted row again — rows V10's idx_txn_dedupe
-- would reject wholesale, turning a double click into a failed import of a batch that already
-- succeeded.
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  PERFORM commit_import_batch(f.batch);

  refusal := NULL;
  BEGIN
    PERFORM commit_import_batch(f.batch);
  EXCEPTION WHEN OTHERS THEN refusal := SQLERRM;
  END;
  ASSERT refusal = 'this batch has already been imported',
    format('a completed batch does not import twice, got: %s', refusal);
  ASSERT (SELECT count(*) FROM transaction WHERE import_batch_id = f.batch) = 2,
    'and the ledger still holds one copy';
  RAISE NOTICE 'PASS 0012 a batch imports once';
END $$;
ROLLBACK;

-- ---------------------------------------------------------------------------------------
-- The balance is taken as of the statement's close, not as of now. The multi-file screen
-- imposes no chronological order, so an older statement committed after a newer one would
-- otherwise be compared against a balance that already holds the newer postings and roll
-- back a batch that is perfectly valid (spec section 4.9 step 5).
-- ---------------------------------------------------------------------------------------
BEGIN;
DO $$
DECLARE f RECORD; imported INTEGER; base CHAR(3);
BEGIN
  SELECT * INTO f FROM _t0012_fixture();
  PERFORM set_config('app.user_id', f.owner_user::TEXT, true);
  SELECT currency INTO base FROM account WHERE id = f.account;

  -- A later month is already in the ledger. It must not count towards March's close.
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_is_carried, fx_rate_to_base,
                           fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
                           source, source_document_id, dedupe_hash)
  VALUES (f.household, f.account, '2026-04-10', 'APRIL RENT', -300, base, '2026-04-10',
          false, 1, 1, -300, -300, 'expense', 'statement_import',
          (SELECT document_id FROM import_batch WHERE id = f.batch), repeat('d', 64));

  imported := commit_import_batch(f.batch);
  ASSERT imported = 2, 'the March batch commits despite April already being posted';
  RAISE NOTICE 'PASS 0012 the balance is taken as of the statement close';
END $$;
ROLLBACK;

DROP FUNCTION _t0012_fixture();
