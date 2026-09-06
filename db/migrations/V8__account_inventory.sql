-- db/migrations/V8__account_inventory.sql
-- M1a. An opening balance typed from a bank app is a stated fact and must read as one until a
-- statement supersedes it (Invariant I7, F05). The note records where the figure came from; the
-- confirmation timestamp stays NULL until the owner acknowledges it. The evidence document
-- reference is deliberately absent: `document` arrives with M2/M10, and a forward migration
-- adds the reference then rather than reserving a column for a table that does not exist.
ALTER TABLE account
  ADD COLUMN opening_balance_note         TEXT,
  ADD COLUMN opening_balance_confirmed_at TIMESTAMPTZ;

-- Closing an account preserves its history and never deletes the row (F06). An inactive account
-- with no closing date is a row whose history cannot be dated, so it is refused here rather than
-- discovered later in a report.
ALTER TABLE account
  ADD CONSTRAINT closed_account_has_a_date CHECK (is_active OR closed_on IS NOT NULL);
