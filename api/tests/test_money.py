"""The freeze helpers, tested where they now live. `test_transactions.py` remains the proof
that the route still behaves; these pin the pieces it composes — the storage scales, the
rounding mode Postgres uses, and which fx_rate legs a transaction currency selects."""

from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

import psycopg
import pytest
from fastapi import HTTPException

from oneledger.money import CENT, RATE, _frozen, _money, _out, _rate_leg

ON = date(2026, 3, 1)
Row = tuple[str, bool, date | None]
Rows = Mapping[tuple[date, str, str], Row]


class FxCursor:
    """The one query `_rate_leg` makes, answered from a dict keyed by (date, base, quote).
    Each value is the row as fx_rate stores it: (rate, is_carried_forward, carried_from)."""

    def __init__(self, rows: Rows) -> None:
        self.rows = rows
        self.answer: Row | None = None
        self.asked: list[tuple[str, str]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        on, base, quote = params
        self.asked.append((base, quote))
        self.answer = self.rows.get((on, base, quote))

    def fetchone(self) -> Row | None:
        return self.answer


def _as_cursor(cursor: FxCursor) -> psycopg.Cursor[Any]:
    return cast("psycopg.Cursor[Any]", cursor)


def _cursor(rows: Rows) -> psycopg.Cursor[Any]:
    return _as_cursor(FxCursor(rows))


def test_the_storage_scales_are_the_column_definitions() -> None:
    """NUMERIC(20,4) for money and NUMERIC(20,10) for a rate. A helper quantizing to the
    wrong scale writes a value the column would round differently."""
    assert CENT == Decimal("0.0001")
    assert RATE == Decimal("0.0000000001")


def test_rounding_is_half_away_from_zero_in_both_directions() -> None:
    """Postgres rounds numeric half away from zero. Python's default is half-to-even, which
    agrees on 0.00015 and disagrees on 0.00025 — so the mode is asserted, not assumed."""
    assert Decimal("0.00025").quantize(CENT, rounding=ROUND_HALF_UP) == Decimal("0.0003")
    assert Decimal("-0.00025").quantize(CENT, rounding=ROUND_HALF_UP) == Decimal("-0.0003")
    assert Decimal("0.00025").quantize(CENT) == Decimal("0.0002"), "the default would differ"


def test_money_accepts_the_wire_form_and_refuses_everything_else() -> None:
    assert _money("-310.0000") == "-310.0000"
    assert _money("12") == "12"
    assert _money(None) is None
    # NUMERIC(20,4) leaves sixteen digits before the point. This is the only gate on that
    # width — nothing between it and the INSERT counts digits again, so a seventeenth reaches
    # Postgres and comes back as a numeric overflow. The route maps that to 422, so the caller
    # is not handed a 500; they are handed the column's complaint instead of the field's, from
    # a check that needed a database round trip to make.
    assert _money("1" * 16 + ".0000") == "1" * 16 + ".0000"
    for bad in ["1.00000", "", "1,000.00", "1e4", "abc", "1" * 17, "-" + "1" * 17]:
        with pytest.raises(ValueError):
            _money(bad)
    with pytest.raises(ValueError):
        _money(310.0)  # type: ignore[arg-type]


def test_out_renders_a_decimal_without_touching_its_scale() -> None:
    assert _out(Decimal("-310.0000")) == "-310.0000"
    assert _out(None) is None


def test_a_rate_leg_reports_the_date_the_rate_is_actually_from() -> None:
    observed = _cursor({(ON, "XAE", "XIN"): ("3.0000000000", False, None)})
    assert _rate_leg(observed, ON, "XAE", "XIN") == (Decimal("3.0000000000"), False, ON)

    carried = _cursor({(ON, "XAE", "XIN"): ("3.0000000000", True, date(2026, 2, 27))})
    rate, is_carried, effective = _rate_leg(carried, ON, "XAE", "XIN")
    assert (is_carried, effective) == (True, date(2026, 2, 27)), "carried names its own date"


def test_a_missing_rate_is_the_callers_422_naming_the_pair() -> None:
    with pytest.raises(HTTPException) as raised:
        _rate_leg(_cursor({}), ON, "XAE", "XIN")
    assert raised.value.status_code == 422
    assert "XAE/XIN" in str(raised.value.detail)


def test_the_transaction_currency_selects_the_legs() -> None:
    """fx_rate quotes against the base, so base and secondary each need one leg and a third
    currency needs both. A unity leg is exact, never a rate looked up against itself — the
    legs asked for are asserted, because a cross through the same pair happens to arrive at
    the same number and would hide a currency reaching for a rate that cannot exist."""
    rows: Rows = {
        (ON, "XAE", "XIN"): ("3.0000000000", False, None),
        (ON, "XSG", "XIN"): ("60.0000000000", False, None),
    }

    in_base = FxCursor(rows)
    assert _frozen(_as_cursor(in_base), ON, "XIN", "XIN", "XAE") == (
        Decimal(1),
        Decimal(1) / Decimal("3.0000000000"),
        False,
        ON,
    )
    assert in_base.asked == [("XAE", "XIN")], "the base leg is unity, not a lookup"

    in_secondary = FxCursor(rows)
    assert _frozen(_as_cursor(in_secondary), ON, "XAE", "XIN", "XAE") == (
        Decimal("3.0000000000"),
        Decimal(1),
        False,
        ON,
    )
    assert in_secondary.asked == [("XAE", "XIN")], "one leg, read once"

    foreign = FxCursor(rows)
    assert _frozen(_as_cursor(foreign), ON, "XSG", "XIN", "XAE") == (
        Decimal("60.0000000000"),
        Decimal("60.0000000000") / Decimal("3.0000000000"),
        False,
        ON,
    )
    assert foreign.asked == [("XSG", "XIN"), ("XAE", "XIN")], "the cross reads both legs"


def test_a_single_leg_reports_its_own_carry() -> None:
    """The base and secondary branches take `carried` and the effective date from the one leg
    they read. Nothing downstream re-derives either: V13's `staged_transaction` has no CHECK
    tying `fx_rate_is_carried` to the dates the way V10's `carried_rate_dates` does on
    `transaction`, so a rate labelled observed here is labelled observed on the review screen
    (Invariant I7). A statement in the base or the secondary currency is the ordinary case."""
    carried_leg: Rows = {(ON, "XAE", "XIN"): ("3.0000000000", True, date(2026, 2, 27))}

    _, _, carried, effective = _frozen(_cursor(carried_leg), ON, "XIN", "XIN", "XAE")
    assert (carried, effective) == (True, date(2026, 2, 27)), "the base leg carries the pair"

    _, _, carried, effective = _frozen(_cursor(carried_leg), ON, "XAE", "XIN", "XAE")
    assert (carried, effective) == (True, date(2026, 2, 27)), "so does the secondary leg"

    observed: Rows = {(ON, "XAE", "XIN"): ("3.0000000000", False, None)}
    for txn_currency in ("XIN", "XAE"):
        _, _, carried, effective = _frozen(_cursor(observed), ON, txn_currency, "XIN", "XAE")
        assert (carried, effective) == (False, ON), f"{txn_currency}: observed is not carried"


def test_two_legs_from_different_dates_are_refused() -> None:
    """One row stores one rate date and one carried flag; legs from two days cannot be
    labelled by either honestly."""
    rows: Rows = {
        (ON, "XSG", "XIN"): ("60.0000000000", True, date(2026, 2, 20)),
        (ON, "XAE", "XIN"): ("3.0000000000", True, date(2026, 2, 27)),
    }
    with pytest.raises(HTTPException) as raised:
        _frozen(_cursor(rows), ON, "XSG", "XIN", "XAE")
    assert raised.value.status_code == 422
    assert "different dates" in str(raised.value.detail)


def test_a_carried_leg_carries_the_pair() -> None:
    rows: Rows = {
        (ON, "XSG", "XIN"): ("60.0000000000", False, None),
        (ON, "XAE", "XIN"): ("3.0000000000", True, ON),
    }
    _, _, carried, _ = _frozen(_cursor(rows), ON, "XSG", "XIN", "XAE")
    assert carried is True, "either leg carried makes the pair carried"
