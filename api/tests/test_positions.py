"""Exact acquisition-only position arithmetic.

These tests derive every expected value by hand.  They protect the boundary between frozen
historical contributions and current valuation: only the current value uses the as-of FX legs.
"""

from decimal import Decimal

from oneledger.investments.positions import calculate_position


def test_current_value_and_gain_use_exact_decimal_arithmetic() -> None:
    """Changing value to binary-float arithmetic or subtracting the wrong cost must fail."""
    result = calculate_position(
        units=Decimal("4.12500000"),
        contribution_native=Decimal("21.7500"),
        contribution_base=Decimal("21.7500"),
        contribution_secondary=Decimal("43.5000"),
        price=Decimal("6.250000"),
        rate_to_base=Decimal("1.0000000000"),
        rate_to_secondary=Decimal("2.0000000000"),
    )

    assert result.current_value_native == Decimal("25.7813")
    assert result.current_value_base == Decimal("25.7813")
    # The native money fact fixes first at 4 dp, matching valuation_snapshot's frozen-value rule.
    assert result.current_value_secondary == Decimal("51.5626")
    assert result.gain_native == Decimal("4.0313")
    assert result.gain_base == Decimal("4.0313")
    assert result.gain_secondary == Decimal("8.0626")
    assert result.calculation_status == "available"


def test_historical_contributions_remain_frozen_when_current_fx_moves() -> None:
    """Re-translating contribution cost at the current rate would rewrite economic history."""
    result = calculate_position(
        units=Decimal("2.00000000"),
        contribution_native=Decimal("10.0000"),
        contribution_base=Decimal("20.0000"),
        contribution_secondary=Decimal("5.0000"),
        price=Decimal("7.000000"),
        rate_to_base=Decimal("3.0000000000"),
        rate_to_secondary=Decimal("4.0000000000"),
    )

    assert result.current_value_native == Decimal("14.0000")
    assert result.current_value_base == Decimal("42.0000")
    assert result.current_value_secondary == Decimal("56.0000")
    assert result.gain_native == Decimal("4.0000")
    assert result.gain_base == Decimal("22.0000")
    assert result.gain_secondary == Decimal("51.0000")


def test_missing_price_abstains_from_every_value_and_gain() -> None:
    """Treating absent evidence as zero would manufacture a loss and understate net worth."""
    result = calculate_position(
        units=Decimal("3.00000000"),
        contribution_native=Decimal("12.0000"),
        contribution_base=Decimal("12.0000"),
        contribution_secondary=Decimal("24.0000"),
        price=None,
        rate_to_base=Decimal("1.0000000000"),
        rate_to_secondary=Decimal("2.0000000000"),
    )

    assert result.current_value_native is None
    assert result.current_value_base is None
    assert result.current_value_secondary is None
    assert result.gain_native is None
    assert result.gain_base is None
    assert result.gain_secondary is None


def test_each_missing_current_fx_leg_abstains_independently() -> None:
    """One unavailable reporting conversion must not erase the native or other-currency fact."""
    result = calculate_position(
        units=Decimal("3.00000000"),
        contribution_native=Decimal("12.0000"),
        contribution_base=Decimal("12.0000"),
        contribution_secondary=Decimal("24.0000"),
        price=Decimal("5.000000"),
        rate_to_base=Decimal("1.0000000000"),
        rate_to_secondary=None,
    )

    assert result.current_value_native == Decimal("15.0000")
    assert result.current_value_base == Decimal("15.0000")
    assert result.gain_base == Decimal("3.0000")
    assert result.current_value_secondary is None
    assert result.gain_secondary is None


def test_legal_stored_scales_abstain_when_the_derived_money_is_out_of_range() -> None:
    """Legal NUMERIC values must not escape as InvalidOperation or huge derived money."""
    result = calculate_position(
        units=Decimal("99999999999999999999.99999999"),
        contribution_native=Decimal("100000000000000.0000"),
        contribution_base=Decimal("100000000000000.0000"),
        contribution_secondary=Decimal("200000000000000.0000"),
        price=Decimal("99999999999999.999999"),
        rate_to_base=Decimal("1.0000000000"),
        rate_to_secondary=Decimal("2.0000000000"),
    )

    assert result.calculation_status == "outside_supported_range"
    assert result.current_value_native is None
    assert result.current_value_base is None
    assert result.current_value_secondary is None
    assert result.gain_native is None
    assert result.gain_base is None
    assert result.gain_secondary is None
