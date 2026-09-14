"""Phase 2.2 Task 1: month-end positions, the stored evidence behind wealth history (spec
"Month-end positions"). `refresh` recomputes every counted account's — and every counted
holdings-mode account's holding's — value at 31 December 2023 (the opening point for January
2024) and every month end from `coverage.HISTORY_ANCHOR` through the last completed month, and
replaces a household's rows in one transaction: nothing is deleted or written until the whole
refresh has succeeded in Python, so a late failure leaves the previous refresh's rows exactly as
they were.

`investments.positions.read_positions` and `current_rate_pair` already do the valuation and FX
work the dashboard and portfolio routes use; this module's own job is choosing which account
counts at which date — the spec's own lifetime predicate, `include_in_net_worth AND (opened_on
IS NULL OR opened_on <= t) AND (closed_on IS NULL OR closed_on >= t)`, not the dashboard's
`is_active` toggle, so a closed account keeps its history — and storing what they compute.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from pydantic import BaseModel

from .coverage import HISTORY_ANCHOR, month_end, months_from_anchor
from .investments.positions import current_rate_pair, read_positions
from .money import translate
from .routes.dashboard import VALUATION_PERIOD_FOR_ACCOUNT

# `read_positions` is re-exported so the live test can monkeypatch this module's own bound name
# to prove a late refresh failure leaves the previous rows intact.
__all__ = ["HISTORY_ANCHOR", "RefreshCounts", "read_positions", "refresh"]

# Wealth history's own counted-account predicate (spec "Month-end positions"): three `%s`
# placeholders in order — household_id, then the as-of date twice.
_COUNTED = """a.household_id = %s AND a.include_in_net_worth
              AND (a.opened_on IS NULL OR a.opened_on <= %s)
              AND (a.closed_on IS NULL OR a.closed_on >= %s)"""


@dataclass(frozen=True)
class _Row:
    account_id: UUID
    holding_id: UUID | None
    valuation_mode: str
    currency: str
    native_value: Decimal | None
    evidence_date: date | None
    rate_to_base: Decimal | None
    rate_to_base_date: date | None
    rate_to_base_carried: bool | None
    rate_to_secondary: Decimal | None
    rate_to_secondary_date: date | None
    rate_to_secondary_carried: bool | None
    value_base: Decimal | None
    value_secondary: Decimal | None
    status: str


class RefreshCounts(BaseModel):
    monthsComputed: int
    positionsComputed: int
    byStatus: dict[str, int]


def _last_completed_month_end(through: date) -> date:
    """The last calendar month fully over as of `through`: that month itself when `through` is
    its own last day, else the month before `through`'s."""
    first_of_this_month = date(through.year, through.month, 1)
    if through == month_end(first_of_this_month):
        return through
    prior_month_last_day = first_of_this_month - timedelta(days=1)
    return month_end(date(prior_month_last_day.year, prior_month_last_day.month, 1))


_Translated = tuple[
    Decimal | None,
    date | None,
    bool | None,
    Decimal | None,
    date | None,
    bool | None,
    Decimal | None,
    Decimal | None,
    str,
]


def _translate_native(
    cur: psycopg.Cursor[Any], native: Decimal, currency: str, base: str, secondary: str, t: date
) -> _Translated:
    """`current_rate_pair` at `t`, applied to one known native value. Either leg missing nulls
    both reporting values (the table's own `month_end_position_values_agree` CHECK), but the
    rate columns still carry whichever leg — base, secondary, or neither — is actually known."""
    pair = current_rate_pair(cur, currency, base, secondary, t)
    to_base, to_secondary = pair.to_base, pair.to_secondary
    if to_base is None or to_secondary is None:
        return (
            None if to_base is None else to_base.rate,
            None if to_base is None else to_base.real_date,
            None if to_base is None else to_base.carried,
            None if to_secondary is None else to_secondary.rate,
            None if to_secondary is None else to_secondary.real_date,
            None if to_secondary is None else to_secondary.carried,
            None,
            None,
            "missing_rate",
        )
    return (
        to_base.rate,
        to_base.real_date,
        to_base.carried,
        to_secondary.rate,
        to_secondary.real_date,
        to_secondary.carried,
        translate(native, to_base.rate),
        translate(native, to_secondary.rate),
        "available",
    )


_LEDGER_ACCOUNTS = f"""
    SELECT a.id, a.currency, a.opening_balance, a.opening_balance_date,
           flow.last_booked, flow.flow_sum
      FROM account a
      JOIN account_valuation_period period ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
      LEFT JOIN LATERAL (
          SELECT max(p.booked_date) AS last_booked, coalesce(sum(p.amount), 0) AS flow_sum
            FROM v_transaction_posting p
           WHERE p.account_id = a.id AND p.booked_date <= %s
      ) flow ON true
     WHERE {_COUNTED} AND period.valuation_mode = 'ledger_balance'
     ORDER BY a.id
"""

_SNAPSHOT_ACCOUNTS = f"""
    SELECT a.id, a.currency, period.effective_from
      FROM account a
      JOIN account_valuation_period period ON period.id = {VALUATION_PERIOD_FOR_ACCOUNT}
     WHERE {_COUNTED} AND period.valuation_mode = 'snapshot'
     ORDER BY a.id
"""

_LATEST_SNAPSHOT = """
    SELECT as_of_date, value_native FROM valuation_snapshot
     WHERE household_id = %s AND account_id = %s AND as_of_date >= %s AND as_of_date <= %s
     ORDER BY as_of_date DESC, created_at DESC, id DESC LIMIT 1
"""


def _ledger_rows(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, t: date
) -> list[_Row]:
    # Text order of `%s` placeholders: the LATERAL flow subquery's one date, then
    # VALUATION_PERIOD_FOR_ACCOUNT's two dates, then _COUNTED's household id and two dates.
    cur.execute(_LEDGER_ACCOUNTS, (t, t, t, household_id, t, t))
    rows: list[_Row] = []
    for (
        account_id,
        currency_raw,
        opening_balance,
        opening_balance_date,
        last_booked,
        flow_sum,
    ) in cur.fetchall():
        currency = str(currency_raw).strip()
        if t < opening_balance_date:
            rows.append(
                _Row(
                    account_id,
                    None,
                    "ledger_balance",
                    currency,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "before_opening",
                )
            )
            continue
        # The same figure as V14's assert_account_reconciles and the V10 balance trigger:
        # opening_balance plus posted rows on or before t, including one booked on the
        # opening date itself.
        native = Decimal(opening_balance) + Decimal(flow_sum)
        evidence_date = last_booked or opening_balance_date
        translated = _translate_native(cur, native, currency, base, secondary, t)
        rows.append(
            _Row(account_id, None, "ledger_balance", currency, native, evidence_date, *translated)
        )
    return rows


def _snapshot_rows(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, t: date
) -> list[_Row]:
    cur.execute(_SNAPSHOT_ACCOUNTS, (t, t, household_id, t, t))
    accounts = cur.fetchall()
    rows: list[_Row] = []
    for account_id, currency_raw, effective_from in accounts:
        currency = str(currency_raw).strip()
        cur.execute(_LATEST_SNAPSHOT, (household_id, account_id, effective_from, t))
        found = cur.fetchone()
        if found is None:
            rows.append(
                _Row(
                    account_id,
                    None,
                    "snapshot",
                    currency,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "no_snapshot",
                )
            )
            continue
        evidence_date, native_raw = found
        native = Decimal(native_raw)
        # Translated at the month-end rate, not the snapshot's own frozen fx_rate_to_base — it
        # can therefore differ from the dashboard's frozen snapshot-date figure for the same day
        # (spec "Month-end positions").
        translated = _translate_native(cur, native, currency, base, secondary, t)
        rows.append(
            _Row(account_id, None, "snapshot", currency, native, evidence_date, *translated)
        )
    return rows


def _holding_rows(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, t: date
) -> list[_Row]:
    rows: list[_Row] = []
    for position in read_positions(
        cur,
        household_id,
        base,
        secondary,
        t,
        included_only=True,
        include_closed=True,
        lifetime=True,
    ):
        if position.valueStatus != "available":
            # Covers both `missing_price` and the rare `outside_supported_range`: neither
            # leaves a native value this table can store (ponytail: the schema names one
            # unavailable-price status, not two).
            rows.append(
                _Row(
                    position.accountId,
                    position.holdingId,
                    "holdings",
                    position.currency,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    "missing_price",
                )
            )
            continue
        assert position.currentValueNative is not None
        native = Decimal(position.currentValueNative)
        rate_by_currency = {leg.reportingCurrency: leg for leg in position.fxRates}
        base_leg, secondary_leg = rate_by_currency[base], rate_by_currency[secondary]
        value_base = (
            None if position.currentValueBase is None else Decimal(position.currentValueBase)
        )
        value_secondary = (
            None
            if position.currentValueSecondary is None
            else Decimal(position.currentValueSecondary)
        )
        both_known = value_base is not None and value_secondary is not None
        status = "available" if both_known else "missing_rate"
        if status == "missing_rate":
            value_base = value_secondary = None
        rows.append(
            _Row(
                position.accountId,
                position.holdingId,
                "holdings",
                position.currency,
                native,
                position.priceDate,
                None if base_leg.rate is None else Decimal(base_leg.rate),
                base_leg.rateDate if base_leg.rate is not None else None,
                base_leg.carried if base_leg.rate is not None else None,
                None if secondary_leg.rate is None else Decimal(secondary_leg.rate),
                secondary_leg.rateDate if secondary_leg.rate is not None else None,
                secondary_leg.carried if secondary_leg.rate is not None else None,
                value_base,
                value_secondary,
                status,
            )
        )
    return rows


def _positions_for_month(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, t: date
) -> list[_Row]:
    return [
        *_ledger_rows(cur, household_id, base, secondary, t),
        *_snapshot_rows(cur, household_id, base, secondary, t),
        *_holding_rows(cur, household_id, base, secondary, t),
    ]


_INSERT = """
    INSERT INTO month_end_position (
        household_id, month_end, account_id, holding_id, valuation_mode, currency, native_value,
        evidence_date, rate_to_base, rate_to_base_date, rate_to_base_carried, rate_to_secondary,
        rate_to_secondary_date, rate_to_secondary_carried, value_base, value_secondary, status)
    VALUES (%s, %s, %s, %s, %s::account_valuation_mode, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s::month_end_position_status)
"""


def refresh(
    cur: psycopg.Cursor[Any], household_id: UUID, base: str, secondary: str, through: date
) -> RefreshCounts:
    """Recompute every month end from 31 December 2023 through the last month fully completed
    as of `through`, and replace this household's rows in one transaction. Nothing is deleted or
    written until every month has been computed and inserted here in Python, so an exception
    partway through — a late failure — propagates to the caller's connection, which rolls back
    the whole transaction and leaves the previous refresh's rows exactly as they were.
    """
    # The household lock is this ledger's serialization point for a write that reads and stores
    # a consistent snapshot of many other tables (spec B3.5 / `commit_import_batch`'s own use).
    cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (household_id,))
    last_completed = _last_completed_month_end(through)
    month_ends = [date(2023, 12, 31), *(month_end(m) for m in months_from_anchor(last_completed))]
    cur.execute("DELETE FROM month_end_position WHERE household_id = %s", (household_id,))
    by_status: Counter[str] = Counter()
    total = 0
    for t in month_ends:
        for row in _positions_for_month(cur, household_id, base, secondary, t):
            by_status[row.status] += 1
            total += 1
            cur.execute(
                _INSERT,
                (
                    household_id,
                    t,
                    row.account_id,
                    row.holding_id,
                    row.valuation_mode,
                    row.currency,
                    row.native_value,
                    row.evidence_date,
                    row.rate_to_base,
                    row.rate_to_base_date,
                    row.rate_to_base_carried,
                    row.rate_to_secondary,
                    row.rate_to_secondary_date,
                    row.rate_to_secondary_carried,
                    row.value_base,
                    row.value_secondary,
                    row.status,
                ),
            )
    return RefreshCounts(
        monthsComputed=len(month_ends), positionsComputed=total, byStatus=dict(by_status)
    )
