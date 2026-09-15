-- Phase 3.3 Task 1: goals and resource reservations. A reservation earmarks an amount of an
-- asset account, or a whole account, for a goal or (Task 2) the emergency reserve; it is a plan,
-- not a ledger transaction, so it never changes a balance, net worth or report. `resources.py`
-- computes each account's native value from the existing valuation rules; `goals.py` computes
-- progress in pure Decimal; `routes/goals.py` locks the household row before every write and
-- filters by it explicitly (the API connects as table owner, so RLS below is defence in depth,
-- not the boundary). All names, identifiers and amounts in tests and fixtures are synthetic.

CREATE TYPE goal_state AS ENUM ('active', 'achieved', 'abandoned');
CREATE TYPE reservation_purpose AS ENUM ('goal', 'emergency');
CREATE TYPE reservation_kind AS ENUM ('amount', 'whole_account');

CREATE TABLE goal (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id         UUID NOT NULL REFERENCES household(id),
  idempotency_key      UUID NOT NULL,
  name                 TEXT NOT NULL,
  target_amount        NUMERIC(20,4) NOT NULL,
  target_currency      CHAR(3) NOT NULL REFERENCES currency(code),
  target_date          DATE NOT NULL,
  priority             SMALLINT NOT NULL,
  planned_monthly      NUMERIC(20,4),
  state                goal_state NOT NULL DEFAULT 'active',
  closed_at            TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_goal_household_id UNIQUE (household_id, id),
  CONSTRAINT uq_goal_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT goal_name_is_bounded CHECK (char_length(name) BETWEEN 1 AND 80 AND name = btrim(name)),
  CONSTRAINT goal_target_is_positive CHECK (target_amount > 0),
  CONSTRAINT goal_priority_is_bounded CHECK (priority BETWEEN 1 AND 5),
  CONSTRAINT goal_planned_is_nonnegative CHECK (planned_monthly IS NULL OR planned_monthly >= 0),
  CONSTRAINT goal_closure_agrees CHECK ((state = 'active') = (closed_at IS NULL))
);

CREATE TABLE resource_reservation (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id           UUID NOT NULL REFERENCES household(id),
  idempotency_key        UUID NOT NULL,
  purpose                reservation_purpose NOT NULL,
  goal_id                UUID,
  account_id             UUID NOT NULL,
  kind                   reservation_kind NOT NULL,
  native_amount          NUMERIC(20,4) NOT NULL,
  currency               CHAR(3) NOT NULL REFERENCES currency(code),
  reserved_on            DATE NOT NULL,
  rate_currency          CHAR(3) NOT NULL REFERENCES currency(code),
  rate_at_reservation    NUMERIC(20,10) NOT NULL,
  released_at            TIMESTAMPTZ,
  release_reason         TEXT,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_reservation_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_reservation_goal_scope FOREIGN KEY (household_id, goal_id)
    REFERENCES goal (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_reservation_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT reservation_purpose_agrees CHECK ((purpose = 'goal') = (goal_id IS NOT NULL)),
  CONSTRAINT reservation_amount_is_positive CHECK (native_amount > 0),
  CONSTRAINT reservation_rate_is_positive CHECK (rate_at_reservation > 0),
  CONSTRAINT reservation_release_agrees CHECK (
    released_at IS NOT NULL OR release_reason IS NULL),
  CONSTRAINT reservation_reason_is_bounded CHECK (release_reason IS NULL OR (
    char_length(release_reason) BETWEEN 1 AND 200 AND release_reason = btrim(release_reason)))
);
CREATE UNIQUE INDEX uq_reservation_whole_account ON resource_reservation (account_id)
  WHERE kind = 'whole_account' AND released_at IS NULL;
CREATE INDEX idx_reservation_account_active ON resource_reservation (household_id, account_id)
  WHERE released_at IS NULL;

-- Immutable once inserted except one release: every UPDATE is refused unless the only change is
-- setting released_at (from null) and release_reason once; DELETE is always refused. The route
-- enforces every other rule (account validity, value, exclusivity, currency, rate) before insert;
-- this trigger is the backstop that a reservation, once made, cannot be quietly edited.
CREATE FUNCTION preserve_resource_reservation() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'a reservation cannot be deleted' USING ERRCODE = '23514';
  END IF;
  IF OLD.released_at IS NOT NULL THEN
    RAISE EXCEPTION 'a released reservation is immutable' USING ERRCODE = '23514';
  END IF;
  IF NEW.household_id IS DISTINCT FROM OLD.household_id
     OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.purpose IS DISTINCT FROM OLD.purpose
     OR NEW.goal_id IS DISTINCT FROM OLD.goal_id
     OR NEW.account_id IS DISTINCT FROM OLD.account_id
     OR NEW.kind IS DISTINCT FROM OLD.kind
     OR NEW.native_amount IS DISTINCT FROM OLD.native_amount
     OR NEW.currency IS DISTINCT FROM OLD.currency
     OR NEW.reserved_on IS DISTINCT FROM OLD.reserved_on
     OR NEW.rate_currency IS DISTINCT FROM OLD.rate_currency
     OR NEW.rate_at_reservation IS DISTINCT FROM OLD.rate_at_reservation
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.released_at IS NULL THEN
    RAISE EXCEPTION 'a reservation can only be released once' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_preserve_resource_reservation
  BEFORE UPDATE OR DELETE ON resource_reservation
  FOR EACH ROW EXECUTE FUNCTION preserve_resource_reservation();

REVOKE ALL ON FUNCTION preserve_resource_reservation() FROM PUBLIC;

-- A goal is edited in place (name, target, date, priority, planned monthly, currency while it
-- holds no reservation) until it closes; `routes/goals.py`'s close route is the one write that
-- may still fire once, moving state off 'active' in the same statement this trigger inspects —
-- OLD.state is still 'active' for that write, so it passes, and every later UPDATE is refused.
CREATE FUNCTION preserve_closed_goal() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.state <> 'active' THEN
    RAISE EXCEPTION 'a closed goal is immutable' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_preserve_closed_goal
  BEFORE UPDATE ON goal
  FOR EACH ROW EXECUTE FUNCTION preserve_closed_goal();

REVOKE ALL ON FUNCTION preserve_closed_goal() FROM PUBLIC;

ALTER TABLE goal ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON goal FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

ALTER TABLE resource_reservation ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON resource_reservation FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
