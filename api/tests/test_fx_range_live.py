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
from typing import Any, cast

import psycopg
import pytest

from oneledger.fxrange import missing_dates, reporting_config, upsert_rates

# Far enough out that no backfill has reached them, so the rows below are the whole story.
COVERED = date(2099, 1, 1)
PARTIAL = date(2099, 1, 2)
OTHER_QUOTE = date(2099, 1, 3)
UNTOUCHED = date(2099, 1, 4)
OBSERVED = date(2099, 1, 5)


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


def _stored(cur: psycopg.Cursor[Any]) -> dict[str, tuple[Decimal, str]]:
    cur.execute(
        "SELECT base_currency, rate, source FROM fx_rate WHERE rate_date = %s",
        (OBSERVED,),
    )
    return {base: (rate, source) for base, rate, source in cur.fetchall()}


def test_an_observed_rate_outranks_a_reference_rate_and_a_reference_rate_refreshes() -> None:
    """`multi-currency-and-fx.html` §5: a rate a person typed or a statement observed beats a
    reference feed. The ON CONFLICT clause is the only thing enforcing that, and this is the
    only place the INSERT is ever executed — a mis-ordered column list or a clause deleted
    outright would ship green otherwise, because no stub cursor reads SQL.

    Both halves of `DO UPDATE` are asserted. The decline alone does not distinguish it from
    `DO NOTHING`, which would leave the docstring's idempotency claim — "re-running a backfill
    overwrites with the same values" — quietly false, and a corrected rate would never land.
    """
    conn = _connect()
    try:
        quote, bases, _ = reporting_config(conn)
        if len(bases) < 2:
            pytest.skip("this household reports against a single base; the decline needs two")
        observed, reference = bases[0], bases[1]
        writer = cast("psycopg.Connection", NoCommit(conn))

        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
                   VALUES (%s, %s, %s, %s, 'manual')""",
                (OBSERVED, observed, quote, Decimal("7.7777")),
            )

            written = upsert_rates(
                writer,
                [
                    _row(OBSERVED, observed, quote, Decimal("1.1111")),
                    _row(OBSERVED, reference, quote, Decimal("2.2222")),
                ],
            )
            assert written == 1, "the declined row is offered, not written"
            stored = _stored(cur)
            assert stored[observed] == (Decimal("7.7777"), "manual"), "the observation stands"
            assert stored[reference] == (Decimal("2.2222"), "ecb"), "the new pair landed"

            # The refresh half: a reference row the feed corrects must actually change.
            again = upsert_rates(writer, [_row(OBSERVED, reference, quote, Decimal("3.3333"))])
            assert again == 1
            assert _stored(cur)[reference] == (Decimal("3.3333"), "ecb"), "a correction lands"
            assert _stored(cur)[observed] == (Decimal("7.7777"), "manual"), "still untouched"
    finally:
        conn.rollback()
        conn.close()
