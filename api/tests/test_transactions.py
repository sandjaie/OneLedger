from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pytest import MonkeyPatch

from oneledger import auth
from oneledger.main import app
from oneledger.routes import transactions

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
ACCOUNT_ID = UUID("44444444-4444-4444-4444-444444444444")
CATEGORY_ID = UUID("77777777-7777-7777-7777-777777777777")
TXN_ID = UUID("88888888-8888-8888-8888-888888888888")
EMAIL = "owner@example.com"
ORIGIN = {"Origin": "http://localhost:3000"}

CREATE = {
    "accountId": str(ACCOUNT_ID),
    "bookedDate": "2024-02-02",
    "description": "COFFEE",
    "amount": "-310.5000",
    "txnType": "expense",
}

# The row a ledger SELECT returns, in COLUMNS order.
FETCHED_ROW = (
    TXN_ID,
    ACCOUNT_ID,
    "Ledger Card",
    date(2024, 2, 2),
    "COFFEE",
    None,
    Decimal("-310.5000"),
    "XAE",
    Decimal("-6210.0000"),
    Decimal("-310.5000"),
    date(2024, 2, 2),
    False,
    Decimal("20.0000000000"),
    Decimal("1.0000000000"),
    "expense",
    CATEGORY_ID,
    "Food & Dining",
    None,
    None,
    None,
    False,
    "user_manual",
    "manual",
    None,
)


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email=EMAIL
    )


class LedgerConnection:
    """The fake-connection pattern from test_accounts.py: statements are recorded, canned rows
    come back, and no database is needed."""

    def __init__(
        self,
        *,
        account: tuple[object, ...] | None = ("XAE",),
        pair: tuple[object, ...] | None = ("XIN", "XAE"),
        fx: dict[tuple[str, str], tuple[object, ...]] | None = None,
        rows: list[tuple[object, ...]] | None = None,
        total: int = 0,
        category_ok: tuple[object, ...] | None = (1,),
    ) -> None:
        self.category_ok = category_ok
        self.account = account
        self.pair = pair
        # (base, quote) -> (rate, is_carried_forward, carried_from)
        self.fx = (
            fx
            if fx is not None
            else {
                ("XAE", "XIN"): (Decimal("22.5000000000"), False, None),
            }
        )
        self.rows = rows or []
        self.total = total
        self.statements: list[str] = []
        self.params: list[tuple[object, ...]] = []

    def __enter__(self) -> LedgerConnection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> LedgerCursor:
        return LedgerCursor(self)


class LedgerCursor:
    def __init__(self, connection: LedgerConnection) -> None:
        self.connection = connection
        self._one: tuple[object, ...] | None = None
        self._many: list[tuple[object, ...]] = []

    def __enter__(self) -> LedgerCursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.connection.statements.append(sql)
        self.connection.params.append(params or ())
        if "FOR UPDATE" in sql:
            self._one = self.connection.pair
        elif "FROM account" in sql:
            self._one = self.connection.account
        elif "FROM category WHERE id" in sql:
            self._one = self.connection.category_ok
        elif "FROM fx_rate" in sql:
            base, quote = (params or ())[1], (params or ())[2]
            self._one = self.connection.fx.get((str(base), str(quote)))
        elif "count(*)" in sql and "FROM transaction" in sql and "description_raw =" in sql:
            self._one = (0,)  # occurrence index for the dedupe hash
        elif "count(*)" in sql:
            self._one = (self.connection.total,)
        elif "INSERT INTO transaction" in sql:
            self._one = (TXN_ID,)
        elif "UPDATE transaction" in sql:
            self._one = (TXN_ID,)
        elif "WHERE t.id =" in sql:
            self._one = self.connection.rows[0] if self.connection.rows else None
        else:
            self._many = list(self.connection.rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


def _client(connection: LedgerConnection, monkeypatch: MonkeyPatch) -> TestClient:
    monkeypatch.setattr(transactions, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    return TestClient(app)


def _teardown() -> None:
    app.dependency_overrides.clear()


def test_money_must_be_an_exact_decimal_string() -> None:
    """A JSON number would be an IEEE-754 float by the time it reached React."""
    for bad in [1000, "1000.00000", "", "1e3", None]:
        with pytest.raises(ValidationError):
            transactions.TransactionCreate.model_validate({**CREATE, "amount": bad})
    assert transactions.TransactionCreate.model_validate(CREATE)


def test_a_rateless_date_is_refused_with_a_labelled_error(monkeypatch: MonkeyPatch) -> None:
    """Never a fallback to 1.0 or today's rate: the save fails and says why (I5/I7)."""
    connection = LedgerConnection(fx={})
    try:
        response = _client(connection, monkeypatch).post(
            "/transactions", json=CREATE, headers=ORIGIN
        )
    finally:
        _teardown()
    assert response.status_code == 422
    assert "FX rate" in response.json()["detail"]
    assert not any("INSERT INTO transaction" in s for s in connection.statements)


def test_the_household_lock_is_taken_before_the_pair_is_read(monkeypatch: MonkeyPatch) -> None:
    """B3.5: every frozen write serializes on the household row, before reading the pair."""
    connection = LedgerConnection(rows=[FETCHED_ROW])
    try:
        _client(connection, monkeypatch).post("/transactions", json=CREATE, headers=ORIGIN)
    finally:
        _teardown()
    first = connection.statements[0]
    assert "FOR UPDATE" in first and "household" in first
    assert HOUSEHOLD_ID in connection.params[0]


def test_a_carried_rate_is_labelled_with_its_real_date(monkeypatch: MonkeyPatch) -> None:
    """A Sunday save stores the Friday rate as the Friday's, flagged carried (I7)."""
    connection = LedgerConnection(
        rows=[FETCHED_ROW],
        fx={("XAE", "XIN"): (Decimal("22.5000000000"), True, date(2024, 1, 31))},
    )
    try:
        _client(connection, monkeypatch).post("/transactions", json=CREATE, headers=ORIGIN)
    finally:
        _teardown()
    insert = next(i for i, s in enumerate(connection.statements) if "INSERT INTO transaction" in s)
    assert True in connection.params[insert]
    assert date(2024, 1, 31) in connection.params[insert]


def test_frozen_amounts_are_rounded_half_up_at_storage(monkeypatch: MonkeyPatch) -> None:
    """multi-currency: half-up at the point of storage; never round intermediates."""
    connection = LedgerConnection(
        rows=[FETCHED_ROW],
        account=("XAE",),
        pair=("XIN", "XAE"),
        fx={("XAE", "XIN"): (Decimal("2"), False, None)},
    )
    body = {**CREATE, "amount": "0.0025"}
    try:
        _client(connection, monkeypatch).post("/transactions", json=body, headers=ORIGIN)
    finally:
        _teardown()
    insert = next(i for i, s in enumerate(connection.statements) if "INSERT INTO transaction" in s)
    # base = 0.0025 * 2 = 0.0050; the account currency IS the secondary, so that leg is 1:1.
    assert Decimal("0.0050") in connection.params[insert]
    assert Decimal("0.0025") in connection.params[insert]


def test_the_insert_never_computes_in_the_database(monkeypatch: MonkeyPatch) -> None:
    """Frozen values arrive as parameters: Decimal in Python, never SQL arithmetic on money."""
    connection = LedgerConnection(rows=[FETCHED_ROW])
    try:
        _client(connection, monkeypatch).post("/transactions", json=CREATE, headers=ORIGIN)
    finally:
        _teardown()
    insert = next(s for s in connection.statements if "INSERT INTO transaction" in s)
    assert "amount * " not in insert and "rate *" not in insert


def test_category_edit_stamps_user_manual_and_never_touches_amounts(
    monkeypatch: MonkeyPatch,
) -> None:
    connection = LedgerConnection(rows=[FETCHED_ROW])
    try:
        response = _client(connection, monkeypatch).patch(
            f"/transactions/{TXN_ID}",
            json={"categoryId": str(CATEGORY_ID), "notes": None},
            headers=ORIGIN,
        )
    finally:
        _teardown()
    assert response.status_code == 200
    update = next(s for s in connection.statements if "UPDATE transaction" in s)
    assert "user_manual" in update
    assert "amount" not in update.split("SET", 1)[1].split("WHERE", 1)[0]


def test_the_ledger_lists_transfer_legs_and_reads_the_base_table(
    monkeypatch: MonkeyPatch,
) -> None:
    """IA section 4: the ledger shows transfer legs with their peer link. Reports read the
    view; this endpoint is the one deliberate non-report reader of the base table."""
    connection = LedgerConnection(rows=[FETCHED_ROW], total=1)
    try:
        body = _client(connection, monkeypatch).get("/transactions").json()
    finally:
        _teardown()
    assert body["total"] == 1
    assert body["transactions"][0]["amount"] == "-310.5000"
    listing = next(s for s in connection.statements if "FROM transaction" in s)
    assert "v_transaction_reportable" not in listing


def test_every_statement_is_scoped_to_the_resolved_household(monkeypatch: MonkeyPatch) -> None:
    connection = LedgerConnection(rows=[FETCHED_ROW], total=1)
    try:
        _client(connection, monkeypatch).get("/transactions")
    finally:
        _teardown()
    for statement, params in zip(connection.statements, connection.params, strict=True):
        assert "household_id = %s" in statement
        assert HOUSEHOLD_ID in params


def test_filters_round_trip_as_parameters(monkeypatch: MonkeyPatch) -> None:
    connection = LedgerConnection(rows=[FETCHED_ROW], total=1)
    try:
        _client(connection, monkeypatch).get(
            "/transactions",
            params={
                "accountId": str(ACCOUNT_ID),
                "categoryId": str(CATEGORY_ID),
                "txnType": "expense",
                "dateFrom": "2024-02-01",
                "dateTo": "2024-02-29",
                "q": "coffee",
            },
        )
    finally:
        _teardown()
    listing = next(s for s in connection.statements if "ORDER BY" in s)
    idx = connection.statements.index(listing)
    for value in (ACCOUNT_ID, CATEGORY_ID, "expense", date(2024, 2, 1), date(2024, 2, 29)):
        assert value in connection.params[idx]
    assert not any("coffee" in s for s in connection.statements), "search terms are parameters"


def test_a_foreign_household_category_is_refused(monkeypatch: MonkeyPatch) -> None:
    """The category foreign key is not tenant-aware; the route predicate is the boundary."""
    connection = LedgerConnection(rows=[FETCHED_ROW], category_ok=None)
    try:
        create = _client(connection, monkeypatch).post(
            "/transactions",
            json={**CREATE, "categoryId": str(CATEGORY_ID)},
            headers=ORIGIN,
        )
    finally:
        _teardown()
    assert create.status_code == 422
    assert not any("INSERT INTO transaction" in s for s in connection.statements)

    connection = LedgerConnection(rows=[FETCHED_ROW], category_ok=None)
    try:
        update = _client(connection, monkeypatch).patch(
            f"/transactions/{TXN_ID}",
            json={"categoryId": str(CATEGORY_ID), "notes": None},
            headers=ORIGIN,
        )
    finally:
        _teardown()
    assert update.status_code == 422
    assert not any("UPDATE transaction" in s for s in connection.statements)


def test_manual_entry_accepts_unlinked_types_only() -> None:
    """opening_balance would move the balance while hiding from every report; linked types
    arrive with their owning features."""
    for bad in ["opening_balance", "transfer_out", "investment_buy", "adjustment"]:
        with pytest.raises(ValidationError):
            transactions.TransactionCreate.model_validate({**CREATE, "txnType": bad})
    assert transactions.TransactionCreate.model_validate({**CREATE, "txnType": "refund"})


def test_sort_is_a_bounded_contract(monkeypatch: MonkeyPatch) -> None:
    connection = LedgerConnection(rows=[FETCHED_ROW], total=1)
    try:
        client = _client(connection, monkeypatch)
        ok = client.get("/transactions", params={"sort": "amount", "direction": "asc"})
        bad = client.get("/transactions", params={"sort": "booked_date; DROP TABLE"})
    finally:
        _teardown()
    assert ok.status_code == 200
    listing = next(s for s in connection.statements if "ORDER BY" in s)
    assert "t.amount ASC" in listing
    assert "t.id DESC" in listing, "a unique tie-breaker keeps offset pages disjoint"
    assert bad.status_code == 422
