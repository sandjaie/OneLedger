-- pg_restore clears the caller's search_path while it recreates a database. Restored rows run
-- V23/V24 CHECK constraints, whose helpers recurse or delegate by unqualified name; pin their
-- function-level path so those calls resolve during a secure restore as well as normal queries.
ALTER FUNCTION classification_payload_has_originals(JSONB)
  SET search_path = pg_catalog, public;
ALTER FUNCTION counterparty_character_is_decimal(TEXT)
  SET search_path = pg_catalog, public;
ALTER FUNCTION counterparty_text_has_private_identifier(TEXT)
  SET search_path = pg_catalog, public;
