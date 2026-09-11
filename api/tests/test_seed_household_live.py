from __future__ import annotations

import os
import pathlib
import subprocess
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from .live_database import connect_to_test_database
from .live_database import test_dsn as _test_dsn

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def owner_row() -> Iterator[tuple[psycopg.Connection[Any], tuple[object, ...]]]:
    connection = connect_to_test_database()
    connection.rollback()
    connection.autocommit = True
    suppressed_owner_ids: list[object] = []
    with connection.cursor() as cur:
        cur.execute(
            """SELECT id, household_id, person_id, email, role, created_at
                 FROM app_user WHERE role = 'owner' ORDER BY created_at, id"""
        )
        owners = cur.fetchall()
        original = owners[0] if owners else None
        created = original is None
        if created:
            cur.execute("SELECT id, household_id FROM person WHERE is_self")
            person = cur.fetchone()
            if person is None:
                connection.close()
                pytest.skip("no seeded self person")
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                     VALUES (%s, %s, %s, %s, 'owner')
                  RETURNING id, household_id, person_id, email, role, created_at""",
                (uuid4(), person[1], person[0], "initial-owner@example.invalid"),
            )
            original = cur.fetchone()
        else:
            suppressed_owner_ids = [owner[0] for owner in owners[1:]]
            if suppressed_owner_ids:
                cur.execute(
                    "UPDATE app_user SET role = 'member' WHERE id = ANY(%s)",
                    (suppressed_owner_ids,),
                )
    if original is None:
        connection.close()
        pytest.fail("owner fixture was not created")
    try:
        yield connection, original
    finally:
        with connection.cursor() as cur:
            if created:
                cur.execute("DELETE FROM app_user WHERE id = %s", (original[0],))
            else:
                cur.execute(
                    "UPDATE app_user SET email = %s WHERE id = %s", (original[3], original[0])
                )
            if suppressed_owner_ids:
                cur.execute(
                    "UPDATE app_user SET role = 'owner' WHERE id = ANY(%s)",
                    (suppressed_owner_ids,),
                )
        connection.close()


def _run(email: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["ALLOWED_USER_EMAIL"] = email
    environment["DATABASE_URL"] = _test_dsn()
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "seed-household.sh"), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_explicit_repair_changes_only_the_existing_owner_email(
    owner_row: tuple[psycopg.Connection[Any], tuple[object, ...]],
) -> None:
    connection, original = owner_row
    replacement = "repaired-owner@example.invalid"

    repaired = _run(replacement, "--repair-owner-email")

    assert repaired.returncode == 0, repaired.stderr
    with connection.cursor() as cur:
        cur.execute(
            """SELECT id, household_id, person_id, email, role
                 FROM app_user WHERE role = 'owner'"""
        )
        assert cur.fetchall() == [(*original[:3], replacement, original[4])]

    refused = _run("another-owner@example.invalid")

    assert refused.returncode != 0
    assert "an owner already exists" in refused.stderr
    with connection.cursor() as cur:
        cur.execute("SELECT email FROM app_user WHERE id = %s", (original[0],))
        assert cur.fetchone() == (replacement,)


def test_repair_refuses_more_than_one_existing_owner(
    owner_row: tuple[psycopg.Connection[Any], tuple[object, ...]],
) -> None:
    connection, original = owner_row
    second_id = uuid4()
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO app_user (id, household_id, person_id, email, role)
                 VALUES (%s, %s, %s, %s, 'owner')""",
            (second_id, original[1], original[2], "second-owner@example.invalid"),
        )
    try:
        result = _run("repaired-owner@example.invalid", "--repair-owner-email")

        assert result.returncode != 0
        assert "requires exactly one existing owner" in result.stderr
        with connection.cursor() as cur:
            cur.execute("SELECT email FROM app_user WHERE id = %s", (original[0],))
            assert cur.fetchone() == (original[3],)
    finally:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM app_user WHERE id = %s", (second_id,))


def test_repair_refuses_no_owner_without_changing_household_or_person(
    owner_row: tuple[psycopg.Connection[Any], tuple[object, ...]],
) -> None:
    connection, original = owner_row
    with connection.cursor() as cur:
        cur.execute("UPDATE app_user SET role = 'member' WHERE id = %s", (original[0],))
        cur.execute(
            """SELECT id, household_id, person_id, email, role, google_subject, created_at
                 FROM app_user ORDER BY id"""
        )
        users_before = cur.fetchall()
        cur.execute("SELECT id, name, base_currency, secondary_currency FROM household ORDER BY id")
        households_before = cur.fetchall()
        cur.execute(
            """SELECT id, household_id, display_name, is_self, tax_residency
                 FROM person ORDER BY id"""
        )
        people_before = cur.fetchall()
    try:
        result = _run("repaired-owner@example.invalid", "--repair-owner-email")

        assert result.returncode != 0
        assert "requires exactly one existing owner" in result.stderr
        with connection.cursor() as cur:
            cur.execute(
                """SELECT id, household_id, person_id, email, role, google_subject, created_at
                     FROM app_user ORDER BY id"""
            )
            assert cur.fetchall() == users_before
            cur.execute(
                "SELECT id, name, base_currency, secondary_currency FROM household ORDER BY id"
            )
            assert cur.fetchall() == households_before
            cur.execute(
                """SELECT id, household_id, display_name, is_self, tax_residency
                     FROM person ORDER BY id"""
            )
            assert cur.fetchall() == people_before
    finally:
        with connection.cursor() as cur:
            cur.execute("UPDATE app_user SET role = %s WHERE id = %s", (original[4], original[0]))
