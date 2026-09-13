"""Live owner-scoped disposal and cash lifecycle invariants."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database


@dataclass(frozen=True)
class LifecycleFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    account_id: UUID
    security_id: UUID
    other_account_id: UUID
    other_security_id: UUID
    currency: str
    secondary_currency: str


@pytest.fixture
def live_lifecycle() -> Iterator[LifecycleFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(
        user_id=user_id,
        household_id=household_id,
        session_id=uuid4(),
        email="lifecycle-owner@example.invalid",
    )
    other_owner = auth.OwnerSession(
        user_id=other_user_id,
        household_id=other_household_id,
        session_id=uuid4(),
        email="other-lifecycle-owner@example.invalid",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        for household, name in (
            (household_id, "lifecycle household"),
            (other_household_id, "other lifecycle household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for day in range(1, 11):
            rate_date = f"2026-02-{day:02d}"
            cur.execute(
                """INSERT INTO fx_rate (
                       rate_date, base_currency, quote_currency, rate, source)
                   VALUES (%s, %s, %s, 0.5000000000, 'manual')
                   ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                     SET rate = EXCLUDED.rate, source = EXCLUDED.source""",
                (rate_date, secondary, base),
            )
        for person, household, name in (
            (person_id, household_id, "lifecycle owner"),
            (other_person_id, other_household_id, "other lifecycle owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, "lifecycle-owner@example.invalid"),
            (
                other_user_id,
                other_household_id,
                other_person_id,
                "other-lifecycle-owner@example.invalid",
            ),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )
        account_ids: list[UUID] = []
        for household, person, name in (
            (household_id, person_id, "Synthetic lifecycle account"),
            (other_household_id, other_person_id, "Other synthetic lifecycle account"),
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
            (household_id, "Synthetic lifecycle security"),
            (other_household_id, "Other synthetic lifecycle security"),
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
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield LifecycleFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
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


def _acquisition(fixture: LifecycleFixture, *, units: str = "10.00000000") -> dict[str, object]:
    gross = "20.0000" if units == "10.00000000" else "8.0000"
    return {
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": "2026-02-01",
        "eventKind": "purchase",
        "units": units,
        "price": "2.000000",
        "grossAmount": gross,
        "charges": "0.0000",
        "withheldTax": "0.0000",
        "netAmount": f"-{gross}",
        "currency": fixture.currency,
        "idempotencyKey": str(uuid4()),
    }


def _lifecycle(
    fixture: LifecycleFixture, kind: str, *, key: UUID | None = None
) -> dict[str, object]:
    dates = {
        "sale": "2026-02-02",
        "redemption": "2026-02-03",
        "maturity": "2026-02-04",
        "cash_distribution": "2026-02-05",
        "interest": "2026-02-06",
        "reinvested_distribution": "2026-02-07",
        "charge": "2026-02-08",
        "withheld_tax": "2026-02-09",
    }
    common: dict[str, object] = {
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": dates[kind],
        "eventKind": kind,
        "charges": "0.0000",
        "withheldTax": "0.0000",
        "currency": fixture.currency,
        "idempotencyKey": str(key or uuid4()),
    }
    shape: dict[str, object]
    if kind in {"sale", "redemption", "maturity"}:
        shape = {
            "units": "-1.00000000",
            "price": "3.000000",
            "grossAmount": "3.0000",
            "netAmount": "3.0000",
        }
    elif kind in {"cash_distribution", "interest"}:
        shape = {
            "units": None,
            "price": None,
            "grossAmount": "2.0000",
            "netAmount": "2.0000",
        }
    elif kind == "reinvested_distribution":
        shape = {
            "units": "1.50000000",
            "price": "2.000000",
            "grossAmount": "3.0000",
            "withheldTax": "0.3750",
            "netAmount": "0.0000",
        }
    elif kind == "charge":
        shape = {
            "units": None,
            "price": None,
            "grossAmount": None,
            "charges": "1.2500",
            "netAmount": "-1.2500",
        }
    else:
        shape = {
            "units": None,
            "price": None,
            "grossAmount": None,
            "withheldTax": "0.7500",
            "netAmount": "-0.7500",
        }
    return {**common, **shape}


def _post(body: dict[str, object]) -> Response:
    return cast(Response, TestClient(app).post("/investment-events/cash-lifecycle", json=body))


def _allocate_lots(disposal_id: str, allocations: list[tuple[str, str]]) -> Response:
    return cast(
        Response,
        TestClient(app).post(
            "/investment-funding/lot-allocations",
            json={
                "disposalEventId": disposal_id,
                "allocations": [
                    {"acquisitionLotId": lot_id, "allocatedUnits": units}
                    for lot_id, units in allocations
                ],
            },
        ),
    )


def test_all_cash_lifecycle_kinds_are_exact_idempotent_and_count_tax_once(
    live_lifecycle: LifecycleFixture,
) -> None:
    """Dropping a kind branch or sharing tax/cash effects could duplicate or reinterpret facts."""
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    assert acquisition.status_code == 201, acquisition.text
    results: dict[str, Any] = {}
    bodies: dict[str, dict[str, object]] = {}
    for kind in (
        "sale",
        "redemption",
        "maturity",
        "cash_distribution",
        "interest",
        "reinvested_distribution",
        "charge",
        "withheld_tax",
    ):
        body = _lifecycle(live_lifecycle, kind)
        response = _post(body)
        assert response.status_code == 201, response.text
        retry = _post(body)
        assert retry.status_code == 200, retry.text
        assert retry.json() == response.json()
        results[kind] = response.json()
        bodies[kind] = body

    for kind in ("sale", "redemption", "maturity"):
        payload = results[kind]
        assert payload["units"] == "-1.00000000"
        assert payload["netAmount"] == "3.0000"
        assert payload["netAmountBase"] == "3.0000"
        assert payload["netAmountSecondary"] == "6.0000"
        assert payload["reviewReasons"] == ["incomplete_lot_allocation"]
        assert payload["lot"] is None
        assert not {"realisedCost", "realisedGain", "remainingBasis"}.intersection(payload)
    for kind in ("cash_distribution", "interest"):
        payload = results[kind]
        assert payload["units"] is None
        assert payload["grossAmount"] == "2.0000"
        assert payload["netAmount"] == "2.0000"
        assert payload["fxRateDate"] == bodies[kind]["eventDate"]
        assert payload["fxRateToBase"] == "1.0000000000"
        assert payload["fxRateToSecondary"] == "2.0000000000"
        assert payload["netAmountBase"] == "2.0000"
        assert payload["netAmountSecondary"] == "4.0000"
        assert payload["reviewReasons"] == []
    assert results["charge"]["netAmount"] == "-1.2500"
    assert results["charge"]["netAmountSecondary"] == "-2.5000"
    assert results["withheld_tax"]["netAmount"] == "-0.7500"
    assert results["withheld_tax"]["netAmountSecondary"] == "-1.5000"
    reinvestment = results["reinvested_distribution"]
    assert reinvestment["withheldTax"] == "0.3750"
    assert reinvestment["netAmount"] == "0.0000"
    assert reinvestment["fxRateDate"] is None
    assert reinvestment["fxRateToBase"] is None
    assert reinvestment["fxRateToSecondary"] is None
    assert reinvestment["netAmountBase"] is None
    assert reinvestment["netAmountSecondary"] is None
    assert reinvestment["lot"] == {
        "id": reinvestment["lot"]["id"],
        "acquisitionDate": "2026-02-07",
        "acquiredUnits": "1.50000000",
        "costBasisNative": "3.0000",
        "currency": live_lifecycle.currency,
    }
    assert reinvestment["currentUnits"] == "8.50000000"

    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction WHERE household_id = %s),
                 (SELECT count(*) FROM investment_unit_effect WHERE household_id = %s),
                 (SELECT count(*) FROM acquisition_lot WHERE household_id = %s),
                 (SELECT count(*) FROM investment_lot_allocation WHERE household_id = %s),
                 (SELECT count(*) FROM investment_review_item
                   WHERE household_id = %s AND reason = 'incomplete_lot_allocation'
                     AND status = 'open')""",
            (live_lifecycle.owner.household_id,) * 5,
        )
        assert cur.fetchone() == (9, 5, 2, 0, 3)
        cur.execute(
            """SELECT event_kind::text, count(*) FROM investment_transaction
                 WHERE household_id = %s GROUP BY event_kind ORDER BY event_kind""",
            (live_lifecycle.owner.household_id,),
        )
        event_counts: dict[str, int] = dict(cur.fetchall())
        assert event_counts["reinvested_distribution"] == 1
        assert event_counts["withheld_tax"] == 1

        cur.execute(
            """UPDATE fx_rate SET rate = 0.2500000000
                 WHERE rate_date = '2026-02-05'
                   AND base_currency = %s AND quote_currency = %s""",
            (live_lifecycle.secondary_currency, live_lifecycle.currency),
        )
    live_lifecycle.connection.commit()
    replay_after_rate_change = _post(bodies["cash_distribution"])
    assert replay_after_rate_change.status_code == 200
    assert replay_after_rate_change.json()["id"] == results["cash_distribution"]["id"]
    assert replay_after_rate_change.json()["netAmountSecondary"] == "4.0000"


def test_partial_close_and_reinvestment_reopen_preserve_holding_history(
    live_lifecycle: LifecycleFixture,
) -> None:
    """Replacing rather than projecting signed effects would lose partial and closed history."""
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions",
        json=_acquisition(live_lifecycle, units="4.00000000"),
    )
    assert acquisition.status_code == 201, acquisition.text
    partial = _post(_lifecycle(live_lifecycle, "sale"))
    assert partial.status_code == 201, partial.text
    close = _post(
        {
            **_lifecycle(live_lifecycle, "redemption"),
            "units": "-3.00000000",
            "price": "3.000000",
            "grossAmount": "9.0000",
            "netAmount": "9.0000",
        }
    )
    assert close.status_code == 201, close.text
    reopened = _post(_lifecycle(live_lifecycle, "reinvested_distribution"))

    assert partial.json()["currentUnits"] == "3.00000000"
    assert close.json()["currentUnits"] == "0.00000000"
    assert reopened.status_code == 201, reopened.text
    assert reopened.json()["currentUnits"] == "1.50000000"
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT current_units::text, opened_on, closed_on,
                      investment_units_from_events(id)::text
                 FROM holding WHERE household_id = %s""",
            (live_lifecycle.owner.household_id,),
        )
        assert cur.fetchone() == ("1.50000000", date(2026, 2, 1), None, "1.50000000")
        cur.execute(
            """SELECT count(*) FROM acquisition_lot WHERE household_id = %s""",
            (live_lifecycle.owner.household_id,),
        )
        assert cur.fetchone() == (2,)


def test_evidenced_partial_sale_across_lots_exposes_only_exact_supported_claims(
    live_lifecycle: LifecycleFixture,
) -> None:
    first = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    second_body = {
        **_acquisition(live_lifecycle, units="4.00000000"),
        "eventDate": "2026-02-02",
        "idempotencyKey": str(uuid4()),
    }
    second = TestClient(app).post("/investment-events/acquisitions", json=second_body)
    assert first.status_code == second.status_code == 201
    sale = _post(
        {
            **_lifecycle(live_lifecycle, "sale"),
            "eventDate": "2026-02-03",
            "units": "-5.00000000",
            "grossAmount": "15.0000",
            "netAmount": "15.0000",
        }
    )
    assert sale.status_code == 201, sale.text
    assert sale.json()["reviewReasons"] == ["incomplete_lot_allocation"]

    confirmed = _allocate_lots(
        sale.json()["id"],
        [
            (first.json()["lot"]["id"], "3.00000000"),
            (second.json()["lot"]["id"], "2.00000000"),
        ],
    )

    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json() == {
        "state": "confirmed",
        "disposalEventId": sale.json()["id"],
        "allocations": [
            {
                "acquisitionLotId": first.json()["lot"]["id"],
                "allocatedUnits": "3.00000000",
            },
            {
                "acquisitionLotId": second.json()["lot"]["id"],
                "allocatedUnits": "2.00000000",
            },
        ],
        "realisedCostNative": "10.0000",
        "realisedGainNative": "5.0000",
        "remainingBasisNative": "18.0000",
        "reviewReasons": [],
    }
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT status::text, resolution_kind::text, resolution_reference_id
                 FROM investment_review_item WHERE investment_transaction_id = %s""",
            (UUID(sale.json()["id"]),),
        )
        assert cur.fetchone() == ("resolved", "lot_allocated", UUID(sale.json()["id"]))


def test_incomplete_lot_evidence_withholds_claims_until_the_disposal_is_fully_allocated(
    live_lifecycle: LifecycleFixture,
) -> None:
    first = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    second = TestClient(app).post(
        "/investment-events/acquisitions",
        json={
            **_acquisition(live_lifecycle, units="4.00000000"),
            "eventDate": "2026-02-02",
            "idempotencyKey": str(uuid4()),
        },
    )
    sale = _post(
        {
            **_lifecycle(live_lifecycle, "sale"),
            "eventDate": "2026-02-03",
            "units": "-4.00000000",
            "grossAmount": "12.0000",
            "netAmount": "12.0000",
        }
    )
    partial = _allocate_lots(sale.json()["id"], [(first.json()["lot"]["id"], "2.00000000")])
    assert partial.status_code == 201, partial.text
    assert partial.json()["realisedCostNative"] is None
    assert partial.json()["realisedGainNative"] is None
    assert partial.json()["remainingBasisNative"] is None
    assert partial.json()["reviewReasons"] == ["incomplete_lot_allocation"]

    completed = _allocate_lots(sale.json()["id"], [(second.json()["lot"]["id"], "2.00000000")])
    assert completed.status_code == 201, completed.text
    assert completed.json()["realisedCostNative"] == "8.0000"
    assert completed.json()["realisedGainNative"] == "4.0000"
    assert completed.json()["remainingBasisNative"] == "20.0000"
    assert completed.json()["reviewReasons"] == []


def test_lot_overallocation_and_foreign_evidence_refuse_atomically(
    live_lifecycle: LifecycleFixture,
) -> None:
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    sale = _post(_lifecycle(live_lifecycle, "sale"))
    assert acquisition.status_code == sale.status_code == 201

    app.dependency_overrides[auth.require_owner] = lambda: live_lifecycle.other_owner
    foreign_acquisition = TestClient(app).post(
        "/investment-events/acquisitions",
        json={
            **_acquisition(live_lifecycle),
            "accountId": str(live_lifecycle.other_account_id),
            "securityId": str(live_lifecycle.other_security_id),
            "idempotencyKey": str(uuid4()),
        },
    )
    assert foreign_acquisition.status_code == 201, foreign_acquisition.text
    app.dependency_overrides[auth.require_owner] = lambda: live_lifecycle.owner

    over = _allocate_lots(sale.json()["id"], [(acquisition.json()["lot"]["id"], "2.00000000")])
    foreign = _allocate_lots(
        sale.json()["id"], [(foreign_acquisition.json()["lot"]["id"], "1.00000000")]
    )
    assert over.status_code == 422
    assert foreign.status_code == 404
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_lot_allocation WHERE household_id = %s),
                 (SELECT count(*) FROM investment_review_item
                   WHERE household_id = %s AND investment_transaction_id = %s
                     AND status = 'open')""",
            (
                live_lifecycle.owner.household_id,
                live_lifecycle.owner.household_id,
                UUID(sale.json()["id"]),
            ),
        )
        assert cur.fetchone() == (0, 1)


def test_disposal_correction_resolves_original_review_and_retry_cannot_reopen_it(
    live_lifecycle: LifecycleFixture,
) -> None:
    """A correction must close the old question once and never recreate it on exact replay."""
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    assert acquisition.status_code == 201, acquisition.text
    original_body = {
        **_lifecycle(live_lifecycle, "sale"),
        "units": "-2.00000000",
        "grossAmount": "6.0000",
        "netAmount": "6.0000",
    }
    original = _post(original_body)
    assert original.status_code == 201, original.text
    correction_body = {
        **_lifecycle(live_lifecycle, "sale"),
        "idempotencyKey": str(uuid4()),
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "  Synthetic   disposal replacement  ",
    }
    corrected = TestClient(app).post(
        f"/investment-events/{original.json()['id']}/cash-lifecycle-corrections",
        json=correction_body,
    )
    retry = TestClient(app).post(
        f"/investment-events/{original.json()['id']}/cash-lifecycle-corrections",
        json=correction_body,
    )
    stale = TestClient(app).post(
        f"/investment-events/{original.json()['id']}/cash-lifecycle-corrections",
        json={**correction_body, "idempotencyKey": str(uuid4())},
    )

    assert corrected.status_code == 201, corrected.text
    assert retry.status_code == 200
    assert retry.json() == corrected.json()
    assert stale.status_code == 409
    assert corrected.json()["correctsEventId"] == original.json()["id"]
    assert corrected.json()["currentUnits"] == "9.00000000"
    assert corrected.json()["reviewReasons"] == ["incomplete_lot_allocation"]
    original_retry = _post(original_body)
    assert original_retry.status_code == 200
    assert original_retry.json()["id"] == original.json()["id"]
    assert original_retry.json()["reviewReasons"] == []
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT units::text, gross_amount::text FROM investment_transaction
                 WHERE id = %s""",
            (original.json()["id"],),
        )
        assert cur.fetchone() == ("-2.00000000", "6.0000")
        cur.execute(
            """SELECT status::text, resolution_kind::text,
                      resolution_reference_id, count(*)
                 FROM investment_review_item WHERE household_id = %s
                   AND reason = 'incomplete_lot_allocation'
                 GROUP BY status, resolution_kind, resolution_reference_id
                 ORDER BY status""",
            (live_lifecycle.owner.household_id,),
        )
        assert cur.fetchall() == [
            ("open", None, None, 1),
            ("resolved", "event_corrected", UUID(corrected.json()["id"]), 1),
        ]
    live_lifecycle.connection.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        with live_lifecycle.connection.transaction(), live_lifecycle.connection.cursor() as cur:
            cur.execute(
                """INSERT INTO investment_review_item (
                       household_id, investment_transaction_id, holding_id, reason)
                     VALUES (%s, %s, %s, 'incomplete_lot_allocation')""",
                (
                    live_lifecycle.owner.household_id,
                    UUID(original.json()["id"]),
                    UUID(original.json()["holdingId"]),
                ),
            )


def test_disposal_corrected_to_income_leaves_only_resolved_review_history(
    live_lifecycle: LifecycleFixture,
) -> None:
    """A non-disposal replacement must not retain an active lot-allocation question."""
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    assert acquisition.status_code == 201, acquisition.text
    original = _post(_lifecycle(live_lifecycle, "sale"))
    assert original.status_code == 201, original.text
    corrected = TestClient(app).post(
        f"/investment-events/{original.json()['id']}/cash-lifecycle-corrections",
        json={
            **_lifecycle(live_lifecycle, "cash_distribution"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic event-kind replacement",
        },
    )

    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["currentUnits"] == "10.00000000"
    assert corrected.json()["reviewReasons"] == []
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT status::text, resolution_kind::text, resolution_reference_id
                 FROM investment_review_item WHERE household_id = %s
                   AND reason = 'incomplete_lot_allocation'""",
            (live_lifecycle.owner.household_id,),
        )
        assert cur.fetchall() == [("resolved", "event_corrected", UUID(corrected.json()["id"]))]


def test_other_review_reasons_do_not_expand_the_cash_lifecycle_response(
    live_lifecycle: LifecycleFixture,
) -> None:
    """Loading a legitimate unrelated review must not fail the endpoint's narrow enum."""
    body = _lifecycle(live_lifecycle, "cash_distribution")
    created = _post(body)
    assert created.status_code == 201, created.text
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO investment_review_item (
                   household_id, investment_transaction_id, holding_id, reason)
                 VALUES (%s, %s, %s, 'duplicate_conflict')""",
            (
                live_lifecycle.owner.household_id,
                UUID(created.json()["id"]),
                UUID(created.json()["holdingId"]),
            ),
        )
    live_lifecycle.connection.commit()

    retry = TestClient(app, raise_server_exceptions=False).post(
        "/investment-events/cash-lifecycle", json=body
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == created.json()["id"]
    assert retry.json()["reviewReasons"] == []


@pytest.mark.parametrize("kind", ["sale", "redemption", "maturity"])
def test_oversell_refuses_every_disposal_kind_without_partial_projection(
    live_lifecycle: LifecycleFixture, kind: str
) -> None:
    """A correctly shaped disposal cannot take reconstructed holdings below zero."""
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions",
        json=_acquisition(live_lifecycle, units="4.00000000"),
    )
    assert acquisition.status_code == 201, acquisition.text

    response = _post(
        {
            **_lifecycle(live_lifecycle, kind),
            "units": "-5.00000000",
            "grossAmount": "15.0000",
            "netAmount": "15.0000",
        }
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "investment event violates its required field matrix"}
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction WHERE household_id = %s),
                 (SELECT count(*) FROM investment_unit_effect WHERE household_id = %s),
                 (SELECT count(*) FROM acquisition_lot WHERE household_id = %s),
                 (SELECT count(*) FROM investment_review_item WHERE household_id = %s),
                 (SELECT count(*) FROM holding WHERE household_id = %s),
                 (SELECT current_units::text FROM holding WHERE household_id = %s),
                 (SELECT investment_units_from_events(id)::text
                    FROM holding WHERE household_id = %s)""",
            (live_lifecycle.owner.household_id,) * 7,
        )
        assert cur.fetchone() == (1, 1, 1, 0, 1, "4.00000000", "4.00000000")


def test_foreign_targets_and_foreign_corrections_are_hidden_atomically(
    live_lifecycle: LifecycleFixture,
) -> None:
    """Missing household predicates could append lifecycle facts into another owner's history."""
    foreign_target = _post(
        {
            **_lifecycle(live_lifecycle, "cash_distribution"),
            "accountId": str(live_lifecycle.other_account_id),
        }
    )
    cross_security = _post(
        {
            **_lifecycle(live_lifecycle, "cash_distribution"),
            "securityId": str(live_lifecycle.other_security_id),
        }
    )
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            "SELECT set_config('app.user_id', %s, true)", (str(live_lifecycle.other_owner.user_id),)
        )
        cur.execute(
            """SELECT append_investment_transaction(
                 %s, %s, '2026-02-02', 'cash_distribution',
                 NULL, NULL, 2.0000, 0.0000, 0.0000, 2.0000, %s,
                 '2026-02-02', 1.0000000000, 2.0000000000, 2.0000, 4.0000,
                 'owner_entered', 'owner_cash_lifecycle_v1', %s,
                 NULL, NULL, NULL, NULL)""",
            (
                live_lifecycle.other_account_id,
                live_lifecycle.other_security_id,
                live_lifecycle.currency,
                str(uuid4()),
            ),
        )
        foreign_event = cur.fetchone()
        assert foreign_event is not None
    live_lifecycle.connection.commit()
    foreign_correction = TestClient(app).post(
        f"/investment-events/{foreign_event[0]}/cash-lifecycle-corrections",
        json={
            **_lifecycle(live_lifecycle, "cash_distribution"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic exact replacement",
        },
    )

    assert foreign_target.status_code == 404
    assert cross_security.status_code == 404
    assert foreign_correction.status_code == 404
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction WHERE household_id = %s""",
            (live_lifecycle.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_wrong_currency_and_private_correction_reason_leave_no_partial_rows(
    live_lifecycle: LifecycleFixture,
) -> None:
    """Validation failures must not leave events, projections, lots, or review rows."""
    wrong_currency = _post(
        {
            **_lifecycle(live_lifecycle, "cash_distribution"),
            "currency": live_lifecycle.secondary_currency,
        }
    )
    acquisition = TestClient(app).post(
        "/investment-events/acquisitions", json=_acquisition(live_lifecycle)
    )
    assert acquisition.status_code == 201, acquisition.text
    disposal = _post(_lifecycle(live_lifecycle, "sale"))
    assert disposal.status_code == 201, disposal.text
    private_reason = "Reference " + "".join(("123", "456", "7"))
    correction = TestClient(app).post(
        f"/investment-events/{disposal.json()['id']}/cash-lifecycle-corrections",
        json={
            **_lifecycle(live_lifecycle, "sale"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": private_reason,
        },
    )

    assert wrong_currency.status_code == 422
    assert correction.status_code == 422
    assert private_reason not in correction.text
    with live_lifecycle.connection.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM investment_transaction WHERE household_id = %s),
                 (SELECT count(*) FROM holding WHERE household_id = %s),
                 (SELECT count(*) FROM investment_unit_effect WHERE household_id = %s),
                 (SELECT count(*) FROM acquisition_lot WHERE household_id = %s),
                 (SELECT count(*) FROM investment_review_item WHERE household_id = %s)""",
            (live_lifecycle.owner.household_id,) * 5,
        )
        assert cur.fetchone() == (2, 1, 2, 1, 1)
