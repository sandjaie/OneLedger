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
from oneledger.main import app
from oneledger.routes import accounts

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
    "****1234",
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
    None,
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
            cur.execute("DELETE FROM account WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM person WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM household WHERE id = %s", (household_id,))
        admin.commit()
        admin.close()


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
        elif "a.account_number_hash" in sql:
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


def test_a_full_account_number_is_never_accepted() -> None:
    """The never-store list is absolute (security-and-privacy.html section 1). A fifth digit
    makes the value an account number, not a mask; both write models refuse it."""
    full_numbers = [
        "4111111111111111",
        "4111-1111-1111-1111",
        "AE07 0331 2345 6789 0123 456",
        "12345",
        "¹²³⁴⁵",
        "፩፪፫፬፭",
    ]
    for bad in full_numbers:
        with pytest.raises(ValidationError):
            accounts.AccountCreate.model_validate({**CREATE, "accountNumberMasked": bad})
        with pytest.raises(ValidationError):
            accounts.AccountUpdate.model_validate({**UPDATE, "accountNumberMasked": bad})
    assert accounts.AccountCreate.model_validate({**CREATE, "accountNumberMasked": "****1234"})
    assert accounts.AccountUpdate.model_validate(
        {**UPDATE, "accountNumberMasked": "Provider ··1234"}
    )


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


@pytest.mark.parametrize("value", ["", "---", "ABC", "123ABC456", "123_456", "١٢٣٤"])
def test_account_identity_refuses_non_numeric_identifiers(
    value: str,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    with pytest.raises(ValidationError):
        accounts.AccountCreate.model_validate({**CREATE, "accountNumber": value})
    with pytest.raises(ValidationError):
        accounts.AccountUpdate.model_validate({**UPDATE, "accountNumber": value})


def test_account_identity_uses_statement_separator_vocabulary(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    assert accounts._account_number_identity("7234\t–56.00/98 12") == (
        "****9812",
        "75c4febe3d619e74f50bbbed373c532d6663d9908e4f27b55a902cbf2364d46c",
    )


def test_statement_identity_hashes_only_a_full_number(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "test-account-hash-salt")
    full_mask, full_hash = accounts._statement_account_identity("7234-5600-9812")
    assert full_mask == "****9812"
    assert full_hash is not None
    assert accounts._statement_account_identity("XXXX XXXX XXXX 9812") == ("****9812", None)
    assert accounts._statement_account_identity("9812") == ("****9812", None)
    with pytest.raises(ValueError, match="usable account identity"):
        accounts._statement_account_identity("12")


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


@pytest.mark.parametrize("hashed", [False, True])
def test_patch_mask_edits_require_consistent_identity(
    live_accounts: tuple[psycopg.Connection, auth.OwnerSession],
    monkeypatch: MonkeyPatch,
    hashed: bool,
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
            **(
                {"accountNumber": "7234-5600-9812"}
                if hashed
                else {"accountNumberMasked": "****9812"}
            ),
        },
        headers=ORIGIN,
    )
    assert created.status_code == 201
    response = client.patch(
        f"/accounts/{created.json()['id']}",
        json={
            **UPDATE,
            "currency": "USD",
            "country": "XA",
            "accountNumberMasked": "****1234",
        },
        headers=ORIGIN,
    )
    assert response.status_code == (409 if hashed else 200)
    with live_accounts[0].cursor() as cur:
        cur.execute(
            "SELECT account_number_masked FROM account WHERE id = %s", (created.json()["id"],)
        )
        assert cur.fetchone() == (("****9812",) if hashed else ("****1234",))


def test_create_hashes_and_masks_the_account_number_without_exposing_it(
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
    with admin.cursor() as cur:
        cur.execute(
            """SELECT account_number_masked, account_number_hash
                 FROM account WHERE id = %s AND household_id = %s""",
            (account_id, live_accounts[1].household_id),
        )
        stored = cur.fetchone()
    assert stored == (
        "****9812",
        "75c4febe3d619e74f50bbbed373c532d6663d9908e4f27b55a902cbf2364d46c",
    )
    assert response.json()["accountNumberMasked"] == "****9812"
    assert "accountNumber" not in response.json()
    assert full_number not in response.text
    assert full_number not in caplog.text
    assert full_number not in repr(accounts.AccountCreate.model_validate(payload))


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
    assert "idx_account_number_hash" in duplicate.json()["detail"]
    assert full_number not in duplicate.text


def test_account_number_write_refuses_an_empty_hash_salt_without_exposing_the_input(
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

    assert response.status_code == 503
    assert response.json()["detail"] == "ACCOUNT_HASH_SALT is not configured"
    assert not connection.statements
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
    with admin.cursor() as cur:
        cur.execute(
            """SELECT account_number_masked, account_number_hash
                 FROM account WHERE id = %s AND household_id = %s""",
            (account_id, request_owner.household_id),
        )
        stored = cur.fetchone()
    assert stored == (
        "****3333",
        "600dc8cd930957977c7cfc782ce6e1aef6b0cb1fc160ead55d77445cc2de296a",
    )


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
    with admin.cursor() as cur:
        cur.execute(
            """SELECT account_number_masked, account_number_hash
                 FROM account WHERE id = %s AND household_id = %s""",
            (account_id, request_owner.household_id),
        )
        stored = cur.fetchone()
    assert stored == (
        "****9812",
        "75c4febe3d619e74f50bbbed373c532d6663d9908e4f27b55a902cbf2364d46c",
    )


def test_account_hash_resolution_is_parameterized_and_never_guesses() -> None:
    account_hash = "75c4febe3d619e74f50bbbed373c532d6663d9908e4f27b55a902cbf2364d46c"
    matching = AccountsConnection(rows=[FETCHED_ROW])
    with matching.cursor() as cur:
        resolved = accounts.resolve_account_by_hash(
            cast(psycopg.Cursor[Any], cur), account_hash, HOUSEHOLD_ID
        )

    assert resolved is not None and resolved.id == ACCOUNT_ID
    assert "a.account_number_hash = %s" in matching.statements[0]
    assert "a.household_id = %s" in matching.statements[0]
    assert matching.parameters[0] == (account_hash, HOUSEHOLD_ID)

    missing = AccountsConnection()
    with missing.cursor() as cur:
        assert (
            accounts.resolve_account_by_hash(
                cast(psycopg.Cursor[Any], cur), account_hash, HOUSEHOLD_ID
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
