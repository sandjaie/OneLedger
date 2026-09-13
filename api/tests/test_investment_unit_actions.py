"""Unit action request contracts keep every financial value exact and reject invalid shapes.

The route body is a Pydantic discriminated union (``Field(discriminator="kind")``); the shared
``main.private_validation_error`` handler cannot resolve a declared property through a
discriminator tag segment, so it safely reports a generic ``field`` location for those nested
errors rather than guessing. These tests assert the resulting status code and error `type`,
never the exact nested `loc`, and check discrimination itself directly against the schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from oneledger import auth
from oneledger.investments.schemas import (
    BonusActionCreate,
    PlatformTransferActionCreate,
    RightsActionCreate,
    SplitActionCreate,
    SwitchActionCreate,
    SystematicTransferActionCreate,
    UnitActionCreate,
)
from oneledger.main import app

_UNIT_ACTION_ADAPTER: TypeAdapter[object] = TypeAdapter(UnitActionCreate)


def _bonus_body() -> dict[str, object]:
    return {
        "kind": "bonus",
        "accountId": str(uuid4()),
        "securityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "units": "2.00000000",
        "currency": "XAA",
        "idempotencyKey": str(uuid4()),
    }


def _rights_body() -> dict[str, object]:
    return {
        "kind": "rights",
        "accountId": str(uuid4()),
        "securityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "units": "5.00000000",
        "price": "8.000000",
        "grossAmount": "40.0000",
        "charges": "1.0000",
        "withheldTax": "0.0000",
        "netAmount": "-41.0000",
        "currency": "XAA",
        "idempotencyKey": str(uuid4()),
    }


def _split_body() -> dict[str, object]:
    return {
        "kind": "split",
        "accountId": str(uuid4()),
        "securityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "unitRatio": "2",
        "idempotencyKey": str(uuid4()),
    }


def _switch_body() -> dict[str, object]:
    account_id = str(uuid4())
    return {
        "kind": "switch",
        "accountId": account_id,
        "outSecurityId": str(uuid4()),
        "inSecurityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "outUnits": "-4.00000000",
        "outPrice": "10.000000",
        "outGrossAmount": "40.0000",
        "inUnits": "8.00000000",
        "inPrice": "5.000000",
        "inGrossAmount": "40.0000",
        "currency": "XAA",
        "idempotencyKey": str(uuid4()),
    }


def _systematic_transfer_body() -> dict[str, object]:
    return {**_switch_body(), "kind": "systematic_transfer"}


def _platform_transfer_body() -> dict[str, object]:
    return {
        "kind": "platform_transfer",
        "outAccountId": str(uuid4()),
        "inAccountId": str(uuid4()),
        "securityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "outUnits": "-6.00000000",
        "inUnits": "6.00000000",
        "currency": "XAA",
        "idempotencyKey": str(uuid4()),
    }


@pytest.fixture
def owner_override() -> Iterator[None]:
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(), household_id=uuid4(), session_id=uuid4(), email="owner@example.invalid"
    )
    try:
        yield
    finally:
        app.dependency_overrides.clear()


def test_unit_action_routes_require_an_owner_session() -> None:
    """Registering these mutations without require_owner would expose a public write."""
    app.dependency_overrides.clear()

    response = TestClient(app).post("/investment-events/unit-actions", json=_bonus_body())

    assert response.status_code == 401


def test_unit_action_correction_route_requires_an_owner_session() -> None:
    app.dependency_overrides.clear()

    response = TestClient(app).post(
        f"/investment-events/{uuid4()}/unit-action-corrections",
        json={
            **_bonus_body(),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": "Synthetic replacement",
        },
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("body_factory", "expected_type"),
    [
        (_bonus_body, BonusActionCreate),
        (_rights_body, RightsActionCreate),
        (_split_body, SplitActionCreate),
        (_switch_body, SwitchActionCreate),
        (_systematic_transfer_body, SystematicTransferActionCreate),
        (_platform_transfer_body, PlatformTransferActionCreate),
    ],
)
def test_each_kind_discriminates_to_its_own_schema(
    body_factory: object, expected_type: type
) -> None:
    """A malformed discriminator mapping would reject every otherwise-valid kind at once."""
    body = body_factory()  # type: ignore[operator]

    parsed = _UNIT_ACTION_ADAPTER.validate_python(body)

    assert isinstance(parsed, expected_type)


def test_an_unknown_kind_is_refused(owner_override: None) -> None:
    response = TestClient(app).post(
        "/investment-events/unit-actions", json={**_bonus_body(), "kind": "reverse_split"}
    )

    assert response.status_code == 422


def test_bonus_units_must_be_positive(owner_override: None) -> None:
    response = TestClient(app).post(
        "/investment-events/unit-actions",
        json={**_bonus_body(), "units": "0.00000000"},
    )

    assert response.status_code == 422


def test_bonus_rejects_a_json_number_for_units(owner_override: None) -> None:
    response = TestClient(app).post(
        "/investment-events/unit-actions",
        json={**_bonus_body(), "units": 2.0},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"units": "-5.00000000"},
        {"price": "-8.000000"},
        {"netAmount": "41.0000"},
        {"netAmount": "-40.0000"},
    ],
)
def test_rights_field_matrix_refuses_invalid_facts(
    owner_override: None, changes: dict[str, object]
) -> None:
    body = {**_rights_body(), **changes}

    response = TestClient(app).post("/investment-events/unit-actions", json=body)

    assert response.status_code == 422
    with pytest.raises(ValidationError):
        RightsActionCreate.model_validate(body)


@pytest.mark.parametrize("ratio", ["0", "1", "-2"])
def test_split_ratio_must_be_positive_and_not_one(owner_override: None, ratio: str) -> None:
    response = TestClient(app).post(
        "/investment-events/unit-actions", json={**_split_body(), "unitRatio": ratio}
    )

    assert response.status_code == 422


def test_switch_requires_two_different_securities(owner_override: None) -> None:
    body = _switch_body()
    body["inSecurityId"] = body["outSecurityId"]

    response = TestClient(app).post("/investment-events/unit-actions", json=body)

    assert response.status_code == 422


def test_switch_requires_equal_gross_value(owner_override: None) -> None:
    body = {**_switch_body(), "inGrossAmount": "41.0000"}

    response = TestClient(app).post("/investment-events/unit-actions", json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "changes",
    [{"outUnits": "4.00000000"}, {"inUnits": "-8.00000000"}],
)
def test_switch_leg_directions_are_fixed(owner_override: None, changes: dict[str, object]) -> None:
    body = {**_switch_body(), **changes}

    response = TestClient(app).post("/investment-events/unit-actions", json=body)

    assert response.status_code == 422
    with pytest.raises(ValidationError):
        SwitchActionCreate.model_validate(body)


def test_platform_transfer_requires_two_different_accounts(owner_override: None) -> None:
    body = _platform_transfer_body()
    body["inAccountId"] = body["outAccountId"]

    response = TestClient(app).post("/investment-events/unit-actions", json=body)

    assert response.status_code == 422


def test_platform_transfer_requires_exactly_opposite_units(owner_override: None) -> None:
    body = {**_platform_transfer_body(), "inUnits": "5.00000000"}

    response = TestClient(app).post("/investment-events/unit-actions", json=body)

    assert response.status_code == 422


def test_platform_transfer_has_no_price_or_gross_fields(owner_override: None) -> None:
    """The wire contract offers no field for a platform-transfer price or fee to leak into."""
    body = _platform_transfer_body()

    response = TestClient(app).post(
        "/investment-events/unit-actions", json={**body, "outPrice": "5.000000"}
    )

    assert response.status_code == 422


def test_correction_requires_full_replacement_and_expected_state(owner_override: None) -> None:
    event_id = uuid4()
    body = {**_split_body(), "expectedCorrectionState": "uncorrected"}
    del body["unitRatio"]

    response = TestClient(app).post(
        f"/investment-events/{event_id}/unit-action-corrections", json=body
    )

    assert response.status_code == 422
    assert any(error["type"] == "missing" for error in response.json()["detail"])


def test_correction_reason_is_normalized_and_cannot_be_blank(owner_override: None) -> None:
    response = TestClient(app).post(
        f"/investment-events/{uuid4()}/unit-action-corrections",
        json={**_split_body(), "expectedCorrectionState": "uncorrected", "correctionReason": " "},
    )

    assert response.status_code == 422
    assert any(
        error["type"] == "value_error" and error["msg"] == "Value error, must not be blank"
        for error in response.json()["detail"]
    )


@pytest.mark.parametrize(
    "invalid_date",
    [1767225600, 1767225600.0, "2026-02-01T00:00:00", "2026-2-1", "20260201"],
)
def test_event_dates_require_canonical_calendar_date_strings(
    owner_override: None, invalid_date: object
) -> None:
    response = TestClient(app).post(
        "/investment-events/unit-actions",
        json={**_split_body(), "eventDate": invalid_date},
    )

    assert response.status_code == 422
    with pytest.raises(ValidationError):
        SplitActionCreate.model_validate({**_split_body(), "eventDate": invalid_date})
