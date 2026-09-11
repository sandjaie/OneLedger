-- V28 copied every legacy identity into account_identifier. From this point the typed relation is
-- authoritative, so keeping writable duplicates on account would permit the two stores to drift.
DROP INDEX idx_account_number_hash;

ALTER TABLE account
  DROP CONSTRAINT account_legacy_identity_is_migratable,
  DROP CONSTRAINT card_has_no_account_number_hash,
  DROP COLUMN account_number_masked,
  DROP COLUMN account_number_hash,
  DROP COLUMN iban_masked;
