"""Grouped classification review against a disposable migrated database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database


def _household(
    cur: psycopg.Cursor[Any], currency: str, secondary_currency: str, label: str
) -> tuple[UUID, UUID, UUID]:
    cur.execute(
        "INSERT INTO household (name, base_currency, secondary_currency)"
        " VALUES (%s, %s, %s) RETURNING id",
        (f"{label} household", currency, secondary_currency),
    )
    household = cur.fetchone()
    assert household is not None
    household_id = cast(UUID, household[0])
    cur.execute(
        "INSERT INTO person (household_id, display_name, is_self)"
        " VALUES (%s, %s, true) RETURNING id",
        (household_id, f"{label} owner"),
    )
    person = cur.fetchone()
    assert person is not None
    person_id = cast(UUID, person[0])
    user_id = uuid4()
    cur.execute(
        "INSERT INTO app_user (id, household_id, person_id, email, role)"
        " VALUES (%s, %s, %s, %s, 'owner')",
        (user_id, household_id, person_id, f"{label}-{user_id}@example.invalid"),
    )
    cur.execute(
        """INSERT INTO account
             (household_id, owner_person_id, name, account_type, currency, country,
              opening_balance, opening_balance_date)
           VALUES (%s, %s, %s, 'savings', %s, 'XZ', 0, DATE '2026-01-01') RETURNING id""",
        (household_id, person_id, f"{label} account", currency),
    )
    account = cur.fetchone()
    assert account is not None
    account_id = cast(UUID, account[0])
    return household_id, user_id, account_id


def _transaction(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    currency: str,
    booked_date: date,
    amount: Decimal,
    description: str,
) -> UUID:
    cur.execute(
        """INSERT INTO transaction
             (household_id, account_id, booked_date, description_raw, description_clean,
              amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
              amount_base, amount_secondary, txn_type, source, dedupe_hash,
              classified_by, classification_confidence)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, 1, %s, %s,
                   'expense', 'manual', %s, 'unclassified', 0) RETURNING id""",
        (
            household_id,
            account_id,
            booked_date,
            description,
            description,
            amount,
            currency,
            booked_date,
            amount,
            amount,
            uuid4().hex + uuid4().hex,
        ),
    )
    transaction = cur.fetchone()
    assert transaction is not None
    return cast(UUID, transaction[0])


def test_groups_are_household_scoped_date_filtered_and_contain_no_money() -> None:
    conn = connect_to_test_database()
    letters = uuid4().hex.translate(str.maketrans("0123456789", "abcdefghij"))
    marker = f"SYNTHETIC REVIEW {letters}".upper()
    with conn.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = (str(value).strip() for value in currencies)
        currency = base
        household_id, user_id, account_id = _household(cur, base, secondary, "review")
        other_household, _, other_account = _household(cur, base, secondary, "other")
        first = _transaction(
            cur,
            household_id,
            account_id,
            currency,
            date(2026, 7, 1),
            Decimal("-12.0000"),
            marker,
        )
        second = _transaction(
            cur,
            household_id,
            account_id,
            currency,
            date(2026, 8, 1),
            Decimal("-91.0000"),
            marker,
        )
        foreign = _transaction(
            cur,
            other_household,
            other_account,
            currency,
            date(2026, 8, 1),
            Decimal("-37.0000"),
            marker,
        )
    conn.commit()

    def owner() -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=user_id,
            household_id=household_id,
            session_id=uuid4(),
            email="review-owner@example.invalid",
        )

    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/classification-review/groups")
        filtered = TestClient(app).get(
            "/classification-review/groups", params={"dateFrom": "2026-08-01"}
        )
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM transaction WHERE household_id IN (%s, %s)",
                (household_id, other_household),
            )
        conn.commit()
        conn.close()

    assert response.status_code == 200, response.text
    body = response.json()
    group = next(item for item in body["groups"] if item["description"] == marker)
    assert group["occurrences"] == 2
    assert set(group["transactionIds"]) == {str(first), str(second)}
    assert str(foreign) not in response.text
    assert "amount" not in response.text.lower()
    filtered_group = next(
        item for item in filtered.json()["groups"] if item["description"] == marker
    )
    assert filtered_group["occurrences"] == 1
    assert filtered_group["transactionIds"] == [str(second)]


def test_group_labels_redact_identifiers_without_changing_grouping() -> None:
    conn = connect_to_test_database()
    private_identifier = "1234-٥٦٧٨-9012-3456"
    marker = f"SYNTHETIC REVIEW {private_identifier}"
    with conn.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        currencies = cur.fetchone()
        assert currencies is not None
        base, secondary = (str(value).strip() for value in currencies)
        household_id, user_id, account_id = _household(cur, base, secondary, "privacy")
        first = _transaction(
            cur,
            household_id,
            account_id,
            base,
            date(2026, 7, 1),
            Decimal("-12.0000"),
            marker,
        )
        second = _transaction(
            cur,
            household_id,
            account_id,
            base,
            date(2026, 8, 1),
            Decimal("-13.0000"),
            marker,
        )
    conn.commit()

    def owner() -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=user_id,
            household_id=household_id,
            session_id=uuid4(),
            email="privacy-owner@example.invalid",
        )

    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).get("/classification-review/groups")
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transaction WHERE household_id = %s", (household_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM app_user WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM person WHERE household_id = %s", (household_id,))
            cur.execute("DELETE FROM household WHERE id = %s", (household_id,))
        conn.commit()
        conn.close()

    assert response.status_code == 200
    assert response.json()["totalGroups"] == 1
    [group] = response.json()["groups"]
    assert group["description"] == "SYNTHETIC REVIEW [redacted]"
    assert group["occurrences"] == 2
    assert set(group["transactionIds"]) == {str(first), str(second)}
    assert private_identifier not in response.text
    assert "1234567890123456" not in response.text
