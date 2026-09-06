from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from oneledger import auth
from oneledger.main import app
from oneledger.routes import categories

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
ROOT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CHILD_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ORIGIN = {"Origin": "http://localhost:3000"}

ROOT = (ROOT_ID, None, "Food & Dining", "food-dining", "expense", "want", 140, True, True)
CHILD = (CHILD_ID, ROOT_ID, "Coffee", "food-dining-coffee", "expense", "want", 20, True, True)


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email="o@example.com"
    )


class CategoriesConnection:
    def __init__(
        self,
        *,
        rows: list[tuple[object, ...]] | None = None,
        existing: tuple[object, ...] | None = None,
        created: tuple[object, ...] | None = None,
    ) -> None:
        self.rows = rows or []
        self.existing = existing
        self.created = created
        self.statements: list[str] = []
        self.params: list[tuple[object, ...]] = []

    def __enter__(self) -> CategoriesConnection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> CategoriesCursor:
        return CategoriesCursor(self)


class CategoriesCursor:
    def __init__(self, connection: CategoriesConnection) -> None:
        self.connection = connection
        self._one: tuple[object, ...] | None = None
        self._many: list[tuple[object, ...]] = []

    def __enter__(self) -> CategoriesCursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.connection.statements.append(sql)
        self.connection.params.append(params or ())
        if "SELECT household_id IS NULL" in sql:
            self._one = self.connection.existing
        elif "category_override" in sql and "INSERT" in sql:
            self._one = None
        elif "WHERE c.id =" in sql:
            self._one = self.connection.created
        elif "INSERT INTO category" in sql or "UPDATE category" in sql:
            self._one = self.connection.created
        else:
            self._many = list(self.connection.rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


def _client(connection: CategoriesConnection, monkeypatch: MonkeyPatch) -> TestClient:
    monkeypatch.setattr(categories, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    return TestClient(app)


def _teardown() -> None:
    app.dependency_overrides.clear()


def test_the_listing_is_scoped_and_ordered(monkeypatch: MonkeyPatch) -> None:
    connection = CategoriesConnection(rows=[ROOT, CHILD])
    try:
        body = _client(connection, monkeypatch).get("/categories").json()
    finally:
        _teardown()
    assert [row["slug"] for row in body] == ["food-dining", "food-dining-coffee"]
    listing = connection.statements[0]
    assert "c.household_id IS NULL OR c.household_id = %s" in listing
    assert "COALESCE(o.bucket" in listing, "the household's overrides win on read"
    assert HOUSEHOLD_ID in connection.params[0]


def test_a_child_inherits_from_its_parent(monkeypatch: MonkeyPatch) -> None:
    connection = CategoriesConnection(created=CHILD)
    try:
        response = _client(connection, monkeypatch).post(
            "/categories",
            json={"name": "Coffee", "parentId": str(ROOT_ID)},
            headers=ORIGIN,
        )
    finally:
        _teardown()
    assert response.status_code == 201
    insert = next(s for s in connection.statements if "INSERT INTO category" in s)
    assert "p.kind" in insert and "p.bucket" in insert, "kind and bucket come from the parent"


def test_a_root_needs_a_kind_and_a_bucket(monkeypatch: MonkeyPatch) -> None:
    connection = CategoriesConnection()
    try:
        response = _client(connection, monkeypatch).post(
            "/categories", json={"name": "Sidegigs"}, headers=ORIGIN
        )
    finally:
        _teardown()
    assert response.status_code == 422


def test_a_seeded_category_keeps_its_name_but_not_its_bucket(monkeypatch: MonkeyPatch) -> None:
    """The bucket is an editable default (ADR-0008); the seeded name is not."""
    connection = CategoriesConnection(existing=(True, "Food & Dining"), created=ROOT)
    try:
        renamed = _client(connection, monkeypatch).patch(
            f"/categories/{ROOT_ID}",
            json={"name": "Chow", "bucket": "need", "isActive": True},
            headers=ORIGIN,
        )
    finally:
        _teardown()
    assert renamed.status_code == 422

    connection = CategoriesConnection(existing=(True, "Food & Dining"), created=ROOT)
    try:
        rebucketed = _client(connection, monkeypatch).patch(
            f"/categories/{ROOT_ID}",
            json={"name": "Food & Dining", "bucket": "need", "isActive": True},
            headers=ORIGIN,
        )
    finally:
        _teardown()
    assert rebucketed.status_code == 200
    override = next(s for s in connection.statements if "category_override" in s and "INSERT" in s)
    assert "ON CONFLICT (household_id, category_id)" in override
    assert not any(
        "UPDATE category" in s and "category_override" not in s for s in connection.statements
    ), "the seed row itself is never rewritten"
    merged = next(s for s in connection.statements if "COALESCE(o.bucket" in s)
    assert merged
