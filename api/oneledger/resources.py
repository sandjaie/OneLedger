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

from .investments.positions import read_positions
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
