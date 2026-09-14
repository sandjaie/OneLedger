"""Live tests for `GET /coverage` and its acknowledgements (Phase 2.1 Task 1).

A synthetic household and two accounts of its own (one opened in 2024, one never opened with a
2025 opening balance — ADR-0011's visible-gap case), covering the spec's required test evidence:
reconciled, a month split across two batches, a cancelled batch that covers nothing, partial,
missing, an acknowledged gap, a later import outranking an acknowledgement, the current month in
progress, and the payslip row. Skipped when no database is reachable. Every name, label and
amount is synthetic.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.db import scope_to_owner
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class CoverageFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    account_id: UUID
    unopened_account_id: UUID
    late_opened_account_id: UUID
    other_account_id: UUID
    non_statement_account_id: UUID
    currency: str


def client() -> TestClient:
    return TestClient(app)


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    name: str,
    currency: str,
    *,
    opened_on: date | None,
    opening_balance_date: date,
    account_type: str = "savings",
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opened_on, opening_balance, opening_balance_date)
           VALUES (%s, %s, %s, %s::account_type, %s, 'XZ', %s, 0, %s) RETURNING id""",
        (household_id, person_id, name, account_type, currency, opened_on, opening_balance_date),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


def _batch(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    period_start: date,
    period_end: date,
    *,
    batch_status: str = "completed",
) -> None:
    cur.execute(
        """INSERT INTO import_batch (household_id, account_id, status, period_start,
                                      period_end, period_is_derived)
           VALUES (%s, %s, %s::import_status, %s, %s, false)""",
        (household_id, account_id, batch_status, period_start, period_end),
    )


@pytest.fixture
def live() -> Iterator[CoverageFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "coverage-owner@example.invalid")
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = str(found[0]).strip(), str(found[1]).strip()
        for household, name in (
            (household_id, "coverage household"),
            (other_household_id, "other coverage household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "coverage owner"),
            (other_person_id, other_household_id, "other coverage owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, "other-coverage@example.invalid"),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )

        account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic coverage account",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance_date=date(2024, 1, 1),
        )
        unopened_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic never-opened coverage account",
            base,
            opened_on=None,
            opening_balance_date=date(2025, 6, 1),
        )
        late_opened_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic late-opened coverage account",
            base,
            opened_on=date(2024, 6, 1),
            opening_balance_date=date(2024, 6, 1),
        )
        other_account_id = _account(
            cur,
            other_household_id,
            other_person_id,
            "Other synthetic coverage account",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance_date=date(2024, 1, 1),
        )
        # Brokerage is not in STATEMENT_SOURCED_TYPES: no coverage row exists for it, so it must
        # 404 exactly as a foreign account does (OL-001).
        non_statement_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic brokerage account",
            base,
            opened_on=date(2024, 1, 1),
            opening_balance_date=date(2024, 1, 1),
            account_type="brokerage",
        )

        # January reconciled by one batch; February split across two batches; March partial;
        # April "covered" only by a cancelled batch, which counts nothing; May has no evidence.
        _batch(cur, household_id, account_id, date(2024, 1, 1), date(2024, 1, 31))
        _batch(cur, household_id, account_id, date(2024, 2, 1), date(2024, 2, 15))
        _batch(cur, household_id, account_id, date(2024, 2, 16), date(2024, 2, 29))
        _batch(cur, household_id, account_id, date(2024, 3, 1), date(2024, 3, 10))
        _batch(
            cur,
            household_id,
            account_id,
            date(2024, 4, 1),
            date(2024, 4, 30),
            batch_status="cancelled",
        )

        # Payslips: January reconciled, February left with no evidence.
        scope_to_owner(connection, user_id)
        cur.execute(
            """SELECT recorded_payslip_id FROM record_payslip(
                   %s, NULL, NULL, %s, %s, %s, %s, %s, %s::payslip_line_kind[], %s, %s)""",
            (
                str(uuid4()),
                date(2024, 1, 1),
                base,
                Decimal("1000"),
                Decimal("0"),
                Decimal("1000"),
                ["earning"],
                ["Synthetic salary"],
                [Decimal("1000")],
            ),
        )
        assert cur.fetchone() is not None
    connection.commit()

    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield CoverageFixture(
            connection=connection,
            owner=owner,
            account_id=account_id,
            unopened_account_id=unopened_account_id,
            late_opened_account_id=late_opened_account_id,
            other_account_id=other_account_id,
            non_statement_account_id=non_statement_account_id,
            currency=base,
        )
    finally:
        app.dependency_overrides.clear()
        households = [household_id, other_household_id]
        with connection.cursor() as cur:
            # coverage_gap_acknowledgement cascades from account on delete, but is truncated
            # directly since a foreign-key delete order would otherwise have to run last anyway.
            # payslip is TRUNCATEd rather than DELETEd: `preserve_payslip` blocks UPDATE/DELETE
            # but not TRUNCATE, matching `test_payslips_live.py`'s own teardown. import_batch,
            # account, app_user, person and household are then deleted in FK-dependency order so
            # this fixture leaves nothing behind for a later suite to trip over.
            cur.execute("TRUNCATE coverage_gap_acknowledgement, payslip_line, payslip CASCADE")
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM import_batch WHERE household_id = ANY(%s)", (households,))
            cur.execute("DELETE FROM account WHERE household_id = ANY(%s)", (households,))
            cur.execute("DELETE FROM app_user WHERE household_id = ANY(%s)", (households,))
            cur.execute("DELETE FROM person WHERE household_id = ANY(%s)", (households,))
            cur.execute("DELETE FROM household WHERE id = ANY(%s)", (households,))
        connection.commit()
        connection.close()


def _months_index(body: dict[str, Any]) -> dict[str, int]:
    return {month: i for i, month in enumerate(body["months"])}


def _row(
    body: dict[str, Any], *, account_id: UUID | None, subject: str = "account"
) -> dict[str, Any]:
    key = None if account_id is None else str(account_id)
    return next(r for r in body["rows"] if r["subject"] == subject and r["accountId"] == key)


def test_coverage_grid_status_order(live: CoverageFixture) -> None:
    response = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN)
    assert response.status_code == 200
    body = response.json()
    assert body["historyAnchor"] == "2024-01-01"
    assert body["months"][0] == "2024-01-01"

    idx = _months_index(body)
    row = _row(body, account_id=live.account_id)
    assert row["lifeStart"] == "2024-01-01"
    assert row["anchorAfterHistory"] is False
    months = row["months"]
    assert months[idx["2024-01-01"]]["status"] == "reconciled"
    assert months[idx["2024-02-01"]]["status"] == "reconciled"  # split across two batches
    assert months[idx["2024-03-01"]]["status"] == "partial"
    assert months[idx["2024-04-01"]]["status"] == "missing"  # cancelled batch covers nothing
    assert months[idx["2024-05-01"]]["status"] == "missing"
    assert months[idx["2024-06-01"]]["status"] == "in_progress"  # the month of asOf, no evidence


def test_never_opened_account_2024_is_missing_and_acknowledgeable(live: CoverageFixture) -> None:
    body = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    idx = _months_index(body)
    row = _row(body, account_id=live.unopened_account_id)
    assert row["lifeStart"] == "2024-01-01"  # the anchor, opened_on is null
    assert row["openingBalanceDate"] == "2025-06-01"
    assert row["anchorAfterHistory"] is True
    assert row["months"][idx["2024-02-01"]]["status"] == "missing"

    put = client().put(
        "/coverage/acknowledgements",
        json={
            "subject": "account",
            "accountId": str(live.unopened_account_id),
            "month": "2024-02-01",
            "reason": "unobtainable",
            "note": "no paper record survives",
        },
        headers=ORIGIN,
    )
    assert put.status_code == 200

    body2 = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    cell = _row(body2, account_id=live.unopened_account_id)["months"][idx["2024-02-01"]]
    assert cell["status"] == "acknowledged"
    assert cell["reason"] == "unobtainable"
    assert cell["note"] == "no paper record survives"


def test_a_later_import_outranks_an_acknowledgement(live: CoverageFixture) -> None:
    idx_month = "2024-05-01"
    put = client().put(
        "/coverage/acknowledgements",
        json={
            "subject": "account",
            "accountId": str(live.account_id),
            "month": idx_month,
            "reason": "not_issued",
        },
        headers=ORIGIN,
    )
    assert put.status_code == 200
    body = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    idx = _months_index(body)
    cell = _row(body, account_id=live.account_id)["months"][idx[idx_month]]
    assert cell["status"] == "acknowledged"

    with live.connection.cursor() as cur:
        _batch(cur, live.owner.household_id, live.account_id, date(2024, 5, 1), date(2024, 5, 31))
    live.connection.commit()

    body2 = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    cell2 = _row(body2, account_id=live.account_id)["months"][idx[idx_month]]
    assert cell2["status"] == "reconciled"


def test_payslip_row_covered_missing_and_acknowledged(live: CoverageFixture) -> None:
    body = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    idx = _months_index(body)
    row = _row(body, account_id=None, subject="payslips")
    assert row["months"][idx["2024-01-01"]]["status"] == "reconciled"
    assert row["months"][idx["2024-02-01"]]["status"] == "missing"

    put = client().put(
        "/coverage/acknowledgements",
        json={"subject": "payslips", "month": "2024-03-01", "reason": "not_issued"},
        headers=ORIGIN,
    )
    assert put.status_code == 200
    body2 = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    row2 = _row(body2, account_id=None, subject="payslips")
    assert row2["months"][idx["2024-03-01"]]["status"] == "acknowledged"


def test_acknowledgement_upsert_replaces_reason_and_note(live: CoverageFixture) -> None:
    body_1 = {
        "subject": "account",
        "accountId": str(live.account_id),
        "month": "2024-05-01",
        "reason": "not_issued",
        "note": "first note",
    }
    first = client().put("/coverage/acknowledgements", json=body_1, headers=ORIGIN)
    assert first.status_code == 200
    assert first.json()["reason"] == "not_issued"

    body_2 = {**body_1, "reason": "unsupported_format", "note": "second note"}
    second = client().put("/coverage/acknowledgements", json=body_2, headers=ORIGIN)
    assert second.status_code == 200
    assert second.json()["reason"] == "unsupported_format"
    assert second.json()["note"] == "second note"

    body_get = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    idx = _months_index(body_get)
    cell = _row(body_get, account_id=live.account_id)["months"][idx["2024-05-01"]]
    assert cell["reason"] == "unsupported_format"
    assert cell["note"] == "second note"


def test_acknowledgement_delete_is_idempotent(live: CoverageFixture) -> None:
    ack = {
        "subject": "account",
        "accountId": str(live.account_id),
        "month": "2024-05-01",
        "reason": "not_issued",
    }
    assert client().put("/coverage/acknowledgements", json=ack, headers=ORIGIN).status_code == 200

    params = {"subject": "account", "accountId": str(live.account_id), "month": "2024-05-01"}
    first = client().request("DELETE", "/coverage/acknowledgements", params=params, headers=ORIGIN)
    assert first.status_code == 204
    second = client().request("DELETE", "/coverage/acknowledgements", params=params, headers=ORIGIN)
    assert second.status_code == 204

    body = client().get("/coverage", params={"asOf": "2024-06-15"}, headers=ORIGIN).json()
    idx = _months_index(body)
    cell = _row(body, account_id=live.account_id)["months"][idx["2024-05-01"]]
    assert cell["status"] == "missing"


def test_acknowledging_a_covered_month_is_refused(live: CoverageFixture) -> None:
    response = client().put(
        "/coverage/acknowledgements",
        json={
            "subject": "account",
            "accountId": str(live.account_id),
            "month": "2024-01-01",
            "reason": "not_issued",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 409


def test_acknowledging_a_foreign_account_is_404(live: CoverageFixture) -> None:
    response = client().put(
        "/coverage/acknowledgements",
        json={
            "subject": "account",
            "accountId": str(live.other_account_id),
            "month": "2024-02-01",
            "reason": "not_issued",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 404


def test_acknowledging_a_non_statement_sourced_account_is_404(live: CoverageFixture) -> None:
    response = client().put(
        "/coverage/acknowledgements",
        json={
            "subject": "account",
            "accountId": str(live.non_statement_account_id),
            "month": "2024-02-01",
            "reason": "not_issued",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 404


def test_acknowledging_before_an_accounts_life_is_refused(live: CoverageFixture) -> None:
    response = client().put(
        "/coverage/acknowledgements",
        json={
            "subject": "account",
            "accountId": str(live.late_opened_account_id),
            "month": "2024-01-01",
            "reason": "not_issued",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 422


def test_doctor_reports_a_coverage_finding(live: CoverageFixture) -> None:
    response = client().get("/doctor", headers=ORIGIN)
    assert response.status_code == 200
    sections = {section["title"]: section for section in response.json()["sections"]}
    assert "Coverage" in sections
    messages = [finding["message"] for finding in sections["Coverage"]["findings"]]
    pattern = re.compile(
        r"Synthetic never-opened coverage account: \d+ months? missing, \d+ partial, "
        r"earliest \d{4}-\d{2}\."
    )
    assert any(pattern.search(message) for message in messages)
