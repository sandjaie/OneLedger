"""`GET /portfolio/holdings/{holdingId}`: events, cash links, prices and review reasons."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import cash_lifecycle
from oneledger.main import app
from oneledger.routes import dashboard, investment_catalogue, portfolio

from .live_database import connect_to_test_database
from .test_investment_events_live import EventFixture
from .test_portfolio_live import _acquire, _dispose, _price

pytest_plugins = ("tests.test_investment_events_live",)

AS_OF = date(2026, 2, 5)


@pytest.fixture
def holding_live(
    live_events: EventFixture, monkeypatch: pytest.MonkeyPatch
) -> Iterator[EventFixture]:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    monkeypatch.setattr(dashboard, "connect", connect_to_test_database)
    monkeypatch.setattr(cash_lifecycle, "connect", connect_to_test_database)
    yield live_events


def _client() -> TestClient:
    return TestClient(app)


def _detail(holding_id: str) -> dict[str, Any]:
    response = _client().get(f"/portfolio/holdings/{holding_id}")
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_holding_detail_requires_an_owner_session() -> None:
    response = _client().get(f"/portfolio/holdings/{uuid4()}")

    assert response.status_code == 401


def test_a_foreign_or_missing_holding_is_404(holding_live: EventFixture) -> None:
    acquired = _acquire(holding_live)

    app.dependency_overrides[auth.require_owner] = lambda: holding_live.other_owner
    try:
        response = _client().get(f"/portfolio/holdings/{acquired['holdingId']}")
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: holding_live.owner

    assert response.status_code == 404
    assert response.status_code == _client().get(f"/portfolio/holdings/{uuid4()}").status_code


def test_holding_detail_lists_events_prices_and_no_lot_or_evidence_fields(
    holding_live: EventFixture,
) -> None:
    acquired = _acquire(holding_live)
    _price(holding_live, date(2026, 2, 1), "6.000000")
    _dispose(holding_live, date(2026, 2, 2), "1.00000000", "6.0000")

    detail = _detail(acquired["holdingId"])

    assert detail["holdingId"] == acquired["holdingId"]
    assert detail["accountId"] == str(holding_live.account_id)
    assert detail["securityId"] == str(holding_live.security_id)
    assert detail["units"] == "3.00000000"
    assert detail["eventsTotal"] == 2
    kinds = {event["eventKind"] for event in detail["events"]}
    assert kinds == {"purchase", "sale"}
    # Newest first.
    assert detail["events"][0]["eventKind"] == "sale"
    assert all(event["correctionState"] == "active" for event in detail["events"])
    assert detail["prices"] == [{"priceDate": "2026-02-01", "price": "6.000000"}]
    # A disposal with no lot evidence opens exactly this non-blocking review.
    assert detail["openReviewReasons"] == ["incomplete_lot_allocation"]
    forbidden = {"lots", "evidence", "identifiers", "sourceEventId"}
    assert forbidden.isdisjoint(detail)
    for event in detail["events"]:
        assert forbidden.isdisjoint(event)


def test_a_fully_closed_holdings_units_render_as_an_exact_zero(
    holding_live: EventFixture,
) -> None:
    """OL-003: `str()` on an exact-zero NUMERIC(28,8) prints as "0E-8", not "0.00000000"."""
    acquired = _acquire(holding_live)
    _price(holding_live, date(2026, 2, 1), "6.000000")
    _dispose(holding_live, date(2026, 2, 2), "4.00000000", "24.0000")

    detail = _detail(acquired["holdingId"])

    assert detail["units"] == "0.00000000"
    assert "0E-8" not in str(detail)
