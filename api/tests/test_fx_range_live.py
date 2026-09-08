"""`missing_dates`' coverage query, against a real database.

Its predicates decide whether a statement's dates are priced, and a stub cursor cannot check
them: one that dispatches on `"FROM fx_rate" in sql` answers the same way for a query missing
its `quote_currency` filter entirely. Everything here runs inside a transaction that rolls
back; nothing persists. Skipped when no database is reachable — the pre-push hook runs with
the stack up, which is where this proof matters.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
import pytest

from oneledger.fxrange import missing_dates, reporting_config

# Far enough out that no backfill has reached them, so the rows below are the whole story.
COVERED = date(2099, 1, 1)
PARTIAL = date(2099, 1, 2)
OTHER_QUOTE = date(2099, 1, 3)
UNTOUCHED = date(2099, 1, 4)


def _connect() -> psycopg.Connection:
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("no database credentials in the environment")
    from oneledger.db import connect

    try:
        return connect()
    except psycopg.OperationalError:
        pytest.skip("database unreachable")


def _insert(cur: psycopg.Cursor[Any], on: date, base: str, quote: str) -> None:
    cur.execute(
        """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
           VALUES (%s, %s, %s, %s, 'ecb')""",
        (on, base, quote, Decimal("1.2345")),
    )


def test_coverage_is_every_base_for_the_households_own_quote() -> None:
    conn = _connect()
    try:
        quote, bases, _ = reporting_config(conn)
        if len(bases) < 2:
            pytest.skip("this household reports against a single base; the partial case needs two")
        with conn.cursor() as cur:
            for base in bases:
                _insert(cur, COVERED, base, quote)
            _insert(cur, PARTIAL, bases[0], quote)
            # Every base present and countable, none quoted against this household's own
            # currency — the rows a reporting-pair change leaves behind, which V7 permits
            # while no amount is frozen. Without the quote filter this date counts as priced.
            for index, base in enumerate(bases):
                _insert(cur, OTHER_QUOTE, base, bases[(index + 1) % len(bases)])

            asked = [COVERED, PARTIAL, OTHER_QUOTE, UNTOUCHED]
            missing = missing_dates(conn, asked)

        assert COVERED not in missing, "every base, right quote — priced"
        assert PARTIAL in missing, "one base of two prices nothing on its own"
        assert OTHER_QUOTE in missing, "rows quoted against another currency are not coverage"
        assert UNTOUCHED in missing, "a date with no rows at all is missing"
    finally:
        conn.rollback()
        conn.close()


def test_the_query_runs_and_returns_nothing_for_an_empty_ask() -> None:
    """A batch whose dates are all priced must not reach the database with an empty array."""
    conn = _connect()
    try:
        assert missing_dates(conn, []) == []
    finally:
        conn.rollback()
        conn.close()
