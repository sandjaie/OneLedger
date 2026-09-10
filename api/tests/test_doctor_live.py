"""`transactions_since` against the test database: the count the backup finding rests on.

The unit tests replace the connection, so nothing there sees the SQL. This is where its
household filter, its comparison and its no-backup case are held. Skipped when no test database
is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from oneledger.routes.doctor import transactions_since

from .test_ingest_route_live import Fixture, _clean, _connect

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
