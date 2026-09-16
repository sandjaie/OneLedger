"""Phase 4.1 Task 1: `GET /spending` and `GET /spending/export.csv`.

Spending is `-sum(amount_base)` over `v_transaction_reportable` where `txn_type <> 'income'`,
the same rule `GET /dashboard` states (`test_dashboard_flows_live.py`) — a refund lowers spending
rather than counting as income, and a transfer or investment leg never appears because the view
already excludes it. A category's effective bucket merges a household's `category_override` over
the seed row (`categories_sql.EFFECTIVE_CATEGORY`).

Skipped when no database is reachable. Every figure, name and description is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, _clean, _connect
from .test_transfer_writes_live import Owner
from .test_transfers_live import an_account, client

MARCH = date(2026, 3, 1)
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
            cur.execute("DELETE FROM category WHERE name LIKE 'Synthetic spending%'")
            if fixture is not None:
                cur.execute("DELETE FROM app_user WHERE id = %s", (fixture.user_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def a_category(
    live: Owner,
    name: str,
    bucket: str,
    *,
    parent_id: UUID | None = None,
    household: UUID | None = None,
) -> UUID:
    """Named `Synthetic spending …` so the fixture's own teardown finds it; cascades to any
    `category_override` when it is removed (V11's `ON DELETE CASCADE`)."""
    owner = household or live.household_id
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO category (household_id, parent_id, name, slug, kind, bucket)
               VALUES (%s, %s, %s, %s, 'expense', %s) RETURNING id""",
            (owner, parent_id, name, f"synthetic-spending-{uuid4().hex[:12]}", bucket),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def an_override(live: Owner, category_id: UUID, *, bucket: str) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO category_override (household_id, category_id, bucket)
               VALUES (%s, %s, %s)""",
            (live.household_id, category_id, bucket),
        )
    live.conn.commit()


def a_spend(
    live: Owner,
    account: UUID,
    amount: str,
    on: date,
    txn_type: str,
    *,
    category_id: UUID | None = None,
    description: str | None = None,
    to_base: str = "1",
    to_secondary: str = "0.25",
    household: UUID | None = None,
    merchant_id: UUID | None = None,
) -> UUID:
    """One committed reportable row, frozen at the rates given — a route may only read this."""
    value = Decimal(amount)
    base, secondary = Decimal(to_base), Decimal(to_secondary)
    raw = description or "synthetic spend"
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        amount, currency, fx_rate_date, fx_rate_to_base,
                                        fx_rate_to_secondary, amount_base, amount_secondary,
                                        txn_type, category_id, merchant_id, source, dedupe_hash)
               VALUES (%s, %s, %s, %s, %s,
                       (SELECT currency FROM account WHERE id = %s), %s, %s, %s, %s, %s, %s, %s,
                       %s, 'manual', %s)
               RETURNING id""",
            (
                household or live.household_id,
                account,
                on,
                raw,
                value,
                account,
                on,
                base,
                secondary,
                (value * base).quantize(FOUR_DP, ROUND_HALF_UP),
                (value * secondary).quantize(FOUR_DP, ROUND_HALF_UP),
                txn_type,
                category_id,
                merchant_id,
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def many_spends(live: Owner, account: UUID, count: int, on: date) -> None:
    """`count` distinct −1.0000/−0.2500 rows on one day, in a single statement — the row-cap
    test needs thousands of rows and this stays fast."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        amount, currency, fx_rate_date, fx_rate_to_base,
                                        fx_rate_to_secondary, amount_base, amount_secondary,
                                        txn_type, source, dedupe_hash)
               SELECT %(household)s, %(account)s, %(on)s, 'bulk ' || n, -1,
                      (SELECT currency FROM account WHERE id = %(account)s), %(on)s, 1, 0.25,
                      -1, -0.25, 'expense', 'manual', md5('bulk-' || n::text)
                 FROM generate_series(1, %(count)s) AS n""",
            {"household": live.household_id, "account": account, "on": on, "count": count},
        )
    live.conn.commit()


def the_spending(from_: date = MARCH, to: date = date(2026, 3, 31)) -> dict[str, Any]:
    response = client().get("/spending", params={"from": str(from_), "to": str(to)}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def by_category_id(body: dict[str, Any], category_id: UUID | None) -> dict[str, Any]:
    key = None if category_id is None else str(category_id)
    return next(row for row in body["byCategory"] if row["categoryId"] == key)


def by_bucket(body: dict[str, Any], bucket: str) -> dict[str, Any]:
    return next(row for row in body["byBucket"] if row["bucket"] == bucket)


# ---------------------------------------------------------------------------------------


def test_a_hand_tallied_range_by_category_and_bucket(live: Owner) -> None:
    """Groceries (need): 120 spent, 20 refunded = 100 net, over two rows. Dining (want, a child
    of Food): 45 + 15 fee = 60. Uncategorised: 30. Income, a transfer and an investment buy are
    all excluded — income by the spending rule, the other two because the reportable view never
    carries them."""
    own = live.account_id
    food = a_category(live, "Synthetic spending food", "need")
    groceries = a_category(live, "Synthetic spending groceries", "need")
    dining = a_category(live, "Synthetic spending dining", "want", parent_id=food)

    a_spend(live, own, "-120", date(2026, 3, 5), "expense", category_id=groceries)
    a_spend(live, own, "20", date(2026, 3, 8), "refund", category_id=groceries)
    a_spend(live, own, "-45", date(2026, 3, 6), "expense", category_id=dining)
    a_spend(live, own, "-15", date(2026, 3, 7), "fee", category_id=dining)
    a_spend(live, own, "-30", date(2026, 3, 9), "expense", category_id=None)
    a_spend(live, own, "9999", date(2026, 3, 10), "income", category_id=groceries)
    a_spend(live, own, "-500", date(2026, 3, 11), "transfer_out", category_id=groceries)
    a_spend(live, own, "-700", date(2026, 3, 12), "investment_buy", category_id=groceries)
    # Outside the range and another household's own row: neither counts.
    a_spend(live, own, "-1", date(2026, 4, 1), "expense", category_id=groceries)
    theirs, their_account = _another_household(live)
    a_spend(live, their_account, "-1", date(2026, 3, 5), "expense", household=theirs)

    body = the_spending()
    assert body["totalBase"] == "190.0000"
    assert body["totalSecondary"] == "47.5000"
    assert body["baseCurrency"] == live.base
    assert body["secondaryCurrency"] == live.secondary

    groceries_row = by_category_id(body, groceries)
    assert groceries_row["amountBase"] == "100.0000"
    assert groceries_row["transactionCount"] == 2
    assert groceries_row["bucket"] == "need"
    assert groceries_row["parentCategoryId"] is None

    dining_row = by_category_id(body, dining)
    assert dining_row["amountBase"] == "60.0000"
    assert dining_row["transactionCount"] == 2
    assert dining_row["bucket"] == "want"
    assert dining_row["parentCategoryId"] == str(food)
    assert dining_row["parentCategoryName"] == "Synthetic spending food"

    uncategorised = by_category_id(body, None)
    assert uncategorised["categoryName"] == "Uncategorised"
    assert uncategorised["bucket"] is None
    assert uncategorised["amountBase"] == "30.0000"

    # Ordered by amountBase descending: groceries (100), dining (60), uncategorised (30).
    assert [row["categoryId"] for row in body["byCategory"]] == [
        str(groceries),
        str(dining),
        None,
    ]

    need = by_bucket(body, "need")
    want = by_bucket(body, "want")
    assert (need["amountBase"], want["amountBase"]) == ("100.0000", "60.0000")
    assert {row["bucket"] for row in body["byBucket"]} == {"need", "want"}


def test_an_overridden_bucket_resolves_to_the_override(live: Owner) -> None:
    category = a_category(live, "Synthetic spending subscriptions", "want")
    an_override(live, category, bucket="need")
    a_spend(live, live.account_id, "-40", date(2026, 3, 10), "expense", category_id=category)

    body = the_spending()
    row = by_category_id(body, category)
    assert row["bucket"] == "need"
    assert by_bucket(body, "need")["amountBase"] == "40.0000"


def test_chart_ratio_is_the_largest_magnitude_in_its_own_list(live: Owner) -> None:
    big = a_category(live, "Synthetic spending big", "need")
    small = a_category(live, "Synthetic spending small", "want")
    a_spend(live, live.account_id, "-100", date(2026, 3, 5), "expense", category_id=big)
    a_spend(live, live.account_id, "-25", date(2026, 3, 6), "expense", category_id=small)

    body = the_spending()
    assert by_category_id(body, big)["chartRatio"] == "1.0000"
    assert by_category_id(body, small)["chartRatio"] == "0.2500"
    assert by_bucket(body, "need")["chartRatio"] == "1.0000"
    assert by_bucket(body, "want")["chartRatio"] == "0.2500"


def test_previous_period_is_the_immediately_preceding_range_of_equal_length(live: Owner) -> None:
    """1..31 March is 31 days; the preceding 31 days run 29 Jan through 28 Feb 2026 (not a leap
    year) — a row the day before that window, and one on its first day, prove the boundary."""
    category = a_category(live, "Synthetic spending utilities", "need")
    a_spend(live, live.account_id, "-10", date(2026, 3, 15), "expense", category_id=category)
    a_spend(live, live.account_id, "-70", date(2026, 1, 29), "expense", category_id=category)
    a_spend(live, live.account_id, "-999", date(2026, 1, 28), "expense", category_id=category)

    body = the_spending()
    assert body["previousTotalBase"] == "70.0000"
    assert by_category_id(body, category)["previousAmountBase"] == "70.0000"


def test_range_refusals(live: Owner) -> None:
    assert (
        client()
        .get("/spending", params={"from": "2026-03-31", "to": "2026-03-01"}, headers=ORIGIN)
        .status_code
        == 422
    )
    tomorrow = date.today() + timedelta(days=1)
    assert (
        client()
        .get("/spending", params={"from": "2026-03-01", "to": str(tomorrow)}, headers=ORIGIN)
        .status_code
        == 422
    )
    assert (
        client()
        .get("/spending", params={"from": "2020-01-01", "to": "2021-06-01"}, headers=ORIGIN)
        .status_code
        == 422
    )


def test_foreign_household_sees_no_rows(live: Owner) -> None:
    theirs, their_account = _another_household(live)
    a_spend(live, their_account, "-500", date(2026, 3, 5), "expense", household=theirs)
    body = the_spending()
    assert body["byCategory"] == []
    assert body["totalBase"] == "0.0000"


def test_routes_require_owner_session() -> None:
    app.dependency_overrides.clear()
    assert (
        client()
        .get("/spending", params={"from": "2026-03-01", "to": "2026-03-31"}, headers=ORIGIN)
        .status_code
        == 401
    )
    assert (
        client()
        .get(
            "/spending/export.csv",
            params={"from": "2026-03-01", "to": "2026-03-31"},
            headers=ORIGIN,
        )
        .status_code
        == 401
    )


# ---------------------------------------------------------------------------------------
# CSV export.


def _csv(from_: date = MARCH, to: date = date(2026, 3, 31)) -> str:
    response = client().get(
        "/spending/export.csv", params={"from": str(from_), "to": str(to)}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    expected = f'attachment; filename="spending-{from_}-{to}.csv"'
    assert response.headers["content-disposition"] == expected
    return str(response.text)


def test_the_range_is_reported_as_from_and_to_on_the_wire(live: Owner) -> None:
    """The Spending screen builds its drill-through link from `from` and `to`; the model field is
    `from_`, so the serialization alias is what keeps the link's dates real."""
    body = the_spending()
    assert body["from"] == "2026-03-01"
    assert body["to"] == "2026-03-31"


def test_csv_header_and_one_exact_row(live: Owner) -> None:
    category = a_category(live, "Synthetic spending fuel", "need")
    a_spend(
        live,
        live.account_id,
        "-55.5",
        date(2026, 3, 9),
        "expense",
        category_id=category,
        description="Synthetic fuel stop",
    )
    lines = _csv().strip("\r\n").split("\r\n")
    assert lines[0] == "booked_date,account,description,category,bucket,amount,currency,amount_base"
    assert lines[1] == (
        f"2026-03-09,route test,Synthetic fuel stop,Synthetic spending fuel,need,-55.5000,"
        f"{live.base},-55.5000"
    )


def test_csv_neutralises_a_description_that_would_run_as_a_formula(live: Owner) -> None:
    """A statement's own description is external text: a leading `=` is executed by Excel and
    Sheets when the owner opens the export, so it is written as text."""
    a_spend(
        live,
        live.account_id,
        "-12",
        date(2026, 3, 9),
        "expense",
        description="=1+1 synthetic",
    )
    body = _csv()
    assert "'=1+1 synthetic" in body
    assert ",=1+1 synthetic" not in body


def test_csv_quotes_a_description_containing_a_comma(live: Owner) -> None:
    a_spend(
        live,
        live.account_id,
        "-12",
        date(2026, 3, 9),
        "expense",
        description="Coffee, tea and biscuits",
    )
    body = _csv()
    assert '"Coffee, tea and biscuits"' in body


def test_csv_row_cap_appends_a_truncation_comment(live: Owner) -> None:
    many_spends(live, live.account_id, 10_001, date(2026, 3, 9))
    lines = _csv().strip("\r\n").split("\r\n")
    assert lines[-1] == "# truncated at 10000 rows"
    assert len(lines) == 1 + 10_000 + 1  # header + capped rows + the comment


# ---------------------------------------------------------------------------------------


def _another_household(live: Owner) -> tuple[UUID, UUID]:
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
