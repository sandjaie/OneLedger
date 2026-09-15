"""`GET /portfolio/holdings/{holdingId}`: events, cash links, prices and review reasons."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.db import scope_to_owner
from oneledger.investments import cash_lifecycle
from oneledger.investments.portfolio import read_holding_detail
from oneledger.main import app
from oneledger.routes import dashboard, investment_catalogue, portfolio

from .live_database import connect_to_test_database
from .test_investment_events_live import EventFixture
from .test_portfolio_live import _acquire, _dispose, _price

pytest_plugins = ("tests.test_investment_events_live",)

AS_OF = date(2026, 2, 5)
ORIGIN = {"Origin": "http://localhost:3000"}


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


def _cash_account(fixture: EventFixture, currency: str) -> UUID:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM person WHERE household_id = %s LIMIT 1", (fixture.owner.household_id,)
        )
        person = cur.fetchone()
        assert person is not None
        cur.execute(
            """INSERT INTO account (
                   household_id, owner_person_id, name, account_type, currency, country,
                   opening_balance, opening_balance_date)
               VALUES (%s, %s, 'Synthetic funding cash', 'cash', %s, 'XZ', 0, '2026-01-01')
               RETURNING id""",
            (fixture.owner.household_id, person[0], currency),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


def _cash_debit(fixture: EventFixture, account_id: UUID, currency: str, amount: str) -> UUID:
    value = Decimal(amount)
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, '2026-02-01', 'SYNTHETIC FUNDING CASH', %s, %s,
                       '2026-02-01', 1.0000000000, 2.0000000000, %s, %s, 'expense',
                       'manual', %s) RETURNING id""",
            (
                fixture.owner.household_id,
                account_id,
                value,
                currency,
                value,
                value * 2,
                uuid4().hex + uuid4().hex,
            ),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


def test_a_purchase_is_marked_funded_once_confirmed(holding_live: EventFixture) -> None:
    """`fundingComplete` restates the same remaining-amount check the funding route makes, so
    the holding page never re-derives it from `netAmount` and `cashLinks` client side."""
    acquired = _acquire(holding_live)
    event_id = acquired["id"]

    before = _detail(acquired["holdingId"])
    purchase_before = next(e for e in before["events"] if e["id"] == event_id)
    assert purchase_before["netAmount"] == "-21.0000"
    assert purchase_before["fundingComplete"] is False

    cash_account = _cash_account(holding_live, holding_live.currency)
    cash_id = _cash_debit(holding_live, cash_account, holding_live.currency, "-21.0000")
    try:
        allocation = {
            "investmentEventId": event_id,
            "cashTransactionId": str(cash_id),
            "allocatedAmount": "21.0000",
        }
        proposal = TestClient(app).post(
            "/investment-funding/proposals", json={"allocations": [allocation]}, headers=ORIGIN
        )
        assert proposal.status_code == 200, proposal.text
        confirmation = TestClient(app).post(
            "/investment-funding/confirmations",
            json={"allocations": [allocation], "expectedState": proposal.json()["expectedState"]},
            headers=ORIGIN,
        )
        assert confirmation.status_code == 201, confirmation.text

        # The confirmed cash leaf now sits in `transaction`, so the route's own paranoid
        # `connect_to_test_database` guard (any row there fails the fixture) would refuse a
        # second HTTP round trip; read the same query `read_holding_detail` runs directly
        # against the fixture's own connection instead.
        with holding_live.connection.cursor() as cur:
            after = read_holding_detail(
                cur, holding_live.owner.household_id, UUID(acquired["holdingId"])
            )
        assert after is not None
        purchase_after = next(e for e in after.events if str(e.id) == event_id)
        assert purchase_after.fundingComplete is True
    finally:
        # `connect_to_test_database` refuses to run against a `transaction` table holding any
        # row, so the one cash leaf this test appends must not outlive it — `holding_live`
        # never otherwise touches that table.
        scope_to_owner(holding_live.connection, holding_live.owner.user_id)
        with holding_live.connection.cursor() as cur:
            cur.execute("SELECT unlink_investment_cash_allocations(%s)", (cash_id,))
            cur.execute("DELETE FROM transaction WHERE id = %s", (cash_id,))
        holding_live.connection.commit()


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
