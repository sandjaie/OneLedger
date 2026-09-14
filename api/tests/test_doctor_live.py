"""`transactions_since` against the test database: the count the backup finding rests on.

The unit tests replace the connection, so nothing there sees the SQL. This is where its
household filter, its comparison and its no-backup case are held. Skipped when no test database
is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app
from oneledger.routes import doctor, investment_catalogue
from oneledger.routes.doctor import transactions_since

from .live_database import connect_to_test_database
from .test_ingest_route_live import Fixture, _clean, _connect
from .test_investment_events_live import EventFixture
from .test_portfolio_live import _acquire

STAMP = datetime(2026, 9, 7, 11, 47, tzinfo=UTC)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    try:
        yield Fixture(conn)
    finally:
        with conn.cursor() as cur:
            _clean(cur)
        conn.commit()
        conn.close()


def a_transaction(live: Fixture, created_at: datetime) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        amount, currency, fx_rate_date, fx_rate_to_base,
                                        fx_rate_to_secondary, amount_base, amount_secondary,
                                        txn_type, source, dedupe_hash, created_at)
               VALUES (%s, %s, DATE '2026-09-01', 'SYNTHETIC', -10.0000, %s, DATE '2026-09-01',
                       1, 1, -10.0000, -10.0000, 'expense', 'manual', %s, %s)""",
            (live.household_id, live.account_id, live.base, uuid4().hex * 2, created_at),
        )
    live.conn.commit()


def test_the_count_is_the_households_rows_created_after_the_stamp(live: Fixture) -> None:
    a_transaction(live, STAMP - timedelta(hours=1))
    a_transaction(live, STAMP + timedelta(hours=1))
    a_transaction(live, STAMP + timedelta(days=2))

    assert transactions_since(live.household_id, STAMP) == 2
    assert transactions_since(live.household_id, None) == 3
    assert transactions_since(uuid4(), None) == 0


pytest_plugins = ("tests.test_investment_events_live",)


@pytest.fixture
def investment_doctor_live(
    live_events: EventFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[EventFixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "connect", connect_to_test_database)
    monkeypatch.setattr(investment_catalogue, "connect", connect_to_test_database)
    yield live_events


def test_wealth_history_counts_the_households_incomplete_months(live: Fixture) -> None:
    """A stored `status <> 'available'` row is how `GET /wealth-history` itself marks a month
    incomplete (routes/wealth_history.py); this reads exactly that fact for a synthetic pair of
    months, one earlier and complete, one later and not."""
    owner = auth.OwnerSession(
        user_id=live.user_id, household_id=live.household_id, session_id=uuid4(), email="o@x"
    )
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO month_end_position
                   (household_id, month_end, account_id, valuation_mode, currency,
                    native_value, evidence_date, value_base, value_secondary, status)
               VALUES (%s, DATE '2026-06-30', %s, 'ledger_balance', %s,
                       100.0000, DATE '2026-06-30', 100.0000, 100.0000, 'available')""",
            (live.household_id, live.account_id, live.base),
        )
        cur.execute(
            """INSERT INTO month_end_position
                   (household_id, month_end, account_id, valuation_mode, currency, status)
               VALUES (%s, DATE '2026-07-31', %s, 'ledger_balance', %s, 'no_snapshot')""",
            (live.household_id, live.account_id, live.base),
        )
    live.conn.commit()

    assert doctor.wealth_history_counts(owner) == (1, date(2026, 7, 31))


def test_doctor_route_includes_wealth_history_findings(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO month_end_position
                   (household_id, month_end, account_id, valuation_mode, currency, status)
               VALUES (%s, DATE '2026-07-31', %s, 'ledger_balance', %s, 'no_snapshot')""",
            (live.household_id, live.account_id, live.base),
        )
    live.conn.commit()

    def owner_dep() -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=live.user_id, household_id=live.household_id, session_id=uuid4(), email="o@x"
        )

    app.dependency_overrides[auth.require_owner] = owner_dep
    try:
        response = TestClient(app).get("/doctor")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    sections = {section["title"]: section for section in response.json()["sections"]}
    messages = [f["message"] for f in sections["Wealth history"]["findings"]]
    assert any("1 month of wealth history is incomplete" in message for message in messages)


def test_doctor_reports_an_unvalued_holding(investment_doctor_live: EventFixture) -> None:
    """A purchase with no dated price yet must surface in Settings › Data Quality."""
    _acquire(investment_doctor_live)

    response = TestClient(app).get("/doctor")

    assert response.status_code == 200
    sections = {section["title"]: section for section in response.json()["sections"]}
    messages = [finding["message"] for finding in sections["Investments"]["findings"]]
    assert any("no dated price and cannot be valued" in message for message in messages)
