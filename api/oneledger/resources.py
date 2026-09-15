"""Phase 3.3 Task 1: the database reads that feed reservable-resource values and goal progress —
account native values (spec "Account value rule"), active reservations and the holdings inflow
query. Every function here takes a cursor and does no writes, so `routes/actions.py` can reuse
them in Task 2 without importing another route module (the Phase 3.2 import-cycle deviation).
`goals.py` holds the pure Decimal math these feed; `routes/goals.py` is the thin glue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg

from .goals import ReservationProgress, goal_totals, reservation_progress
from .investments.positions import current_rate_pair, read_positions
from .money import translate
from .routes.dashboard import VALUATION_PERIOD_FOR_ACCOUNT

ValuationMode = Literal["ledger_balance", "holdings", "snapshot"]
NativeValueReason = Literal["missing_snapshot", "missing_price", "mixed_currency"]
ReservationKind = Literal["amount", "whole_account"]
ReservationPurpose = Literal["goal", "emergency"]

# The five investment_transaction kinds "Goal progress" counts as new money into a holdings
# account, and how -net_amount already signs them (purchases add, sales and redemptions subtract).
_INFLOW_KINDS = ("purchase", "recurring_purchase", "rights", "sale", "redemption")


@dataclass(frozen=True)
class NativeValue:
    value: Decimal | None
    currency: str
    valuationMode: ValuationMode
    valueDate: date | None
    reason: NativeValueReason | None


@dataclass(frozen=True)
class ReservationFact:
    id: UUID
    purpose: ReservationPurpose
    goalId: UUID | None
    accountId: UUID
    kind: ReservationKind
    nativeAmount: Decimal
    currency: str
    reservedOn: date
    rateCurrency: str
    rateAtReservation: Decimal
    releasedAt: datetime | None
    releaseReason: str | None


_ACCOUNTS = f"""
    SELECT a.id, a.currency, a.current_balance, a.current_balance_as_of,
           period.valuation_mode::text, period.effective_from
      FROM account a
      JOIN account_valuation_period period
        ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
     WHERE a.household_id = %s AND a.is_active AND a.account_class = 'asset'
     ORDER BY a.id"""


def account_native_values(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, as_of: date
) -> dict[UUID, NativeValue]:
    """Every active asset account's value in its own currency (spec "Account value rule")."""
    cur.execute(_ACCOUNTS, (as_of, as_of, household_id))
    accounts = cur.fetchall()
    positions_by_account: dict[UUID, list[Any]] = {}
    if any(mode == "holdings" for *_rest, mode, _effective in accounts):
        for position in read_positions(cur, household_id, base, secondary, as_of):
            positions_by_account.setdefault(position.accountId, []).append(position)

    result: dict[UUID, NativeValue] = {}
    for account_id, currency_raw, balance, balance_as_of, mode, effective_from in accounts:
        currency = str(currency_raw).strip()
        if mode == "ledger_balance":
            result[UUID(str(account_id))] = NativeValue(
                value=Decimal(balance),
                currency=currency,
                valuationMode="ledger_balance",
                valueDate=None if balance_as_of is None else balance_as_of.date(),
                reason=None,
            )
        elif mode == "snapshot":
            cur.execute(
                """SELECT as_of_date, value_native FROM valuation_snapshot
                    WHERE household_id = %s AND account_id = %s
                      AND as_of_date >= %s AND as_of_date <= %s
                    ORDER BY as_of_date DESC, created_at DESC, id DESC LIMIT 1""",
                (household_id, account_id, effective_from, as_of),
            )
            snapshot = cur.fetchone()
            result[UUID(str(account_id))] = (
                NativeValue(None, currency, "snapshot", None, "missing_snapshot")
                if snapshot is None
                else NativeValue(Decimal(snapshot[1]), currency, "snapshot", snapshot[0], None)
            )
        else:  # holdings
            held = positions_by_account.get(UUID(str(account_id)), [])
            if any(position.currency != currency for position in held):
                result[UUID(str(account_id))] = NativeValue(
                    None, currency, "holdings", None, "mixed_currency"
                )
            elif any(position.currentValueNative is None for position in held):
                result[UUID(str(account_id))] = NativeValue(
                    None, currency, "holdings", None, "missing_price"
                )
            else:
                total = sum(
                    (Decimal(position.currentValueNative) for position in held),
                    Decimal("0.0000"),
                )
                result[UUID(str(account_id))] = NativeValue(
                    total, currency, "holdings", as_of, None
                )
    return result


_RESERVATIONS = """
    SELECT id, purpose::text, goal_id, account_id, kind::text, native_amount, currency,
           reserved_on, rate_currency, rate_at_reservation, released_at, release_reason
      FROM resource_reservation WHERE household_id = %s"""


def household_reservations(
    cur: psycopg.Cursor[Any], household_id: UUID, *, active_only: bool = False
) -> list[ReservationFact]:
    """Every reservation in the household, or only its active ones."""
    query = _RESERVATIONS + (" AND released_at IS NULL" if active_only else "")
    cur.execute(query, (household_id,))
    return [
        ReservationFact(
            id=UUID(str(row[0])),
            purpose=row[1],
            goalId=None if row[2] is None else UUID(str(row[2])),
            accountId=UUID(str(row[3])),
            kind=row[4],
            nativeAmount=Decimal(row[5]),
            currency=str(row[6]).strip(),
            reservedOn=row[7],
            rateCurrency=str(row[8]).strip(),
            rateAtReservation=Decimal(row[9]),
            releasedAt=row[10],
            releaseReason=row[11],
        )
        for row in cur.fetchall()
    ]


_HOLDINGS_INFLOW = """
    SELECT coalesce(-sum(net_amount), 0.0000) FROM investment_transaction e
     WHERE e.household_id = %s AND e.account_id = %s AND e.event_kind = ANY(%s)
       AND e.event_date > %s AND e.event_date <= %s
       AND NOT EXISTS (
           SELECT FROM investment_transaction c WHERE c.corrects_transaction_id = e.id)"""


def holdings_inflow_native(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID, since: date, as_of: date
) -> Decimal:
    """New money into a holdings-mode account since a whole-account reservation was made (spec
    "Goal progress"): the native -net_amount sum of purchase/recurring_purchase/rights/sale/
    redemption rows dated after `since`, uncorrected — purchases add, sales and redemptions
    subtract, as stored."""
    cur.execute(_HOLDINGS_INFLOW, (household_id, account_id, list(_INFLOW_KINDS), since, as_of))
    row = cur.fetchone()
    assert row is not None
    return Decimal(row[0])


# ---------------------------------------------------------------------------------------
# Phase 3.3 Task 2: the emergency reserve reads `routes/emergency_reserve.py` and
# `routes/actions.py` both need — kept here, not in either route module, for the same reason as
# the reads above.


def _rate_into(
    cur: psycopg.Cursor[Any],
    currency: str,
    target_currency: str,
    base: str,
    secondary: str,
    on: date,
) -> Decimal | None:
    """The rate from `currency` into a reporting currency that is the household base or secondary
    (a goal's or the emergency reserve's own currency); any other currency is unavailable rather
    than guessed, the same rule `routes/goals.py`'s `_goal_rate` applies."""
    pair = current_rate_pair(cur, currency, base, secondary, on)
    leg = {base: pair.to_base, secondary: pair.to_secondary}.get(target_currency)
    return None if leg is None else leg.rate


@dataclass(frozen=True)
class EmergencyReserveSettings:
    targetMonths: Decimal
    baselineMethod: str
    referenceMonths: int
    buckets: list[str]
    manualMonthlyBase: Decimal | None


def read_emergency_reserve_settings(
    cur: psycopg.Cursor[Any], household_id: UUID
) -> EmergencyReserveSettings | None:
    """The household's emergency reserve settings, or `None` when it has never configured one."""
    cur.execute(
        """SELECT target_months, baseline_method::text, reference_months, buckets::text[],
                  manual_monthly_base
             FROM emergency_reserve WHERE household_id = %s""",
        (household_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    target_months, method, reference_months, buckets, manual_base = row
    return EmergencyReserveSettings(
        targetMonths=Decimal(target_months),
        baselineMethod=method,
        referenceMonths=reference_months,
        buckets=list(buckets),
        manualMonthlyBase=None if manual_base is None else Decimal(manual_base),
    )


def eligible_account_ids(cur: psycopg.Cursor[Any], household_id: UUID) -> list[UUID]:
    """Every account the household has marked an emergency resource."""
    cur.execute(
        "SELECT account_id FROM emergency_resource_account WHERE household_id = %s", (household_id,)
    )
    return [UUID(str(row[0])) for row in cur.fetchall()]


_SPENDING = """
    WITH scoped AS (
        SELECT t.amount_base, t.category_id, COALESCE(o.bucket, c.bucket)::text AS effective_bucket
          FROM v_transaction_reportable t
          LEFT JOIN category c ON c.id = t.category_id
          LEFT JOIN category_override o
            ON o.category_id = t.category_id AND o.household_id = t.household_id
         WHERE t.household_id = %(household)s AND t.txn_type <> 'income'
           AND t.booked_date >= %(start)s AND t.booked_date <= %(end)s
    )
    SELECT coalesce(-sum(amount_base) FILTER (WHERE effective_bucket = ANY(%(buckets)s)), 0.0000),
           coalesce(-sum(amount_base) FILTER (WHERE category_id IS NULL), 0.0000)
      FROM scoped"""


def spending_totals(
    cur: psycopg.Cursor[Any], household_id: UUID, start: date, end: date, buckets: list[str]
) -> tuple[Decimal, Decimal]:
    """Bucketed spending and separately reported uncategorised spending over one reference period
    (spec "Baseline"): `-sum(amount_base)` of `v_transaction_reportable` rows, never `income`,
    joined to the household's effective category bucket (`routes/categories.py`'s
    `COALESCE(o.bucket, c.bucket)` rule)."""
    cur.execute(
        _SPENDING, {"household": household_id, "start": start, "end": end, "buckets": buckets}
    )
    row = cur.fetchone()
    assert row is not None
    return Decimal(row[0]), Decimal(row[1])


def eligible_native_and_base(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    native: NativeValue | None,
    active_reservations: list[ReservationFact],
    base: str,
    secondary: str,
    as_of: date,
) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
    """One eligible account's `(goalReserved, emergencyReserved, eligibleNative, eligibleBase)`
    (spec "Resources"): eligible is the value less what a goal has reserved, never below zero;
    `eligibleBase` is `None` only when the account's value is known but its rate is not (spec
    "Coverage" — a missing value is excluded from the total, a missing rate makes it
    unavailable)."""
    active = [r for r in active_reservations if r.accountId == account_id]
    goal_reserved = sum((r.nativeAmount for r in active if r.purpose == "goal"), Decimal("0.0000"))
    emergency_reserved = sum(
        (r.nativeAmount for r in active if r.purpose == "emergency"), Decimal("0.0000")
    )
    value = native.value if native else None
    eligible = None if value is None else max(value - goal_reserved, Decimal("0.0000"))
    if value is None or eligible is None:
        return goal_reserved, emergency_reserved, None, None
    currency = native.currency if native else base
    rate = current_rate_pair(cur, currency, base, secondary, as_of).to_base
    eligible_base = None if rate is None else translate(eligible, rate.rate)
    return goal_reserved, emergency_reserved, eligible, eligible_base


@dataclass(frozen=True)
class GoalShortfall:
    goalId: UUID
    name: str
    targetDate: date
    currency: str
    shortfall: Decimal


def goal_shortfalls(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date, base: str, secondary: str
) -> list[GoalShortfall]:
    """Every active goal whose progress is available and whose shortfall is positive (spec
    `action_list` "goal.<goalId>" items) — the same progress `routes/goals.py`'s `_goal_row`
    computes for the API, reused here through `goals.py`'s pure functions rather than importing
    that route module."""
    cur.execute("SELECT id FROM goal WHERE household_id = %s AND state = 'active'", (household_id,))
    goal_ids = [UUID(str(row[0])) for row in cur.fetchall()]
    if not goal_ids:
        return []
    values = account_native_values(cur, household_id, base, secondary, as_of)
    all_reservations = household_reservations(cur, household_id, active_only=True)
    by_goal: dict[UUID, list[ReservationFact]] = {}
    for reservation in all_reservations:
        if reservation.goalId is not None:
            by_goal.setdefault(reservation.goalId, []).append(reservation)

    results: list[GoalShortfall] = []
    for goal_id in goal_ids:
        cur.execute(
            """SELECT name, target_amount, target_currency, target_date, planned_monthly
                 FROM goal WHERE household_id = %s AND id = %s""",
            (household_id, goal_id),
        )
        row = cur.fetchone()
        assert row is not None
        name, target_amount, target_currency_raw, target_date, planned_monthly = row
        target_currency = str(target_currency_raw).strip()
        progresses: list[ReservationProgress | None] = []
        for reservation in by_goal.get(goal_id, []):
            native = values.get(reservation.accountId)
            native_now = (
                reservation.nativeAmount
                if reservation.kind == "amount"
                else (native.value if native else None)
            )
            rate_now = _rate_into(
                cur, reservation.currency, target_currency, base, secondary, as_of
            )
            if reservation.kind == "amount" or native_now is None or native is None:
                inflow = Decimal("0.0000")
            elif native.valuationMode == "ledger_balance":
                inflow = native_now - reservation.nativeAmount
            elif native.valuationMode == "holdings":
                inflow = holdings_inflow_native(
                    cur, household_id, reservation.accountId, reservation.reservedOn, as_of
                )
            else:  # snapshot
                inflow = Decimal("0.0000")
            progresses.append(
                reservation_progress(
                    native_amount=reservation.nativeAmount,
                    rate_at_reservation=reservation.rateAtReservation,
                    native_now=native_now,
                    rate_now=rate_now,
                    inflow_native=inflow,
                )
            )
        planned = None if planned_monthly is None else Decimal(planned_monthly)
        totals = goal_totals(
            target_amount=Decimal(target_amount),
            planned_monthly=planned,
            target_date=target_date,
            as_of=as_of,
            reservation_progresses=progresses,
            over_reserved=False,  # unused by action_list's shortfall item
        )
        if totals.status == "available" and totals.shortfall is not None and totals.shortfall > 0:
            results.append(
                GoalShortfall(
                    goalId=goal_id,
                    name=name,
                    targetDate=target_date,
                    currency=target_currency,
                    shortfall=totals.shortfall,
                )
            )
    return results
