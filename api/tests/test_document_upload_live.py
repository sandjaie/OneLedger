"""Live owner-scoped tests for the owner document upload (Phase 3.4 Task 2): a synthetic PDF
stored and listed with `importKind: "upload"` and its title, the same-bytes reuse/conflict rule,
a non-PDF and an oversized file refused, a statement type refused, and the record document link
routes' idempotency and household isolation. Skipped when no database is reachable. Every name,
label and byte string is synthetic — never a real PDF, filename or identifier."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}

# Just enough of a PDF for the header check — nothing here is ever parsed as a real document.
_PDF_HEADER = b"%PDF-1.4\n%synthetic\n"
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class UploadFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    household_id: UUID
    other_household_id: UUID
    record_id: UUID
    other_record_id: UUID


def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[UploadFixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "upload-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-upload-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base = str(found[0]).strip()
        for household, name in (
            (household_id, "upload household"),
            (other_household_id, "other upload household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, (SELECT max(code) FROM currency))",
                (household, name, base),
            )
        for person, household, name in (
            (person_id, household_id, "upload owner"),
            (other_person_id, other_household_id, "other upload owner"),
        ):
            cur.execute(
                "INSERT INTO person (id, household_id, display_name, is_self) "
                "VALUES (%s, %s, %s, true)",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, other_owner.email),
        ):
            cur.execute(
                "INSERT INTO app_user (id, household_id, person_id, email, role) "
                "VALUES (%s, %s, %s, %s, 'owner')",
                (user, household, person, email),
            )
        record_id, other_record_id = uuid4(), uuid4()
        cur.execute(
            """INSERT INTO register_record (id, household_id, idempotency_key, kind, title,
                                             key_date)
               VALUES (%s, %s, %s, 'administration', 'Synthetic deadline', '2027-01-01')""",
            (record_id, household_id, uuid4()),
        )
        cur.execute(
            """INSERT INTO register_record (id, household_id, idempotency_key, kind, title,
                                             key_date)
               VALUES (%s, %s, %s, 'administration', 'Other synthetic deadline', '2027-01-01')""",
            (other_record_id, other_household_id, uuid4()),
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield UploadFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            household_id=household_id,
            other_household_id=other_household_id,
            record_id=record_id,
            other_record_id=other_record_id,
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE register_record, document CASCADE")
        connection.commit()
        connection.close()


def _upload(
    data: bytes, document_type: str = "tax_document", title: str = "Synthetic tax document"
) -> httpx.Response:
    response: httpx.Response = client().post(
        "/documents",
        files={"file": ("ignored.pdf", data, "application/pdf")},
        data={"documentType": document_type, "title": title},
        headers=ORIGIN,
    )
    return response


def test_upload_is_stored_and_listed_as_an_upload_with_its_title(live: UploadFixture) -> None:
    uploaded = _upload(_PDF_HEADER + b"one", title="Synthetic policy schedule")
    assert uploaded.status_code == 201, uploaded.text
    row = uploaded.json()
    assert row["documentType"] == "tax_document"
    assert row["importKind"] == "upload"
    assert row["title"] == "Synthetic policy schedule"
    assert row["importId"] is None
    assert row["importStatus"] is None

    listed = client().get("/documents", headers=ORIGIN)
    assert listed.status_code == 200, listed.text
    by_id = {r["id"]: r for r in listed.json()["documents"]}
    assert by_id[row["id"]]["importKind"] == "upload"
    assert by_id[row["id"]]["title"] == "Synthetic policy schedule"

    # No uploaded filename ever reaches the wire.
    assert "ignored.pdf" not in listed.text


def test_same_bytes_same_type_returns_the_same_row(live: UploadFixture) -> None:
    data = _PDF_HEADER + b"two"
    first = _upload(data)
    assert first.status_code == 201, first.text
    second = _upload(data)
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]


def test_same_bytes_another_type_is_409(live: UploadFixture) -> None:
    data = _PDF_HEADER + b"three"
    first = _upload(data, document_type="tax_document")
    assert first.status_code == 201, first.text
    conflicting = _upload(data, document_type="receipt")
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()["detail"] == "this file is already in the document library"


def test_non_pdf_is_refused(live: UploadFixture) -> None:
    response = _upload(b"this is not a pdf")
    assert response.status_code == 422, response.text


def test_empty_file_is_refused(live: UploadFixture) -> None:
    response = _upload(b"")
    assert response.status_code == 422, response.text


def test_oversized_file_is_refused(live: UploadFixture) -> None:
    oversized = _PDF_HEADER + (b"0" * _MAX_UPLOAD_BYTES)
    response = _upload(oversized)
    assert response.status_code == 422, response.text
    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM document WHERE household_id = %s", (live.household_id,))
        row = cur.fetchone()
        assert row is not None and row[0] == 0


def test_a_statement_type_is_refused(live: UploadFixture) -> None:
    response = _upload(_PDF_HEADER + b"four", document_type="bank_statement")
    assert response.status_code == 422, response.text


def _link(record_id: UUID, document_id: str) -> httpx.Response:
    response: httpx.Response = client().put(
        f"/register/{record_id}/documents/{document_id}", headers=ORIGIN
    )
    return response


def _unlink(record_id: UUID, document_id: str) -> httpx.Response:
    response: httpx.Response = client().delete(
        f"/register/{record_id}/documents/{document_id}", headers=ORIGIN
    )
    return response


def test_link_and_unlink_are_idempotent(live: UploadFixture) -> None:
    document_id = _upload(_PDF_HEADER + b"five").json()["id"]

    linked = _link(live.record_id, document_id)
    assert linked.status_code == 200, linked.text
    assert [d["documentId"] for d in linked.json()["documents"]] == [document_id]

    relinked = _link(live.record_id, document_id)
    assert relinked.status_code == 200, relinked.text
    assert len(relinked.json()["documents"]) == 1

    unlinked = _unlink(live.record_id, document_id)
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["documents"] == []

    reunlinked = _unlink(live.record_id, document_id)
    assert reunlinked.status_code == 200, reunlinked.text
    assert reunlinked.json()["documents"] == []


def test_a_statement_document_cannot_be_linked(live: UploadFixture) -> None:
    """Only a document of one of the six upload types may be linked to a record — an imported
    statement is the household's own document and still refuses with 422, so the rule is not the
    404 the foreign-document case already proves."""
    statement_id = uuid4()
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO document (id, household_id, document_type, original_filename,
                     storage_bucket, storage_path, mime_type, byte_size, sha256, parsed_status)
               VALUES (%s, %s, 'bank_statement', 'synthetic.pdf', 'documents',
                       'synthetic/statement.pdf', 'application/pdf', 10,
                       encode(gen_random_bytes(32), 'hex'), 'parsed')""",
            (statement_id, live.household_id),
        )
    live.connection.commit()

    refused = _link(live.record_id, str(statement_id))
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "only an uploaded document can be linked to a record"

    listed = client().get("/register", headers=ORIGIN).json()["records"]
    assert next(r for r in listed if r["id"] == str(live.record_id))["documents"] == []


def test_foreign_document_cannot_be_seen_or_linked(live: UploadFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign_document_id = _upload(_PDF_HEADER + b"six").json()["id"]
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    my_documents = client().get("/documents", headers=ORIGIN).json()
    assert foreign_document_id not in {r["id"] for r in my_documents["documents"]}

    not_found = _link(live.record_id, foreign_document_id)
    assert not_found.status_code == 404, not_found.text

    unrelated_record = _link(live.other_record_id, foreign_document_id)
    assert unrelated_record.status_code == 404, unrelated_record.text
