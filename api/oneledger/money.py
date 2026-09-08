# api/oneledger/money.py
"""The freeze helpers, in one module. Both reporting amounts are frozen at save time from the
rate as stored, and a missing FX rate refuses the write — never a fallback to 1.0 or today's
rate (Phase 1 spec B3.5, Invariant I5). The manual-transaction route and the statement import
pipeline freeze identically because they run this code, not because two copies agree."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from fastapi import HTTPException

# Money crosses the wire as a decimal string in both directions. api-contracts.html section 6.
MONEY = re.compile(r"-?\d{1,16}(\.\d{1,4})?")

CENT = Decimal("0.0001")  # amounts store at 4 dp
RATE = Decimal("0.0000000001")  # rates store at 10 dp


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ValueError("money must be a decimal string with at most four decimal places")
    return value


def _out(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _rate_leg(
    cur: psycopg.Cursor[Any], on: date, base: str, quote: str
) -> tuple[Decimal, bool, date]:
    """One stored fx_rate row: (rate, carried, the date the rate is actually from)."""
    cur.execute(
        """SELECT rate, is_carried_forward, carried_from FROM fx_rate
            WHERE rate_date = %s AND base_currency = %s AND quote_currency = %s""",
        (on, base, quote),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"no FX rate for {base}/{quote} on {on} — run the backfill first",
        )
    rate, carried, carried_from = row
    return Decimal(rate), bool(carried), (carried_from if carried else on)


def _frozen(
    cur: psycopg.Cursor[Any], on: date, txn_currency: str, base: str, secondary: str
) -> tuple[Decimal, Decimal, bool, date]:
    """(rate_to_base, rate_to_secondary, carried, effective rate date).

    fx_rate quotes everything against the household base, so the secondary leg is a cross
    through it. Intermediates stay unrounded; storage rounds once, half-up
    (multi-currency-and-fx.html)."""
    if txn_currency == base:
        rate_sb, carried, effective = _rate_leg(cur, on, secondary, base)
        return Decimal(1), Decimal(1) / rate_sb, carried, effective
    if txn_currency == secondary:
        rate_sb, carried, effective = _rate_leg(cur, on, secondary, base)
        return rate_sb, Decimal(1), carried, effective
    rate_cb, carried_cb, effective_cb = _rate_leg(cur, on, txn_currency, base)
    rate_sb, carried_sb, effective_sb = _rate_leg(cur, on, secondary, base)
    # One row stores one rate date and one carried flag. Two legs from different days cannot
    # be labelled by either one honestly, so the mismatch is refused with the cure named —
    # backfilling rates for the booked date makes the legs agree.
    if effective_cb != effective_sb:
        raise HTTPException(
            status_code=422,
            detail=(
                f"the FX legs for {on} come from different dates ({effective_cb} and "
                f"{effective_sb}); backfill rates for the booked date first"
            ),
        )
    return (
        rate_cb,
        rate_cb / rate_sb,
        carried_cb or carried_sb,
        effective_cb,
    )
