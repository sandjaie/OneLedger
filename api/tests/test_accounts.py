from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pytest import MonkeyPatch

from oneledger import auth
from oneledger.identifiers import CompleteIdentifier
from oneledger.main import app
from oneledger.routes import accounts

from .live_database import clear_synthetic_account_valuation_periods

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
ACCOUNT_ID = UUID("44444444-4444-4444-4444-444444444444")
INSTITUTION_ID = UUID("55555555-5555-5555-5555-555555555555")
CLOSED_ID = UUID("66666666-6666-6666-6666-666666666666")
EMAIL = "owner@example.com"
ORIGIN = {"Origin": "http://localhost:3000"}

CREATE = {
    "name": "Inventory Savings",
    "institutionId": str(INSTITUTION_ID),
    "accountType": "savings",
    "currency": "XIN",
    "country": "XB",
    "openingBalance": "250000.0000",
    "openingBalanceDate": "2024-01-01",
}

UPDATE = {
    "name": "Inventory Savings",
    "accountType": "savings",
    "currency": "XIN",
    "country": "XB",
    "includeInNetWorth": True,
    "isLiquid": True,
    "openingBalance": "-4500.0000",
    "openingBalanceDate": "2024-01-01",
    "acknowledgeOpeningBalance": False,
}

FETCHED_ROW = (
    ACCOUNT_ID,
    "Inventory Savings",
    INSTITUTION_ID,
    "Institution A",
    "savings",
    "asset",
    None,
    "XIN",
    "XB",
    date(2024, 1, 1),
    None,
    True,
    True,
    True,
    Decimal("-4500.0000"),
    date(2024, 1, 1),
    None,
    None,
    Decimal("-4500.0000"),
    None,
    Decimal("20000.0000"),
)

CLOSED_ROW = (
    CLOSED_ID,
    "Old Savings",
    None,
    None,
    "savings",
    "asset",
    None,
    "XIN",
    "XB",
    date(2020, 1, 1),
    date(2026, 6, 30),
    False,
    True,
    True,
    Decimal("100.0000"),
    date(2020, 1, 1),
    None,
    None,
    Decimal("100.0000"),
    None,
    None,
)

TOTAL = ("asset", "XB", "XIN", Decimal("250000.0000"))


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email=EMAIL
    )


@pytest.fixture
def live_accounts(
    monkeypatch: MonkeyPatch,
) -> Iterator[tuple[psycopg.Connection, auth.OwnerSession]]:
    """A real PostgreSQL household used through the real FastAPI account routes."""
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("no database credentials in the environment")
    from oneledger.db import connect as live_connect

    try:
        admin = live_connect()
    except psycopg.OperationalError:
        pytest.skip("database unreachable")

    household_id = uuid4()
    person_id = uuid4()
    request_owner = auth.OwnerSession(
        user_id=uuid4(),
        household_id=household_id,
        session_id=uuid4(),
        email="owner@example.com",
    )
    with admin.cursor() as cur:
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
               VALUES (%s, %s, %s, %s)""",
            (household_id, "Household A", "USD", "EUR"),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
               VALUES (%s, %s, %s, true)""",
            (person_id, household_id, "Owner A"),
        )
    admin.commit()

    monkeypatch.setattr(accounts, "connect", live_connect)
    app.dependency_overrides[auth.require_owner] = lambda: request_owner
    try:
        yield admin, request_owner
    finally:
        app.dependency_overrides.clear()
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute("DELETE FROM account_identifier WHERE household_id = %s", (household_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM person WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM household WHERE id = %s", (household_id,))
        admin.commit()
        admin.close()


def stored_identifiers(
    connection: psycopg.Connection, account_id: UUID
) -> list[tuple[object, ...]]:
    with connection.cursor() as cur:
        cur.execute(
            """SELECT kind, masked_value, full_value, normalized_value, provenance, is_active
                 FROM account_identifier WHERE account_id = %s
                ORDER BY created_at, id""",
            (account_id,),
        )
        return cur.fetchall()


class AccountsConnection:
    def __init__(
        self,
        *,
        fail_institution: bool = False,
        rows: list[tuple[object, ...]] | None = None,
        totals: list[tuple[object, ...]] | None = None,
        institutions: list[tuple[object, ...]] | None = None,
        self_people: list[tuple[object, ...]] | None = None,
        ledger_state: tuple[object, ...] = ("XIN", False, date(2024, 1, 1), False, None, None),
        update_overflows: bool = False,
    ) -> None:
        self.ledger_state = ledger_state
        self.update_overflows = update_overflows
        self.fail_institution = fail_institution
        self.rows = rows or []
        self.totals = totals or []
        self.institutions = institutions
        self.self_people = self_people if self_people is not None else [(USER_ID,)]
        self.statements: list[str] = []
        self.params: list[tuple[object, ...]] = []
        self.parameters = self.params

    def __enter__(self) -> AccountsConnection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> AccountsCursor:
        return AccountsCursor(self)


class AccountsCursor:
    def __init__(self, connection: AccountsConnection) -> None:
        self.connection = connection
        self._one: tuple[object, ...] | None = None
        self._many: list[tuple[object, ...]] = []

    def __enter__(self) -> AccountsCursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.connection.statements.append(sql)
        self.connection.params.append(params or ())
        if "UPDATE account" in sql and self.connection.update_overflows:
            raise psycopg.errors.NumericValueOutOfRange("numeric field overflow")
        if "INSERT INTO institution" in sql:
            self._one = (INSTITUTION_ID, "My Broker", "MB", "XB", "broker")
        elif "SELECT 1 FROM institution" in sql:
            self._one = None if self.connection.fail_institution else (1,)
        elif "FROM person" in sql and "INSERT" not in sql:
            selves = self.connection.self_people
            self._many = list(selves)
            self._one = selves[0] if len(selves) == 1 else None
        elif "INSERT INTO account_identifier" in sql:
            self._one = (uuid4(),)
        elif "FROM account_identifier" in sql and "jsonb_agg" not in sql:
            self._many = []
            self._one = None
        elif "INSERT INTO account" in sql:
            self._one = (ACCOUNT_ID,)
        elif "UPDATE account" in sql:
            self._one = (ACCOUNT_ID,)
        elif "SUM(" in sql:
            self._many = list(self.connection.totals)
        elif "FROM institution" in sql:
            self._many = list(self.connection.institutions or [])
        elif "EXISTS (SELECT FROM transaction" in sql:
            self._one = self.connection.ledger_state
        elif "ai.normalized_value = %s" in sql:
            self._one = self.connection.rows[0] if self.connection.rows else None
        elif "WHERE a.id =" in sql:
            self._one = self.connection.rows[0] if self.connection.rows else None
        else:
            self._many = list(self.connection.rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


def test_money_must_be_an_exact_decimal_string() -> None:
    """A JSON number would be an IEEE-754 float by the time it reached React."""
    for bad in [1000, "1000.00000", "", "1e3", None]:
        with pytest.raises(ValidationError):
            accounts.AccountCreate.model_validate({**CREATE, "openingBalance": bad})
    assert accounts.AccountCreate.model_validate({**CREATE, "openingBalance": "-4500.0000"})


@pytest.mark.parametrize(
    ("account_type", "expected_kind"),
    [("mutual_fund_folio", "folio"), ("brokerage", "brokerage_account")],
)
def test_investment_account_types_store_their_private_identifier_under_the_right_kind(
    account_type: str, expected_kind: str
) -> None:
    """A mistaken mapping would serialize or reveal the wrong private investment kind."""
    body = accounts.AccountCreate.model_validate(
        {**CREATE, "accountType": account_type, "accountNumber": "AB-12345"}
    )

    identifier = accounts._body_identifier(body)

    assert identifier is not None
    assert identifier.kind == expected_kind


def test_a_liability_opening_balance_must_not_be_positive() -> None:
    """A positive liability opening would increase net worth (README invariant 2)."""
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate(
            {**CREATE, "accountType": "credit_card", "openingBalance": "4500.0000"}
        )
    with pytest.raises(ValidationError):
        accounts.AccountUpdate.model_validate(
            {**UPDATE, "accountType": "payable", "openingBalance": "100.0000"}
        )
    assert accounts.AccountCreate.model_validate(
        {
            **CREATE,
            "accountType": "credit_card",
            "openingBalance": "-4500.0000",
            "creditLimit": "20000.0000",
        }
    )
    assert accounts.AccountUpdate.model_validate({**UPDATE, "openingBalance": "0.0000"})


def test_a_credit_card_requires_a_credit_limit() -> None:
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate(
            {**CREATE, "accountType": "credit_card", "openingBalance": "-1.0000"}
        )
    assert accounts.AccountCreate.model_validate(
        {
            **CREATE,
            "accountType": "credit_card",
            "openingBalance": "-1.0000",
            "creditLimit": "20000.0000",
        }
    )


def test_a_credit_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate(
            {
                **CREATE,
                "accountType": "credit_card",
                "openingBalance": "-1.0000",
                "creditLimit": "-20000.0000",
            }
        )
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate(
            {
                **CREATE,
                "accountType": "credit_card",
                "openingBalance": "-1.0000",
                "creditLimit": "0.0000",
            }
        )


def test_a_credit_limit_belongs_only_on_a_credit_card() -> None:
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate({**CREATE, "creditLimit": "20000.0000"})
    with pytest.raises(ValidationError):
        accounts.AccountUpdate.model_validate({**UPDATE, "creditLimit": "1.0000"})


def test_the_retired_compatibility_mask_field_is_rejected() -> None:
    legacy_values = [
        "4111111111111111",
        "4111-1111-1111-1111",
        "AE07 0331 2345 6789 0123 456",
        "12345",
        "¹²³⁴⁵",
        "፩፪፫፬፭",
        "****1234",
    ]
    for bad in legacy_values:
        with pytest.raises(ValidationError):
            accounts.AccountCreate.model_validate({**CREATE, "accountNumberMasked": bad})
        with pytest.raises(ValidationError):
            accounts.AccountUpdate.model_validate({**UPDATE, "accountNumberMasked": bad})


@pytest.mark.parametrize("method,payload", [("post", CREATE), ("patch", UPDATE)])
@pytest.mark.parametrize(
    "bad",
    [
        {"accountNumber": 723456009812},
        {"accountNumberMasked": "723456009812"},
        {"accountNumber": "723456009812", "accountType": "credit_card", "creditLimit": None},
    ],
)
def test_account_validation_never_echoes_input(
    method: str,
    payload: dict[str, object],
    bad: dict[str, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).request(
            method,
            "/accounts" if method == "post" else f"/accounts/{ACCOUNT_ID}",
            json={**payload, **bad},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "723456009812" not in response.text
    assert "723456009812" not in caplog.text
    assert all(set(error) <= {"type", "loc", "msg"} for error in response.json()["detail"])


@pytest.mark.parametrize("value", ["", "---", "ABC", "123_456", "١٢٣٤"])
def test_account_identity_refuses_malformed_identifiers(value: str) -> None:
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate({**CREATE, "accountNumber": value})
    with pytest.raises(ValidationError):
        accounts.AccountUpdate.model_validate({**UPDATE, "accountNumber": value})


def test_account_identity_uses_the_shared_value_boundary_without_a_key() -> None:
    body = accounts.AccountCreate.model_validate({**CREATE, "accountNumber": "AB72 –56.00/98 12"})
    identifier = accounts._body_identifier(body)

    assert isinstance(identifier, CompleteIdentifier)
    assert identifier.masked_value == "****9812"
    assert identifier.normalized_value == "AB7256009812"


def test_account_type_selects_the_identifier_kind() -> None:
    assert accounts._identifier_kind("credit_card") == "card"
    assert accounts._identifier_kind("savings") == "bank_account"


@pytest.mark.parametrize("method", ["post", "patch"])
def test_account_database_validation_cannot_echo_identifier(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
    method: str,
) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    response = TestClient(app).request(
        method,
        "/accounts" if method == "post" else f"/accounts/{ACCOUNT_ID}",
        json={
            **(CREATE if method == "post" else UPDATE),
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "999988887777",
            "accountType": "999988887777",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 422
    assert "999988887777" not in response.text


def test_create_stores_and_masks_the_account_number_without_exposing_it(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    full_number = "7234-5600-9812"
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    admin, _ = live_accounts
    payload = {
        **CREATE,
        "institutionId": None,
        "currency": "USD",
        "country": "XA",
        "accountNumber": full_number,
    }

    response = TestClient(app).post("/accounts", json=payload, headers=ORIGIN)

    assert response.status_code == 201
    account_id = UUID(response.json()["id"])
    stored = stored_identifiers(admin, account_id)
    assert stored == [
        ("bank_account", "****9812", full_number, "723456009812", "owner_entered", True)
    ]
    assert "accountNumberMasked" not in response.json()
    assert response.json()["identifiers"][0]["masked"] == "****9812"
    assert response.json()["identifiers"][0]["canReveal"] is True
    assert "accountNumber" not in response.json()
    assert full_number not in response.text
    assert full_number not in caplog.text
    assert full_number not in repr(accounts.AccountCreate.model_validate(payload))


def test_create_card_retains_the_complete_value_and_needs_no_key(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    full_number = "7234-5600-9812"
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)
    response = TestClient(app).post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "accountType": "credit_card",
            "currency": "USD",
            "country": "XA",
            "accountNumber": full_number,
            "openingBalance": "-1.0000",
            "creditLimit": "100.0000",
        },
        headers=ORIGIN,
    )

    assert response.status_code == 201
    assert stored_identifiers(live_accounts[0], UUID(response.json()["id"])) == [
        ("card", "****9812", full_number, "723456009812", "owner_entered", True)
    ]
    assert full_number not in response.text
    assert full_number not in caplog.text
    assert full_number not in repr(
        accounts.AccountCreate.model_validate(
            {
                **CREATE,
                "accountType": "credit_card",
                "accountNumber": full_number,
                "openingBalance": "-1.0000",
                "creditLimit": "100.0000",
            }
        )
    )


def test_complete_identifier_is_masked_on_reads_and_revealed_only_by_its_route(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
) -> None:
    source = "AB٧٢ 5600 9812"
    normalized = "AB7256009812"
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)
    client = TestClient(app)

    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": source,
        },
        headers=ORIGIN,
    )

    assert created.status_code == 201
    ordinary = created.json()
    assert "accountNumberMasked" not in ordinary
    assert ordinary["identifiers"][0]["masked"] == "****9812"
    assert ordinary["identifiers"][0]["canReveal"] is True
    assert source not in created.text
    assert normalized not in created.text

    listed = client.get("/accounts")
    assert listed.status_code == 200
    assert source not in listed.text
    assert normalized not in listed.text

    revealed = client.post(
        f"/accounts/{ordinary['id']}/identifiers/{ordinary['identifiers'][0]['id']}/reveal",
        headers=ORIGIN,
    )
    assert revealed.status_code == 200
    assert revealed.json() == {
        "status": "revealed",
        "value": source,
        "kind": "bank_account",
    }
    assert revealed.headers["cache-control"] == "no-store"
    assert revealed.headers["pragma"] == "no-cache"


def test_account_list_returns_multiple_typed_identifiers_without_complete_values(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
) -> None:
    admin, owner = live_accounts
    source_iban = "XZ00 TEST 0000 9812"
    client = TestClient(app)
    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "7234-5600-9812",
        },
        headers=ORIGIN,
    ).json()
    with admin.cursor() as cur:
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix,
                    full_value, normalized_value, provenance)
                 VALUES (%s, %s, 'iban', '****9812', '9812', %s,
                         'XZ00TEST00009812', 'owner_entered')""",
            (owner.household_id, created["id"], source_iban),
        )
    admin.commit()

    response = client.get("/accounts")

    assert response.status_code == 200
    account = next(row for row in response.json()["accounts"] if row["id"] == created["id"])
    safe_identifiers = [
        (item["kind"], item["masked"], item["canReveal"]) for item in account["identifiers"]
    ]
    assert safe_identifiers == [
        ("bank_account", "****9812", True),
        ("iban", "****9812", True),
    ]
    assert source_iban not in response.text
    assert "XZ00TEST00009812" not in response.text


def test_mask_only_identifier_reveal_is_unavailable_and_owner_scoped(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
) -> None:
    admin, request_owner = live_accounts
    client = TestClient(app)
    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
        },
        headers=ORIGIN,
    ).json()
    with admin.cursor() as cur:
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix, provenance)
                 VALUES (%s, %s, 'bank_account', '****9812', '9812', 'legacy_migration')
                 RETURNING id""",
            (request_owner.household_id, created["id"]),
        )
        identifier_id = cur.fetchone()[0]  # type: ignore[index]
    admin.commit()

    unavailable = client.post(
        f"/accounts/{created['id']}/identifiers/{identifier_id}/reveal", headers=ORIGIN
    )
    assert unavailable.json() == {"status": "unavailable", "reason": "source_mask_only"}
    assert unavailable.headers["cache-control"] == "no-store"

    missing = client.post(f"/accounts/{uuid4()}/identifiers/{identifier_id}/reveal", headers=ORIGIN)
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"

    app.dependency_overrides.clear()
    try:
        unauthenticated = client.post(
            f"/accounts/{created['id']}/identifiers/{identifier_id}/reveal", headers=ORIGIN
        )
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live_accounts[1]
    assert unauthenticated.status_code == 401


def test_complete_identifier_reveal_hides_another_household(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin, _owner = live_accounts
    foreign_household_id = uuid4()
    foreign_person_id = uuid4()
    foreign_account_id = uuid4()
    foreign_identifier_id = uuid4()
    source = "ZX90 0000 1234 5678"
    normalized = "ZX90000012345678"
    with admin.cursor() as cur:
        cur.execute(
            """INSERT INTO household (id, name, base_currency, secondary_currency)
                 VALUES (%s, 'Foreign Household', 'USD', 'EUR')""",
            (foreign_household_id,),
        )
        cur.execute(
            """INSERT INTO person (id, household_id, display_name, is_self)
                 VALUES (%s, %s, 'Foreign Owner', true)""",
            (foreign_person_id, foreign_household_id),
        )
        cur.execute(
            """INSERT INTO account
                   (id, household_id, owner_person_id, name, account_type, currency, country,
                    opening_balance_date)
                 VALUES (%s, %s, %s, 'Foreign Account', 'savings', 'USD', 'XA',
                         DATE '2026-01-01')""",
            (foreign_account_id, foreign_household_id, foreign_person_id),
        )
        cur.execute(
            """INSERT INTO account_identifier
                   (id, household_id, account_id, kind, masked_value, visible_suffix,
                    full_value, normalized_value, provenance)
                 VALUES (%s, %s, %s, 'bank_account', '****5678', '5678', %s, %s,
                         'owner_entered')""",
            (
                foreign_identifier_id,
                foreign_household_id,
                foreign_account_id,
                source,
                normalized,
            ),
        )
    admin.commit()
    try:
        response = TestClient(app).post(
            f"/accounts/{foreign_account_id}/identifiers/{foreign_identifier_id}/reveal",
            headers=ORIGIN,
        )
        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert source not in response.text
        assert normalized not in response.text
        assert source not in caplog.text
        assert normalized not in caplog.text
    finally:
        with admin.cursor() as cur:
            cur.execute("DELETE FROM account_identifier WHERE id = %s", (foreign_identifier_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE id = %s", (foreign_account_id,))
            cur.execute("DELETE FROM person WHERE id = %s", (foreign_person_id,))
            cur.execute("DELETE FROM household WHERE id = %s", (foreign_household_id,))
        admin.commit()


def test_reveal_openapi_union_has_required_discriminators() -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "status" in schemas["RevealedAccountIdentifier"]["required"]
    assert {"status", "reason"} <= set(schemas["UnavailableAccountIdentifier"]["required"])


def test_changing_account_type_preserves_its_separate_bank_identifier(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    client = TestClient(app)
    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "7234-5600-9812",
        },
        headers=ORIGIN,
    )
    account_id = UUID(created.json()["id"])

    response = client.patch(
        f"/accounts/{account_id}",
        json={
            **UPDATE,
            "accountType": "credit_card",
            "currency": "USD",
            "country": "XA",
            "openingBalance": "-1.0000",
            "creditLimit": "100.0000",
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200
    assert stored_identifiers(live_accounts[0], account_id) == [
        (
            "bank_account",
            "****9812",
            "7234-5600-9812",
            "723456009812",
            "owner_entered",
            True,
        )
    ]


def test_patch_card_replaces_the_active_card_identifier_and_needs_no_key(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    full_number = "1111 2222 3333"
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)
    client = TestClient(app)
    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "accountType": "credit_card",
            "currency": "USD",
            "country": "XA",
            "openingBalance": "-1.0000",
            "creditLimit": "100.0000",
        },
        headers=ORIGIN,
    )
    account_id = UUID(created.json()["id"])
    with live_accounts[0].cursor() as cur:
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix, provenance)
                 VALUES (%s, %s, 'card', '****9812', '9812', 'legacy_migration')""",
            (live_accounts[1].household_id, account_id),
        )
    live_accounts[0].commit()

    response = client.patch(
        f"/accounts/{account_id}",
        json={
            **UPDATE,
            "accountType": "credit_card",
            "currency": "USD",
            "country": "XA",
            "accountNumber": full_number,
            "openingBalance": "-1.0000",
            "creditLimit": "100.0000",
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200
    stored = stored_identifiers(live_accounts[0], account_id)
    assert [(row[0], row[1], row[2], row[5]) for row in stored] == [
        ("card", "****9812", None, False),
        ("card", "****3333", full_number, True),
    ]
    assert full_number not in response.text
    assert full_number not in caplog.text


def test_same_account_number_is_rejected_twice_within_one_household(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
) -> None:
    full_number = "7234-5600-9812"
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    payload = {
        **CREATE,
        "institutionId": None,
        "currency": "USD",
        "country": "XA",
        "accountNumber": full_number,
    }

    first = TestClient(app).post("/accounts", json=payload, headers=ORIGIN)
    duplicate = TestClient(app).post(
        "/accounts", json={**payload, "name": "Account B"}, headers=ORIGIN
    )

    assert first.status_code == 201
    assert duplicate.status_code == 422
    assert "idx_account_identifier_active_normalized_value" in duplicate.json()["detail"]
    assert full_number not in duplicate.text


def test_account_number_write_has_no_hash_salt_gate_or_response_leak(
    monkeypatch: MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    full_number = "7234-5600-9812"
    connection = AccountsConnection(rows=[FETCHED_ROW])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "  ")
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/accounts", json={**CREATE, "accountNumber": full_number}, headers=ORIGIN
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert any("INSERT INTO account_identifier" in sql for sql in connection.statements)
    assert full_number not in response.text
    assert full_number not in caplog.text


def test_patch_with_an_account_number_replaces_the_stored_identity(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    admin, request_owner = live_accounts
    created = TestClient(app).post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "7234-5600-9812",
        },
        headers=ORIGIN,
    )
    account_id = UUID(created.json()["id"])

    response = TestClient(app).patch(
        f"/accounts/{account_id}",
        json={
            **UPDATE,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "1111 2222 3333",
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200
    stored = stored_identifiers(admin, account_id)
    assert [(row[1], row[2], row[3], row[5]) for row in stored] == [
        ("****9812", "7234-5600-9812", "723456009812", False),
        ("****3333", "1111 2222 3333", "111122223333", True),
    ]


def test_patch_omitting_account_number_preserves_the_stored_identity(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    admin, request_owner = live_accounts
    created = TestClient(app).post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "7234-5600-9812",
        },
        headers=ORIGIN,
    )
    account_id = UUID(created.json()["id"])

    response = TestClient(app).patch(
        f"/accounts/{account_id}",
        json={**UPDATE, "currency": "USD", "country": "XA"},
        headers=ORIGIN,
    )

    assert response.status_code == 200
    stored = stored_identifiers(admin, account_id)
    assert [(row[1], row[2], row[3], row[5]) for row in stored] == [
        ("****9812", "7234-5600-9812", "723456009812", True)
    ]


def test_patch_null_account_number_preserves_the_stored_identity(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
) -> None:
    admin, _request_owner = live_accounts
    client = TestClient(app)
    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "7234-5600-9812",
        },
        headers=ORIGIN,
    )
    account_id = UUID(created.json()["id"])

    response = client.patch(
        f"/accounts/{account_id}",
        json={
            **UPDATE,
            "currency": "USD",
            "country": "XA",
            "accountNumber": None,
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200
    assert stored_identifiers(admin, account_id) == [
        ("bank_account", "****9812", "7234-5600-9812", "723456009812", "owner_entered", True)
    ]


def test_patch_omitting_identity_preserves_a_v28_legacy_mask_and_fingerprint(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
) -> None:
    admin, _owner = live_accounts
    client = TestClient(app)
    created = client.post(
        "/accounts",
        json={
            **CREATE,
            "institutionId": None,
            "currency": "USD",
            "country": "XA",
            "accountNumber": "7234-5600-9812",
        },
        headers=ORIGIN,
    ).json()
    identifier_id = created["identifiers"][0]["id"]
    fingerprint = bytes.fromhex("ab" * 32)
    with admin.cursor() as cur:
        cur.execute(
            """UPDATE account_identifier
                  SET masked_value = 'XXXX9812', visible_suffix = NULL,
                      full_value = NULL, normalized_value = NULL,
                      legacy_fingerprint = %s,
                      legacy_fingerprint_scheme = 'legacy_sha256_v1',
                      provenance = 'legacy_migration'
                WHERE id = %s""",
            (fingerprint, identifier_id),
        )
    admin.commit()
    response = client.patch(
        f"/accounts/{created['id']}",
        json={
            **UPDATE,
            "currency": "USD",
            "country": "XA",
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200
    with admin.cursor() as cur:
        cur.execute(
            """SELECT id, masked_value, visible_suffix, legacy_fingerprint, is_active
                 FROM account_identifier WHERE account_id = %s ORDER BY created_at, id""",
            (created["id"],),
        )
        assert cur.fetchall() == [(UUID(identifier_id), "XXXX9812", None, fingerprint, True)]


def test_account_identifier_resolution_is_parameterized_and_never_guesses() -> None:
    normalized = "723456009812"
    matching = AccountsConnection(rows=[FETCHED_ROW])
    with matching.cursor() as cur:
        resolved = accounts.resolve_account_by_identifier(
            cast(psycopg.Cursor[Any], cur), "bank_account", normalized, HOUSEHOLD_ID
        )

    assert resolved is not None and resolved.id == ACCOUNT_ID
    assert "ai.normalized_value = %s" in matching.statements[0]
    assert "a.household_id = %s" in matching.statements[0]
    assert matching.parameters[0] == (HOUSEHOLD_ID, "bank_account", normalized)

    missing = AccountsConnection()
    with missing.cursor() as cur:
        assert (
            accounts.resolve_account_by_identifier(
                cast(psycopg.Cursor[Any], cur), "bank_account", normalized, HOUSEHOLD_ID
            )
            is None
        )


def test_creating_an_account_refuses_ambiguous_self_person(monkeypatch: MonkeyPatch) -> None:
    """V2 allows more than one is_self row; INSERT … SELECT would create one account per row."""
    connection = AccountsConnection(self_people=[(USER_ID,), (ACCOUNT_ID,)])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post("/accounts", json=CREATE, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "self" in response.json()["detail"].lower()
    assert not any("INSERT INTO account" in statement for statement in connection.statements)


def test_creating_an_account_rejects_another_households_institution(
    monkeypatch: MonkeyPatch,
) -> None:
    """UUID secrecy is not the household boundary; the insert must scope the institution."""
    connection = AccountsConnection(fail_institution=True)
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post("/accounts", json=CREATE)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "institution" in response.json()["detail"].lower()


def test_creating_an_institution_scopes_it_to_the_owner(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(institutions=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        body = (
            TestClient(app)
            .post(
                "/institutions",
                json={
                    "name": "My Broker",
                    "shortName": "MB",
                    "country": "XB",
                    "institutionType": "broker",
                },
            )
            .json()
        )
    finally:
        app.dependency_overrides.clear()
    assert body["name"] == "My Broker"
    assert "INSERT INTO institution" in connection.statements[0]
    assert connection.params[0][0] == HOUSEHOLD_ID


def test_account_update_carries_the_core_editable_fields() -> None:
    """Spec C2: type, currency, country, opening anchor and credit limit are editable."""
    for field in (
        "accountType",
        "currency",
        "country",
        "openingBalance",
        "openingBalanceDate",
        "creditLimit",
    ):
        assert field in accounts.AccountUpdate.model_fields


def test_creating_an_account_never_accepts_a_class_from_the_client() -> None:
    """account_class is derived by the V3 trigger; a client value would be a second source."""
    assert "accountClass" not in accounts.AccountCreate.model_fields


def test_the_row_mapper_emits_money_exactly_as_stored() -> None:
    row = accounts.account_row(FETCHED_ROW)
    assert row.openingBalance == "-4500.0000"
    assert row.currentBalance == "-4500.0000"
    assert row.creditLimit == "20000.0000"


def test_the_inventory_includes_closed_accounts_and_database_computed_totals(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(rows=[FETCHED_ROW, CLOSED_ROW], totals=[TOTAL])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        body = TestClient(app).get("/accounts").json()
    finally:
        app.dependency_overrides.clear()

    assert [a["isActive"] for a in body["accounts"]] == [True, False]
    # Aggregation happens in Postgres; the service shapes the response and sums nothing.
    assert body["totals"] == [
        {"accountClass": "asset", "country": "XB", "currency": "XIN", "total": "250000.0000"}
    ]
    assert "SUM(" in connection.statements[-1] and "GROUP BY" in connection.statements[-1]


def test_the_inventory_join_does_not_name_another_households_institution(
    monkeypatch: MonkeyPatch,
) -> None:
    """The service bypasses RLS; a foreign key on institution.id is not a household boundary."""
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).get("/accounts")
    finally:
        app.dependency_overrides.clear()
    assert "i.household_id IS NULL OR i.household_id = a.household_id" in connection.statements[0]


def test_every_statement_is_scoped_to_the_resolved_household(monkeypatch: MonkeyPatch) -> None:
    """The service connects privileged and bypasses RLS, so the predicate is the boundary."""
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).get("/accounts")
    finally:
        app.dependency_overrides.clear()
    for statement, params in zip(connection.statements, connection.parameters, strict=True):
        assert "household_id = %s" in statement
        assert HOUSEHOLD_ID in params


def test_closing_an_account_derives_is_active_from_the_closing_date(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(rows=[CLOSED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}", json={**UPDATE, "closedOn": "2026-06-30"}, headers=ORIGIN
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["isActive"] is False
    assert "is_active = (%s::date IS NULL)" in next(
        s for s in connection.statements if "UPDATE account" in s
    )


def test_a_non_anchor_patch_preserves_the_observed_current_balance(
    monkeypatch: MonkeyPatch,
) -> None:
    """Acknowledge, close and metadata edits must not invent a position from the opening
    figure. Only an opening-anchor change rewrites the materialised current_balance."""
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "acknowledgeOpeningBalance": True},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    statement = next(s for s in connection.statements if "UPDATE account" in s)
    rewrite = statement[
        statement.index("current_balance =") : statement.index("ELSE current_balance")
    ]
    assert "opening_balance IS DISTINCT FROM" in rewrite
    assert "opening_balance_date IS DISTINCT FROM" in rewrite
    assert "ELSE current_balance_as_of" in statement


def test_acknowledging_an_opening_balance_is_recorded_and_reversible(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "acknowledgeOpeningBalance": True},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    update_at = next(i for i, s in enumerate(connection.statements) if "UPDATE account" in s)
    assert "opening_balance_confirmed_at" in connection.statements[update_at]
    assert True in connection.parameters[update_at]


def test_changing_the_opening_anchor_clears_the_acknowledgment(
    monkeypatch: MonkeyPatch,
) -> None:
    """A confirmation timestamp must never predate the figure it confirms (Invariant I7). The
    statement nulls the timestamp when either anchor field changes, before the acknowledge flag
    is consulted, so a full-replacement body still carrying acknowledgeOpeningBalance=True
    cannot re-date the old acknowledgment onto a replacement figure."""
    connection = AccountsConnection(rows=[FETCHED_ROW], totals=[])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "openingBalance": "999.0000", "acknowledgeOpeningBalance": True},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    statement = next(s for s in connection.statements if "UPDATE account" in s)
    anchor_guard = statement.index("opening_balance IS DISTINCT FROM")
    assert anchor_guard < statement.index("COALESCE(opening_balance_confirmed_at")
    assert "opening_balance_date IS DISTINCT FROM" in statement


def test_accounts_are_never_deleted() -> None:
    assert not any(
        "DELETE" in (route.methods or ()) and route.path.startswith("/accounts")
        for route in app.routes
        if isinstance(route, APIRoute)
    )


def test_an_anchor_edit_recomputes_the_balance_from_the_ledger(
    monkeypatch: MonkeyPatch,
) -> None:
    """opening + the sum of the account's posting rows — Postgres arithmetic, split-neutral,
    so an anchor correction after M1b's ledger exists cannot erase transaction history."""
    connection = AccountsConnection(rows=[FETCHED_ROW])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "openingBalance": "999.0000"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    update = next(s for s in connection.statements if "UPDATE account" in s)
    assert "COALESCE(sum(t.amount), 0)" in update
    assert "parent_transaction_id IS NULL" in update


def test_a_currency_change_is_refused_once_ledger_rows_exist(
    monkeypatch: MonkeyPatch,
) -> None:
    """The rows' native amounts are immutable facts in their denomination; relabelling the
    account would mix denominations in one balance."""
    connection = AccountsConnection(
        rows=[FETCHED_ROW], ledger_state=("XIN", True, date(2024, 1, 1), False, None, None)
    )
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "currency": "XAE"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert not any("UPDATE account" in s for s in connection.statements)


def test_an_anchor_edit_keeps_the_as_of_at_the_latest_posting(
    monkeypatch: MonkeyPatch,
) -> None:
    """The stored figure carries the date it is actually true for (I7): with postings in the
    balance, the as-of is the later of the opening date and the latest posting."""
    connection = AccountsConnection(rows=[FETCHED_ROW])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "openingBalance": "999.0000"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    update = next(s for s in connection.statements if "UPDATE account" in s)
    assert "GREATEST" in update
    assert "max(t.booked_date)" in update


def test_an_account_edit_serializes_on_the_household_row(monkeypatch: MonkeyPatch) -> None:
    """The same lock order as POST /transactions (B3.5), so the currency guard cannot race an
    in-flight ledger insert."""
    connection = AccountsConnection(rows=[FETCHED_ROW])
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        TestClient(app).patch(f"/accounts/{ACCOUNT_ID}", json=UPDATE, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    first = connection.statements[0]
    assert "FOR UPDATE" in first and "FROM household" in first


def test_reanchoring_past_existing_postings_is_refused(monkeypatch: MonkeyPatch) -> None:
    """A posting before the new anchor would be counted twice: once inside the new opening
    balance by definition, once as a ledger row. Nothing is reinterpreted silently."""
    connection = AccountsConnection(
        rows=[FETCHED_ROW], ledger_state=("XIN", True, date(2024, 1, 1), True, None, None)
    )
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "openingBalanceDate": "2024-02-15"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
    assert "double-count" in response.json()["detail"]
    assert not any("UPDATE account" in s for s in connection.statements)


def test_lifecycle_edits_cannot_strand_existing_postings(monkeypatch: MonkeyPatch) -> None:
    """POST refuses out-of-lifetime postings; an account edit must not create them after
    the fact."""
    connection = AccountsConnection(
        rows=[FETCHED_ROW],
        ledger_state=("XIN", True, date(2024, 1, 1), False, date(2024, 2, 1), date(2024, 6, 1)),
    )
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        opened_late = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "openedOn": "2024-03-01"},
            headers=ORIGIN,
        )
        closed_early = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "closedOn": "2024-05-01"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert opened_late.status_code == 409
    assert "predates" in opened_late.json()["detail"]
    assert closed_early.status_code == 409
    assert "postdates" in closed_early.json()["detail"]


def test_a_recomputed_balance_that_overflows_is_a_422(monkeypatch: MonkeyPatch) -> None:
    """opening + SUM can exceed NUMERIC(20,4) even when both inputs fit; the caller gets a
    validation error, not a 500."""
    connection = AccountsConnection(rows=[FETCHED_ROW], update_overflows=True)
    monkeypatch.setattr(accounts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).patch(
            f"/accounts/{ACCOUNT_ID}",
            json={**UPDATE, "openingBalance": "999.0000"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
