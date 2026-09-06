from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from oneledger import auth
from oneledger.main import app
from oneledger.routes import currencies, household

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
EMAIL = "owner@example.com"

BODY = {
    "name": "New name",
    "ownerDisplayName": "New owner",
    "ownerTaxResidency": "XA",
    "baseCurrency": "XIN",
    "secondaryCurrency": "XAE",
}
# The CSRF middleware only inspects cookie-authenticated mutations; the header is sent so the
# test exercises the same shape the browser will.
ORIGIN = {"Origin": "http://localhost:3000"}


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email=EMAIL
    )


class Connection:
    def __init__(self) -> None:
        self.scoped: list[str] = []
        self.called: list[tuple[object, ...]] = []
        self.cursor_params: tuple[UUID, UUID] | None = None

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        if "set_config" in sql and params:
            self.scoped.append(str(params[0]))
        elif params is not None:
            self.called.append(params)

    def cursor(self) -> Cursor:
        return Cursor(self)


class Cursor:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, _: str, params: tuple[UUID, UUID]) -> None:
        self.connection.cursor_params = params

    def fetchone(
        self,
    ) -> tuple[UUID, str, str, None, str, str, bool, str, int, str, str, int, str]:
        return (
            HOUSEHOLD_ID,
            "Fixture household",
            "Fixture owner",
            None,
            "XIN",
            "XAE",
            False,
            "\u20b9",
            2,
            "indian",
            "AED",
            2,
            "international",
        )


class CurrencyCursor:
    def __enter__(self) -> CurrencyCursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, _: str) -> None:
        pass

    def fetchall(self) -> list[tuple[str, str, str, int, str]]:
        return [
            ("XAE", "Synthetic A", "A", 2, "international"),
            ("XIN", "Synthetic I", "I", 2, "indian"),
            ("XZZ", "Synthetic Z", "Z", 3, "international"),
        ]


class CurrencyConnection:
    def __enter__(self) -> CurrencyConnection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> CurrencyCursor:
        return CurrencyCursor()


def test_household_returns_the_authenticated_owners_config(monkeypatch: MonkeyPatch) -> None:
    connection = Connection()
    monkeypatch.setattr(household, "connect", lambda: connection, raising=False)

    result = household.fetch_household(owner())
    assert result is not None
    assert result.model_dump(mode="json") == {
        "id": str(HOUSEHOLD_ID),
        "name": "Fixture household",
        "ownerDisplayName": "Fixture owner",
        "ownerTaxResidency": None,
        "baseCurrency": "XIN",
        "secondaryCurrency": "XAE",
        "reportingPairLocked": False,
        "currencies": [
            {"code": "XIN", "symbol": "\u20b9", "decimals": 2, "numberFormat": "indian"},
            {"code": "XAE", "symbol": "AED", "decimals": 2, "numberFormat": "international"},
        ],
    }
    assert connection.cursor_params == (USER_ID, HOUSEHOLD_ID)


def test_household_scopes_the_connection_before_reading(monkeypatch: MonkeyPatch) -> None:
    connection = Connection()
    monkeypatch.setattr(household, "connect", lambda: connection, raising=False)
    result = household.fetch_household(owner())
    assert result is not None
    assert result.name == "Fixture household"
    assert result.ownerDisplayName == "Fixture owner"
    assert result.reportingPairLocked is False
    assert connection.scoped == [str(USER_ID)]


def test_updating_the_configuration_passes_every_field_to_the_owner_guarded_function(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = Connection()
    monkeypatch.setattr(household, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch("/household", json=BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert connection.scoped == [str(USER_ID)]
    assert connection.called[-1] == (
        "New name",
        "New owner",
        "XA",
        "XIN",
        "XAE",
    )


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (psycopg.errors.ObjectInUse, 409),
        (psycopg.errors.InvalidParameterValue, 422),
        (psycopg.errors.ForeignKeyViolation, 422),
        (psycopg.errors.InsufficientPrivilege, 403),
    ],
)
def test_reporting_pair_failures_stay_distinguishable(
    monkeypatch: MonkeyPatch, error: type[Exception], status: int
) -> None:
    """A locked pair is not an invalid one, and neither is a missing authorization scope."""

    def refuse() -> object:
        raise error("refused")

    monkeypatch.setattr(household, "connect", refuse, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch("/household", json=BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status
    if status == 409:
        assert "backfill" in response.json()["detail"]


def test_currencies_come_from_the_table_not_a_hardcoded_list(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(currencies, "connect", lambda: CurrencyConnection(), raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/currencies")
    finally:
        app.dependency_overrides.clear()
    assert [row["code"] for row in response.json()] == ["XAE", "XIN", "XZZ"]


def test_household_endpoint_returns_the_authenticated_owners_config(
    monkeypatch: MonkeyPatch,
) -> None:
    app.dependency_overrides[auth.require_owner] = owner
    monkeypatch.setattr(
        household,
        "fetch_household",
        lambda _: {
            "id": str(HOUSEHOLD_ID),
            "name": "Fixture household",
            "ownerDisplayName": "Fixture owner",
            "ownerTaxResidency": None,
            "baseCurrency": "XIN",
            "secondaryCurrency": "XAE",
            "reportingPairLocked": False,
            "currencies": [
                {"code": "XIN", "symbol": "\u20b9", "decimals": 2, "numberFormat": "indian"},
                {"code": "XAE", "symbol": "AED", "decimals": 2, "numberFormat": "international"},
            ],
        },
        raising=False,
    )
    try:
        response = TestClient(app).get("/household")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["baseCurrency"] == "XIN"
    assert response.json()["currencies"][1]["numberFormat"] == "international"


def test_household_openapi_schema_has_structured_client_fields() -> None:
    schema = app.openapi()
    response_schema = schema["paths"]["/household"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/HouseholdResponse"}

    household_schema = schema["components"]["schemas"]["HouseholdResponse"]
    assert set(household_schema["required"]) == {
        "id",
        "name",
        "ownerDisplayName",
        "ownerTaxResidency",
        "baseCurrency",
        "secondaryCurrency",
        "reportingPairLocked",
        "currencies",
    }
    assert household_schema["properties"]["id"]["format"] == "uuid"
    assert household_schema["properties"]["baseCurrency"]["type"] == "string"
    assert household_schema["properties"]["secondaryCurrency"]["type"] == "string"
    assert household_schema["properties"]["currencies"]["items"] == {
        "$ref": "#/components/schemas/HouseholdCurrency"
    }

    currency_schema = schema["components"]["schemas"]["HouseholdCurrency"]
    assert set(currency_schema["required"]) == {"code", "symbol", "decimals", "numberFormat"}
    assert currency_schema["properties"]["code"]["type"] == "string"
    assert currency_schema["properties"]["symbol"]["type"] == "string"
    assert currency_schema["properties"]["decimals"]["type"] == "integer"
    assert currency_schema["properties"]["numberFormat"]["enum"] == ["international", "indian"]


def test_household_rejects_an_owner_without_a_matching_household(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(household, "fetch_household", lambda _: None, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/household")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_household_does_not_accept_an_id_from_the_browser() -> None:
    response = TestClient(app).get("/household", params={"id": str(HOUSEHOLD_ID)})
    assert response.status_code == 401
