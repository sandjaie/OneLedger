"""Owner-scoped acquisition positions from immutable events and accepted dated prices."""

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
from .test_ingest_route_live import ORIGIN
from .test_investment_events_live import EventFixture, _body

pytest_plugins = ("tests.test_investment_events_live",)

AS_OF = date(2026, 2, 5)


@pytest.fixture
def position_live(
    live_events: EventFixture, monkeypatch: pytest.MonkeyPatch
) -> Iterator[EventFixture]:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    monkeypatch.setattr(dashboard, "connect", connect_to_test_database)
    yield live_events


def _client() -> TestClient:
    return TestClient(app)


def _acquire(fixture: EventFixture, **changes: object) -> dict[str, Any]:
    response = _client().post(
        "/investment-events/acquisitions", json={**_body(fixture), **changes}, headers=ORIGIN
    )
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def _price(fixture: EventFixture, on: date, value: str) -> None:
    response = _client().post(
        f"/investment-catalogue/securities/{fixture.security_id}/manual-prices",
        json={"priceDate": str(on), "price": value},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _positions(as_of: date = AS_OF) -> dict[str, Any]:
    response = _client().get("/portfolio/positions", params={"asOf": str(as_of)})
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_portfolio_positions_require_an_owner_session() -> None:
    response = _client().get("/portfolio/positions", params={"asOf": str(AS_OF)})

    assert response.status_code == 401


def test_acquisition_position_separates_frozen_contribution_from_current_value(
    position_live: EventFixture,
) -> None:
    """Revaluing historical contribution FX or serializing numerics would break this wire result."""
    acquired = _acquire(position_live)
    _price(position_live, date(2026, 2, 2), "6.000000")

    payload = _positions()

    assert payload["asOf"] == str(AS_OF)
    assert payload["baseCurrency"] == position_live.currency
    assert payload["secondaryCurrency"] == position_live.secondary_currency
    [row] = payload["positions"]
    assert row == {
        "holdingId": acquired["holdingId"],
        "accountId": str(position_live.account_id),
        "securityId": str(position_live.security_id),
        "securityName": "Synthetic acquisition security",
        "assetClass": "equity",
        "currency": position_live.currency,
        "asOf": str(AS_OF),
        "units": "4.00000000",
        "cumulativeContributionsNative": "21.0000",
        "cumulativeContributionsBase": "21.0000",
        "cumulativeContributionsSecondary": "42.0000",
        "priceStatus": "stale",
        "valueStatus": "available",
        "price": "6.000000",
        "priceDate": "2026-02-02",
        "currentValueNative": "24.0000",
        "currentValueBase": "24.0000",
        "currentValueSecondary": "48.0000",
        "gainNative": "3.0000",
        "gainBase": "3.0000",
        "gainSecondary": "6.0000",
        "missingFx": [],
        "fxRates": [
            {
                "reportingCurrency": position_live.currency,
                "rate": "1",
                "rateDate": str(AS_OF),
                "carried": False,
            },
            {
                "reportingCurrency": position_live.secondary_currency,
                "rate": "2.0000000000",
                "rateDate": "2026-02-02",
                "carried": True,
            },
        ],
    }
    forbidden = {"realisedGain", "remainingBasis", "distributions", "return", "xirr"}
    assert forbidden.isdisjoint(row)
    for key, value in row.items():
        if key in {"units", "price"} or "Value" in key or "gain" in key or "Contribution" in key:
            assert value is None or isinstance(value, str), key


def test_only_latest_non_future_accepted_price_wins(
    position_live: EventFixture,
) -> None:
    """Future accepted and later non-future unaccepted observations are retained but not valued."""
    _acquire(position_live)
    _price(position_live, date(2026, 2, 2), "6.000000")
    _price(position_live, date(2026, 2, 10), "99.000000")
    with position_live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO price_snapshot (
                   household_id, security_id, source_mapping_id, price_date, price, currency)
               SELECT mapping.household_id, mapping.security_id, mapping.id,
                      '2026-02-04', 88.000000, security.currency
                 FROM security_price_source mapping
                 JOIN security ON security.id = mapping.security_id
                WHERE mapping.household_id = %s AND mapping.security_id = %s
                  AND mapping.source_key = 'owner_manual'""",
            (position_live.owner.household_id, position_live.security_id),
        )
    position_live.connection.commit()

    payload = _positions()
    [row] = payload["positions"]

    assert (row["price"], row["priceDate"], row["currentValueNative"]) == (
        "6.000000",
        "2026-02-02",
        "24.0000",
    )
    assert "99.000000" not in str(payload)
    assert "88.000000" not in str(payload)
    dashboard_response = _client().get("/dashboard", params={"asOf": str(AS_OF)})
    assert dashboard_response.status_code == 200
    assert dashboard_response.json()["netWorth"]["totalBase"] == "24.0000"
    assert "88.000000" not in dashboard_response.text


def test_as_of_units_and_contributions_ignore_later_acquisitions(
    position_live: EventFixture,
) -> None:
    """Reading holding.current_units would pull a later acquisition into an earlier position."""
    _acquire(position_live)
    _acquire(position_live, eventDate="2026-02-02", idempotencyKey=str(uuid4()))
    _price(position_live, date(2026, 2, 1), "6.000000")

    [row] = _positions(date(2026, 2, 1))["positions"]

    assert row["units"] == "4.00000000"
    assert row["cumulativeContributionsNative"] == "21.0000"
    assert row["currentValueNative"] == "24.0000"


@pytest.mark.parametrize(
    ("with_future", "expected"), ((False, "missing"), (True, "future_price_only"))
)
def test_no_eligible_price_is_typed_without_manufacturing_zero(
    position_live: EventFixture, with_future: bool, expected: str
) -> None:
    """Missing and future-only evidence are different recovery states, neither a zero value."""
    _acquire(position_live)
    if with_future:
        _price(position_live, date(2026, 2, 10), "9.000000")

    [row] = _positions()["positions"]

    assert row["priceStatus"] == expected
    assert row["valueStatus"] == "missing_price"
    assert row["price"] is None
    assert row["priceDate"] is None
    for key in (
        "currentValueNative",
        "currentValueBase",
        "currentValueSecondary",
        "gainNative",
        "gainBase",
        "gainSecondary",
    ):
        assert row[key] is None


def test_current_fx_is_explicitly_missing_without_rewriting_frozen_contributions(
    position_live: EventFixture,
) -> None:
    """Removing the current secondary leg must retain native/base facts and frozen contribution."""
    _acquire(position_live)
    _price(position_live, date(2026, 2, 2), "6.000000")
    with position_live.connection.cursor() as cur:
        cur.execute(
            "DELETE FROM fx_rate WHERE base_currency = %s AND quote_currency = %s",
            (position_live.secondary_currency, position_live.currency),
        )
        cur.execute(
            """INSERT INTO fx_rate (
                   rate_date, base_currency, quote_currency, rate, source)
               VALUES ('2026-02-10', %s, %s, 9.0000000000, 'manual')""",
            (position_live.secondary_currency, position_live.currency),
        )
    position_live.connection.commit()

    [row] = _positions()["positions"]

    assert row["cumulativeContributionsSecondary"] == "42.0000"
    assert row["currentValueNative"] == "24.0000"
    assert row["currentValueBase"] == "24.0000"
    assert row["gainBase"] == "3.0000"
    assert row["currentValueSecondary"] is None
    assert row["gainSecondary"] is None
    assert row["missingFx"] == [position_live.secondary_currency]
    assert row["fxRates"][1] == {
        "reportingCurrency": position_live.secondary_currency,
        "rate": None,
        "rateDate": None,
        "carried": False,
    }


def test_position_uses_the_active_correction_and_hides_another_household(
    position_live: EventFixture,
) -> None:
    """Counting the original beside its replacement or dropping household scope inflates units."""
    original = _acquire(position_live)
    corrected = _client().post(
        f"/investment-events/{original['id']}/corrections",
        json={
            **_body(position_live),
            "units": "3.00000000",
            "grossAmount": "15.0000",
            "charges": "0.0000",
            "netAmount": "-15.0000",
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic exact replacement",
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert corrected.status_code == 201, corrected.text
    _price(position_live, date(2026, 2, 2), "6.000000")
    app.dependency_overrides[auth.require_owner] = lambda: position_live.other_owner
    foreign = _client().post(
        "/investment-events/acquisitions",
        json={
            **_body(position_live),
            "accountId": str(position_live.other_account_id),
            "securityId": str(position_live.other_security_id),
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert foreign.status_code == 201, foreign.text
    foreign_price = _client().post(
        f"/investment-catalogue/securities/{position_live.other_security_id}/manual-prices",
        json={"priceDate": "2026-02-02", "price": "88.000000"},
        headers=ORIGIN,
    )
    assert foreign_price.status_code == 201, foreign_price.text
    app.dependency_overrides[auth.require_owner] = lambda: position_live.owner

    [row] = _positions()["positions"]

    assert row["units"] == "3.00000000"
    assert row["cumulativeContributionsNative"] == "15.0000"
    assert row["currentValueNative"] == "18.0000"
    assert row["gainNative"] == "3.0000"
    assert str(position_live.other_account_id) not in str(_positions())
    assert str(position_live.other_security_id) not in str(_positions())
    assert "88.000000" not in str(_positions())


def test_legal_event_and_price_scales_return_typed_unavailable_value(
    position_live: EventFixture,
) -> None:
    """A large legal unit total and large legal price must not cause an uncontrolled API 500."""
    _acquire(
        position_live,
        units="99999999999999999999.99999999",
        price="0.000001",
        grossAmount="100000000000000.0000",
        charges="0.0000",
        netAmount="-100000000000000.0000",
    )
    _price(position_live, date(2026, 2, 2), "99999999999999.999999")

    portfolio_payload = _positions()
    [row] = portfolio_payload["positions"]
    dashboard_response = _client().get("/dashboard", params={"asOf": str(AS_OF)})

    assert row["valueStatus"] == "outside_supported_range"
    for key in (
        "currentValueNative",
        "currentValueBase",
        "currentValueSecondary",
        "gainNative",
        "gainBase",
        "gainSecondary",
    ):
        assert row[key] is None
    assert "InvalidOperation" not in str(portfolio_payload)
    assert dashboard_response.status_code == 200
    dashboard_worth = dashboard_response.json()["netWorth"]
    assert dashboard_worth["totalBase"] is None
    assert dashboard_worth["totalSecondary"] is None
    assert dashboard_worth["investmentCoverage"][0]["status"] == "outside_supported_range"


def _dispose(fixture: EventFixture, on: date, units: str, gross: str) -> None:
    response = _client().post(
        "/investment-events/cash-lifecycle",
        json={
            "accountId": str(fixture.account_id),
            "securityId": str(fixture.security_id),
            "eventDate": str(on),
            "eventKind": "sale",
            "units": f"-{units}",
            "price": "6.000000",
            "grossAmount": gross,
            "charges": "0.0000",
            "withheldTax": "0.0000",
            "netAmount": gross,
            "currency": fixture.currency,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def test_disposal_reduces_valued_units_and_withholds_contribution_gain(
    position_live: EventFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Counting purchase units only would value sold units and overstate net worth."""
    monkeypatch.setattr(cash_lifecycle, "connect", connect_to_test_database)
    _acquire(position_live)
    _price(position_live, date(2026, 2, 1), "6.000000")
    _dispose(position_live, date(2026, 2, 2), "1.00000000", "6.0000")

    [before] = _positions(date(2026, 2, 1))["positions"]
    [row] = _positions()["positions"]

    assert before["units"] == "4.00000000"
    assert before["gainNative"] == "3.0000"
    assert row["units"] == "3.00000000"
    assert row["currentValueNative"] == "18.0000"
    assert row["cumulativeContributionsNative"] == "21.0000"
    assert row["valueStatus"] == "available"
    assert (row["gainNative"], row["gainBase"], row["gainSecondary"]) == (None, None, None)


def test_fully_disposed_holding_is_absent_from_current_value(
    position_live: EventFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closed holding keeps history but must not add value to current positions."""
    monkeypatch.setattr(cash_lifecycle, "connect", connect_to_test_database)
    _acquire(position_live)
    _price(position_live, date(2026, 2, 1), "6.000000")
    _dispose(position_live, date(2026, 2, 2), "4.00000000", "24.0000")

    assert _positions()["positions"] == []
    assert len(_positions(date(2026, 2, 1))["positions"]) == 1
