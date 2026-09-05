# Test Suite Reference — the source

> **This is the executable form of the test strategy.** What each test must *prove*, and why the
> invariants are the tests that matter, is in
> [testing-and-data-quality.html](../operations/testing-and-data-quality.html).
>
> Tests are written task-by-task in the milestone plans, following TDD. This file is the reference
> the plans draw from, so the assertions stay consistent as they get distributed across milestones.
>
> **Since [ADR-0013](../adr/0013-python-backend-service.html) the suite is Python** — `pytest` and
> `hypothesis` in `api/tests/`, plus SQL tests in `db/tests/` for constraints, triggers, views and
> RLS. The TypeScript snippets below are retained where they test the web app's own behaviour and
> retargeted where they tested logic that has since moved into the service.

---

## The invariant suite

```python
# api/tests/test_invariants.py  and  db/tests/invariants.sql
# (shown in Python-like pseudocode for readability; implemented in vitest and pgTAP)

def test_I1_originals_immutable():
    """amount and currency cannot be modified after insert."""
    txn = create_transaction(amount=Decimal("-310.00"), currency="AED")
    with pytest.raises(DatabaseError, match="immutable"):
        db.execute("UPDATE transaction SET amount = -400 WHERE id = %s", txn.id)

def test_I2_signed_balances_sum_to_net_worth():
    """net_worth = SUM(balance), with no CASE and no subtraction."""
    savings = account(type="savings",     opening=Decimal("100000"))
    card    = account(type="credit_card", opening=Decimal("-4500"))
    loan    = account(type="home_loan",   opening=Decimal("-4000000"))
    assert net_worth() == Decimal("-3904500")

def test_I3_transfers_excluded_from_spending():
    """Linking a transfer removes it from spending; unlinking restores it."""
    src, dst = remittance_pair(Decimal("-5000"), Decimal("111000"))
    before = monthly_spending()
    transfer = confirm_transfer(src, dst)
    assert monthly_spending() == before - Decimal("5000")
    reject_transfer(transfer)
    assert monthly_spending() == before

def test_I4_card_payment_not_double_counted():
    """Card purchase counts once; the payment counts zero."""
    purchase = card_transaction(Decimal("-310"))
    payment  = card_payment(Decimal("3900"))
    assert monthly_spending() == Decimal("310")

def test_I5_historical_flows_frozen():
    """Changing the rate table does not change a past report."""
    txn = import_transaction(amount=Decimal("-1000"), currency="AED", date="2026-03-15")
    before = march_spending_base()
    update_fx_rate("2026-03-15", base="USD", quote="INR", rate=Decimal("95.00"))  # a large move
    assert march_spending_base() == before

def test_I6_every_imported_row_has_provenance():
    rows = db.query("""
        SELECT id FROM transaction
        WHERE source = 'statement_import' AND source_document_id IS NULL
    """)
    assert rows == []

def test_I7_derived_values_are_labelled():
    """Carried FX, projected schedules and residuals are all explicit."""
    txn = import_on_a_sunday()
    assert txn.fx_rate_is_carried is True
    assert txn.fx_rate_date < txn.booked_date

def test_I8_reconciliation_blocks_bad_import():
    """A dropped row fails the batch rather than importing silently."""
    statement = load_fixture("enbd_sep2026.pdf")
    corrupted = drop_row(statement, index=47)
    with pytest.raises(ImportBlocked, match="Reconciliation failed"):
        import_statement(corrupted)
    assert staged_row_count() == 0
```

---

## Static checks

```typescript
// api/tests/test_static_checks.py
import { globSync } from "glob";
import { readFileSync } from "node:fs";

it("no reporting function reads the base transaction table (I3)", () => {
  const offenders = globSync("db/functions/*.sql").filter((f) => {
    const sql = readFileSync(f, "utf8");
    return isReportingFunction(sql) && /\bFROM\s+transaction\b/i.test(sql);
  });
  expect(offenders).toEqual([]);
});

it("no query multiplies amount by a live rate (I5)", () => {
  for (const f of globSync("db/functions/*.sql")) {
    expect(readFileSync(f, "utf8")).not.toMatch(/amount\s*\*\s*\w*rate/i);
  }
});

it("no money value is ever a JS number", () => {
  // Money arrives as a string and stays one. parseFloat/Number on a money field is the
  // single most likely way precision is silently lost.
  for (const f of globSync("web/lib/**/*.ts")) {
    const src = readFileSync(f, "utf8");
    expect(src).not.toMatch(/(parseFloat|Number)\s*\(\s*\w*[Aa]mount/);
    expect(src).not.toMatch(/(parseFloat|Number)\s*\(\s*\w*[Bb]alance/);
  }
});
```

---

## Property-based tests

```typescript
# hypothesis equivalents live in api/tests/test_properties.py

it("reconciliation holds for any generated statement", () => {
  fc.assert(fc.property(statementRows(), money(), (rows, opening) => {
    const result = parse(synthesiseStatement(rows, opening));
    const computed = result.rows.reduce((s, r) => s.plus(r.amount), new Decimal(opening));
    expect(computed.equals(result.closing)).toBe(true);
  }));
});

it("the FX freeze is internally consistent", () => {
  fc.assert(fc.property(money(), fxRate(), (amount, rate) => {
    const txn = normalize({ amount, currency: "AED", rateToBase: rate });
    expect(txn.amountInr).toBe(new Decimal(amount).times(rate).toFixed(4));
  }));
});
```

---

## Sign convention, per card layout

```typescript
it.each(CARD_PARSERS)("$layout: a card purchase is stored negative", async (parser) => {
  // Card statements present a purchase as a positive debit because it increases what you owe.
  // The ledger stores it negative (Invariant I2). Getting this wrong is internally consistent
  // and therefore invisible without an explicit assertion.
  const { rows } = parser.extract(await loadFixture(parser.layout));
  expect(new Decimal(findRow(rows, "EMIRATES FLIGHT CATERING").amount).isNegative()).toBe(true);
  expect(new Decimal(findRow(rows, "PAYMENT RECEIVED").amount).isPositive()).toBe(true);
});
```

---

## Financial calculations

```typescript
it("XIRR matches a spreadsheet on a 24-month SIP", () => {
  // 24 outflows of 10,000 (2024-09-01 .. 2026-08-01), terminal inflow 268,400 on 2026-09-01.
  // 240,000 invested, 268,400 returned, average holding period ~12.5 months.
  // Verified independently: NPV at 0.111228 is 0.00; at 0.1428 it is -6,328.45.
  const flows = monthlySip({ amount: "-10000", months: 24, start: "2024-09-01" });
  flows.push({ date: "2026-09-01", amount: "268400" });          // terminal value
  expect(Math.abs(xirr(flows) - 0.111228)).toBeLessThan(0.0001);
});

it("XIRR engages the bisection fallback on irregular flows", () => {
  // Newton–Raphson alone diverges on real SIP series with interleaved redemptions.
  // A silent null there would render as a blank XIRR that looks like missing data.
  const result = xirr(irregularFlowsThatBreakNewton());
  expect(result).not.toBeNull();
  expect(result).toBeGreaterThan(-0.99);
  expect(result).toBeLessThan(10.0);
});
```
