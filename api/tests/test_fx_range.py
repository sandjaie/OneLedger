"""Fetch-on-missing: the rates a statement's dates need, obtained by the code path the
backfill route already uses. Spec §4.8.

`ingest/` may not import from `routes/` (spec §4.1), so the callable both the route and the
pipeline share lives in `oneledger.fxrange` rather than in `routes/fx.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

import psycopg
import pytest

from oneledger.fxrange import FxCoverageError, ensure_rates, fill_range, missing_dates

HOUSEHOLD = ("XIN", "XAE")
PEGS = [("XAE", "XUS", Decimal("3.6725"))]
ACCOUNTS = [("XAE",)]
# What reporting_config derives from the three rows above: quote XIN, bases XAE and its anchor.
BASES = ["XAE", "XUS"]


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.result: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.conn.executed.append((" ".join(sql.split()), params))
        if "FROM household" in sql:
            self.result = [HOUSEHOLD]
        elif "FROM currency" in sql:
            self.result = list(self.conn.pegs)
        elif "FROM account" in sql:
            self.result = list(self.conn.accounts)
        elif "FROM fx_rate" in sql:
            self.result = [(d,) for d in sorted(self.conn.covered | self.conn.stored())]
        else:
            raise AssertionError(f"unexpected query: {sql}")

    def executemany(self, sql: str, rows: list[dict[str, Any]]) -> None:
        self.conn.written.extend(rows)
        # The real ON CONFLICT clause declines to overwrite an observed rate, so rowcount is
        # rows actually written and not rows offered.
        self.rowcount = len(rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None: ...


class FakeConn:
    def __init__(self, covered: set[date] | None = None) -> None:
        self.pegs = PEGS
        self.accounts = ACCOUNTS
        self.covered = covered or set()
        # Dates whose writes the store declines. `upsert_rates`' ON CONFLICT clause does
        # exactly this for a rate a person entered or a statement observed: a reference feed
        # does not overwrite an observation. A write that returns without storing is the one
        # thing the post-fetch check exists to catch.
        self.declines: set[date] = set()
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.written: list[dict[str, Any]] = []
        self.commits = 0

    def stored(self) -> set[date]:
        """The dates the writes below actually cover — every base, as the real query counts."""
        seen: dict[date, set[str]] = {}
        for row in self.written:
            if row["rate_date"] in self.declines:
                continue
            seen.setdefault(row["rate_date"], set()).add(row["base_currency"])
        return {d for d, bases in seen.items() if bases >= set(BASES)}

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1


def _conn(covered: set[date] | None = None) -> psycopg.Connection:
    return cast("psycopg.Connection", FakeConn(covered))


class FakeSource:
    """Publishes on the days it is given and on no others, exactly as the ECB does."""

    name = "ecb"

    def __init__(self, published: dict[date, dict[str, Decimal]]) -> None:
        self.published = published
        self.calls: list[tuple[date, date, list[str]]] = []

    def fetch_range(
        self, start: date, end: date, codes: list[str]
    ) -> dict[date, dict[str, Decimal]]:
        self.calls.append((start, end, codes))
        return {d: r for d, r in self.published.items() if start <= d <= end}


class FailingSource:
    name = "ecb"

    def fetch_range(
        self, start: date, end: date, codes: list[str]
    ) -> dict[date, dict[str, Decimal]]:
        raise ConnectionError("the rate source is unreachable")


def _published(days: list[date]) -> dict[date, dict[str, Decimal]]:
    return {d: {"XIN": Decimal("91.28"), "XUS": Decimal("1.0956")} for d in days}


MONDAY = date(2026, 3, 2)
FRIDAY = date(2026, 3, 6)
SATURDAY = date(2026, 3, 7)
SUNDAY = date(2026, 3, 8)


def test_a_present_date_is_not_fetched() -> None:
    """The cheapest fetch is the one that does not happen. A date already covered for every
    pair the household needs is not a gap."""
    conn = FakeConn(covered={MONDAY})
    source = FakeSource(_published([MONDAY]))
    written = ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=source)
    assert written == 0
    assert source.calls == [], "no gap, no request"
    assert conn.written == []


def test_a_missing_date_is_fetched() -> None:
    conn = FakeConn(covered=set())
    source = FakeSource(_published([MONDAY]))
    written = ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=source)
    assert written > 0
    assert len(source.calls) == 1
    assert {r["rate_date"] for r in conn.written} == {MONDAY}
    assert {r["quote_currency"] for r in conn.written} == {"XIN"}
    assert {r["base_currency"] for r in conn.written} == set(BASES)


def test_only_the_gaps_are_requested_and_as_one_range() -> None:
    """Spec §4.8: one contiguous range covering the gaps, not a request per date."""
    conn = FakeConn(covered={MONDAY, FRIDAY})
    days = [MONDAY, date(2026, 3, 3), date(2026, 3, 4), FRIDAY]
    source = FakeSource(_published(days))
    ensure_rates(cast("psycopg.Connection", conn), days, source=source)
    assert len(source.calls) == 1, "one range, not one call per gap"
    start, end, _ = source.calls[0]
    assert end == date(2026, 3, 4), "the range ends at the last gap, not the last date"
    assert start <= date(2026, 3, 3), "and begins at or before the first"


def test_a_weekend_carries_the_previous_rate_forward_and_says_so() -> None:
    """No rate is published on a Saturday. Carrying Friday's forward is correct; presenting
    it as a Saturday observation is Invariant I7's whole subject."""
    conn = FakeConn()
    source = FakeSource(_published([FRIDAY]))
    ensure_rates(cast("psycopg.Connection", conn), [FRIDAY, SATURDAY, SUNDAY], source=source)

    by_day = {(r["rate_date"], r["base_currency"]): r for r in conn.written}
    friday = by_day[(FRIDAY, "XUS")]
    saturday = by_day[(SATURDAY, "XUS")]
    assert friday["is_carried_forward"] is False and friday["carried_from"] is None
    assert saturday["is_carried_forward"] is True, "a carried rate is stored as carried"
    assert saturday["carried_from"] == FRIDAY, "and names the day it was carried from"
    assert saturday["rate"] == friday["rate"], "carried, not interpolated"


def test_a_fetch_failure_is_a_stated_refusal_not_a_skipped_row() -> None:
    """The silent alternative is a batch that stages every row it could price and drops the
    rest — an import that looks complete and is not."""
    conn = FakeConn()
    with pytest.raises(FxCoverageError) as raised:
        ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=FailingSource())
    assert MONDAY.isoformat() in str(raised.value)
    assert conn.written == [], "nothing is stored when the fetch fails"


def test_a_date_the_fetch_did_not_cover_is_refused_by_name() -> None:
    """A range can return successfully and still leave a date unpriced — a feed that has not
    published yet for today. Never a fallback to 1.0, never today's rate (§4.8)."""
    conn = FakeConn()
    # Published only after the dates asked for, so carry_forward has nothing to fill from.
    source = FakeSource(_published([date(2026, 4, 1)]))
    with pytest.raises(FxCoverageError) as raised:
        ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=source)
    assert MONDAY.isoformat() in str(raised.value)


def test_a_fetch_that_stores_nothing_is_refused_after_the_write() -> None:
    """The fetch can succeed, the write can return, and the date can still be unpriced —
    `upsert_rates` declines to overwrite a rate a person entered or a statement observed. The
    row count says the write happened; only re-reading coverage says it landed."""
    conn = FakeConn()
    conn.declines = {MONDAY}
    source = FakeSource(_published([MONDAY]))
    with pytest.raises(FxCoverageError) as raised:
        ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=source)
    assert MONDAY.isoformat() in str(raised.value)
    assert len(source.calls) == 1, "the fetch itself succeeded"


def test_no_dates_asks_nothing() -> None:
    conn = FakeConn()
    source = FakeSource({})
    assert ensure_rates(cast("psycopg.Connection", conn), [], source=source) == 0
    assert source.calls == []


def test_a_date_covered_for_only_some_pairs_is_still_a_gap() -> None:
    """`_frozen` reads two legs. A date holding one of them prices nothing on its own."""
    conn = FakeConn()
    conn.covered = set()  # the coverage query returns only fully-covered dates
    assert missing_dates(cast("psycopg.Connection", conn), [MONDAY]) == [MONDAY]
    sql, params = next((s, p) for s, p in conn.executed if "FROM fx_rate" in s)
    assert "HAVING count(DISTINCT base_currency) =" in sql, "partial coverage is not coverage"
    assert params is not None and params[-1] == len(BASES), "every pair, not any pair"


def test_fill_range_writes_what_the_backfill_would() -> None:
    """The route keeps its guards and loses nothing else: this is the body it now calls."""
    conn = FakeConn()
    source = FakeSource(_published([MONDAY]))
    written = fill_range(cast("psycopg.Connection", conn), MONDAY, MONDAY, source=source)
    assert written == len(BASES)
    assert conn.commits == 1
    start, _, codes = source.calls[0]
    assert start < MONDAY, "the feed snaps a range start back, so ask from earlier"
    assert codes == ["XIN", "XUS"], "the quote and the end of each peg chain, not XAE"
