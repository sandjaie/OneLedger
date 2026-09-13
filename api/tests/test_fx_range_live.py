"""`missing_dates`' coverage query, against a real database.

Its predicates decide whether a statement's dates are priced, and a stub cursor cannot check
them: one that dispatches on `"FROM fx_rate" in sql` answers the same way for a query missing
its `quote_currency` filter entirely. Everything here runs inside a transaction that rolls
back; nothing persists. The final verifier supplies the isolated test database where this proof
runs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

import psycopg
import pytest

from oneledger.fxrange import missing_dates, reporting_config, upsert_rates

from .live_database import connect_to_test_database

# Far enough out that no backfill has reached them, so the rows below are the whole story.
COVERED = date(2099, 1, 1)
PARTIAL = date(2099, 1, 2)
OTHER_QUOTE = date(2099, 1, 3)
UNTOUCHED = date(2099, 1, 4)
OBSERVED = date(2099, 1, 5)
CARRIED_FROM = date(2099, 1, 4)


def _connect() -> psycopg.Connection:
    """See `tests/live_database.py`: the test database, and only the test database."""
    return connect_to_test_database()


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


def test_an_empty_ask_returns_nothing_rather_than_raising() -> None:
    """A batch whose dates are all priced asks for none, and that must be an ordinary empty
    answer — not an exception the pipeline has to know to catch."""
    conn = _connect()
    try:
        assert missing_dates(conn, []) == []
    finally:
        conn.rollback()
        conn.close()


class NoCommit:
    """`upsert_rates` commits, and a test must not: a committed row outlives the failure that
    prevented its cleanup, and the next run then fails on its own leftover — the setup INSERT
    hits the primary key, the transaction aborts, and the cleanup that would have fixed it
    cannot run either. Wrapping the connection with a no-op `commit` keeps the INSERT running
    against real Postgres, which is the whole point, while leaving every row inside the one
    transaction this test rolls back."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def cursor(self) -> psycopg.Cursor[Any]:
        return self._conn.cursor()

    def commit(self) -> None: ...


def _row(on: date, base: str, quote: str, rate: Decimal) -> dict[str, Any]:
    return {
        "rate_date": on,
        "base_currency": base,
        "quote_currency": quote,
        "rate": rate,
        "source": "ecb",
        "is_carried_forward": False,
        "carried_from": None,
    }


def _stored(cur: psycopg.Cursor[Any], quote: str) -> dict[str, tuple[Any, ...]]:
    """Every column the conflict clause assigns, so a dropped assignment is visible."""
    cur.execute(
        "SELECT base_currency, rate, source, is_carried_forward, carried_from"
        " FROM fx_rate WHERE rate_date = %s AND quote_currency = %s",
        (OBSERVED, quote),
    )
    return {row[0]: tuple(row[1:]) for row in cur.fetchall()}


def test_an_observed_rate_outranks_a_reference_rate_and_a_reference_rate_refreshes() -> None:
    """`multi-currency-and-fx.html` §5: a rate a person typed or a statement observed beats a
    reference feed. The ON CONFLICT clause is the only thing enforcing that, and this is the
    only place the INSERT is ever executed — a mis-ordered column list, a deleted WHERE or a
    dropped SET assignment would ship green otherwise, because no stub cursor reads SQL.

    Both halves of `DO UPDATE` are asserted, and the refreshable row is stale in *every*
    column the SET list assigns. Agreeing on any of them would let a dropped assignment read
    back as the value the assertion wanted.
    """
    conn = _connect()
    try:
        quote, bases, _ = reporting_config(conn)
        if len(bases) < 2:
            pytest.skip("this household reports against a single base; the decline needs two")
        observed, reference = bases[0], bases[1]
        writer = cast("psycopg.Connection", NoCommit(conn))

        with conn.cursor() as cur:
            # An observation, which no reference feed may displace.
            cur.execute(
                "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)"
                " VALUES (%s, %s, %s, %s, 'manual')",
                (OBSERVED, observed, quote, Decimal("7.7777")),
            )
            # Refreshable, and stale in all of rate, source, carried flag and carried date.
            cur.execute(
                "INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source,"
                " is_carried_forward, carried_from)"
                " VALUES (%s, %s, %s, %s, 'derived_peg', true, %s)",
                (OBSERVED, reference, quote, Decimal("9.9999"), CARRIED_FROM),
            )

            written = upsert_rates(
                writer,
                [
                    _row(OBSERVED, observed, quote, Decimal("1.1111")),
                    _row(OBSERVED, reference, quote, Decimal("2.2222")),
                ],
            )
            assert written == 1, "the declined row is offered, not written"

            stored = _stored(cur, quote)
            assert stored[observed] == (Decimal("7.7777"), "manual", False, None), (
                "an observation outranks a reference feed"
            )
            # Invariant I7 on the update path: a day republished as a real observation must
            # stop claiming it was carried, and stop naming the day it was carried from.
            assert stored[reference] == (Decimal("2.2222"), "ecb", False, None), (
                "every column the refresh assigns actually moves"
            )

            # A correction lands too, which is what `upsert_rates` means by idempotent.
            # `fetched_at` is not asserted: now() is the transaction timestamp and this test
            # is one transaction, so it reads the same before and after by construction.
            again = upsert_rates(writer, [_row(OBSERVED, reference, quote, Decimal("3.3333"))])
            assert again == 1
            refreshed = _stored(cur, quote)
            assert refreshed[reference][0] == Decimal("3.3333"), "a corrected rate lands"
            assert refreshed[observed][0] == Decimal("7.7777"), "the observation still stands"
    finally:
        conn.rollback()
        conn.close()
