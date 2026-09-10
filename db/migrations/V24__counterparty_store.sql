-- Household-private counterparty identities and evidenced, role-specific aliases.
-- Businesses keep their canonical identity in merchant. Counterparty rows distinguish the
-- economic role seen on a statement without creating a second business catalogue.

ALTER TABLE account
  ADD CONSTRAINT uq_account_household_id UNIQUE (household_id, id);

-- Alias/display labels may contain a masked last four, but never a full unmasked identifier.
-- PostgreSQL's regex digit class is ASCII-only under the database locale. Keep the complete
-- Unicode 17 General_Category=Decimal_Number ranges here instead. This is a closed digit rule;
-- any non-letter may then be a grouping separator, as in the ingestion redactor.
CREATE FUNCTION counterparty_character_is_decimal(value TEXT) RETURNS BOOLEAN
  LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE point INTEGER := ascii(value);
BEGIN
  RETURN
    point BETWEEN 48 AND 57 OR
    point BETWEEN 1632 AND 1641 OR point BETWEEN 1776 AND 1785 OR
    point BETWEEN 1984 AND 1993 OR point BETWEEN 2406 AND 2415 OR
    point BETWEEN 2534 AND 2543 OR point BETWEEN 2662 AND 2671 OR
    point BETWEEN 2790 AND 2799 OR point BETWEEN 2918 AND 2927 OR
    point BETWEEN 3046 AND 3055 OR point BETWEEN 3174 AND 3183 OR
    point BETWEEN 3302 AND 3311 OR point BETWEEN 3430 AND 3439 OR
    point BETWEEN 3558 AND 3567 OR point BETWEEN 3664 AND 3673 OR
    point BETWEEN 3792 AND 3801 OR point BETWEEN 3872 AND 3881 OR
    point BETWEEN 4160 AND 4169 OR point BETWEEN 4240 AND 4249 OR
    point BETWEEN 6112 AND 6121 OR point BETWEEN 6160 AND 6169 OR
    point BETWEEN 6470 AND 6479 OR point BETWEEN 6608 AND 6617 OR
    point BETWEEN 6784 AND 6793 OR point BETWEEN 6800 AND 6809 OR
    point BETWEEN 6992 AND 7001 OR point BETWEEN 7088 AND 7097 OR
    point BETWEEN 7232 AND 7241 OR point BETWEEN 7248 AND 7257 OR
    point BETWEEN 42528 AND 42537 OR point BETWEEN 43216 AND 43225 OR
    point BETWEEN 43264 AND 43273 OR point BETWEEN 43472 AND 43481 OR
    point BETWEEN 43504 AND 43513 OR point BETWEEN 43600 AND 43609 OR
    point BETWEEN 44016 AND 44025 OR point BETWEEN 65296 AND 65305 OR
    point BETWEEN 66720 AND 66729 OR point BETWEEN 68912 AND 68921 OR
    point BETWEEN 68928 AND 68937 OR point BETWEEN 69734 AND 69743 OR
    point BETWEEN 69872 AND 69881 OR point BETWEEN 69942 AND 69951 OR
    point BETWEEN 70096 AND 70105 OR point BETWEEN 70384 AND 70393 OR
    point BETWEEN 70736 AND 70745 OR point BETWEEN 70864 AND 70873 OR
    point BETWEEN 71248 AND 71257 OR point BETWEEN 71360 AND 71369 OR
    point BETWEEN 71376 AND 71395 OR point BETWEEN 71472 AND 71481 OR
    point BETWEEN 71904 AND 71913 OR point BETWEEN 72016 AND 72025 OR
    point BETWEEN 72688 AND 72697 OR point BETWEEN 72784 AND 72793 OR
    point BETWEEN 73040 AND 73049 OR point BETWEEN 73120 AND 73129 OR
    point BETWEEN 73184 AND 73193 OR point BETWEEN 73552 AND 73561 OR
    point BETWEEN 90416 AND 90425 OR point BETWEEN 92768 AND 92777 OR
    point BETWEEN 92864 AND 92873 OR point BETWEEN 93008 AND 93017 OR
    point BETWEEN 93552 AND 93561 OR point BETWEEN 118000 AND 118009 OR
    point BETWEEN 120782 AND 120831 OR point BETWEEN 123200 AND 123209 OR
    point BETWEEN 123632 AND 123641 OR point BETWEEN 124144 AND 124153 OR
    point BETWEEN 124401 AND 124410 OR point BETWEEN 125264 AND 125273 OR
    point BETWEEN 130032 AND 130041;
END $$;

CREATE FUNCTION counterparty_text_has_private_identifier(value TEXT) RETURNS BOOLEAN
  LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  character TEXT;
  digit_count INTEGER := 0;
BEGIN
  -- This alphanumeric tax identifier has only four digits, so the numeric-run rule cannot
  -- catch it. Its explicit shape is part of the same never-store boundary.
  IF value ~* '(^|[^[:alnum:]])[A-Z]{5}[0-9]{4}[A-Z]([^[:alnum:]]|$)' THEN
    RETURN true;
  END IF;
  FOR index IN 1..char_length(value) LOOP
    character := substr(value, index, 1);
    IF counterparty_character_is_decimal(character) THEN
      digit_count := digit_count + 1;
      IF digit_count >= 7 THEN RETURN true; END IF;
    ELSIF digit_count > 0 AND character !~ '^[[:alpha:]]$' THEN
      CONTINUE;
    ELSE
      digit_count := 0;
    END IF;
  END LOOP;
  RETURN false;
END $$;

CREATE TABLE counterparty (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id   UUID NOT NULL REFERENCES household(id),
  kind           TEXT NOT NULL,
  display_name   TEXT NOT NULL,
  merchant_id    UUID REFERENCES merchant(id) ON DELETE RESTRICT,
  own_account_id UUID REFERENCES account(id) ON DELETE RESTRICT,
  is_active      BOOLEAN NOT NULL DEFAULT true,
  deactivated_at TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT uq_counterparty_household_id UNIQUE (household_id, id),
  CONSTRAINT fk_counterparty_own_account_household
    FOREIGN KEY (household_id, own_account_id)
    REFERENCES account(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT counterparty_kind_is_known CHECK (
    kind IN ('merchant', 'person', 'own_account', 'institution', 'intermediary', 'unresolved')),
  CONSTRAINT counterparty_kind_reference_agrees CHECK (
    (kind IN ('merchant', 'intermediary') AND merchant_id IS NOT NULL
      AND own_account_id IS NULL)
    OR (kind = 'own_account' AND own_account_id IS NOT NULL AND merchant_id IS NULL)
    OR (kind IN ('person', 'institution', 'unresolved')
      AND merchant_id IS NULL AND own_account_id IS NULL)),
  CONSTRAINT counterparty_display_name_is_bounded CHECK (
    length(display_name) BETWEEN 1 AND 200
    AND octet_length(display_name) <= 800),
  CONSTRAINT counterparty_display_name_excludes_private_identifiers CHECK (
    NOT counterparty_text_has_private_identifier(display_name)),
  CONSTRAINT counterparty_lifecycle_agrees CHECK (
    (is_active AND deactivated_at IS NULL)
    OR (NOT is_active AND deactivated_at IS NOT NULL))
);

-- Tenancy is identity, not editable merchant metadata. Making it immutable closes the parent
-- side of every counterparty reference and removes the insert/update race entirely.
CREATE FUNCTION preserve_merchant_household() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.household_id IS DISTINCT FROM OLD.household_id THEN
    RAISE EXCEPTION 'merchant household is immutable'
      USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_merchant_household_is_immutable
  BEFORE UPDATE OF household_id ON merchant
  FOR EACH ROW EXECUTE FUNCTION preserve_merchant_household();

-- A global/system merchant or a merchant owned by this household is valid. A trigger is needed
-- because a composite key cannot express the system row's NULL household alternative.
CREATE FUNCTION assert_counterparty_merchant_household() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE merchant_household UUID;
BEGIN
  IF NEW.merchant_id IS NULL THEN RETURN NEW; END IF;
  SELECT household_id INTO merchant_household
    FROM merchant WHERE id = NEW.merchant_id FOR KEY SHARE;
  IF NOT FOUND THEN RETURN NEW; END IF; -- The simple foreign key reports a missing merchant.
  IF merchant_household IS NOT NULL AND merchant_household <> NEW.household_id THEN
    RAISE EXCEPTION 'counterparty references merchant outside household'
      USING ERRCODE = '23503', CONSTRAINT = 'fk_counterparty_merchant_household';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_counterparty_merchant_household
  BEFORE INSERT OR UPDATE OF household_id, merchant_id ON counterparty
  FOR EACH ROW EXECUTE FUNCTION assert_counterparty_merchant_household();

CREATE TABLE counterparty_alias (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  counterparty_id       UUID NOT NULL,
  knowledge_version_id UUID NOT NULL,
  pattern              TEXT NOT NULL,
  pattern_kind         pattern_type NOT NULL DEFAULT 'contains',
  role                  TEXT NOT NULL,
  priority              SMALLINT NOT NULL DEFAULT 100,
  is_active             BOOLEAN NOT NULL DEFAULT true,
  deactivated_at        TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_counterparty_alias_entity_household
    FOREIGN KEY (household_id, counterparty_id)
    REFERENCES counterparty(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_counterparty_alias_knowledge_household
    FOREIGN KEY (household_id, knowledge_version_id)
    REFERENCES classification_knowledge_version(household_id, id) ON DELETE RESTRICT,
  CONSTRAINT counterparty_alias_role_is_known CHECK (
    role IN ('purchase', 'wallet', 'processor', 'transfer', 'refund', 'own_account',
             'institution', 'unresolved', 'other')),
  CONSTRAINT counterparty_alias_priority_is_bounded CHECK (priority BETWEEN 0 AND 1000),
  CONSTRAINT counterparty_alias_pattern_is_bounded CHECK (
    length(pattern) BETWEEN 1 AND 500 AND octet_length(pattern) <= 2000),
  CONSTRAINT counterparty_alias_excludes_private_identifiers CHECK (
    NOT counterparty_text_has_private_identifier(pattern)),
  CONSTRAINT counterparty_alias_lifecycle_agrees CHECK (
    (is_active AND deactivated_at IS NULL)
    OR (NOT is_active AND deactivated_at IS NOT NULL))
);

CREATE FUNCTION assert_counterparty_alias_role_agrees() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE entity_kind TEXT;
BEGIN
  SELECT kind INTO entity_kind
    FROM counterparty
   WHERE id = NEW.counterparty_id AND household_id = NEW.household_id;
  IF entity_kind IS NULL THEN RETURN NEW; END IF; -- The composite foreign key reports this case.
  IF (NEW.role IN ('purchase', 'refund') AND entity_kind <> 'merchant')
  OR (NEW.role IN ('wallet', 'processor') AND entity_kind <> 'intermediary')
  OR (NEW.role = 'own_account' AND entity_kind <> 'own_account')
  OR (NEW.role = 'institution' AND entity_kind <> 'institution')
  OR (NEW.role = 'unresolved' AND entity_kind <> 'unresolved') THEN
    RAISE EXCEPTION 'counterparty alias role disagrees with entity kind'
      USING ERRCODE = '23514', CONSTRAINT = 'counterparty_alias_role_agrees';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_counterparty_alias_role_agrees
  BEFORE INSERT OR UPDATE OF household_id, counterparty_id, role ON counterparty_alias
  FOR EACH ROW EXECUTE FUNCTION assert_counterparty_alias_role_agrees();

CREATE INDEX idx_counterparty_active_kind
  ON counterparty (household_id, kind) WHERE is_active;
CREATE INDEX idx_counterparty_alias_resolution
  ON counterparty_alias (household_id, role, priority, pattern_kind) WHERE is_active;
CREATE INDEX idx_counterparty_alias_entity
  ON counterparty_alias (household_id, counterparty_id, created_at);

-- Identity and evidence links never change in place. Deactivation leaves both rows available to
-- explain historical classifications, and a changed interpretation is a new evidenced alias.
CREATE FUNCTION preserve_counterparty_history() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'counterparty identity is immutable; deactivate it instead'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.id             IS DISTINCT FROM OLD.id
  OR NEW.household_id   IS DISTINCT FROM OLD.household_id
  OR NEW.kind           IS DISTINCT FROM OLD.kind
  OR NEW.display_name   IS DISTINCT FROM OLD.display_name
  OR NEW.merchant_id    IS DISTINCT FROM OLD.merchant_id
  OR NEW.own_account_id IS DISTINCT FROM OLD.own_account_id
  OR NEW.created_at     IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'counterparty identity is immutable; deactivate it and create a successor'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.is_active IS NOT DISTINCT FROM OLD.is_active
     AND NEW.deactivated_at IS NOT DISTINCT FROM OLD.deactivated_at THEN
    RETURN NEW;
  END IF;
  IF OLD.is_active AND OLD.deactivated_at IS NULL
     AND NOT NEW.is_active AND NEW.deactivated_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'counterparty lifecycle only allows active to inactive'
    USING ERRCODE = '55000';
END $$;

CREATE FUNCTION preserve_counterparty_alias_history() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'counterparty alias is immutable; deactivate it instead'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.id                   IS DISTINCT FROM OLD.id
  OR NEW.household_id         IS DISTINCT FROM OLD.household_id
  OR NEW.counterparty_id      IS DISTINCT FROM OLD.counterparty_id
  OR NEW.knowledge_version_id IS DISTINCT FROM OLD.knowledge_version_id
  OR NEW.pattern              IS DISTINCT FROM OLD.pattern
  OR NEW.pattern_kind         IS DISTINCT FROM OLD.pattern_kind
  OR NEW.role                 IS DISTINCT FROM OLD.role
  OR NEW.priority             IS DISTINCT FROM OLD.priority
  OR NEW.created_at           IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'counterparty alias is immutable; deactivate it and create a successor'
      USING ERRCODE = '55000';
  END IF;
  IF NEW.is_active IS NOT DISTINCT FROM OLD.is_active
     AND NEW.deactivated_at IS NOT DISTINCT FROM OLD.deactivated_at THEN
    RETURN NEW;
  END IF;
  IF OLD.is_active AND OLD.deactivated_at IS NULL
     AND NOT NEW.is_active AND NEW.deactivated_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'counterparty alias lifecycle only allows active to inactive'
    USING ERRCODE = '55000';
END $$;

CREATE TRIGGER trg_counterparty_history_is_preserved
  BEFORE UPDATE OR DELETE ON counterparty
  FOR EACH ROW EXECUTE FUNCTION preserve_counterparty_history();
CREATE TRIGGER trg_counterparty_alias_history_is_preserved
  BEFORE UPDATE OR DELETE ON counterparty_alias
  FOR EACH ROW EXECUTE FUNCTION preserve_counterparty_alias_history();

ALTER TABLE counterparty ENABLE ROW LEVEL SECURITY;
ALTER TABLE counterparty_alias ENABLE ROW LEVEL SECURITY;

CREATE POLICY counterparty_household_isolation ON counterparty FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
CREATE POLICY counterparty_alias_household_isolation ON counterparty_alias FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
