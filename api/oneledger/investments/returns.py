"""Exact XIRR and allocation, per the spec's "Returns and allocation" section.

The solver, its grid, tolerances and reason codes are the specification's contract, not a
convenience approximation — every step name below (`y_min`/`y_max`/`step`, the 4,097-point log
scan, the 1e-12 rate tolerance, the 1e-24 scaled-residual tolerance) matches its prose exactly so
the two stay auditable against each other.

`holding_flows` and `portfolio_flows` are thin database readers around two pure, DB-free
functions — `classify_event_flows` and `group_allocation` — so the event-kind and grouping rules
that decide what counts as a flow are unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Any, Literal
from uuid import UUID

import psycopg

from .positions import current_rate_pair, read_positions

_PRECISION = 50
_RATE_MIN = Decimal("-0.999999999999")
_RATE_MAX = Decimal("1000000")
_GRID_STEPS = 4096
_RATE_TOLERANCE = Decimal("0.000000000001")  # 1e-12
_RESIDUAL_TOLERANCE = Decimal("1E-24")
_PERCENT_QUANT = Decimal("0.00000001")  # 8 dp — NUMERIC(20,8)
_ALLOCATION_QUANT = Decimal("0.0000000001")  # 10 dp — NUMERIC(20,10)
_BISECTION_STEPS = 200

XirrReason = Literal[
    "no_sign_change",
    "multiple_roots",
    "missing_terminal_value",
    "incomplete_history",
    "missing_price",
    "blocked_action",
    "not_converged",
]

# Kinds that move external cash. Reinvested distributions, bonus, split and every transfer leg
# are cash-neutral (net_amount = 0 by the V31 CHECK) and are excluded rather than added as
# zero-value flows.
_OUTFLOW_KINDS = frozenset({"purchase", "recurring_purchase", "rights", "charge", "withheld_tax"})
_INFLOW_KINDS = frozenset({"sale", "redemption", "maturity", "cash_distribution", "interest"})
_NO_FLOW_KINDS = frozenset({"reinvested_distribution", "bonus", "split"})
# A holding that ever received or sent one of these cannot state its own acquisition history:
# the paired leg lives on a different holding or account. Portfolio-level flows are unaffected —
# both legs are net_amount = 0, so they already contribute nothing to the sum.
_BLOCKING_KINDS = frozenset(
    {
        "switch_in",
        "switch_out",
        "systematic_transfer_in",
        "systematic_transfer_out",
        "platform_transfer_in",
        "platform_transfer_out",
    }
)


@dataclass(frozen=True)
class CashFlow:
    on: date
    amount: Decimal


@dataclass(frozen=True)
class XirrResult:
    rate_percent: Decimal | None
    reason: XirrReason | None


@dataclass(frozen=True)
class FlowsResult:
    flows: list[CashFlow] | None
    reason: XirrReason | None


@dataclass(frozen=True)
class ClassifiedFlows:
    flows: list[CashFlow]
    has_blocking_leg: bool
    has_unknown_kind: bool


def classify_event_flows(events: Sequence[tuple[str, date, Decimal]]) -> ClassifiedFlows:
    """Turn ordered (event_kind, event_date, net_amount) rows into external cash flows.

    Pure and DB-free: `holding_flows`/`portfolio_flows` read the rows and add the terminal
    value; this decides, once, which event kinds move money and which do not.
    """
    flows: list[CashFlow] = []
    has_blocking = False
    has_unknown = False
    for kind, event_date, net_amount in events:
        if kind in _BLOCKING_KINDS:
            has_blocking = True
            continue
        if kind in _NO_FLOW_KINDS:
            continue
        if kind in _OUTFLOW_KINDS or kind in _INFLOW_KINDS:
            if net_amount != 0:
                flows.append(CashFlow(event_date, net_amount))
            continue
        has_unknown = True
    return ClassifiedFlows(flows, has_blocking, has_unknown)


def _residual(flows: Sequence[CashFlow], d0: date, rate: Decimal, scale: Decimal) -> Decimal:
    base = Decimal(1) + rate
    total = Decimal(0)
    for flow in flows:
        years = Decimal((flow.on - d0).days) / Decimal(365)
        total += flow.amount / (base**years)
    return total / scale


def _bisect(
    residual: Callable[[Decimal], Decimal], lo_r: Decimal, hi_r: Decimal, lo_f: Decimal
) -> tuple[Decimal, bool]:
    """Safeguarded bisection: the bracket already sign-changed, so this always converges."""
    mid, mid_f = lo_r, lo_f
    for _ in range(_BISECTION_STEPS):
        mid = (lo_r + hi_r) / 2
        mid_f = residual(mid)
        if hi_r - lo_r <= _RATE_TOLERANCE and abs(mid_f) <= _RESIDUAL_TOLERANCE:
            return mid, True
        if mid_f == 0:
            return mid, True
        if (mid_f < 0) == (lo_f < 0):
            lo_r, lo_f = mid, mid_f
        else:
            hi_r = mid
    return mid, hi_r - lo_r <= _RATE_TOLERANCE and abs(mid_f) <= _RESIDUAL_TOLERANCE


def xirr(flows: Sequence[CashFlow]) -> XirrResult:
    """`sum(CF_i / (1 + r)^((d_i - d_0) / 365)) = 0` over a 50-significant-digit log-scale scan."""
    scale = sum((abs(flow.amount) for flow in flows), Decimal(0))
    if len(flows) < 2 or scale == 0:
        return XirrResult(None, "no_sign_change")

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        d0 = min(flow.on for flow in flows)

        def residual(rate: Decimal) -> Decimal:
            return _residual(flows, d0, rate, scale)

        y_min = (_RATE_MIN + 1).ln()
        y_max = (_RATE_MAX + 1).ln()
        step = (y_max - y_min) / _GRID_STEPS

        grid_r: list[Decimal] = []
        grid_f: list[Decimal] = []
        for i in range(_GRID_STEPS + 1):
            r = (y_min + step * i).exp() - 1
            grid_r.append(r)
            grid_f.append(residual(r))

        roots: list[Decimal] = [
            grid_r[i] for i, value in enumerate(grid_f) if abs(value) <= _RESIDUAL_TOLERANCE
        ]
        for i in range(_GRID_STEPS):
            lo_f, hi_f = grid_f[i], grid_f[i + 1]
            if lo_f == 0 or hi_f == 0 or (lo_f < 0) == (hi_f < 0):
                continue
            root, converged = _bisect(residual, grid_r[i], grid_r[i + 1], lo_f)
            if not converged:
                return XirrResult(None, "not_converged")
            roots.append(root)

        if not roots:
            return XirrResult(None, "no_sign_change")

        roots.sort()
        merged = [roots[0]]
        for root in roots[1:]:
            if root - merged[-1] > _RATE_TOLERANCE:
                merged.append(root)
        if len(merged) > 1:
            return XirrResult(None, "multiple_roots")

        percent = (merged[0] * 100).quantize(_PERCENT_QUANT)
        return XirrResult(percent, None)


_EVENTS_SQL = """
    SELECT event.event_kind::text, event.event_date, event.net_amount
      FROM investment_transaction event
     WHERE event.holding_id = %s AND event.event_date <= %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
     ORDER BY event.event_date, event.created_at
"""

# Portfolio flows mix securities that can hold different currencies, so this reads each event's
# already-frozen base-currency amount (net_amount_base) rather than native — no as-of FX guess
# re-translates a flow that money.py already froze once, at its own day (I5).
_PORTFOLIO_EVENTS_SQL = """
    SELECT event.event_kind::text, event.event_date, COALESCE(event.net_amount_base, 0)
      FROM investment_transaction event
     WHERE event.holding_id = %s AND event.event_date <= %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction correction
            WHERE correction.corrects_transaction_id = event.id)
     ORDER BY event.event_date, event.created_at
"""

_UNITS_SQL = """
    SELECT h.security_id, security.currency, COALESCE(sum(effect.signed_units), 0)
      FROM holding h
      JOIN security ON security.id = h.security_id
      LEFT JOIN investment_unit_effect effect
        ON effect.holding_id = h.id AND effect.effect_date <= %s
     WHERE h.id = %s
     GROUP BY h.security_id, security.currency
"""

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


def holding_flows(cur: psycopg.Cursor[Any], holding_id: UUID, as_of: date) -> FlowsResult:
    """One holding's own external cash-flow series, native currency, terminal value included."""
    cur.execute(_EVENTS_SQL, (holding_id, as_of))
    classified = classify_event_flows(
        [(kind, event_date, Decimal(amount)) for kind, event_date, amount in cur.fetchall()]
    )
    if classified.has_unknown_kind:
        return FlowsResult(None, "blocked_action")
    if classified.has_blocking_leg:
        return FlowsResult(None, "incomplete_history")

    flows = list(classified.flows)
    cur.execute(_UNITS_SQL, (as_of, holding_id))
    unit_row = cur.fetchone()
    if unit_row is not None and Decimal(unit_row[2]) > 0:
        security_id, units = unit_row[0], Decimal(unit_row[2])
        priced = _latest_price(cur, security_id, as_of)
        if priced is None:
            return FlowsResult(None, "missing_price")
        price, _currency = priced
        flows.append(CashFlow(as_of, units * price))

    if not flows:
        return FlowsResult(None, "missing_terminal_value")
    return FlowsResult(flows, None)


_HOUSEHOLD_HOLDINGS_SQL = "SELECT id FROM holding WHERE household_id = %s"


def portfolio_flows(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> FlowsResult:
    """Every holding's frozen base-currency flows plus one terminal current-value flow.

    A single missing price, or a holding whose currency has no as-of base rate, fails the whole
    result rather than silently omitting a holding from the terminal value — a portfolio
    denominator never drops coverage quietly.
    """
    cur.execute(_HOUSEHOLD_HOLDINGS_SQL, (household_id,))
    holding_ids = [row[0] for row in cur.fetchall()]

    flows: list[CashFlow] = []
    open_value = Decimal(0)
    any_open = False
    rate_cache: dict[str, Decimal | None] = {}
    for holding_id in holding_ids:
        cur.execute(_PORTFOLIO_EVENTS_SQL, (holding_id, as_of))
        classified = classify_event_flows(
            [(kind, event_date, Decimal(amount)) for kind, event_date, amount in cur.fetchall()]
        )
        if classified.has_unknown_kind:
            return FlowsResult(None, "blocked_action")
        flows.extend(classified.flows)

        cur.execute(_UNITS_SQL, (as_of, holding_id))
        unit_row = cur.fetchone()
        if unit_row is None or Decimal(unit_row[2]) <= 0:
            continue
        any_open = True
        security_id, currency, units = unit_row[0], str(unit_row[1]).strip(), Decimal(unit_row[2])
        priced = _latest_price(cur, security_id, as_of)
        if priced is None:
            return FlowsResult(None, "missing_price")
        price, _price_currency = priced
        if currency not in rate_cache:
            rates = current_rate_pair(cur, currency, base, secondary, as_of)
            rate_cache[currency] = None if rates.to_base is None else rates.to_base.rate
        rate = rate_cache[currency]
        if rate is None:
            return FlowsResult(None, "missing_price")
        open_value += units * price * rate

    if any_open:
        flows.append(CashFlow(as_of, open_value))
    if not flows:
        return FlowsResult(None, "missing_terminal_value")
    return FlowsResult(flows, None)


@dataclass(frozen=True)
class PositionValue:
    label_asset_class: str
    label_account: str
    value_base: Decimal
    is_valued: bool


@dataclass(frozen=True)
class AllocationGroup:
    label: str
    value_base: Decimal
    ratio: Decimal | None


@dataclass(frozen=True)
class Allocation:
    by_asset_class: list[AllocationGroup]
    by_account: list[AllocationGroup]
    total_value_base: Decimal
    complete: bool


def group_allocation(positions: Sequence[PositionValue]) -> Allocation:
    """Group current value by asset class and by account.

    Value is the exact sum of the valued subset. A ratio is produced only when every included
    position is valued — an incomplete denominator states its groups' sums without pretending a
    percentage of an unknown whole.
    """
    complete = all(position.is_valued for position in positions)
    total = sum((position.value_base for position in positions if position.is_valued), Decimal(0))

    def _grouped(label_of: Callable[[PositionValue], str]) -> list[AllocationGroup]:
        totals: dict[str, Decimal] = {}
        for position in positions:
            if not position.is_valued:
                continue
            label = label_of(position)
            totals[label] = totals.get(label, Decimal(0)) + position.value_base
        return [
            AllocationGroup(
                label,
                value,
                (value / total).quantize(_ALLOCATION_QUANT) if complete and total != 0 else None,
            )
            for label, value in sorted(totals.items())
        ]

    return Allocation(
        by_asset_class=_grouped(lambda p: p.label_asset_class),
        by_account=_grouped(lambda p: p.label_account),
        total_value_base=total,
        complete=complete,
    )


def allocation(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> Allocation:
    """Thin database wrapper: read positions, then `group_allocation` does the pure grouping."""
    positions = read_positions(cur, household_id, base, secondary, as_of)
    values = [
        PositionValue(
            label_asset_class=position.assetClass,
            label_account=str(position.accountId),
            value_base=Decimal(position.currentValueBase)
            if position.currentValueBase is not None
            else Decimal(0),
            is_valued=position.valueStatus == "available",
        )
        for position in positions
    ]
    return group_allocation(values)


__all__ = [
    "Allocation",
    "AllocationGroup",
    "CashFlow",
    "ClassifiedFlows",
    "FlowsResult",
    "PositionValue",
    "XirrReason",
    "XirrResult",
    "allocation",
    "classify_event_flows",
    "group_allocation",
    "holding_flows",
    "portfolio_flows",
    "xirr",
]
