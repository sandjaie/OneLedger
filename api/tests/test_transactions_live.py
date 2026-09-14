"""The B3.5 interleaving, against the live database: a reporting-pair change and a frozen
write serialize on the household row in both orders. Everything runs inside transactions that
roll back; nothing persists. The final verifier supplies the isolated test database where this
proof runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database
from .test_import_review_live import Staged, _all_row_ids
from .test_import_review_live import _connect as _connect_review
from .test_ingest_route_live import ORIGIN, SALT, _clean, _statement


def _connect() -> psycopg.Connection:
    """See `tests/live_database.py`: the test database, and only the test database."""
    return connect_to_test_database()


def _fixture(cur: psycopg.Cursor[Any]) -> tuple[Any, Any, str, str, tuple[Any, ...]]:
    cur.execute(
        """SELECT h.id, u.id, h.base_currency, h.secondary_currency,
                  h.name, p.display_name, p.tax_residency
             FROM household h
             JOIN person p ON p.household_id = h.id AND p.is_self
             JOIN app_user u ON u.household_id = h.id AND u.person_id = p.id
            WHERE u.role = 'owner'
            ORDER BY h.created_at, h.id
            LIMIT 1"""
    )
    fixture = cur.fetchone()
    if fixture is None:
        pytest.skip("no bound owner yet — live identity setup pending")
    return (
        fixture[0],
        fixture[1],
        str(fixture[2]).strip(),
        str(fixture[3]).strip(),
        (
            fixture[4],
            fixture[5],
            fixture[6],
        ),
    )


def test_a_pair_change_waits_for_an_in_flight_frozen_write() -> None:
    writer = _connect()
    changer = _connect()
    try:
        with writer.cursor() as wcur:
            hid, uid, base, secondary, (hname, pname, ptax) = _fixture(wcur)
            # The frozen writer's exact opening sequence: lock the household row, then read
            # the pair. Held open, uncommitted.
            wcur.execute(
                "SELECT base_currency, secondary_currency FROM household WHERE id = %s FOR UPDATE",
                (hid,),
            )
            with changer.cursor() as ccur:
                ccur.execute("SET LOCAL lock_timeout = '500ms'")
                ccur.execute("SELECT set_config('app.user_id', %s, true)", (str(uid),))
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    ccur.execute(
                        "SELECT update_household_configuration(%s, %s, %s, %s, %s)",
                        (hname, pname, ptax, base, secondary),
                    )
    finally:
        writer.rollback()
        changer.rollback()
        writer.close()
        changer.close()


def test_a_frozen_write_waits_for_an_in_flight_pair_change() -> None:
    changer = _connect()
    writer = _connect()
    try:
        with changer.cursor() as ccur:
            hid, uid, base, secondary, (hname, pname, ptax) = _fixture(ccur)
            ccur.execute("SELECT set_config('app.user_id', %s, true)", (str(uid),))
            # A same-values pair change still takes the household row lock (B3.5).
            ccur.execute(
                "SELECT update_household_configuration(%s, %s, %s, %s, %s)",
                (hname, pname, ptax, base, secondary),
            )
            with writer.cursor() as wcur:
                wcur.execute("SET LOCAL lock_timeout = '500ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    wcur.execute(
                        """SELECT base_currency, secondary_currency FROM household
                            WHERE id = %s FOR UPDATE""",
                        (hid,),
                    )
    finally:
        changer.rollback()
        writer.rollback()
        changer.close()
        writer.close()


def test_committed_rows_and_transfer_legs_carry_source_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`TransactionRow` gained `sourceDocumentId`/`sourcePageNumber`/`sourceRowNumber`, appended
    to the shared `COLUMNS`/`FIELDS` lists together — this proves both that `GET /transactions`
    decodes them and that `transfers.py`, which slices the same lists by width, still decodes a
    leg (Phase 1.8 Task 2)."""
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect_review()
    try:
        fixture = Staged(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        try:
            batch = fixture.stage(statement=_statement())
            rows = _all_row_ids(fixture, batch)
            accepted = fixture.patch(batch, {"rowIds": rows, "decision": "accepted"})
            assert accepted.status_code == 200, accepted.text
            committed = fixture.commit(batch)
            assert committed.status_code == 200, committed.text

            with conn.cursor() as cur:
                cur.execute(
                    """SELECT t.id, t.source_document_id, t.source_page_number,
                              t.source_row_number
                         FROM transaction t
                        WHERE t.import_batch_id = %s
                        ORDER BY t.source_row_number LIMIT 1""",
                    (batch,),
                )
                transaction_id, document_id, page_number, row_number = cur.fetchone()  # type: ignore[misc]

            listed = TestClient(app).get(
                "/transactions", params={"sourceDocumentId": str(document_id)}, headers=ORIGIN
            )
            assert listed.status_code == 200, listed.text
            listed_rows = listed.json()["transactions"]
            assert listed_rows
            assert all(row["sourceDocumentId"] == str(document_id) for row in listed_rows)
            found = next(row for row in listed_rows if row["id"] == str(transaction_id))
            assert found["sourcePageNumber"] == page_number
            assert found["sourceRowNumber"] == row_number

            candidates = TestClient(app).get(
                f"/transactions/{transaction_id}/transfer-candidates", headers=ORIGIN
            )
            assert candidates.status_code == 200, candidates.text
        finally:
            app.dependency_overrides.clear()
            with conn.cursor() as cur:
                _clean(cur)
                cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
            conn.commit()
    finally:
        conn.close()
