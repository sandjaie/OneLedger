"""Owner-authorized holding-level unit action operations: split, bonus, rights and the paired
switch, systematic transfer and platform transfer legs. None of these manufacture cash except
rights, which is a funded acquisition and freezes reporting amounts exactly like a purchase."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Any
from uuid import UUID

import psycopg

from ..auth import OwnerSession
from ..db import connect, scope_to_owner
from ..money import _frozen, translate
from .commands import (
    FrozenFlow,
    InvestmentCommandError,
    _existing_key,
    _lock_event_mutation,
    _lock_reporting_pair,
    _screen_correction_reason,
    _stored_frozen_flow,
    _translate_database_error,
)
from .schemas import (
    AcquisitionLotRow,
    BonusActionCorrectionCreate,
    BonusActionCreate,
    PlatformTransferActionCreate,
    RightsActionCorrectionCreate,
    RightsActionCreate,
    SplitActionCorrectionCreate,
    SplitActionCreate,
    SwitchActionCreate,
    SystematicTransferActionCreate,
    UnitActionCorrectionCreate,
    UnitActionCreate,
    UnitActionEventRow,
    UnitActionLegRow,
    UnitActionResult,
)

_BONUS_SOURCE = "owner_unit_action_bonus_v1"
_BONUS_CORRECTION_SOURCE = "owner_unit_action_bonus_correction_v1"
_RIGHTS_SOURCE = "owner_unit_action_rights_v1"
_RIGHTS_CORRECTION_SOURCE = "owner_unit_action_rights_correction_v1"
_SPLIT_SOURCE = "owner_unit_action_split_v1"
_SPLIT_CORRECTION_SOURCE = "owner_unit_action_split_correction_v1"
_TRANSFER_SOURCE = {
    "switch": "owner_unit_action_switch_v1",
    "systematic_transfer": "owner_unit_action_systematic_transfer_v1",
    "platform_transfer": "owner_unit_action_platform_transfer_v1",
}
_TRANSFER_CORRECTION_SOURCE = {
    "switch": "owner_unit_action_switch_correction_v1",
    "systematic_transfer": "owner_unit_action_systematic_transfer_correction_v1",
    "platform_transfer": "owner_unit_action_platform_transfer_correction_v1",
}
_OUT_KIND = {
    "switch": "switch_out",
    "systematic_transfer": "systematic_transfer_out",
    "platform_transfer": "platform_transfer_out",
}


def _existing_leg_key(
    cur: psycopg.Cursor[Any], household_id: UUID, source_contract: str, source_event_id: str
) -> UUID | None:
    cur.execute(
        """SELECT id FROM investment_transaction
             WHERE household_id = %s AND source_contract = %s AND source_event_id = %s""",
        (household_id, source_contract, source_event_id),
    )
    row = cur.fetchone()
    return None if row is None else UUID(str(row[0]))


def _translate_unit_action_error(exc: psycopg.Error) -> InvestmentCommandError:
    constraint = exc.diag.constraint_name
    if constraint == "investment_split_units_are_exact":
        return InvestmentCommandError(422, "inexact_split")
    if constraint in {"investment_unit_event_order", "investment_split_is_latest"}:
        return InvestmentCommandError(422, "event_order_conflict")
    if constraint == "investment_unit_transfer_pair_agrees":
        return InvestmentCommandError(422, "invalid_transfer_pair")
    return _translate_database_error(exc)


@dataclass(frozen=True)
class UnitActionCommandResult:
    result: UnitActionResult
    created: bool


def _rights_frozen_flow(
    cur: psycopg.Cursor[Any], body: RightsActionCreate, reporting_pair: tuple[str, str]
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


def _load_event(cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID) -> UnitActionEventRow:
    cur.execute(
        """SELECT event.id, event.holding_id, event.account_id, event.security_id,
                  event.event_date, event.event_kind::text, event.units, event.unit_ratio,
                  event.price, event.gross_amount, event.net_amount, rtrim(event.currency),
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
        raise RuntimeError("unit action projection is incomplete")
    lot = (
        None
        if row[15] is None
        else AcquisitionLotRow(
            id=row[15],
            acquisitionDate=row[16],
            acquiredUnits=f"{Decimal(row[17]):.8f}",
            costBasisNative=f"{Decimal(row[18]):.4f}",
            currency=row[19],
        )
    )
    return UnitActionEventRow(
        id=row[0],
        holdingId=row[1],
        accountId=row[2],
        securityId=row[3],
        eventDate=row[4],
        eventKind=row[5],
        units=f"{Decimal(row[6]):.8f}",
        unitRatio=None if row[7] is None else str(Decimal(row[7])),
        price=None if row[8] is None else f"{Decimal(row[8]):.6f}",
        grossAmount=None if row[9] is None else f"{Decimal(row[9]):.4f}",
        netAmount=f"{Decimal(row[10]):.4f}",
        currency=row[11],
        correctsEventId=row[12],
        correctionState="corrected" if row[13] else "uncorrected",
        currentUnits=f"{Decimal(row[14]):.8f}",
        lot=lot,
    )


def _load_leg(cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID) -> UnitActionLegRow:
    cur.execute(
        """SELECT event.id, event.holding_id, event.account_id, event.security_id,
                  event.event_date, event.event_kind::text, event.units, event.price,
                  event.gross_amount, rtrim(event.currency), event.corrects_transaction_id,
                  EXISTS (SELECT FROM investment_transaction correction
                           WHERE correction.corrects_transaction_id = event.id),
                  holding.current_units
             FROM investment_transaction event
             JOIN holding ON holding.household_id = event.household_id
                         AND holding.id = event.holding_id
            WHERE event.household_id = %s AND event.id = %s""",
        (household_id, event_id),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("unit action projection is incomplete")
    return UnitActionLegRow(
        id=row[0],
        holdingId=row[1],
        accountId=row[2],
        securityId=row[3],
        eventDate=row[4],
        eventKind=row[5],
        units=f"{Decimal(row[6]):.8f}",
        price=None if row[7] is None else f"{Decimal(row[7]):.6f}",
        grossAmount=None if row[8] is None else f"{Decimal(row[8]):.4f}",
        currency=row[9],
        correctsEventId=row[10],
        correctionState="corrected" if row[11] else "uncorrected",
        currentUnits=f"{Decimal(row[12]):.8f}",
    )


def _append_bonus(
    cur: psycopg.Cursor[Any],
    body: BonusActionCreate,
    source_contract: str,
    corrects_event_id: UUID | None,
    correction_reason: str | None,
) -> UUID:
    cur.execute(
        """SELECT append_investment_transaction(
             %s, %s, %s, 'bonus'::investment_event_kind,
             %s, NULL, NULL, 0.0000, 0.0000, 0.0000, %s,
             NULL, NULL, NULL, NULL, NULL,
             'owner_entered'::investment_event_source, %s, %s,
             NULL, NULL, %s, %s)""",
        (
            body.accountId,
            body.securityId,
            body.eventDate,
            Decimal(body.units),
            body.currency,
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


def _append_rights(
    cur: psycopg.Cursor[Any],
    body: RightsActionCreate,
    source_contract: str,
    corrects_event_id: UUID | None,
    correction_reason: str | None,
    frozen_flow: FrozenFlow,
) -> UUID:
    fx_date, rate_base, rate_secondary, amount_base, amount_secondary = frozen_flow
    cur.execute(
        """SELECT append_investment_transaction(
             %s, %s, %s, 'rights'::investment_event_kind,
             %s, %s, %s, %s, %s, %s, %s,
             %s, %s, %s, %s, %s,
             'owner_entered'::investment_event_source, %s, %s,
             NULL, NULL, %s, %s)""",
        (
            body.accountId,
            body.securityId,
            body.eventDate,
            Decimal(body.units),
            Decimal(body.price),
            Decimal(body.grossAmount),
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


def _append_split(
    cur: psycopg.Cursor[Any],
    body: SplitActionCreate,
    source_contract: str,
    corrects_event_id: UUID | None,
    correction_reason: str | None,
) -> UUID:
    cur.execute(
        """SELECT append_investment_split(
             %s, %s, %s, %s,
             'owner_entered'::investment_event_source, %s, %s,
             NULL, NULL, %s, %s)""",
        (
            body.accountId,
            body.securityId,
            body.eventDate,
            Decimal(body.unitRatio),
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


def _append_transfer(
    cur: psycopg.Cursor[Any],
    body: SwitchActionCreate | SystematicTransferActionCreate | PlatformTransferActionCreate,
    source_contract: str,
    corrects_out_event_id: UUID | None,
    correction_reason: str | None,
) -> tuple[UUID, UUID]:
    out_kind = _OUT_KIND[body.kind]
    key = str(body.idempotencyKey)
    if isinstance(body, PlatformTransferActionCreate):
        out_account_id = body.outAccountId
        in_account_id = body.inAccountId
        out_security_id = in_security_id = body.securityId
        out_price = in_price = None
        out_gross = in_gross = None
    else:
        out_account_id = in_account_id = body.accountId
        out_security_id = body.outSecurityId
        in_security_id = body.inSecurityId
        out_price = Decimal(body.outPrice)
        in_price = Decimal(body.inPrice)
        out_gross = Decimal(body.outGrossAmount)
        in_gross = Decimal(body.inGrossAmount)
    cur.execute(
        """SELECT out_event_id, in_event_id FROM append_investment_unit_transfer(
             %s::investment_event_kind, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
             'owner_entered'::investment_event_source, %s,
             %s, NULL, NULL, %s, NULL, NULL, %s, %s)""",
        (
            out_kind,
            body.eventDate,
            out_account_id,
            out_security_id,
            Decimal(body.outUnits),
            out_price,
            out_gross,
            in_account_id,
            in_security_id,
            Decimal(body.inUnits),
            in_price,
            in_gross,
            body.currency,
            source_contract,
            f"{key}:out",
            f"{key}:in",
            corrects_out_event_id,
            correction_reason,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment unit transfer returned no legs")
    return UUID(str(row[0])), UUID(str(row[1]))


def create_unit_action(owner: OwnerSession, body: UnitActionCreate) -> UnitActionCommandResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            if isinstance(body, BonusActionCreate):
                existing = _existing_key(
                    cur, owner.household_id, _BONUS_SOURCE, body.idempotencyKey
                )
                event_id = _append_bonus(cur, body, _BONUS_SOURCE, None, None)
                return UnitActionCommandResult(
                    UnitActionResult(
                        kind="bonus", event=_load_event(cur, owner.household_id, event_id)
                    ),
                    existing is None,
                )
            if isinstance(body, RightsActionCreate):
                reporting_pair = _lock_reporting_pair(cur, owner.household_id)
                existing = _existing_key(
                    cur, owner.household_id, _RIGHTS_SOURCE, body.idempotencyKey
                )
                frozen_flow = (
                    _rights_frozen_flow(cur, body, reporting_pair)
                    if existing is None
                    else _stored_frozen_flow(cur, owner.household_id, existing)
                )
                event_id = _append_rights(cur, body, _RIGHTS_SOURCE, None, None, frozen_flow)
                return UnitActionCommandResult(
                    UnitActionResult(
                        kind="rights", event=_load_event(cur, owner.household_id, event_id)
                    ),
                    existing is None,
                )
            if isinstance(body, SplitActionCreate):
                existing = _existing_key(
                    cur, owner.household_id, _SPLIT_SOURCE, body.idempotencyKey
                )
                event_id = _append_split(cur, body, _SPLIT_SOURCE, None, None)
                return UnitActionCommandResult(
                    UnitActionResult(
                        kind="split", event=_load_event(cur, owner.household_id, event_id)
                    ),
                    existing is None,
                )
            source_contract = _TRANSFER_SOURCE[body.kind]
            existing = _existing_leg_key(
                cur, owner.household_id, source_contract, f"{body.idempotencyKey}:out"
            )
            out_id, in_id = _append_transfer(cur, body, source_contract, None, None)
            return UnitActionCommandResult(
                UnitActionResult(
                    kind=body.kind,
                    outEvent=_load_leg(cur, owner.household_id, out_id),
                    inEvent=_load_leg(cur, owner.household_id, in_id),
                ),
                existing is None,
            )
    except psycopg.Error as exc:
        raise _translate_unit_action_error(exc) from None


def correct_unit_action(
    owner: OwnerSession, event_id: UUID, body: UnitActionCorrectionCreate
) -> UnitActionCommandResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            _lock_event_mutation(cur, event_id)
            cur.execute(
                """SELECT event_kind::text FROM investment_transaction
                     WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, event_id),
            )
            original = cur.fetchone()
            if original is None:
                raise InvestmentCommandError(404, "investment event not found")

            if isinstance(body, BonusActionCorrectionCreate):
                if original[0] != "bonus":
                    raise InvestmentCommandError(422, "only a bonus event can be corrected here")
                existing = _existing_key(
                    cur, owner.household_id, _BONUS_CORRECTION_SOURCE, body.idempotencyKey
                )
                if existing is None:
                    _screen_correction_reason(cur, body.correctionReason)
                    _refuse_stale_correction(cur, owner.household_id, event_id)
                replacement_id = _append_bonus(
                    cur, body, _BONUS_CORRECTION_SOURCE, event_id, body.correctionReason
                )
                return UnitActionCommandResult(
                    UnitActionResult(
                        kind="bonus", event=_load_event(cur, owner.household_id, replacement_id)
                    ),
                    existing is None,
                )
            if isinstance(body, RightsActionCorrectionCreate):
                if original[0] != "rights":
                    raise InvestmentCommandError(422, "only a rights event can be corrected here")
                reporting_pair = _lock_reporting_pair(cur, owner.household_id)
                existing = _existing_key(
                    cur, owner.household_id, _RIGHTS_CORRECTION_SOURCE, body.idempotencyKey
                )
                if existing is None:
                    _screen_correction_reason(cur, body.correctionReason)
                    _refuse_stale_correction(cur, owner.household_id, event_id)
                frozen_flow = (
                    _rights_frozen_flow(cur, body, reporting_pair)
                    if existing is None
                    else _stored_frozen_flow(cur, owner.household_id, existing)
                )
                replacement_id = _append_rights(
                    cur,
                    body,
                    _RIGHTS_CORRECTION_SOURCE,
                    event_id,
                    body.correctionReason,
                    frozen_flow,
                )
                return UnitActionCommandResult(
                    UnitActionResult(
                        kind="rights", event=_load_event(cur, owner.household_id, replacement_id)
                    ),
                    existing is None,
                )
            if isinstance(body, SplitActionCorrectionCreate):
                if original[0] != "split":
                    raise InvestmentCommandError(422, "only a split event can be corrected here")
                existing = _existing_key(
                    cur, owner.household_id, _SPLIT_CORRECTION_SOURCE, body.idempotencyKey
                )
                if existing is None:
                    _screen_correction_reason(cur, body.correctionReason)
                    _refuse_stale_correction(cur, owner.household_id, event_id)
                replacement_id = _append_split(
                    cur, body, _SPLIT_CORRECTION_SOURCE, event_id, body.correctionReason
                )
                return UnitActionCommandResult(
                    UnitActionResult(
                        kind="split", event=_load_event(cur, owner.household_id, replacement_id)
                    ),
                    existing is None,
                )
            expected_out_kind = _OUT_KIND[body.kind]
            if original[0] != expected_out_kind:
                raise InvestmentCommandError(
                    422, "only the out leg of a paired transfer can be corrected here"
                )
            source_contract = _TRANSFER_CORRECTION_SOURCE[body.kind]
            existing = _existing_leg_key(
                cur, owner.household_id, source_contract, f"{body.idempotencyKey}:out"
            )
            if existing is None:
                _screen_correction_reason(cur, body.correctionReason)
                _refuse_stale_correction(cur, owner.household_id, event_id)
            out_id, in_id = _append_transfer(
                cur, body, source_contract, event_id, body.correctionReason
            )
            return UnitActionCommandResult(
                UnitActionResult(
                    kind=body.kind,
                    outEvent=_load_leg(cur, owner.household_id, out_id),
                    inEvent=_load_leg(cur, owner.household_id, in_id),
                ),
                existing is None,
            )
    except psycopg.Error as exc:
        raise _translate_unit_action_error(exc) from None


def _refuse_stale_correction(cur: psycopg.Cursor[Any], household_id: UUID, event_id: UUID) -> None:
    cur.execute(
        """SELECT id FROM investment_transaction
             WHERE household_id = %s AND corrects_transaction_id = %s""",
        (household_id, event_id),
    )
    if cur.fetchone() is not None:
        raise InvestmentCommandError(409, "investment event correction state changed")
