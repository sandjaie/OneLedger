"""Acquisition-only investment positions from committed facts and dated market evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg
from pydantic import BaseModel

from ..money import _fixed, _out

_MONEY = Decimal("0.0001")


@dataclass(frozen=True)
class PositionCalculation:
    current_value_native: Decimal | None
    current_value_base: Decimal | None
    current_value_secondary: Decimal | None
    gain_native: Decimal | None
    gain_base: Decimal | None
    gain_secondary: Decimal | None


@dataclass(frozen=True)
class CurrentRate:
    rate: Decimal
    real_date: date
    carried: bool


@dataclass(frozen=True)
class CurrentRatePair:
    to_base: CurrentRate | None
    to_secondary: CurrentRate | None


class PositionFxRate(BaseModel):
    reportingCurrency: str
    rate: str | None
    rateDate: date | None
    carried: bool


class AcquisitionPosition(BaseModel):
    holdingId: UUID
    accountId: UUID
    securityId: UUID
    securityName: str
    assetClass: str
    currency: str
    asOf: date
    units: str
    cumulativeContributionsNative: str
    cumulativeContributionsBase: str
    cumulativeContributionsSecondary: str
    priceStatus: Literal["current", "stale", "missing", "future_price_only"]
    price: str | None
    priceDate: date | None
    currentValueNative: str | None
    currentValueBase: str | None
    currentValueSecondary: str | None
    gainNative: str | None
    gainBase: str | None
    gainSecondary: str | None
    missingFx: list[str]
    fxRates: list[PositionFxRate]


class PortfolioPositions(BaseModel):
    asOf: date
    baseCurrency: str
    secondaryCurrency: str
    positions: list[AcquisitionPosition]


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def calculate_position(
    *,
    units: Decimal,
    contribution_native: Decimal,
    contribution_base: Decimal,
    contribution_secondary: Decimal,
    price: Decimal | None,
    rate_to_base: Decimal | None,
    rate_to_secondary: Decimal | None,
) -> PositionCalculation:
    """Value current units while retaining each acquisition's frozen reporting contribution."""
    if price is None:
        return PositionCalculation(None, None, None, None, None, None)
    native = _money(units * price)
    base = _money(native * rate_to_base) if rate_to_base is not None else None
    secondary = _money(native * rate_to_secondary) if rate_to_secondary is not None else None
    return PositionCalculation(
        current_value_native=native,
        current_value_base=base,
        current_value_secondary=secondary,
        gain_native=_money(native - contribution_native),
        gain_base=None if base is None else _money(base - contribution_base),
        gain_secondary=(None if secondary is None else _money(secondary - contribution_secondary)),
    )


_RATE = """SELECT rate, rate_date, is_carried_forward, carried_from FROM fx_rate
            WHERE base_currency = %s AND quote_currency = %s AND rate_date <= %s
            ORDER BY rate_date DESC LIMIT 1"""


def _rate(cur: psycopg.Cursor[Any], currency: str, base: str, as_of: date) -> CurrentRate | None:
    if currency == base:
        return CurrentRate(Decimal(1), as_of, False)
    cur.execute(_RATE, (currency, base, as_of))
    row = cur.fetchone()
    if row is None:
        return None
    rate, row_date, is_carried, carried_from = row
    real_date = carried_from if is_carried else row_date
    return CurrentRate(Decimal(rate), real_date, bool(is_carried) or row_date < as_of)


def current_rate_pair(
    cur: psycopg.Cursor[Any], currency: str, base: str, secondary: str, as_of: date
) -> CurrentRatePair:
    """Select current as-of reporting legs without inventing an unavailable cross-rate."""
    to_base = _rate(cur, currency, base, as_of)
    if currency == secondary:
        to_secondary = CurrentRate(Decimal(1), as_of, False)
    else:
        secondary_to_base = _rate(cur, secondary, base, as_of)
        if to_base is None or secondary_to_base is None:
            to_secondary = None
        else:
            to_secondary = CurrentRate(
                _fixed(to_base.rate / secondary_to_base.rate),
                min(to_base.real_date, secondary_to_base.real_date),
                to_base.carried or secondary_to_base.carried,
            )
    return CurrentRatePair(to_base, to_secondary)


_POSITIONS = """
    WITH active_events AS (
        SELECT event.holding_id, event.net_amount, event.net_amount_base,
               event.net_amount_secondary, effect.signed_units
          FROM investment_transaction event
          JOIN investment_unit_effect effect
            ON effect.household_id = event.household_id
           AND effect.investment_transaction_id = event.id
         WHERE event.household_id = %(household)s
           AND event.event_date <= %(as_of)s
           AND event.event_kind IN ('purchase', 'recurring_purchase')
           AND NOT EXISTS (
               SELECT FROM investment_transaction correction
                WHERE correction.corrects_transaction_id = event.id)
    ), totals AS (
        SELECT holding_id,
               sum(signed_units)::numeric(28,8) AS units,
               -sum(net_amount)::numeric(20,4) AS contribution_native,
               -sum(net_amount_base)::numeric(20,4) AS contribution_base,
               -sum(net_amount_secondary)::numeric(20,4) AS contribution_secondary
          FROM active_events GROUP BY holding_id
    )
    SELECT h.id, h.account_id, h.security_id, security.name, security.asset_class::text,
           security.currency, totals.units, totals.contribution_native,
           totals.contribution_base, totals.contribution_secondary,
           accepted.price, accepted.price_date,
           EXISTS (SELECT FROM price_snapshot future
                    WHERE future.security_id = h.security_id
                      AND future.is_accepted AND future.price_date > %(as_of)s)
      FROM totals
      JOIN holding h ON h.household_id = %(household)s AND h.id = totals.holding_id
      JOIN account a ON a.household_id = h.household_id AND a.id = h.account_id
      JOIN security ON security.id = h.security_id
      JOIN account_valuation_period period
        ON period.household_id = a.household_id AND period.account_id = a.id
       AND period.valuation_mode = 'holdings'
       AND %(as_of)s >= period.effective_from
       AND (period.effective_to IS NULL OR %(as_of)s <= period.effective_to)
      LEFT JOIN LATERAL (
          SELECT price, price_date FROM price_snapshot snapshot
           WHERE snapshot.security_id = h.security_id AND snapshot.is_accepted
             AND snapshot.price_date <= %(as_of)s
           ORDER BY snapshot.price_date DESC, snapshot.observed_at DESC, snapshot.id DESC
           LIMIT 1
      ) accepted ON true
     WHERE totals.units > 0 AND a.is_active
       AND (NOT %(included_only)s OR a.include_in_net_worth)
     ORDER BY security.name, h.id
"""


def read_positions(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    base: str,
    secondary: str,
    as_of: date,
    *,
    included_only: bool = False,
) -> list[AcquisitionPosition]:
    """Build open acquisition positions from active event leaves and accepted dated prices."""
    cur.execute(
        _POSITIONS,
        {"household": household_id, "as_of": as_of, "included_only": included_only},
    )
    positions: list[AcquisitionPosition] = []
    rate_cache: dict[str, CurrentRatePair] = {}
    for row in cur.fetchall():
        currency = str(row[5]).strip()
        rates = rate_cache.get(currency)
        if rates is None:
            rates = current_rate_pair(cur, currency, base, secondary, as_of)
            rate_cache[currency] = rates
        price = None if row[10] is None else Decimal(row[10])
        price_date = row[11]
        calculation = calculate_position(
            units=Decimal(row[6]),
            contribution_native=Decimal(row[7]),
            contribution_base=Decimal(row[8]),
            contribution_secondary=Decimal(row[9]),
            price=price,
            rate_to_base=None if rates.to_base is None else rates.to_base.rate,
            rate_to_secondary=None if rates.to_secondary is None else rates.to_secondary.rate,
        )
        status: Literal["current", "stale", "missing", "future_price_only"]
        if price_date == as_of:
            status = "current"
        elif price_date is not None:
            status = "stale"
        elif row[12]:
            status = "future_price_only"
        else:
            status = "missing"
        missing_fx = [
            reporting
            for reporting, rate in ((base, rates.to_base), (secondary, rates.to_secondary))
            if rate is None
        ]
        positions.append(
            AcquisitionPosition(
                holdingId=row[0],
                accountId=row[1],
                securityId=row[2],
                securityName=row[3],
                assetClass=row[4],
                currency=currency,
                asOf=as_of,
                units=str(row[6]),
                cumulativeContributionsNative=str(row[7]),
                cumulativeContributionsBase=str(row[8]),
                cumulativeContributionsSecondary=str(row[9]),
                priceStatus=status,
                price=_out(price),
                priceDate=price_date,
                currentValueNative=_out(calculation.current_value_native),
                currentValueBase=_out(calculation.current_value_base),
                currentValueSecondary=_out(calculation.current_value_secondary),
                gainNative=_out(calculation.gain_native),
                gainBase=_out(calculation.gain_base),
                gainSecondary=_out(calculation.gain_secondary),
                missingFx=missing_fx,
                fxRates=[
                    PositionFxRate(
                        reportingCurrency=reporting,
                        rate=_out(None if rate is None else rate.rate),
                        rateDate=None if rate is None else rate.real_date,
                        carried=False if rate is None else rate.carried,
                    )
                    for reporting, rate in ((base, rates.to_base), (secondary, rates.to_secondary))
                ],
            )
        )
    return positions
