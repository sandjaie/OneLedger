"""Holding/portfolio flow assembly against a real database: corrected units, not originals."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import commands
from oneledger.investments.returns import holding_flows, portfolio_flows
from oneledger.main import app

from .live_database import connect_to_test_database

EVENT_DATE = "2026-03-01"
AS_OF = "2026-03-15"


@dataclass(frozen=True)
class ReturnsFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    household_id: UUID
    account_id: UUID
    security_id: UUID
    currency: str
    secondary_currency: str


@pytest.fixture
def live_returns(monkeypatch: pytest.MonkeyPatch) -> Iterator[ReturnsFixture]:
    connection = connect_to_test_database()
    household_id = uuid4()
    person_id = uuid4()
    user_id = uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "returns-owner@example.invalid")
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = (str(value).strip() for value in currencies)
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, %s, %s, %s)""",
            (household_id, "returns household", base, secondary),
        )
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 0.5000000000, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                 SET rate = EXCLUDED.rate, source = EXCLUDED.source""",
            (EVENT_DATE, secondary, base),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, %s, true)""",
            (person_id, household_id, "returns owner"),
        )
        cur.execute(
            """INSERT INTO app_user (id, household_id, person_id, email, role)
               VALUES (%s, %s, %s, %s, 'owner')""",
            (user_id, household_id, person_id, "returns-owner@example.invalid"),
        )
        cur.execute(
            """INSERT INTO account (
                   household_id, owner_person_id, name, account_type, currency, country,
                   opening_balance, opening_balance_date)
               VALUES (%s, %s, 'Synthetic returns account', 'brokerage', %s, 'XZ', 0, '2026-01-01')
               RETURNING id""",
            (household_id, person_id, base),
        )
        account_row = cur.fetchone()
        assert account_row is not None
        account_id = UUID(str(account_row[0]))
        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class)
               VALUES (%s, 'mutual_fund', 'Synthetic returns security', %s, 'XZ', 'equity')
               RETURNING id""",
            (household_id, base),
        )
        security_row = cur.fetchone()
        assert security_row is not None
        security_id = UUID(str(security_row[0]))
    connection.commit()
    monkeypatch.setattr(commands, "connect", connect_to_test_database)
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield ReturnsFixture(
            connection, owner, household_id, account_id, security_id, base, secondary
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _acquisition_body(fixture: ReturnsFixture, *, units: str, gross: str) -> dict[str, object]:
    return {
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": EVENT_DATE,
        "eventKind": "purchase",
        "units": units,
        "price": "10.000000",
        "grossAmount": gross,
        "charges": "0.0000",
        "withheldTax": "0.0000",
        "netAmount": f"-{gross}",
        "currency": fixture.currency,
        "idempotencyKey": str(uuid4()),
    }


def _accept_price(fixture: ReturnsFixture, *, price: str, price_date: str) -> None:
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO security_price_source (
                   household_id, security_id, source_key, source_version,
                   source_security_code, effective_from)
               VALUES (%s, %s, 'owner_manual', 'v1', %s, '2026-01-01')
               RETURNING id""",
            (fixture.household_id, fixture.security_id, f"synthetic:{fixture.security_id}"),
        )
        mapping_row = cur.fetchone()
        assert mapping_row is not None
        cur.execute(
            """INSERT INTO price_snapshot (
                   household_id, security_id, source_mapping_id, price_date, price, currency,
                   is_accepted, accepted_at)
               VALUES (%s, %s, %s, %s, %s, %s, true, now())""",
            (
                fixture.household_id,
                fixture.security_id,
                mapping_row[0],
                price_date,
                price,
                fixture.currency,
            ),
        )
    fixture.connection.commit()


def test_holding_and_portfolio_flows_use_corrected_units_not_original(
    live_returns: ReturnsFixture,
) -> None:
    """A correction replaces the original's contribution to current units; it does not add to it."""
    created = TestClient(app).post(
        "/investment-events/acquisitions",
        json=_acquisition_body(live_returns, units="10.00000000", gross="100.0000"),
    )
    assert created.status_code == 201, created.text
    original_id = created.json()["id"]
    holding_id = UUID(created.json()["holdingId"])

    corrected = TestClient(app).post(
        f"/investment-events/{original_id}/corrections",
        json={
            **_acquisition_body(live_returns, units="12.00000000", gross="120.0000"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic exact replacement",
        },
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["currentUnits"] == "12.00000000"

    _accept_price(live_returns, price="10.000000", price_date=EVENT_DATE)
    as_of = date.fromisoformat(AS_OF)

    with live_returns.connection.cursor() as cur:
        holding_result = holding_flows(cur, holding_id, as_of)
        portfolio_result = portfolio_flows(
            cur,
            live_returns.household_id,
            live_returns.currency,
            live_returns.secondary_currency,
            as_of,
        )

    expected_terminal = Decimal("12.00000000") * Decimal("10.000000")

    assert holding_result.reason is None
    assert holding_result.flows is not None
    # The corrected purchase's outflow appears once (-120), not the original's -100 too, and the
    # terminal value uses the corrected 12 units, not 22 (10 original + 12 correction).
    assert [flow.amount for flow in holding_result.flows] == [
        Decimal("-120.0000"),
        expected_terminal,
    ]

    assert portfolio_result.reason is None
    assert portfolio_result.flows is not None
    assert portfolio_result.flows[-1].amount == expected_terminal
