# api/oneledger/money.py
"""The freeze helpers, in one module. Both reporting amounts are frozen at save time from the
rate as stored, and a missing FX rate refuses the write — never a fallback to 1.0 or today's
rate (Phase 1 spec B3.5, Invariant I5). The manual-transaction route and the statement import
pipeline freeze identically because they run this code, not because two copies agree."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg
from fastapi import HTTPException

# Money crosses the wire as a decimal string in both directions. api-contracts.html section 6.
MONEY = re.compile(r"-?\d{1,16}(\.\d{1,4})?")

CENT = Decimal("0.0001")  # amounts store at 4 dp
RATE = Decimal("0.0000000001")  # rates store at 10 dp
_ZERO = Decimal("0.0000")
_RATIO_PLACES = Decimal("0.0001")


def chart_ratios[K](totals: Mapping[K, Decimal]) -> dict[K, str]:
    """Each key's total divided by the largest absolute total in the mapping, `Decimal` at 4
    places half-even — never half-up, which would nudge a value exactly between two bars a fixed
    direction on every chart. `"0.0000"` for every key when the largest magnitude is zero, so an
    all-zero chart draws flat bars instead of a division by zero. Moved here from
    `routes/wealth_history.py::_chart_ratios` (Phase 4.1 Task 1), which was keyed by `date` alone
    and could not serve `routes/spending.py`'s `UUID | None` and `str | None` keys; wealth
    history's own values are unchanged by the move."""
    max_abs = max((abs(total) for total in totals.values()), default=_ZERO)
    if max_abs == 0:
        return {key: "0.0000" for key in totals}
    return {
        # `+ _ZERO` turns a rounded negative zero into "0.0000".
        key: str((total / max_abs).quantize(_RATIO_PLACES, rounding=ROUND_HALF_EVEN) + _ZERO)
        for key, total in totals.items()
    }


def chart_coordinates[K](totals: Mapping[K, Decimal]) -> dict[K, int]:
    """Signed, bounded integer display positions on one shared Decimal scale."""
    return {key: int(Decimal(ratio) * 10000) for key, ratio in chart_ratios(totals).items()}


def _money(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not MONEY.fullmatch(value):
        raise ValueError("money must be a decimal string with at most four decimal places")
    return value


def _out(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _decimal_eq(stored: Decimal | None, value: str | None) -> bool:
    """True when a stored `Decimal` and a wire decimal string represent the same amount and
    the same nullness — an idempotency replay's stored-vs-submitted comparison, shared by every
    route that checks a nullable money field on retry."""
    if (stored is None) != (value is None):
        return False
    return stored is None or Decimal(stored) == Decimal(value)  # type: ignore[arg-type]


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
    through it — a division, which for most real rates does not terminate. **Both legs are
    returned already fixed at the stored precision** (10 dp, half-up), because the stored rate
    is the auditable fact and every amount must derive from it: `round(amount x stored rate)`
    is what `transaction.frozen_amounts_derive` and `StagedTransaction` both check against, and
    a repeating cross-rate rounded only after the multiplication disagrees with it.

    Fixing the rate here rather than in each caller is deliberate. `1 / 3` is a 28-digit
    Decimal, and `NUMERIC(20,10)` — and the `Rate` annotation that mirrors it — refuse it
    outright, so a caller that forgot to quantize did not merely round differently: it could
    not store the row at all (multi-currency-and-fx.html).
    """
    if txn_currency == base:
        rate_sb, carried, effective = _rate_leg(cur, on, secondary, base)
        return Decimal(1), _fixed(Decimal(1) / rate_sb), carried, effective
    if txn_currency == secondary:
        rate_sb, carried, effective = _rate_leg(cur, on, secondary, base)
        return _fixed(rate_sb), Decimal(1), carried, effective
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
        _fixed(rate_cb),
        _fixed(rate_cb / rate_sb),
        carried_cb or carried_sb,
        effective_cb,
    )


def translate(amount: Decimal, rate: Decimal) -> Decimal:
    """A flow's frozen figure: the amount times the stored rate, fixed at four places, half away
    from zero — as Postgres rounds, because `frozen_amounts_derive` compares against
    `round(amount * rate, 4)` and any other rule stages a row the ledger's own CHECK refuses.

    This is the one place outside a migration where a transaction amount meets a rate (I5).
    The manual route, the import pipeline and the staging validator all call it, so they freeze
    identically because they run this code rather than because three copies happen to agree —
    and `scripts/check-identifiers.py` refuses a fourth. `InvalidOperation` is the caller's to
    name, because only the caller knows which row it was.
    """
    return (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)


def _fixed(rate: Decimal) -> Decimal:
    """One rate at the precision it is stored at, half away from zero, as Postgres rounds."""
    try:
        return rate.quantize(RATE, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        # A rate too large to state at 10 dp is not a rate this ledger can freeze against, and
        # saying so beats an InvalidOperation surfacing from a pydantic validator as a 500.
        raise HTTPException(
            status_code=422, detail="the exchange rate exceeds the storable range"
        ) from exc
