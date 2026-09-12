"""Real-route security, household, and atomic manual-price checks."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app
from oneledger.routes import investment_catalogue as catalogue

from .live_database import connect_to_test_database


@pytest.fixture
def live_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[psycopg.Connection, auth.OwnerSession]]:
    connection = connect_to_test_database()
    household_id = uuid4()
    person_id = uuid4()
    owner = auth.OwnerSession(
        user_id=uuid4(),
        household_id=household_id,
        session_id=uuid4(),
        email="owner@example.invalid",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, 'catalogue household', %s, %s)""",
            (household_id, base, secondary),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, 'catalogue owner', true)""",
            (person_id, household_id),
        )
    connection.commit()
    # Routes own and close their connection; each request receives a real connection to this
    # run's isolated database rather than borrowing the fixture's cleanup connection.
    monkeypatch.setattr(catalogue, "connect", connect_to_test_database)
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield connection, owner
    finally:
        app.dependency_overrides.clear()
        connection.rollback()
        with connection.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = cur.fetchone()
            assert database is not None and str(database[0]).startswith("pytest_")
            # Price observations are deliberately immutable in production. This fixture owns a
            # throwaway pytest database, so truncation is the only valid cleanup path.
            cur.execute("TRUNCATE price_snapshot")
            cur.execute(
                "DELETE FROM security_price_source WHERE household_id = %s", (household_id,)
            )
            cur.execute("DELETE FROM security WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM person WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM household WHERE id = %s", (household_id,))
        connection.commit()
        connection.close()


def _security(connection: psycopg.Connection, household_id: UUID | None, *, name: str) -> UUID:
    with connection.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency")
        result = cur.fetchone()
        assert result is not None
        currency = result[0]
        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class
               )
               VALUES (%s, 'mutual_fund', %s, %s, 'XZ', 'equity') RETURNING id""",
            (household_id, name, currency),
        )
        created = cur.fetchone()
        assert created is not None
        security_id = UUID(str(created[0]))
    connection.commit()
    return security_id


def test_catalogue_routes_require_an_owner_session() -> None:
    app.dependency_overrides.clear()
    response = TestClient(app).post("/investment-catalogue/securities/resolve", json={"name": "x"})
    assert response.status_code == 401


def test_exact_resolution_hides_other_household_rows_and_name_ambiguity_abstains(
    live_catalogue: tuple[psycopg.Connection, auth.OwnerSession],
) -> None:
    connection, owner = live_catalogue
    private = _security(connection, owner.household_id, name="Same synthetic name")
    _security(connection, None, name="Same synthetic name")
    other_household_id = uuid4()
    other_person_id = uuid4()
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, 'other catalogue household', %s, %s)""",
            (other_household_id, base, secondary),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, 'other catalogue owner', true)""",
            (other_person_id, other_household_id),
        )
    connection.commit()
    other_security = _security(connection, other_household_id, name="Other household security")
    with TestClient(app) as client:
        ambiguous = client.post(
            "/investment-catalogue/securities/resolve", json={"name": "Same synthetic name"}
        )
        hidden = client.post(
            "/investment-catalogue/securities/resolve", json={"securityId": str(other_security)}
        )

    assert ambiguous.status_code == 200
    assert ambiguous.json()["status"] == "ambiguous"
    assert {item["id"] for item in ambiguous.json()["candidates"]} == {str(private)} | {
        item["id"] for item in ambiguous.json()["candidates"] if item["id"] != str(private)
    }
    assert hidden.json() == {
        "status": "needs_confirmation",
        "reason": "no_exact_identity",
        "candidates": [],
    }
    with connection.cursor() as cur:
        cur.execute("DELETE FROM security WHERE id = %s", (other_security,))
        cur.execute("DELETE FROM person WHERE id = %s", (other_person_id,))
        cur.execute("DELETE FROM household WHERE id = %s", (other_household_id,))
    connection.commit()


def test_manual_price_is_owner_scoped_exact_and_conflict_safe(
    live_catalogue: tuple[psycopg.Connection, auth.OwnerSession],
) -> None:
    connection, owner = live_catalogue
    household_security = _security(connection, owner.household_id, name="Owner security")
    shared_security = _security(connection, None, name="Shared security")
    body = {"priceDate": "2026-09-01", "price": "12.500001"}
    with TestClient(app) as client:
        created = client.post(
            f"/investment-catalogue/securities/{household_security}/manual-prices", json=body
        )
        retried = client.post(
            f"/investment-catalogue/securities/{household_security}/manual-prices", json=body
        )
        conflict = client.post(
            f"/investment-catalogue/securities/{household_security}/manual-prices",
            json={**body, "price": "13.000000"},
        )
        shared = client.post(
            f"/investment-catalogue/securities/{shared_security}/manual-prices", json=body
        )
        invalid = client.post(
            f"/investment-catalogue/securities/{household_security}/manual-prices",
            json={"priceDate": "2026-09-01", "price": 12.5},
        )

    assert created.status_code == 201
    assert created.json()["price"] == "12.500001"
    assert retried.status_code == 200
    assert retried.json() == created.json()
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "a different manual price already exists for this date"
    assert shared.status_code == 422
    assert shared.json()["detail"] == "manual prices require a household-owned security"
    assert invalid.status_code == 422
    assert "12.5" not in str(invalid.json())
    with connection.cursor() as cur:
        cur.execute(
            """SELECT price::text FROM price_snapshot
                 WHERE household_id = %s AND security_id = %s""",
            (owner.household_id, household_security),
        )
        assert cur.fetchall() == [("12.500001",)]
