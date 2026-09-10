-- A card identity is last-four display data only. Remove hashes written by the ordinary account
-- form before that boundary was enforced, then make every writer preserve it.
UPDATE account
   SET account_number_hash = NULL
 WHERE account_type = 'credit_card' AND account_number_hash IS NOT NULL;

ALTER TABLE account ADD CONSTRAINT card_has_no_account_number_hash
  CHECK (account_type <> 'credit_card' OR account_number_hash IS NULL);
