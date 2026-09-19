"""Owner-scoped workspace search over safe, already-visible fields."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database

LiveFixture = tuple[psycopg.Connection, auth.OwnerSession]


@pytest.fixture(scope="module")
def live() -> Iterator[LiveFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id = uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "search-owner@example.invalid")

    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = currencies
        for household, name in (
            (household_id, "Synthetic search household"),
            (other_household_id, "Synthetic foreign search household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "Synthetic search owner"),
            (other_person_id, other_household_id, "Synthetic foreign owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        cur.execute(
            """INSERT INTO app_user (id, household_id, person_id, email, role)
               VALUES (%s, %s, %s, %s, 'owner')""",
            (user_id, household_id, person_id, owner.email),
        )
        cur.execute(
            """INSERT INTO institution (household_id, name, country, institution_type)
               VALUES (%s, 'Atlas Institution', 'XZ', 'bank') RETURNING id""",
            (household_id,),
        )
        institution_id = cur.fetchone()[0]  # type: ignore[index]

        account_ids: list[UUID] = []
        for name in ("Atlas Reserve", "Family Atlas Account", "Megaatlas Account"):
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, institution_id, name, account_type,
                       currency, country, opening_balance_date)
                   VALUES (%s, %s, %s, %s, 'savings', %s, 'XZ', DATE '2026-01-01')
                   RETURNING id""",
                (household_id, person_id, institution_id, name, base),
            )
            account_ids.append(UUID(str(cur.fetchone()[0])))  # type: ignore[index]
        cur.execute(
            """INSERT INTO account_identifier (
                   household_id, account_id, kind, masked_value, visible_suffix, provenance)
               VALUES (%s, %s, 'bank_account', '****5920', '5920', 'owner_entered')""",
            (household_id, account_ids[0]),
        )
        cur.execute(
            """INSERT INTO merchant (household_id, canonical_name, display_name, country)
               VALUES (%s, 'Atlas Merchant', 'Atlas Merchant', 'XZ') RETURNING id""",
            (household_id,),
        )
        merchant_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, description_clean,
                   amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                   amount_base, amount_secondary, txn_type, merchant_id, source, dedupe_hash)
               VALUES (%s, %s, DATE '2026-02-03', 'ATLAS PAYMENT 1234567890',
                       'Atlas payment 1234567890', -1, %s, DATE '2026-02-03', 1, 1,
                       -1, -1, 'expense', %s, 'manual', %s)""",
            (household_id, account_ids[0], base, merchant_id, uuid4().hex),
        )
        cur.execute(
            """INSERT INTO document (
                   household_id, account_id, document_type, original_filename, storage_bucket,
                   storage_path, mime_type, byte_size, sha256, period_end)
               VALUES (%s, %s, 'bank_statement', 'fixture.pdf', 'fixture', 'fixture/path',
                       'application/pdf', 1, %s, DATE '2026-02-28')""",
            (household_id, account_ids[0], uuid4().hex + uuid4().hex),
        )
        cur.execute(
            """INSERT INTO security (
                   household_id, security_type, name, currency, country, asset_class)
               VALUES (%s, 'mutual_fund', 'Atlas Security', %s, 'XZ', 'equity') RETURNING id""",
            (household_id, base),
        )
        security_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'on', true)")
        cur.execute(
            """INSERT INTO holding (
                   household_id, account_id, security_id, current_units, opened_on)
                   VALUES (%s, %s, %s, 1, DATE '2026-01-02')""",
            (household_id, account_ids[0], security_id),
        )
        cur.execute("SELECT set_config('oneledger.investment_event_append', 'off', true)")
        cur.execute(
            """INSERT INTO goal (
                   household_id, idempotency_key, name, target_amount, target_currency,
                   target_date, priority)
               VALUES (%s, %s, 'Atlas Goal', 1, %s, DATE '2027-01-01', 1)""",
            (household_id, uuid4(), base),
        )
        cur.execute(
            """INSERT INTO commitment (
                   household_id, idempotency_key, account_id, label, amount, currency,
                   cadence, first_date)
               VALUES (%s, %s, %s, 'Atlas Commitment', -1, %s, 'once', DATE '2026-12-01')""",
            (household_id, uuid4(), account_ids[0], base),
        )
        cur.execute(
            """INSERT INTO register_record (
                   household_id, idempotency_key, kind, title, key_date)
               VALUES (%s, %s, 'administration', 'Atlas Register', DATE '2026-12-15')""",
            (household_id, uuid4()),
        )

        cur.execute(
            """INSERT INTO account (
                   household_id, owner_person_id, name, account_type, currency, country,
                   opening_balance_date)
               VALUES (%s, %s, 'Atlas Foreign Account', 'savings', %s, 'XZ', DATE '2026-01-01')""",
            (other_household_id, other_person_id, base),
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield connection, owner
    finally:
        app.dependency_overrides.clear()
        connection.close()


def _results(query: str, limit: int = 10) -> list[dict[str, object]]:
    response = TestClient(app).get("/search", params={"q": query, "limit": limit})
    assert response.status_code == 200, response.text
    return response.json()["results"]


def test_owner_search_returns_all_kinds_in_fixed_order_and_excludes_other_households(
    live: LiveFixture,
) -> None:
    results = _results("Atlas")

    assert [row["kind"] for row in results] == [
        "account",
        "account",
        "account",
        "transaction",
        "document",
        "holding",
        "goal",
        "commitment",
        "register",
    ]
    assert all("Foreign" not in str(row) for row in results)
    assert {row["kind"] for row in results} == {
        "account",
        "transaction",
        "document",
        "holding",
        "goal",
        "commitment",
        "register",
    }


def test_per_kind_limit_ranking_masked_lookup_and_transaction_redaction(
    live: LiveFixture,
) -> None:
    ranked = [row for row in _results("Atlas", limit=2) if row["kind"] == "account"]
    assert [row["primaryLabel"] for row in ranked] == ["Atlas Reserve", "Family Atlas Account"]

    masked = _results("5920")
    assert len(masked) == 1
    assert masked[0]["secondaryLabel"] == "Atlas Institution · ****5920"

    [transaction] = [row for row in _results("Atlas") if row["kind"] == "transaction"]
    assert transaction["primaryLabel"] == "Atlas payment [redacted]"
    assert "1234567890" not in str(transaction)


def test_search_is_read_only_and_never_serializes_private_document_or_identifier_fields(
    live: LiveFixture,
) -> None:
    connection, owner = live
    with connection.cursor() as cur:
        cur.execute(
            """SELECT (SELECT count(*) FROM account WHERE household_id = %s),
                      (SELECT count(*) FROM transaction WHERE household_id = %s),
                      (SELECT count(*) FROM document WHERE household_id = %s),
                      (SELECT count(*) FROM holding WHERE household_id = %s),
                      (SELECT count(*) FROM goal WHERE household_id = %s),
                      (SELECT count(*) FROM commitment WHERE household_id = %s),
                      (SELECT count(*) FROM register_record WHERE household_id = %s)""",
            (owner.household_id,) * 7,
        )
        before = cur.fetchone()

    response = TestClient(app).get("/search", params={"q": "Atlas", "limit": 10})
    assert response.status_code == 200, response.text
    payload = response.json()

    with connection.cursor() as cur:
        cur.execute(
            """SELECT (SELECT count(*) FROM account WHERE household_id = %s),
                      (SELECT count(*) FROM transaction WHERE household_id = %s),
                      (SELECT count(*) FROM document WHERE household_id = %s),
                      (SELECT count(*) FROM holding WHERE household_id = %s),
                      (SELECT count(*) FROM goal WHERE household_id = %s),
                      (SELECT count(*) FROM commitment WHERE household_id = %s),
                      (SELECT count(*) FROM register_record WHERE household_id = %s)""",
            (owner.household_id,) * 7,
        )
        after = cur.fetchone()
    assert after == before
    forbidden = {
        "filename",
        "originalFilename",
        "storagePath",
        "storageBucket",
        "content",
        "fullValue",
        "normalizedValue",
        "accountNumber",
    }
    assert forbidden.isdisjoint(str(payload))


def test_search_refuses_short_queries_and_out_of_range_limits(live: LiveFixture) -> None:
    client = TestClient(app)
    assert client.get("/search", params={"q": " x "}).status_code == 422
    assert client.get("/search", params={"q": "Atlas", "limit": 0}).status_code == 422
    assert client.get("/search", params={"q": "Atlas", "limit": 11}).status_code == 422


def test_search_refuses_a_member_session(monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides.clear()
    monkeypatch.setattr(auth, "_lookup_active_session", lambda *_: None)
    now = datetime.now(UTC).replace(microsecond=0)
    token = jwt.encode(
        {
            "sub": str(uuid4()),
            "email": "member@example.invalid",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=12)).timestamp()),
            "jti": str(uuid4()),
            "iss": auth.SESSION_ISSUER,
            "aud": auth.SESSION_AUDIENCE,
        },
        auth.session_secret(),
        algorithm="HS256",
    )
    client = TestClient(app)
    client.cookies.set("session", token)

    assert client.get("/search", params={"q": "Atlas"}).status_code == 403
