"""Official price refresh against a real database: mapping, idempotency, outage, ownership."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import prices as prices_module
from oneledger.main import app
from oneledger.routes.investment_prices import get_price_client

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}
_ISIN = "INF000A01011"
_SCHEME_CODE = "500100"
_PUBLICATION = (
    "Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;"
    "Net Asset Value;Date\n\n"
    "Open Ended Schemes(Synthetic Category)\n\n"
    f"{_SCHEME_CODE};{_ISIN};-;Synthetic Fund - Growth;150.500000;01-Sep-2026\n"
)
_PUBLICATION_NEXT_DAY = _PUBLICATION.replace("01-Sep-2026", "02-Sep-2026")


@dataclass(frozen=True)
class PricesFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    security_id: UUID
    currency: str


@pytest.fixture
def live_prices() -> Iterator[PricesFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "prices-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-prices-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currency_row = cur.fetchone()
        assert currency_row is not None
        currency, secondary_currency = (str(value).strip() for value in currency_row)
        for household, name in (
            (household_id, "prices household"),
            (other_household_id, "other prices household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, currency, secondary_currency),
            )
        for person, household, name in (
            (person_id, household_id, "prices owner"),
            (other_person_id, other_household_id, "other prices owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, "prices-owner@example.invalid"),
            (
                other_user_id,
                other_household_id,
                other_person_id,
                "other-prices-owner@example.invalid",
            ),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )
        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class, isin)
               VALUES (%s, 'mutual_fund', 'Synthetic Fund', %s, 'XZ', 'equity', %s)
               RETURNING id""",
            (household_id, currency, _ISIN),
        )
        found = cur.fetchone()
        assert found is not None
        security_id = UUID(str(found[0]))
    connection.commit()
    prices_module._last_success.clear()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield PricesFixture(connection, owner, other_owner, security_id, currency)
    finally:
        app.dependency_overrides.clear()
        prices_module._last_success.clear()
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE price_snapshot, security_price_source CASCADE")
            cur.execute(
                "DELETE FROM security WHERE household_id IN (%s, %s)",
                (household_id, other_household_id),
            )
            cur.execute("DELETE FROM app_user WHERE id IN (%s, %s)", (user_id, other_user_id))
            cur.execute("DELETE FROM person WHERE id IN (%s, %s)", (person_id, other_person_id))
            cur.execute(
                "DELETE FROM household WHERE id IN (%s, %s)",
                (household_id, other_household_id),
            )
        connection.commit()
        connection.close()


def _mock_client(text: str, *, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=text)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _refresh(
    monkeypatch: pytest.MonkeyPatch, text: str, *, status_code: int = 200
) -> httpx.Response:
    """The caller sets OFFICIAL_NAV_CURRENCY first when the test cares about it."""
    monkeypatch.setenv("OFFICIAL_NAV_URL", "https://official-nav.example.invalid/publication.txt")
    app.dependency_overrides[get_price_client] = lambda: _mock_client(text, status_code=status_code)
    try:
        response: httpx.Response = TestClient(app).post(
            "/investment-prices/refresh", headers=ORIGIN
        )
        return response
    finally:
        del app.dependency_overrides[get_price_client]


def test_refresh_maps_isin_security_and_stores_an_accepted_dated_price(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)

    response = _refresh(monkeypatch, _PUBLICATION)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "refreshed"
    assert body["status"]["mappedCount"] == 1
    assert body["status"]["unmappedCount"] == 0
    assert body["status"]["latestPriceDate"] == "2026-09-01"

    with live_prices.connection.cursor() as cur:
        cur.execute(
            """SELECT price, is_accepted FROM price_snapshot
                 WHERE security_id = %s AND price_date = '2026-09-01'""",
            (live_prices.security_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert Decimal(row[0]) == Decimal("150.500000")
        assert row[1] is True


def test_second_refresh_within_a_day_is_a_no_op(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)
    first = _refresh(monkeypatch, _PUBLICATION)
    assert first.json()["outcome"] == "refreshed"

    second = _refresh(monkeypatch, _PUBLICATION_NEXT_DAY)

    assert second.status_code == 200, second.text
    assert second.json()["outcome"] == "not_due"
    # The no-op skipped ingestion entirely — no 2 September price was ever recorded.
    with live_prices.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM price_snapshot WHERE security_id = %s",
            (live_prices.security_id,),
        )
        count = cur.fetchone()
        assert count is not None and count[0] == 1


def test_refresh_is_due_again_after_the_interval_elapses(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)
    first = _refresh(monkeypatch, _PUBLICATION)
    assert first.json()["outcome"] == "refreshed"
    prices_module._last_success[live_prices.owner.household_id] = datetime.now(UTC) - timedelta(
        hours=25
    )

    second = _refresh(monkeypatch, _PUBLICATION_NEXT_DAY)

    assert second.json()["outcome"] == "refreshed"
    with live_prices.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM price_snapshot WHERE security_id = %s",
            (live_prices.security_id,),
        )
        count = cur.fetchone()
        assert count is not None and count[0] == 2


def test_outage_keeps_the_cached_price_and_reports_source_unavailable(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)
    first = _refresh(monkeypatch, _PUBLICATION)
    assert first.json()["outcome"] == "refreshed"
    prices_module._last_success[live_prices.owner.household_id] = datetime.now(UTC) - timedelta(
        hours=25
    )

    outage = _refresh(monkeypatch, "", status_code=503)

    assert outage.status_code == 200, outage.text
    assert outage.json()["outcome"] == "source_unavailable"
    with live_prices.connection.cursor() as cur:
        cur.execute(
            """SELECT price FROM price_snapshot
                 WHERE security_id = %s AND is_accepted""",
            (live_prices.security_id,),
        )
        row = cur.fetchone()
        assert row is not None
        assert Decimal(row[0]) == Decimal("150.500000")


def test_a_200_response_with_no_parseable_rows_is_source_unavailable(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed/empty 200 body must not count as a successful check."""
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)
    header_only = _PUBLICATION.splitlines()[0] + "\n"

    response = _refresh(monkeypatch, header_only)

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "source_unavailable"
    assert prices_module._last_success.get(live_prices.owner.household_id) is None

    # The 24-hour gate was never armed, so an immediate retry is attempted again, not skipped.
    retried = _refresh(monkeypatch, _PUBLICATION)
    assert retried.json()["outcome"] == "refreshed"


def test_a_conflicting_value_for_an_existing_date_is_retained_unaccepted(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)
    first = _refresh(monkeypatch, _PUBLICATION)
    assert first.json()["outcome"] == "refreshed"
    prices_module._last_success[live_prices.owner.household_id] = datetime.now(UTC) - timedelta(
        hours=25
    )
    conflicting = _PUBLICATION.replace("150.500000", "999.990000")

    second = _refresh(monkeypatch, conflicting)

    assert second.json()["outcome"] == "refreshed"
    with live_prices.connection.cursor() as cur:
        cur.execute(
            """SELECT price, is_accepted FROM price_snapshot
                 WHERE security_id = %s AND price_date = '2026-09-01'
                 ORDER BY price""",
            (live_prices.security_id,),
        )
        rows = cur.fetchall()
        assert [(Decimal(price), accepted) for price, accepted in rows] == [
            (Decimal("150.500000"), True),
            (Decimal("999.990000"), False),
        ]


def test_currency_mismatch_leaves_the_security_unmapped(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", "ZZZ")

    response = _refresh(monkeypatch, _PUBLICATION)

    assert response.json()["outcome"] == "refreshed"
    assert response.json()["status"]["mappedCount"] == 0
    assert response.json()["status"]["unmappedCount"] == 1


def test_ambiguous_isin_across_two_codes_leaves_the_security_unmapped(
    live_prices: PricesFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OFFICIAL_NAV_CURRENCY", live_prices.currency)
    ambiguous = _PUBLICATION + f"500200;{_ISIN};-;Duplicate ISIN Fund;10.000000;01-Sep-2026\n"

    response = _refresh(monkeypatch, ambiguous)

    assert response.json()["outcome"] == "refreshed"
    assert response.json()["status"]["mappedCount"] == 0
    assert response.json()["status"]["unmappedCount"] == 1


def test_status_route_is_removed(live_prices: PricesFixture) -> None:
    # UI gaps Task 3a: the standalone status read was never used by the web app — the portfolio
    # page reads the same status through `/portfolio/summary` — so the route is gone. The shared
    # `read_status()` function stays; only this endpoint was removed.
    response = TestClient(app).get("/investment-prices/status", headers=ORIGIN)

    assert response.status_code == 404


def test_refresh_is_refused_without_an_owner_session(live_prices: PricesFixture) -> None:
    del app.dependency_overrides[auth.require_owner]
    try:
        response = TestClient(app).post("/investment-prices/refresh", headers=ORIGIN)
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live_prices.owner

    assert response.status_code == 401
