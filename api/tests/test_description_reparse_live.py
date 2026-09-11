"""Guarded reparsing of derived descriptions for already committed statement rows.

Every statement value is synthetic. The route must prove the stored original still produces the
same immutable row structure before it may update only ``description_clean`` and review state.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf
from .test_import_review_live import Staged, _all_row_ids, _connect
from .test_ingest_route_live import LAYOUT_ID, ORIGIN, SALT, _clean


def _centered_statement() -> bytes:
    return build_pdf(
        [
            Placement(page=0, x=20, y=760, text="Acme Bank Statement"),
            Placement(page=0, x=20, y=750, text="Account Number 1234567890"),
            Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
            Placement(page=0, x=40, y=700, text="Date"),
            Placement(page=0, x=140, y=700, text="Description"),
            Placement(page=0, x=420, y=700, text="Amount"),
            Placement(page=0, x=500, y=700, text="Balance"),
            Placement(page=0, x=40, y=670, text="01/03/2026"),
            Placement(page=0, x=140, y=670, text="FIRST"),
            Placement(page=0, x=420, y=670, text="-10.00"),
            Placement(page=0, x=500, y=670, text="990.00"),
            # Physically before the second row, but vertically closest to its centred date.
            Placement(page=0, x=140, y=646, text="SECOND LEADING"),
            Placement(page=0, x=40, y=640, text="02/03/2026"),
            Placement(page=0, x=140, y=640, text="TAIL"),
            Placement(page=0, x=420, y=640, text="500.00"),
            Placement(page=0, x=500, y=640, text="1,490.00"),
            Placement(page=0, x=20, y=600, text="Closing Balance 1,490.00"),
        ]
    )


class ReparseFixture(Staged):
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        super().__init__(conn)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE statement_layout
                      SET continuation_rule = 'blank_date_and_money_columns'
                    WHERE id = %s""",
                (LAYOUT_ID,),
            )
        conn.commit()

    def committed_batch(self) -> str:
        batch = self.stage(statement=_centered_statement())
        rows = _all_row_ids(self, batch)
        accepted = self.patch(batch, {"rowIds": rows, "decision": "accepted"})
        assert accepted.status_code == 200, accepted.text
        committed = self.commit(batch)
        assert committed.status_code == 200, committed.text
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE transaction
                      SET classified_by = 'user_manual', classification_confidence = 1.000,
                          is_reviewed = true, notes = 'keep'"""
                " WHERE import_batch_id = %s",
                (batch,),
            )
        self.conn.commit()
        return batch

    def newer_layout(self) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO statement_layout
                     (household_id, institution_id, account_type, name, version, fingerprints,
                      header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                      chronological_order, row_start_rule, continuation_rule,
                      skip_line_patterns, opening_balance_pattern, closing_balance_pattern,
                      has_running_balance, quirks, metadata_patterns)
                   SELECT household_id, institution_id, account_type, name, 2, fingerprints,
                          header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                          chronological_order, row_start_rule, continuation_rule,
                          skip_line_patterns, opening_balance_pattern, closing_balance_pattern,
                          has_running_balance, ARRAY['row_assembly:nearest_dated_row'],
                          metadata_patterns
                     FROM statement_layout WHERE id = %s
                 RETURNING id""",
                (LAYOUT_ID,),
            )
            layout_id = str(cur.fetchone()[0])  # type: ignore[index]
        self.conn.commit()
        return layout_id

    def reparse(self, batch: str, layout: str, *, apply: bool = False) -> httpx.Response:
        response: httpx.Response = TestClient(app).post(
            f"/import/{batch}/reparse-descriptions",
            json={"layoutId": layout, "apply": apply},
            headers=ORIGIN,
        )
        return response

    def rows(self, batch: str) -> list[tuple[Any, ...]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT source_row_number, description_raw, description_clean, is_reviewed,
                          to_jsonb(t) - ARRAY['description_clean', 'is_reviewed', 'updated_at']
                     FROM transaction t
                    WHERE import_batch_id = %s AND parent_transaction_id IS NULL
                    ORDER BY source_row_number""",
                (batch,),
            )
            return cur.fetchall()

    def stored_path(self, batch: str) -> Path:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT d.storage_path FROM import_batch b
                     JOIN document d ON d.id = b.document_id WHERE b.id = %s""",
                (batch,),
            )
            stored = Path(cur.fetchone()[0])  # type: ignore[index]
        return stored if stored.is_absolute() else Path(os.environ["DOCUMENTS_DIR"]) / stored


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ReparseFixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        fixture = ReparseFixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
        conn.commit()
        conn.close()


def test_preview_proves_the_structure_and_writes_nothing(live: ReparseFixture) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()
    before = live.rows(batch)

    response = live.reparse(batch, layout)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "batchId": batch,
        "applied": False,
        "rowsCompared": 2,
        "descriptionsChanged": 2,
        "rowsQueuedForReview": 0,
    }
    assert live.rows(batch) == before


def test_apply_changes_only_clean_description_and_review_state_then_is_idempotent(
    live: ReparseFixture,
) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()
    before = live.rows(batch)

    applied = live.reparse(batch, layout, apply=True)

    assert applied.status_code == 200, applied.text
    assert applied.json() == {
        "batchId": batch,
        "applied": True,
        "rowsCompared": 2,
        "descriptionsChanged": 2,
        "rowsQueuedForReview": 2,
    }
    after = live.rows(batch)
    assert [row[1] for row in after] == [row[1] for row in before]
    assert [row[2] for row in before] == ["FIRST SECOND LEADING", "TAIL"]
    assert [row[2] for row in after] == ["FIRST", "SECOND LEADING TAIL"]
    assert [row[3] for row in after] == [False, False]
    assert [row[4] for row in after] == [row[4] for row in before]
    review = TestClient(app).get("/classification-review/groups", headers=ORIGIN)
    assert review.status_code == 200, review.text
    assert review.json()["totalOccurrences"] == 2

    repeated = live.reparse(batch, layout, apply=True)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["descriptionsChanged"] == 0
    assert repeated.json()["rowsQueuedForReview"] == 0


def test_old_layout_and_unfinished_batch_are_refused_without_writes(
    live: ReparseFixture,
) -> None:
    batch = live.committed_batch()
    before = live.rows(batch)

    old = live.reparse(batch, str(LAYOUT_ID), apply=True)

    assert old.status_code == 409
    assert live.rows(batch) == before

    layout = live.newer_layout()
    with live.conn.cursor() as cur:
        cur.execute("UPDATE import_batch SET status = 'awaiting_review' WHERE id = %s", (batch,))
    live.conn.commit()

    unfinished = live.reparse(batch, layout, apply=True)
    assert unfinished.status_code == 409
    assert live.rows(batch) == before


def test_unknown_batch_and_an_unrelated_newer_layout_are_refused(
    live: ReparseFixture,
) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()

    assert live.reparse(str(uuid4()), layout).status_code == 404

    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET name = 'unrelated layout' WHERE id = %s", (layout,)
        )
    live.conn.commit()

    unrelated = live.reparse(batch, layout, apply=True)
    assert unrelated.status_code == 409


def test_hash_or_row_identity_mismatch_refuses_the_whole_repair(live: ReparseFixture) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()
    before = live.rows(batch)
    path = live.stored_path(batch)
    original = path.read_bytes()
    path.write_bytes(original + b"changed")

    mismatched_file = live.reparse(batch, layout, apply=True)

    assert mismatched_file.status_code == 409
    assert "path" not in mismatched_file.text.lower()
    assert live.rows(batch) == before

    path.write_bytes(original)
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE staged_transaction SET source_page_number = source_page_number + 1"
            " WHERE import_batch_id = %s AND source_row_number = 1",
            (batch,),
        )
    live.conn.commit()

    mismatched_row = live.reparse(batch, layout, apply=True)
    assert mismatched_row.status_code == 409
    assert live.rows(batch) == before


def test_parse_failure_never_echoes_the_transient_password(live: ReparseFixture) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()
    path = live.stored_path(batch)
    malformed = b"not a statement"
    path.write_bytes(malformed)
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE document SET sha256 = %s
                WHERE id = (SELECT document_id FROM import_batch WHERE id = %s)""",
            (hashlib.sha256(malformed).hexdigest(), batch),
        )
    live.conn.commit()
    secret = "transient-password-must-not-appear"

    response = TestClient(app).post(
        f"/import/{batch}/reparse-descriptions",
        json={"layoutId": layout, "apply": True, "password": secret},
        headers=ORIGIN,
    )

    assert response.status_code == 422
    assert secret not in response.text
    assert "not a statement" not in response.text


def test_a_relative_link_inside_the_document_store_is_still_refused(
    live: ReparseFixture,
) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()
    original = live.stored_path(batch)
    linked = original.parent / "linked-original.pdf"
    linked.symlink_to(original)
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE document SET storage_path = %s
                WHERE id = (SELECT document_id FROM import_batch WHERE id = %s)""",
            (str(linked.relative_to(Path(os.environ["DOCUMENTS_DIR"]))), batch),
        )
    live.conn.commit()

    response = live.reparse(batch, layout, apply=True)

    assert response.status_code == 409
    assert "path" in response.json()["detail"]


def test_reparse_requires_the_owner_boundary(live: ReparseFixture) -> None:
    batch = live.committed_batch()
    layout = live.newer_layout()
    app.dependency_overrides.clear()
    try:
        response = live.reparse(batch, layout)
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner

    assert response.status_code == 401
