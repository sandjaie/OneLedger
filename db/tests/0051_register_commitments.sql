-- V54: a commitment sourced from a register record — one source per commitment, one open
-- commitment per record, and the replaced `assert_commitment_payment()` refusing an `income`
-- posting for a claim- or deposit-sourced commitment while every V48 condition still holds
-- (0045 re-runs against this migrated schema and still passes, so this file only exercises what
-- V54 adds). `register_document`'s own isolation and foreign document scope key are new here too
-- — V53's 0050 only checked the table exists. All names, identifiers and amounts are synthetic.
\set ON_ERROR_STOP on

DO $$
BEGIN
  ASSERT (SELECT count(*) FROM information_schema.columns
           WHERE table_name = 'commitment' AND column_name = 'source_register_record_id') = 1,
    'commitment must carry source_register_record_id';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'commitment_has_one_source') = 1,
    'commitment_has_one_source must exist';
  ASSERT (SELECT count(*) FROM pg_constraint WHERE conname = 'fk_commitment_register_scope') = 1,
    'fk_commitment_register_scope must exist';
  ASSERT (SELECT count(*) FROM pg_indexes
           WHERE indexname = 'uq_commitment_open_register_record') = 1,
    'uq_commitment_open_register_record must exist';
  RAISE NOTICE 'PASS 0051 structure';
END $$;

CREATE OR REPLACE FUNCTION pg_temp.t0051_fixture()
  RETURNS TABLE (
    h1 UUID, h2 UUID, u1 UUID, m1 UUID, u2 UUID,
    a1 UUID, a_fd UUID,
    cc1 UUID, cs1 UUID,
    r_policy UUID, r_deposit UUID, r_claim UUID, r_foreign UUID,
    doc1 UUID, doc_foreign UUID,
    base CHAR(3)
  )
  LANGUAGE plpgsql AS $$
DECLARE p1 UUID; p1m UUID; p2 UUID;
BEGIN
  SELECT min(code) INTO base FROM currency;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('register commitments household one', base, (SELECT max(code) FROM currency))
  RETURNING id INTO h1;
  INSERT INTO household (name, base_currency, secondary_currency)
  VALUES ('register commitments household two', base, (SELECT max(code) FROM currency))
  RETURNING id INTO h2;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'register commitments owner', true) RETURNING id INTO p1;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h1, 'register commitments member', false) RETURNING id INTO p1m;
  INSERT INTO person (household_id, display_name, is_self)
  VALUES (h2, 'other register commitments owner', true) RETURNING id INTO p2;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1, 'register-commitments-owner@example.invalid', 'owner')
  RETURNING id INTO u1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h1, p1m, 'register-commitments-member@example.invalid', 'member')
  RETURNING id INTO m1;
  INSERT INTO app_user (id, household_id, person_id, email, role)
  VALUES (gen_random_uuid(), h2, p2, 'other-register-commitments-owner@example.invalid', 'owner')
  RETURNING id INTO u2;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic register commitments asset', 'savings', base, 'XZ', 0, '2026-01-01')
  RETURNING id INTO a1;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date)
  VALUES (h1, p1, 'Synthetic register commitments deposit', 'fixed_deposit', base, 'XZ', 5000,
          '2026-01-01')
  RETURNING id INTO a_fd;
  INSERT INTO account (household_id, owner_person_id, name, account_type, currency, country,
                        opening_balance, opening_balance_date, credit_limit)
  VALUES (h1, p1, 'Synthetic register commitments card', 'credit_card', base, 'XZ', 0,
          '2026-01-01', 5000)
  RETURNING id INTO cc1;
  INSERT INTO card_statement (household_id, account_id, period_start, period_end, statement_date,
                               payment_due_date, currency, opening_balance, closing_balance,
                               statement_balance)
  VALUES (h1, cc1, '2026-01-01', '2026-01-31', '2026-02-01', '2026-02-20', base, 0, -400, 400)
  RETURNING id INTO cs1;

  INSERT INTO register_record (household_id, idempotency_key, kind, title, premium_amount,
                                premium_cadence, currency, key_date)
  VALUES (h1, gen_random_uuid(), 'insurance_policy', 'Synthetic policy', 100, 'monthly', base,
          '2027-01-01')
  RETURNING id INTO r_policy;
  INSERT INTO register_record (household_id, idempotency_key, kind, title, amount, currency,
                                key_date, account_id)
  VALUES (h1, gen_random_uuid(), 'deposit', 'Synthetic deposit', 1000, base, '2027-01-01', a_fd)
  RETURNING id INTO r_deposit;
  INSERT INTO register_record (household_id, idempotency_key, kind, title, counterparty, amount,
                                currency, key_date)
  VALUES (h1, gen_random_uuid(), 'claim', 'Synthetic claim', 'Synthetic insurer', 1000, base,
          '2027-01-01')
  RETURNING id INTO r_claim;
  INSERT INTO register_record (household_id, idempotency_key, kind, title, key_date)
  VALUES (h2, gen_random_uuid(), 'administration', 'Other synthetic deadline', '2027-01-01')
  RETURNING id INTO r_foreign;

  INSERT INTO document (household_id, document_type, title, original_filename, storage_bucket,
                         storage_path, mime_type, byte_size, sha256, parsed_status)
  VALUES (h1, 'insurance_policy', 'Synthetic policy schedule', 'document.pdf', 'documents',
          'synthetic/doc1.pdf', 'application/pdf', 10, encode(gen_random_bytes(32), 'hex'),
          'not_applicable')
  RETURNING id INTO doc1;
  INSERT INTO document (household_id, document_type, title, original_filename, storage_bucket,
                         storage_path, mime_type, byte_size, sha256, parsed_status)
  VALUES (h2, 'insurance_policy', 'Other synthetic document', 'document.pdf', 'documents',
          'synthetic/doc2.pdf', 'application/pdf', 10, encode(gen_random_bytes(32), 'hex'),
          'not_applicable')
  RETURNING id INTO doc_foreign;
  RETURN NEXT;
END $$;

-- One source per commitment: a card statement and a register record together are refused.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0051_fixture();
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_card_statement_id,
                             source_register_record_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic two-source commitment', -400.0000, f.base,
            'once', '2026-02-20', f.cs1, f.r_policy);
    RAISE EXCEPTION 'a commitment with two sources must be refused';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%commitment_has_one_source%', refusal;
  END;
  RAISE NOTICE 'PASS 0051 one source per commitment';
END $$;
ROLLBACK;

-- The register scope key, and one open commitment per record (archiving frees it).
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT; c1 UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0051_fixture();
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_register_record_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic foreign record source', -100.0000, f.base,
            'monthly', '2026-02-20', f.r_foreign);
    RAISE EXCEPTION 'a cross-household register record reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_commitment_register_scope%', refusal;
  END;

  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, source_register_record_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic premium plan', -100.0000, f.base, 'monthly',
          '2026-02-20', f.r_policy)
  RETURNING id INTO c1;
  BEGIN
    INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                             cadence, first_date, source_register_record_id)
    VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic second plan for the same record',
            -100.0000, f.base, 'monthly', '2026-02-20', f.r_policy);
    RAISE EXCEPTION 'a second open plan for the same record must be refused';
  EXCEPTION WHEN unique_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%uq_commitment_open_register_record%', refusal;
  END;

  UPDATE commitment SET archived_at = now() WHERE id = c1;
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, source_register_record_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic replan after archiving', -100.0000, f.base,
          'monthly', '2026-02-20', f.r_policy);
  ASSERT (SELECT count(*) FROM commitment
           WHERE household_id = f.h1 AND source_register_record_id = f.r_policy) = 2,
    'archiving the first plan must free the record for a new one';

  RAISE NOTICE 'PASS 0051 register scope key and one open commitment per record';
END $$;
ROLLBACK;

-- The replaced trigger: an income posting is refused for a claim- or deposit-sourced commitment,
-- accepted for a policy- or owner-sourced one, and every other V48 condition still composes
-- correctly (a wrong-account posting is still refused for a register-sourced commitment too).
BEGIN;
DO $$
DECLARE
  f RECORD; refusal TEXT;
  c_claim UUID; c_deposit UUID; c_policy UUID; c_owner UUID;
  income_txn UUID; income_txn2 UUID; refund_txn UUID; transfer_in_txn UUID; wrong_account_txn UUID;
BEGIN
  SELECT * INTO f FROM pg_temp.t0051_fixture();

  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date, source_register_record_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic claim settlement', 1000.0000, f.base, 'once',
          '2026-02-01', '2026-02-01', f.r_claim)
  RETURNING id INTO c_claim;
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date, source_register_record_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic deposit proceeds', 1000.0000, f.base, 'once',
          '2026-02-01', '2026-02-01', f.r_deposit)
  RETURNING id INTO c_deposit;
  -- The route never lets a policy's premium be positive; this row isolates the trigger's own new
  -- condition (source kind) from the sign check the route would otherwise also apply.
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date, source_register_record_id)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic policy credit', 1000.0000, f.base, 'monthly',
          '2026-02-01', '2026-02-01', f.r_policy)
  RETURNING id INTO c_policy;
  INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount, currency,
                           cadence, first_date, next_due_date)
  VALUES (f.h1, gen_random_uuid(), f.a1, 'Synthetic owner income commitment', 1000.0000, f.base,
          'once', '2026-02-01', '2026-02-01')
  RETURNING id INTO c_owner;

  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (f.h1, f.a1, '2026-02-01', 'SYNTHETIC INCOME', 1000.0000, f.base, '2026-02-01', 1, 1,
            1000.0000, 1000.0000, 'income', 'manual', 'rc-income')
    RETURNING id INTO income_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (f.h1, f.a1, '2026-02-01', 'SYNTHETIC INCOME TWO', 1000.0000, f.base, '2026-02-01', 1,
            1, 1000.0000, 1000.0000, 'income', 'manual', 'rc-income-2')
    RETURNING id INTO income_txn2;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (f.h1, f.a1, '2026-02-01', 'SYNTHETIC REFUND', 400.0000, f.base, '2026-02-01', 1, 1,
            400.0000, 400.0000, 'refund', 'manual', 'rc-refund')
    RETURNING id INTO refund_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (f.h1, f.a1, '2026-02-01', 'SYNTHETIC TRANSFER IN', 1000.0000, f.base, '2026-02-01', 1,
            1, 1000.0000, 1000.0000, 'transfer_in', 'manual', 'rc-transfer-in')
    RETURNING id INTO transfer_in_txn;
  INSERT INTO transaction (household_id, account_id, booked_date, description_raw, amount,
                           currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                           amount_base, amount_secondary, txn_type, source, dedupe_hash)
    VALUES (f.h1, f.a_fd, '2026-02-01', 'SYNTHETIC WRONG ACCOUNT', 1000.0000, f.base,
            '2026-02-01', 1, 1, 1000.0000, 1000.0000, 'refund', 'manual', 'rc-wrong-account')
    RETURNING id INTO wrong_account_txn;

  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, c_claim, '2026-02-01', 'payment', true, income_txn);
    RAISE EXCEPTION 'an income posting must not settle a claim-sourced commitment';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, c_deposit, '2026-02-01', 'payment', true, income_txn);
    RAISE EXCEPTION 'an income posting must not settle a deposit-sourced commitment';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;
  BEGIN
    INSERT INTO commitment_resolution (
      household_id, commitment_id, due_date, kind, closes, transaction_id)
    VALUES (f.h1, c_claim, '2026-02-01', 'payment', false, wrong_account_txn);
    RAISE EXCEPTION 'a wrong-account posting must still be refused for a register-sourced plan';
  EXCEPTION WHEN check_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%this posting cannot settle this commitment%', refusal;
  END;

  -- Accepted: a refund part-payment and a transfer-in leg settle a claim and a deposit; an income
  -- posting settles a policy or an owner commitment (source kinds the new condition never touches).
  INSERT INTO commitment_resolution (
    household_id, commitment_id, due_date, kind, closes, transaction_id)
  VALUES (f.h1, c_claim, '2026-02-01', 'payment', false, refund_txn);
  INSERT INTO commitment_resolution (
    household_id, commitment_id, due_date, kind, closes, transaction_id)
  VALUES (f.h1, c_deposit, '2026-02-01', 'payment', true, transfer_in_txn);
  INSERT INTO commitment_resolution (
    household_id, commitment_id, due_date, kind, closes, transaction_id)
  VALUES (f.h1, c_policy, '2026-02-01', 'payment', true, income_txn);
  INSERT INTO commitment_resolution (
    household_id, commitment_id, due_date, kind, closes, transaction_id)
  VALUES (f.h1, c_owner, '2026-02-01', 'payment', true, income_txn2);
  ASSERT (SELECT count(*) FROM commitment_resolution WHERE household_id = f.h1) = 4,
    'a refund, a transfer-in and two income postings must all be accepted where the source allows';

  RAISE NOTICE 'PASS 0051 income refusal for claim and deposit sources, accepted elsewhere';
END $$;
ROLLBACK;

-- register_document: the two scope keys, and owner/member/foreign/anonymous isolation.
BEGIN;
DO $$
DECLARE f RECORD; refusal TEXT;
BEGIN
  SELECT * INTO f FROM pg_temp.t0051_fixture();

  BEGIN
    INSERT INTO register_document (household_id, record_id, document_id)
    VALUES (f.h1, f.r_foreign, f.doc1);
    RAISE EXCEPTION 'a cross-household record reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_register_document_record%', refusal;
  END;
  BEGIN
    INSERT INTO register_document (household_id, record_id, document_id)
    VALUES (f.h1, f.r_policy, f.doc_foreign);
    RAISE EXCEPTION 'a cross-household document reference must be refused';
  EXCEPTION WHEN foreign_key_violation THEN
    GET STACKED DIAGNOSTICS refusal = MESSAGE_TEXT;
    ASSERT refusal LIKE '%fk_register_document_document%', refusal;
  END;

  INSERT INTO register_document (household_id, record_id, document_id) VALUES (f.h1, f.r_policy, f.doc1);
  INSERT INTO register_document (household_id, record_id, document_id)
  VALUES (f.h2, f.r_foreign, f.doc_foreign);

  PERFORM set_config('app.user_id', f.u1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM register_document) = 1, 'owner sees only its own household link';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.m1::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM register_document) = 0, 'a household member reads no link';
  RESET ROLE;

  PERFORM set_config('app.user_id', f.u2::TEXT, true);
  SET LOCAL ROLE authenticated;
  ASSERT (SELECT count(*) FROM register_document) = 1, 'the other household sees only its own link';
  ASSERT (SELECT count(*) FROM register_document WHERE household_id = f.h1) = 0,
    'the other household cannot read the first household''s link';
  RESET ROLE;

  SET LOCAL ROLE anon;
  ASSERT (SELECT count(*) FROM register_document) = 0, 'anonymous register_document reads are empty';
  RESET ROLE;

  RAISE NOTICE 'PASS 0051 register_document scope keys and RLS';
END $$;
ROLLBACK;
