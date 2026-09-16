"""`oneledger.recurring` without a database: the rules that decide whether a group of spending
rows is proposed at all, the amount cluster it belongs to, and the amount that proposal carries.
All values are synthetic."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from oneledger.recurring import (
    CADENCE_WINDOWS,
    SpendRow,
    amounts_are_stable,
    detect_candidates,
)
from oneledger.routes.recurring import _LOOKBACK_MONTHS, _months_back

CURRENCY = "USD"


def rows(
    gaps: list[int], amounts: list[str] | None = None, *, currency: str = CURRENCY
) -> list[SpendRow]:
    """One group of `len(gaps) + 1` rows, spaced by `gaps` days from a fixed start, sharing one
    identity (a constant description) and currency."""
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
            amount=Decimal(value),
            currency=currency,
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
    """50, 50, 60, 60 clusters as one group (60 sits within 20% of the running median), whose
    typical amount is the average of the middle two sorted values."""
    proposed = detect_candidates(rows([30, 30, 31], ["50.0000", "50.0000", "60.0000", "60.0000"]))
    assert len(proposed) == 1
    assert proposed[0].typical_amount == Decimal("55.0000")
    assert proposed[0].native_amount == Decimal("60.0000")


def test_three_yearly_charges_are_proposed_as_annual() -> None:
    proposed = detect_candidates(rows([365, 366]))
    assert len(proposed) == 1
    assert proposed[0].cadence == "annual"


def test_semiannual_detected() -> None:
    proposed = detect_candidates(rows([182, 183]))
    assert len(proposed) == 1
    assert proposed[0].cadence == "semiannual"


def test_the_lookback_window_holds_three_yearly_charges() -> None:
    """The route reads only this window. Three yearly charges at the widest annual spacing span two
    gaps of the window's upper bound; at 18 months they never fit, so no yearly subscription was
    ever proposed."""
    today = date(2026, 9, 16)
    widest_annual_gap = CADENCE_WINDOWS["annual"][1]
    assert _months_back(today, _LOOKBACK_MONTHS) <= today - timedelta(days=2 * widest_annual_gap)


def test_amounts_are_stable() -> None:
    assert amounts_are_stable([Decimal("100"), Decimal("100"), Decimal("104")])
    assert not amounts_are_stable([Decimal("100"), Decimal("100"), Decimal("110")])
    assert amounts_are_stable([])


def test_clustering_is_order_independent_for_the_same_rows() -> None:
    forward = rows([30, 30, 31])
    backward = list(reversed(forward))
    assert detect_candidates(forward) == detect_candidates(backward)


def test_overlapping_clusters_stay_apart() -> None:
    """Two amount bands (10 and 50, far more than 20% apart) interleaved every 15 days: each
    band's own dates are monthly and its range overlaps the other's, so both are proposed as
    separate series rather than one merged group."""
    interleaved = rows(
        [15] * 7,
        ["10.0000", "50.0000", "10.0000", "50.0000", "10.0000", "50.0000", "10.0000", "50.0000"],
    )
    proposed = detect_candidates(interleaved)
    assert len(proposed) == 2
    amounts = sorted(c.native_amount for c in proposed)
    assert amounts == [Decimal("10.0000"), Decimal("50.0000")]
    for candidate in proposed:
        assert candidate.occurrences == 4
        assert candidate.cadence == "monthly"


def test_sequential_clusters_merge() -> None:
    """Three charges at 50, then three at 80, with no date overlap between the two amount bands:
    one series whose price changed, not two."""
    sequential = rows(
        [30, 30, 30, 30, 30],
        ["50.0000", "50.0000", "50.0000", "80.0000", "80.0000", "80.0000"],
    )
    proposed = detect_candidates(sequential)
    assert len(proposed) == 1
    assert proposed[0].occurrences == 6
    assert proposed[0].native_amount == Decimal("80.0000")


def test_a_3_percent_change_stays_one_cluster() -> None:
    amounts = ["100.0000", "100.0000", "100.0000", "103.0000"]
    proposed = detect_candidates(rows([30, 30, 30], amounts))
    assert len(proposed) == 1
    assert proposed[0].occurrences == 4
    assert proposed[0].amounts_are_stable


def test_one_odd_charge_does_not_split_a_subscription() -> None:
    proposed = detect_candidates(
        rows([30, 30, 30, 30], ["50.0000", "50.0000", "50.0000", "50.0000", "58.0000"])
    )
    assert len(proposed) == 1
    assert proposed[0].occurrences == 5


def test_a_bill_varying_30_percent_is_still_detected_through_the_fallback() -> None:
    """Alternating 100/130 (30% apart, outside the 20% cluster band) splits into two
    two-occurrence clusters that overlap in date and neither clears the occurrence bar on its
    own; the fallback evaluates the whole identity as one group instead."""
    varying = rows([30, 30, 30], ["100.0000", "130.0000", "100.0000", "130.0000"])
    proposed = detect_candidates(varying)
    assert len(proposed) == 1
    assert proposed[0].occurrences == 4
    assert proposed[0].cadence == "monthly"
    assert not proposed[0].amounts_are_stable


def test_a_second_pass_over_members_and_leftover_one_offs_proposes_nothing() -> None:
    """Review OL-001, without a database. After a first pass the subscription's charges are members;
    one-off purchases that overlapped them must not merge into a false group on the second pass."""
    from dataclasses import replace

    monthly = rows([30] * 11, ["10.0000"] * 12)
    start = monthly[0].booked_date
    one_offs = [
        replace(
            monthly[0],
            transaction_id=uuid4(),
            booked_date=start + timedelta(days=days),
            amount=Decimal(amount),
            amount_base=Decimal(amount),
        )
        for days, amount in ((41, "50.0000"), (69, "100.0000"), (100, "200.0000"))
    ]
    first = detect_candidates(monthly + one_offs)
    assert [(c.cadence, len(c.transaction_ids)) for c in first] == [("monthly", 12)]

    members = [replace(row, in_series=True) for row in monthly]
    assert detect_candidates(members + one_offs) == []


def test_new_charges_after_a_removed_series_stand_as_their_own_group() -> None:
    """A run of new charges sequential to an existing series' members does not merge into it."""
    from dataclasses import replace

    old = [replace(row, in_series=True) for row in rows([30, 30, 30], ["18.0000"] * 4)]
    later_start = old[-1].booked_date + timedelta(days=60)
    new = [
        replace(
            old[0],
            transaction_id=uuid4(),
            in_series=False,
            amount=Decimal("40.0000"),
            amount_base=Decimal("40.0000"),
            booked_date=later_start + timedelta(days=30 * i),
        )
        for i in range(3)
    ]
    proposed = detect_candidates(old + new)
    assert [(c.cadence, len(c.transaction_ids)) for c in proposed] == [("monthly", 3)]
