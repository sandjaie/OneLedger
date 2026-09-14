"""Phase 1.8 Task 2: the document library and the owner-only original download.

`GET /documents` reads a document however it arrived — a statement batch, an investment import
or a payslip — and never a private column. `GET /documents/{id}/original` proves the stored bytes
before it serves them, through the same helper the description re-read already trusted.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.db import scope_to_owner
from oneledger.main import app

from .test_import_review_live import Staged, _all_row_ids, _connect
from .test_ingest_route_live import ORIGIN, SALT, _clean, _statement


class DocumentsFixture(Staged):
    def committed_batch(self) -> str:
        batch = self.stage(statement=_statement())
        rows = _all_row_ids(self, batch)
        accepted = self.patch(batch, {"rowIds": rows, "decision": "accepted"})
        assert accepted.status_code == 200, accepted.text
        committed = self.commit(batch)
        assert committed.status_code == 200, committed.text
        return batch

    def cancel_and_restage(self) -> tuple[str, str]:
        """Two batches on one document — the same bytes both times — an abandoned attempt,
        then the real import."""
        statement = _statement()
        first = self.stage(statement=statement)
        assert self.cancel(first).status_code == 200
        second = self.stage(statement=statement)
        rows = _all_row_ids(self, second)
        accepted = self.patch(second, {"rowIds": rows, "decision": "accepted"})
        assert accepted.status_code == 200, accepted.text
        committed = self.commit(second)
        assert committed.status_code == 200, committed.text
        return first, second

    def document_for_batch(self, batch: str) -> UUID:
        with self.conn.cursor() as cur:
            cur.execute("SELECT document_id FROM import_batch WHERE id = %s", (batch,))
            return UUID(str(cur.fetchone()[0]))  # type: ignore[index]

    def batch_reconciliation_status(self, batch: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT reconciliation_status FROM import_batch WHERE id = %s", (batch,))
            found = cur.fetchone()[0]  # type: ignore[index]
            return None if found is None else str(found)

    def investment_document(self) -> tuple[UUID, UUID]:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO document (
                       household_id, document_type, original_filename, storage_bucket,
                       storage_path, mime_type, byte_size, sha256, page_count, parsed_status,
                       is_password_protected)
                   VALUES (%s, 'investment_statement', 'investment.pdf', 'documents', %s,
                           'application/pdf', 12, %s, 2, 'parsed', false)
                   RETURNING id""",
                (
                    self.household_id,
                    f"synthetic/{uuid4()}.pdf",
                    hashlib.sha256(uuid4().bytes).hexdigest(),
                ),
            )
            document_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
            cur.execute(
                """INSERT INTO investment_import
                       (household_id, document_id, source_contract, extraction_version, status)
                   VALUES (%s, %s, 'synthetic_broker', '1', 'awaiting_review')
                   RETURNING id""",
                (self.household_id, document_id),
            )
            import_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
        self.conn.commit()
        return document_id, import_id

    def payslip_document(self) -> UUID:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO document (
                       household_id, document_type, original_filename, storage_bucket,
                       storage_path, mime_type, byte_size, sha256, page_count, parsed_status,
                       is_password_protected)
                   VALUES (%s, 'payslip', 'payslip.pdf', 'documents', %s, 'application/pdf', 4,
                           %s, 1, 'parsed', false)
                   RETURNING id""",
                (
                    self.household_id,
                    f"synthetic/{uuid4()}.pdf",
                    hashlib.sha256(uuid4().bytes).hexdigest(),
                ),
            )
            document_id = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
            scope_to_owner(self.conn, self.user_id)
            cur.execute(
                """SELECT recorded_payslip_id FROM record_payslip(
                       %s, NULL, %s, %s, %s, %s, %s, %s, %s::payslip_line_kind[], %s, %s)""",
                (
                    str(uuid4()),
                    document_id,
                    date(2026, 3, 1),
                    self.base,
                    Decimal("1000"),
                    Decimal("200"),
                    Decimal("800"),
                    ["earning", "deduction"],
                    ["Salary", "Tax"],
                    [Decimal("1000"), Decimal("200")],
                ),
            )
            assert cur.fetchone() is not None
        self.conn.commit()
        return document_id

    def documents(self, **params: str) -> httpx.Response:
        response: httpx.Response = TestClient(app).get("/documents", params=params, headers=ORIGIN)
        return response

    def original(self, document_id: UUID) -> httpx.Response:
        response: httpx.Response = TestClient(app).get(
            f"/documents/{document_id}/original", headers=ORIGIN
        )
        return response

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
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DocumentsFixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        fixture = DocumentsFixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
        conn.commit()
        conn.close()


def test_filters_period_overlap_and_document_kinds(live: DocumentsFixture) -> None:
    _cancelled, batch = live.cancel_and_restage()
    document_id = live.document_for_batch(batch)
    investment_id, investment_import_id = live.investment_document()
    payslip_id = live.payslip_document()

    everything = live.documents()
    assert everything.status_code == 200, everything.text
    body = everything.json()
    by_id = {row["id"]: row for row in body["documents"]}
    assert {str(document_id), str(investment_id), str(payslip_id)} <= by_id.keys()
    assert body["total"] == len(body["documents"])

    statement_row = by_id[str(document_id)]
    assert statement_row["documentType"] == "bank_statement"
    assert statement_row["importKind"] == "statement"
    # The newest batch wins, not the cancelled first attempt.
    assert statement_row["importStatus"] == "completed"
    assert statement_row["reconciliationStatus"] == live.batch_reconciliation_status(batch)
    assert statement_row["reconciliationStatus"] is not None
    assert statement_row["accountId"] == str(live.account_id)
    assert statement_row["accountNumberMasked"] == "****7890"
    assert statement_row["period"] == {
        "start": "2026-03-01",
        "end": "2026-03-02",
        "isDerived": True,
    }

    investment_row = by_id[str(investment_id)]
    assert investment_row["importKind"] == "investment"
    assert investment_row["importId"] == str(investment_import_id)
    assert investment_row["importStatus"] == "awaiting_review"
    assert investment_row["reconciliationStatus"] is None
    assert investment_row["accountId"] is None
    assert investment_row["period"] == {"start": None, "end": None, "isDerived": None}

    payslip_row = by_id[str(payslip_id)]
    assert payslip_row["importKind"] == "payslip"
    assert payslip_row["importId"] is None
    assert payslip_row["importStatus"] == "completed"
    assert payslip_row["reconciliationStatus"] is None
    assert payslip_row["accountId"] is None
    assert payslip_row["period"] == {"start": None, "end": None, "isDerived": None}

    # Period overlap: a filter excludes the payslip and investment documents outright, since
    # neither carries a period, and excludes a statement document whose period does not overlap.
    inside = live.documents(periodFrom="2026-03-01", periodTo="2026-03-31")
    assert {row["id"] for row in inside.json()["documents"]} == {str(document_id)}

    outside = live.documents(periodFrom="2026-04-01", periodTo="2026-04-30")
    assert str(document_id) not in {row["id"] for row in outside.json()["documents"]}

    inverted = live.documents(periodFrom="2026-04-01", periodTo="2026-03-01")
    assert inverted.status_code == 422

    by_account = live.documents(accountId=str(live.account_id))
    assert {row["id"] for row in by_account.json()["documents"]} == {str(document_id)}

    by_type = live.documents(documentType="payslip")
    assert {row["id"] for row in by_type.json()["documents"]} == {str(payslip_id)}

    assert live.documents(documentType="not_a_real_type").status_code == 422

    by_status = live.documents(importStatus="awaiting_review")
    assert {row["id"] for row in by_status.json()["documents"]} == {str(investment_id)}

    assert live.documents(importStatus="not_a_real_status").status_code == 422

    # No private column reaches the wire: not the real account number, not the masked filename,
    # not the on-disk path.
    text = everything.text
    for sentinel in ("1234567890", "statement-4111", str(live.stored_path(batch))):
        assert sentinel not in text


def test_foreign_household_gets_an_empty_list_and_a_404_original(
    live: DocumentsFixture,
) -> None:
    batch = live.committed_batch()
    document_id = live.document_for_batch(batch)

    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=live.other_user_id,
        household_id=live.other_household_id,
        session_id=UUID(int=9),
        email="other@e.com",
    )
    try:
        foreign_list = TestClient(app).get("/documents", headers=ORIGIN)
        assert foreign_list.status_code == 200, foreign_list.text
        assert str(document_id) not in {row["id"] for row in foreign_list.json()["documents"]}

        foreign_original = TestClient(app).get(f"/documents/{document_id}/original", headers=ORIGIN)
        assert foreign_original.status_code == 404
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner

    assert live.original(uuid4()).status_code == 404


def test_original_bytes_and_headers_for_a_relative_and_an_absolute_path(
    live: DocumentsFixture,
) -> None:
    batch = live.committed_batch()
    document_id = live.document_for_batch(batch)
    original_bytes = live.stored_path(batch).read_bytes()

    absolute = live.original(document_id)
    assert absolute.status_code == 200, absolute.text
    assert absolute.content == original_bytes
    assert absolute.headers["content-type"] == "application/pdf"
    assert absolute.headers["content-disposition"] == 'attachment; filename="document.pdf"'
    assert absolute.headers["cache-control"] == "no-store"
    assert absolute.headers["pragma"] == "no-cache"
    assert absolute.headers["x-content-type-options"] == "nosniff"

    root = Path(os.environ["DOCUMENTS_DIR"])
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE document SET storage_path = %s WHERE id = %s",
            (str(live.stored_path(batch).relative_to(root)), document_id),
        )
    live.conn.commit()

    relative = live.original(document_id)
    assert relative.status_code == 200, relative.text
    assert relative.content == original_bytes


def test_original_refuses_a_symlink_an_escape_a_tampered_hash_and_an_unset_store(
    live: DocumentsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = live.committed_batch()
    document_id = live.document_for_batch(batch)
    original_path = live.stored_path(batch)
    with live.conn.cursor() as cur:
        cur.execute("SELECT sha256 FROM document WHERE id = %s", (document_id,))
        real_sha256 = str(cur.fetchone()[0])  # type: ignore[index]

    linked = original_path.parent / "linked-original.pdf"
    linked.symlink_to(original_path)
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE document SET storage_path = %s WHERE id = %s", (str(linked), document_id)
        )
    live.conn.commit()
    assert live.original(document_id).status_code == 409

    outside = Path(os.environ["DOCUMENTS_DIR"]).parent / f"escaped-{uuid4()}.pdf"
    outside.write_bytes(b"not stored under the documents root")
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE document SET storage_path = %s WHERE id = %s", (str(outside), document_id)
        )
    live.conn.commit()
    assert live.original(document_id).status_code == 409
    outside.unlink()

    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE document SET storage_path = %s, sha256 = %s WHERE id = %s",
            (str(original_path), "0" * 64, document_id),
        )
    live.conn.commit()
    assert live.original(document_id).status_code == 409

    with live.conn.cursor() as cur:
        cur.execute("UPDATE document SET storage_bucket = 'other' WHERE id = %s", (document_id,))
    live.conn.commit()
    assert live.original(document_id).status_code == 409

    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE document SET storage_bucket = 'documents', sha256 = %s WHERE id = %s",
            (real_sha256, document_id),
        )
    live.conn.commit()
    monkeypatch.delenv("DOCUMENTS_DIR", raising=False)
    assert live.original(document_id).status_code == 409


def test_reads_require_the_owner_boundary(live: DocumentsFixture) -> None:
    batch = live.committed_batch()
    document_id = live.document_for_batch(batch)
    app.dependency_overrides.clear()
    try:
        assert TestClient(app).get("/documents", headers=ORIGIN).status_code == 401
        assert live.original(document_id).status_code == 401
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
