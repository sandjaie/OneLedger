"""`GET /portfolio/summary`: totals, gain availability and XIRR against the live database."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from oneledger.investments import cash_lifecycle
from oneledger.investments.returns import portfolio_flows, xirr
from oneledger.main import app
from oneledger.routes import dashboard, investment_catalogue, portfolio

from .live_database import connect_to_test_database
from .test_investment_events_live import EventFixture
from .test_portfolio_live import _acquire, _dispose, _price

pytest_plugins = ("tests.test_investment_events_live",)

AS_OF = date(2026, 2, 5)


@pytest.fixture
def summary_live(
    live_events: EventFixture, monkeypatch: pytest.MonkeyPatch
) -> Iterator[EventFixture]:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    monkeypatch.setattr(dashboard, "connect", connect_to_test_database)
    monkeypatch.setattr(cash_lifecycle, "connect", connect_to_test_database)
    yield live_events


def _client() -> TestClient:
    return TestClient(app)


def _summary(as_of: date = AS_OF) -> dict[str, Any]:
    response = _client().get("/portfolio/summary", params={"asOf": str(as_of)})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_portfolio_summary_requires_an_owner_session() -> None:
    response = _client().get("/portfolio/summary", params={"asOf": str(AS_OF)})

    assert response.status_code == 401


def test_summary_totals_reconcile_to_the_one_open_position(summary_live: EventFixture) -> None:
    _acquire(summary_live)
    _price(summary_live, date(2026, 2, 2), "6.000000")

    summary = _summary()

    assert summary["asOf"] == str(AS_OF)
    assert summary["valueBase"] == "24.0000"
    assert summary["valueSecondary"] == "48.0000"
    assert summary["valueComplete"] is True
    assert summary["contributionsBase"] == "21.0000"
    assert summary["contributionsSecondary"] == "42.0000"
    assert summary["gainBase"] == "3.0000"
    assert summary["gainSecondary"] == "6.0000"
    assert summary["valuedCount"] == 1
    assert summary["unvaluedCount"] == 0
    [group] = summary["allocationByAssetClass"]
    assert group == {"label": "equity", "valueBase": "24.0000", "ratio": "1.0000000000"}
    assert summary["allocationComplete"] is True


def test_summary_gain_is_null_once_any_holding_withholds_it(
    summary_live: EventFixture,
) -> None:
    """A single disposal makes the holding's gain unavailable; the portfolio total must follow."""
    _acquire(summary_live)
    _price(summary_live, date(2026, 2, 1), "6.000000")
    _dispose(summary_live, date(2026, 2, 2), "1.00000000", "6.0000")

    summary = _summary()

    assert summary["gainBase"] is None
    assert summary["gainSecondary"] is None
    # Contributions and value stay honest sums regardless of the gain-completeness rule.
    assert summary["contributionsBase"] == "21.0000"
    assert summary["valueBase"] == "18.0000"


def test_summary_xirr_equals_the_returns_engine_for_the_same_flows(
    summary_live: EventFixture,
) -> None:
    _acquire(summary_live)
    _price(summary_live, date(2026, 2, 1), "6.000000")

    summary = _summary()

    with summary_live.connection.cursor() as cur:
        flows = portfolio_flows(
            cur,
            summary_live.owner.household_id,
            summary_live.currency,
            summary_live.secondary_currency,
            AS_OF,
        )
    assert flows.flows is not None
    expected = xirr(flows.flows)
    assert summary["xirrPercent"] == (
        None if expected.rate_percent is None else str(expected.rate_percent)
    )
    assert summary["xirrReason"] == expected.reason


def test_summary_with_no_holdings_states_zero_rather_than_erroring(
    summary_live: EventFixture,
) -> None:
    summary = _summary()

    assert summary["valueBase"] == "0.0000"
    assert summary["contributionsBase"] == "0.0000"
    assert summary["valuedCount"] == 0
    assert summary["allocationByAssetClass"] == []
