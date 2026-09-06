# api/oneledger/routes/fx.py
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..fx import (
    EcbFrankfurterSource,
    Peg,
    anchor_chain,
    carry_forward,
    cross_rate,
    terminal_anchor,
)

router = APIRouter(prefix="/fx", tags=["fx"])


class FxRateResponse(BaseModel):
    base: str = Field(min_length=3, max_length=3)
    quote: str = Field(min_length=3, max_length=3)
    rate: str
    source: str
    carried: bool
    carried_from: date | None


def reporting_config(conn: psycopg.Connection) -> tuple[str, list[str], dict[str, Peg]]:
    """What this deployment needs quoted: (base currency, the others, the pegs).

    Read, never assumed. A household reporting in EUR against CHF fetches EUR and CHF.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT base_currency, secondary_currency FROM household LIMIT 1")
        row = cur.fetchone()
        if row is None:
            # Seeded by Task 3. Failing here with a sentence beats unpacking None.
            raise RuntimeError("no household row — run the Task 3 seed before backfilling")
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


@router.post("/backfill")
def backfill(
    start: date, end: date, _: Annotated[OwnerSession, Depends(require_owner)]
) -> dict[str, int]:
    # carry_forward fills every calendar day in the range, so an end date in the future
    # writes the last known rate forward into it — years of invented history from one typo.
    # Nothing is silently invented (Invariant 7): refuse rather than clamp, so the caller
    # learns their range was wrong instead of quietly getting a different one.
    # One day of slack, deliberately. The service runs UTC and the caller may not: a request
    # for their genuine "today" from UTC+4 arrives a calendar day ahead of the server's, and
    # rejecting it would refuse a correct range. A typo like 2030-01-01 is still refused.
    if end > date.today() + timedelta(days=1):
        raise HTTPException(status_code=422, detail="end date is in the future")
    if start > end:
        raise HTTPException(status_code=422, detail="start date is after end date")

    with connect() as conn:
        quote, bases, pegs = reporting_config(conn)
        # A pegged currency is derived from its anchor, so only the end of each chain is
        # fetched. `bases` already carries the intermediates; they are stored, not requested.
        fetch = sorted({terminal_anchor(c, pegs) for c in [quote, *bases]})
        # Frankfurter snaps a range start backward to the previous publication day, so asking
        # from 10 days earlier guarantees carry_forward has something to fill from.
        published = EcbFrankfurterSource().fetch_range(start - timedelta(days=10), end, fetch)
        written = upsert_rates(conn, to_rows(published, start, end, quote, bases, pegs))
    return {"written": written}


@router.get("/{on}")
def rate_on(on: date, _: Annotated[OwnerSession, Depends(require_owner)]) -> list[FxRateResponse]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT base_currency, quote_currency, rate, source,
                      is_carried_forward, carried_from
               FROM fx_rate WHERE rate_date = %s ORDER BY base_currency""",
            (on,),
        )
        return [
            FxRateResponse(
                base=b,
                quote=q,
                rate=str(r),
                source=s,
                carried=c,
                carried_from=cf,
            )
            for b, q, r, s, c, cf in cur.fetchall()
        ]
