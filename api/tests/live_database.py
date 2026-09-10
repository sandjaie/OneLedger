"""The one place a live test may get a database connection.

Every `*_live.py` fixture empties `transaction`, `import_batch`, `document`,
`statement_layout` and `classification_rule`. Pointed at a working ledger that is not a test
run, that is data loss — which is what happened on 10 September 2026, taking 4,184 imported
transactions and four layout profiles with it. Six files each carried their own `_connect`,
each reading the same `POSTGRES_*` variables the application uses, so exporting `.env` to make
the live tests run was enough to aim all six at the real database.

They now share this module, and it reads **`ONELEDGER_TEST_DSN` and nothing else**. It is set
by `scripts/test-db.sh`, which points at the `db-test` container — its own port, its own
volume, behind its own compose profile.
"""

from __future__ import annotations

import os

import psycopg
import pytest


def test_dsn() -> str:
    dsn = os.environ.get("ONELEDGER_TEST_DSN")
    if not dsn:
        pytest.skip('no test database: eval "$(scripts/test-db.sh up)"')
    return dsn


def connect_to_test_database() -> psycopg.Connection:
    """Connect, and refuse anything that looks like a real ledger.

    Two checks, because the container is the mechanism and these are the backstop.

    The first refuses a database with committed transactions in it: a test database has none
    by construction, and a `TRUNCATE` against one that does is the accident this module
    exists to make impossible.

    The second refuses a **split**: the fixtures connect through this DSN while the routes
    under test connect through `oneledger.db.connect`, which reads `POSTGRES_*`. If those
    point somewhere else, a test would truncate one database and exercise another — passing
    or failing for reasons that have nothing to do with the code, while writing to a ledger
    nobody meant to touch. `scripts/test-db.sh` exports both from the same container.
    """
    dsn = test_dsn()
    try:
        connection = psycopg.connect(dsn)
    except psycopg.OperationalError:
        pytest.skip("test database unreachable")

    # Every table the fixtures empty, not just the ledger. The list is exactly `_clean`'s,
    # and it is one line longer than it first was: a database with no transactions but
    # thirty-seven classification rules in it is still one somebody is using, and the run
    # that proved this guard works is the run that deleted them.
    with connection.cursor() as cur:
        for table in (
            "transaction",
            "document",
            "statement_layout",
            "classification_rule",
            "transfer",
        ):
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
            row = cur.fetchone()
            if row is not None and row[0] > 0:
                connection.close()
                pytest.fail(
                    f"ONELEDGER_TEST_DSN points at a database holding {row[0]} rows in "
                    f"{table}. These fixtures empty that table; they do not run against a "
                    "database that has anything in it."
                )
        cur.execute("SELECT current_database(), inet_server_port()")
        mine = cur.fetchone()

    from oneledger.db import connect as application_connect

    try:
        with application_connect() as theirs_conn, theirs_conn.cursor() as cur:
            cur.execute("SELECT current_database(), inet_server_port()")
            theirs = cur.fetchone()
    except psycopg.OperationalError:
        connection.close()
        pytest.skip("the application cannot reach a database; export the test POSTGRES_* too")

    if mine != theirs:
        connection.close()
        pytest.fail(
            f"the fixtures would use {mine} and the routes under test {theirs}. Export both "
            'from the same container: eval "$(scripts/test-db.sh up)"'
        )
    return connection
