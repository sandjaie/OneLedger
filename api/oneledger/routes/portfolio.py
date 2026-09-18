"""Owner-only acquisition position, portfolio summary and holding-detail read models.

Phase 4.3 Task 1 adds `GET /portfolio/analysis` and the allocation-target and benchmark
correction routes (spec `docs/superpowers/specs/2026-09-16-phase-4-3-investment-analysis.md`,
plan `docs/superpowers/plans/2026-09-16-phase-4-3-investment-analysis.md` "Phase 4.3 Task 1").
`GET /portfolio/analysis` loads holdings, active events, stored month-end positions, stored
targets and benchmarks, then hands them to the pure `investment_analysis` module — no figure is
computed here. `GET` opens `SET TRANSACTION READ ONLY`; the target and benchmark writes take the
household lock first, matching `routes/salary.py`'s convention.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..investment_analysis import (
    ASSET_CLASS_ORDER,
    ContributionLines,
    DietzResult,
    GroupComputation,
    GroupEvent,
    MonthFlowEvent,
    MonthPositionRow,
    MonthResult,
    compute_group,
    compute_month,
    compute_year,
    excess_percent,
    inception_window,
    infer_target,
    modified_dietz,
)
from ..investments.portfolio import (
    HoldingDetail,
    HoldingLotRow,
    PortfolioSummary,
    read_holding_detail,
    read_holding_lots,
    read_summary,
)
from ..investments.positions import (
    AcquisitionPosition,
    PortfolioPositions,
    current_rate_pair,
    read_positions,
)
from ..investments.returns import holding_flows, xirr
from ..money import _out, chart_ratios
from ..resources import household_currencies
from ..wealth_history import stored_month_ends

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _with_xirr(
    cur: psycopg.Cursor[Any], position: AcquisitionPosition, as_of: date
) -> AcquisitionPosition:
    """A holding's own realised-plus-open XIRR, computed from the same engine `/holdings` uses."""
    flows = holding_flows(cur, position.holdingId, as_of)
    if flows.flows is None:
        return position.model_copy(update={"xirrPercent": None, "xirrReason": flows.reason})
    result = xirr(flows.flows)
    percent = None if result.rate_percent is None else str(result.rate_percent)
    return position.model_copy(update={"xirrPercent": percent, "xirrReason": result.reason})


@router.get("/positions", response_model=PortfolioPositions)
def portfolio_positions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
    includeClosed: Annotated[bool, Query()] = False,
) -> PortfolioPositions:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        base, secondary = household_currencies(cur, owner.household_id)
        positions = read_positions(
            cur, owner.household_id, base, secondary, as_of, include_closed=includeClosed
        )
        enriched = [_with_xirr(cur, position, as_of) for position in positions]
        return PortfolioPositions(
            asOf=as_of, baseCurrency=base, secondaryCurrency=secondary, positions=enriched
        )


@router.get("/summary", response_model=PortfolioSummary)
def portfolio_summary(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> PortfolioSummary:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        base, secondary = household_currencies(cur, owner.household_id)
        return read_summary(cur, owner.household_id, base, secondary, as_of)


@router.get("/holdings/{holdingId}", response_model=HoldingDetail)
def holding_detail(
    holdingId: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
    eventsOffset: Annotated[int, Query(ge=0)] = 0,
    eventsLimit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> HoldingDetail:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        detail = read_holding_detail(
            cur, owner.household_id, holdingId, events_offset=eventsOffset, events_limit=eventsLimit
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="holding not found")
        return detail


@router.get("/holdings/{holdingId}/lots", response_model=list[HoldingLotRow])
def holding_lots(
    holdingId: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> list[HoldingLotRow]:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        lots = read_holding_lots(cur, owner.household_id, holdingId)
        if lots is None:
            raise HTTPException(status_code=404, detail="holding not found")
        return lots


# ---------------------------------------------------------------------------------------------
# Phase 4.3 Task 1: GET /portfolio/analysis and the allocation-target/benchmark corrections.
# ---------------------------------------------------------------------------------------------

# The six paired switch/systematic-transfer/platform-transfer kinds — the same vocabulary
# investments/returns.py's own `_BLOCKING_KINDS` names (schema's fixed enum, not a second
# solver).
_BLOCKING_KINDS = (
    "switch_in",
    "switch_out",
    "systematic_transfer_in",
    "systematic_transfer_out",
    "platform_transfer_in",
    "platform_transfer_out",
)


@dataclass(frozen=True)
class _Holding:
    holding_id: UUID
    account_id: UUID
    account_name: str
    asset_class: str
    security_id: UUID
    currency: str


# Same active-as-of-holdings population `investments/returns.py::_HOUSEHOLD_HOLDINGS_SQL` and
# `investments/positions.py::_POSITIONS` use (active accounts in holdings mode as of `as_of`),
# with the account name and security metadata class and account XIRR need.
_HOLDINGS_SQL = """
    SELECT h.id, h.account_id, a.name, security.asset_class::text, h.security_id, security.currency
      FROM holding h
      JOIN account a ON a.household_id = h.household_id AND a.id = h.account_id
      JOIN security ON security.id = h.security_id
      JOIN account_valuation_period period
        ON period.household_id = a.household_id AND period.account_id = a.id
       AND period.valuation_mode = 'holdings'
       AND %(as_of)s >= period.effective_from
       AND (period.effective_to IS NULL OR %(as_of)s <= period.effective_to)
     WHERE h.household_id = %(household)s AND a.is_active
"""


def _load_household_holdings(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date
) -> list[_Holding]:
    cur.execute(_HOLDINGS_SQL, {"household": household_id, "as_of": as_of})
    return [
        _Holding(row[0], row[1], row[2], row[3], row[4], str(row[5]).strip())
        for row in cur.fetchall()
    ]


# Same active-event definition as `investments/returns.py::_PORTFOLIO_EVENTS_SQL`, plus the
# event's own id — the group-scoped switch/transfer counterpart check below needs it.
_EVENTS_SQL = """
    SELECT event.id, event.event_kind::text, event.event_date, COALESCE(event.net_amount_base, 0)
      FROM investment_transaction event
     WHERE event.holding_id = %s AND event.event_date <= %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
     ORDER BY event.event_date, event.created_at
"""


def _load_counterpart_holdings(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date
) -> dict[UUID, UUID | None]:
    """Every active blocking-kind event's own counterpart holding id, household-wide.

    `investment_transaction.counterpart_transaction_id` is only ever set on the paired `_in` leg
    (V40), naming its own `_out` counterpart; an `_out` leg's own counterpart is resolved here by
    the reverse lookup — whichever `_in` leg (if any) points back at it.
    """
    cur.execute(
        """SELECT t.id, t.holding_id, t.event_kind::text, t.counterpart_transaction_id
             FROM investment_transaction t
            WHERE t.household_id = %(household)s AND t.event_date <= %(as_of)s
              AND t.event_kind::text = ANY(%(kinds)s)
              AND NOT EXISTS (
                  SELECT FROM investment_transaction correction
                   WHERE correction.corrects_transaction_id = t.id)""",
        {"household": household_id, "as_of": as_of, "kinds": list(_BLOCKING_KINDS)},
    )
    rows = cur.fetchall()
    holding_by_id: dict[UUID, UUID] = {row[0]: row[1] for row in rows}
    in_holding_by_out_id: dict[UUID, UUID] = {
        row[3]: row[1] for row in rows if row[2].endswith("_in") and row[3] is not None
    }
    result: dict[UUID, UUID | None] = {}
    for txn_id, _holding_id, kind, counterpart_id in rows:
        if kind.endswith("_in"):
            result[txn_id] = None if counterpart_id is None else holding_by_id.get(counterpart_id)
        else:
            result[txn_id] = in_holding_by_out_id.get(txn_id)
    return result


_LATEST_PRICE_SQL = """
    SELECT price, currency FROM price_snapshot
     WHERE security_id = %s AND is_accepted AND price_date <= %s
     ORDER BY price_date DESC, observed_at DESC, id DESC
     LIMIT 1
"""


def _latest_price(
    cur: psycopg.Cursor[Any], security_id: UUID, as_of: date
) -> tuple[Decimal, str] | None:
    cur.execute(_LATEST_PRICE_SQL, (security_id, as_of))
    row = cur.fetchone()
    return None if row is None else (Decimal(row[0]), str(row[1]).strip())


# Same active-event definition as `investments/returns.py::_UNITS_SQL`.
_UNITS_SQL = """
    SELECT COALESCE(sum(effect.signed_units), 0)
      FROM holding h
      LEFT JOIN investment_transaction event
        ON event.holding_id = h.id AND event.event_date <= %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
      LEFT JOIN investment_unit_effect effect
        ON effect.investment_transaction_id = event.id
     WHERE h.id = %s
"""


def _holding_terminal(
    cur: psycopg.Cursor[Any],
    holding: _Holding,
    as_of: date,
    base: str,
    secondary: str,
    rate_cache: dict[str, Decimal | None],
) -> tuple[bool, bool, Decimal]:
    """`(any_open, value_ok, value_base)` for one holding at as-of — the same units, latest
    accepted price and as-of base rate `investments/returns.py::portfolio_flows` uses for its
    own terminal value."""
    cur.execute(_UNITS_SQL, (as_of, holding.holding_id))
    row = cur.fetchone()
    assert row is not None
    units = Decimal(row[0])
    if units <= 0:
        return False, True, Decimal(0)
    priced = _latest_price(cur, holding.security_id, as_of)
    if priced is None:
        return True, False, Decimal(0)
    price, _price_currency = priced
    if holding.currency not in rate_cache:
        rates = current_rate_pair(cur, holding.currency, base, secondary, as_of)
        rate_cache[holding.currency] = None if rates.to_base is None else rates.to_base.rate
    rate = rate_cache[holding.currency]
    if rate is None:
        return True, False, Decimal(0)
    # `investments/positions.py::_money`'s own scale and rounding — every other current-value
    # figure in this file is that same NUMERIC(20,4), half-up.
    value = (units * price * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return True, True, value


@dataclass(frozen=True)
class _GroupResult:
    computation: GroupComputation
    any_open: bool
    value_ok: bool
    terminal_value: Decimal


def _group_result(
    cur: psycopg.Cursor[Any],
    group_holdings: Sequence[_Holding],
    group_label: str | None,
    label_by_holding: dict[UUID, str],
    as_of: date,
    base: str,
    secondary: str,
    counterpart_holding: dict[UUID, UUID | None],
    rate_cache: dict[str, Decimal | None],
) -> _GroupResult:
    """One asset-class, account or (`group_label=None`) portfolio group's own
    `investment_analysis.compute_group` inputs. A blocking leg is only ever checked against
    `group_label` when one is given — the portfolio group is never `incomplete_history`, matching
    `investments/returns.py::portfolio_flows`'s existing behaviour."""
    events: list[GroupEvent] = []
    any_open = False
    value_ok = True
    terminal_total = Decimal(0)
    for holding in group_holdings:
        cur.execute(_EVENTS_SQL, (holding.holding_id, as_of))
        for txn_id, kind, event_date, net_amount_base in cur.fetchall():
            in_group = True
            if group_label is not None and kind in _BLOCKING_KINDS:
                counterpart = counterpart_holding.get(txn_id)
                in_group = (
                    counterpart is not None and label_by_holding.get(counterpart) == group_label
                )
            events.append(GroupEvent(kind, event_date, Decimal(net_amount_base), in_group))
        holding_any_open, holding_value_ok, holding_value = _holding_terminal(
            cur, holding, as_of, base, secondary, rate_cache
        )
        if holding_any_open:
            any_open = True
            value_ok = value_ok and holding_value_ok
            terminal_total += holding_value
    computation = compute_group(
        events, as_of, any_open=any_open, value_complete=value_ok, terminal_value=terminal_total
    )
    return _GroupResult(computation, any_open, value_ok, terminal_total)


# `investments/positions.py::_money`'s own scale — every money figure below is quantized here
# before `_out`, so a genuinely zero amount still wires as "0.0000" rather than the unquantized
# `Decimal(0)`'s own bare "0" (`test_portfolio_summary_live.py`'s own convention for a zero
# `valueBase`).
_MONEY_QUANT = Decimal("0.0001")


def _money_out(value: Decimal | None) -> str | None:
    return None if value is None else _out(value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP))


def _group_value_out(group: _GroupResult) -> str | None:
    if not group.any_open:
        return _money_out(Decimal(0))
    return _money_out(group.terminal_value) if group.value_ok else None


def _security_value_at(
    cur: psycopg.Cursor[Any], security_id: UUID, currency: str, at: date, base: str, secondary: str
) -> Decimal | None:
    priced = _latest_price(cur, security_id, at)
    if priced is None:
        return None
    price, _price_currency = priced
    rates = current_rate_pair(cur, currency, base, secondary, at)
    if rates.to_base is None:
        return None
    return (price * rates.to_base.rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _benchmark_dietz_for_window(
    cur: psycopg.Cursor[Any],
    benchmark: tuple[UUID, str] | None,
    t0: date,
    t1: date,
    base: str,
    secondary: str,
) -> DietzResult | None:
    """Spec "Benchmarks": one-unit Modified Dietz, `C = 0`, on the security's own latest accepted
    price translated to base at `t0`/`t1`. `None` when there is no benchmark assignment for this
    scope at all — "No benchmark", never a zero-return proxy."""
    if benchmark is None:
        return None
    security_id, currency = benchmark
    bmv = _security_value_at(cur, security_id, currency, t0, base, secondary)
    emv = _security_value_at(cur, security_id, currency, t1, base, secondary)
    return modified_dietz([], t0, t1, bmv, emv)


_EARLIEST_EVENT_SQL = """
    SELECT min(event.event_date) FROM investment_transaction event
     WHERE event.household_id = %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
"""


def _inception_t0(cur: psycopg.Cursor[Any], household_id: UUID, as_of: date) -> date:
    """Spec "Period return": the calendar day before the household's earliest active investment
    event, household-wide (not restricted to currently active or holdings-mode accounts). With no
    investment history at all, `as_of` itself is returned — the inception window then has no
    flows and `BMV = EMV = 0`, so the exact `t0` chosen cannot change its `no_denominator` Dietz
    reason."""
    cur.execute(_EARLIEST_EVENT_SQL, (household_id,))
    row = cur.fetchone()
    earliest = row[0] if row else None
    return as_of if earliest is None else earliest - timedelta(days=1)


@dataclass(frozen=True)
class _MonthHoldingRow:
    holding_id: UUID
    asset_class: str
    value_base: Decimal | None
    status: str


# Spec "Contribution effects": the month-window population is every holding with a holdings-mode
# `month_end_position` row at `t0` or `t1` — restated from `month_end_position` and `holding`
# rather than `investments/positions.py::read_positions`, since a closed or excluded account's
# stored row must still count here.
_MONTH_HOLDINGS_SQL = """
    SELECT p.holding_id, security.asset_class::text, p.value_base, p.status::text
      FROM month_end_position p
      JOIN holding h ON h.household_id = p.household_id AND h.id = p.holding_id
      JOIN security ON security.id = h.security_id
     WHERE p.household_id = %s AND p.month_end = %s AND p.valuation_mode = 'holdings'
"""


def _load_month_holdings(
    cur: psycopg.Cursor[Any], household_id: UUID, month_end: date
) -> list[_MonthHoldingRow]:
    cur.execute(_MONTH_HOLDINGS_SQL, (household_id, month_end))
    return [_MonthHoldingRow(row[0], row[1], row[2], row[3]) for row in cur.fetchall()]


_MONTH_EVENTS_SQL = """
    SELECT security.asset_class::text, event.event_kind::text, event.event_date,
           COALESCE(event.net_amount_base, 0)
      FROM investment_transaction event
      JOIN holding h ON h.household_id = event.household_id AND h.id = event.holding_id
      JOIN security ON security.id = h.security_id
     WHERE event.household_id = %s AND event.holding_id = ANY(%s)
       AND event.event_date > %s AND event.event_date <= %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
     ORDER BY event.event_date, event.created_at
"""


def _load_month_events(
    cur: psycopg.Cursor[Any], household_id: UUID, holding_ids: list[UUID], t0: date, t1: date
) -> list[MonthFlowEvent]:
    if not holding_ids:
        return []
    cur.execute(_MONTH_EVENTS_SQL, (household_id, holding_ids, t0, t1))
    return [MonthFlowEvent(row[0], row[1], row[2], Decimal(row[3])) for row in cur.fetchall()]


@dataclass(frozen=True)
class _RawMonth:
    month_end: date
    previous_month_end: date
    opening: list[MonthPositionRow]
    closing: list[MonthPositionRow]
    events: list[MonthFlowEvent]
    status_counts: dict[str, int]


_MONTH_STATUS_COUNTS_SQL = """
    SELECT month_end, status::text, count(*)
      FROM month_end_position
     WHERE household_id = %s AND valuation_mode = 'holdings'
     GROUP BY month_end, status
"""

# Same one-row-per-household refresh marker `routes/wealth_history.py::_REFRESH_STATE` reads.
_REFRESH_STATE_SQL = """
    SELECT computed_through, computed_at FROM wealth_history_refresh WHERE household_id = %s
"""

_COVERAGE_ACCOUNTS_SQL = """
    SELECT a.id, a.name, period.valuation_mode::text
      FROM account a
      JOIN account_valuation_period period
        ON period.household_id = a.household_id AND period.account_id = a.id
       AND period.valuation_mode IN ('snapshot', 'ledger_balance')
       AND %(as_of)s >= period.effective_from
       AND (period.effective_to IS NULL OR %(as_of)s <= period.effective_to)
     WHERE a.household_id = %(household)s AND a.is_active
     ORDER BY a.name
"""

_BENCHMARKS_SQL = """
    SELECT b.scope_kind, b.asset_class::text, b.security_id, security.name, security.currency
      FROM investment_benchmark b
      JOIN security ON security.id = b.security_id
     WHERE b.household_id = %s
     ORDER BY b.scope_kind, b.asset_class
"""


def _load_stored_targets(cur: psycopg.Cursor[Any], household_id: UUID) -> dict[str, Decimal]:
    cur.execute(
        "SELECT label::text, ratio FROM investment_allocation_target WHERE household_id = %s",
        (household_id,),
    )
    return {row[0]: Decimal(row[1]) for row in cur.fetchall()}


class CoverageAccountOut(BaseModel):
    accountId: UUID
    accountName: str
    valuationMode: str


class EffectWindowOut(BaseModel):
    dietzPercent: str | None
    dietzReason: str | None
    contributions: str | None
    withdrawals: str | None
    income: str | None
    charges: str | None
    marketAndFx: str | None
    opening: str | None
    closing: str | None
    benchmarkDietzPercent: str | None
    benchmarkDietzReason: str | None
    excessPercent: str | None


def _effect_window_fields(
    dietz: DietzResult, lines: ContributionLines, benchmark: DietzResult | None
) -> dict[str, Any]:
    excess = None if benchmark is None else excess_percent(dietz, benchmark)
    return {
        "dietzPercent": _out(dietz.percent),
        "dietzReason": dietz.reason,
        "contributions": _money_out(lines.contributions),
        "withdrawals": _money_out(lines.withdrawals),
        "income": _money_out(lines.income),
        "charges": _money_out(lines.charges),
        "marketAndFx": _money_out(lines.market_and_fx),
        "opening": _money_out(lines.opening),
        "closing": _money_out(lines.closing),
        "benchmarkDietzPercent": None if benchmark is None else _out(benchmark.percent),
        "benchmarkDietzReason": None if benchmark is None else benchmark.reason,
        "excessPercent": _out(excess),
    }


def _effect_window_out(
    dietz: DietzResult, lines: ContributionLines, benchmark: DietzResult | None
) -> EffectWindowOut:
    return EffectWindowOut(**_effect_window_fields(dietz, lines, benchmark))


class ClassReturnOut(BaseModel):
    label: str
    xirrPercent: str | None
    xirrReason: str | None
    valueBase: str | None
    inception: EffectWindowOut


class AccountReturnOut(BaseModel):
    accountId: UUID
    accountName: str
    xirrPercent: str | None
    xirrReason: str | None
    valueBase: str | None


class YearClassOut(EffectWindowOut):
    label: str


class YearOut(BaseModel):
    year: int
    incompleteMonths: int
    portfolio: EffectWindowOut
    classes: list[YearClassOut]


class MonthClassOut(EffectWindowOut):
    label: str
    valueBase: str | None
    ratio: str | None
    targetRatio: str | None
    drift: str | None


class MonthOut(BaseModel):
    monthEnd: date
    complete: bool
    statusCounts: dict[str, int]
    portfolio: EffectWindowOut
    classes: list[MonthClassOut]
    returnCoordinate: int | None = None
    benchmarkCoordinate: int | None = None


def _attach_chart_coordinates(months: list[MonthOut]) -> None:
    """Add bounded display coordinates on one shared Decimal scale; never alter exact returns."""
    values: dict[tuple[date, str], Decimal] = {}
    for month in months:
        if not month.complete:
            continue
        if month.portfolio.dietzPercent is not None:
            values[(month.monthEnd, "actual")] = Decimal(month.portfolio.dietzPercent)
        if month.portfolio.benchmarkDietzPercent is not None:
            values[(month.monthEnd, "benchmark")] = Decimal(month.portfolio.benchmarkDietzPercent)
    ratios = chart_ratios(values)
    for month in months:
        actual = ratios.get((month.monthEnd, "actual"))
        benchmark = ratios.get((month.monthEnd, "benchmark"))
        month.returnCoordinate = None if actual is None else int(Decimal(actual) * 10000)
        month.benchmarkCoordinate = None if benchmark is None else int(Decimal(benchmark) * 10000)


class TargetGroupOut(BaseModel):
    label: str
    ratio: str


class TargetOut(BaseModel):
    source: Literal["rule", "inferred", "none"]
    groups: list[TargetGroupOut]


class BenchmarkOut(BaseModel):
    scopeKind: Literal["portfolio", "asset_class"]
    assetClass: str | None
    securityId: UUID
    securityName: str


class AnalysisResponse(BaseModel):
    asOf: date
    wealthHistoryComputedAt: Any | None
    wealthHistoryReason: str | None
    coverage: list[CoverageAccountOut]
    classes: list[ClassReturnOut]
    accounts: list[AccountReturnOut]
    inception: EffectWindowOut
    years: list[YearOut]
    months: list[MonthOut]
    target: TargetOut
    benchmarks: list[BenchmarkOut]


def _target_out(
    source: Literal["rule", "inferred", "none"], targets: dict[str, Decimal]
) -> TargetOut:
    return TargetOut(
        source=source,
        groups=[
            TargetGroupOut(label=label, ratio=str(targets[label]))
            for label in ASSET_CLASS_ORDER
            if label in targets
        ],
    )


def _month_out(
    m: MonthResult,
    benchmark_by_scope: dict[str | None, tuple[UUID, str]],
    base: str,
    secondary: str,
    cur: psycopg.Cursor[Any],
) -> MonthOut:
    portfolio_benchmark = None
    if m.complete:
        portfolio_benchmark = _benchmark_dietz_for_window(
            cur, benchmark_by_scope.get(None), m.previous_month_end, m.month_end, base, secondary
        )
    classes_out = []
    for effect in m.classes:
        benchmark = None
        if m.complete:
            benchmark = _benchmark_dietz_for_window(
                cur,
                benchmark_by_scope.get(effect.label),
                m.previous_month_end,
                m.month_end,
                base,
                secondary,
            )
        classes_out.append(
            MonthClassOut(
                label=effect.label,
                valueBase=_money_out(effect.lines.closing),
                ratio=_out(effect.ratio),
                targetRatio=_out(effect.target_ratio),
                drift=_out(effect.drift),
                **_effect_window_fields(effect.dietz, effect.lines, benchmark),
            )
        )
    return MonthOut(
        monthEnd=m.month_end,
        complete=m.complete,
        statusCounts=dict(m.status_counts),
        portfolio=_effect_window_out(m.portfolio_dietz, m.portfolio_lines, portfolio_benchmark),
        classes=classes_out,
    )


def _year_out(
    year: int,
    months: list[MonthResult],
    benchmark_by_scope: dict[str | None, tuple[UUID, str]],
    base: str,
    secondary: str,
    cur: psycopg.Cursor[Any],
) -> YearOut:
    result = compute_year(year, months)
    if result.incomplete_months:
        return YearOut(
            year=result.year,
            incompleteMonths=result.incomplete_months,
            portfolio=_effect_window_out(result.portfolio_dietz, result.portfolio_lines, None),
            classes=[],
        )
    ordered = sorted(months, key=lambda m: m.month_end)
    t0, t1 = ordered[0].previous_month_end, ordered[-1].month_end
    portfolio_benchmark = _benchmark_dietz_for_window(
        cur, benchmark_by_scope.get(None), t0, t1, base, secondary
    )
    classes_out = [
        YearClassOut(
            label=class_result.label,
            **_effect_window_fields(
                class_result.dietz,
                class_result.lines,
                _benchmark_dietz_for_window(
                    cur, benchmark_by_scope.get(class_result.label), t0, t1, base, secondary
                ),
            ),
        )
        for class_result in result.classes
    ]
    return YearOut(
        year=result.year,
        incompleteMonths=0,
        portfolio=_effect_window_out(
            result.portfolio_dietz, result.portfolio_lines, portfolio_benchmark
        ),
        classes=classes_out,
    )


def _parse_as_of(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="asOf must be an ISO date") from None


@router.get("/analysis", response_model=AnalysisResponse)
def portfolio_analysis(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[str | None, Query()] = None,
) -> AnalysisResponse:
    as_of = _parse_as_of(asOf)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        scope_to_owner(conn, owner.user_id)
        base, secondary = household_currencies(cur, owner.household_id)

        all_holdings = _load_household_holdings(cur, owner.household_id, as_of)
        counterpart_holding = _load_counterpart_holdings(cur, owner.household_id, as_of)
        class_label_by_holding = {h.holding_id: h.asset_class for h in all_holdings}
        account_label_by_holding = {h.holding_id: str(h.account_id) for h in all_holdings}
        rate_cache: dict[str, Decimal | None] = {}

        class_labels = sorted({h.asset_class for h in all_holdings})
        class_groups = {
            label: _group_result(
                cur,
                [h for h in all_holdings if h.asset_class == label],
                label,
                class_label_by_holding,
                as_of,
                base,
                secondary,
                counterpart_holding,
                rate_cache,
            )
            for label in class_labels
        }

        account_name_by_id = {h.account_id: h.account_name for h in all_holdings}
        account_ids = sorted(account_name_by_id, key=str)
        account_groups = {
            account_id: _group_result(
                cur,
                [h for h in all_holdings if h.account_id == account_id],
                str(account_id),
                account_label_by_holding,
                as_of,
                base,
                secondary,
                counterpart_holding,
                rate_cache,
            )
            for account_id in account_ids
        }

        portfolio_group = _group_result(
            cur,
            all_holdings,
            None,
            class_label_by_holding,
            as_of,
            base,
            secondary,
            counterpart_holding,
            rate_cache,
        )

        cur.execute(_BENCHMARKS_SQL, (owner.household_id,))
        benchmark_rows = cur.fetchall()
        benchmark_by_scope: dict[str | None, tuple[UUID, str]] = {}
        benchmarks_out: list[BenchmarkOut] = []
        for scope_kind, asset_class, security_id, security_name, currency in benchmark_rows:
            key = None if scope_kind == "portfolio" else asset_class
            benchmark_by_scope[key] = (security_id, str(currency).strip())
            benchmarks_out.append(
                BenchmarkOut(
                    scopeKind=scope_kind,
                    assetClass=asset_class,
                    securityId=security_id,
                    securityName=security_name,
                )
            )

        t0_inception = _inception_t0(cur, owner.household_id, as_of)
        portfolio_dietz, portfolio_lines = inception_window(
            portfolio_group.computation, t0_inception, as_of
        )
        portfolio_inception_benchmark = _benchmark_dietz_for_window(
            cur, benchmark_by_scope.get(None), t0_inception, as_of, base, secondary
        )
        inception_out = _effect_window_out(
            portfolio_dietz, portfolio_lines, portfolio_inception_benchmark
        )

        classes_out: list[ClassReturnOut] = []
        for label in class_labels:
            group = class_groups[label]
            dietz, lines = inception_window(group.computation, t0_inception, as_of)
            benchmark = _benchmark_dietz_for_window(
                cur, benchmark_by_scope.get(label), t0_inception, as_of, base, secondary
            )
            classes_out.append(
                ClassReturnOut(
                    label=label,
                    xirrPercent=_out(group.computation.xirr_result.rate_percent),
                    xirrReason=group.computation.xirr_result.reason,
                    valueBase=_group_value_out(group),
                    inception=_effect_window_out(dietz, lines, benchmark),
                )
            )

        accounts_out = [
            AccountReturnOut(
                accountId=account_id,
                accountName=account_name_by_id[account_id],
                xirrPercent=_out(account_groups[account_id].computation.xirr_result.rate_percent),
                xirrReason=account_groups[account_id].computation.xirr_result.reason,
                valueBase=_group_value_out(account_groups[account_id]),
            )
            for account_id in account_ids
        ]

        cur.execute(_COVERAGE_ACCOUNTS_SQL, {"household": owner.household_id, "as_of": as_of})
        coverage_out = [
            CoverageAccountOut(accountId=row[0], accountName=row[1], valuationMode=row[2])
            for row in cur.fetchall()
        ]

        cur.execute(_REFRESH_STATE_SQL, (owner.household_id,))
        state = cur.fetchone()
        wealth_history_reason: str | None = None
        wealth_history_computed_at: Any | None = None
        months_out: list[MonthOut] = []
        years_out: list[YearOut] = []
        if state is None:
            wealth_history_reason = "wealth_history_not_refreshed"
            stored_targets = _load_stored_targets(cur, owner.household_id)
            target_out = (
                _target_out("rule", stored_targets) if stored_targets else _target_out("none", {})
            )
        else:
            computed_through, computed_at = state
            wealth_history_computed_at = computed_at
            all_month_ends = stored_month_ends(computed_through)

            cur.execute(_MONTH_STATUS_COUNTS_SQL, (owner.household_id,))
            status_counts_by_month: dict[date, dict[str, int]] = {}
            for month_end_value, status_value, count in cur.fetchall():
                status_counts_by_month.setdefault(month_end_value, {})[status_value] = int(count)

            raw_months: list[_RawMonth] = []
            for i in range(1, len(all_month_ends)):
                t0, t1 = all_month_ends[i - 1], all_month_ends[i]
                opening_rows = _load_month_holdings(cur, owner.household_id, t0)
                closing_rows = _load_month_holdings(cur, owner.household_id, t1)
                holding_ids = sorted(
                    {row.holding_id for row in (*opening_rows, *closing_rows)}, key=str
                )
                events = _load_month_events(cur, owner.household_id, holding_ids, t0, t1)
                raw_months.append(
                    _RawMonth(
                        t1,
                        t0,
                        [
                            MonthPositionRow(r.asset_class, r.value_base, r.status)
                            for r in opening_rows
                        ],
                        [
                            MonthPositionRow(r.asset_class, r.value_base, r.status)
                            for r in closing_rows
                        ],
                        events,
                        status_counts_by_month.get(t1, {}),
                    )
                )

            def _compute_all_months(targets: dict[str, Decimal]) -> list[MonthResult]:
                return [
                    compute_month(
                        m.month_end,
                        m.previous_month_end,
                        m.opening,
                        m.closing,
                        m.events,
                        m.status_counts,
                        targets,
                    )
                    for m in raw_months
                ]

            bare_months = _compute_all_months({})
            stored_targets = _load_stored_targets(cur, owner.household_id)
            if stored_targets:
                targets = stored_targets
                target_source: Literal["rule", "inferred", "none"] = "rule"
            else:
                first_complete = next((m for m in bare_months if m.complete), None)
                if first_complete is None:
                    targets = {}
                    target_source = "none"
                else:
                    ratios = {
                        effect.label: effect.ratio
                        for effect in first_complete.classes
                        if effect.ratio is not None
                    }
                    inferred = infer_target(ratios)
                    targets = {g.label: g.ratio for g in inferred}
                    target_source = "inferred" if inferred else "none"

            final_months = bare_months if not targets else _compute_all_months(targets)
            target_out = _target_out(target_source, targets)

            months_out = [
                _month_out(m, benchmark_by_scope, base, secondary, cur)
                for m in sorted(final_months, key=lambda m: m.month_end, reverse=True)
            ]
            _attach_chart_coordinates(months_out)
            years_by_year: dict[int, list[MonthResult]] = {}
            for m in final_months:
                years_by_year.setdefault(m.month_end.year, []).append(m)
            years_out = [
                _year_out(year, months, benchmark_by_scope, base, secondary, cur)
                for year, months in sorted(years_by_year.items(), reverse=True)
            ]

    return AnalysisResponse(
        asOf=as_of,
        wealthHistoryComputedAt=wealth_history_computed_at,
        wealthHistoryReason=wealth_history_reason,
        coverage=coverage_out,
        classes=classes_out,
        accounts=accounts_out,
        inception=inception_out,
        years=years_out,
        months=months_out,
        target=target_out,
        benchmarks=benchmarks_out,
    )


class AllocationTargetGroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    ratio: str


class AllocationTargetsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[AllocationTargetGroupIn]


class AllocationTargetsOut(BaseModel):
    groups: list[TargetGroupOut]


def _parse_ratio(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="ratio must be a decimal string") from None
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -10:
        raise HTTPException(
            status_code=422, detail="ratio must not have more than ten decimal places"
        )
    if value < 0 or value > 1:
        raise HTTPException(status_code=422, detail="ratio must be between 0 and 1")
    return value


@router.get("/allocation-targets", response_model=AllocationTargetsOut)
def get_allocation_targets(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> AllocationTargetsOut:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            "SELECT label::text, ratio FROM investment_allocation_target"
            " WHERE household_id = %s ORDER BY label",
            (owner.household_id,),
        )
        groups = [TargetGroupOut(label=row[0], ratio=str(row[1])) for row in cur.fetchall()]
    return AllocationTargetsOut(groups=groups)


@router.put("/allocation-targets", response_model=AllocationTargetsOut)
def put_allocation_targets(
    body: AllocationTargetsIn, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> AllocationTargetsOut:
    parsed: dict[str, Decimal] = {}
    for group in body.groups:
        if group.label not in ASSET_CLASS_ORDER:
            raise HTTPException(status_code=422, detail=f"{group.label} is not an asset class")
        if group.label in parsed:
            raise HTTPException(status_code=422, detail=f"{group.label} appears twice")
        parsed[group.label] = _parse_ratio(group.ratio)
    total = sum(parsed.values(), Decimal(0))
    if total != Decimal("1.0000000000"):
        raise HTTPException(status_code=422, detail="ratios must sum to exactly 1")

    # One transaction: nothing is deleted or written until every row above has been validated in
    # Python, so a late failure here (a database-level refusal this loop did not already catch)
    # rolls back and leaves the previous set exactly as it was.
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "DELETE FROM investment_allocation_target WHERE household_id = %s",
            (owner.household_id,),
        )
        for label, ratio in parsed.items():
            cur.execute(
                "INSERT INTO investment_allocation_target (household_id, label, ratio)"
                " VALUES (%s, %s, %s)",
                (owner.household_id, label, ratio),
            )
    return AllocationTargetsOut(
        groups=[
            TargetGroupOut(label=label, ratio=str(parsed[label]))
            for label in ASSET_CLASS_ORDER
            if label in parsed
        ]
    )


@router.delete("/allocation-targets", status_code=status.HTTP_204_NO_CONTENT)
def delete_allocation_targets(owner: Annotated[OwnerSession, Depends(require_owner)]) -> Response:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "DELETE FROM investment_allocation_target WHERE household_id = %s",
            (owner.household_id,),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class BenchmarksOut(BaseModel):
    benchmarks: list[BenchmarkOut]


class BenchmarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    securityId: UUID


def _check_benchmark_scope(scope_kind: str, asset_class: str | None) -> None:
    if scope_kind == "asset_class":
        if asset_class is None:
            raise HTTPException(
                status_code=422, detail="assetClass is required for the asset_class scope"
            )
        if asset_class not in ASSET_CLASS_ORDER:
            raise HTTPException(status_code=422, detail=f"{asset_class} is not an asset class")
    elif asset_class is not None:
        raise HTTPException(
            status_code=422, detail="assetClass must not be given for the portfolio scope"
        )


@router.get("/benchmarks", response_model=BenchmarksOut)
def get_benchmarks(owner: Annotated[OwnerSession, Depends(require_owner)]) -> BenchmarksOut:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            "SELECT b.scope_kind, b.asset_class::text, b.security_id, security.name"
            " FROM investment_benchmark b JOIN security ON security.id = b.security_id"
            " WHERE b.household_id = %s ORDER BY b.scope_kind, b.asset_class",
            (owner.household_id,),
        )
        benchmarks = [
            BenchmarkOut(
                scopeKind=row[0], assetClass=row[1], securityId=row[2], securityName=row[3]
            )
            for row in cur.fetchall()
        ]
    return BenchmarksOut(benchmarks=benchmarks)


@router.put("/benchmarks/{scopeKind}", response_model=BenchmarkOut)
def put_benchmark(
    scopeKind: Literal["portfolio", "asset_class"],
    body: BenchmarkIn,
    owner: Annotated[OwnerSession, Depends(require_owner)],
    assetClass: Annotated[str | None, Query()] = None,
) -> BenchmarkOut:
    _check_benchmark_scope(scopeKind, assetClass)
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        # The application's own DB role owns these tables and so bypasses RLS (V30's
        # `security_read` is enforced only for `authenticated`, the SQL-test role) — this must
        # therefore filter explicitly, exactly like `investments/staging.py`'s own security
        # lookup: the household's own security or a shared reference row (`household_id IS
        # NULL`). An invisible or nonexistent id is indistinguishable here, both refused the
        # same way (spec "bad seed-table reference").
        cur.execute(
            "SELECT name FROM security WHERE id = %s AND (household_id IS NULL OR"
            " household_id = %s)",
            (body.securityId, owner.household_id),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=422, detail="security not found")
        security_name = found[0]
        cur.execute(
            "INSERT INTO investment_benchmark (household_id, scope_kind, asset_class, security_id)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (household_id, scope_kind, asset_class) DO UPDATE"
            " SET security_id = EXCLUDED.security_id, updated_at = now()",
            (owner.household_id, scopeKind, assetClass, body.securityId),
        )
    return BenchmarkOut(
        scopeKind=scopeKind,
        assetClass=assetClass,
        securityId=body.securityId,
        securityName=security_name,
    )


@router.delete("/benchmarks/{scopeKind}", status_code=status.HTTP_204_NO_CONTENT)
def delete_benchmark(
    scopeKind: Literal["portfolio", "asset_class"],
    owner: Annotated[OwnerSession, Depends(require_owner)],
    assetClass: Annotated[str | None, Query()] = None,
) -> Response:
    _check_benchmark_scope(scopeKind, assetClass)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "DELETE FROM investment_benchmark WHERE household_id = %s AND scope_kind = %s"
            " AND asset_class IS NOT DISTINCT FROM %s",
            (owner.household_id, scopeKind, assetClass),
        )
        removed = cur.rowcount
    if removed == 0:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
