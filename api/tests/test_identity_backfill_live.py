"""Read-only identity backfill preview over synthetic completed imports.

The route must derive its selection from import provenance, re-read only a verified stored PDF,
and return no statement value that could identify the account.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app
from oneledger.routes import identity_backfill

from .fixtures.synthetic_pdf import Placement, build_encrypted_pdf
from .live_database import connect_to_test_database
from .test_account_from_statement_live import (
    ACCOUNT_NUMBER,
    LAYOUT_ID,
    ORIGIN,
    SALT,
    Fixture,
    _statement,
)


def _encrypted_statement(password: str) -> bytes:
    placements = [
        Placement(page=0, x=20, y=760, text="Synthetic Account Statement"),
        Placement(page=0, x=20, y=775, text="Issuer Proposal Test Institution"),
        Placement(page=0, x=20, y=745, text=f"Account Number {ACCOUNT_NUMBER}"),
        Placement(page=0, x=20, y=730, text="Currency AED"),
        Placement(page=0, x=20, y=715, text="Opening Balance 100.00"),
        Placement(page=0, x=20, y=700, text="Period 01/04/2026 to 01/04/2026"),
        Placement(page=0, x=30, y=680, text="Date"),
        Placement(page=0, x=140, y=680, text="Description"),
        Placement(page=0, x=420, y=680, text="Amount"),
        Placement(page=0, x=500, y=680, text="Balance"),
        Placement(page=0, x=30, y=650, text="01/04/2026"),
        Placement(page=0, x=140, y=650, text="SYNTHETIC PURCHASE"),
        Placement(page=0, x=420, y=650, text="-10.00"),
        Placement(page=0, x=500, y=650, text="90.00"),
        Placement(page=0, x=20, y=610, text="Closing Balance 90.00"),
    ]
    return build_encrypted_pdf(placements, password)


class BackfillFixture(Fixture):
    def __init__(self, conn: psycopg.Connection[Any], root: Path) -> None:
        super().__init__(conn)
        self.root = root
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO account
                       (household_id, owner_person_id, institution_id, name, account_type,
                        currency, country, opening_balance_date)
                     VALUES (%s, %s, %s, 'proposal test backfill', 'savings', %s, 'ZZ',
                             DATE '2026-01-01') RETURNING id""",
                (self.household_id, self.person_id, self.institution_id, self.currency),
            )
            self.account_id = cur.fetchone()[0]  # type: ignore[index]
            legacy = hashlib.sha256(f"{SALT}{ACCOUNT_NUMBER}".encode()).hexdigest()
            cur.execute(
                """INSERT INTO account_identifier
                       (household_id, account_id, kind, masked_value, visible_suffix,
                        legacy_fingerprint, legacy_fingerprint_scheme, provenance)
                     VALUES (%s, %s, 'bank_account', '****3579', '3579', decode(%s, 'hex'),
                             'legacy_sha256_v1', 'legacy_migration')""",
                (self.household_id, self.account_id, legacy),
            )
        conn.commit()

    def completed(self, statement: bytes | None = None) -> tuple[str, str, Path]:
        data = statement or _statement()
        digest = hashlib.sha256(data).hexdigest()
        relative = Path(str(self.household_id)) / f"{digest}.pdf"
        stored = self.root / relative
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO document
                       (household_id, institution_id, account_id, document_type,
                        original_filename, storage_bucket, storage_path, mime_type, byte_size,
                        sha256, parsed_status, statement_layout_id, layout_version)
                     VALUES (%s, %s, %s, 'bank_statement', 'statement.pdf', 'documents', %s,
                             'application/pdf', %s, %s, 'parsed', %s, 1)
                     RETURNING id""",
                (
                    self.household_id,
                    self.institution_id,
                    self.account_id,
                    str(relative),
                    len(data),
                    digest,
                    LAYOUT_ID,
                ),
            )
            document_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO import_batch
                       (household_id, document_id, account_id, status, statement_layout_id,
                        layout_version, completed_at)
                     VALUES (%s, %s, %s, 'completed', %s, 1, now()) RETURNING id""",
                (self.household_id, document_id, self.account_id, LAYOUT_ID),
            )
            batch_id = cur.fetchone()[0]  # type: ignore[index]
        self.conn.commit()
        return str(batch_id), str(document_id), stored

    def identity_state(self) -> list[tuple[Any, ...]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, masked_value, full_value, normalized_value, provenance,
                          source_document_id, source_layout_id, source_layout_version
                     FROM account_identifier WHERE account_id = %s ORDER BY id""",
                (self.account_id,),
            )
            return cur.fetchall()

    def preview(
        self, *, account_id: UUID | None = None, password: str | None = None
    ) -> httpx.Response:
        body: dict[str, str] = {}
        if account_id is not None:
            body["accountId"] = str(account_id)
        if password is not None:
            body["password"] = password
        return cast(
            httpx.Response,
            TestClient(app).post(
                "/account-identifiers/backfill/preview", json=body, headers=ORIGIN
            ),
        )


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[BackfillFixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = connect_to_test_database()
    fixture = BackfillFixture(conn, tmp_path)
    app.dependency_overrides[auth.require_owner] = fixture.owner
    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute(
                """DELETE FROM account_identifier ai USING account a
                     WHERE ai.account_id = a.id AND a.name LIKE 'proposal test%'"""
            )
            cur.execute("DELETE FROM account WHERE name LIKE 'proposal test%'")
            cur.execute(
                "DELETE FROM statement_layout WHERE household_id = %s"
                " AND name = 'proposal test layout'",
                (fixture.household_id,),
            )
            cur.execute("DELETE FROM institution WHERE id = %s", (fixture.institution_id,))
            cur.execute("DELETE FROM fx_rate")
        conn.commit()
        conn.close()


def test_preview_is_read_only_and_returns_only_safe_identity_state(live: BackfillFixture) -> None:
    _, document_id, stored = live.completed()
    before = live.identity_state()

    response = live.preview(account_id=live.account_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accounts"] == [
        {
            "accountId": str(live.account_id),
            "outcome": "complete",
            "documents": [
                {
                    "documentId": document_id,
                    "outcome": "complete",
                    "reason": "complete_identity",
                }
            ],
        }
    ]
    assert body["counts"] == {
        "complete": 1,
        "mask_only": 0,
        "missing": 0,
        "conflicting": 0,
        "unsupported": 0,
    }
    assert len(body["expectedState"]) == 64
    assert body["previewedAt"].endswith("Z")
    assert live.identity_state() == before
    assert ACCOUNT_NUMBER not in response.text
    assert str(stored) not in response.text
    assert "SYNTHETIC PURCHASE" not in response.text


def test_preview_uses_only_completed_provenance_and_owner_accounts(live: BackfillFixture) -> None:
    assert live.preview().json()["accounts"] == []
    missing = live.preview(account_id=uuid4())
    assert missing.status_code == 404
    assert missing.json() == {"detail": "no such account"}

    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT base_currency, secondary_currency FROM household WHERE id = %s",
            (live.household_id,),
        )
        pair = cur.fetchone()
        assert pair is not None
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
                 VALUES ('foreign backfill household', %s, %s) RETURNING id""",
            pair,
        )
        foreign_household = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO person (household_id, display_name, is_self)
                 VALUES (%s, 'foreign owner', true) RETURNING id""",
            (foreign_household,),
        )
        foreign_person = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, name, account_type, currency, country,
                    opening_balance_date)
                 VALUES (%s, %s, 'foreign backfill account', 'savings', %s, 'ZZ',
                         DATE '2026-01-01') RETURNING id""",
            (foreign_household, foreign_person, live.currency),
        )
        foreign_account = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    try:
        foreign = live.preview(account_id=foreign_account)
        assert foreign.status_code == 404
        assert str(foreign_account) not in live.preview().text
    finally:
        with live.conn.cursor() as cur:
            cur.execute("DELETE FROM account WHERE household_id = %s", (foreign_household,))
            cur.execute("DELETE FROM person WHERE household_id = %s", (foreign_household,))
            cur.execute("DELETE FROM household WHERE id = %s", (foreign_household,))
        live.conn.commit()

    live.completed()
    with live.conn.cursor() as cur:
        cur.execute("UPDATE import_batch SET status = 'cancelled'")
    live.conn.commit()
    assert live.preview().json()["accounts"] == []


def test_preview_groups_agreement_and_conflict_per_account(live: BackfillFixture) -> None:
    live.completed(_statement(amount="-10.00"))
    live.completed(_statement(amount="-11.00"))
    agreed = live.preview().json()
    assert agreed["counts"]["complete"] == 1
    assert len(agreed["accounts"][0]["documents"]) == 2

    live.completed(_statement("9999999999", amount="-12.00"))
    conflicted = live.preview().json()
    assert conflicted["counts"]["conflicting"] == 1
    assert conflicted["accounts"][0]["outcome"] == "conflicting"
    assert "account_mismatch" in {
        document["reason"] for document in conflicted["accounts"][0]["documents"]
    }


def test_preview_distinguishes_mask_only_and_missing_capture(live: BackfillFixture) -> None:
    live.completed()
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns =
                      (metadata_patterns - 'account_number') || %s::jsonb
                WHERE id = %s""",
            (
                psycopg.types.json.Json(
                    {"account_number_masked": r"Account\s*Number\s*[0-9]{6}([0-9]{4})"}
                ),
                LAYOUT_ID,
            ),
        )
    live.conn.commit()
    masked = live.preview().json()
    assert masked["accounts"][0]["outcome"] == "mask_only"
    assert masked["accounts"][0]["documents"][0]["reason"] == "source_mask_only"

    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = metadata_patterns - 'account_number_masked'
                WHERE id = %s""",
            (LAYOUT_ID,),
        )
    live.conn.commit()
    missing = live.preview().json()
    assert missing["accounts"][0]["outcome"] == "missing"
    assert missing["accounts"][0]["documents"][0]["reason"] == "identity_missing"


def test_preview_uses_newest_version_of_the_recorded_layout_name(live: BackfillFixture) -> None:
    live.completed()
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO statement_layout
                   (household_id, institution_id, account_type, name, version, fingerprints,
                    header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                    chronological_order, row_start_rule, continuation_rule, skip_line_patterns,
                    opening_balance_pattern, closing_balance_pattern, has_running_balance,
                    quirks, metadata_patterns)
                 SELECT household_id, institution_id, account_type, name, 2, fingerprints,
                        header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                        chronological_order, row_start_rule, continuation_rule,
                        skip_line_patterns, opening_balance_pattern, closing_balance_pattern,
                        has_running_balance, quirks, metadata_patterns
                   FROM statement_layout WHERE id = %s""",
            (LAYOUT_ID,),
        )
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = metadata_patterns - 'account_number'
                WHERE id = %s""",
            (LAYOUT_ID,),
        )
    live.conn.commit()

    assert live.preview().json()["accounts"][0]["outcome"] == "complete"


def test_preview_names_multiple_identity_captures_as_conflicting(live: BackfillFixture) -> None:
    live.completed(_statement(second_number="1111111111"))

    preview = live.preview().json()["accounts"][0]
    assert preview["outcome"] == "conflicting"
    assert preview["documents"][0]["reason"] == "capture_conflict"


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("missing", "source_unreadable"),
        ("hash", "source_hash_mismatch"),
        ("outside", "source_path_invalid"),
        ("symlink", "source_path_invalid"),
    ],
)
def test_preview_refuses_unverified_stored_sources(
    live: BackfillFixture, tmp_path: Path, damage: str, reason: str
) -> None:
    _, document_id, stored = live.completed()
    if damage == "missing":
        stored.unlink()
    elif damage == "hash":
        stored.write_bytes(b"not the stored statement")
    elif damage == "outside":
        outside = tmp_path.parent / f"outside-{uuid4()}.pdf"
        outside.write_bytes(_statement())
        with live.conn.cursor() as cur:
            cur.execute(
                "UPDATE document SET storage_path = %s WHERE id = %s", (str(outside), document_id)
            )
        live.conn.commit()
    else:
        target = tmp_path / "target.pdf"
        target.write_bytes(_statement())
        stored.unlink()
        stored.symlink_to(target)

    body = live.preview().json()
    assert body["accounts"][0]["outcome"] == "unsupported"
    assert body["accounts"][0]["documents"] == [
        {"documentId": document_id, "outcome": "unsupported", "reason": reason}
    ]
    if damage == "outside":
        outside.unlink()


def test_preview_refuses_incomplete_layout_and_storage_provenance(live: BackfillFixture) -> None:
    batch_id, _, _ = live.completed()
    with live.conn.cursor() as cur:
        cur.execute("UPDATE import_batch SET statement_layout_id = NULL WHERE id = %s", (batch_id,))
    live.conn.commit()
    assert live.preview().json()["accounts"][0]["documents"][0]["reason"] == (
        "layout_provenance_missing"
    )

    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE import_batch SET statement_layout_id = %s, layout_version = 1 WHERE id = %s",
            (LAYOUT_ID, batch_id),
        )
        cur.execute("UPDATE document SET storage_bucket = 'remote' ")
    live.conn.commit()
    assert live.preview().json()["accounts"][0]["documents"][0]["reason"] == ("storage_unsupported")


def test_preview_passes_password_only_to_pdf_reading(
    live: BackfillFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.completed()
    supplied: list[str | None] = []
    verified = identity_backfill.verified_pages

    @contextmanager
    def recording_pages(path: str, digest: str, password: str | None) -> Iterator[list[Any]]:
        supplied.append(password)
        with verified(path, digest, password) as pages:
            yield pages

    monkeypatch.setattr(identity_backfill, "verified_pages", recording_pages)
    response = live.preview(password="synthetic-private-password")

    assert response.status_code == 200
    assert supplied == ["synthetic-private-password"]
    assert "synthetic-private-password" not in response.text

    too_long = "p" * 4097
    refused = live.preview(password=too_long)
    assert refused.status_code == 422
    assert too_long not in refused.text


def test_expected_state_binds_password_dependent_assessment(
    live: BackfillFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.completed(_encrypted_statement("correct synthetic password"))
    monkeypatch.setattr(
        identity_backfill,
        "_now",
        lambda: datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
    )

    refused = live.preview(password="wrong synthetic password").json()
    accepted = live.preview(password="correct synthetic password").json()

    assert refused["accounts"][0]["documents"][0]["reason"] == ("password_required_or_invalid")
    assert accepted["accounts"][0]["documents"][0]["reason"] == "complete_identity"
    assert refused["expectedState"] != accepted["expectedState"]


def test_malformed_verified_pdf_is_not_reported_as_a_password_failure(
    live: BackfillFixture,
) -> None:
    live.completed(b"synthetic malformed PDF bytes")

    preview = live.preview().json()["accounts"][0]["documents"][0]

    assert preview == {
        "documentId": preview["documentId"],
        "outcome": "unsupported",
        "reason": "layout_unsupported",
    }


def test_expected_state_binds_institution_resolution(
    live: BackfillFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.completed()
    monkeypatch.setattr(
        identity_backfill,
        "_now",
        lambda: datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
    )
    before = live.preview().json()

    competing_id = uuid4()
    try:
        with live.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO institution
                       (id, household_id, name, country, institution_type)
                     VALUES (%s, %s, 'Competing Test Institution', 'ZZ', 'bank')""",
                (competing_id, live.household_id),
            )
            cur.execute(
                """INSERT INTO institution_alias
                       (household_id, institution_id, alias, alias_normalized, country,
                        provenance)
                     VALUES (%s, %s, 'Proposal Test Institution',
                             'proposal test institution', 'ZZ', 'owner_confirmed')""",
                (live.household_id, competing_id),
            )
        live.conn.commit()

        after = live.preview().json()

        assert before["accounts"][0]["documents"][0]["reason"] == "complete_identity"
        assert after["accounts"][0]["documents"][0]["reason"] == "account_mismatch"
        assert before["expectedState"] != after["expectedState"]
    finally:
        with live.conn.cursor() as cur:
            cur.execute("DELETE FROM institution_alias WHERE institution_id = %s", (competing_id,))
            cur.execute("DELETE FROM institution WHERE id = %s", (competing_id,))
        live.conn.commit()


def test_expected_state_is_stable_and_tracks_account_document_and_layout_state(
    live: BackfillFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.completed()
    instant = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(identity_backfill, "_now", lambda: instant)
    first = live.preview().json()["expectedState"]
    assert live.preview().json()["expectedState"] == first

    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE account SET updated_at = updated_at + interval '1 second' WHERE id = %s",
            (live.account_id,),
        )
    live.conn.commit()
    account_changed = live.preview().json()["expectedState"]
    assert account_changed != first

    with live.conn.cursor() as cur:
        cur.execute("UPDATE document SET byte_size = byte_size + 1")
    live.conn.commit()
    document_changed = live.preview().json()["expectedState"]
    assert document_changed != account_changed

    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO statement_layout
                   (household_id, institution_id, account_type, name, version, fingerprints,
                    header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                    chronological_order, row_start_rule, continuation_rule, skip_line_patterns,
                    opening_balance_pattern, closing_balance_pattern, has_running_balance,
                    quirks, metadata_patterns)
                 SELECT household_id, institution_id, account_type, name, 2, fingerprints,
                        header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                        chronological_order, row_start_rule, continuation_rule,
                        skip_line_patterns, opening_balance_pattern, closing_balance_pattern,
                        has_running_balance, quirks, metadata_patterns
                   FROM statement_layout WHERE id = %s""",
            (LAYOUT_ID,),
        )
    live.conn.commit()
    layout_changed = live.preview().json()["expectedState"]
    assert layout_changed != document_changed
    assert live.preview().json()["accounts"][0]["outcome"] == "complete"
