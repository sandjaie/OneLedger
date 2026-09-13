-- Phase 1.6: exact staged split ratios and evidence-backed fact preservation.

ALTER TABLE investment_event_draft
  ADD COLUMN unit_ratio NUMERIC(20,10);

ALTER TABLE investment_event_draft
  ADD CONSTRAINT investment_event_draft_ratio_is_valid CHECK (
    unit_ratio IS NULL OR (unit_ratio > 0 AND unit_ratio <> 1)
  ),
  ADD CONSTRAINT investment_event_draft_ratio_shape_agrees CHECK (
    (event_kind = 'split' AND units IS NULL
      AND (state NOT IN ('ready', 'duplicate', 'committed') OR unit_ratio IS NOT NULL))
    OR (event_kind IS DISTINCT FROM 'split' AND unit_ratio IS NULL)
  );

CREATE OR REPLACE FUNCTION preserve_investment_evidence_target() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
DECLARE has_evidence BOOLEAN;
BEGIN
  IF TG_TABLE_NAME = 'investment_event_draft' THEN
    SELECT EXISTS (
      SELECT FROM investment_evidence
       WHERE household_id = OLD.household_id AND draft_id = OLD.id
    ) INTO has_evidence;
    IF has_evidence AND (
      TG_OP = 'DELETE' OR (
        NEW.id, NEW.household_id, NEW.group_id, NEW.source_order,
        NEW.event_date, NEW.event_kind, NEW.units, NEW.unit_ratio, NEW.price,
        NEW.gross_amount, NEW.charges, NEW.withheld_tax, NEW.net_amount,
        NEW.currency, NEW.created_at
      ) IS DISTINCT FROM (
        OLD.id, OLD.household_id, OLD.group_id, OLD.source_order,
        OLD.event_date, OLD.event_kind, OLD.units, OLD.unit_ratio, OLD.price,
        OLD.gross_amount, OLD.charges, OLD.withheld_tax, OLD.net_amount,
        OLD.currency, OLD.created_at
      )
    ) THEN
      RAISE EXCEPTION 'evidence-backed investment draft facts are immutable'
        USING ERRCODE = '55000';
    END IF;
  ELSE
    SELECT EXISTS (
      SELECT FROM investment_evidence
       WHERE household_id = OLD.household_id AND checkpoint_id = OLD.id
    ) INTO has_evidence;
    IF has_evidence AND (
      TG_OP = 'DELETE' OR (
        NEW.id, NEW.household_id, NEW.group_id, NEW.source_order, NEW.as_of_date,
        NEW.reported_units, NEW.reported_cost_native, NEW.reported_value_native,
        NEW.currency, NEW.declared_unit_scale, NEW.created_at
      ) IS DISTINCT FROM (
        OLD.id, OLD.household_id, OLD.group_id, OLD.source_order, OLD.as_of_date,
        OLD.reported_units, OLD.reported_cost_native, OLD.reported_value_native,
        OLD.currency, OLD.declared_unit_scale, OLD.created_at
      )
    ) THEN
      RAISE EXCEPTION 'evidence-backed investment checkpoint facts are immutable'
        USING ERRCODE = '55000';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
