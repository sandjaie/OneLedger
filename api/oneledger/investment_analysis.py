"""Phase 4.3 Task 1: the pure investment-analysis engine (no database, `docs/superpowers/specs/
2026-09-16-phase-4-3-investment-analysis.md`). `routes/portfolio.py` is the only caller: it loads
holdings, active investment events, stored month-end positions, allocation targets and benchmark
prices, then hands them here. Everything below is `Decimal` arithmetic; nothing fabricates a
price, an index or a filled gap.

`investments.returns.xirr` is reused directly as the solver — this module never re-implements the
grid/bisection search. `investments.returns.group_allocation` is reused directly for allocation
ratios. The one genuinely new rule is the group-scoped switch/systematic-transfer/platform-
transfer leg: `investments.returns.classify_event_flows`'s own `has_blocking_leg` is a single
household-wide boolean and cannot say whether *this* leg's counterpart is inside *this* class or
account group, so `compute_group` below restates that one classification step with a per-event
`counterpart_in_group` answer the caller resolves from `counterpart_transaction_id`. The event-
kind vocabulary sets mirror `investments.returns.py`'s own — the schema's fixed enum, not a
second solver.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal

from .investments.returns import CashFlow, PositionValue, XirrResult, group_allocation, xirr

_PERCENT_QUANT = Decimal("0.00000001")  # 8 dp — matches returns.py's XIRR/Dietz percent scale
_RATIO_QUANT = Decimal("0.0000000001")  # 10 dp — matches returns.py's allocation ratio scale
_DIETZ_PRECISION = 50

# `security.asset_class` enum order (V30's `CREATE TYPE asset_class`) — used to break a tied
# "largest class" in target inference (spec "Allocation drift": "the first label in enum order
# if tied") and to order the target's own group list.
ASSET_CLASS_ORDER: tuple[str, ...] = (
    "equity",
    "fixed_income",
    "cash",
    "commodity",
    "real_asset",
    "alternative",
    "other",
)

# Kinds that move external cash outward/inward, matching investments/returns.py's own
# `_OUTFLOW_KINDS`/`_INFLOW_KINDS`/`_NO_FLOW_KINDS`/`_BLOCKING_KINDS` exactly (V31/V37/V41's
# `investment_event_kind` enum).
_OUTFLOW_KINDS = frozenset({"purchase", "recurring_purchase", "rights", "charge", "withheld_tax"})
_INFLOW_KINDS = frozenset({"sale", "redemption", "maturity", "cash_distribution", "interest"})
_NO_FLOW_KINDS = frozenset({"reinvested_distribution", "bonus", "split"})
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

# Spec "Contribution effects" table: the Dietz-signed line each kind contributes to.
_CONTRIBUTION_KINDS = frozenset({"purchase", "recurring_purchase", "rights"})
_WITHDRAWAL_KINDS = frozenset({"sale", "redemption", "maturity"})
_INCOME_KINDS = frozenset({"cash_distribution", "interest"})
_CHARGE_KINDS = frozenset({"charge", "withheld_tax"})

# XIRR reasons that mean the group's own current value cannot be trusted either — a genuinely
# incomplete or blocked history has no honest EMV, not merely no honest rate. `no_sign_change`,
# `multiple_roots`, `not_converged` and `missing_terminal_value` all mean the value and flows
# were fine but the rate itself is unsolved (or there was nothing to solve) — EMV stays usable.
_VALUE_INVALIDATING_XIRR_REASONS = frozenset(
    {"missing_price", "incomplete_history", "blocked_action"}
)

DietzReason = Literal["no_denominator", "missing_price"]


@dataclass(frozen=True)
class GroupEvent:
    """One active event on a holding inside an XIRR/Dietz group, on or before as-of.

    `counterpart_in_group` is only consulted for a switch/systematic-transfer/platform-transfer
    leg: whether *this* leg's own counterpart holding (via `counterpart_transaction_id`) belongs
    to the same group being built. Ignored for every other kind.
    """

    kind: str
    on: date
    net_amount_base: Decimal
    counterpart_in_group: bool = True


@dataclass(frozen=True)
class DietzFlow:
    """One classified flow, kept in the XIRR sign convention (a purchase is negative). Modified
    Dietz negates this to get its own `C_i` (spec "Period return (Modified Dietz)")."""

    kind: str
    on: date
    net_amount_base: Decimal


@dataclass(frozen=True)
class DietzResult:
    percent: Decimal | None
    reason: DietzReason | None


@dataclass(frozen=True)
class ContributionLines:
    """Spec "Contribution effects" table, all in base. `market_and_fx` is the plug —
    `opening + contributions + withdrawals + income + charges + market_and_fx == closing` is
    definitional, not itself a test; the tested identity is that one window's class lines sum to
    that window's portfolio line, field by field."""

    opening: Decimal | None
    closing: Decimal | None
    contributions: Decimal | None
    withdrawals: Decimal | None
    income: Decimal | None
    charges: Decimal | None
    market_and_fx: Decimal | None


_NULL_LINES = ContributionLines(None, None, None, None, None, None, None)


@dataclass(frozen=True)
class GroupComputation:
    """One XIRR group's (class, account or the whole portfolio) classification. `xirr_result`
    matches `investments.returns.xirr`'s own contract exactly. `dietz_flows` and
    `terminal_value` are the same underlying events and current value, ready for
    `modified_dietz`/`contribution_lines` (`terminal_value` is `None` whenever nothing in the
    group is currently open, or the group's own classification failed)."""

    xirr_result: XirrResult
    dietz_flows: tuple[DietzFlow, ...]
    terminal_value: Decimal | None


def compute_group(
    events: Sequence[GroupEvent],
    as_of: date,
    *,
    any_open: bool,
    value_complete: bool,
    terminal_value: Decimal,
) -> GroupComputation:
    """Class/account/portfolio XIRR: `investments.returns.portfolio_flows`'s own flow
    construction, restricted to one group's events, except a switch, systematic-transfer or
    platform-transfer leg is cash-neutral only when its own `counterpart_in_group` is true;
    otherwise the whole group is `incomplete_history` (spec "Class and account returns"). A
    missing price or as-of base rate on any open holding in the group still fails the whole
    group (`missing_price`, signalled by the caller as `value_complete=False`), exactly as
    `portfolio_flows` does; an unknown event kind is `blocked_action`. `any_open`,
    `value_complete` and `terminal_value` are the caller's own as-of valuation facts for this
    exact group (typically from `investments.returns.group_allocation`) — this function makes no
    price or FX decision itself.
    """
    flows: list[CashFlow] = []
    dietz_flows: list[DietzFlow] = []
    has_blocking = False
    has_unknown = False
    for event in events:
        if event.kind in _BLOCKING_KINDS:
            if not event.counterpart_in_group:
                has_blocking = True
            continue
        if event.kind in _NO_FLOW_KINDS:
            continue
        if event.kind in _OUTFLOW_KINDS or event.kind in _INFLOW_KINDS:
            dietz_flows.append(DietzFlow(event.kind, event.on, event.net_amount_base))
            if event.net_amount_base != 0:
                flows.append(CashFlow(event.on, event.net_amount_base))
            continue
        has_unknown = True
    dietz_tuple = tuple(dietz_flows)

    if has_unknown:
        return GroupComputation(XirrResult(None, "blocked_action"), dietz_tuple, None)
    if has_blocking:
        return GroupComputation(XirrResult(None, "incomplete_history"), dietz_tuple, None)
    if any_open and not value_complete:
        return GroupComputation(XirrResult(None, "missing_price"), dietz_tuple, None)

    terminal = terminal_value if any_open else None
    xirr_flows = list(flows)
    if any_open:
        xirr_flows.append(CashFlow(as_of, terminal_value))
    if not xirr_flows:
        return GroupComputation(XirrResult(None, "missing_terminal_value"), dietz_tuple, terminal)
    return GroupComputation(xirr(xirr_flows), dietz_tuple, terminal)


def modified_dietz(
    flows: Sequence[DietzFlow], t0: date, t1: date, bmv: Decimal | None, emv: Decimal | None
) -> DietzResult:
    """Spec "Period return (Modified Dietz)": `C_i = -amount` (money added to the portfolio is
    positive), `w_i = (t1 - d_i) / (t1 - t0)` in whole days (a flow on `t1` has weight 0),
    `dietz = (EMV - BMV - C) / (BMV + W)`, percent at 8 dp half-even. `missing_price` when either
    endpoint is unavailable; `no_denominator` when `BMV + W` is exactly zero.
    """
    if bmv is None or emv is None:
        return DietzResult(None, "missing_price")
    span = (t1 - t0).days
    with localcontext() as ctx:
        ctx.prec = _DIETZ_PRECISION
        total_c = Decimal(0)
        weighted = Decimal(0)
        for flow in flows:
            c = -flow.net_amount_base
            total_c += c
            weight = Decimal((t1 - flow.on).days) / Decimal(span) if span else Decimal(0)
            weighted += c * weight
        denominator = bmv + weighted
        if denominator == 0:
            return DietzResult(None, "no_denominator")
        percent = ((emv - bmv - total_c) / denominator * 100).quantize(
            _PERCENT_QUANT, rounding=ROUND_HALF_EVEN
        )
    return DietzResult(percent, None)


def contribution_lines(
    flows: Sequence[DietzFlow], opening: Decimal | None, closing: Decimal | None
) -> ContributionLines:
    """Spec "Contribution effects" table. Null throughout when either endpoint is unavailable —
    an incomplete window withholds sums rather than reporting a partial one."""
    if opening is None or closing is None:
        return _NULL_LINES
    contributions = Decimal(0)
    withdrawals = Decimal(0)
    income = Decimal(0)
    charges = Decimal(0)
    for flow in flows:
        c = -flow.net_amount_base
        if flow.kind in _CONTRIBUTION_KINDS:
            contributions += c
        elif flow.kind in _WITHDRAWAL_KINDS:
            withdrawals += c
        elif flow.kind in _INCOME_KINDS:
            income += c
        elif flow.kind in _CHARGE_KINDS:
            charges += c
        # Every other kind (no-flow, blocking, unknown) is cash-neutral to these four lines.
    market_and_fx = closing - opening - (contributions + withdrawals + income + charges)
    return ContributionLines(
        opening, closing, contributions, withdrawals, income, charges, market_and_fx
    )


def inception_window(
    computation: GroupComputation, t0: date, as_of: date
) -> tuple[DietzResult, ContributionLines]:
    """Spec "Period return": inception `t0` is the day before the household's earliest active
    event, `BMV = 0` exactly, `EMV` is the group's own live current value — independent of
    whether wealth history has ever been refreshed (this does not read `month_end_position` at
    all). `missing_price`/`incomplete_history`/`blocked_action` invalidate `EMV` itself, since a
    genuinely incomplete or blocked history has no honest current value either; the other XIRR
    reasons (`no_sign_change`, `multiple_roots`, `not_converged`, `missing_terminal_value`) mean
    only the rate is unsolved or moot — `EMV` stays usable (0 when nothing is open).
    """
    if computation.xirr_result.reason in _VALUE_INVALIDATING_XIRR_REASONS:
        emv = None
    else:
        emv = computation.terminal_value if computation.terminal_value is not None else Decimal(0)
    dietz = modified_dietz(computation.dietz_flows, t0, as_of, Decimal(0), emv)
    lines = contribution_lines(computation.dietz_flows, Decimal(0), emv)
    return dietz, lines


@dataclass(frozen=True)
class TargetGroup:
    label: str
    ratio: Decimal


def infer_target(ratios: Mapping[str, Decimal]) -> list[TargetGroup]:
    """Spec "Allocation drift": the first complete month's `group_allocation` ratios, with the
    rounding remainder (`1 - sum`) assigned to the class with the largest ratio (the first label
    in `ASSET_CLASS_ORDER` when tied), so the inferred set always sums to exactly 1. Empty input
    (no complete month yet) infers nothing."""
    if not ratios:
        return []
    total = sum(ratios.values(), Decimal(0))
    remainder = Decimal(1) - total
    max_ratio = max(ratios.values())
    largest = next(label for label in ASSET_CLASS_ORDER if ratios.get(label) == max_ratio)
    adjusted = dict(ratios)
    adjusted[largest] = adjusted[largest] + remainder
    return [TargetGroup(label, adjusted[label]) for label in ASSET_CLASS_ORDER if label in adjusted]


def compute_drift(actual: Decimal | None, target: Decimal | None) -> Decimal | None:
    """Spec "Allocation drift": `actualRatio - targetRatio`, 10 dp, null when actual allocation
    is incomplete or no target exists."""
    if actual is None or target is None:
        return None
    return (actual - target).quantize(_RATIO_QUANT, rounding=ROUND_HALF_EVEN)


def excess_percent(subject: DietzResult, benchmark: DietzResult) -> Decimal | None:
    """Spec "Benchmarks": subject Dietz minus benchmark Dietz, 8 dp, null when either side is
    unavailable."""
    if subject.percent is None or benchmark.percent is None:
        return None
    return (subject.percent - benchmark.percent).quantize(_PERCENT_QUANT, rounding=ROUND_HALF_EVEN)


def target_ratio_for(label: str, targets: Mapping[str, Decimal]) -> Decimal | None:
    """Spec "Allocation drift": "a class that appears only in actuals has target 0" — but only
    once a target (stored or inferred) exists at all; with no target whatsoever every class's
    own target ratio is null, not 0."""
    if not targets:
        return None
    return targets.get(label, Decimal("0.0000000000"))


@dataclass(frozen=True)
class MonthPositionRow:
    """One holdings-mode `month_end_position` row at a specific month end."""

    asset_class: str
    value_base: Decimal | None
    status: str


def _nn(value: Decimal | None) -> Decimal:
    """Asserts a value the caller already knows (from `MonthResult.complete`) cannot be `None`."""
    assert value is not None
    return value


def _sum_available(rows: Sequence[MonthPositionRow]) -> Decimal:
    """Sums `value_base` over rows already known (by the caller checking `status`) to all be
    `available` — every row reaching this point in `compute_month` carries a value."""
    total = Decimal(0)
    for row in rows:
        assert row.value_base is not None
        total += row.value_base
    return total


@dataclass(frozen=True)
class MonthFlowEvent:
    """One active investment event booked in a month window `(t0, t1]`, denormalized with its
    holding's own asset class so contribution lines can be split by class without a second join
    per class."""

    asset_class: str
    kind: str
    on: date
    net_amount_base: Decimal


@dataclass(frozen=True)
class ClassEffect:
    label: str
    dietz: DietzResult
    lines: ContributionLines
    ratio: Decimal | None
    target_ratio: Decimal | None
    drift: Decimal | None
    flows: tuple[DietzFlow, ...] = ()


@dataclass(frozen=True)
class MonthResult:
    month_end: date
    previous_month_end: date
    complete: bool
    status_counts: Mapping[str, int]
    portfolio_dietz: DietzResult
    portfolio_lines: ContributionLines
    portfolio_flows: tuple[DietzFlow, ...]
    classes: tuple[ClassEffect, ...]


def compute_month(
    month_end: date,
    previous_month_end: date,
    opening_rows: Sequence[MonthPositionRow],
    closing_rows: Sequence[MonthPositionRow],
    events: Sequence[MonthFlowEvent],
    status_counts: Mapping[str, int],
    targets: Mapping[str, Decimal],
) -> MonthResult:
    """Spec "Contribution effects"/"Allocation drift": the month-window population is every
    holding with a holdings-mode `month_end_position` row at `t0` or `t1` — `opening_rows` and
    `closing_rows` together are exactly that population's rows at each date (`status_counts` is
    this month's own status breakdown, already computed by the caller from the same rows the
    existing `GET /wealth-history` counts). The month is incomplete once any of those rows is not
    `available` — including a row carried over from an incomplete previous month end, since that
    row IS this month's own `t0` row in `opening_rows`.
    """
    all_rows = (*opening_rows, *closing_rows)
    complete = all(row.status == "available" for row in all_rows)
    all_labels = sorted({row.asset_class for row in all_rows} | set(targets))

    if not complete:
        incomplete_classes = tuple(
            ClassEffect(
                label,
                DietzResult(None, "missing_price"),
                _NULL_LINES,
                None,
                target_ratio_for(label, targets),
                None,
            )
            for label in all_labels
        )
        return MonthResult(
            month_end,
            previous_month_end,
            False,
            dict(status_counts),
            DietzResult(None, "missing_price"),
            _NULL_LINES,
            (),
            incomplete_classes,
        )

    opening_total = _sum_available(opening_rows)
    closing_total = _sum_available(closing_rows)
    portfolio_flows = tuple(
        DietzFlow(event.kind, event.on, event.net_amount_base) for event in events
    )
    portfolio_dietz = modified_dietz(
        portfolio_flows, previous_month_end, month_end, opening_total, closing_total
    )
    portfolio_lines = contribution_lines(portfolio_flows, opening_total, closing_total)

    allocation = group_allocation(
        [
            PositionValue(row.asset_class, "", row.value_base or Decimal(0), True)
            for row in closing_rows
        ]
    )
    ratio_by_class = {group.label: group.ratio for group in allocation.by_asset_class}

    classes: list[ClassEffect] = []
    for label in all_labels:
        class_opening = _sum_available([row for row in opening_rows if row.asset_class == label])
        class_closing = _sum_available([row for row in closing_rows if row.asset_class == label])
        class_flows = tuple(
            DietzFlow(event.kind, event.on, event.net_amount_base)
            for event in events
            if event.asset_class == label
        )
        class_dietz = modified_dietz(
            class_flows, previous_month_end, month_end, class_opening, class_closing
        )
        class_lines = contribution_lines(class_flows, class_opening, class_closing)
        target = target_ratio_for(label, targets)
        actual_ratio = ratio_by_class.get(label, Decimal("0.0000000000"))
        classes.append(
            ClassEffect(
                label,
                class_dietz,
                class_lines,
                actual_ratio,
                target,
                compute_drift(actual_ratio, target),
                class_flows,
            )
        )
    return MonthResult(
        month_end,
        previous_month_end,
        True,
        dict(status_counts),
        portfolio_dietz,
        portfolio_lines,
        portfolio_flows,
        tuple(classes),
    )


@dataclass(frozen=True)
class YearClassResult:
    label: str
    dietz: DietzResult
    lines: ContributionLines


@dataclass(frozen=True)
class YearResult:
    year: int
    incomplete_months: int
    portfolio_dietz: DietzResult
    portfolio_lines: ContributionLines
    classes: tuple[YearClassResult, ...]


def compute_year(year: int, months: Sequence[MonthResult]) -> YearResult:
    """Spec "Contribution effects"/"Allocation drift": "a year sums complete months only; if any
    month in that year is incomplete the year lists `incompleteMonths` and withholds the sums."
    `months` must be exactly this year's own rows from the series, in any order. The year's own
    Dietz is computed fresh over the whole year window (concatenating the months' own flows) —
    not chained from monthly percentages, since Modified Dietz does not compose across
    sub-periods (no TWR linking, spec "Non-goals") — while every contribution line is a literal
    sum of the months' own lines, so the class-sums-to-portfolio identity holds at the year level
    exactly as it does at the month level.
    """
    incomplete = sum(1 for month in months if not month.complete)
    if incomplete or not months:
        return YearResult(year, incomplete, DietzResult(None, "missing_price"), _NULL_LINES, ())

    ordered = sorted(months, key=lambda month: month.month_end)
    t0, t1 = ordered[0].previous_month_end, ordered[-1].month_end
    opening = ordered[0].portfolio_lines.opening
    closing = ordered[-1].portfolio_lines.closing
    assert opening is not None and closing is not None
    flows = [flow for month in ordered for flow in month.portfolio_flows]
    portfolio_dietz = modified_dietz(flows, t0, t1, opening, closing)
    contributions = sum((_nn(month.portfolio_lines.contributions) for month in ordered), Decimal(0))
    withdrawals = sum((_nn(month.portfolio_lines.withdrawals) for month in ordered), Decimal(0))
    income = sum((_nn(month.portfolio_lines.income) for month in ordered), Decimal(0))
    charges = sum((_nn(month.portfolio_lines.charges) for month in ordered), Decimal(0))
    market_and_fx = closing - opening - (contributions + withdrawals + income + charges)
    portfolio_lines = ContributionLines(
        opening, closing, contributions, withdrawals, income, charges, market_and_fx
    )

    first_month, last_month = ordered[0], ordered[-1]
    labels = sorted({effect.label for month in ordered for effect in month.classes})
    class_results: list[YearClassResult] = []
    for label in labels:
        first_effect = next((c for c in first_month.classes if c.label == label), None)
        last_effect = next((c for c in last_month.classes if c.label == label), None)
        class_opening = first_effect.lines.opening if first_effect is not None else Decimal(0)
        class_closing = last_effect.lines.closing if last_effect is not None else Decimal(0)
        assert class_opening is not None and class_closing is not None
        class_flows: list[DietzFlow] = []
        c_contrib = c_withdraw = c_income = c_charges = Decimal(0)
        for month in ordered:
            effect = next((c for c in month.classes if c.label == label), None)
            if effect is None or effect.lines.contributions is None:
                continue
            class_flows.extend(effect.flows)
            c_contrib += effect.lines.contributions
            c_withdraw += effect.lines.withdrawals or Decimal(0)
            c_income += effect.lines.income or Decimal(0)
            c_charges += effect.lines.charges or Decimal(0)
        class_market_and_fx = (
            class_closing - class_opening - (c_contrib + c_withdraw + c_income + c_charges)
        )
        class_dietz = modified_dietz(class_flows, t0, t1, class_opening, class_closing)
        class_lines = ContributionLines(
            class_opening,
            class_closing,
            c_contrib,
            c_withdraw,
            c_income,
            c_charges,
            class_market_and_fx,
        )
        class_results.append(YearClassResult(label, class_dietz, class_lines))

    return YearResult(year, 0, portfolio_dietz, portfolio_lines, tuple(class_results))


__all__ = [
    "ASSET_CLASS_ORDER",
    "ClassEffect",
    "ContributionLines",
    "DietzFlow",
    "DietzReason",
    "DietzResult",
    "GroupComputation",
    "GroupEvent",
    "MonthFlowEvent",
    "MonthPositionRow",
    "MonthResult",
    "TargetGroup",
    "YearClassResult",
    "YearResult",
    "compute_drift",
    "compute_group",
    "compute_month",
    "compute_year",
    "contribution_lines",
    "excess_percent",
    "inception_window",
    "infer_target",
    "modified_dietz",
    "target_ratio_for",
]
