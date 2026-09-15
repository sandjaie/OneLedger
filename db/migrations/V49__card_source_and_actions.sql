-- Phase 3.1 Task 2: a commitment may be planned from a card statement's due amount, and every
-- item that asks the owner to act — a commitment occurrence due soon, a card statement with an
-- unplanned amount due, and owner-entered follow-ups — is gathered by `routes/actions.py` from
-- `commitment`, `card_statement`, `action_item` and this migration's owner decisions on a derived
-- item, `action_mark`. Neither new table has a write path beyond the ordinary owner-scoped
-- routes; there is no SECURITY DEFINER function here. All names, identifiers and amounts in
-- tests and fixtures are synthetic.

-- `commitment`'s new scoped foreign key needs a household-qualified unique key on the referenced
-- side, the same shape V48 added for `commitment` itself.
ALTER TABLE card_statement
  ADD CONSTRAINT uq_card_statement_household_id UNIQUE (household_id, id);

ALTER TABLE commitment
  ADD COLUMN source_card_statement_id UUID,
  ADD CONSTRAINT fk_commitment_card_statement_scope
    FOREIGN KEY (household_id, source_card_statement_id)
    REFERENCES card_statement (household_id, id) ON DELETE RESTRICT,
  ADD CONSTRAINT commitment_card_source_is_one_payment CHECK (
    source_card_statement_id IS NULL OR (cadence = 'once' AND amount < 0));

-- At most one open (unarchived) commitment may plan a given statement's payment; archiving frees
-- the statement for a new plan, which `GET /actions` surfaces again as an unplanned card due.
CREATE UNIQUE INDEX uq_commitment_open_card_statement ON commitment (source_card_statement_id)
  WHERE source_card_statement_id IS NOT NULL AND archived_at IS NULL;

CREATE TYPE action_item_state AS ENUM ('open', 'completed', 'dismissed');

-- An owner-entered follow-up: the only action kind with its own row. A commitment item's
-- completion and dismissal are its occurrence's resolution (`commitment_resolution`, V48); a
-- card item's are planning its payment (which turns it into a commitment item) or a dismissal
-- mark below. Neither writes here.
CREATE TABLE action_item (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id    UUID NOT NULL REFERENCES household(id),
  idempotency_key UUID NOT NULL,
  title           TEXT NOT NULL,
  due_date        DATE,
  account_id      UUID,
  state           action_item_state NOT NULL DEFAULT 'open',
  dismiss_reason  TEXT,
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_action_item_idempotency UNIQUE (household_id, idempotency_key),
  CONSTRAINT fk_action_item_account_scope FOREIGN KEY (household_id, account_id)
    REFERENCES account (household_id, id) ON DELETE RESTRICT,
  CONSTRAINT action_item_title_is_bounded CHECK (
    char_length(title) BETWEEN 1 AND 120 AND title = btrim(title)),
  CONSTRAINT action_item_resolution_agrees CHECK ((state = 'open') = (resolved_at IS NULL)),
  CONSTRAINT action_item_dismissal_has_reason CHECK (
    (state = 'dismissed') = (dismiss_reason IS NOT NULL)
    AND (dismiss_reason IS NULL OR (char_length(dismiss_reason) BETWEEN 1 AND 200
                                    AND dismiss_reason = btrim(dismiss_reason))))
);

-- An owner decision about a derived item (a commitment occurrence, a card due, or a follow-up),
-- keyed by the same string `routes/actions.py` uses to identify it on read. A stale mark whose
-- item is gone (the commitment resolved, the card planned or its statement superseded, the
-- follow-up... a follow-up's own key never goes stale on its own row, but nothing here deletes a
-- mark when its item disappears) is harmless: `GET /actions` never lists an item that does not
-- independently exist, so a stale mark is simply never read.
CREATE TABLE action_mark (
  household_id   UUID NOT NULL REFERENCES household(id),
  action_key     TEXT NOT NULL,
  snoozed_until  DATE,
  dismiss_reason TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (household_id, action_key),
  CONSTRAINT action_mark_key_shape CHECK (action_key ~
    '^(follow-up\.[0-9a-f-]{36}|commitment\.[0-9a-f-]{36}\.[0-9]{4}-[0-9]{2}-[0-9]{2}|card\.[0-9a-f-]{36})$'),
  CONSTRAINT action_mark_is_one_mark CHECK (num_nonnulls(snoozed_until, dismiss_reason) = 1),
  CONSTRAINT action_mark_dismissal_is_card CHECK (dismiss_reason IS NULL OR action_key LIKE 'card.%'),
  CONSTRAINT action_mark_reason_is_bounded CHECK (dismiss_reason IS NULL OR (
    char_length(dismiss_reason) BETWEEN 1 AND 200 AND dismiss_reason = btrim(dismiss_reason)))
);

ALTER TABLE action_item ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON action_item FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

ALTER TABLE action_mark ENABLE ROW LEVEL SECURITY;
CREATE POLICY household_isolation ON action_mark FOR ALL TO authenticated
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());
