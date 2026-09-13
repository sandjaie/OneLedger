"""Owner-authorized investment event commands over the atomic database operation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from typing import Any
from uuid import UUID

import psycopg

from ..auth import OwnerSession
from ..db import connect, scope_to_owner
from ..ingest.engine import CONTEXTUAL_SECRET
from ..money import _frozen, translate
from .schemas import (
    AcquisitionCorrectionCreate,
    AcquisitionCreate,
    AcquisitionLotRow,
    InvestmentEventRow,
)

_ACQUISITION_SOURCE = "owner_acquisition_v1"
_CORRECTION_SOURCE = "owner_acquisition_correction_v1"
FrozenFlow = tuple[date, Decimal, Decimal, Decimal, Decimal]


class InvestmentCommandError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class CommandResult:
    event: InvestmentEventRow
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


def _append(
    cur: psycopg.Cursor[Any],
    body: AcquisitionCreate,
    source_contract: str,
    corrects_event_id: UUID | None,
    correction_reason: str | None,
    frozen_flow: FrozenFlow,
) -> UUID:
    fx_rate_date, rate_to_base, rate_to_secondary, amount_base, amount_secondary = frozen_flow
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
            Decimal(body.units),
            Decimal(body.price),
            Decimal(body.grossAmount),
            Decimal(body.charges),
            Decimal(body.withheldTax),
            Decimal(body.netAmount),
            body.currency,
            fx_rate_date,
            rate_to_base,
            rate_to_secondary,
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


def _lock_reporting_pair(cur: psycopg.Cursor[Any], household_id: UUID) -> tuple[str, str]:
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
    cur: psycopg.Cursor[Any], body: AcquisitionCreate, reporting_pair: tuple[str, str]
) -> FrozenFlow:
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


def _screen_correction_reason(cur: psycopg.Cursor[Any], reason: str) -> None:
    private = CONTEXTUAL_SECRET.search(reason) is not None
    if not private:
        cur.execute("SELECT counterparty_text_has_private_identifier(%s)", (reason,))
        found = cur.fetchone()
        private = found is not None and bool(found[0])
    if private:
        raise InvestmentCommandError(422, "correction reason must not contain private information")


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
    return row[0], Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4])


def _load_event(cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID) -> InvestmentEventRow:
    cur.execute(
        """SELECT event.id, event.holding_id, event.account_id, event.security_id,
                  event.event_date, event.event_kind::text, event.units, event.price,
                  event.gross_amount, event.charges, event.withheld_tax, event.net_amount,
                  event.currency, event.fx_rate_date, event.fx_rate_to_base,
                  event.fx_rate_to_secondary, event.net_amount_base,
                  event.net_amount_secondary, event.source_kind::text,
                  event.corrects_transaction_id,
                  EXISTS (SELECT FROM investment_transaction correction
                           WHERE correction.corrects_transaction_id = event.id),
                  holding.current_units,
                  lot.id, lot.acquisition_date, lot.acquired_units,
                  lot.cost_basis_native, lot.currency
             FROM investment_transaction event
             JOIN holding ON holding.household_id = event.household_id
                         AND holding.id = event.holding_id
             JOIN acquisition_lot lot ON lot.household_id = event.household_id
                                     AND lot.origin_transaction_id = event.id
            WHERE event.household_id = %s AND event.id = %s""",
        (household_id, event_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment acquisition projection is incomplete")
    return InvestmentEventRow(
        id=row[0],
        holdingId=row[1],
        accountId=row[2],
        securityId=row[3],
        eventDate=row[4],
        eventKind=row[5],
        units=str(row[6]),
        price=str(row[7]),
        grossAmount=str(row[8]),
        charges=str(row[9]),
        withheldTax=str(row[10]),
        netAmount=str(row[11]),
        currency=str(row[12]).strip(),
        fxRateDate=row[13],
        fxRateToBase=str(row[14]),
        fxRateToSecondary=str(row[15]),
        netAmountBase=str(row[16]),
        netAmountSecondary=str(row[17]),
        sourceKind=row[18],
        correctsEventId=row[19],
        correctionState="corrected" if row[20] else "uncorrected",
        currentUnits=str(row[21]),
        lot=AcquisitionLotRow(
            id=row[22],
            acquisitionDate=row[23],
            acquiredUnits=str(row[24]),
            costBasisNative=str(row[25]),
            currency=str(row[26]).strip(),
        ),
    )


def _translate_database_error(exc: psycopg.Error) -> InvestmentCommandError:
    if exc.sqlstate == "42501":
        return InvestmentCommandError(404, "investment account or security not found")
    if exc.diag.constraint_name == "investment_correction_requires_unfunded_event":
        return InvestmentCommandError(422, "a funded investment event cannot be corrected")
    if exc.sqlstate == "23505":
        return InvestmentCommandError(409, "idempotency key conflicts with another event")
    if exc.sqlstate in {"22003", "22023", "22P02", "23502", "23503", "23514"}:
        return InvestmentCommandError(422, "investment event violates its required field matrix")
    raise exc


def create_acquisition(owner: OwnerSession, body: AcquisitionCreate) -> CommandResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            reporting_pair = _lock_reporting_pair(cur, owner.household_id)
            existing = _existing_key(
                cur, owner.household_id, _ACQUISITION_SOURCE, body.idempotencyKey
            )
            frozen_flow = (
                _derive_frozen_flow(cur, body, reporting_pair)
                if existing is None
                else _stored_frozen_flow(cur, owner.household_id, existing)
            )
            event_id = _append(cur, body, _ACQUISITION_SOURCE, None, None, frozen_flow)
            return CommandResult(_load_event(cur, owner.household_id, event_id), existing is None)
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None


def correct_acquisition(
    owner: OwnerSession, event_id: UUID, body: AcquisitionCorrectionCreate
) -> CommandResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            reporting_pair = _lock_reporting_pair(cur, owner.household_id)
            cur.execute(
                """SELECT event_kind::text FROM investment_transaction
                     WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, event_id),
            )
            original = cur.fetchone()
            if original is None:
                raise InvestmentCommandError(404, "investment event not found")
            if original[0] not in {"purchase", "recurring_purchase"}:
                raise InvestmentCommandError(422, "only acquisition events can be corrected here")
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
            correction_id = _append(
                cur,
                body,
                _CORRECTION_SOURCE,
                event_id,
                body.correctionReason,
                _derive_frozen_flow(cur, body, reporting_pair)
                if existing is None
                else _stored_frozen_flow(cur, owner.household_id, existing),
            )
            return CommandResult(
                _load_event(cur, owner.household_id, correction_id), existing is None
            )
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None
