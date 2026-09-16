"""`oneledger.recurring` without a database: the rules that decide whether a group of spending
rows is proposed at all, and the amount that proposal carries. All values are synthetic."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from oneledger.recurring import CADENCE_WINDOWS, SpendRow, detect_candidates
from oneledger.routes.recurring import _LOOKBACK_MONTHS, _months_back


def rows(gaps: list[int], amounts: list[str] | None = None) -> list[SpendRow]:
    """One group of `len(gaps) + 1` rows, spaced by `gaps` days from a fixed start."""
    on = date(2026, 1, 5)
    dates = [on]
    for gap in gaps:
        on = on + timedelta(days=gap)
        dates.append(on)
    values = amounts or ["50.0000"] * len(dates)
    assert len(values) == len(dates)
    return [
        SpendRow(
            transaction_id=uuid4(),
            booked_date=booked,
            amount_base=Decimal(value),
            merchant_id=None,
            merchant_display_name=None,
            description_clean=None,
            description_raw="Synthetic recurring row",
            category_id=None,
        )
        for booked, value in zip(dates, values, strict=True)
    ]


def test_a_group_whose_gaps_mostly_miss_the_window_is_not_proposed() -> None:
    """The median alone is not enough: 30, 30, 5, 200 has a monthly median, but only half its
    gaps fall inside the monthly window, so the group is irregular and nothing is proposed."""
    assert detect_candidates(rows([30, 30, 5, 200])) == []


def test_a_group_whose_gaps_mostly_hit_the_window_is_proposed() -> None:
    """The same shape with three of four gaps inside the window clears the two-thirds bar."""
    proposed = detect_candidates(rows([30, 30, 31, 200]))
    assert len(proposed) == 1
    assert proposed[0].cadence == "monthly"


def test_two_occurrences_are_never_proposed() -> None:
    assert detect_candidates(rows([30])) == []


def test_the_typical_amount_of_an_even_number_of_rows_averages_the_middle_two() -> None:
    proposed = detect_candidates(rows([30, 30, 31], ["50.0000", "50.0000", "60.0000", "60.0000"]))
    assert len(proposed) == 1
    assert proposed[0].typical_amount_base == Decimal("55.0000")


def test_three_yearly_charges_are_proposed_as_annual() -> None:
    proposed = detect_candidates(rows([365, 366]))
    assert len(proposed) == 1
    assert proposed[0].cadence == "annual"


def test_the_lookback_window_holds_three_yearly_charges() -> None:
    """The route reads only this window. Three yearly charges at the widest annual spacing span two
    gaps of the window's upper bound; at 18 months they never fit, so no yearly subscription was
    ever proposed."""
    today = date(2026, 9, 16)
    widest_annual_gap = CADENCE_WINDOWS["annual"][1]
    assert _months_back(today, _LOOKBACK_MONTHS) <= today - timedelta(days=2 * widest_annual_gap)
