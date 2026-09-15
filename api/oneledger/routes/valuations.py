# api/oneledger/routes/valuations.py
"""Phase UI completion Task 2a: owner-entered valuation snapshots and valuation-mode transitions
for snapshot-valued asset accounts (retirement, property, precious metal, crypto, other asset).
Snapshots are immutable (`preserve_valuation_snapshot`); there is no correction route — a later
snapshot supersedes an earlier one only from its own date. The API connects as the table owner,
so RLS does not protect these routes: every query filters explicitly by `owner.household_id`."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..ingest.engine import CONTEXTUAL_SECRET
from ..money import _frozen, translate

router = APIRouter(tags=["valuations"])

ValuationMode = Literal["ledger_balance", "holdings", "snapshot"]
ValuationMethod = Literal["owner_entered", "market_quote", "indexed", "statement"]
ValuationConfidence = Literal["high", "medium", "low"]

# Same shape as money.MONEY, minus the sign: value_native is CHECKed non-negative.
_VALUE_NATIVE = re.compile(r"\d{1,16}(\.\d{1,4})?")


class ValuationSnapshotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asOfDate: date
    valueNative: str
    method: Literal["owner_entered", "statement"]
    confidence: ValuationConfidence
    sourceNote: str | None = None

    @field_validator("valueNative")
    @classmethod
    def _value_native(cls, value: str) -> str:
        if not _VALUE_NATIVE.fullmatch(value):
            raise ValueError(
                "valueNative must be a non-negative decimal string with at most four decimal places"
            )
        return value

    @model_validator(mode="after")
    def _not_future(self) -> ValuationSnapshotCreate:
        if self.asOfDate > date.today():
            raise ValueError("as-of date must not be in the future")
        return self


class ValuationSnapshotRow(BaseModel):
    id: UUID
    asOfDate: date
    valueNative: str
    currency: str
    fxRateDate: date
    fxRateToBase: str
    fxRateToSecondary: str
    valueBase: str
    valueSecondary: str
    method: ValuationMethod
    confidence: ValuationConfidence
    sourceNote: str | None
    createdAt: datetime


class ValuationPeriodRow(BaseModel):
    id: UUID
    valuationMode: ValuationMode
    effectiveFrom: date
    effectiveTo: date | None


class AccountValuationsResponse(BaseModel):
    accountId: UUID
    currentMode: ValuationMode
    currentEffectiveFrom: date
    periods: list[ValuationPeriodRow]
    snapshots: list[ValuationSnapshotRow]


class ValuationModeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valuationMode: ValuationMode
    effectiveFrom: date


class ValuationModeResponse(BaseModel):
    accountId: UUID
    valuationMode: ValuationMode
    effectiveFrom: date


def _screen_source_note(cur: psycopg.Cursor[Any], note: str) -> None:
    private = CONTEXTUAL_SECRET.search(note) is not None
    if not private:
        cur.execute("SELECT counterparty_text_has_private_identifier(%s)", (note,))
        found = cur.fetchone()
        private = found is not None and bool(found[0])
    if private:
        raise HTTPException(
            status_code=422, detail="the source note must not contain private information"
        )


def _account_currency(cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID) -> str:
    cur.execute(
        "SELECT currency FROM account WHERE household_id = %s AND id = %s",
        (household_id, account_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="no such account")
    return str(found[0]).strip()


_SNAPSHOT_COLUMNS = """id, as_of_date, value_native, currency, fx_rate_date, fx_rate_to_base,
                       fx_rate_to_secondary, value_base, value_secondary, method::text,
                       confidence::text, source_note, created_at"""


def _snapshot_row(row: tuple[Any, ...]) -> ValuationSnapshotRow:
    return ValuationSnapshotRow(
        id=row[0],
        asOfDate=row[1],
        valueNative=str(row[2]),
        currency=str(row[3]).strip(),
        fxRateDate=row[4],
        fxRateToBase=str(row[5]),
        fxRateToSecondary=str(row[6]),
        valueBase=str(row[7]),
        valueSecondary=str(row[8]),
        method=row[9],
        confidence=row[10],
        sourceNote=row[11],
        createdAt=row[12],
    )


def _translate_snapshot_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint in {
        "fk_valuation_snapshot_target_scope",
        "fk_valuation_snapshot_account_household",
    }:
        return HTTPException(status_code=404, detail="no such account")
    if exc.sqlstate == "23505":
        # The household row lock below serializes writes for one household, so this is a
        # defensive fallback rather than an expected path.
        return HTTPException(status_code=409, detail="a valuation already exists for this date")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="valuation snapshot violates its required field matrix"
        )
    raise exc


@router.get("/accounts/{account_id}/valuations", response_model=AccountValuationsResponse)
def list_valuations(
    account_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> AccountValuationsResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM account WHERE household_id = %s AND id = %s",
            (owner.household_id, account_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="no such account")
        cur.execute(
            """SELECT id, valuation_mode::text, effective_from, effective_to
                 FROM account_valuation_period
                WHERE household_id = %s AND account_id = %s
                ORDER BY effective_from DESC""",
            (owner.household_id, account_id),
        )
        periods = [
            ValuationPeriodRow(id=r[0], valuationMode=r[1], effectiveFrom=r[2], effectiveTo=r[3])
            for r in cur.fetchall()
        ]
        cur.execute(
            f"""SELECT {_SNAPSHOT_COLUMNS} FROM valuation_snapshot
                WHERE household_id = %s AND account_id = %s
                ORDER BY as_of_date DESC, created_at DESC, id DESC""",
            (owner.household_id, account_id),
        )
        snapshots = [_snapshot_row(r) for r in cur.fetchall()]
    current = next((p for p in periods if p.effectiveTo is None), periods[0])
    return AccountValuationsResponse(
        accountId=account_id,
        currentMode=current.valuationMode,
        currentEffectiveFrom=current.effectiveFrom,
        periods=periods,
        snapshots=snapshots,
    )


@router.post(
    "/accounts/{account_id}/valuation-snapshots",
    response_model=ValuationSnapshotRow,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ValuationSnapshotRow, "description": "Identical replay"}},
)
def create_valuation_snapshot(
    account_id: UUID,
    body: ValuationSnapshotCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> ValuationSnapshotRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            if body.sourceNote is not None:
                _screen_source_note(cur, body.sourceNote)
            # The household lock serializes valuation writes the same way POST /transactions
            # does (spec B3.5): two posts for the same account/date cannot race past each other.
            cur.execute(
                "SELECT base_currency, secondary_currency FROM household WHERE id = %s FOR UPDATE",
                (owner.household_id,),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="household not found")
            base, secondary = str(found[0]).strip(), str(found[1]).strip()
            currency = _account_currency(cur, owner.household_id, account_id)

            cur.execute(
                f"""SELECT {_SNAPSHOT_COLUMNS} FROM valuation_snapshot
                    WHERE household_id = %s AND account_id = %s AND as_of_date = %s""",
                (owner.household_id, account_id, body.asOfDate),
            )
            existing = cur.fetchone()
            if existing is not None:
                current = _snapshot_row(existing)
                if (
                    Decimal(current.valueNative) == Decimal(body.valueNative)
                    and current.method == body.method
                    and current.confidence == body.confidence
                    and current.sourceNote == body.sourceNote
                ):
                    response.status_code = status.HTTP_200_OK
                    return current
                raise HTTPException(
                    status_code=409,
                    detail="a different valuation is already recorded for this date",
                )

            rate_to_base, rate_to_secondary, _carried, rate_date = _frozen(
                cur, body.asOfDate, currency, base, secondary
            )
            native = Decimal(body.valueNative)
            cur.execute(
                f"""INSERT INTO valuation_snapshot (
                        household_id, account_id, as_of_date, value_native, currency,
                        fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                        value_base, value_secondary, method, confidence, source_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::valuation_method,
                            %s::valuation_confidence, %s)
                    RETURNING {_SNAPSHOT_COLUMNS}""",
                (
                    owner.household_id,
                    account_id,
                    body.asOfDate,
                    native,
                    currency,
                    rate_date,
                    rate_to_base,
                    rate_to_secondary,
                    translate(native, rate_to_base),
                    translate(native, rate_to_secondary),
                    body.method,
                    body.confidence,
                    body.sourceNote,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            return _snapshot_row(created)
    except psycopg.Error as exc:
        raise _translate_snapshot_error(exc) from None


def _current_period(
    cur: psycopg.Cursor[Any], household_id: UUID, account_id: UUID
) -> tuple[UUID, str, date] | None:
    cur.execute(
        """SELECT id, valuation_mode::text, effective_from FROM account_valuation_period
            WHERE household_id = %s AND account_id = %s AND effective_to IS NULL""",
        (household_id, account_id),
    )
    row = cur.fetchone()
    return None if row is None else (row[0], row[1], row[2])


def _translate_mode_error(exc: psycopg.Error) -> HTTPException:
    if exc.sqlstate == "42501":
        return HTTPException(status_code=404, detail="no such account")
    if exc.sqlstate == "55000":
        return HTTPException(status_code=409, detail="account has no current valuation period")
    if exc.sqlstate == "23514":
        constraint = exc.diag.constraint_name
        if constraint == "account_liability_uses_ledger_value":
            return HTTPException(
                status_code=422, detail="liability accounts require ledger_balance valuation mode"
            )
        if constraint == "account_valuation_mode_starts_after_anchor":
            return HTTPException(
                status_code=422, detail="valuation mode cannot predate the account balance anchor"
            )
        if constraint == "account_valuation_transition_retains_snapshots":
            return HTTPException(
                status_code=422,
                detail="the transition would strand retained valuation snapshots",
            )
        return HTTPException(
            status_code=422, detail="valuation mode transition violates its required field matrix"
        )
    raise exc


@router.post("/accounts/{account_id}/valuation-mode", response_model=ValuationModeResponse)
def change_valuation_mode(
    account_id: UUID,
    body: ValuationModeCreate,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> ValuationModeResponse:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            current = _current_period(cur, owner.household_id, account_id)
            if current is None:
                raise HTTPException(status_code=404, detail="no such account")
            _current_id, current_mode, current_from = current
            if current_mode == body.valuationMode:
                if current_from == body.effectiveFrom:
                    return ValuationModeResponse(
                        accountId=account_id,
                        valuationMode=current_mode,
                        effectiveFrom=current_from,
                    )
                raise HTTPException(
                    status_code=409, detail="the account is already in this valuation mode"
                )
            cur.execute(
                "SELECT transition_account_valuation_mode(%s, %s::account_valuation_mode, %s)",
                (account_id, body.valuationMode, body.effectiveFrom),
            )
            return ValuationModeResponse(
                accountId=account_id,
                valuationMode=body.valuationMode,
                effectiveFrom=body.effectiveFrom,
            )
    except psycopg.Error as exc:
        raise _translate_mode_error(exc) from None
