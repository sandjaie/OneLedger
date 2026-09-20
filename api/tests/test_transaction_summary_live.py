"""Phase 4.8 Task 4.8.4: exact filtered summary for ``GET /transactions``.

The ledger count and attention counts read the base table; money reads only
``v_transaction_reportable``. Every label, identifier and amount is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, _clean, _connect
from .test_transfer_writes_live import Owner
from .test_transfers_live import an_account, client


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Owner]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    fixture: Owner | None = None
    try:
        fixture = Owner(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("DELETE FROM merchant WHERE canonical_name LIKE 'Synthetic transaction%'")
            cur.execute("DELETE FROM category WHERE name LIKE 'Synthetic transaction%'")
            if fixture is not None:
                cur.execute("DELETE FROM app_user WHERE id = %s", (fixture.user_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def category(live: Owner, label: str) -> UUID:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO category (household_id, name, slug, kind, bucket)
               VALUES (%s, %s, %s, 'expense', 'want') RETURNING id""",
            (
                live.household_id,
                f"Synthetic transaction {label}",
                f"synthetic-transaction-{uuid4().hex}",
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def merchant(live: Owner, label: str) -> UUID:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO merchant (household_id, canonical_name)
               VALUES (%s, %s) RETURNING id""",
            (live.household_id, f"Synthetic transaction {label}"),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def document(live: Owner, account_id: UUID, label: str) -> UUID:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO document (
                   household_id, account_id, document_type, original_filename, storage_bucket,
                   storage_path, mime_type, byte_size, sha256)
               VALUES (%s, %s, 'bank_statement', %s, 'local', %s,
                       'application/pdf', 1, %s) RETURNING id""",
            (
                live.household_id,
                account_id,
                f"synthetic-{label}.pdf",
                f"synthetic/{label}.pdf",
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def transaction(
    live: Owner,
    account_id: UUID,
    *,
    amount: str,
    booked: date,
    description: str,
    txn_type: str,
    category_id: UUID | None = None,
    merchant_id: UUID | None = None,
    document_id: UUID | None = None,
    country: str | None = None,
    tags: list[str] | None = None,
    reviewed: bool = False,
    classified_by: str = "unclassified",
    excluded: bool = False,
    household_id: UUID | None = None,
) -> UUID:
    value = Decimal(amount)
    owner = household_id or live.household_id
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (
                   household_id, account_id, booked_date, description_raw, amount, currency,
                   fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                   amount_secondary, txn_type, category_id, merchant_id, country, tags,
                   source, source_document_id, dedupe_hash, is_reviewed, classified_by,
                   classification_confidence, is_excluded)
               VALUES (%s, %s, %s, %s, %s,
                       (SELECT currency FROM account WHERE id = %s), %s, 1, 1, %s, %s,
                       %s, %s, %s, %s, %s, 'manual', %s, %s, %s, %s, 1, %s)
               RETURNING id""",
            (
                owner,
                account_id,
                booked,
                description,
                value,
                account_id,
                booked,
                value,
                value,
                txn_type,
                category_id,
                merchant_id,
                country,
                tags,
                document_id,
                uuid4().hex + uuid4().hex,
                reviewed,
                classified_by,
                excluded,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def get_transactions(**params: object) -> dict[str, Any]:
    response = client().get("/transactions", params=params, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_reportable_money_refunds_and_ledger_attention_are_distinct(live: Owner) -> None:
    income = transaction(
        live,
        live.account_id,
        amount="100.0000",
        booked=date(2026, 3, 1),
        description="Synthetic transaction income",
        txn_type="income",
    )
    transaction(
        live,
        live.account_id,
        amount="-40.0000",
        booked=date(2026, 3, 2),
        description="Synthetic transaction expense",
        txn_type="expense",
        reviewed=True,
        classified_by="user_manual",
    )
    transaction(
        live,
        live.account_id,
        amount="60.0000",
        booked=date(2026, 3, 3),
        description="Synthetic transaction refund",
        txn_type="refund",
        reviewed=False,
        classified_by="user_manual",
    )
    transaction(
        live,
        live.account_id,
        amount="-500.0000",
        booked=date(2026, 3, 4),
        description="Synthetic transaction card settlement",
        txn_type="transfer_out",
    )
    transaction(
        live,
        live.account_id,
        amount="-700.0000",
        booked=date(2026, 3, 5),
        description="Synthetic transaction investment",
        txn_type="investment_buy",
    )
    transaction(
        live,
        live.account_id,
        amount="-900.0000",
        booked=date(2026, 3, 6),
        description="Synthetic transaction duplicate",
        txn_type="expense",
        excluded=True,
    )

    body = get_transactions(dateFrom="2026-03-01", dateTo="2026-03-31", limit=200)
    assert body["total"] == 6
    assert str(income) in {row["id"] for row in body["transactions"]}
    assert body["summary"] == {
        "reportableIncomeBase": "100.0000",
        "reportableIncomeSecondary": "100.0000",
        "reportableSpendingBase": "-20.0000",
        "reportableSpendingSecondary": "-20.0000",
        "reportableNetBase": "120.0000",
        "reportableNetSecondary": "120.0000",
        "unreviewedCount": 5,
        "uncategorizedCount": 4,
        "attentionCount": 5,
        "moneyScope": "reportable",
        "attentionScope": "ledger",
    }

    for txn_type in ("transfer_out", "investment_buy"):
        selected = get_transactions(txnType=txn_type)
        assert selected["total"] == 1
        assert selected["summary"]["reportableIncomeBase"] == "0.0000"
        assert selected["summary"]["reportableSpendingBase"] == "0.0000"


def test_every_filter_scopes_rows_counts_attention_and_money(live: Owner) -> None:
    target_account = live.account_id
    other_account = an_account(live, currency=live.secondary)
    target_category = category(live, "target category")
    other_category = category(live, "other category")
    target_merchant = merchant(live, "target merchant")
    other_merchant = merchant(live, "other merchant")
    target_document = document(live, target_account, "target")
    other_document = document(live, other_account, "other")
    target = transaction(
        live,
        target_account,
        amount="-10.0000",
        booked=date(2026, 3, 20),
        description="Synthetic transaction target needle",
        txn_type="expense",
        category_id=target_category,
        merchant_id=target_merchant,
        document_id=target_document,
        country="XA",
        tags=["target-tag"],
    )
    other = transaction(
        live,
        other_account,
        amount="20.0000",
        booked=date(2026, 3, 10),
        description="Synthetic transaction other haystack",
        txn_type="income",
        category_id=other_category,
        merchant_id=other_merchant,
        document_id=other_document,
        country="XB",
        tags=["other-tag"],
        reviewed=True,
        classified_by="user_manual",
    )

    cases: list[tuple[dict[str, object], UUID]] = [
        ({"transactionId": target}, target),
        ({"accountId": target_account}, target),
        ({"categoryId": target_category}, target),
        ({"txnType": "expense"}, target),
        ({"dateFrom": "2026-03-15"}, target),
        ({"dateTo": "2026-03-15"}, other),
        ({"q": "needle"}, target),
        ({"merchantId": target_merchant}, target),
        ({"currency": live.base}, target),
        ({"country": "XA"}, target),
        ({"amountMin": "1.0000"}, other),
        ({"amountMax": "-1.0000"}, target),
        ({"tag": "target-tag"}, target),
        ({"reviewState": "unreviewed"}, target),
        ({"classifiedBy": "unclassified"}, target),
        ({"sourceDocumentId": target_document}, target),
    ]
    for params, expected_id in cases:
        body = get_transactions(**params)
        assert body["total"] == 1, params
        assert [row["id"] for row in body["transactions"]] == [str(expected_id)], params
        if expected_id == target:
            assert body["summary"]["reportableSpendingBase"] == "10.0000", params
            assert body["summary"]["reportableIncomeBase"] == "0.0000", params
            assert body["summary"]["attentionCount"] == 1, params
        else:
            assert body["summary"]["reportableIncomeBase"] == "20.0000", params
            assert body["summary"]["reportableSpendingBase"] == "0.0000", params
            assert body["summary"]["attentionCount"] == 0, params


def test_sort_and_pagination_do_not_change_the_filtered_summary(live: Owner) -> None:
    transaction(
        live,
        live.account_id,
        amount="-10.0000",
        booked=date(2026, 3, 20),
        description="Synthetic transaction paged expense",
        txn_type="expense",
    )
    transaction(
        live,
        live.account_id,
        amount="20.0000",
        booked=date(2026, 3, 21),
        description="Synthetic transaction paged income",
        txn_type="income",
        reviewed=True,
        classified_by="user_manual",
    )
    first = get_transactions(limit=1, offset=0, sort="amount", direction="asc")
    second = get_transactions(limit=1, offset=1, sort="description", direction="desc")
    assert first["total"] == second["total"] == 2
    assert len(first["transactions"]) == len(second["transactions"]) == 1
    assert first["summary"] == second["summary"]
    assert first["summary"]["reportableNetBase"] == "10.0000"


def test_summary_is_owner_only_household_scoped_and_read_only(
    live: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    own = transaction(
        live,
        live.account_id,
        amount="-10.0000",
        booked=date(2026, 3, 20),
        description="Synthetic transaction owned",
        txn_type="expense",
    )
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('Synthetic transaction foreign household', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        foreign_household = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO person (household_id, display_name, is_self)
               VALUES (%s, 'Synthetic transaction foreign owner', true)""",
            (foreign_household,),
        )
    live.conn.commit()
    foreign_account = an_account(live, household=foreign_household)
    transaction(
        live,
        foreign_account,
        amount="-99.0000",
        booked=date(2026, 3, 20),
        description="Synthetic transaction foreign row",
        txn_type="expense",
        household_id=foreign_household,
    )
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT id, updated_at, amount_base, classified_by::text, is_reviewed
                 FROM transaction WHERE household_id = %s ORDER BY id""",
            (live.household_id,),
        )
        before = cur.fetchall()

    body = get_transactions()
    assert body["total"] == 1
    assert body["transactions"][0]["id"] == str(own)
    assert body["summary"]["reportableSpendingBase"] == "10.0000"
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT id, updated_at, amount_base, classified_by::text, is_reviewed
                 FROM transaction WHERE household_id = %s ORDER BY id""",
            (live.household_id,),
        )
        assert cur.fetchall() == before

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
    member = TestClient(app)
    member.cookies.set("session", token)
    assert member.get("/transactions", headers=ORIGIN).status_code == 403
