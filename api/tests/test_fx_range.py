"""Fetch-on-missing: the rates a statement's dates need, obtained by the code path the
backfill route already uses. Spec §4.8.

`ingest/` may not import from `routes/` (spec §4.1), so the callable both the route and the
pipeline share lives in `oneledger.fxrange` rather than in `routes/fx.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.pq import TransactionStatus
from pytest import MonkeyPatch

from oneledger.auth import OwnerSession, require_owner
from oneledger.fxrange import FxCoverageError, ensure_rates, fill_range, missing_dates
from oneledger.main import app

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
        # psycopg3 opens a transaction on the first statement and holds it until the caller
        # commits or rolls back. Without this the fake cannot see a function leave one open.
        self.conn.transaction_status = TransactionStatus.INTRANS
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
        declined = sum((r["rate_date"], r["base_currency"]) in self.conn.declines for r in rows)
        # `INSERT 0 1` for two offered rows when one conflicts and the WHERE declines it.
        self.rowcount = len(rows) - declined
        if self.conn.on_write is not None:
            self.conn.on_write()

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None: ...


@dataclass(frozen=True)
class FakeInfo:
    transaction_status: TransactionStatus


class FakeConn:
    def __init__(self, covered: set[date] | None = None) -> None:
        self.transaction_status = TransactionStatus.IDLE
        self.pegs = PEGS
        self.accounts = list(ACCOUNTS)
        self.covered = covered or set()
        # (date, base) pairs the store declines to overwrite, as `upsert_rates`' ON CONFLICT
        # clause declines a rate a person entered or a statement observed. Verified against
        # Postgres: the declined row is **not** counted in `rowcount`, and the pre-existing
        # row stays, so the pair remains covered. Both halves matter — the first is why
        # `upsert_rates` returns rows written rather than rows offered, and the second is why
        # a decline is not a way a date goes unpriced.
        self.declines: set[tuple[date, str]] = set()
        # Called after each write, so a test can widen what the household reports in.
        self.on_write: Callable[[], None] | None = None
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.written: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0

    def stored(self) -> set[date]:
        """The dates the writes below actually cover — every base, as the real query counts."""
        seen: dict[date, set[str]] = {}
        for row in self.written:
            # A declined row still covers its pair: the store kept the observation that
            # displaced the write.
            seen.setdefault(row["rate_date"], set()).add(row["base_currency"])
        return {d for d, bases in seen.items() if bases >= set(self.bases())}

    def bases(self) -> list[str]:
        """What `reporting_config` derives from `accounts` — recomputed, so a test can widen
        it between the write and the coverage re-read."""
        return sorted({c for (c,) in self.accounts} | {"XUS"})

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1
        self.transaction_status = TransactionStatus.IDLE

    def rollback(self) -> None:
        self.rollbacks += 1
        self.transaction_status = TransactionStatus.IDLE

    @property
    def info(self) -> FakeInfo:
        return FakeInfo(self.transaction_status)


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


def _route_stub(monkeypatch: MonkeyPatch) -> list[tuple[date, date]]:
    """Replace the route's database and its range call, recording what it was handed.

    Through `monkeypatch` rather than by assignment: a direct `fx.fill_range = ...` outlives
    the test and would leave every later test running against the stub.
    """
    from oneledger.routes import fx

    called: list[tuple[date, date]] = []

    class Conn:
        def __enter__(self) -> Conn:
            return self

        def __exit__(self, *_: object) -> None: ...

    def fake_fill(conn: object, start: date, end: date) -> int:
        called.append((start, end))
        return 7

    monkeypatch.setattr(fx, "connect", lambda: Conn(), raising=False)
    monkeypatch.setattr(fx, "fill_range", fake_fill)
    return called


def _owned_client() -> TestClient:
    app.dependency_overrides[require_owner] = lambda: OwnerSession(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        household_id=UUID("22222222-2222-2222-2222-222222222222"),
        session_id=UUID("33333333-3333-3333-3333-333333333333"),
        email="owner@example.com",
    )
    return TestClient(app)


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


def test_a_declined_write_is_not_counted_but_still_covers_its_pair() -> None:
    """`upsert_rates` returns rows written, not rows offered. Postgres reports `INSERT 0 1`
    for two offered rows when one conflicts and the clause declines it — and the row that
    displaced the write is still there, so the pair stays priced."""
    conn = FakeConn()
    conn.declines = {(MONDAY, "XAE")}
    source = FakeSource(_published([MONDAY]))
    written = fill_range(cast("psycopg.Connection", conn), MONDAY, MONDAY, source=source)
    assert written == len(BASES) - 1, "a declined row is not a row written"
    assert missing_dates(cast("psycopg.Connection", conn), [MONDAY]) == [], "still covered"


def test_a_date_left_uncovered_by_the_write_is_refused_after_it() -> None:
    """The fetch can succeed, the write can land, and the date can still be unpriced: an
    account in a new currency committed while the range was in flight widens what the
    household reports in, and the rows just written no longer cover it. Refusing is the only
    honest answer — never 1.0, never today's rate (§4.8)."""
    conn = FakeConn()
    source = FakeSource(_published([MONDAY]))
    # Committed by another writer between the coverage read and the coverage re-read.
    conn.on_write = lambda: conn.accounts.append(("XBR",))
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


def test_rates_are_never_fetched_inside_an_open_transaction() -> None:
    """`upsert_rates` commits. Called mid-transaction it would persist whatever the caller had
    already written — and spec §5.5 says a blocked batch leaves nothing behind, not even the
    `document` row whose unique checksum would then refuse the retry."""
    conn = FakeConn()
    conn.transaction_status = TransactionStatus.INTRANS
    source = FakeSource(_published([MONDAY]))
    with pytest.raises(FxCoverageError, match="already in progress"):
        ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=source)
    assert conn.written == [] and conn.commits == 0, "nothing written, nothing committed"
    assert source.calls == [], "and nothing fetched"
    # The guard raises outside the try, deliberately: the refusal must leave the caller's
    # transaction alone for the caller to unwind. Moving it inside would let the closing
    # rollback discard work the pipeline had already done — the same defect as leaving the
    # transaction open, inverted.
    assert conn.rollbacks == 0, "a refusal is not a rollback of someone else's work"
    assert conn.transaction_status == TransactionStatus.INTRANS, "the caller's is the caller's"


def test_the_backfill_route_refuses_a_future_end_and_a_reversed_range(
    monkeypatch: MonkeyPatch,
) -> None:
    """`carry_forward` fills every calendar day in the range, so a future end date writes the
    last known rate forward into it — years of invented history from one typo (Invariant 7).
    Both guards stay on the route, because they are about what a person may ask for."""
    called = _route_stub(monkeypatch)
    client = _owned_client()
    try:
        future = (date.today() + timedelta(days=30)).isoformat()
        ahead = client.post(f"/fx/backfill?start=2026-01-01&end={future}")
        reversed_range = client.post("/fx/backfill?start=2026-03-06&end=2026-03-02")
    finally:
        app.dependency_overrides.clear()

    assert ahead.status_code == 422 and "future" in ahead.json()["detail"]
    assert reversed_range.status_code == 422
    assert "after end date" in reversed_range.json()["detail"]
    assert called == [], "neither refusal reaches the range"


def test_the_backfill_route_passes_its_range_through_in_order(monkeypatch: MonkeyPatch) -> None:
    """The extraction has to preserve argument order: `fill_range(conn, end, start)` fetches
    nothing and reports a clean backfill, which is worse than an error."""
    called = _route_stub(monkeypatch)
    client = _owned_client()
    try:
        response = client.post("/fx/backfill?start=2026-03-02&end=2026-03-06")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"written": 7}
    assert called == [(date(2026, 3, 2), date(2026, 3, 6))], "start first, end second"


def test_the_same_connection_can_be_asked_twice() -> None:
    """Reading opens a transaction, so a function that guards on IDLE and leaves one open
    refuses its own next call — and refuses a pipeline that read a layout profile first."""
    conn = FakeConn(covered={MONDAY})
    source = FakeSource(_published([MONDAY]))
    for _ in range(2):
        assert ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=source) == 0
    assert conn.transaction_status == TransactionStatus.IDLE, "it leaves the state it demanded"


def test_a_refusal_still_closes_the_reads_it_opened() -> None:
    """The rollback is in a finally: a caller that catches the refusal and retries — or that
    goes on to open the staging transaction — must not inherit this function's open reads."""
    conn = FakeConn()
    with pytest.raises(FxCoverageError):
        ensure_rates(cast("psycopg.Connection", conn), [MONDAY], source=FailingSource())
    assert conn.transaction_status == TransactionStatus.IDLE
    assert conn.rollbacks >= 1
