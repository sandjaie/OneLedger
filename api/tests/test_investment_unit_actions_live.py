"""Live owner-scoped holding-level unit action invariants: split, bonus, rights, switch,
systematic transfer and platform transfer."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from oneledger import auth
from oneledger.investments import unit_actions
from oneledger.main import app
from oneledger.routes import investment_catalogue, portfolio

from .live_database import connect_to_test_database


@dataclass(frozen=True)
class UnitActionFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    account_id: UUID
    account_2_id: UUID
    security_id: UUID
    security_2_id: UUID
    other_account_id: UUID
    other_security_id: UUID
    currency: str
    secondary_currency: str


@pytest.fixture
def live_unit_actions(monkeypatch: pytest.MonkeyPatch) -> Iterator[UnitActionFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(
        user_id=user_id,
        household_id=household_id,
        session_id=uuid4(),
        email="unit-action-owner@example.invalid",
    )
    other_owner = auth.OwnerSession(
        user_id=other_user_id,
        household_id=other_household_id,
        session_id=uuid4(),
        email="other-unit-action-owner@example.invalid",
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        for household, name in (
            (household_id, "unit action household"),
            (other_household_id, "other unit action household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for rate_date in ("2026-02-01", "2026-02-02", "2026-02-15", "2026-03-01"):
            cur.execute(
                """INSERT INTO fx_rate (
                       rate_date, base_currency, quote_currency, rate, source)
                   VALUES (%s, %s, %s, 0.5000000000, 'manual')
                   ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                     SET rate = EXCLUDED.rate, source = EXCLUDED.source""",
                (rate_date, secondary, base),
            )
        for person, household, name in (
            (person_id, household_id, "unit action owner"),
            (other_person_id, other_household_id, "other unit action owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, "unit-action-owner@example.invalid"),
            (
                other_user_id,
                other_household_id,
                other_person_id,
                "other-unit-action-owner@example.invalid",
            ),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )
        account_ids: list[UUID] = []
        for household, person, name in (
            (household_id, person_id, "Synthetic unit action account one"),
            (household_id, person_id, "Synthetic unit action account two"),
            (other_household_id, other_person_id, "Other synthetic unit action account"),
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
            (household_id, "Synthetic unit action security one"),
            (household_id, "Synthetic unit action security two"),
            (other_household_id, "Other synthetic unit action security"),
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
    monkeypatch.setattr(unit_actions, "connect", connect_to_test_database)
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield UnitActionFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            account_id=account_ids[0],
            account_2_id=account_ids[1],
            security_id=security_ids[0],
            security_2_id=security_ids[1],
            other_account_id=account_ids[2],
            other_security_id=security_ids[2],
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _post(path: str, body: dict[str, object]) -> Response:
    return cast(Response, TestClient(app).post(f"/investment-events/{path}", json=body))


def _purchase(
    fixture: UnitActionFixture,
    *,
    account_id: UUID | None = None,
    security_id: UUID | None = None,
    units: str = "10.00000000",
    gross: str = "100.0000",
    event_date: str = "2026-02-01",
) -> dict[str, object]:
    body: dict[str, object] = {
        "accountId": str(account_id or fixture.account_id),
        "securityId": str(security_id or fixture.security_id),
        "eventDate": event_date,
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
    response = _post("acquisitions", body)
    assert response.status_code == 201, response.text
    return cast(dict[str, object], response.json())


def _bonus_body(fixture: UnitActionFixture, *, key: UUID | None = None) -> dict[str, object]:
    return {
        "kind": "bonus",
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": "2026-02-02",
        "units": "2.00000000",
        "currency": fixture.currency,
        "idempotencyKey": str(key or uuid4()),
    }


def _rights_body(fixture: UnitActionFixture, *, key: UUID | None = None) -> dict[str, object]:
    return {
        "kind": "rights",
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": "2026-02-02",
        "units": "5.00000000",
        "price": "8.000000",
        "grossAmount": "40.0000",
        "charges": "1.0000",
        "withheldTax": "0.0000",
        "netAmount": "-41.0000",
        "currency": fixture.currency,
        "idempotencyKey": str(key or uuid4()),
    }


def _split_body(
    fixture: UnitActionFixture,
    *,
    ratio: str = "2",
    event_date: str = "2026-02-02",
    key: UUID | None = None,
) -> dict[str, object]:
    return {
        "kind": "split",
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": event_date,
        "unitRatio": ratio,
        "idempotencyKey": str(key or uuid4()),
    }


def _switch_body(fixture: UnitActionFixture, *, key: UUID | None = None) -> dict[str, object]:
    return {
        "kind": "switch",
        "accountId": str(fixture.account_id),
        "outSecurityId": str(fixture.security_id),
        "inSecurityId": str(fixture.security_2_id),
        "eventDate": "2026-02-02",
        "outUnits": "-4.00000000",
        "outPrice": "10.000000",
        "outGrossAmount": "40.0000",
        "inUnits": "8.00000000",
        "inPrice": "5.000000",
        "inGrossAmount": "40.0000",
        "currency": fixture.currency,
        "idempotencyKey": str(key or uuid4()),
    }


def _platform_transfer_body(
    fixture: UnitActionFixture, *, key: UUID | None = None
) -> dict[str, object]:
    return {
        "kind": "platform_transfer",
        "outAccountId": str(fixture.account_id),
        "inAccountId": str(fixture.account_2_id),
        "securityId": str(fixture.security_id),
        "eventDate": "2026-02-02",
        "outUnits": "-6.00000000",
        "inUnits": "6.00000000",
        "currency": fixture.currency,
        "idempotencyKey": str(key or uuid4()),
    }


def test_bonus_creates_a_zero_cost_lot_with_an_identical_retry(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    body = _bonus_body(live_unit_actions)

    created = _post("unit-actions", body)
    retried = _post("unit-actions", body)

    assert created.status_code == 201, created.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == created.json()
    payload = created.json()
    assert payload["kind"] == "bonus"
    assert payload["event"]["netAmount"] == "0.0000"
    assert payload["event"]["currentUnits"] == "12.00000000"
    assert payload["event"]["lot"] == {
        "id": payload["event"]["lot"]["id"],
        "acquisitionDate": "2026-02-02",
        "acquiredUnits": "2.00000000",
        "costBasisNative": "0.0000",
        "currency": live_unit_actions.currency,
    }


def test_rights_is_a_funded_acquisition_with_frozen_reporting_amounts(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    body = _rights_body(live_unit_actions)

    response = _post("unit-actions", body)

    assert response.status_code == 201, response.text
    event = response.json()["event"]
    assert event["netAmount"] == "-41.0000"
    assert event["currentUnits"] == "15.00000000"
    assert event["lot"]["costBasisNative"] == "41.0000"


def test_split_doubles_units_creates_no_lot_and_replays_idempotently(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    key = uuid4()
    body = _split_body(live_unit_actions, key=key)

    created = _post("unit-actions", body)
    retried = _post("unit-actions", body)

    assert created.status_code == 201, created.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == created.json()
    event = created.json()["event"]
    assert event["eventKind"] == "split"
    assert event["units"] == "10.00000000"
    assert event["unitRatio"] == "2.0000000000"
    assert event["currentUnits"] == "20.00000000"
    assert event["lot"] is None
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM acquisition_lot WHERE household_id = %s",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (1,)  # only the original purchase lot


def test_an_inexact_split_is_refused_atomically(live_unit_actions: UnitActionFixture) -> None:
    _purchase(live_unit_actions, units="1.00000000", gross="10.0000")

    response = _post("unit-actions", _split_body(live_unit_actions, ratio="1.3333333333"))

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "inexact_split"
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction
                 WHERE household_id = %s AND event_kind = 'split'""",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_a_split_predating_a_later_purchase_is_refused(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions, event_date="2026-02-01")
    _purchase(live_unit_actions, event_date="2026-03-01", units="1.00000000", gross="10.0000")

    response = _post("unit-actions", _split_body(live_unit_actions, event_date="2026-02-15"))

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "event_order_conflict"


def test_split_correction_replaces_the_original_and_recomputes_units(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    original = _post("unit-actions", _split_body(live_unit_actions))
    assert original.status_code == 201, original.text
    original_id = original.json()["event"]["id"]

    corrected = _post(
        f"{original_id}/unit-action-corrections",
        {
            **_split_body(live_unit_actions, ratio="3", event_date="2026-03-01"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic exact replacement ratio",
        },
    )

    assert corrected.status_code == 201, corrected.text
    event = corrected.json()["event"]
    assert event["correctsEventId"] == original_id
    assert event["units"] == "20.00000000"
    assert event["currentUnits"] == "30.00000000"


def test_split_correction_cannot_skip_backward_over_an_intervening_purchase(
    live_unit_actions: UnitActionFixture,
) -> None:
    """A backdated correction must not silently absorb a purchase into the new baseline."""
    _purchase(live_unit_actions, event_date="2026-02-01")
    # Dated after the correction's target (02-02) but before the split's original date (03-01);
    # committed before the split exists, since a backdated purchase after an active split is
    # refused by the unrelated V40 unit-event order guard.
    _purchase(
        live_unit_actions,
        event_date="2026-02-15",
        units="5.00000000",
        gross="50.0000",
    )
    original = _post("unit-actions", _split_body(live_unit_actions, event_date="2026-03-01"))
    assert original.status_code == 201, original.text
    original_id = original.json()["event"]["id"]

    corrected = _post(
        f"{original_id}/unit-action-corrections",
        {
            **_split_body(live_unit_actions, event_date="2026-02-02"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic backdated split correction",
        },
    )

    assert corrected.status_code == 422, corrected.text
    assert corrected.json()["detail"] == "event_order_conflict"
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction
                 WHERE household_id = %s AND event_kind = 'split'""",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (1,)


def test_switch_moves_equal_gross_value_between_two_securities(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    key = uuid4()
    body = _switch_body(live_unit_actions, key=key)

    created = _post("unit-actions", body)
    retried = _post("unit-actions", body)

    assert created.status_code == 201, created.text
    assert retried.status_code == 200, retried.text
    assert retried.json() == created.json()
    payload = created.json()
    assert payload["kind"] == "switch"
    assert payload["outEvent"]["eventKind"] == "switch_out"
    assert payload["outEvent"]["currentUnits"] == "6.00000000"
    assert payload["inEvent"]["eventKind"] == "switch_in"
    assert payload["inEvent"]["currentUnits"] == "8.00000000"


def test_switch_with_unequal_gross_value_is_refused_before_any_write(
    live_unit_actions: UnitActionFixture,
) -> None:
    """The wire contract itself refuses a mismatched pair; db/tests/0038 proves the database
    would refuse it too (as investment_unit_transfer_pair_agrees) for any other write path."""
    _purchase(live_unit_actions)
    body = {**_switch_body(live_unit_actions), "inGrossAmount": "41.0000"}

    response = _post("unit-actions", body)

    assert response.status_code == 422, response.text
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction
                 WHERE household_id = %s AND event_kind::text LIKE 'switch%%'""",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_platform_transfer_moves_opposite_units_across_two_accounts(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    body = _platform_transfer_body(live_unit_actions)

    response = _post("unit-actions", body)

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["outEvent"]["currentUnits"] == "4.00000000"
    assert payload["inEvent"]["currentUnits"] == "6.00000000"
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM acquisition_lot WHERE household_id = %s",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (1,)  # only the original purchase lot; no lot on either leg


def test_owner_only_and_household_scope_refuse_before_any_write(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    app.dependency_overrides[auth.require_owner] = lambda: live_unit_actions.other_owner

    foreign_split = _post("unit-actions", _split_body(live_unit_actions))
    foreign_platform = _post("unit-actions", _platform_transfer_body(live_unit_actions))

    assert foreign_split.status_code == 404, foreign_split.text
    assert foreign_platform.status_code == 404, foreign_platform.text
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction
                 WHERE household_id = %s AND event_kind IN (
                   'split', 'platform_transfer_in', 'platform_transfer_out')""",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (0,)


def test_lot_allocation_is_refused_for_a_switch_out_leg(
    live_unit_actions: UnitActionFixture,
) -> None:
    """Financial write floor: a switch leg must never gain a per-lot realised claim."""
    purchase = _purchase(live_unit_actions)
    switch = _post("unit-actions", _switch_body(live_unit_actions))
    assert switch.status_code == 201, switch.text
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            "SELECT id FROM acquisition_lot WHERE origin_transaction_id = %s",
            (purchase["id"],),
        )
        lot_row = cur.fetchone()
        assert lot_row is not None
        cur.execute(
            "SELECT set_config('app.user_id', %s, true)", (str(live_unit_actions.owner.user_id),)
        )
        cur.execute("SELECT set_config('oneledger.investment_lot_allocation_write', 'on', true)")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """INSERT INTO investment_lot_allocation (
                       household_id, holding_id, disposal_transaction_id, acquisition_lot_id,
                       allocated_units, allocated_by)
                   SELECT %s, holding_id, %s, %s, 4.00000000, %s
                     FROM investment_transaction WHERE id = %s""",
                (
                    live_unit_actions.owner.household_id,
                    switch.json()["outEvent"]["id"],
                    lot_row[0],
                    live_unit_actions.owner.user_id,
                    switch.json()["outEvent"]["id"],
                ),
            )
    live_unit_actions.connection.rollback()


def _price(security_id: UUID, on: str, value: str) -> None:
    response = TestClient(app).post(
        f"/investment-catalogue/securities/{security_id}/manual-prices",
        json={"priceDate": on, "price": value},
    )
    assert response.status_code == 201, response.text


def _positions(as_of: str) -> list[dict[str, object]]:
    response = TestClient(app).get("/portfolio/positions", params={"asOf": as_of})
    assert response.status_code == 200, response.text
    return cast(list[dict[str, object]], response.json()["positions"])


def test_rights_adds_contribution_and_bonus_adds_none(
    live_unit_actions: UnitActionFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    _purchase(live_unit_actions)  # contributes 100.0000 / 100.0000 / 200.0000
    assert _post("unit-actions", _bonus_body(live_unit_actions)).status_code == 201
    assert _post("unit-actions", _rights_body(live_unit_actions)).status_code == 201  # +41/41/82

    [row] = _positions("2026-02-02")

    assert row["cumulativeContributionsNative"] == "141.0000"
    assert row["cumulativeContributionsBase"] == "141.0000"
    assert row["cumulativeContributionsSecondary"] == "282.0000"


def test_gain_stays_available_after_purchase_bonus_rights_and_split(
    live_unit_actions: UnitActionFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    _purchase(live_unit_actions)
    assert _post("unit-actions", _bonus_body(live_unit_actions)).status_code == 201
    assert _post("unit-actions", _rights_body(live_unit_actions)).status_code == 201
    split = _post("unit-actions", _split_body(live_unit_actions, event_date="2026-02-15"))
    assert split.status_code == 201, split.text
    _price(live_unit_actions.security_id, "2026-02-15", "10.000000")

    [row] = _positions("2026-02-15")

    assert row["gainNative"] is not None
    assert row["gainBase"] is not None


def test_gain_is_withheld_on_both_holdings_after_a_switch(
    live_unit_actions: UnitActionFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    _purchase(live_unit_actions)
    switch = _post("unit-actions", _switch_body(live_unit_actions))
    assert switch.status_code == 201, switch.text
    _price(live_unit_actions.security_id, "2026-02-02", "10.000000")
    _price(live_unit_actions.security_2_id, "2026-02-02", "5.000000")

    positions = {row["securityId"]: row for row in _positions("2026-02-02")}

    assert positions[str(live_unit_actions.security_id)]["gainNative"] is None
    assert positions[str(live_unit_actions.security_2_id)]["gainNative"] is None


def test_gain_is_withheld_on_both_accounts_after_a_platform_transfer(
    live_unit_actions: UnitActionFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(portfolio, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    _purchase(live_unit_actions)
    transfer = _post("unit-actions", _platform_transfer_body(live_unit_actions))
    assert transfer.status_code == 201, transfer.text
    _price(live_unit_actions.security_id, "2026-02-02", "10.000000")

    positions = {row["accountId"]: row for row in _positions("2026-02-02")}

    assert positions[str(live_unit_actions.account_id)]["gainNative"] is None
    assert positions[str(live_unit_actions.account_2_id)]["gainNative"] is None


def test_split_correction_stale_conflicting_and_identical_retry(
    live_unit_actions: UnitActionFixture,
) -> None:
    """Financial write floor: stale second writer, conflicting key reuse and identical retry."""
    _purchase(live_unit_actions)
    original = _post("unit-actions", _split_body(live_unit_actions))
    assert original.status_code == 201, original.text
    original_id = original.json()["event"]["id"]
    correction_body = {
        **_split_body(live_unit_actions, ratio="3", event_date="2026-03-01"),
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "Synthetic exact replacement ratio",
    }

    corrected = _post(f"{original_id}/unit-action-corrections", correction_body)
    assert corrected.status_code == 201, corrected.text

    identical_retry = _post(f"{original_id}/unit-action-corrections", correction_body)
    assert identical_retry.status_code == 200, identical_retry.text
    assert identical_retry.json() == corrected.json()

    conflicting_retry = _post(
        f"{original_id}/unit-action-corrections",
        {**correction_body, "unitRatio": "4"},
    )
    assert conflicting_retry.status_code == 409, conflicting_retry.text

    stale = _post(
        f"{original_id}/unit-action-corrections",
        {**correction_body, "idempotencyKey": str(uuid4())},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"] == "investment event correction state changed"

    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction
                 WHERE household_id = %s AND event_kind = 'split'""",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (2,)


def test_split_correction_reason_containing_private_text_is_refused(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    original = _post("unit-actions", _split_body(live_unit_actions))
    assert original.status_code == 201, original.text
    original_id = original.json()["event"]["id"]

    response = _post(
        f"{original_id}/unit-action-corrections",
        {
            **_split_body(live_unit_actions, ratio="3", event_date="2026-03-01"),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "PIN " + "1234",
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "correction reason must not contain private information"
    with live_unit_actions.connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM investment_transaction
                 WHERE household_id = %s AND event_kind = 'split'""",
            (live_unit_actions.owner.household_id,),
        )
        assert cur.fetchone() == (1,)


def test_bonus_correction_replaces_the_original(live_unit_actions: UnitActionFixture) -> None:
    _purchase(live_unit_actions)
    original = _post("unit-actions", _bonus_body(live_unit_actions))
    assert original.status_code == 201, original.text
    original_id = original.json()["event"]["id"]

    corrected = _post(
        f"{original_id}/unit-action-corrections",
        {
            **_bonus_body(live_unit_actions),
            "units": "3.00000000",
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic bonus unit correction",
        },
    )

    assert corrected.status_code == 201, corrected.text
    event = corrected.json()["event"]
    assert event["correctsEventId"] == original_id
    assert event["units"] == "3.00000000"
    assert event["currentUnits"] == "13.00000000"


def test_rights_correction_replaces_the_original_and_refreezes_fx(
    live_unit_actions: UnitActionFixture,
) -> None:
    _purchase(live_unit_actions)
    original = _post("unit-actions", _rights_body(live_unit_actions))
    assert original.status_code == 201, original.text
    original_id = original.json()["event"]["id"]

    corrected = _post(
        f"{original_id}/unit-action-corrections",
        {
            **_rights_body(live_unit_actions),
            "units": "4.00000000",
            "price": "10.000000",
            "grossAmount": "40.0000",
            "netAmount": "-41.0000",
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic rights unit correction",
        },
    )

    assert corrected.status_code == 201, corrected.text
    event = corrected.json()["event"]
    assert event["correctsEventId"] == original_id
    assert event["units"] == "4.00000000"
    assert event["currentUnits"] == "14.00000000"
    assert event["lot"]["costBasisNative"] == "41.0000"


def test_pair_correction_restores_units_on_both_holdings(
    live_unit_actions: UnitActionFixture,
) -> None:
    """Correcting a pair to different amounts must exclude the superseded legs entirely, not
    add the new effect on top of the old one."""
    _purchase(live_unit_actions)
    original = _post("unit-actions", _switch_body(live_unit_actions))
    assert original.status_code == 201, original.text
    out_id = original.json()["outEvent"]["id"]

    corrected = _post(
        f"{out_id}/unit-action-corrections",
        {
            **_switch_body(live_unit_actions),
            "outUnits": "-2.00000000",
            "outGrossAmount": "20.0000",
            "inUnits": "4.00000000",
            "inGrossAmount": "20.0000",
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic switch amount correction",
        },
    )

    assert corrected.status_code == 201, corrected.text
    payload = corrected.json()
    assert payload["outEvent"]["correctsEventId"] == out_id
    assert payload["outEvent"]["currentUnits"] == "8.00000000"  # 10 - 2, not 10 - 4 - 2
    assert payload["inEvent"]["currentUnits"] == "4.00000000"  # 4, not 8 + 4
