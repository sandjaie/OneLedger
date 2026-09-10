"""`GET /dashboard`, the balance-sheet half: net worth and cards due (slice 1.4, task 4b2a).

The acceptance is `testing-and-data-quality.html` §1's I2 case, through the route: a savings account
at +100,000, a card at −4,500 and a home loan at −4,000,000 sum to −3,904,500 with no CASE and no
subtraction — and to the same figure translated into the secondary currency.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app
from oneledger.routes import dashboard

from .test_ingest_route_live import ORIGIN, Fixture, _clean, _connect

# The fixture holds 1 secondary = 4 base on 1 and 2 March 2026, and nothing after.
RATED = date(2026, 3, 2)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    try:
        fixture = Fixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def an_account(
    live: Fixture,
    account_type: str,
    opening: str,
    *,
    currency: str | None = None,
    liquid: bool = True,
    counted: bool = True,
    closed: bool = False,
    household: UUID | None = None,
) -> UUID:
    """Named like the fixture's own account so `_clean` removes it. V6 sets `current_balance`
    to the opening balance when it is left unset, which is how these reach a known figure."""
    owner = household or live.household_id
    with live.conn.cursor() as cur:
        cur.execute("SELECT id FROM person WHERE household_id = %s ORDER BY is_self DESC", (owner,))
        person = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                    country, opening_balance, opening_balance_date,
                                    include_in_net_worth, is_liquid, credit_limit, closed_on,
                                    is_active)
               VALUES (%s, %s, 'route test', %s, %s, 'ZZ', %s, DATE '2026-01-01', %s, %s, %s, %s,
                       %s)
               RETURNING id""",
            (
                owner,
                person,
                account_type,
                currency or live.base,
                Decimal(opening),
                counted,
                liquid,
                Decimal("20000") if account_type == "credit_card" else None,
                date(2026, 2, 1) if closed else None,
                # Set, not inferred: a closed account is inactive whether or not a trigger says so.
                not closed,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def a_cycle(
    live: Fixture,
    card: UUID,
    period_end: date,
    owed: str,
    due_on: date,
    *,
    limit: str | None = "20000.0000",
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO card_statement (household_id, account_id, period_start, period_end,
                                           statement_date, payment_due_date, currency,
                                           opening_balance, closing_balance, statement_balance,
                                           minimum_due, credit_limit)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, 40, %s)""",
            (
                live.household_id,
                card,
                period_end.replace(day=1),
                period_end,
                period_end,
                due_on,
                live.base,
                -Decimal(owed),
                Decimal(owed),
                None if limit is None else Decimal(limit),
            ),
        )
    live.conn.commit()


def a_rate(
    live: Fixture, on: date, currency: str, rate: str, carried_from: date | None = None
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source,
                                    is_carried_forward, carried_from)
               VALUES (%s, %s, %s, %s, 'manual', %s, %s)""",
            (on, currency, live.base, Decimal(rate), carried_from is not None, carried_from),
        )
    live.conn.commit()


def third_currency(live: Fixture) -> str:
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT min(code) FROM currency WHERE code NOT IN (%s, %s)", (live.base, live.secondary)
        )
        found: str = cur.fetchone()[0]  # type: ignore[index]
        return found


def the_dashboard(as_of: date | None = None) -> dict[str, Any]:
    params = {} if as_of is None else {"asOf": str(as_of)}
    response = TestClient(app).get("/dashboard", params=params, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def i2(live: Fixture) -> None:
    """The three accounts §1 names. The fixture's own savings account holds zero."""
    an_account(live, "savings", "100000.0000")
    an_account(live, "credit_card", "-4500.0000")
    an_account(live, "home_loan", "-4000000.0000", liquid=False)


# ---------------------------------------------------------------------------------------
# I2 — net worth is one SUM of signed balances, in both reporting currencies.


def test_net_worth_is_the_signed_balances_summed_in_both_currencies(live: Fixture) -> None:
    """+100,000 − 4,500 − 4,000,000 = −3,904,500 in the base currency. The secondary is the same
    sum at 1 base = 0.25 secondary on the fixture's rate date: −976,125."""
    i2(live)

    worth = the_dashboard(RATED)["netWorth"]

    assert worth["asOf"] == str(RATED)
    assert worth["totalBase"] == "-3904500.0000"
    assert worth["totalSecondary"] == "-976125.0000"
    assert worth["missing"] == []


def test_the_subtotals_split_the_same_sum(live: Fixture) -> None:
    i2(live)

    worth = the_dashboard(RATED)["netWorth"]

    assert worth["assetsBase"] == "100000.0000"
    assert worth["liabilitiesBase"] == "-4004500.0000"
    # Liquid leaves the loan out: 100,000 − 4,500.
    assert worth["liquidBase"] == "95500.0000"
    assert worth["liquidSecondary"] == "23875.0000"


def test_a_liability_lowers_net_worth_with_no_case_in_the_query(live: Fixture) -> None:
    """ADR-0010: signed balances make the aggregate one SUM. A CASE on the account's class is
    the place a sign error hides, so the query has none — and a new liability lowers the total
    by exactly its own magnitude."""
    assert "CASE" not in dashboard.NET_WORTH.upper()
    i2(live)
    before = Decimal(the_dashboard(RATED)["netWorth"]["totalBase"])

    an_account(live, "personal_loan", "-1000.0000")

    assert Decimal(the_dashboard(RATED)["netWorth"]["totalBase"]) == before - Decimal("1000")


def test_only_active_counted_accounts_of_this_household_count(live: Fixture) -> None:
    i2(live)
    an_account(live, "savings", "7.0000", counted=False)
    an_account(live, "savings", "11.0000", closed=True)
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
    an_account(live, "savings", "999999.0000", household=theirs)

    assert the_dashboard(RATED)["netWorth"]["totalBase"] == "-3904500.0000"


# ---------------------------------------------------------------------------------------
# I7 — a rate that is not the day's own is labelled, and a missing one is never guessed.


def test_a_rate_past_the_last_fetch_is_carried_and_says_from_when(live: Fixture) -> None:
    """The table is forward-filled only as far as the last fetch. Past it, the dashboard fills
    forward the same way the table fills its weekends — never interpolating — and says so."""
    abroad = an_account(live, "current", "100.0000", currency=live.secondary)

    worth = the_dashboard(date(2026, 3, 10))["netWorth"]

    assert worth["totalBase"] == "400.0000"
    [rate] = [r for r in worth["rates"] if r["currency"] == live.secondary]
    assert rate["carried"] is True
    assert rate["rateDate"] == "2026-03-02"
    assert abroad


def test_a_weekend_row_is_labelled_with_the_date_it_is_really_from(live: Fixture) -> None:
    an_account(live, "current", "100.0000", currency=live.secondary)
    a_rate(live, date(2026, 3, 7), live.secondary, "5.0000000000", carried_from=date(2026, 3, 6))

    worth = the_dashboard(date(2026, 3, 7))["netWorth"]

    [rate] = [r for r in worth["rates"] if r["currency"] == live.secondary]
    assert (rate["carried"], rate["rateDate"]) == (True, "2026-03-06")
    assert worth["totalBase"] == "500.0000"


def test_a_currency_with_no_rate_nulls_net_worth_and_names_itself(live: Fixture) -> None:
    """Never a partial sum: a total that silently left an account out would be a smaller,
    confident, wrong number."""
    i2(live)
    other = third_currency(live)
    an_account(live, "savings", "250.0000", currency=other)

    worth = the_dashboard(RATED)["netWorth"]

    assert worth["totalBase"] is None
    assert worth["totalSecondary"] is None
    assert {(m["currency"], m["reportingCurrency"], m["asOf"]) for m in worth["missing"]} >= {
        (other, live.base, str(RATED))
    }


def test_each_reporting_currency_stands_or_falls_on_its_own(live: Fixture) -> None:
    """Before any rate is held, a household in its base currency still has a base net worth —
    no rate is needed for it — but no secondary one."""
    i2(live)

    worth = the_dashboard(date(2025, 12, 31))["netWorth"]

    assert worth["totalBase"] == "-3904500.0000"
    assert worth["totalSecondary"] is None
    assert [m["reportingCurrency"] for m in worth["missing"]] == [live.secondary]


# ---------------------------------------------------------------------------------------
# Cards due.


def test_each_card_shows_its_latest_cycle_with_days_and_utilisation(live: Fixture) -> None:
    card = an_account(live, "credit_card", "-4500.0000")
    a_cycle(live, card, date(2026, 1, 31), "1200.0000", date(2026, 2, 20))
    a_cycle(live, card, date(2026, 2, 28), "4500.0000", date(2026, 3, 20))

    [due] = the_dashboard(RATED)["cardsDue"]

    assert due["accountId"] == str(card)
    assert due["periodEnd"] == "2026-02-28"
    assert due["statementBalance"] == "4500.0000"
    assert due["minimumDue"] == "40.0000"
    assert due["paymentDueDate"] == "2026-03-20"
    assert due["daysRemaining"] == 18
    # 4,500 of 20,000, as a human-readable percentage.
    assert due["utilisationPct"] == "22.5000"


def test_utilisation_is_absent_without_a_limit(live: Fixture) -> None:
    card = an_account(live, "credit_card", "-4500.0000")
    a_cycle(live, card, date(2026, 2, 28), "4500.0000", date(2026, 3, 20), limit=None)

    [due] = the_dashboard(RATED)["cardsDue"]

    assert due["utilisationPct"] is None


def test_a_cycle_after_the_as_of_date_is_not_yet_known(live: Fixture) -> None:
    card = an_account(live, "credit_card", "-4500.0000")
    a_cycle(live, card, date(2026, 2, 28), "4500.0000", date(2026, 3, 20))
    a_cycle(live, card, date(2026, 3, 31), "900.0000", date(2026, 4, 20))

    [due] = the_dashboard(RATED)["cardsDue"]

    assert due["periodEnd"] == "2026-02-28"


# ---------------------------------------------------------------------------------------
# The wire.


def test_every_money_figure_is_a_string(live: Fixture) -> None:
    i2(live)
    card = an_account(live, "credit_card", "-4500.0000")
    a_cycle(live, card, date(2026, 2, 28), "4500.0000", date(2026, 3, 20))

    body = the_dashboard(RATED)
    worth = body["netWorth"]

    for key in ("totalBase", "totalSecondary", "assetsBase", "liabilitiesBase", "liquidBase"):
        assert isinstance(worth[key], str), key
    for due in body["cardsDue"]:
        for key in ("statementBalance", "minimumDue", "utilisationPct"):
            assert isinstance(due[key], str), key


def test_the_as_of_date_defaults_to_today(live: Fixture) -> None:
    assert the_dashboard()["netWorth"]["asOf"] == str(date.today())
