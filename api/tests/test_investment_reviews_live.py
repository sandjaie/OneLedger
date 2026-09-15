"""`GET /investment-reviews`: this household's open investment review queue."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .test_investment_funding_live import FundingFixture, _cash_lifecycle_event, _event

pytest_plugins = ("tests.test_investment_funding_live",)


def _client() -> TestClient:
    return TestClient(app)


def _reviews(**params: object) -> httpx.Response:
    return cast(httpx.Response, _client().get("/investment-reviews", params=params))


def test_investment_reviews_requires_an_owner_session() -> None:
    response = _client().get("/investment-reviews")
    assert response.status_code == 401


def test_a_disposal_with_no_lot_evidence_lists_its_holding_security_and_account(
    live_funding: FundingFixture,
) -> None:
    _event(live_funding, "10.0000", kind="purchase")
    disposal = _cash_lifecycle_event(live_funding, "sale", "10.0000", units="-1.00000000")

    response = _reviews()
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["reason"] == "incomplete_lot_allocation"
    assert row["isBlocking"] is False
    assert row["investmentTransactionId"] == str(disposal)
    assert row["importId"] is None
    assert row["securityName"] == "Synthetic funding security"
    assert row["accountName"] == "Synthetic investment account"

    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT holding_id FROM investment_transaction WHERE id = %s", (disposal,))
        holding_row = cur.fetchone()
    assert holding_row is not None
    assert row["holdingId"] == str(holding_row[0])


def test_a_resolved_allocation_removes_the_review_and_another_household_never_sees_it(
    live_funding: FundingFixture,
) -> None:
    purchase = _event(live_funding, "10.0000", kind="purchase")
    disposal = _cash_lifecycle_event(live_funding, "sale", "10.0000", units="-1.00000000")
    with live_funding.connection.cursor() as cur:
        cur.execute("SELECT id FROM acquisition_lot WHERE origin_transaction_id = %s", (purchase,))
        lot_row = cur.fetchone()
    assert lot_row is not None
    lot_id = str(lot_row[0])

    allocations = [{"acquisitionLotId": lot_id, "allocatedUnits": "1.00000000"}]
    proposed = _client().post(
        "/investment-funding/lot-allocation-proposals",
        json={"disposalEventId": str(disposal), "allocations": allocations},
    )
    assert proposed.status_code == 200, proposed.text
    confirmed = _client().post(
        "/investment-funding/lot-allocations",
        json={
            "disposalEventId": str(disposal),
            "allocations": allocations,
            "expectedState": proposed.json()["expectedState"],
            "idempotencyKey": str(uuid4()),
        },
    )
    assert confirmed.status_code == 201, confirmed.text

    assert _reviews().json() == []

    app.dependency_overrides[auth.require_owner] = lambda: live_funding.other_owner
    try:
        foreign = _reviews()
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live_funding.owner
    assert foreign.status_code == 200, foreign.text
    assert foreign.json() == []


def test_investment_reviews_are_paged_newest_first(live_funding: FundingFixture) -> None:
    _event(live_funding, "10.0000", kind="purchase")
    _event(live_funding, "10.0000", kind="purchase")
    first = _cash_lifecycle_event(live_funding, "sale", "10.0000", units="-1.00000000")
    second = _cash_lifecycle_event(live_funding, "sale", "10.0000", units="-1.00000000")

    first_page = _reviews(limit=1, offset=0)
    assert first_page.status_code == 200, first_page.text
    first_rows = first_page.json()
    assert len(first_rows) == 1
    assert first_rows[0]["investmentTransactionId"] == str(second)

    second_page = _reviews(limit=1, offset=1)
    second_rows = second_page.json()
    assert len(second_rows) == 1
    assert second_rows[0]["investmentTransactionId"] == str(first)
