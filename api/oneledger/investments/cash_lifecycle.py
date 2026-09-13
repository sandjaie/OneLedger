"""Owner-authorized exact disposal and cash lifecycle event operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from typing import Any
from uuid import UUID

import psycopg

from ..auth import OwnerSession
from ..db import connect, scope_to_owner
from ..money import _frozen, translate
from .commands import InvestmentCommandError, _screen_correction_reason, _translate_database_error
from .schemas import (
    AcquisitionLotRow,
    CashLifecycleCorrectionCreate,
    CashLifecycleCreate,
    CashLifecycleEventRow,
)

_SOURCE = "owner_cash_lifecycle_v1"
_CORRECTION_SOURCE = "owner_cash_lifecycle_correction_v1"
_DISPOSALS = frozenset({"sale", "redemption", "maturity"})
FrozenFlow = tuple[
    date | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
]


@dataclass(frozen=True)
class CashLifecycleResult:
    event: CashLifecycleEventRow
    created: bool


def _existing_key(
    cur: psycopg.Cursor[Any], household_id: UUID, source_contract: str, key: UUID
) -> UUID | None:
    cur.execute(
        """SELECT id FROM investment_transaction
             WHERE household_id = %s AND source_contract = %s AND source_event_id = %s""",
        (household_id, source_contract, str(key)),
    )
    row = cur.fetchone()
    return None if row is None else UUID(str(row[0]))


def _reporting_pair(cur: psycopg.Cursor[Any], household_id: UUID) -> tuple[str, str]:
    cur.execute(
        """SELECT base_currency, secondary_currency FROM household
             WHERE id = %s FOR UPDATE""",
        (household_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise InvestmentCommandError(404, "household not found")
    return str(row[0]).strip(), str(row[1]).strip()


def _derive_frozen_flow(
    cur: psycopg.Cursor[Any], body: CashLifecycleCreate, reporting_pair: tuple[str, str]
) -> FrozenFlow:
    if Decimal(body.netAmount) == 0:
        return None, None, None, None, None
    try:
        rate_to_base, rate_to_secondary, _carried, rate_date = _frozen(
            cur, body.eventDate, body.currency, *reporting_pair
        )
        native_amount = Decimal(body.netAmount)
        return (
            rate_date,
            rate_to_base,
            rate_to_secondary,
            translate(native_amount, rate_to_base),
            translate(native_amount, rate_to_secondary),
        )
    except DecimalException:
        raise InvestmentCommandError(
            422, "investment reporting amount exceeds the exact range"
        ) from None


def _stored_frozen_flow(cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID) -> FrozenFlow:
    cur.execute(
        """SELECT fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                  net_amount_base, net_amount_secondary
             FROM investment_transaction WHERE household_id = %s AND id = %s""",
        (household_id, event_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("idempotent investment event is missing")
    return (
        row[0],
        None if row[1] is None else Decimal(row[1]),
        None if row[2] is None else Decimal(row[2]),
        None if row[3] is None else Decimal(row[3]),
        None if row[4] is None else Decimal(row[4]),
    )


def _append(
    cur: psycopg.Cursor[Any],
    body: CashLifecycleCreate,
    source_contract: str,
    corrects_event_id: UUID | None,
    correction_reason: str | None,
    frozen_flow: FrozenFlow,
) -> UUID:
    fx_date, rate_base, rate_secondary, amount_base, amount_secondary = frozen_flow
    cur.execute(
        """SELECT append_investment_transaction(
             %s, %s, %s, %s::investment_event_kind,
             %s, %s, %s, %s, %s, %s, %s,
             %s, %s, %s, %s, %s,
             'owner_entered'::investment_event_source, %s, %s,
             NULL, NULL, %s, %s)""",
        (
            body.accountId,
            body.securityId,
            body.eventDate,
            body.eventKind,
            None if body.units is None else Decimal(body.units),
            None if body.price is None else Decimal(body.price),
            None if body.grossAmount is None else Decimal(body.grossAmount),
            Decimal(body.charges),
            Decimal(body.withheldTax),
            Decimal(body.netAmount),
            body.currency,
            fx_date,
            rate_base,
            rate_secondary,
            amount_base,
            amount_secondary,
            source_contract,
            str(body.idempotencyKey),
            corrects_event_id,
            correction_reason,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment append returned no event")
    return UUID(str(row[0]))


def _ensure_disposal_review(
    cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID, event_kind: str
) -> None:
    if event_kind not in _DISPOSALS:
        return
    cur.execute(
        """INSERT INTO investment_review_item (
             household_id, investment_transaction_id, holding_id, reason)
           SELECT event.household_id, event.id, event.holding_id,
                  'incomplete_lot_allocation'::investment_review_reason
             FROM investment_transaction event
            WHERE event.household_id = %s AND event.id = %s
           ON CONFLICT DO NOTHING""",
        (household_id, event_id),
    )


def _fixed(value: Decimal | int | str | None, places: int) -> str | None:
    return None if value is None else f"{Decimal(value):.{places}f}"


def _required_fixed(value: Decimal | int | str, places: int) -> str:
    return f"{Decimal(value):.{places}f}"


def _load_event(
    cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID
) -> CashLifecycleEventRow:
    cur.execute(
        """SELECT event.id, event.holding_id, event.account_id, event.security_id,
                  event.event_date, event.event_kind::text, event.units, event.price,
                  event.gross_amount, event.charges, event.withheld_tax, event.net_amount,
                  rtrim(event.currency), event.fx_rate_date, event.fx_rate_to_base,
                  event.fx_rate_to_secondary, event.net_amount_base,
                  event.net_amount_secondary, event.source_kind::text,
                  event.corrects_transaction_id,
                  EXISTS (SELECT FROM investment_transaction correction
                           WHERE correction.corrects_transaction_id = event.id),
                  holding.current_units,
                  lot.id, lot.acquisition_date, lot.acquired_units,
                  lot.cost_basis_native, rtrim(lot.currency)
             FROM investment_transaction event
             JOIN holding ON holding.household_id = event.household_id
                         AND holding.id = event.holding_id
             LEFT JOIN acquisition_lot lot ON lot.household_id = event.household_id
                                          AND lot.origin_transaction_id = event.id
            WHERE event.household_id = %s AND event.id = %s""",
        (household_id, event_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment lifecycle projection is incomplete")
    cur.execute(
        """SELECT reason::text FROM investment_review_item
             WHERE household_id = %s AND investment_transaction_id = %s
               AND status = 'open' ORDER BY reason""",
        (household_id, event_id),
    )
    reasons = [reason[0] for reason in cur.fetchall()]
    lot = (
        None
        if row[22] is None
        else AcquisitionLotRow(
            id=row[22],
            acquisitionDate=row[23],
            acquiredUnits=_required_fixed(row[24], 8),
            costBasisNative=_required_fixed(row[25], 4),
            currency=row[26],
        )
    )
    return CashLifecycleEventRow(
        id=row[0],
        holdingId=row[1],
        accountId=row[2],
        securityId=row[3],
        eventDate=row[4],
        eventKind=row[5],
        units=_fixed(row[6], 8),
        price=_fixed(row[7], 6),
        grossAmount=_fixed(row[8], 4),
        charges=_required_fixed(row[9], 4),
        withheldTax=_required_fixed(row[10], 4),
        netAmount=_required_fixed(row[11], 4),
        currency=row[12],
        fxRateDate=row[13],
        fxRateToBase=_fixed(row[14], 10),
        fxRateToSecondary=_fixed(row[15], 10),
        netAmountBase=_fixed(row[16], 4),
        netAmountSecondary=_fixed(row[17], 4),
        sourceKind=row[18],
        correctsEventId=row[19],
        correctionState="corrected" if row[20] else "uncorrected",
        currentUnits=_required_fixed(row[21], 8),
        lot=lot,
        reviewReasons=reasons,
    )


def create_cash_lifecycle_event(
    owner: OwnerSession, body: CashLifecycleCreate
) -> CashLifecycleResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            reporting_pair = _reporting_pair(cur, owner.household_id)
            existing = _existing_key(cur, owner.household_id, _SOURCE, body.idempotencyKey)
            event_id = _append(
                cur,
                body,
                _SOURCE,
                None,
                None,
                _derive_frozen_flow(cur, body, reporting_pair)
                if existing is None
                else _stored_frozen_flow(cur, owner.household_id, existing),
            )
            _ensure_disposal_review(cur, owner.household_id, event_id, body.eventKind)
            return CashLifecycleResult(
                _load_event(cur, owner.household_id, event_id), existing is None
            )
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None


def correct_cash_lifecycle_event(
    owner: OwnerSession,
    event_id: UUID,
    body: CashLifecycleCorrectionCreate,
) -> CashLifecycleResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            reporting_pair = _reporting_pair(cur, owner.household_id)
            cur.execute(
                """SELECT event_kind::text FROM investment_transaction
                     WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, event_id),
            )
            original = cur.fetchone()
            if original is None:
                raise InvestmentCommandError(404, "investment event not found")
            if original[0] not in _DISPOSALS | {
                "cash_distribution",
                "interest",
                "reinvested_distribution",
                "charge",
                "withheld_tax",
            }:
                raise InvestmentCommandError(
                    422, "only cash lifecycle events can be corrected here"
                )
            existing = _existing_key(
                cur, owner.household_id, _CORRECTION_SOURCE, body.idempotencyKey
            )
            if existing is None:
                _screen_correction_reason(cur, body.correctionReason)
                cur.execute(
                    """SELECT id FROM investment_transaction
                         WHERE household_id = %s AND corrects_transaction_id = %s""",
                    (owner.household_id, event_id),
                )
                if cur.fetchone() is not None:
                    raise InvestmentCommandError(409, "investment event correction state changed")
            replacement_id = _append(
                cur,
                body,
                _CORRECTION_SOURCE,
                event_id,
                body.correctionReason,
                _derive_frozen_flow(cur, body, reporting_pair)
                if existing is None
                else _stored_frozen_flow(cur, owner.household_id, existing),
            )
            _ensure_disposal_review(cur, owner.household_id, replacement_id, body.eventKind)
            return CashLifecycleResult(
                _load_event(cur, owner.household_id, replacement_id), existing is None
            )
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None
