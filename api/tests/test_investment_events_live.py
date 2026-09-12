"""Live owner-scoped acquisition and append-only correction invariants."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.investments import commands
from oneledger.main import app

from .live_database import connect_to_test_database


@dataclass(frozen=True)
class EventFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    account_id: UUID
    security_id: UUID
    other_account_id: UUID
    other_security_id: UUID
    currency: str
    secondary_currency: str


@pytest.fixture
def live_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[EventFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(
        user_id=user_id,
        household_id=household_id,
        session_id=uuid4(),
        email="event-owner@example.invalid",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        for household, name in (
            (household_id, "event household"),
            (other_household_id, "other event household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for rate_date in ("2026-02-01", "2026-02-02"):
            cur.execute(
                """INSERT INTO fx_rate (
                       rate_date, base_currency, quote_currency, rate, source)
                   VALUES (%s, %s, %s, 0.5000000000, 'manual')
                   ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                     SET rate = EXCLUDED.rate, source = EXCLUDED.source""",
                (rate_date, secondary, base),
            )
        for person, household, name in (
            (person_id, household_id, "event owner"),
            (other_person_id, other_household_id, "other event owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, "event-owner@example.invalid"),
            (
                other_user_id,
                other_household_id,
                other_person_id,
                "other-event-owner@example.invalid",
            ),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )
        account_ids: list[UUID] = []
        for household, person, name in (
            (household_id, person_id, "Synthetic investment account"),
            (other_household_id, other_person_id, "Other synthetic investment account"),
        ):
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
                   VALUES (%s, %s, %s, 'brokerage', %s, 'XZ', 0, '2026-01-01')
                   RETURNING id""",
                (household, person, name, base),
            )
            row = cur.fetchone()
            assert row is not None
            account_ids.append(UUID(str(row[0])))
        security_ids: list[UUID] = []
        for household, name in (
            (household_id, "Synthetic acquisition security"),
            (other_household_id, "Other synthetic acquisition security"),
        ):
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', %s, %s, 'XZ', 'equity') RETURNING id""",
                (household, name, base),
            )
            row = cur.fetchone()
            assert row is not None
            security_ids.append(UUID(str(row[0])))
    connection.commit()
    monkeypatch.setattr(commands, "connect", connect_to_test_database)
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield EventFixture(
            connection=connection,
            owner=owner,
            account_id=account_ids[0],
            security_id=security_ids[0],
            other_account_id=account_ids[1],
            other_security_id=security_ids[1],
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _body(fixture: EventFixture, *, key: UUID | None = None) -> dict[str, object]:
    return {
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": "2026-02-01",
        "eventKind": "purchase",
        "units": "4.00000000",
        "price": "5.000000",
        "grossAmount": "20.0000",
        "charges": "1.0000",
        "withheldTax": "0.0000",
        "netAmount": "-21.0000",
        "currency": fixture.currency,
        "idempotencyKey": str(key or uuid4()),
    }


def test_acquisition_creates_one_event_effect_lot_and_identical_retry(
    live_events: EventFixture,
) -> None:
    """Splitting the command or weakening dedupe could duplicate or partially project a purchase."""
    body = _body(live_events)
    created = TestClient(app).post("/investment-events/acquisitions", json=body)
    with live_events.connection.cursor() as cur:
        cur.execute(
            """UPDATE fx_rate SET rate = 0.2500000000
                 WHERE rate_date = '2026-02-01'
                   AND base_currency = %s AND quote_currency = %s""",
            (live_events.secondary_currency, live_events.currency),
        )
    live_events.connection.commit()
    with TestClient(app) as client:
        retried = client.post("/investment-events/acquisitions", json=body)

    assert created.status_code == 201, created.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == created.json()
    payload = created.json()
    assert payload["units"] == "4.00000000"
    assert payload["price"] == "5.000000"
    assert payload["grossAmount"] == "20.0000"
    assert payload["fxRateDate"] == "2026-02-01"
    assert payload["fxRateToBase"] == "1.0000000000"
    assert payload["fxRateToSecondary"] == "2.0000000000"
    assert payload["netAmountBase"] == "-21.0000"
    assert payload["netAmountSecondary"] == "-42.0000"
    assert payload["currentUnits"] == "4.00000000"
    assert payload["lot"] == {
        "id": payload["lot"]["id"],
        "acquisitionDate": "2026-02-01",
        "acquiredUnits": "4.00000000",
        "costBasisNative": "21.0000",
        "currency": live_events.currency,
    }
    with live_events.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction WHERE household_id = %s),
                 (SELECT count(*) FROM investment_unit_effect WHERE household_id = %s),
                 (SELECT count(*) FROM acquisition_lot WHERE household_id = %s)""",
            (live_events.owner.household_id,) * 3,
        )
        assert cur.fetchone() == (1, 1, 1)


def test_correction_is_idempotent_preserves_original_and_refuses_stale_second_writer(
    live_events: EventFixture,
) -> None:
    """Replacing in place or accepting two corrections would destroy the acquisition audit trail."""
    original_body = _body(live_events)
    created = TestClient(app).post("/investment-events/acquisitions", json=original_body)
    assert created.status_code == 201, created.text
    original_id = created.json()["id"]
    correction_body = {
        **_body(live_events),
        "units": "3.00000000",
        "grossAmount": "15.0000",
        "charges": "0.0000",
        "netAmount": "-15.0000",
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "Synthetic exact replacement",
    }
    with TestClient(app) as client:
        corrected = client.post(
            f"/investment-events/{original_id}/corrections", json=correction_body
        )
        retried = client.post(f"/investment-events/{original_id}/corrections", json=correction_body)
        stale = client.post(
            f"/investment-events/{original_id}/corrections",
            json={**correction_body, "idempotencyKey": str(uuid4())},
        )

    assert corrected.status_code == 201, corrected.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == corrected.json()
    assert stale.status_code == 409
    assert stale.json()["detail"] == "investment event correction state changed"
    assert corrected.json()["correctsEventId"] == original_id
    assert corrected.json()["currentUnits"] == "3.00000000"
    assert corrected.json()["lot"]["costBasisNative"] == "15.0000"
    with live_events.connection.cursor() as cur:
        cur.execute(
            """SELECT units::text, gross_amount::text, net_amount::text
                 FROM investment_transaction WHERE id = %s""",
            (original_id,),
        )
        assert cur.fetchone() == ("4.00000000", "20.0000", "-21.0000")
        cur.execute(
            """SELECT count(*), count(DISTINCT origin_transaction_id)
                 FROM acquisition_lot WHERE household_id = %s""",
            (live_events.owner.household_id,),
        )
        assert cur.fetchone() == (2, 2)
        cur.execute(
            """SELECT h.current_units::text, investment_units_from_events(h.id)::text
                 FROM holding h WHERE h.household_id = %s""",
            (live_events.owner.household_id,),
        )
        assert cur.fetchone() == ("3.00000000", "3.00000000")


def test_cross_household_references_and_foreign_correction_are_hidden(
    live_events: EventFixture,
) -> None:
    """Dropping a household predicate would allow writes into foreign investment history."""
    with TestClient(app) as client:
        foreign_account = client.post(
            "/investment-events/acquisitions",
            json={**_body(live_events), "accountId": str(live_events.other_account_id)},
        )
        foreign_security = client.post(
            "/investment-events/acquisitions",
            json={**_body(live_events), "securityId": str(live_events.other_security_id)},
        )
        foreign_correction = client.post(
            f"/investment-events/{uuid4()}/corrections",
            json={
                **_body(live_events),
                "expectedCorrectionState": "uncorrected",
                "correctionReason": "Synthetic exact replacement",
            },
        )

    assert foreign_account.status_code == 404
    assert foreign_security.status_code == 404
    assert foreign_correction.status_code == 404
    with live_events.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_events.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_conflicting_idempotency_rolls_back_a_new_holding(live_events: EventFixture) -> None:
    """A dedupe conflict occurs after holding creation and must roll the whole transaction back."""
    key = uuid4()
    first = TestClient(app).post(
        "/investment-events/acquisitions", json=_body(live_events, key=key)
    )
    assert first.status_code == 201, first.text
    with live_events.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class)
               VALUES (%s, 'mutual_fund', 'Second synthetic acquisition security', %s,
                       'XZ', 'equity') RETURNING id""",
            (live_events.owner.household_id, live_events.currency),
        )
        row = cur.fetchone()
        assert row is not None
        second_security = UUID(str(row[0]))
    live_events.connection.commit()

    conflict = TestClient(app).post(
        "/investment-events/acquisitions",
        json={**_body(live_events, key=key), "securityId": str(second_security)},
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency key conflicts with another event"
    with live_events.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM holding WHERE household_id = %s AND security_id = %s",
            (live_events.owner.household_id, second_security),
        )
        assert cur.fetchone() == (0,)


def test_correction_refuses_a_deferred_lifecycle_original(live_events: EventFixture) -> None:
    """Task 6 must not become a side door for correcting sales before Task 14 owns them."""
    acquisition = TestClient(app).post("/investment-events/acquisitions", json=_body(live_events))
    assert acquisition.status_code == 201, acquisition.text
    with live_events.connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.user_id', %s, true)",
            (str(live_events.owner.user_id),),
        )
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, '2026-02-02', 'sale',
                 -1.00000000, 5.000000, 5.0000, 0.0000, 0.0000, 5.0000, %s,
                 '2026-02-02', 1.0000000000, 1.0000000000, 5.0000, 5.0000,
                 'statement', 'synthetic_sale_v1', %s, NULL, NULL, NULL, NULL)""",
            (
                live_events.account_id,
                live_events.security_id,
                live_events.currency,
                str(uuid4()),
            ),
        )
        row = cur.fetchone()
        assert row is not None
        sale_id = UUID(str(row[0]))
    live_events.connection.commit()
    correction = TestClient(app).post(
        f"/investment-events/{sale_id}/corrections",
        json={
            **_body(live_events),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic exact replacement",
        },
    )

    assert correction.status_code == 422
    assert correction.json()["detail"] == "only acquisition events can be corrected here"
    with live_events.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE corrects_transaction_id = %s",
            (sale_id,),
        )
        assert cur.fetchone() == (0,)


def test_acquisition_refuses_client_supplied_frozen_fx(live_events: EventFixture) -> None:
    """A caller must not manufacture reporting values by supplying a self-consistent FX pair."""
    response = TestClient(app).post(
        "/investment-events/acquisitions",
        json={
            **_body(live_events),
            "fxRateDate": "2026-02-01",
            "fxRateToBase": "4.0000000000",
            "fxRateToSecondary": "5.0000000000",
            "netAmountBase": "-84.0000",
            "netAmountSecondary": "-105.0000",
        },
    )

    assert response.status_code == 422
    extra_errors = [
        error for error in response.json()["detail"] if error["type"] == "extra_forbidden"
    ]
    assert len(extra_errors) == 5
    assert all(error["loc"] == ["body", "field"] for error in extra_errors)
    assert all(
        field not in response.text
        for field in (
            "fxRateDate",
            "fxRateToBase",
            "fxRateToSecondary",
            "netAmountBase",
            "netAmountSecondary",
        )
    )
    with live_events.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM investment_transaction WHERE household_id = %s",
            (live_events.owner.household_id,),
        )
        assert cur.fetchone() == (0,)
