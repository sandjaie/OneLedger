# api/tests/test_fx.py
from datetime import date
from decimal import Decimal

import pytest

from oneledger.fx import Peg, carry_forward, cross_rate

# What the `currency` table holds for the first household: one peg, held since 1997.
PEGS = {"AED": Peg(anchor="USD", units_per_anchor=Decimal("3.6725"))}


def test_cross_rate_matches_published_ecb_values() -> None:
    """ECB 2024-01-02: EUR/INR 91.285, EUR/USD 1.0956. Verified against the live API."""
    published = {"INR": Decimal("91.285"), "USD": Decimal("1.0956")}
    usd_inr = cross_rate(published, "USD", "INR", PEGS)
    assert usd_inr.quantize(Decimal("0.0001")) == Decimal("83.3196")


def test_a_pegged_currency_needs_no_published_rate() -> None:
    """The ECB publishes no AED, which is exactly why the peg derivation exists."""
    published = {"INR": Decimal("91.285"), "USD": Decimal("1.0956")}
    aed_inr = cross_rate(published, "AED", "INR", PEGS)
    usd_inr = cross_rate(published, "USD", "INR", PEGS)
    # the peg identity must hold to 8 dp — test 2 of multi-currency-and-fx.html §9
    assert (aed_inr * PEGS["AED"].units_per_anchor).quantize(
        Decimal("0.00000001")
    ) == usd_inr.quantize(Decimal("0.00000001"))


def test_an_unpublished_unpegged_currency_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="neither published nor pegged"):
        cross_rate({"INR": Decimal("91.285")}, "BRL", "INR", PEGS)


def test_carry_forward_fills_gaps_and_never_interpolates() -> None:
    """A Sunday uses Friday's rate, flagged — not the average of Friday and Monday."""
    published = {date(2024, 1, 5): Decimal("83.00"), date(2024, 1, 8): Decimal("84.00")}
    filled = carry_forward(published, date(2024, 1, 5), date(2024, 1, 8))
    assert [(d.isoformat(), str(v), c.isoformat() if c else None) for d, v, c in filled] == [
        ("2024-01-05", "83.00", None),
        ("2024-01-06", "83.00", "2024-01-05"),
        ("2024-01-07", "83.00", "2024-01-05"),
        ("2024-01-08", "84.00", None),
    ]


def test_carry_forward_refuses_to_start_on_a_gap() -> None:
    with pytest.raises(ValueError, match="no published rate at or before"):
        carry_forward({date(2024, 1, 8): Decimal("84.00")}, date(2024, 1, 5), date(2024, 1, 8))
