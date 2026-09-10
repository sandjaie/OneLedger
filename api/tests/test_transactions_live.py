"""The B3.5 interleaving, against the live database: a reporting-pair change and a frozen
write serialize on the household row in both orders. Everything runs inside transactions that
roll back; nothing persists. Skipped when no database or no bound owner is reachable — the
pre-push hook runs with the stack up, which is where this proof matters."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from .live_database import connect_to_test_database


def _connect() -> psycopg.Connection:
    """See `tests/live_database.py`: the test database, and only the test database."""
    return connect_to_test_database()


def _fixture(cur: psycopg.Cursor[Any]) -> tuple[Any, Any, str, str, tuple[Any, ...]]:
    cur.execute("SELECT id, name, base_currency, secondary_currency FROM household LIMIT 1")
    household = cur.fetchone()
    if household is None:
        pytest.skip("no seeded household")
    cur.execute("SELECT id FROM app_user LIMIT 1")
    user = cur.fetchone()
    if user is None:
        pytest.skip("no bound owner yet — live identity setup pending")
    cur.execute(
        "SELECT display_name, tax_residency FROM person WHERE household_id = %s AND is_self",
        (household[0],),
    )
    person = cur.fetchone()
    if person is None:
        pytest.skip("no self person")
    return (
        household[0],
        user[0],
        str(household[2]).strip(),
        str(household[3]).strip(),
        (
            household[1],
            person[0],
            person[1],
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
