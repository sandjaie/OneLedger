"""Scoring a candidate pair, on values.

`transfers-and-reconciliation.html` §3 is the specification and this suite is written against
its figures rather than against the implementation: the weights, the 5% cross-currency
tolerance, the per-kind date windows and the presentation bands are all pinned to the numbers
that page states. Every amount here is invented.

The module answers a question and writes nothing. It issues no query either — the market rate,
the pair history and the rule hint arrive as arguments, because `ingest/` may not import from
`routes/`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from oneledger.ingest.transfers import (
    AMOUNT_WEIGHT,
    DATE_WEIGHT,
    DESCRIPTION_WEIGHT,
    PAIR_HISTORY_WEIGHT,
    RULE_HINT_WEIGHT,
    Evidence,
    Leg,
    StatedRemittance,
    band,
    date_window,
    propose_kind,
    score_match,
    stated_remittance,
)

SOURCE = UUID("11111111-1111-4111-8111-111111111111")
DESTINATION = UUID("22222222-2222-4222-8222-222222222222")
ELSEWHERE = UUID("33333333-3333-4333-8333-333333333333")
BANK = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_BANK = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def leg(
    account: UUID = SOURCE,
    amount: str = "-1000.0000",
    currency: str = "XXX",
    day: int = 2,
    description: str = "OUTWARD TRANSFER",
    **changes: object,
) -> Leg:
    values: dict[str, object] = {
        "transaction_id": uuid4(),
        "account_id": account,
        "institution_id": BANK,
        "booked_date": date(2026, 3, day),
        "amount": Decimal(amount),
        "currency": currency,
        "description_clean": description,
        "counterparty_raw": None,
        "reference_raw": None,
        "is_transfer_candidate": False,
        "counterparty_account_id": None,
    }
    values.update(changes)
    return Leg(**values)  # type: ignore[arg-type]


def evidence(**changes: object) -> Evidence:
    values: dict[str, object] = {
        "kind": "internal_same_currency",
        "market_rate": None,
        "confirmed_pairs": 0,
    }
    values.update(changes)
    return Evidence(**values)  # type: ignore[arg-type]


def credit(**changes: object) -> Leg:
    changes.setdefault("account", DESTINATION)
    changes.setdefault("amount", "1000.0000")
    changes.setdefault("description", "INWARD TRANSFER")
    return leg(**changes)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# The three refusals. Each is a shape the scorer must not put a number on at all — a nonzero
# score on any of them would surface a pair the owner could confirm into a wrong ledger.


def test_a_pair_inside_one_account_is_not_a_transfer() -> None:
    assert score_match(leg(), credit(account=SOURCE), evidence()) == 0.0


def test_the_source_leg_must_be_a_debit() -> None:
    assert score_match(leg(amount="1000.0000"), credit(), evidence()) == 0.0


def test_the_destination_leg_must_be_a_credit() -> None:
    assert score_match(leg(), credit(amount="-1000.0000"), evidence()) == 0.0


def test_a_zero_leg_is_neither() -> None:
    """`>= 0` and `<= 0` in §3, so zero fails both halves rather than passing one."""
    assert score_match(leg(amount="0.0000"), credit(), evidence()) == 0.0
    assert score_match(leg(), credit(amount="0.0000"), evidence()) == 0.0


# ---------------------------------------------------------------------------------------
# Amount, 35%. Same currency first.


def component(total: float, *, without: float) -> float:
    """The weight a single signal contributed, given a total and the total without it."""
    return round(total - without, 10)


def test_an_exact_amount_scores_the_whole_amount_weight() -> None:
    matched = score_match(leg(), credit(), evidence())
    missed = score_match(leg(), credit(amount="5000.0000"), evidence())
    assert component(matched, without=missed) == pytest.approx(AMOUNT_WEIGHT)


def test_a_deducted_fee_still_scores() -> None:
    """A transfer that arrives 1% short is the ordinary case, not a non-match: 1 - 0.01*20."""
    scored = score_match(leg(), credit(amount="990.0000"), evidence())
    floor = score_match(leg(), credit(amount="5000.0000"), evidence())
    assert component(scored, without=floor) == pytest.approx(AMOUNT_WEIGHT * 0.80, abs=1e-9)


def test_an_amount_five_percent_short_scores_nothing_on_amount() -> None:
    """Past 1/20 the same-currency curve is at zero, and never below it."""
    scored = score_match(leg(), credit(amount="940.0000"), evidence())
    floor = score_match(leg(), credit(amount="5000.0000"), evidence())
    assert component(scored, without=floor) == 0.0


# ---------------------------------------------------------------------------------------
# Amount, cross-currency. The 5% tolerance is the point: an exchange house takes a spread and
# a strict match would miss every genuine remittance.


def test_a_cross_currency_pair_is_converted_at_the_rate_for_the_debit_s_date() -> None:
    scored = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="22000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=Decimal("22")),
    )
    missed = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="90000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=Decimal("22")),
    )
    assert component(scored, without=missed) == pytest.approx(AMOUNT_WEIGHT)


def test_a_two_percent_spread_still_scores_most_of_the_amount_weight() -> None:
    """0.5–3% is what an exchange house takes; 1 - 0.02/0.05 = 0.6 of the weight."""
    scored = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="21560.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=Decimal("22")),
    )
    floor = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="90000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=Decimal("22")),
    )
    assert component(scored, without=floor) == pytest.approx(AMOUNT_WEIGHT * 0.6, abs=1e-9)


def test_a_six_percent_spread_is_more_likely_a_coincidence() -> None:
    scored = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="20680.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=Decimal("22")),
    )
    floor = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="90000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=Decimal("22")),
    )
    assert component(scored, without=floor) == 0.0


def test_no_rate_scores_no_amount_rather_than_a_guessed_one() -> None:
    """Invariant 7. A cross-currency pair with no rate for that date is a pair nothing can
    say the amount of, and inventing a rate here would put a number on a comparison that was
    never made."""
    scored = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="22000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=None),
    )
    floor = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="90000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=None),
    )
    assert scored == floor


def test_equal_figures_in_two_currencies_are_not_a_match_without_a_rate() -> None:
    """The case a parity fallback would get most wrong: 1,000 of one currency and 1,000 of
    another look identical as numbers and are not the same amount. With no rate held for the
    day, nothing can say whether they are, so amount contributes nothing."""
    unrated = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="1000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=None),
    )
    floor = score_match(
        leg(amount="-1000.0000", currency="XXX"),
        credit(amount="90000.0000", currency="YYY"),
        evidence(kind="remittance", market_rate=None),
    )
    assert unrated == floor


# ---------------------------------------------------------------------------------------
# Date, 25%, over a window that varies by kind.


def test_the_same_day_scores_the_whole_date_weight() -> None:
    same = score_match(leg(day=2), credit(day=2), evidence())
    far = score_match(leg(day=2), credit(day=28), evidence())
    assert component(same, without=far) == pytest.approx(DATE_WEIGHT)


def test_the_score_decays_across_the_window_and_reaches_zero_at_its_edge() -> None:
    inside = score_match(leg(day=2), credit(day=3), evidence(kind="remittance"))
    edge = score_match(leg(day=2), credit(day=9), evidence(kind="remittance"))
    beyond = score_match(leg(day=2), credit(day=20), evidence(kind="remittance"))
    assert inside > edge
    assert edge == beyond, "the edge of the window is already zero, and stays zero past it"


@pytest.mark.parametrize(
    ("kind", "same_institution", "days"),
    [
        # §3's table. Same bank is usually instant; cross-bank has to clear, and a 0–1 day
        # window would refuse every domestic transfer that took a day to settle.
        ("internal_same_currency", True, 1),
        ("internal_same_currency", False, 3),
        ("remittance", True, 7),
        ("remittance", False, 7),
        ("card_payment", True, 5),
        ("card_payment", False, 5),
        ("other", True, 3),
        ("other", False, 3),
    ],
)
def test_each_kind_has_the_window_the_specification_gives_it(
    kind: str, same_institution: bool, days: int
) -> None:
    assert date_window(kind, same_institution=same_institution) == days


def test_a_credit_before_its_debit_is_not_a_match() -> None:
    """Money arrives after it leaves. A credit dated earlier is a different movement."""
    backwards = score_match(leg(day=9), credit(day=2), evidence())
    far = score_match(leg(day=9), credit(day=28), evidence())
    assert component(backwards, without=far) == 0.0


# ---------------------------------------------------------------------------------------
# Description, 20%.


def test_shared_tokens_score() -> None:
    shared = score_match(
        leg(description="TRANSFER TO SAVINGS POT"),
        credit(description="TRANSFER FROM CURRENT POT"),
        evidence(),
    )
    unrelated = score_match(
        leg(description="QQQQ WWWW"), credit(description="ZZZZ YYYY"), evidence()
    )
    assert shared > unrelated


def test_a_direction_keyword_on_each_leg_is_evidence() -> None:
    keyed = score_match(
        leg(description="OUTWARD REMIT"), credit(description="INWARD REMIT"), evidence()
    )
    plain = score_match(leg(description="QQQQ"), credit(description="ZZZZ"), evidence())
    assert keyed > plain


def test_a_keyword_on_one_leg_alone_is_not_evidence_of_a_pair() -> None:
    """A direction keyword says a row is *a* transfer. It takes one on each side to say two
    rows might be the same one — otherwise every outward remittance scores against every
    unrelated credit in the window."""
    one_sided = score_match(
        leg(description="OUTWARD REMIT"), credit(description="ZZZZ"), evidence()
    )
    neither = score_match(leg(description="QQQQ"), credit(description="ZZZZ"), evidence())
    assert one_sided == neither


def test_a_reference_appearing_in_the_other_leg_is_strong_evidence() -> None:
    """The one signal that identifies a specific movement rather than a kind of movement."""
    named = score_match(
        leg(description="TT REF QQ99WW", reference_raw="QQ99WW"),
        credit(description="INWARD TT QQ99WW"),
        evidence(),
    )
    unnamed = score_match(
        leg(description="TT REF QQ99WW", reference_raw="QQ99WW"),
        credit(description="INWARD TT"),
        evidence(),
    )
    assert named > unnamed


def test_a_short_reference_is_not_treated_as_evidence() -> None:
    """A two-character reference appears in half the ledger by chance."""
    noisy = score_match(
        leg(description="PAYMENT", reference_raw="A1"),
        credit(description="A1 CREDITED"),
        evidence(),
    )
    plain = score_match(leg(description="PAYMENT"), credit(description="CREDITED"), evidence())
    assert noisy == plain


# ---------------------------------------------------------------------------------------
# Pair history, 15%, and the rule hint, 5%.


def test_a_pair_confirmed_before_scores_higher_than_one_never_seen() -> None:
    seen = score_match(leg(), credit(), evidence(confirmed_pairs=11))
    unseen = score_match(leg(), credit(), evidence(confirmed_pairs=0))
    assert component(seen, without=unseen) == pytest.approx(PAIR_HISTORY_WEIGHT)


def test_pair_history_saturates_rather_than_growing_without_bound() -> None:
    many = score_match(leg(), credit(), evidence(confirmed_pairs=500))
    eleven = score_match(leg(), credit(), evidence(confirmed_pairs=11))
    assert many == eleven


def test_a_rule_naming_the_peer_account_is_the_direct_signal() -> None:
    hinted = score_match(
        leg(is_transfer_candidate=True, counterparty_account_id=DESTINATION),
        credit(),
        evidence(kind="card_payment"),
    )
    plain = score_match(leg(), credit(), evidence(kind="card_payment"))
    assert component(hinted, without=plain) == pytest.approx(RULE_HINT_WEIGHT)


def test_a_rule_naming_a_different_account_is_not_a_signal_for_this_pair() -> None:
    misdirected = score_match(
        leg(is_transfer_candidate=True, counterparty_account_id=ELSEWHERE),
        credit(),
        evidence(kind="card_payment"),
    )
    plain = score_match(leg(), credit(), evidence(kind="card_payment"))
    assert misdirected == plain


def test_the_transfer_flag_without_a_counterparty_names_no_pair() -> None:
    flagged = score_match(leg(is_transfer_candidate=True), credit(), evidence())
    plain = score_match(leg(), credit(), evidence())
    assert flagged == plain


# ---------------------------------------------------------------------------------------
# The weighted sum, and its ceiling.


def test_the_weights_are_the_ones_the_specification_states() -> None:
    assert (
        AMOUNT_WEIGHT,
        DATE_WEIGHT,
        DESCRIPTION_WEIGHT,
        PAIR_HISTORY_WEIGHT,
        RULE_HINT_WEIGHT,
    ) == (0.35, 0.25, 0.20, 0.15, 0.05)
    assert sum(
        (AMOUNT_WEIGHT, DATE_WEIGHT, DESCRIPTION_WEIGHT, PAIR_HISTORY_WEIGHT, RULE_HINT_WEIGHT)
    ) == pytest.approx(1.0)


def test_everything_agreeing_scores_one() -> None:
    perfect = score_match(
        leg(
            description="OUTWARD TRANSFER QQ99WW",
            reference_raw="QQ99WW",
            is_transfer_candidate=True,
            counterparty_account_id=DESTINATION,
        ),
        credit(description="INWARD TRANSFER QQ99WW"),
        evidence(confirmed_pairs=11),
    )
    assert perfect == pytest.approx(1.0)
    assert perfect <= 1.0


def test_nothing_agreeing_scores_nothing() -> None:
    assert (
        score_match(
            leg(day=2, description="QQQQ"),
            credit(day=28, amount="5000.0000", description="ZZZZ"),
            evidence(),
        )
        == 0.0
    )


def test_the_score_is_a_float_and_never_money() -> None:
    scored = score_match(leg(), credit(), evidence())
    assert isinstance(scored, float)
    assert not isinstance(scored, Decimal)


# ---------------------------------------------------------------------------------------
# The bands are presentation. Nothing here confirms anything (ADR-0007).


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, "high"),
        (0.90, "high"),
        (0.89, "probable"),
        (0.70, "probable"),
        (0.69, "weak"),
        (0.50, "weak"),
        (0.49, "hidden"),
        (0.0, "hidden"),
    ],
)
def test_the_bands_are_the_thresholds_the_specification_tabulates(
    score: float, expected: str
) -> None:
    assert band(score) == expected


def test_no_band_means_confirmed() -> None:
    """Even 0.99 requires a click in Phase 1 (ADR-0007), so the vocabulary has no word for
    'linked' — a band this module could return would be one a caller could act on."""
    assert {band(s / 100) for s in range(0, 101)} == {"high", "probable", "weak", "hidden"}


# ---------------------------------------------------------------------------------------
# The kind a pair proposes, so 3b3 does not invent a second answer.


def test_two_currencies_propose_a_remittance() -> None:
    assert (
        propose_kind(leg(currency="XXX"), credit(currency="YYY"), destination_is_card=False)
        == "remittance"
    )


def test_money_arriving_in_a_card_proposes_a_card_payment() -> None:
    assert propose_kind(leg(), credit(), destination_is_card=True) == "card_payment"


def test_one_currency_between_two_ordinary_accounts_is_an_internal_move() -> None:
    assert propose_kind(leg(), credit(), destination_is_card=False) == "internal_same_currency"


def test_a_card_being_paid_in_another_currency_is_still_a_card_payment() -> None:
    """The posting lag is what the window is for, and it does not stop applying because the
    card is denominated elsewhere."""
    assert (
        propose_kind(leg(currency="XXX"), credit(currency="YYY"), destination_is_card=True)
        == "card_payment"
    )


# ---------------------------------------------------------------------------------------
# What the source's own narration states (`multi-currency-and-fx.html` §5). An outbound
# remittance can name the amount that arrives, its currency and the rate achieved. Where it
# does, the destination is compared in its own currency — no market rate stands between the
# two — and a credit arriving as stated is near-certain. 100000 at .04271 is the 4271.00
# debited: the document's own invented example, as arithmetic.

STATED = StatedRemittance(amount=Decimal("100000"), currency="YYY", rate=Decimal(".04271"))
# 4271 at 23.9 expects 102076.90, 2% from what arrived. §3's tolerance scores that only in part,
# which is the weak band every remittance on the owner's ledger landed in.
REMITTANCE = evidence(kind="remittance", market_rate=Decimal("23.9"))


def remitted(**changes: object) -> Leg:
    changes.setdefault("amount", "-4271.0000")
    changes.setdefault("description", "OUTWARD REMITTANCE")
    changes.setdefault("remittance", STATED)
    return leg(**changes)  # type: ignore[arg-type]


def arriving(amount: str = "100000.0000", **changes: object) -> Leg:
    changes.setdefault("currency", "YYY")
    changes.setdefault("description", "INWARD CREDIT")
    return credit(amount=amount, **changes)


def test_a_credit_arriving_as_the_narration_states_is_near_certain() -> None:
    assert band(score_match(remitted(), arriving(), REMITTANCE)) == "high"
    assert band(score_match(remitted(remittance=None), arriving(), REMITTANCE)) == "weak"


def test_the_stated_amount_needs_no_market_rate() -> None:
    """Compared in the destination's own currency, nothing is converted — so a day with no rate
    held still has an amount to compare, where §3's path rightly scores it nothing (I7)."""
    unrated = evidence(kind="remittance", market_rate=None)
    assert band(score_match(remitted(), arriving(), unrated)) == "high"
    assert band(score_match(remitted(remittance=None), arriving(), unrated)) == "hidden"


def test_a_receiving_charge_keeps_it_near_certain() -> None:
    """Short by a flat charge at the receiving end — here 0.5% — is the same movement."""
    assert band(score_match(remitted(), arriving("99500.0000"), REMITTANCE)) == "high"


def test_short_by_more_than_a_charge_is_left_to_the_weighted_sum() -> None:
    """3% short is a different amount, not a fee, so it is not near-certain. Its amount is still
    compared with the stated one, in one currency: 1 - 0.03*20 of the weight."""
    scored = score_match(remitted(), arriving("97000.0000"), REMITTANCE)
    floor = score_match(remitted(), arriving("50000.0000"), REMITTANCE)
    assert band(scored) != "high"
    assert component(scored, without=floor) == pytest.approx(AMOUNT_WEIGHT * 0.4, abs=1e-9)


def test_more_than_the_narration_states_is_a_different_movement() -> None:
    """Money does not grow in transit."""
    assert band(score_match(remitted(), arriving("100500.0000"), REMITTANCE)) != "high"


def test_near_certain_needs_the_remittance_window() -> None:
    """Arriving before it left, or after the window, it is not this movement's arrival."""
    assert band(score_match(remitted(day=9), arriving(day=2), REMITTANCE)) != "high"
    assert band(score_match(remitted(day=2), arriving(day=20), REMITTANCE)) != "high"


def test_exactly_a_one_percent_charge_is_near_certain_and_past_it_is_not() -> None:
    """The bound the transactions screen's pre-tick rests on, pinned at its edge."""
    assert band(score_match(remitted(), arriving("99000.0000"), REMITTANCE)) == "high"
    assert band(score_match(remitted(), arriving("98999.0000"), REMITTANCE)) != "high"


def test_a_tenth_of_a_percent_over_is_already_a_different_movement() -> None:
    """Under the exact tolerance over is rounding; at it, the credit is larger than stated."""
    assert band(score_match(remitted(), arriving("100099.0000"), REMITTANCE)) == "high"
    assert band(score_match(remitted(), arriving("100100.0000"), REMITTANCE)) != "high"


def test_the_narration_is_read_while_its_figures_reproduce_the_debit_within_a_charge() -> None:
    """100000 at .0431371 is 4313.71, exactly 1% over the 4271 debited: read. At .043141371
    it is 4314.1371, just past 1%: the capture describes some other movement."""
    within = StatedRemittance(amount=Decimal("100000"), currency="YYY", rate=Decimal(".0431371"))
    beyond = StatedRemittance(amount=Decimal("100000"), currency="YYY", rate=Decimal(".043141371"))
    assert band(score_match(remitted(remittance=within), arriving(), REMITTANCE)) == "high"
    assert score_match(remitted(remittance=beyond), arriving(), REMITTANCE) == score_match(
        remitted(remittance=None), arriving(), REMITTANCE
    )


def test_a_narration_naming_another_currency_says_nothing_about_this_credit() -> None:
    elsewhere = arriving(currency="ZZZ")
    assert score_match(remitted(), elsewhere, REMITTANCE) == score_match(
        remitted(remittance=None), elsewhere, REMITTANCE
    )


def test_figures_that_do_not_reproduce_the_debit_are_not_read() -> None:
    """100000 at .5 is 50000, not the 4271 debited: the capture read something else, and a
    near-certain match built on it would be pre-ticked on the transactions screen."""
    misread = StatedRemittance(amount=Decimal("100000"), currency="YYY", rate=Decimal(".5"))
    assert score_match(remitted(remittance=misread), arriving(), REMITTANCE) == score_match(
        remitted(remittance=None), arriving(), REMITTANCE
    )


def test_the_narration_is_read_from_its_three_captures() -> None:
    assert stated_remittance("100,000", "yyy", ".04271") == StatedRemittance(
        amount=Decimal("100000"), currency="YYY", rate=Decimal(".04271")
    )


@pytest.mark.parametrize(
    ("amount", "currency", "rate"),
    [
        (None, "YYY", ".04271"),
        ("100000", None, ".04271"),
        ("100000", "YYY", None),
        ("ABC", "YYY", ".04271"),
        ("100000", "YYY", "x"),
        ("100000", "YYY", "NaN"),
        # Decimal() reads both, and neither is a figure a statement prints: the first overflows
        # the reproduction check into a 500 on every candidate request.
        ("100000", "YYY", "1E+999999"),
        ("100000", "YYY", "1_0"),
        ("0", "YYY", ".04271"),
        ("-100000", "YYY", ".04271"),
        ("100000", "YYY", "0"),
        ("100000", "YY", ".04271"),
    ],
)
def test_a_capture_that_is_missing_or_not_a_figure_states_nothing(
    amount: str | None, currency: str | None, rate: str | None
) -> None:
    assert stated_remittance(amount, currency, rate) is None
