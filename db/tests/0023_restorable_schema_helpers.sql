BEGIN;

-- pg_restore deliberately uses an empty caller search path. These functions execute while
-- restored rows pass their CHECK constraints, so every helper they call must still resolve.
SET LOCAL search_path = '';

DO $$
BEGIN
  IF NOT public.classification_payload_has_originals(
    '{"nested":{"amount":"synthetic"}}'::JSONB
  ) THEN
    RAISE EXCEPTION 'nested classification originals must be detected';
  END IF;
  IF public.classification_payload_has_originals(
    '{"nested":{"category_id":null}}'::JSONB
  ) THEN
    RAISE EXCEPTION 'safe nested classification state must remain allowed';
  END IF;
  IF NOT public.counterparty_character_is_decimal('٣') THEN
    RAISE EXCEPTION 'Unicode decimal characters must remain detectable';
  END IF;
  IF NOT public.counterparty_text_has_private_identifier('Reference ١٢٣٤٥٦٧') THEN
    RAISE EXCEPTION 'Unicode identifier runs must remain detectable';
  END IF;
  IF public.counterparty_text_has_private_identifier('SYNTHETIC MARKET') THEN
    RAISE EXCEPTION 'ordinary counterparty text must remain allowed';
  END IF;
END $$;

ROLLBACK;
