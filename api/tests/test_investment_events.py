"""Investment entry request contracts keep every financial value exact."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app


def _body() -> dict[str, object]:
    return {
        "accountId": str(uuid4()),
        "securityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "eventKind": "purchase",
        "units": "4.00000000",
        "price": "5.000000",
        "grossAmount": "20.0000",
        "charges": "1.0000",
        "withheldTax": "0.0000",
        "netAmount": "-21.0000",
        "currency": "XAA",
        "idempotencyKey": str(uuid4()),
    }


@pytest.fixture
def owner_override() -> Iterator[None]:
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(), household_id=uuid4(), session_id=uuid4(), email="owner@example.invalid"
    )
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_investment_event_routes_require_an_owner_session() -> None:
    """Registering a financial mutation without require_owner would expose a public write."""
    app.dependency_overrides.clear()

    response = TestClient(app).post("/investment-events/acquisitions", json=_body())

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("units", 4.0),
        ("price", 5.0),
        ("grossAmount", 20.0),
        ("charges", 1.0),
        ("withheldTax", 0.0),
        ("netAmount", -21.0),
    ],
)
def test_acquisition_rejects_json_numbers(owner_override: None, field: str, value: float) -> None:
    """Accepting a JSON number would lose precision before PostgreSQL receives the value."""
    body = _body()
    body[field] = value

    response = TestClient(app).post("/investment-events/acquisitions", json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("changes", "bad_location"),
    [
        ({"eventKind": "sale"}, "eventKind"),
        ({"units": "-4.00000000"}, "units"),
        ({"price": "5.0000001"}, "price"),
        ({"grossAmount": "20.00001"}, "grossAmount"),
        ({"netAmount": "21.0000"}, "netAmount"),
        ({"netAmount": "-20.0000"}, "body"),
    ],
)
def test_acquisition_field_matrix_refuses_invalid_or_deferred_facts(
    owner_override: None, changes: dict[str, object], bad_location: str
) -> None:
    """Weakening the acquisition matrix could admit a later lifecycle kind or inconsistent flow."""
    body = {**_body(), **changes}

    response = TestClient(app).post("/investment-events/acquisitions", json=body)

    assert response.status_code == 422
    assert any(error["loc"][-1] == bad_location for error in response.json()["detail"])


def test_acquisition_validation_does_not_echo_a_hostile_extra_key(owner_override: None) -> None:
    """Request-controlled identifier-like keys must not be reflected in serialized errors."""
    unsafe_key = "ＦＯＬ１２３４５"

    response = TestClient(app).post(
        "/investment-events/acquisitions", json={**_body(), unsafe_key: "private"}
    )

    assert response.status_code == 422
    assert unsafe_key not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "field"]


def test_correction_requires_full_replacement_and_stale_state_guard(
    owner_override: None,
) -> None:
    """A partial correction could silently inherit an obsolete financial fact."""
    event_id = uuid4()
    body = {
        **_body(),
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "Synthetic exact replacement",
    }
    del body["price"]

    response = TestClient(app).post(f"/investment-events/{event_id}/corrections", json=body)

    assert response.status_code == 422
    assert ["body", "price"] in [error["loc"] for error in response.json()["detail"]]
