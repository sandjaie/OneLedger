"""Read-only identity backfill preview over synthetic completed imports.

The route must derive its selection from import provenance, re-read only a verified stored PDF,
and return no statement value that could identify the account.
"""

from __future__ import annotations

import hashlib
import re
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
from oneledger.db import scope_to_owner
from oneledger.main import app
from oneledger.routes import identity_backfill

from .fixtures.synthetic_pdf import Placement, build_encrypted_pdf
from .live_database import connect_to_test_database
from .live_database import test_dsn as _test_dsn
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
                          is_active, source_document_id, source_layout_id, source_layout_version
                     FROM account_identifier WHERE account_id = %s ORDER BY created_at, id""",
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

    def apply(
        self,
        preview: dict[str, Any],
        *,
        account_id: UUID | None = None,
        password: str | None = None,
    ) -> httpx.Response:
        body = {
            "accountId": str(account_id or self.account_id),
            "previewedAt": preview["previewedAt"],
            "expectedState": preview["expectedState"],
        }
        if password is not None:
            body["password"] = password
        return cast(
            httpx.Response,
            TestClient(app).post("/account-identifiers/backfill/apply", json=body, headers=ORIGIN),
        )

    def ledger_snapshot(self) -> dict[str, list[tuple[Any, ...]]]:
        tables = (
            "account",
            "transaction",
            "import_batch",
            "document",
            "card_statement",
            "transfer",
            "v_transaction_reportable",
        )
        snapshot: dict[str, list[tuple[Any, ...]]] = {}
        with self.conn.cursor() as cur:
            for table in tables:
                cur.execute(
                    f"SELECT to_jsonb(row) FROM {table} row WHERE household_id = %s"  # noqa: S608
                    " ORDER BY id",
                    (self.household_id,),
                )
                snapshot[table] = cur.fetchall()
        return snapshot

    def populate_ledger_facts(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO account
                       (household_id, owner_person_id, institution_id, name, account_type,
                        currency, country, opening_balance_date, credit_limit)
                     VALUES (%s, %s, %s, 'proposal test preservation card', 'credit_card',
                             %s, 'ZZ', DATE '2026-01-01', 1000) RETURNING id""",
                (self.household_id, self.person_id, self.institution_id, self.currency),
            )
            card_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO transaction
                       (household_id, account_id, booked_date, description_raw, amount, currency,
                        fx_rate_date, fx_rate_to_base, fx_rate_to_secondary, amount_base,
                        amount_secondary, txn_type, source, dedupe_hash)
                     VALUES (%s, %s, DATE '2026-04-01', 'SYNTHETIC PRESERVATION', -5, %s,
                             DATE '2026-04-01', 1, 1, -5, -5, 'expense', 'manual', %s)""",
                (self.household_id, self.account_id, self.currency, uuid4().hex * 2),
            )
            cur.execute(
                """INSERT INTO card_statement
                       (household_id, account_id, period_start, period_end, statement_date,
                        payment_due_date, currency, opening_balance, closing_balance,
                        statement_balance)
                     VALUES (%s, %s, DATE '2026-04-01', DATE '2026-04-30', DATE '2026-04-30',
                             DATE '2026-05-20', %s, 0, -20, 20)""",
                (self.household_id, card_id, self.currency),
            )
            cur.execute(
                """INSERT INTO transfer
                       (household_id, source_account_id, destination_account_id, source_amount,
                        source_currency, destination_amount, destination_currency, transfer_date,
                        transfer_kind, match_method)
                     VALUES (%s, %s, %s, 7, %s, 7, %s, DATE '2026-04-02',
                             'internal_same_currency', 'synthetic_preservation')""",
                (self.household_id, self.account_id, card_id, self.currency, self.currency),
            )
        self.conn.commit()


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
            cur.execute("DELETE FROM transfer WHERE household_id = %s", (fixture.household_id,))
            cur.execute(
                "DELETE FROM card_statement WHERE household_id = %s", (fixture.household_id,)
            )
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
            cur.execute(
                "DELETE FROM institution_alias WHERE institution_id = %s",
                (fixture.institution_id,),
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
            "eligible": True,
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
    assert body["eligibleCount"] == 1
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


def test_apply_requires_a_fresh_preview_and_refuses_cross_household_accounts(
    live: BackfillFixture,
) -> None:
    live.completed()
    before = live.identity_state()

    missing = TestClient(app).post(
        "/account-identifiers/backfill/apply",
        json={"accountId": str(live.account_id)},
        headers=ORIGIN,
    )
    assert missing.status_code == 422

    preview = live.preview().json()
    forged = {**preview, "expectedState": "0" * 64}
    stale = live.apply(forged)
    assert stale.status_code == 409
    assert stale.json() == {"detail": "identity backfill preview is stale"}
    assert live.identity_state() == before

    foreign = live.apply(preview, account_id=uuid4())
    assert foreign.status_code == 404
    assert live.identity_state() == before


def test_apply_upgrades_legacy_identity_once_and_preserves_every_ledger_fact(
    live: BackfillFixture,
) -> None:
    _, document_id, _ = live.completed()
    live.populate_ledger_facts()
    before_ledger = live.ledger_snapshot()
    preview = live.preview().json()
    assert preview["eligibleCount"] == 1

    response = live.apply(preview)

    assert response.status_code == 200, response.text
    assert response.json() == {"accountId": str(live.account_id), "status": "applied"}
    assert ACCOUNT_NUMBER not in response.text
    assert live.ledger_snapshot() == before_ledger
    rows = live.identity_state()
    assert len(rows) == 2
    by_provenance = {row[5]: row for row in rows}
    assert by_provenance["legacy_migration"][6] is False
    statement = by_provenance["statement_backfill"]
    assert statement[2] == "****3579"
    assert statement[3] == ACCOUNT_NUMBER
    assert statement[4] == ACCOUNT_NUMBER
    assert statement[6] is True
    assert str(statement[7]) == document_id
    assert statement[8] == LAYOUT_ID
    assert statement[9] == 1

    second = live.preview().json()
    assert second["eligibleCount"] == 0
    unchanged = live.apply(second)
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "unchanged"
    assert live.identity_state() == rows

    doctor = TestClient(app).get("/doctor").json()
    identity = next(
        section for section in doctor["sections"] if section["title"] == "Account identities"
    )
    assert identity["findings"] == [
        {"level": "ok", "message": "Every account identity is on the current local model."}
    ]


def test_apply_confirms_unmatched_pdf_issuer_for_the_imported_account(
    live: BackfillFixture,
) -> None:
    live.completed(_statement(institution="Unseen Test Institution"))

    preview = live.preview().json()

    assert preview["accounts"][0]["outcome"] == "complete"
    assert preview["eligibleCount"] == 1
    response = live.apply(preview)
    assert response.status_code == 200, response.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT institution_id, alias, provenance FROM institution_alias
                WHERE household_id = %s AND alias_normalized = 'unseen test institution'""",
            (live.household_id,),
        )
        assert cur.fetchone() == (
            live.institution_id,
            "Unseen Test Institution",
            "owner_confirmed",
        )
    assert live.preview().json()["eligibleCount"] == 0


def test_apply_can_confirm_an_issuer_alias_when_the_source_identity_is_mask_only(
    live: BackfillFixture,
) -> None:
    live.completed(_statement(institution="Unseen Test Institution"))
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
    before = live.identity_state()

    preview = live.preview().json()

    assert preview["accounts"][0]["outcome"] == "mask_only"
    assert preview["eligibleCount"] == 1
    response = live.apply(preview)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "applied"
    assert live.identity_state() == before
    assert live.preview().json()["eligibleCount"] == 0


def _configure_historical_card_mask(live: BackfillFixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE account
                  SET account_type = 'credit_card', credit_limit = 1000
                WHERE id = %s""",
            (live.account_id,),
        )
        cur.execute(
            """UPDATE account_identifier
                  SET kind = 'card', masked_value = '****0000', visible_suffix = '0000',
                      legacy_fingerprint = NULL, legacy_fingerprint_scheme = NULL
                WHERE account_id = %s""",
            (live.account_id,),
        )
        cur.execute(
            """UPDATE statement_layout
                  SET account_type = 'credit_card',
                      metadata_patterns =
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
    live.completed(_statement("1234562468"))


def test_apply_adopts_a_different_source_mask_for_its_historical_card_account(
    live: BackfillFixture,
) -> None:
    _configure_historical_card_mask(live)
    before = live.identity_state()

    preview = live.preview().json()

    assert preview["accounts"][0]["outcome"] == "mask_only"
    assert preview["accounts"][0]["eligible"] is True
    assert preview["eligibleCount"] == 1
    response = live.apply(preview)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "applied"
    rows = live.identity_state()
    assert len(rows) == 2
    legacy, statement = rows
    assert legacy[6] is False
    assert statement[1:7] == (
        "card",
        "****2468",
        None,
        None,
        "statement_backfill",
        True,
    )
    assert statement[7] is not None
    assert statement[8:10] == (LAYOUT_ID, 1)
    assert live.preview().json()["eligibleCount"] == 0
    assert before != rows


def test_mask_adoption_refuses_when_another_account_already_uses_the_source_suffix(
    live: BackfillFixture,
) -> None:
    _configure_historical_card_mask(live)
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, institution_id, name, account_type,
                    currency, country, opening_balance_date, credit_limit)
                 VALUES (%s, %s, %s, 'proposal test competing card', 'credit_card',
                         %s, 'ZZ', DATE '2026-01-01', 1000) RETURNING id""",
            (live.household_id, live.person_id, live.institution_id, live.currency),
        )
        competing_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix, provenance)
                 VALUES (%s, %s, 'card', '****2468', '2468', 'owner_entered')""",
            (live.household_id, competing_id),
        )
    live.conn.commit()
    before = live.identity_state()

    preview = live.preview().json()

    assert preview["accounts"][0]["outcome"] == "conflicting"
    assert preview["accounts"][0]["documents"][0]["reason"] == "account_mismatch"
    assert preview["eligibleCount"] == 0
    response = live.apply(preview)
    assert response.status_code == 409
    assert live.identity_state() == before


def test_mask_adoption_refuses_when_the_pdf_has_no_currency_evidence(
    live: BackfillFixture,
) -> None:
    _configure_historical_card_mask(live)
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = metadata_patterns - 'currency'
                WHERE id = %s""",
            (LAYOUT_ID,),
        )
    live.conn.commit()
    before = live.identity_state()

    preview = live.preview().json()

    assert preview["accounts"][0]["outcome"] == "conflicting"
    assert preview["accounts"][0]["documents"][0]["reason"] == "account_mismatch"
    assert preview["eligibleCount"] == 0
    response = live.apply(preview)
    assert response.status_code == 409
    assert live.identity_state() == before


@pytest.mark.parametrize(
    ("source", "normalized"),
    [
        ("AB12-CD34-EF56", "AB12CD34EF56"),
        ("AB١٢-CD٣٤-EF٥٦", "AB12CD34EF56"),
    ],
)
def test_backfill_never_stores_a_complete_account_identifier_as_an_issuer_alias(
    live: BackfillFixture,
    caplog: pytest.LogCaptureFixture,
    source: str,
    normalized: str,
) -> None:
    with live.conn.cursor() as cur:
        cur.execute("DELETE FROM account_identifier WHERE account_id = %s", (live.account_id,))
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix,
                    full_value, normalized_value, provenance)
                 VALUES (%s, %s, 'bank_account', '****EF56', 'EF56', %s, %s,
                         'owner_entered')""",
            (live.household_id, live.account_id, source, normalized),
        )
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = metadata_patterns || %s::jsonb
                WHERE id = %s""",
            (
                psycopg.types.json.Json(
                    {
                        "institution_name": rf"Issuer\s*({re.escape(source)})",
                        "account_number": rf"Account\s*Number\s*({re.escape(source)})",
                    }
                ),
                LAYOUT_ID,
            ),
        )
    live.conn.commit()
    live.completed(_statement(number=source, institution=source))

    preview_response = live.preview()
    preview = preview_response.json()

    assert preview["accounts"][0]["outcome"] == "conflicting"
    assert preview["accounts"][0]["documents"][0]["reason"] == "institution_unresolved"
    assert preview["eligibleCount"] == 0
    refused = live.apply(preview)
    assert refused.status_code == 409
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM institution_alias WHERE household_id = %s",
            (live.household_id,),
        )
        assert cur.fetchone() == (0,)
    for private_value in {source, normalized}:
        assert private_value not in preview_response.text
        assert private_value not in refused.text
        assert private_value not in caplog.text


def test_apply_refuses_the_whole_account_when_documents_disagree(
    live: BackfillFixture,
) -> None:
    live.completed()
    live.completed(_statement("9999999999", amount="-11.00"))
    preview = live.preview().json()
    before = live.identity_state()

    response = live.apply(preview)

    assert response.status_code == 409
    assert response.json() == {"detail": "identity backfill source documents do not agree"}
    assert live.identity_state() == before


def test_apply_unique_race_rolls_back_legacy_deactivation(
    live: BackfillFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    live.completed()
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, institution_id, name, account_type,
                    currency, country, opening_balance_date)
                 VALUES (%s, %s, %s, 'proposal test racing account', 'savings', %s, 'ZZ',
                         DATE '2026-01-01') RETURNING id""",
            (live.household_id, live.person_id, live.institution_id, live.currency),
        )
        competing_account = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    preview = live.preview().json()
    writer = cast(Any, vars(identity_backfill)["_replace_account_identifier"])

    def racing_writer(cur: psycopg.Cursor[Any], *args: object, **kwargs: object) -> None:
        with psycopg.connect(_test_dsn()) as rival, rival.cursor() as rival_cur:
            scope_to_owner(rival, live.owner().user_id)
            # Model the privileged out-of-band race the service-level household lock cannot
            # serialize. Unique indexes remain active while FK trigger lock acquisition is skipped.
            rival_cur.execute("SET LOCAL session_replication_role = replica")
            rival_cur.execute(
                """INSERT INTO account_identifier
                       (household_id, account_id, kind, masked_value, visible_suffix,
                        full_value, normalized_value, provenance)
                     VALUES (%s, %s, 'bank_account', '****3579', '3579', %s, %s,
                             'owner_entered')""",
                (live.household_id, competing_account, ACCOUNT_NUMBER, ACCOUNT_NUMBER),
            )
        writer(cur, *args, **kwargs)

    monkeypatch.setattr(identity_backfill, "_replace_account_identifier", racing_writer)

    response = live.apply(preview)

    assert response.status_code == 409
    assert response.json() == {"detail": "account identity already exists"}
    [legacy] = live.identity_state()
    assert legacy[5] == "legacy_migration"
    assert legacy[6] is True
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT account_id, normalized_value FROM account_identifier
                WHERE account_id = %s AND is_active""",
            (competing_account,),
        )
        assert cur.fetchone() == (competing_account, ACCOUNT_NUMBER)


def test_apply_rechecks_password_and_concurrent_state_before_writing(
    live: BackfillFixture,
) -> None:
    live.completed(_encrypted_statement("correct synthetic password"))
    preview = live.preview(password="correct synthetic password").json()
    before = live.identity_state()

    wrong_password = live.apply(preview, password="wrong synthetic password")
    assert wrong_password.status_code == 409
    assert wrong_password.json() == {"detail": "identity backfill preview is stale"}
    assert live.identity_state() == before

    fresh = live.preview(password="correct synthetic password").json()
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE account SET updated_at = updated_at + interval '1 second' WHERE id = %s",
            (live.account_id,),
        )
    live.conn.commit()

    concurrent = live.apply(fresh, password="correct synthetic password")
    assert concurrent.status_code == 409
    assert concurrent.json() == {"detail": "identity backfill preview is stale"}
    assert live.identity_state() == before
