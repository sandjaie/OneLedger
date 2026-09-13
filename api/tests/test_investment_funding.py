from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app


def _allocation() -> dict[str, object]:
    return {
        "investmentEventId": str(uuid4()),
        "cashTransactionId": str(uuid4()),
        "allocatedAmount": "20.0000",
    }


def _lot_allocation() -> dict[str, object]:
    return {"acquisitionLotId": str(uuid4()), "allocatedUnits": "1.00000000"}


@pytest.fixture
def owner_override() -> Iterator[None]:
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(), household_id=uuid4(), session_id=uuid4(), email="owner@example.invalid"
    )
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/investment-funding/proposals", {"allocations": [_allocation()]}),
        (
            "post",
            "/investment-funding/confirmations",
            {"allocations": [_allocation()], "expectedState": "0" * 64},
        ),
        ("delete", f"/investment-funding/cash-transactions/{uuid4()}", None),
        (
            "post",
            "/investment-funding/lot-allocations",
            {"disposalEventId": str(uuid4()), "allocations": [_lot_allocation()]},
        ),
    ],
)
def test_every_funding_route_requires_an_owner_session(
    method: str, path: str, body: dict[str, object] | None
) -> None:
    app.dependency_overrides.clear()
    response = TestClient(app).request(method.upper(), path, json=body)
    assert response.status_code == 401


@pytest.mark.parametrize("value", [20, 20.0, "0", "-1.0000", "1.00001", "1e1"])
def test_allocation_amounts_are_positive_scale_four_strings(
    owner_override: None, value: object
) -> None:
    response = TestClient(app).post(
        "/investment-funding/proposals",
        json={"allocations": [{**_allocation(), "allocatedAmount": value}]},
    )
    assert response.status_code == 422


def test_a_confirmation_requires_the_exact_proposal_state(owner_override: None) -> None:
    for expected in (None, "short", "g" * 64):
        body: dict[str, object] = {"allocations": [_allocation()]}
        if expected is not None:
            body["expectedState"] = expected
        response = TestClient(app).post("/investment-funding/confirmations", json=body)
        assert response.status_code == 422


def test_duplicate_allocation_pairs_are_refused_before_the_database(owner_override: None) -> None:
    allocation = _allocation()
    response = TestClient(app).post(
        "/investment-funding/proposals", json={"allocations": [allocation, allocation]}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]


def test_request_controlled_fields_never_echo_from_validation(owner_override: None) -> None:
    unsafe_key = "ＦＯＬ１２３４５"
    response = TestClient(app).post(
        "/investment-funding/proposals",
        json={"allocations": [{**_allocation(), unsafe_key: "private"}]},
    )
    assert response.status_code == 422
    assert unsafe_key not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "allocations", 0, "field"]


def test_callers_cannot_supply_a_different_cash_role(owner_override: None) -> None:
    response = TestClient(app).post(
        "/investment-funding/proposals",
        json={"allocations": [{**_allocation(), "role": "proceeds"}]},
    )
    assert response.status_code == 422
    assert "proceeds" not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "allocations", 0, "field"]


@pytest.mark.parametrize("value", [1, 1.0, "0", "-1.00000000", "1.000000001", "1e0"])
def test_lot_units_are_positive_scale_eight_strings(owner_override: None, value: object) -> None:
    response = TestClient(app).post(
        "/investment-funding/lot-allocations",
        json={
            "disposalEventId": str(uuid4()),
            "allocations": [{**_lot_allocation(), "allocatedUnits": value}],
        },
    )

    assert response.status_code == 422


def test_duplicate_lot_allocations_are_refused_before_the_database(owner_override: None) -> None:
    allocation = _lot_allocation()
    response = TestClient(app).post(
        "/investment-funding/lot-allocations",
        json={"disposalEventId": str(uuid4()), "allocations": [allocation, allocation]},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]
