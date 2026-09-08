# api/oneledger/fxrange.py
"""One range of stored FX rates, fetched once and shared.

`POST /fx/backfill` used to hold this whole body. M2's importer needs the same work — a
statement's booked dates must be priced before its rows can be staged — and a second
implementation of "fetch, derive the pegs, carry the gaps forward, store the provenance" is
how two writers of `fx_rate` come to disagree about what a Saturday was worth.

It lives here rather than in `routes/fx.py` because `oneledger/ingest/` may not import from
`oneledger/routes/` (spec §4.1), and the parse pipeline is the second caller. The route keeps
its own guards — a future end date, a reversed range — because they are about what a person
may ask for, not about what a range is.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

import httpx
import psycopg
from psycopg.pq import TransactionStatus

from .fx import EcbFrankfurterSource, Peg, anchor_chain, carry_forward, cross_rate, terminal_anchor


class FxCoverageError(RuntimeError):
    """A date the ledger needs priced has no rate and could not be given one.

    Always names the dates. Nothing about money is invented silently (Invariant 7): there is
    no fallback to 1.0, no today's rate and no interpolation, so the only honest outcome is a
    refusal that says which days are missing.
    """


class RateSource(Protocol):
    def fetch_range(
        self, start: date, end: date, codes: list[str]
    ) -> dict[date, dict[str, Decimal]]: ...


def reporting_config(conn: psycopg.Connection) -> tuple[str, list[str], dict[str, Peg]]:
    """What this deployment needs quoted: (base currency, the others, the pegs).

    Read, never assumed. A household reporting in EUR against CHF fetches EUR and CHF.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT base_currency, secondary_currency FROM household LIMIT 1")
        row = cur.fetchone()
        if row is None:
            # Seeded by Task 3. Failing here with a sentence beats unpacking None.
            raise RuntimeError(
                "no household row — apply the migrations: docker compose run --rm flyway migrate"
            )
        quote, secondary = row
        cur.execute("SELECT code, pegged_to, peg_rate FROM currency WHERE pegged_to IS NOT NULL")
        pegs = {code: Peg(anchor, rate) for code, anchor, rate in cur.fetchall()}
        cur.execute(
            "SELECT DISTINCT currency FROM account WHERE currency <> %s UNION SELECT %s",
            (quote, secondary),
        )
        bases = {row[0] for row in cur.fetchall()}

    # Every anchor along every chain is quoted too, even when no account holds it. Without
    # this the peg-identity check joins AED->INR rows against USD->INR rows that were never
    # written, finds nothing, and reports zero violations while proving nothing.
    #
    # The whole chain, not one hop: A->B->USD stored as A and B leaves the B->USD identity
    # just as vacuous as storing A alone left A->USD. And the quote's own chain, not only the
    # bases': reporting in a pegged AED writes INR->AED and nothing that exposes the 3.6725,
    # so the derivation every one of those rows travelled through is never checked. Storing
    # USD->AED makes it the peg rate itself, and a row that must equal currency.peg_rate.
    bases = {c for b in [quote, *bases] for c in anchor_chain(b, pegs)}
    # ...but a chain can pass through the reporting currency — base USD with secondary AED
    # puts USD back, and the quote is in the seed set by construction. A USD/USD row violates
    # the no_self_pair constraint and means nothing.
    bases.discard(quote)
    return quote, sorted(bases), pegs


def to_rows(
    published: dict[date, dict[str, Decimal]],
    start: date,
    end: date,
    quote: str,
    bases: list[str],
    pegs: dict[str, Peg],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in bases:
        series = {day: cross_rate(rates, base, quote, pegs) for day, rates in published.items()}
        # Either side going through a peg makes the result derived. Checking only the base
        # labels an ECB-published USD as `ecb` while reporting in a pegged AED, when the cross
        # in fact travelled through the peg.
        derived = base in pegs or quote in pegs
        source = "derived_peg" if derived else EcbFrankfurterSource.name
        for day, rate, carried in carry_forward(series, start, end):
            rows.append(
                {
                    "rate_date": day,
                    "base_currency": base,
                    "quote_currency": quote,
                    "rate": rate,
                    "source": source,
                    "is_carried_forward": carried is not None,
                    "carried_from": carried,
                }
            )
    return rows


def upsert_rates(conn: psycopg.Connection, rows: list[dict[str, Any]]) -> int:
    """Idempotent: re-running a backfill overwrites with the same values rather than failing.

    Returns rows actually written, not rows offered. The conflict clause skips authoritative
    sources, so `len(rows)` would report writes that deliberately did not happen — and a
    backfill claiming to have stored a rate it declined to touch is worse than no number.
    """
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source,
                                 is_carried_forward, carried_from)
            VALUES (%(rate_date)s, %(base_currency)s, %(quote_currency)s, %(rate)s, %(source)s,
                    %(is_carried_forward)s, %(carried_from)s)
            ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
              SET rate = EXCLUDED.rate, source = EXCLUDED.source,
                  is_carried_forward = EXCLUDED.is_carried_forward,
                  carried_from = EXCLUDED.carried_from, fetched_at = now()
              -- Only reference rates are refreshable. A `manual` rate someone typed and an
              -- `actual_transfer` rate read off a statement are observations, and a reference
              -- feed does not get to overwrite an observation — multi-currency-and-fx.html §5
              -- is explicit that actual rates beat reference rates.
              WHERE fx_rate.source IN ('ecb', 'derived_peg')
            """,
            rows,
        )
        written = cur.rowcount
    conn.commit()
    return written


def fill_range(
    conn: psycopg.Connection, start: date, end: date, *, source: RateSource | None = None
) -> int:
    """Fetch, derive and store every rate this household needs for `start`..`end` inclusive."""
    quote, bases, pegs = reporting_config(conn)
    # A pegged currency is derived from its anchor, so only the end of each chain is
    # fetched. `bases` already carries the intermediates; they are stored, not requested.
    fetch = sorted({terminal_anchor(c, pegs) for c in [quote, *bases]})
    # Frankfurter snaps a range start backward to the previous publication day, so asking
    # from 10 days earlier guarantees carry_forward has something to fill from.
    published = (source or EcbFrankfurterSource()).fetch_range(
        start - timedelta(days=10), end, fetch
    )
    return upsert_rates(conn, to_rows(published, start, end, quote, bases, pegs))


def missing_dates(conn: psycopg.Connection, dates: Iterable[date]) -> list[date]:
    """Which of `dates` lack a stored rate for **every** base `reporting_config` returns.

    That is deliberately stricter than freezing one row needs: `money._frozen` reads only the
    account's currency and the secondary, while `bases` also carries every other account
    currency and every peg-chain intermediate. The strictness is what makes this function and
    `fill_range` symmetric — `to_rows` writes exactly `bases` — and that symmetry is what
    makes `ensure_rates` terminate instead of asking for the same range forever. Do not
    loosen it to match `_frozen` without giving `ensure_rates` another way to stop.

    Partial coverage is still not coverage: a date holding one leg prices nothing on its own.
    """
    wanted = sorted(set(dates))
    if not wanted:
        return []
    quote, bases, _ = reporting_config(conn)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT rate_date FROM fx_rate
                WHERE quote_currency = %s AND base_currency = ANY(%s) AND rate_date = ANY(%s)
                GROUP BY rate_date
               HAVING count(DISTINCT base_currency) = %s""",
            (quote, bases, wanted, len(bases)),
        )
        covered = {row[0] for row in cur.fetchall()}
    return [d for d in wanted if d not in covered]


def ensure_rates(
    conn: psycopg.Connection, dates: Iterable[date], *, source: RateSource | None = None
) -> int:
    """Price every one of `dates`, fetching only what is missing. Returns rows written.

    One contiguous range covers the gaps rather than one request per date (spec §4.8): the
    feed publishes ranges, and a statement's gaps are almost always consecutive anyway.

    Called **before** the staging transaction opens, because `upsert_rates` commits.
    """
    wanted = sorted(set(dates))
    if not wanted:
        return 0
    # `upsert_rates` commits. Called inside a caller's open transaction it would persist
    # whatever that caller had already written — and spec §5.5 says a blocked batch leaves
    # nothing behind, no document row included. Refuse rather than commit someone else's work.
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise FxCoverageError(
            "rates must be fetched before a transaction opens — this one is already in "
            "progress, and storing a rate would commit it"
        )
    # Reading opens a transaction of its own, and leaving it open would arm the guard above
    # against this function's own next call — and against a pipeline that reads a layout
    # before it asks for rates. The guard proved the caller was idle on entry, so whatever is
    # open at exit is ours: the reads, and nothing of the caller's to lose.
    try:
        gaps = missing_dates(conn, wanted)
        if not gaps:
            return 0

        try:
            written = fill_range(conn, min(gaps), max(gaps), source=source)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            # The caller asked for dates, so the refusal answers in dates. A bare transport
            # error names a URL and leaves the importer unable to say which rows it could
            # not price.
            raise FxCoverageError(f"{_named(gaps)} could not be priced: {exc}") from exc

        still = missing_dates(conn, wanted)
        if still:
            # A range can return successfully and still leave a day unpriced — a feed that
            # has not published yet, or a base currency that arrived while the range was in
            # flight. Refusing is the only honest answer: never 1.0, never today's rate,
            # never an interpolation (multi-currency-and-fx.html §3).
            raise FxCoverageError(
                f"{_named(still)} have no exchange rate and none could be fetched"
            )
        return written
    finally:
        # The upsert has already committed; this discards only the reads that followed it.
        conn.rollback()


def _named(dates: list[date]) -> str:
    """The dates themselves, up to a point — a year of gaps is a paragraph, not a message."""
    shown = [d.isoformat() for d in dates[:5]]
    if len(dates) > 5:
        shown.append(f"and {len(dates) - 5} more")
    return ", ".join(shown)
