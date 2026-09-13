"""Cash lifecycle requests keep financial facts exact before database access."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app


def _body(kind: str = "sale") -> dict[str, object]:
    common: dict[str, object] = {
        "accountId": str(uuid4()),
        "securityId": str(uuid4()),
        "eventDate": "2026-02-01",
        "eventKind": kind,
        "charges": "0.0000",
        "withheldTax": "0.0000",
        "currency": "XAA",
        "idempotencyKey": str(uuid4()),
    }
    shapes: dict[str, dict[str, object]] = {
        "sale": {
            "units": "-1.00000000",
            "price": "5.000000",
            "grossAmount": "5.0000",
            "netAmount": "5.0000",
        },
        "redemption": {
            "units": "-1.00000000",
            "price": "5.000000",
            "grossAmount": "5.0000",
            "netAmount": "5.0000",
        },
        "maturity": {
            "units": "-1.00000000",
            "price": "5.000000",
            "grossAmount": "5.0000",
            "netAmount": "5.0000",
        },
        "cash_distribution": {
            "units": None,
            "price": None,
            "grossAmount": "5.0000",
            "netAmount": "5.0000",
        },
        "interest": {
            "units": None,
            "price": None,
            "grossAmount": "5.0000",
            "netAmount": "5.0000",
        },
        "reinvested_distribution": {
            "units": "1.00000000",
            "price": "5.000000",
            "grossAmount": "5.0000",
            "netAmount": "0.0000",
        },
        "charge": {
            "units": None,
            "price": None,
            "grossAmount": None,
            "charges": "1.0000",
            "netAmount": "-1.0000",
        },
        "withheld_tax": {
            "units": None,
            "price": None,
            "grossAmount": None,
            "withheldTax": "1.0000",
            "netAmount": "-1.0000",
        },
    }
    return {**common, **shapes[kind]}


@pytest.fixture
def owner_override() -> Iterator[None]:
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(), household_id=uuid4(), session_id=uuid4(), email="owner@example.invalid"
    )
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/investment-events/cash-lifecycle", _body()),
        (
            f"/investment-events/{uuid4()}/cash-lifecycle-corrections",
            {
                **_body(),
                "expectedCorrectionState": "uncorrected",
                "correctionReason": "Synthetic exact replacement",
            },
        ),
    ],
)
def test_cash_lifecycle_routes_require_an_owner(path: str, body: dict[str, object]) -> None:
    """Omitting owner authorization from either financial mutation would expose a public write."""
    app.dependency_overrides.clear()

    response = TestClient(app).post(path, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("units", -1.0),
        ("price", 5.0),
        ("grossAmount", 5.0),
        ("charges", 0.0),
        ("withheldTax", 0.0),
        ("netAmount", 5.0),
    ],
)
def test_cash_lifecycle_rejects_json_numbers(
    owner_override: None, field: str, value: float
) -> None:
    """Accepting JSON numbers would lose exactness before Decimal sees the request."""
    response = TestClient(app).post(
        "/investment-events/cash-lifecycle", json={**_body(), field: value}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        ("sale", {"units": "1.00000000"}),
        ("redemption", {"netAmount": "0.0000"}),
        ("maturity", {"price": None}),
        ("cash_distribution", {"units": "1.00000000"}),
        ("interest", {"netAmount": "-1.0000"}),
        ("reinvested_distribution", {"charges": "0.0001"}),
        ("reinvested_distribution", {"netAmount": "1.0000"}),
        ("charge", {"grossAmount": "1.0000"}),
        ("withheld_tax", {"charges": "1.0000"}),
    ],
)
def test_each_cash_lifecycle_shape_refuses_an_incompatible_fact(
    owner_override: None, kind: str, changes: dict[str, object]
) -> None:
    """Weakening the wire matrix could reinterpret cash, units, charges, or tax."""
    response = TestClient(app).post(
        "/investment-events/cash-lifecycle", json={**_body(kind), **changes}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body"]


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("sale", "units", "-1.000000001"),
        ("sale", "price", "5.0000001"),
        ("cash_distribution", "grossAmount", "5.00001"),
        ("charge", "charges", "1e0"),
        ("withheld_tax", "netAmount", "-0"),
    ],
)
def test_cash_lifecycle_fixed_point_scales_are_exact(
    owner_override: None, kind: str, field: str, value: str
) -> None:
    """Permissive decimal syntax could round or reinterpret a financial fact."""
    response = TestClient(app).post(
        "/investment-events/cash-lifecycle", json={**_body(kind), field: value}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_date", [1767225600, 1767225600.0, "2026-02-01T00:00:00", "2026-2-1", "20260201"]
)
def test_cash_lifecycle_dates_are_canonical(owner_override: None, invalid_date: object) -> None:
    """Date coercion must not silently change the evidenced effective date."""
    response = TestClient(app).post(
        "/investment-events/cash-lifecycle",
        json={**_body(), "eventDate": invalid_date},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "eventDate"]


def test_cash_lifecycle_validation_hides_hostile_extra_keys(owner_override: None) -> None:
    """Request-controlled private-looking keys must not be reflected in diagnostics."""
    unsafe_key = "ＦＯＬ１２３４５"

    response = TestClient(app).post(
        "/investment-events/cash-lifecycle", json={**_body(), unsafe_key: "private"}
    )

    assert response.status_code == 422
    assert unsafe_key not in response.text
    assert response.json()["detail"][0]["loc"] == ["body", "field"]


def test_callers_cannot_supply_cash_lifecycle_frozen_fx(owner_override: None) -> None:
    """Caller-provided reporting values would manufacture an external cash-flow translation."""
    fields = {
        "fxRateDate": "2026-02-01",
        "fxRateToBase": "4.0000000000",
        "fxRateToSecondary": "5.0000000000",
        "netAmountBase": "20.0000",
        "netAmountSecondary": "25.0000",
    }

    response = TestClient(app).post("/investment-events/cash-lifecycle", json={**_body(), **fields})

    assert response.status_code == 422
    assert len(response.json()["detail"]) == 5
    assert all(field not in response.text for field in fields)


def test_cash_lifecycle_correction_requires_a_full_replacement(owner_override: None) -> None:
    """A partial correction could silently inherit an obsolete disposal or cash fact."""
    body = {
        **_body(),
        "expectedCorrectionState": "uncorrected",
        "correctionReason": "Synthetic exact replacement",
    }
    del body["grossAmount"]

    response = TestClient(app).post(
        f"/investment-events/{uuid4()}/cash-lifecycle-corrections", json=body
    )

    assert response.status_code == 422
    assert ["body", "grossAmount"] in [error["loc"] for error in response.json()["detail"]]


def test_cash_lifecycle_correction_reason_cannot_be_blank(owner_override: None) -> None:
    """Whitespace cannot satisfy append-only correction provenance."""
    response = TestClient(app).post(
        f"/investment-events/{uuid4()}/cash-lifecycle-corrections",
        json={
            **_body(),
            "expectedCorrectionState": "uncorrected",
            "correctionReason": " \t \n ",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "correctionReason"]
