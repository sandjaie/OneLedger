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


def test_an_observed_rate_outranks_a_reference_rate() -> None:
    """`multi-currency-and-fx.html` §5: a rate a person typed or a statement observed beats a
    reference feed. The ON CONFLICT clause is the only thing enforcing it, and this is the
    only place the INSERT is ever executed — a mis-ordered column list would ship green
    otherwise, because no stub cursor reads the statement.

    `upsert_rates` commits, so this cannot ride inside a rolled-back transaction; the rows
    are dated far past any backfill and removed explicitly.
    """
    conn = _connect()
    try:
        quote, bases, _ = reporting_config(conn)
        observed, fresh = bases[0], bases[-1]
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
                   VALUES (%s, %s, %s, %s, 'manual')""",
                (OBSERVED, observed, quote, Decimal("7.7777")),
            )
        conn.commit()

        written = upsert_rates(
            conn,
            [
                _row(OBSERVED, observed, quote, Decimal("1.1111")),
                _row(OBSERVED, fresh, quote, Decimal("2.2222")),
            ],
        )

        with conn.cursor() as cur:
            cur.execute(
                "SELECT base_currency, rate, source FROM fx_rate"
                " WHERE rate_date = %s ORDER BY base_currency",
                (OBSERVED,),
            )
            stored = {b: (r, s) for b, r, s in cur.fetchall()}

        if observed != fresh:
            assert written == 1, "the declined row is not a row written"
        assert stored[observed] == (Decimal("7.7777"), "manual"), "the observation stands"
        if observed != fresh:
            assert stored[fresh] == (Decimal("2.2222"), "ecb"), "the new pair landed"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fx_rate WHERE rate_date >= %s", (COVERED,))
        conn.commit()
        conn.close()


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
