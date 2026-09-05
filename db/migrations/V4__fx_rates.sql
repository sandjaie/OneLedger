-- db/migrations/V4__fx_rates.sql
-- Global, not household-scoped: exchange rates are public data. But "public" means readable,
-- not writable. RLS is defence in depth here: if the service ever connects as a role other than
-- the owner, an unscoped connection must not be able to rewrite the historical rate table and
-- silently corrupt every balance conversion.
CREATE TABLE fx_rate (
  rate_date          DATE    NOT NULL,
  base_currency      CHAR(3) NOT NULL REFERENCES currency(code),
  quote_currency     CHAR(3) NOT NULL REFERENCES currency(code),
  rate               NUMERIC(20,10) NOT NULL,     -- 1 base = <rate> quote
  source             TEXT NOT NULL
    CHECK (source IN ('ecb','derived_peg','manual','actual_transfer')),
  is_carried_forward BOOLEAN NOT NULL DEFAULT false,
  carried_from       DATE,
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (rate_date, base_currency, quote_currency),
  CONSTRAINT rate_positive     CHECK (rate > 0),
  CONSTRAINT carried_has_source CHECK (NOT is_carried_forward OR carried_from IS NOT NULL),
  CONSTRAINT no_self_pair       CHECK (base_currency <> quote_currency)
);

CREATE INDEX idx_fx_lookup ON fx_rate (base_currency, quote_currency, rate_date DESC);

-- Exact date only. Carry-forward is materialised as real rows at backfill time, not resolved
-- here, so that every transaction's fx_rate_is_carried flag reflects a stored fact
-- (Invariant I7) rather than a query-time inference.
CREATE FUNCTION fx_rate_on(p_base CHAR(3), p_quote CHAR(3), p_date DATE)
  RETURNS NUMERIC(20,10) LANGUAGE sql STABLE
AS $$
  SELECT rate FROM fx_rate
  WHERE base_currency = p_base AND quote_currency = p_quote AND rate_date = p_date
$$;

-- Read-only to every role. No INSERT/UPDATE/DELETE policy exists, so writes are denied and
-- must go through upsert_fx_rates(), which authorizes independently.
ALTER TABLE fx_rate  ENABLE ROW LEVEL SECURITY;
ALTER TABLE currency ENABLE ROW LEVEL SECURITY;
CREATE POLICY reference_read ON fx_rate  FOR SELECT TO authenticated USING (true);
CREATE POLICY reference_read ON currency FOR SELECT TO authenticated USING (true);
