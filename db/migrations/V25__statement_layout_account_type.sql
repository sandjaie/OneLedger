-- A layout may state the account product it describes, so an unresolved statement can
-- prefill an account without guessing from an institution or filename.
ALTER TABLE statement_layout
  ADD COLUMN account_type account_type;
