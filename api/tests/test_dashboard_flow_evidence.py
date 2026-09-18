"""Overview flow chart: only complete statement coverage can turn a zero into a fact."""

from datetime import date
from uuid import UUID

from oneledger.routes.coverage import CoverageCounts, CoverageRow, CoverageStatus, MonthCell
from oneledger.routes.dashboard import MonthFlows, flow_evidence


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
