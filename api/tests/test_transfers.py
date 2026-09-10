"""The market rate a pair is scored at, derived from rates quoted against the household base.

`fx_rate` holds everything as `1 <currency> = <rate> <base>`, so a rate between two account
currencies is a cross through the base — a division, which for most real rates does not
terminate. It is fixed at the precision rates are stored at, as `money._frozen` fixes its own
crosses, so the rate a candidate was scored at is one the ledger could have stored.

No database: these are values in, a value out. Every rate is invented.
"""

from __future__ import annotations

from decimal import Decimal

from oneledger.routes.transfers import pair_rate

BASE = "XXX"
TO_BASE = {"YYY": Decimal("4.0000000000"), "ZZZ": Decimal("3.0000000000")}


def test_one_currency_is_parity() -> None:
    assert pair_rate(TO_BASE, BASE, "YYY", "YYY") == Decimal(1)


def test_into_the_base_is_the_stored_rate() -> None:
    assert pair_rate(TO_BASE, BASE, "YYY", BASE) == Decimal("4.0000000000")


def test_out_of_the_base_is_its_reciprocal() -> None:
    assert pair_rate(TO_BASE, BASE, BASE, "YYY") == Decimal("0.2500000000")


def test_between_two_non_base_currencies_is_a_cross_fixed_at_ten_places() -> None:
    """4 / 3 does not terminate. Unfixed it is a 28-digit Decimal no rate column can hold."""
    rate = pair_rate(TO_BASE, BASE, "YYY", "ZZZ")
    assert rate == Decimal("1.3333333333")
    assert rate is not None and rate.as_tuple().exponent == -10


def test_a_missing_leg_is_no_rate_rather_than_parity() -> None:
    """Invariant 7. The scorer reads None as 'nothing can say whether the amounts agree';
    a fallback of 1 would read two currencies' figures as one currency's."""
    assert pair_rate(TO_BASE, BASE, "YYY", "QQQ") is None
    assert pair_rate(TO_BASE, BASE, "QQQ", BASE) is None
