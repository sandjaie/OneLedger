-- db/migrations/V19__classification_rules.sql
-- Slice 1.3, task 3a1: the rule store, per `docs/superpowers/schema.md` `0009`.
--
-- A rule is the fourth step of the classification cascade and the only one the owner writes
-- directly. Two `CHECK`s carry the whole of what a rule is: it must match something, and it
-- must decide something. A rule with outcomes and no matcher would apply to every row; a rule
-- with matchers and no outcome would consume a row and leave it unclassified, which is worse
-- than not matching at all because the cascade stops at the first match.
--
-- `priority` is ascending and the defaults do the arbitration: a user rule starts at 100 and a
-- seeded one at 500, so a user rule beats a seed rule without either knowing the other exists
-- (`classification.html` §2). Nothing reads `origin` to decide precedence.
--
-- There is no shared seed row here, unlike `category`. A seeded rule belongs to the household
-- that loaded it — the seed pack is applied through the API per household (3a4) — so
-- `household_id` is `NOT NULL` and the policy is a plain equality rather than V9's
-- `household_id IS NULL OR …`.

CREATE TABLE classification_rule (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id              UUID NOT NULL REFERENCES household(id),
  name                      TEXT,
  priority                  SMALLINT NOT NULL DEFAULT 100,   -- lower evaluated first
  is_active                 BOOLEAN NOT NULL DEFAULT true,

  -- matchers: all non-null conditions must match
  match_description_pattern TEXT,
  match_pattern_kind        pattern_type DEFAULT 'contains',
  match_account_id          UUID REFERENCES account(id),
  match_currency            CHAR(3),
  match_amount_min          NUMERIC(20,4),
  match_amount_max          NUMERIC(20,4),
  match_amount_exact        NUMERIC(20,4),
  match_direction           TEXT DEFAULT 'any',   -- debit|credit|any
  match_country             CHAR(2),
  match_reference_pattern   TEXT,

  -- outcomes
  set_category_id           UUID REFERENCES category(id),
  set_merchant_id           UUID REFERENCES merchant(id),
  set_txn_type              txn_type,
  set_payment_method        payment_method,
  set_country               CHAR(2),
  set_tags                  TEXT[],
  set_is_transfer_candidate BOOLEAN NOT NULL DEFAULT false,
  set_counterparty_account_id UUID REFERENCES account(id),

  origin                    TEXT NOT NULL,   -- seed|user_created|learned_from_correction
  -- Maintained by the cascade when a rule actually decides a row (tasks 3a2 and 3a4), not by a
  -- trigger. Stated here because `statement_layout.times_used` is the same shape and nothing
  -- has ever written it: a counter no code increments reads as "never used" forever, and the
  -- screen that ranks rules by it would rank them all equal.
  times_applied             INTEGER NOT NULL DEFAULT 0,
  last_applied_at           TIMESTAMPTZ,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- Set by the route that edits the rule, as `category`, `merchant` and `statement_layout` do.
  -- This tree maintains `updated_at` by trigger on exactly one table (`account`, inside
  -- `set_account_class`), and inventing a second convention for one table is how a schema ends
  -- up with two.
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT has_matcher CHECK (num_nonnulls(
    match_description_pattern, match_account_id, match_amount_exact, match_reference_pattern) > 0),
  CONSTRAINT has_outcome CHECK (num_nonnulls(
    set_category_id, set_merchant_id, set_txn_type, set_payment_method) > 0
    OR set_is_transfer_candidate),
  -- Stated here rather than left to the service: `origin` decides nothing at match time, but a
  -- value outside this set would make the rule's own history unreadable, and the column is
  -- written by three different paths (the seed pack, the screen, and Phase 2's learning).
  CONSTRAINT origin_is_known CHECK (origin IN ('seed', 'user_created', 'learned_from_correction')),
  -- `match_direction` is read by the cascade as a three-way switch; anything else silently
  -- matches nothing, which looks like a rule that does not work rather than one that is wrong.
  CONSTRAINT direction_is_known CHECK (match_direction IN ('debit', 'credit', 'any'))
);

COMMENT ON TABLE classification_rule IS
  'Step four of the classification cascade: the rules the owner writes, plus the seed pack a '
  'household loads. Evaluated by ascending priority; the first match wins and evaluation stops.';

CREATE INDEX idx_rule_priority ON classification_rule (household_id, priority) WHERE is_active;

ALTER TABLE classification_rule ENABLE ROW LEVEL SECURITY;

CREATE POLICY classification_rule_isolation ON classification_rule FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- The two foreign keys `schema.md` `0018` reserves for the migration that creates the target.
-- V10 and V13 left `classification_rule_id` a bare `UUID` because this table did not exist;
-- both columns are entirely NULL on every existing row, so these are pure schema changes.
--
-- `RESTRICT` rather than `SET NULL`: a committed row names the rule that classified it, and
-- Invariant I6 is about a row being able to say where it came from. Nulling the id on delete
-- would leave `classified_by = 'user_rule'` beside no rule at all — a row asserting a
-- provenance it can no longer produce. Retiring a rule is `is_active = false`, which keeps
-- both the rule and every answer it gave.
ALTER TABLE transaction
  ADD CONSTRAINT fk_txn_classification_rule
    FOREIGN KEY (classification_rule_id) REFERENCES classification_rule(id) ON DELETE RESTRICT;

ALTER TABLE staged_transaction
  ADD CONSTRAINT fk_staged_classification_rule
    FOREIGN KEY (classification_rule_id) REFERENCES classification_rule(id) ON DELETE RESTRICT;
