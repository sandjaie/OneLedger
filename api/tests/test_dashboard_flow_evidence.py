"""The dashboard's two pure presentation rules.

The flow chart: only complete statement coverage can turn a zero into a fact. The position
composition: exact buckets, absolute shares, bounded coordinates and a missing reporting value
that nulls its own currency whole rather than shrinking the picture.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from oneledger.routes.coverage import CoverageCounts, CoverageRow, CoverageStatus, MonthCell
from oneledger.routes.dashboard import MonthFlows, flow_evidence, position_composition


def _month(on: date, income: str = "0.0000", net: str = "0.0000") -> MonthFlows:
    return MonthFlows(
        month=on,
        incomeBase=income,
        incomeSecondary=income,
        spendingBase="0.0000",
        spendingSecondary="0.0000",
        netBase=net,
        netSecondary=net,
        savingsRatePctBase=None,
        savingsRatePctSecondary=None,
    )


def _account(*statuses: CoverageStatus) -> CoverageRow:
    return CoverageRow(
        subject="account",
        accountId=UUID("00000000-0000-4000-8000-000000000001"),
        accountName="Synthetic account",
        lifeStart=date(2026, 1, 1),
        openingBalanceDate=date(2026, 1, 1),
        anchorAfterHistory=True,
        months=[MonthCell(status=status) for status in statuses],
        counts=CoverageCounts(),
    )


def test_flow_chart_has_a_gap_for_missing_coverage_even_when_a_flow_is_recorded() -> None:
    months = [_month(date(2026, 1, 1), "10.0000", "10.0000"), _month(date(2026, 2, 1))]
    points = flow_evidence(
        months,
        [date(2026, 1, 1), date(2026, 2, 1)],
        [_account("reconciled", "missing")],
        date(2026, 3, 31),
    )

    assert [point.status for point in points] == ["complete", "unknown"]
    assert points[0].incomeBaseCoordinate == 10000
    assert points[1].incomeBaseCoordinate is None


def test_partial_current_month_and_no_account_evidence_remain_unknown() -> None:
    march = _month(date(2026, 3, 1), "20.0000", "20.0000")
    assert (
        flow_evidence([march], [march.month], [_account("reconciled")], date(2026, 3, 15))[0].status
        == "unknown"
    )
    assert flow_evidence([march], [march.month], [], date(2026, 3, 31))[0].status == "unknown"


def test_complete_zero_and_negative_net_have_bounded_signed_coordinates() -> None:
    months = [_month(date(2026, 1, 1)), _month(date(2026, 2, 1), "10.0000", "-5.0000")]
    points = flow_evidence(
        months,
        [date(2026, 1, 1), date(2026, 2, 1)],
        [_account("reconciled", "reconciled")],
        date(2026, 3, 31),
    )
    assert [point.status for point in points] == ["complete", "complete"]
    assert [point.incomeBaseCoordinate for point in points] == [0, 10000]
    assert [point.netBaseCoordinate for point in points] == [0, -5000]


# ---------------------------------------------------------------------------------------
# Position composition — the same resolved values, grouped.

XIN = "XIN"
XAE = "XAE"


def _id(number: int) -> UUID:
    return UUID(int=number)


def _fact(
    number: int,
    account_class: str,
    account_type: str,
    base: str | None,
    secondary: str | None,
) -> tuple[UUID, str, str, Decimal | None, Decimal | None]:
    return (
        _id(number),
        account_class,
        account_type,
        None if base is None else Decimal(base),
        None if secondary is None else Decimal(secondary),
    )


def test_buckets_keep_their_declared_order_and_group_every_account_type() -> None:
    """One account per bucket, deliberately supplied out of order: the response order is the
    server's, never the order accounts happened to be resolved in."""
    composition = position_composition(
        [
            _fact(1, "liability", "home_loan", "-100.0000", "-25.0000"),
            _fact(2, "asset", "receivable", "100.0000", "25.0000"),
            _fact(3, "asset", "retirement", "100.0000", "25.0000"),
            _fact(4, "asset", "property", "100.0000", "25.0000"),
            _fact(5, "asset", "mutual_fund_folio", "100.0000", "25.0000"),
            _fact(6, "asset", "recurring_deposit", "100.0000", "25.0000"),
        ],
        XIN,
        XAE,
    )

    assert [row.key for row in composition.rows] == [
        "cash",
        "investments",
        "property",
        "retirement",
        "other",
        "liabilities",
    ]
    # Every share is 1/6 of the absolute total, so the bucket membership above is exact.
    assert {row.sharePercentBase for row in composition.rows} == {"16.6667"}


def test_an_empty_bucket_is_absent_rather_than_a_zero_row() -> None:
    composition = position_composition([_fact(1, "asset", "cash", "10.0000", "2.5000")], XIN, XAE)

    assert [row.key for row in composition.rows] == ["cash"]


def test_a_liability_keeps_its_sign_and_takes_a_non_negative_share() -> None:
    """ADR-0010 keeps the ledger's sign on the wire; the share is absolute so a liability is a
    visible part of the position instead of cancelling an asset out of the picture."""
    composition = position_composition(
        [
            _fact(1, "asset", "savings", "75.0000", "18.7500"),
            _fact(2, "liability", "credit_card", "-25.0000", "-6.2500"),
        ],
        XIN,
        XAE,
    )
    cash, liabilities = composition.rows

    assert (cash.valueBase, cash.sharePercentBase, cash.chartCoordinateBase) == (
        "75.0000",
        "75.0000",
        7500,
    )
    assert liabilities.valueBase == "-25.0000"
    assert liabilities.sharePercentBase == "25.0000"
    assert liabilities.chartCoordinateBase == 2500
    assert liabilities.valueSecondary == "-6.2500"
    assert liabilities.chartCoordinateSecondary == 2500


def test_shares_round_half_even_at_four_places_within_bounded_coordinates() -> None:
    """Half-up would nudge a value exactly between two representations the same direction on
    every chart. 1/8 of the total lands exactly on a half at the fourth place: 12.5000%."""
    composition = position_composition(
        [
            _fact(1, "asset", "cash", "1.0000", "1.0000"),
            _fact(2, "asset", "property", "7.0000", "7.0000"),
        ],
        XIN,
        XAE,
    )

    assert [row.sharePercentBase for row in composition.rows] == ["12.5000", "87.5000"]
    assert [row.chartCoordinateBase for row in composition.rows] == [1250, 8750]
    assert all(
        row.chartCoordinateBase is not None and 0 <= row.chartCoordinateBase <= 10000
        for row in composition.rows
    )


def test_a_zero_denominator_states_zero_shares_instead_of_dividing() -> None:
    composition = position_composition(
        [
            _fact(1, "asset", "cash", "0.0000", "0.0000"),
            _fact(2, "liability", "payable", "0.0000", "0.0000"),
        ],
        XIN,
        XAE,
    )

    assert [row.sharePercentBase for row in composition.rows] == ["0.0000", "0.0000"]
    assert [row.chartCoordinateSecondary for row in composition.rows] == [0, 0]
    assert composition.missing == []


def test_holdings_of_one_account_add_into_its_bucket_once_each() -> None:
    """A holdings-mode account contributes one fact per holding and no ledger balance, so the
    bucket is the sum of its holdings rather than a balance counted beside them."""
    composition = position_composition(
        [
            _fact(7, "asset", "brokerage", "12.0000", "3.0000"),
            _fact(7, "asset", "brokerage", "12.0000", "3.0000"),
        ],
        XIN,
        XAE,
    )

    assert [(row.key, row.valueBase, row.sharePercentBase) for row in composition.rows] == [
        ("investments", "24.0000", "100.0000")
    ]


def test_a_missing_value_nulls_its_own_reporting_currency_whole_and_names_the_account() -> None:
    """The other reporting currency stands on its own (I7's rule, applied to the composition):
    a smaller, confident, wrong share is worse than an absent one."""
    composition = position_composition(
        [
            _fact(1, "asset", "savings", "75.0000", "18.7500"),
            _fact(2, "asset", "brokerage", None, "6.2500"),
        ],
        XIN,
        XAE,
    )

    assert [row.valueBase for row in composition.rows] == [None, None]
    assert [row.sharePercentBase for row in composition.rows] == [None, None]
    assert [row.chartCoordinateBase for row in composition.rows] == [None, None]
    assert [row.valueSecondary for row in composition.rows] == ["18.7500", "6.2500"]
    assert [row.sharePercentSecondary for row in composition.rows] == ["75.0000", "25.0000"]
    assert [(item.accountId, item.currency) for item in composition.missing] == [(_id(2), XIN)]
