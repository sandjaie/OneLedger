from uuid import UUID

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from oneledger import auth
from oneledger.main import app
from oneledger.routes import household

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
EMAIL = "owner@example.com"


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email=EMAIL
    )


def test_household_returns_the_authenticated_owners_config(monkeypatch: MonkeyPatch) -> None:
    seen: list[object] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def execute(self, _: str, params: tuple[UUID, UUID]) -> None:
            seen.extend(params)

        def fetchone(self) -> tuple[UUID, str, str, str, int, str, str, int, str]:
            return (
                HOUSEHOLD_ID,
                "INR",
                "AED",
                "\u20b9",
                2,
                "indian",
                "AED",
                2,
                "international",
            )

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(household, "connect", lambda: Connection(), raising=False)

    assert household.fetch_household(owner()) == {
        "id": str(HOUSEHOLD_ID),
        "baseCurrency": "INR",
        "secondaryCurrency": "AED",
        "currencies": [
            {"code": "INR", "symbol": "\u20b9", "decimals": 2, "numberFormat": "indian"},
            {"code": "AED", "symbol": "AED", "decimals": 2, "numberFormat": "international"},
        ],
    }
    assert seen == [USER_ID, HOUSEHOLD_ID]


def test_household_endpoint_returns_the_authenticated_owners_config(
    monkeypatch: MonkeyPatch,
) -> None:
    app.dependency_overrides[auth.require_owner] = owner
    monkeypatch.setattr(
        household,
        "fetch_household",
        lambda _: {
            "id": str(HOUSEHOLD_ID),
            "baseCurrency": "INR",
            "secondaryCurrency": "AED",
            "currencies": [
                {"code": "INR", "symbol": "\u20b9", "decimals": 2, "numberFormat": "indian"},
                {"code": "AED", "symbol": "AED", "decimals": 2, "numberFormat": "international"},
            ],
        },
        raising=False,
    )
    try:
        response = TestClient(app).get("/household")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["baseCurrency"] == "INR"
    assert response.json()["currencies"][1]["numberFormat"] == "international"


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
