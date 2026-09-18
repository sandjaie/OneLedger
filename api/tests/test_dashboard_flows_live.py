"""`GET /dashboard`, the flow half: this month and the twelve-month trend (slice 1.4, task 4b2b).

Flows read `v_transaction_reportable` and sum the frozen `amount_base` / `amount_secondary` — never
the base table, never a rate (I3, I5). Income is the `income` rows; spending is every other
reportable row with its sign turned, so a refund lowers spending rather than counting as income
(`classification.html`, `finance-os.html` F02); net is one SUM of the lot, and the savings rate is
net over income, null when income is not positive.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from oneledger import auth
from oneledger.main import app
from oneledger.routes import dashboard

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, _clean, _connect
from .test_transfer_writes_live import Owner, a_rate, confirm
from .test_transfers_live import an_account, client

MARCH_END = date(2026, 3, 31)
FOUR_DP = Decimal("0.0001")


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Owner]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    fixture: Owner | None = None
    try:
        fixture = Owner(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            if fixture is not None:
                cur.execute("DELETE FROM app_user WHERE id = %s", (fixture.user_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def a_flow(
    live: Owner,
    account: UUID,
    amount: str,
    on: date,
    txn_type: str,
    *,
    to_base: str = "1",
    to_secondary: str = "0.25",
    household: UUID | None = None,
) -> UUID:
    """One committed row, frozen at the rates given — which is all a report may read."""
    value = Decimal(amount)
    base, secondary = Decimal(to_base), Decimal(to_secondary)
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        description_clean, amount, currency, fx_rate_date,
                                        fx_rate_to_base, fx_rate_to_secondary, amount_base,
                                        amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, %s, 'flow', 'flow', %s,
                       (SELECT currency FROM account WHERE id = %s), %s, %s, %s, %s, %s, %s,
                       'manual', %s)
               RETURNING id""",
            (
                household or live.household_id,
                account,
                on,
                value,
                account,
                on,
                base,
                secondary,
                (value * base).quantize(FOUR_DP, ROUND_HALF_UP),
                (value * secondary).quantize(FOUR_DP, ROUND_HALF_UP),
                txn_type,
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def another_households_account(live: Owner) -> tuple[UUID, UUID]:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('theirs', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        theirs = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self) VALUES (%s, 'them', true)",
            (theirs,),
        )
    live.conn.commit()
    return theirs, an_account(live, household=theirs)


def the_dashboard(as_of: date = MARCH_END) -> dict[str, Any]:
    response = client().get("/dashboard", params={"asOf": str(as_of)}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def the_month(as_of: date = MARCH_END) -> dict[str, Any]:
    month: dict[str, Any] = the_dashboard(as_of)["thisMonth"]
    return month


# ---------------------------------------------------------------------------------------
# This month.


def test_a_hand_tallied_month(live: Owner) -> None:
    """Income 10,000. Spending 1,234.50 + 30 fee + 70 tax + 100 interest − 200 refunded =
    1,234.50. Net 8,765.50, a savings rate of 87.6550%. The secondary froze the salary at 0.25
    and the rest at 0.20, each on its own day: 2,500 in, 246.90 spent, 2,253.10 kept — 90.1240%,
    which is why each currency carries its own rate."""
    own = live.account_id
    a_flow(live, own, "10000", date(2026, 3, 1), "income")
    a_flow(live, own, "-1234.50", date(2026, 3, 5), "expense", to_secondary="0.2")
    a_flow(live, own, "-30", date(2026, 3, 6), "fee", to_secondary="0.2")
    a_flow(live, own, "-70", date(2026, 3, 7), "tax", to_secondary="0.2")
    a_flow(live, own, "-100", date(2026, 3, 8), "interest", to_secondary="0.2")
    a_flow(live, own, "200", date(2026, 3, 9), "refund", to_secondary="0.2")
    # A balance-sheet movement, not consumption: only the reporting view leaves it out, which is
    # what makes this month prove the view is read and not merely named.
    a_flow(live, own, "-5000", date(2026, 3, 11), "investment_buy")
    # Either side of the month, and another household's March: none of it counts.
    a_flow(live, own, "-500", date(2026, 2, 28), "expense")
    a_flow(live, own, "-700", date(2026, 4, 1), "expense")
    theirs, their_account = another_households_account(live)
    a_flow(live, their_account, "-9999", date(2026, 3, 10), "expense", household=theirs)

    assert the_month() == {
        "month": "2026-03-01",
        "incomeBase": "10000.0000",
        "incomeSecondary": "2500.0000",
        "spendingBase": "1234.5000",
        "spendingSecondary": "246.9000",
        "netBase": "8765.5000",
        "netSecondary": "2253.1000",
        "savingsRatePctBase": "87.6550",
        "savingsRatePctSecondary": "90.1240",
    }


def test_evidenced_trend_requires_owned_completed_statement_coverage(live: Owner) -> None:
    a_flow(live, live.account_id, "100.0000", date(2026, 1, 10), "income")
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO import_batch (household_id, account_id, status, period_start,
                                          period_end, period_is_derived)
               VALUES (%s, %s, 'completed', DATE '2026-01-01', DATE '2026-01-31', false)""",
            (live.household_id, live.account_id),
        )
    live.conn.commit()
    theirs, their_account = another_households_account(live)
    a_flow(live, their_account, "9999.0000", date(2026, 1, 10), "income", household=theirs)

    body = the_dashboard()
    assert body["hasRecordedFlows"] is True
    january = next(item for item in body["flowEvidence"] if item["month"] == "2026-01-01")
    february = next(item for item in body["flowEvidence"] if item["month"] == "2026-02-01")
    assert january["status"] == "complete"
    assert january["incomeBaseCoordinate"] == 10000
    assert february["status"] == "unknown"
    assert february["incomeBaseCoordinate"] is None
    assert (
        next(item for item in body["trend"] if item["month"] == "2026-01-01")["incomeBase"]
        == "100.0000"
    )


def test_unrecorded_flow_history_is_not_presented_as_activity(live: Owner) -> None:
    assert the_dashboard()["hasRecordedFlows"] is False


def test_closed_account_flow_remains_in_history(live: Owner) -> None:
    a_flow(live, live.account_id, "100.0000", date(2026, 1, 10), "income")
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE account SET is_active = false, closed_on = DATE '2026-01-31' WHERE id = %s",
            (live.account_id,),
        )
    live.conn.commit()

    body = the_dashboard()
    assert body["hasRecordedFlows"] is True
    assert (
        next(item for item in body["trend"] if item["month"] == "2026-01-01")["incomeBase"]
        == "100.0000"
    )


def test_the_month_is_byte_identical_after_the_rate_table_moves(live: Owner) -> None:
    """I5: move every rate by a factor of ten after the rows are committed; the month does not
    move, because it reads what was frozen and never a rate."""
    abroad = an_account(live, currency=live.secondary)
    a_rate(live, date(2026, 3, 10), "4.0000000000")
    a_flow(live, abroad, "1000", date(2026, 3, 10), "income", to_base="4", to_secondary="1")
    a_flow(live, abroad, "-250", date(2026, 3, 10), "expense", to_base="4", to_secondary="1")
    before = the_dashboard()
    assert (before["thisMonth"]["incomeBase"], before["thisMonth"]["spendingBase"]) == (
        "4000.0000",
        "1000.0000",
    )

    with live.conn.cursor() as cur:
        cur.execute("UPDATE fx_rate SET rate = rate * 10 WHERE source = 'manual'")
    live.conn.commit()
    after = the_dashboard()

    assert (after["thisMonth"], after["trend"]) == (before["thisMonth"], before["trend"])


def test_a_confirmed_card_payment_moves_no_spending_figure(live: Owner) -> None:
    """§1's I4 case through the report: a −310 purchase and a +3,900 payment yield spending of
    exactly 310 — the month is what it was with no payment at all. Unlinking puts exactly the
    payment back (I3: the exclusion is live, not assumed)."""
    card = an_account(live, "credit_card")
    a_flow(live, card, "-310", date(2026, 3, 12), "expense")
    alone = the_month()
    debit = a_flow(live, live.account_id, "-3900", date(2026, 3, 20), "expense")
    credit = a_flow(live, card, "3900", date(2026, 3, 20), "income")

    created = confirm(debit, credit, proposed=False)
    assert created.status_code == 201, created.text
    linked = the_month()

    assert linked["spendingBase"] == "310.0000"
    assert linked == alone

    unlinked = client().delete(f"/transfers/{created.json()['id']}", headers=ORIGIN)
    assert unlinked.status_code < 300, unlinked.text
    assert Decimal(the_month()["spendingBase"]) == Decimal("310") + Decimal("3900")


def test_the_savings_rate_is_null_when_income_is_not_positive(live: Owner) -> None:
    """F02: not applicable at zero income — not a division by zero, and not zero — and not at
    negative income either, where a percentage would read as meaningful and is not."""
    a_flow(live, live.account_id, "-80", date(2026, 3, 3), "expense")
    month = the_month()
    assert (month["incomeBase"], month["savingsRatePctBase"]) == ("0.0000", None)

    a_flow(live, live.account_id, "-50", date(2026, 3, 4), "income")
    month = the_month()
    assert (month["incomeBase"], month["savingsRatePctBase"], month["savingsRatePctSecondary"]) == (
        "-50.0000",
        None,
        None,
    )


# ---------------------------------------------------------------------------------------
# Year to date.


def test_year_to_date_sums_from_january_through_as_of_in_both_currencies(live: Owner) -> None:
    """Same classification as the month, widened to the calendar year: the prior year's December
    and the day after as-of are both excluded."""
    own = live.account_id
    a_flow(live, own, "-999", date(2026, 12, 31), "expense")  # last year: excluded
    a_flow(live, own, "10000", date(2027, 1, 5), "income")
    a_flow(live, own, "-1234.50", date(2027, 1, 10), "expense", to_secondary="0.2")
    a_flow(live, own, "-700", date(2027, 1, 16), "expense")  # after as-of: excluded

    ytd = the_dashboard(date(2027, 1, 15))["yearToDate"]
    assert ytd["yearStart"] == "2027-01-01"
    assert ytd["asOf"] == "2027-01-15"
    assert ytd["incomeBase"] == "10000.0000"
    assert ytd["incomeSecondary"] == "2500.0000"
    assert ytd["spendingBase"] == "1234.5000"
    assert ytd["spendingSecondary"] == "246.9000"
    assert ytd["netBase"] == "8765.5000"
    assert ytd["netSecondary"] == "2253.1000"
    assert ytd["savingsRatePctBase"] == "87.6550"
    assert ytd["savingsRatePctSecondary"] == "90.1240"
    assert ytd["investedBase"] == "0.0000"
    assert ytd["investedSecondary"] == "0.0000"


# ---------------------------------------------------------------------------------------
# The trend.


def test_the_trend_is_twelve_months_in_order_including_empty_ones(live: Owner) -> None:
    """A year on from the fixture account's balance anchor, so every row below can be booked."""
    own = live.account_id
    a_flow(live, own, "-40", date(2026, 3, 31), "expense")  # thirteen months back: outside
    a_flow(live, own, "-10", date(2026, 4, 1), "expense")  # the first month's first day
    a_flow(live, own, "-20", date(2027, 3, 15), "expense")  # the as-of date itself
    a_flow(live, own, "-30", date(2027, 3, 16), "expense")  # after it: not yet

    body = the_dashboard(date(2027, 3, 15))
    trend = body["trend"]

    months = [date(2026, m, 1) for m in range(4, 13)] + [date(2027, m, 1) for m in range(1, 4)]
    assert [m["month"] for m in trend] == [str(m) for m in months]
    assert [m["spendingBase"] for m in trend] == ["10.0000"] + ["0.0000"] * 10 + ["20.0000"]
    assert trend[5] == {
        "month": "2026-09-01",
        **{
            f"{figure}{side}": "0.0000"
            for figure in ("income", "spending", "net")
            for side in ("Base", "Secondary")
        },
        "savingsRatePctBase": None,
        "savingsRatePctSecondary": None,
    }
    assert body["thisMonth"] == trend[-1]


def test_the_flows_read_the_reporting_view() -> None:
    """I3 in the query itself; `check-identifiers.py` rules 3 and 4 enforce it tree-wide."""
    assert "FROM v_transaction_reportable" in dashboard.FLOWS
