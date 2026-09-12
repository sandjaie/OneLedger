"""Owner-managed private merchant knowledge against a disposable migrated database."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database
from .test_ingest_route_live import ORIGIN


@dataclass
class Fixture:
    conn: psycopg.Connection[Any]
    household_id: UUID
    user_id: UUID
    account_id: UUID
    category_id: UUID
    currency: str

    def owner(self) -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=self.user_id,
            household_id=self.household_id,
            session_id=UUID(int=7),
            email="knowledge-owner@example.invalid",
        )


@pytest.fixture
def live() -> Iterator[Fixture]:
    conn = connect_to_test_database()
    with conn.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        base, secondary = cur.fetchone()  # type: ignore[misc]
        cur.execute(
            "INSERT INTO household (name, base_currency, secondary_currency)"
            " VALUES (%s, %s, %s) RETURNING id",
            (f"knowledge household {uuid4()}", base, secondary),
        )
        household_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO person (household_id, display_name, is_self)"
            " VALUES (%s, 'synthetic owner', true) RETURNING id",
            (household_id,),
        )
        person_id = cur.fetchone()[0]  # type: ignore[index]
        user_id = uuid4()
        cur.execute(
            "INSERT INTO app_user (id, household_id, person_id, email, role)"
            " VALUES (%s, %s, %s, %s, 'owner')",
            (user_id, household_id, person_id, f"knowledge-{user_id}@example.invalid"),
        )
        cur.execute(
            """INSERT INTO account
                 (household_id, owner_person_id, name, account_type, currency, country,
                  opening_balance, opening_balance_date)
               VALUES (%s, %s, %s, 'savings', %s, 'XZ', 0, DATE '2026-01-01') RETURNING id""",
            (household_id, person_id, f"synthetic account {uuid4()}", base),
        )
        account_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO category (household_id, name, slug, kind, bucket)"
            " VALUES (%s, 'Synthetic groceries', %s, 'expense', 'need') RETURNING id",
            (household_id, f"synthetic-groceries-{uuid4()}"),
        )
        category_id = cur.fetchone()[0]  # type: ignore[index]
    conn.commit()
    fixture = Fixture(conn, household_id, user_id, account_id, category_id, str(base))
    app.dependency_overrides[auth.require_owner] = fixture.owner
    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transaction WHERE household_id = %s", (household_id,))
        conn.commit()
        conn.close()


def body(live: Fixture, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "displayName": "Synthetic Market",
        "kind": "merchant",
        "merchantName": "Synthetic Market",
        "pattern": "SYNTHETIC MARKET",
        "patternKind": "contains",
        "role": "purchase",
        "priority": 50,
        "sourceKind": "owner",
        "attribution": "household owner",
        "evidenceNote": "confirmed synthetic merchant",
        "matcherVersion": "private-v1",
        "direction": "debit",
        "categoryId": str(live.category_id),
        "txnType": "expense",
        "paymentMethod": "card",
    }
    value.update(changes)
    return value


def test_create_list_revise_and_deactivate_preserves_history(live: Fixture) -> None:
    client = TestClient(app)
    created = client.post("/merchant-knowledge", json=body(live), headers=ORIGIN)
    assert created.status_code == 201, created.text
    first = created.json()
    assert (first["version"], first["active"], first["kind"], first["role"]) == (
        1,
        True,
        "merchant",
        "purchase",
    )
    assert client.get("/merchant-knowledge").json() == [first]

    revised = client.patch(
        f"/merchant-knowledge/{first['aliasId']}",
        json=body(live, pattern="SYNTHETIC MARKET EXPRESS", patternKind="exact"),
        headers=ORIGIN,
    )
    assert revised.status_code == 200, revised.text
    second = revised.json()
    assert second["version"] == 2
    assert second["aliasId"] != first["aliasId"]
    assert second["counterpartyId"] == first["counterpartyId"]
    assert (
        client.delete(f"/merchant-knowledge/{second['aliasId']}", headers=ORIGIN).status_code == 204
    )
    assert client.get("/merchant-knowledge").json() == []

    history = client.get("/merchant-knowledge", params={"includeInactive": True}).json()
    assert [(row["version"], row["active"]) for row in history] == [(2, False), (1, False)]


def test_cross_household_references_are_refused(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute("SELECT secondary_currency FROM household WHERE id = %s", (live.household_id,))
        secondary = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO household (name, base_currency, secondary_currency)"
            " VALUES ('other synthetic household', %s, %s) RETURNING id",
            (live.currency, secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO category (household_id, name, slug, kind, bucket)"
            " VALUES (%s, 'Other category', %s, 'expense', 'want') RETURNING id",
            (other, f"other-{uuid4()}"),
        )
        foreign_category = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()

    response = TestClient(app).post(
        "/merchant-knowledge",
        json=body(live, categoryId=str(foreign_category)),
        headers=ORIGIN,
    )
    assert response.status_code == 422
    assert "not available" in response.json()["detail"]


@pytest.mark.parametrize(
    "field",
    ["displayName", "merchantName", "pattern", "attribution", "evidenceNote", "matcherVersion"],
)
def test_private_identifiers_are_rejected_without_echoing_them(live: Fixture, field: str) -> None:
    private_value = "MERCHANT ٤١١١‑١١١١‑١١١١‑١١١١"
    response = TestClient(app, raise_server_exceptions=False).post(
        "/merchant-knowledge",
        json=body(live, **{field: private_value}),
        headers=ORIGIN,
    )
    assert response.status_code == 422
    assert private_value not in response.text


@pytest.mark.parametrize(
    ("field", "private_value"),
    [("evidenceNote", "OTP 123456"), ("attribution", "PIN 1234"), ("pattern", "CVV 123")],
)
def test_contextual_secrets_are_rejected_without_echoing_them(
    live: Fixture, field: str, private_value: str
) -> None:
    response = TestClient(app).post(
        "/merchant-knowledge",
        json=body(live, **{field: private_value}),
        headers=ORIGIN,
    )
    assert response.status_code == 422
    assert private_value not in response.text


def test_revise_rejects_private_evidence_and_url_credentials(live: Fixture) -> None:
    created = TestClient(app).post("/merchant-knowledge", json=body(live), headers=ORIGIN).json()
    private_value = "OTP 123456"
    private = TestClient(app).patch(
        f"/merchant-knowledge/{created['aliasId']}",
        json=body(live, evidenceNote=private_value),
        headers=ORIGIN,
    )
    credentials = TestClient(app).patch(
        f"/merchant-knowledge/{created['aliasId']}",
        json=body(
            live,
            sourceKind="public_source",
            sourceUrl="https://user:secret@public.invalid/evidence",
        ),
        headers=ORIGIN,
    )
    private_url = TestClient(app).patch(
        f"/merchant-knowledge/{created['aliasId']}",
        json=body(
            live,
            sourceKind="public_source",
            sourceUrl="https://public.invalid/evidence?password=hunter2",
        ),
        headers=ORIGIN,
    )
    assert (private.status_code, credentials.status_code, private_url.status_code) == (
        422,
        422,
        422,
    )
    assert private_value not in private.text


def test_public_source_is_recorded_without_fetching_it_and_resolves_consistently(
    live: Fixture,
) -> None:
    created = TestClient(app).post(
        "/merchant-knowledge",
        json=body(
            live,
            sourceKind="public_source",
            sourceUrl="https://unreachable.invalid/evidence",
            attribution="synthetic public directory",
            txnType="investment_fee",
        ),
        headers=ORIGIN,
    )
    assert created.status_code == 201, created.text

    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction
                 (household_id, account_id, booked_date, description_raw, description_clean,
                  amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                  amount_base, amount_secondary, txn_type, source, dedupe_hash,
                  classified_by, classification_confidence)
               VALUES (%s, %s, DATE '2026-06-01', 'SYNTHETIC MARKET BRANCH',
                       'SYNTHETIC MARKET BRANCH', -12, %s, DATE '2026-06-01', 1, 1, -12, -12,
                       'expense', 'manual', %s, 'unclassified', 0) RETURNING id""",
            (live.household_id, live.account_id, live.currency, uuid4().hex + uuid4().hex),
        )
        transaction_id = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()

    preview = TestClient(app).post("/classify", params={"preview": True}, headers=ORIGIN)
    assert preview.status_code == 200, preview.text
    assert preview.json()["changed"] == 1
    applied = TestClient(app).post("/classify", headers=ORIGIN)
    assert applied.status_code == 200, applied.text
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT category_id, txn_type::text, payment_method::text, classified_by::text"
            " FROM transaction WHERE id = %s",
            (transaction_id,),
        )
        stored = cur.fetchone()
    assert stored == (live.category_id, "investment_fee", "card", "merchant_alias")

    assert (
        TestClient(app)
        .delete(f"/merchant-knowledge/{created.json()['aliasId']}", headers=ORIGIN)
        .status_code
        == 204
    )
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction
                 (household_id, account_id, booked_date, description_raw, description_clean,
                  amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                  amount_base, amount_secondary, txn_type, source, dedupe_hash,
                  classified_by, classification_confidence)
               VALUES (%s, %s, DATE '2026-06-02', 'SYNTHETIC MARKET BRANCH',
                       'SYNTHETIC MARKET BRANCH', -13, %s, DATE '2026-06-02', 1, 1, -13, -13,
                       'expense', 'manual', %s, 'unclassified', 0)""",
            (live.household_id, live.account_id, live.currency, uuid4().hex + uuid4().hex),
        )
    live.conn.commit()
    after_deactivation = TestClient(app).post("/classify", params={"preview": True}, headers=ORIGIN)
    assert after_deactivation.json()["changed"] == 0
