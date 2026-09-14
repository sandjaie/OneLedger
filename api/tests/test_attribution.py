"""Phase 2.2 Task 2: `attribution.attribute` (spec "Attribution for one month").

Pure `Decimal` fixtures, no database. `test_dated_cash_flow_fixture_reconciles_with_zero_residual`
is the spec's own required proof: a two-currency month with income, spending, a cross-currency
transfer with spread, a funded cross-currency holding purchase, a full sale, market movement, a
snapshot revaluation, a liability, a base-currency account, a mid-window account opening and a
rate move between the funding event and the closing rate — every position's own native and
translated values are picked by hand so `closing_native = opening_native + booked native flows`
holds exactly, which is what makes the residual exactly zero rather than merely small: the
components-plus-residual identity is a telescoping sum of the module's own formulas, so any wrong
sign or an omitted bridge term shows up as a nonzero residual here, not just a wrong line total.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from oneledger.attribution import Attribution, Flow, HoldingFunding, Position, attribute
from oneledger.routes.wealth_history import _mode_mismatch_reasons, _StoredPosition

BASE = "XAB"
SEC = "XCD"
ZERO = Decimal("0.0000")


def _attribute_base() -> Attribution:
    # Rates: SEC->BASE was 0.5 at the opening month end, 0.55 at the closing month end, and the
    # window's own SEC-currency flows/funding froze at 0.52 — three different rates, so nothing
    # here coincidentally cancels.
    closing_rates = {BASE: Decimal("1"), SEC: Decimal("0.5500000000")}

    positions = [
        # P1: base-currency account carrying income and spending, plus an excluded flow.
        Position(
            "p1",
            "ledger_balance",
            BASE,
            Decimal("1000.0000"),
            Decimal("1000.0000"),
            Decimal("1330.0000"),
            Decimal("1330.0000"),
        ),
        # P2: secondary-currency account, the cross-currency transfer-out leg with spread.
        Position(
            "p2",
            "ledger_balance",
            SEC,
            Decimal("200.0000"),
            Decimal("100.0000"),
            Decimal("100.0000"),
            Decimal("55.0000"),
        ),
        # P3: base-currency account opened mid-window (absent at t0), the transfer-in leg.
        Position("p3", "ledger_balance", BASE, ZERO, ZERO, Decimal("50.0000"), Decimal("50.0000")),
        # P5: base-currency account funding the cross-currency holding purchase (H1).
        Position(
            "p5",
            "ledger_balance",
            BASE,
            Decimal("500.0000"),
            Decimal("500.0000"),
            Decimal("479.2000"),
            Decimal("479.2000"),
        ),
        # P6: base-currency account receiving the full sale's proceeds (H2) and a dividend.
        Position(
            "p6",
            "ledger_balance",
            BASE,
            Decimal("300.0000"),
            Decimal("300.0000"),
            Decimal("415.0000"),
            Decimal("415.0000"),
        ),
        # L1: a liability (signed negative, ADR-0010), paid down by one loan_repayment.
        Position(
            "l1",
            "ledger_balance",
            BASE,
            Decimal("-500.0000"),
            Decimal("-500.0000"),
            Decimal("-450.0000"),
            Decimal("-450.0000"),
        ),
        # S1: a manual valuation snapshot, revalued upward.
        Position(
            "s1",
            "snapshot",
            BASE,
            Decimal("2000.0000"),
            Decimal("2000.0000"),
            Decimal("2100.0000"),
            Decimal("2100.0000"),
        ),
        # H1: a new holding (absent at t0), bought cross-currency, with market movement.
        Position("h1", "holdings", SEC, ZERO, ZERO, Decimal("45.0000"), Decimal("24.7500")),
        # H2: an existing base-currency holding, fully sold within the window.
        Position("h2", "holdings", BASE, Decimal("100.0000"), Decimal("100.0000"), ZERO, ZERO),
    ]

    flows = [
        Flow("p1", BASE, Decimal("500.0000"), Decimal("500.0000"), True, False, "income"),
        Flow("p1", BASE, Decimal("-200.0000"), Decimal("-200.0000"), True, False, "expense"),
        Flow("p1", BASE, Decimal("30.0000"), Decimal("30.0000"), False, True, "income"),
        Flow("p2", SEC, Decimal("-100.0000"), Decimal("-52.0000"), False, False, "transfer_out"),
        Flow("p3", BASE, Decimal("50.0000"), Decimal("50.0000"), False, False, "transfer_in"),
        Flow("p5", BASE, Decimal("-20.8000"), Decimal("-20.8000"), False, False, "investment_buy"),
        Flow("p6", BASE, Decimal("110.0000"), Decimal("110.0000"), False, False, "investment_sell"),
        Flow("p6", BASE, Decimal("5.0000"), Decimal("5.0000"), False, False, "investment_income"),
        Flow("l1", BASE, Decimal("50.0000"), Decimal("50.0000"), False, False, "loan_repayment"),
    ]

    funding = [
        # H1's purchase: 40 SEC out, frozen at 0.52 (0.5's neighbour, not 0.55's).
        HoldingFunding("h1", SEC, Decimal("-40.0000"), Decimal("-20.8000")),
        # H2's sale: 110 BASE in, closing the holding out at a gain over its 100 book value.
        HoldingFunding("h2", BASE, Decimal("110.0000"), Decimal("110.0000")),
    ]

    return attribute(
        positions=positions, flows=flows, holding_funding=funding, closing_rates=closing_rates
    )


def test_dated_cash_flow_fixture_reconciles_with_zero_residual() -> None:
    result = _attribute_base()
    assert result.available
    # `available` alone doesn't narrow the Decimal | None fields for mypy; the Attribution
    # contract guarantees they're all set together, so assert that explicitly once.
    assert (
        result.retained_income is not None
        and result.investment_income is not None
        and result.internal_movements_and_costs is not None
        and result.other_external_changes is not None
        and result.market_movement is not None
        and result.asset_revaluation is not None
        and result.fx_translation is not None
        and result.residual is not None
    )

    assert result.opening == Decimal("3500.0000")
    assert result.closing == Decimal("4003.9500")
    assert result.actual_change == Decimal("503.9500")

    assert result.retained_income == Decimal("300.0000")  # income 500 - expense 200
    assert result.investment_income == Decimal("5.0000")  # the dividend
    assert result.internal_movements_and_costs == Decimal("48.0000")
    assert result.other_external_changes == Decimal("30.0000")  # the excluded flow
    assert result.market_movement == Decimal("12.7500")  # H1's 2.75 + H2's 10.00 realised gain
    assert result.asset_revaluation == Decimal("100.0000")  # S1's snapshot step-up
    assert result.fx_translation == Decimal("8.2000")

    total_lines = (
        result.retained_income
        + result.investment_income
        + result.internal_movements_and_costs
        + result.other_external_changes
        + result.market_movement
        + result.asset_revaluation
        + result.fx_translation
    )
    assert result.actual_change == total_lines + result.residual
    assert result.residual == ZERO


def test_missing_closing_rate_for_a_held_currency_is_unavailable() -> None:
    positions = [
        Position(
            "p1",
            "ledger_balance",
            SEC,
            Decimal("100.0000"),
            Decimal("50.0000"),
            Decimal("100.0000"),
            Decimal("50.0000"),
        ),
    ]
    result = attribute(positions=positions, flows=[], holding_funding=[], closing_rates={})
    assert result.available is False
    assert result.reasons == (f"no closing rate for {SEC}",)
    assert result.opening is None
    assert result.residual is None


def test_missing_closing_rate_for_a_flowing_currency_is_unavailable() -> None:
    flows = [Flow("p1", SEC, Decimal("10.0000"), Decimal("5.0000"), True, False, "income")]
    result = attribute(
        positions=[], flows=flows, holding_funding=[], closing_rates={BASE: Decimal("1")}
    )
    assert result.available is False
    assert result.reasons == (f"no closing rate for {SEC}",)


def test_incomplete_month_is_unavailable_before_any_arithmetic() -> None:
    result = attribute(
        positions=[],
        flows=[],
        holding_funding=[],
        closing_rates={},
        unavailable_reasons=["before_opening account Synthetic savings account"],
    )
    assert result.available is False
    assert result.reasons == ("before_opening account Synthetic savings account",)
    assert result.opening is None


def test_flow_classification_first_match_order() -> None:
    """Each of the spec's five lines, one flow apiece, proves the first-match order rather than
    just its final outcome: a reportable row wins over everything else, is_excluded wins over
    txn_type, investment_income is its own line, the internal set, and the opening/adjustment
    fallback."""
    positions = [
        Position(
            "acct",
            "ledger_balance",
            BASE,
            Decimal("1000.0000"),
            Decimal("1000.0000"),
            Decimal("1000.0000"),
            Decimal("1000.0000"),
        ),
    ]
    flows = [
        Flow("acct", BASE, Decimal("10.0000"), Decimal("10.0000"), True, False, "income"),
        Flow("acct", BASE, Decimal("20.0000"), Decimal("20.0000"), False, True, "expense"),
        Flow(
            "acct", BASE, Decimal("30.0000"), Decimal("30.0000"), False, False, "investment_income"
        ),
        Flow("acct", BASE, Decimal("-15.0000"), Decimal("-15.0000"), False, False, "transfer_out"),
        Flow("acct", BASE, Decimal("-45.0000"), Decimal("-45.0000"), False, False, "adjustment"),
    ]
    result = attribute(
        positions=positions, flows=flows, holding_funding=[], closing_rates={BASE: Decimal("1")}
    )
    assert result.available
    assert result.retained_income == Decimal("10.0000")
    assert result.investment_income == Decimal("30.0000")
    assert result.internal_movements_and_costs == Decimal("-15.0000")
    assert result.other_external_changes == Decimal("20.0000") + Decimal("-45.0000")


def test_a_valuation_mode_change_between_t0_and_t1_is_a_mode_mismatch_reason() -> None:
    """OL-005 (review repair round): the route's own pure `_mode_mismatch_reasons` helper, unit
    tested directly against hand-built `_StoredPosition` rows — no database needed. An account
    that was `ledger_balance` at t0 and `holdings` at t1 has no single honest formula, so it must
    be named, not silently attributed under whichever end's kind happened to be picked."""
    account_id = uuid4()
    t0, t1 = date(2023, 12, 31), date(2024, 1, 31)

    def row(mode: str) -> _StoredPosition:
        return _StoredPosition(
            account_id=account_id,
            account_name="Synthetic switched account",
            holding_id=None,
            valuation_mode=mode,
            currency=BASE,
            native_value=Decimal("100.0000"),
            rate_to_base=Decimal("1"),
            rate_to_secondary=Decimal("1"),
            value_base=Decimal("100.0000"),
            value_secondary=Decimal("100.0000"),
            status="available",
        )

    reasons = _mode_mismatch_reasons([row("ledger_balance")], [row("holdings")], t0, t1)
    assert reasons == [f"Synthetic switched account changed valuation mode between {t0} and {t1}"]

    # Same kind at both ends, or absent at one end: no mismatch to report.
    assert _mode_mismatch_reasons([row("ledger_balance")], [row("ledger_balance")], t0, t1) == []
    assert _mode_mismatch_reasons([], [row("holdings")], t0, t1) == []


if __name__ == "__main__":
    # ponytail: one runnable self-check for this money-path module beyond pytest.
    r = _attribute_base()
    assert r.available and r.residual == ZERO, r
    print("attribution self-check ok")
