"""Phase 4.4 Task 1's pure remittance-analysis engine: corridor and year grouping, per-provider
cost and effective rate, the weighted achieved rate, the corridor-year total row and the
cheapest-channel comparison. No database — `test_remittance_analysis_live.py` covers the routes
and their SQL.

Every figure below is hand-derived from the specification's own definitions
(`docs/superpowers/specs/2026-09-21-phase-4-4-fx-remittance-analysis.md`), not read back from the
module, so a changed rule fails here rather than agreeing with itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from oneledger.remittance_analysis import TransferFact, analyse


def _fact(
    *,
    source: str = "AED",
    destination: str = "INR",
    on: date = date(2026, 3, 1),
    sent: str = "5000",
    received: str = "111000",
    implied: str | None = "22.2000000000",
    spread: str | None = "77.6000",
    total: str | None = "77.6000",
    provider: str | None = "Exchange house",
    approximate: bool = False,
) -> TransferFact:
    return TransferFact(
        source_currency=source,
        destination_currency=destination,
        transfer_date=on,
        source_amount=Decimal(sent),
        destination_amount=Decimal(received),
        implied_rate=None if implied is None else Decimal(implied),
        fx_spread_cost_source=None if spread is None else Decimal(spread),
        total_cost_source=None if total is None else Decimal(total),
        provider=provider,
        cost_is_approximate=approximate,
    )


# ---------------------------------------------------------------------------
# Corridors and years
# ---------------------------------------------------------------------------


def test_two_currency_pairs_never_share_a_corridor() -> None:
    corridors = analyse(
        [
            _fact(destination="INR", sent="5000", received="111000"),
            _fact(destination="USD", sent="4000", received="1080", implied="0.2700000000"),
        ]
    )

    assert [(c.source_currency, c.destination_currency) for c in corridors] == [
        ("AED", "INR"),
        ("AED", "USD"),
    ]
    assert [c.years[0].total_sent_source for c in corridors] == [Decimal("5000"), Decimal("4000")]


def test_the_busiest_corridor_is_listed_first() -> None:
    corridors = analyse(
        [
            _fact(destination="USD", sent="9000", received="2430"),
            _fact(destination="INR", sent="1000", received="22200"),
        ]
    )

    assert [c.destination_currency for c in corridors] == ["USD", "INR"]


def test_years_are_newest_first_and_hold_only_their_own_transfers() -> None:
    corridors = analyse(
        [
            _fact(on=date(2025, 6, 1), sent="1000"),
            _fact(on=date(2026, 6, 1), sent="2000"),
        ]
    )

    [corridor] = corridors
    assert [year.year for year in corridor.years] == [2026, 2025]
    assert [year.total_sent_source for year in corridor.years] == [
        Decimal("2000"),
        Decimal("1000"),
    ]


# ---------------------------------------------------------------------------
# Per-provider figures
# ---------------------------------------------------------------------------


def test_a_providers_sums_and_effective_rate_are_the_hand_computed_figures() -> None:
    # AED 10,000 sent for AED 250 of cost, AED 30 of it an explicit fee folded into the total.
    corridors = analyse(
        [
            _fact(sent="6000", received="133200", spread="120.0000", total="150.0000"),
            _fact(
                sent="4000", received="88800", spread="100.0000", total="100.0000", approximate=True
            ),
        ]
    )

    [group] = corridors[0].years[0].providers
    assert group.provider == "Exchange house"
    assert group.transfer_count == 2
    assert group.sent_source == Decimal("10000")
    assert group.costed_sent_source == Decimal("10000")
    assert group.spread_source == Decimal("220.0000")
    assert group.total_cost_source == Decimal("250.0000")
    # The fee line by subtraction: 250 total − 220 spread.
    assert group.fees_source == Decimal("30.0000")
    # 250 / 10,000 = 2.5%
    assert group.effective_cost_pct == Decimal("2.5000")
    assert group.approximate_count == 1


def test_fees_and_spread_always_sum_to_the_total_across_a_mixed_fixture() -> None:
    corridors = analyse(
        [
            _fact(provider="A", sent="1000", spread="10.0000", total="17.5000"),
            _fact(provider="A", sent="2000", spread=None, total="5.0000"),
            _fact(provider="B", sent="3000", spread="42.1234", total="42.1234"),
            _fact(provider=None, sent="500", spread="9.9999", total="12.3456"),
        ]
    )

    year = corridors[0].years[0]
    groups = [*year.providers, year.unattributed]
    for group in groups:
        assert group is not None
        assert group.fees_source is not None and group.spread_source is not None
        assert group.fees_source + group.spread_source == group.total_cost_source
    assert year.total_fees_source is not None and year.total_spread_source is not None
    assert year.total_fees_source + year.total_spread_source == year.total_cost_source


def test_an_uncosted_transfer_counts_as_sent_but_not_as_costed() -> None:
    # A same-currency remittance: FX_COST never ran, and no fee has been recorded.
    corridors = analyse(
        [
            _fact(sent="1000", spread="25.0000", total="25.0000"),
            _fact(
                source="AED",
                destination="AED",
                sent="2000",
                received="2000",
                implied=None,
                spread=None,
                total=None,
            ),
        ]
    )

    cross_currency = next(c for c in corridors if c.destination_currency != c.source_currency)
    same_currency = next(c for c in corridors if c.destination_currency == c.source_currency)

    [priced] = cross_currency.years[0].providers
    assert priced.sent_source == Decimal("1000")
    assert priced.costed_sent_source == Decimal("1000")

    [unpriced] = same_currency.years[0].providers
    assert unpriced.transfer_count == 1
    assert unpriced.sent_source == Decimal("2000")
    assert unpriced.costed_sent_source == Decimal("0")
    assert unpriced.fees_source is None
    assert unpriced.spread_source is None
    assert unpriced.total_cost_source is None
    assert unpriced.effective_cost_pct is None
    assert unpriced.avg_rate is None


def test_the_achieved_rate_is_weighted_by_amount_not_a_mean_of_rates() -> None:
    # 9,000 at 22.0 and 1,000 at 24.0. Weighted: 220,000 / 10,000 = 22.2. A simple mean of the
    # two rates would be 23.0, so this fixture tells the two apart.
    corridors = analyse(
        [
            _fact(sent="9000", received="198000", implied="22.0000000000"),
            _fact(sent="1000", received="24000", implied="24.0000000000"),
        ]
    )

    [group] = corridors[0].years[0].providers
    assert group.avg_rate == Decimal("22.2000000000")


def test_a_same_currency_leg_is_left_out_of_the_achieved_rate() -> None:
    corridors = analyse(
        [
            _fact(sent="1000", received="22200", implied="22.2000000000"),
            _fact(sent="1000", received="1000", implied=None, spread=None, total=None),
        ]
    )

    [group] = corridors[0].years[0].providers
    assert group.avg_rate == Decimal("22.2000000000")


def test_providers_are_ordered_cheapest_first_with_an_unrated_one_last() -> None:
    corridors = analyse(
        [
            _fact(provider="Dear", sent="1000", spread="30.0000", total="30.0000"),
            _fact(provider="Cheap", sent="1000", spread="10.0000", total="10.0000"),
            _fact(provider="Unknown cost", sent="1000", spread=None, total=None),
        ]
    )

    assert [g.provider for g in corridors[0].years[0].providers] == [
        "Cheap",
        "Dear",
        "Unknown cost",
    ]


# ---------------------------------------------------------------------------
# The corridor-year total row
# ---------------------------------------------------------------------------


def test_the_total_row_covers_named_and_unattributed_money_alike() -> None:
    corridors = analyse(
        [
            _fact(provider="A", sent="1000", spread="10.0000", total="10.0000"),
            _fact(provider=None, sent="3000", spread="90.0000", total="90.0000"),
        ]
    )

    year = corridors[0].years[0]
    assert year.total_sent_source == Decimal("4000")
    assert year.total_costed_sent_source == Decimal("4000")
    assert year.total_cost_source == Decimal("100.0000")
    # 100 / 4,000 = 2.5%
    assert year.total_effective_cost_pct == Decimal("2.5000")
    assert year.unattributed is not None
    assert year.unattributed.sent_source == Decimal("3000")


def test_a_year_with_nothing_costed_reports_no_total_cost_or_percentage() -> None:
    corridors = analyse([_fact(sent="1000", spread=None, total=None)])

    year = corridors[0].years[0]
    assert year.total_sent_source == Decimal("1000")
    assert year.total_costed_sent_source == Decimal("0")
    assert year.total_cost_source is None
    assert year.total_fees_source is None
    assert year.total_spread_source is None
    assert year.total_effective_cost_pct is None


# ---------------------------------------------------------------------------
# The cheapest-channel comparison
# ---------------------------------------------------------------------------


def test_one_costed_provider_is_never_crowned_cheapest() -> None:
    corridors = analyse(
        [
            _fact(provider="Only one", sent="1000", spread="20.0000", total="20.0000"),
            _fact(provider="No cost yet", sent="1000", spread=None, total=None),
        ]
    )

    year = corridors[0].years[0]
    assert year.cheapest_provider is None
    assert year.potential_savings_source is None


def test_two_providers_yield_the_cheapest_and_the_hand_computed_saving() -> None:
    # Dear: 10,000 sent, 300 cost → 3%. Cheap: 10,000 sent, 100 cost → 1%.
    # Incurred 400. All 20,000 at 1% would have cost 200. Saving 200.
    corridors = analyse(
        [
            _fact(provider="Dear", sent="10000", spread="300.0000", total="300.0000"),
            _fact(provider="Cheap", sent="10000", spread="100.0000", total="100.0000"),
        ]
    )

    year = corridors[0].years[0]
    assert year.cheapest_provider == "Cheap"
    assert year.potential_savings_source == Decimal("200.0000")


def test_a_corridor_already_entirely_on_the_cheapest_channel_saves_exactly_zero() -> None:
    corridors = analyse(
        [
            _fact(provider="Same", sent="10000", spread="100.0000", total="100.0000"),
            _fact(provider="Also same", sent="10000", spread="100.0000", total="100.0000"),
        ]
    )

    year = corridors[0].years[0]
    assert year.potential_savings_source == Decimal("0.0000")


def test_unattributed_money_never_enters_the_comparison() -> None:
    # Two named providers at 3% and 1%, plus unlabelled money at 0.1%. The unlabelled row must
    # not become the cheapest channel, and its 50,000 must not inflate the saving.
    corridors = analyse(
        [
            _fact(provider="Dear", sent="10000", spread="300.0000", total="300.0000"),
            _fact(provider="Cheap", sent="10000", spread="100.0000", total="100.0000"),
            _fact(provider=None, sent="50000", spread="50.0000", total="50.0000"),
        ]
    )

    year = corridors[0].years[0]
    assert year.cheapest_provider == "Cheap"
    assert year.potential_savings_source == Decimal("200.0000")
    assert year.total_sent_source == Decimal("70000")


def test_a_favourable_spread_keeps_its_sign_but_manufactures_no_saving() -> None:
    # The plan-review fixture: provider A's achieved rate beat the mid-market rate for its date,
    # so FX_COST stored a negative cost. Projecting −1% onto 20,000 would state a saving of 300 —
    # more than the 200 ever spent, on a rate that was luck rather than a tariff.
    corridors = analyse(
        [
            _fact(provider="Favourable", sent="10000", spread="-100.0000", total="-100.0000"),
            _fact(provider="Ordinary", sent="10000", spread="200.0000", total="200.0000"),
        ]
    )

    year = corridors[0].years[0]
    favourable = next(g for g in year.providers if g.provider == "Favourable")
    # The evidence is reported as it stands.
    assert favourable.total_cost_source == Decimal("-100.0000")
    assert favourable.effective_cost_pct == Decimal("-1.0000")
    assert year.cheapest_provider == "Favourable"
    # Incurred is 100 across both. The cheapest rate clamps to 0%, so the counterfactual is 0 and
    # the saving is bounded by the money actually spent.
    assert year.total_cost_source == Decimal("100.0000")
    assert year.potential_savings_source == Decimal("100.0000")


def test_a_corridor_that_came_out_ahead_overall_is_offered_no_saving() -> None:
    corridors = analyse(
        [
            _fact(provider="Favourable", sent="10000", spread="-300.0000", total="-300.0000"),
            _fact(provider="Ordinary", sent="10000", spread="100.0000", total="100.0000"),
        ]
    )

    year = corridors[0].years[0]
    assert year.total_cost_source == Decimal("-200.0000")
    assert year.potential_savings_source == Decimal("0.0000")


def test_nothing_is_reported_for_an_empty_ledger() -> None:
    assert analyse([]) == []
