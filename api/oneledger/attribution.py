"""Phase 2.2 Task 2: the monthly attribution waterfall (spec "Attribution for one month").

Pure `Decimal` arithmetic over one currency's worth of typed inputs — two months of stored
positions, the window's frozen ledger flows and holding funding events, and the closing rates
needed to translate them. No database access, so the spec's own rules (first-match flow
classification, the FX bridge on flows and holding funding, snapshot revaluation, a position
absent at one end valued at zero) are cheap to prove directly against dated fixtures
(`tests/test_attribution.py`). `routes/wealth_history.py` builds these inputs from stored
`month_end_position` rows and `v_transaction_posting`/`investment_transaction`, once per
reporting currency.

This module multiplies a flow's native amount by the closing rate on purpose — the FX bridge the
spec requires (moving FX on intra-period flows out of their own line and into FX translation) —
so it is in `scripts/check-identifiers.py`'s `RATE_MULTIPLIERS` allowlist for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

PositionKind = Literal["ledger_balance", "holdings", "snapshot"]

_CENT = Decimal("0.0001")
_ZERO = Decimal("0.0000")

# Spec "Attribution for one month", ledger flow first-match order, step 4.
_INTERNAL_TXN_TYPES = frozenset(
    {
        "transfer_in",
        "transfer_out",
        "investment_buy",
        "investment_sell",
        "investment_fee",
        "loan_disbursement",
        "loan_repayment",
    }
)
# Step 5.
_OTHER_EXTERNAL_TXN_TYPES = frozenset({"opening_balance", "adjustment"})


def _round4(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Position:
    """One counted account or holding, matched by `key` across `t0` and `t1`. A position absent
    at one end carries `Decimal("0.0000")` for both its native and translated value there (spec:
    "A position absent at one end has V_nat = 0 at that end")."""

    key: str
    kind: PositionKind
    currency: str
    opening_native: Decimal
    opening_value: Decimal
    closing_native: Decimal
    closing_value: Decimal


@dataclass(frozen=True)
class Flow:
    """One `v_transaction_posting` row on a counted ledger-mode account, booked in `(t0, t1]`.
    `position_key` matches the ledger `Position.key` it posted against. `frozen_amount` is the
    row's own frozen reporting amount for the currency this attribution is being computed in
    (`amount_base` or `amount_secondary`)."""

    position_key: str
    currency: str
    native_amount: Decimal
    frozen_amount: Decimal
    is_reportable: bool
    is_excluded: bool
    txn_type: str


@dataclass(frozen=True)
class HoldingFunding:
    """One active investment event in the window whose kind moves cash with a holding (purchase,
    recurring_purchase, rights, sale, redemption, maturity). `position_key` matches the holding
    `Position.key`. `frozen_net` is `net_amount_base`/`net_amount_secondary` for the currency this
    attribution is being computed in."""

    position_key: str
    currency: str
    native_net: Decimal
    frozen_net: Decimal


@dataclass(frozen=True)
class Attribution:
    available: bool
    reasons: tuple[str, ...]
    opening: Decimal | None
    closing: Decimal | None
    actual_change: Decimal | None
    retained_income: Decimal | None
    investment_income: Decimal | None
    internal_movements_and_costs: Decimal | None
    other_external_changes: Decimal | None
    market_movement: Decimal | None
    asset_revaluation: Decimal | None
    fx_translation: Decimal | None
    residual: Decimal | None


def _unavailable(reasons: list[str]) -> Attribution:
    return Attribution(
        available=False,
        reasons=tuple(reasons),
        opening=None,
        closing=None,
        actual_change=None,
        retained_income=None,
        investment_income=None,
        internal_movements_and_costs=None,
        other_external_changes=None,
        market_movement=None,
        asset_revaluation=None,
        fx_translation=None,
        residual=None,
    )


_FlowLine = Literal["retained_income", "investment_income", "internal", "other_external"]


def _classify_flow(flow: Flow) -> _FlowLine:
    """First-match line for one ledger flow (spec "Attribution for one month", "Ledger positions").

    `is_reportable` already carries the reportable view's own predicate (transfer leg, excluded,
    and non-flow `txn_type`s all excluded from it), so this only needs to add the reportable
    view's own exclusions back in, in the spec's order.
    """
    if flow.is_reportable:
        return "retained_income"
    if flow.is_excluded:
        return "other_external"
    if flow.txn_type == "investment_income":
        return "investment_income"
    if flow.txn_type in _INTERNAL_TXN_TYPES:
        return "internal"
    # opening_balance, adjustment, or (defensively) anything else reaching this branch.
    return "other_external"


def attribute(
    *,
    positions: list[Position],
    flows: list[Flow],
    holding_funding: list[HoldingFunding],
    closing_rates: dict[str, Decimal],
    unavailable_reasons: list[str] | None = None,
) -> Attribution:
    """One currency's waterfall for one month: opening, closing, actual change, the named
    components and the residual, or `unavailable` naming why. `closing_rates` maps a currency to
    `r1`, the closing rate to the currency this attribution is being computed in; a currency held
    or flowing in the window with no entry there makes the month unavailable (spec: "a missing r1
    for any held or flowing currency makes the month unavailable"). `unavailable_reasons` carries
    reasons the caller already knows (an incomplete month end at `t0` or `t1`) and short-circuits
    before any arithmetic, since incomplete stored positions cannot be trusted as inputs.
    """
    reasons = list(unavailable_reasons or [])
    if reasons:
        return _unavailable(reasons)

    needed_currencies = (
        {p.currency for p in positions}
        | {f.currency for f in flows}
        | {h.currency for h in holding_funding}
    )
    missing = sorted(c for c in needed_currencies if c not in closing_rates)
    if missing:
        return _unavailable([f"no closing rate for {c}" for c in missing])

    opening_total = sum((p.opening_value for p in positions), _ZERO)
    closing_total = sum((p.closing_value for p in positions), _ZERO)
    actual_change = closing_total - opening_total

    retained_income = investment_income = internal = other_external = _ZERO
    market_movement = asset_revaluation = fx_translation = _ZERO

    flows_by_position: dict[str, list[Flow]] = {}
    for flow in flows:
        flows_by_position.setdefault(flow.position_key, []).append(flow)
        r1 = closing_rates[flow.currency]
        line = _classify_flow(flow)
        if line == "retained_income":
            retained_income += flow.frozen_amount
        elif line == "investment_income":
            investment_income += flow.frozen_amount
        elif line == "internal":
            internal += flow.frozen_amount
        else:
            other_external += flow.frozen_amount
        # The FX bridge: a flow's frozen figure came from its own booked-date rate; the gap to
        # today's closing rate is FX translation, not the flow's own line (spec "FX translation").
        fx_translation += _round4(flow.native_amount * r1) - flow.frozen_amount

    funding_by_position: dict[str, list[HoldingFunding]] = {}
    for funding in holding_funding:
        funding_by_position.setdefault(funding.position_key, []).append(funding)
        r1 = closing_rates[funding.currency]
        internal += -funding.frozen_net
        # Same negated sign as F_nat and the internal-movements line just above: this position's
        # own lines look at funding from the holding's side (cash arriving), so the FX bridge
        # must bridge that same signed figure, not the raw net_amount — otherwise a funding
        # event's frozen rate differing from r1 leaves a spurious residual on this position.
        fx_translation += funding.frozen_net - _round4(funding.native_net * r1)

    for position in positions:
        r1 = closing_rates[position.currency]
        if position.kind == "ledger_balance":
            flow_native_sum = sum(
                (f.native_amount for f in flows_by_position.get(position.key, [])), _ZERO
            )
            unexplained = position.closing_native - position.opening_native - flow_native_sum
            other_external += _round4(unexplained * r1)
        elif position.kind == "holdings":
            funding_native_sum = sum(
                (-f.native_net for f in funding_by_position.get(position.key, [])), _ZERO
            )
            unexplained = position.closing_native - position.opening_native - funding_native_sum
            market_movement += _round4(unexplained * r1)
        else:
            asset_revaluation += _round4((position.closing_native - position.opening_native) * r1)
        if position.opening_native != 0:
            fx_translation += _round4(position.opening_native * r1) - position.opening_value

    total_lines = (
        retained_income
        + investment_income
        + internal
        + other_external
        + market_movement
        + asset_revaluation
        + fx_translation
    )
    residual = actual_change - total_lines

    return Attribution(
        available=True,
        reasons=(),
        opening=opening_total,
        closing=closing_total,
        actual_change=actual_change,
        retained_income=retained_income,
        investment_income=investment_income,
        internal_movements_and_costs=internal,
        other_external_changes=other_external,
        market_movement=market_movement,
        asset_revaluation=asset_revaluation,
        fx_translation=fx_translation,
        residual=residual,
    )
