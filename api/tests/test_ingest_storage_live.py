"""Layer 1's query, against a real database.

`refuse_if_already_imported` is what makes "the same file twice imports once" true, and a stub
cursor that records parameters and discards the SQL cannot check it: a table-name typo, a
reversed ORDER BY and a LEFT JOIN turned inner all pass such a stub. Everything here runs
inside a transaction that rolls back; nothing persists. Skipped when no database is reachable.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
import pytest

from oneledger.ingest.storage import DuplicateDocument, refuse_if_already_imported

SHA = "0" * 64
OTHER_SHA = "1" * 64


def _connect() -> psycopg.Connection:
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("no database credentials in the environment")
    from oneledger.db import connect

    try:
        return connect()
    except psycopg.OperationalError:
        pytest.skip("database unreachable")


def _fixture(cur: psycopg.Cursor[Any]) -> tuple[UUID, UUID]:
    """A household and an account to hang a batch on. The account is created here rather than
    reused, because a fresh install has none and skipping would make this proof vacuous — the
    whole reason these tests exist is that a stub cursor could not check the SQL."""
    cur.execute("SELECT id, base_currency FROM household LIMIT 1")
    household = cur.fetchone()
    if household is None:
        pytest.skip("no seeded household")
    cur.execute("SELECT id FROM person WHERE household_id = %s LIMIT 1", (household[0],))
    person = cur.fetchone()
    if person is None:
        pytest.skip("no seeded person")
    # `account.country` is NOT NULL and `person.tax_residency` is not: V2 seeds it NULL and
    # only V7's configuration call fills it, so deriving the country from the person breaks on
    # a freshly migrated database. ZZ is ISO 3166-1 user-assigned — reserved for private use
    # and not any country's code, which is also what ADR-0012 wants of a fixture.
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                country, opening_balance_date, current_balance)
           VALUES (%s, %s, 'storage test', 'savings', %s, 'ZZ', DATE '2026-01-01', 0)
           RETURNING id""",
        (household[0], person[0], household[1]),
    )
    return household[0], UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def _document(cur: psycopg.Cursor[Any], household: UUID, sha: str, on: date) -> UUID:
    cur.execute(
        """INSERT INTO document (household_id, document_type, original_filename, storage_bucket,
                                 storage_path, mime_type, byte_size, sha256, uploaded_at)
           VALUES (%s, 'bank_statement', 'masked.pdf', 'documents', %s,
                   'application/pdf', 1, %s, %s) RETURNING id""",
        (household, f"{sha}.pdf", sha, on),
    )
    return UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def _batch(cur: psycopg.Cursor[Any], household: UUID, account: UUID, doc: UUID, at: str) -> UUID:
    cur.execute(
        """INSERT INTO import_batch (household_id, account_id, document_id, created_at)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (household, account, doc, at),
    )
    return UUID(str(cur.fetchone()[0]))  # type: ignore[index]


def test_the_earlier_batch_is_the_one_named() -> None:
    """§4.7 asks for the earlier import, and a reversed ORDER BY names the later one — which
    reads as a different, more recent mistake than the one the owner actually made."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            household, account = _fixture(cur)
            document = _document(cur, household, SHA, date(2026, 2, 1))
            earlier = _batch(cur, household, account, document, "2026-02-01T09:00:00Z")
            later = _batch(cur, household, account, document, "2026-03-01T09:00:00Z")

            with pytest.raises(DuplicateDocument) as raised:
                refuse_if_already_imported(cur, household, SHA)

            message = str(raised.value)
            assert "2026-02-01" in message, "the date the file first arrived"
            assert str(earlier) in message
            assert str(later) not in message, "the earlier import, not the most recent one"
    finally:
        conn.rollback()
        conn.close()


def test_a_document_with_no_batch_yet_is_still_a_duplicate() -> None:
    """A parse that stored the blob and never reached staging must not let the same file in
    again — an inner join here would silently readmit it."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            household, _ = _fixture(cur)
            _document(cur, household, SHA, date(2026, 2, 1))
            with pytest.raises(DuplicateDocument, match="2026-02-01"):
                refuse_if_already_imported(cur, household, SHA)
    finally:
        conn.rollback()
        conn.close()


def test_another_households_batch_is_never_named() -> None:
    """Nothing in the schema ties a batch to its document's household, and the service
    bypasses RLS — so this query is the boundary, not a convenience over one."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            household, account = _fixture(cur)
            cur.execute(
                "INSERT INTO household (name, base_currency, secondary_currency)"
                " SELECT 'Other', base_currency, secondary_currency FROM household WHERE id = %s"
                " RETURNING id",
                (household,),
            )
            other = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
            document = _document(cur, household, SHA, date(2026, 2, 1))
            intruder = _batch(cur, other, account, document, "2026-01-01T09:00:00Z")
            mine = _batch(cur, household, account, document, "2026-02-01T09:00:00Z")

            with pytest.raises(DuplicateDocument) as raised:
                refuse_if_already_imported(cur, household, SHA)

            assert str(intruder) not in str(raised.value), "another household's batch id"
            assert str(mine) in str(raised.value)
    finally:
        conn.rollback()
        conn.close()


def test_a_file_this_household_has_not_seen_passes() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            household, _ = _fixture(cur)
            _document(cur, household, SHA, date(2026, 2, 1))
            refuse_if_already_imported(cur, household, OTHER_SHA)
    finally:
        conn.rollback()
        conn.close()


def test_the_same_bytes_under_another_household_are_another_document() -> None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            household, _ = _fixture(cur)
            cur.execute(
                "INSERT INTO household (name, base_currency, secondary_currency)"
                " SELECT 'Other', base_currency, secondary_currency FROM household WHERE id = %s"
                " RETURNING id",
                (household,),
            )
            other = UUID(str(cur.fetchone()[0]))  # type: ignore[index]
            _document(cur, household, SHA, date(2026, 2, 1))
            refuse_if_already_imported(cur, other, SHA)
    finally:
        conn.rollback()
        conn.close()
