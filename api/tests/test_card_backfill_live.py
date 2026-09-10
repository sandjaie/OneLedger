"""Card cycles for statements committed before their layout could read them (slice 1.4).

A card statement committed while its layout captured none of the cycle's facts has its rows and
no `card_statement`. `POST /card-statements/backfill` re-reads each such statement's stored
original with the newest version of its layout and writes the cycle the commit would have written,
through the commit's own code, or says structurally why it could not. It never guesses a fact
(Invariant 7), and a second run writes nothing.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .test_card_cycle_live import CARD_LAYOUT_ID, CARD_METADATA, CardStaged, _card_statement
from .test_import_review_live import _connect
from .test_ingest_route_live import ORIGIN, SALT


class Earlier(CardStaged):
    """The card fixture as it stood before its layout learned the cycle's facts: version 1
    captures none of them, so a commit under it writes no cycle."""

    def __init__(self, conn: psycopg.Connection) -> None:
        super().__init__(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE statement_layout SET metadata_patterns = NULL WHERE id = %s",
                (CARD_LAYOUT_ID,),
            )
        conn.commit()

    def committed_without_a_cycle(self, statement: bytes | None = None) -> str:
        batch = self.stage_card(statement)
        self.accept_all(batch)
        committed = self.commit(batch)
        assert committed.status_code == 200, committed.text
        assert committed.json()["cardStatementId"] is None
        return batch

    def learn_the_facts(self) -> None:
        """Version 2: the same layout by name, now reading the four facts."""
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO statement_layout
                     (household_id, name, version, fingerprints, columns, date_format,
                      sign_convention, inverts_sign, chronological_order, row_start_rule,
                      opening_balance_pattern, closing_balance_pattern, has_running_balance,
                      metadata_patterns)
                   SELECT household_id, name, 2, fingerprints, columns, date_format,
                          sign_convention, inverts_sign, chronological_order, row_start_rule,
                          opening_balance_pattern, closing_balance_pattern, has_running_balance,
                          %s
                     FROM statement_layout WHERE id = %s""",
                (psycopg.types.json.Json(CARD_METADATA), CARD_LAYOUT_ID),
            )
        self.conn.commit()

    def original_of(self, batch: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT d.storage_path FROM import_batch b JOIN document d
                     ON d.id = b.document_id WHERE b.id = %s""",
                (batch,),
            )
            found: str = cur.fetchone()[0]  # type: ignore[index]
            return found


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Earlier]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        fixture = Earlier(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute("DELETE FROM statement_layout")
            cur.execute("DELETE FROM account WHERE name = 'route test'")
            cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
            cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
        conn.commit()
        conn.close()


def backfill() -> dict[str, Any]:
    response = TestClient(app).post("/card-statements/backfill", headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_statement_committed_before_its_layout_read_the_facts_gets_its_cycle(
    live: Earlier,
) -> None:
    live.committed_without_a_cycle()
    assert live.cycles() == []
    live.learn_the_facts()

    assert backfill() == {"written": 1, "skipped": []}

    # Exactly the cycle a commit under version 2 would have written.
    [cycle] = live.cycles()
    assert cycle["account_id"] == live.card_id
    assert (cycle["period_start"], cycle["period_end"]) == (date(2026, 3, 1), date(2026, 3, 2))
    assert (cycle["statement_date"], cycle["payment_due_date"]) == (
        date(2026, 3, 15),
        date(2026, 4, 4),
    )
    assert (cycle["minimum_due"], cycle["credit_limit"]) == (
        Decimal("40.0000"),
        Decimal("20000.0000"),
    )
    assert (cycle["opening_balance"], cycle["closing_balance"], cycle["statement_balance"]) == (
        Decimal("0.0000"),
        Decimal("-400.0000"),
        Decimal("400.0000"),
    )
    assert str(cycle["source_document_id"]) == live.parsed["documentId"]


def test_running_it_twice_writes_nothing_the_second_time(live: Earlier) -> None:
    live.committed_without_a_cycle()
    live.learn_the_facts()
    backfill()

    assert backfill() == {"written": 0, "skipped": []}
    assert len(live.cycles()) == 1


def test_a_statement_whose_original_states_no_dates_is_named_and_not_guessed(
    live: Earlier,
) -> None:
    batch = live.committed_without_a_cycle(_card_statement(dated=False))
    live.learn_the_facts()

    body = backfill()

    assert body["written"] == 0
    assert body["skipped"] == [
        {
            "importBatchId": batch,
            "reason": "its cycle needs payment_due_date, statement_date, "
            "which its original does not state",
        }
    ]
    assert live.cycles() == []


def test_an_original_that_cannot_be_read_is_named(live: Earlier) -> None:
    batch = live.committed_without_a_cycle()
    live.learn_the_facts()
    stored = Path(live.original_of(batch))
    (stored if stored.is_absolute() else Path(os.environ["DOCUMENTS_DIR"]) / stored).unlink()

    assert backfill() == {
        "written": 0,
        "skipped": [{"importBatchId": batch, "reason": "its original could not be read"}],
    }


def test_a_bank_batch_is_not_a_card_statement_and_is_left_alone(live: Earlier) -> None:
    batch = live.stage()
    live.accept_all(batch)
    assert live.commit(batch).status_code == 200

    assert backfill() == {"written": 0, "skipped": []}


def test_a_statement_still_under_review_is_not_read(live: Earlier) -> None:
    # Only a committed statement has rows a cycle can describe; one still under review would
    # get a cycle for rows that may never land.
    live.stage_card()
    live.learn_the_facts()

    assert backfill() == {"written": 0, "skipped": []}
    assert live.cycles() == []


def test_a_statement_flagged_protected_that_opens_without_one_still_gets_its_cycle(
    live: Earlier,
) -> None:
    # The flag records that a password was offered at import; it is not a reason to refuse a
    # file that opens without one, which every card statement in the working ledger does.
    batch = live.committed_without_a_cycle()
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE document SET is_password_protected = true
                WHERE id = (SELECT document_id FROM import_batch WHERE id = %s)""",
            (batch,),
        )
    live.conn.commit()
    live.learn_the_facts()

    assert backfill() == {"written": 1, "skipped": []}


def test_an_original_that_will_not_open_is_named(live: Earlier) -> None:
    batch = live.committed_without_a_cycle()
    live.learn_the_facts()
    stored = Path(live.original_of(batch))
    (stored if stored.is_absolute() else Path(os.environ["DOCUMENTS_DIR"]) / stored).write_bytes(
        b"not a statement"
    )

    assert backfill() == {
        "written": 0,
        "skipped": [
            {
                "importBatchId": batch,
                "reason": "its original will not open; "
                "if it is password-protected, import it again",
            }
        ],
    }
