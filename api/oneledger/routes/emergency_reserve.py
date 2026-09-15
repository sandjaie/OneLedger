"""Phase 3.3 Task 2: the emergency reserve (spec "API boundary", `routes/emergency_reserve.py`).
Settings and eligible accounts are owner assumptions like `commitment` — no SECURITY DEFINER write
path, plain household-scoped RLS as defence in depth. `emergency.py` holds the pure baseline and
coverage Decimal math; `resources.py` holds the account value, reservation and spending reads
shared with `routes/actions.py`'s reserve action item, so this module supplies only the display
joins (account names, upcoming commitments, per-currency rates) and reuses `routes/goals.py`'s
reservation row and `routes/plans.py`'s commitment progress rather than duplicating either."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..coverage import months_from_anchor
from ..db import connect
from ..emergency import Baseline, coverage
from ..investments.positions import current_rate_pair
from ..money import _out, translate
from ..resources import (
    NativeValueReason,
    ValuationMode,
    account_native_values,
    eligible_native_and_base,
    emergency_baseline,
    household_currencies,
    household_reservations,
    read_emergency_reserve_settings,
)
from .coverage import coverage_rows
from .goals import ReservationRow, _positive_money, _reservation_row
from .plans import _commitment_progress

router = APIRouter(tags=["emergency-reserve"])

Bucket = Literal["need", "want", "investment", "debt_service"]
_TARGET_MONTHS_RE = re.compile(r"^\d{1,2}(\.\d)?$")
_UPCOMING_WINDOW_DAYS = 30


def _normalize_note(value: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(collapsed) <= 200:
        raise ValueError("restriction note must be 1-200 characters")
    return collapsed


class EmergencyReserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetMonths: str
    baselineMethod: Literal["reference_period", "manual"]
    referenceMonths: int = Field(ge=1, le=24)
    buckets: list[Bucket]
    manualMonthlyBase: str | None = None

    @field_validator("targetMonths")
    @classmethod
    def _target_months(cls, value: str) -> str:
        if not _TARGET_MONTHS_RE.fullmatch(value):
            raise ValueError("target months must be a decimal with at most one place")
        if not (Decimal("0") < Decimal(value) <= Decimal("60")):
            raise ValueError("target months must be greater than zero and at most 60")
        return value

    @field_validator("buckets")
    @classmethod
    def _buckets_nonempty(cls, value: list[Bucket]) -> list[Bucket]:
        if not value:
            raise ValueError("at least one bucket is required")
        return value

    @model_validator(mode="after")
    def _manual_agrees(self) -> EmergencyReserveIn:
        if (self.baselineMethod == "manual") != (self.manualMonthlyBase is not None):
            raise ValueError("manualMonthlyBase is required for manual and forbidden otherwise")
        if self.manualMonthlyBase is not None:
            self.manualMonthlyBase = _positive_money(self.manualMonthlyBase)
        return self


class EligibleAccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accessDays: int = Field(ge=0, le=365)
    restrictionNote: str | None = None

    @field_validator("restrictionNote")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return None if value is None else _normalize_note(value)


class UpcomingCommitmentRow(BaseModel):
    commitmentId: UUID
    label: str
    dueDate: date
    remainingNext: str | None


class EligibleAccountRow(BaseModel):
    accountId: UUID
    accountName: str
    currency: str
    valuationMode: ValuationMode
    value: str | None
    valueDate: date | None
    reason: NativeValueReason | None
    goalReserved: str
    emergencyReserved: str
    eligible: str | None
    accessDays: int
    restrictionNote: str | None
    upcomingCommitments: list[UpcomingCommitmentRow]


class BaselineOut(BaseModel):
    method: str
    monthlyBase: str | None
    referenceStart: date | None
    referenceEnd: date | None
    uncategorisedBase: str | None
    status: str


class CurrencyTotalRow(BaseModel):
    currency: str
    eligibleNative: str
    rate: str | None
    rateDate: date | None


class EmergencyReserveSettings(BaseModel):
    targetMonths: str
    baselineMethod: str
    referenceMonths: int
    buckets: list[str]
    manualMonthlyBase: str | None


class EmergencyReserveResponse(BaseModel):
    asOf: date
    configured: bool
    settings: EmergencyReserveSettings | None
    baseline: BaselineOut | None
    accounts: list[EligibleAccountRow]
    eligibleBase: str | None
    coverageMonths: str | None
    reservedMonths: str | None
    targetBase: str | None
    gapBase: str | None
    coverageStatus: str
    reasons: list[str]
    currencies: list[CurrencyTotalRow]
    coverageGapMonths: int
    baseCurrency: str
    reservations: list[ReservationRow]


def _coverage_gap_months(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date, start: date | None, end: date | None
) -> int:
    """The number of missing/partial statement-coverage cells within a reference period (spec
    "Coverage"), reusing `coverage_rows` rather than a second read of the same evidence."""
    if start is None or end is None:
        return 0
    months = months_from_anchor(as_of)
    rows = coverage_rows(cur, household_id, as_of)
    return sum(
        1
        for row in rows
        for month, cell in zip(months, row.months, strict=True)
        if start <= month <= end and cell.status in ("missing", "partial")
    )


def _upcoming_commitments(
    cur: psycopg.Cursor[Any], household_id: UUID, account_ids: list[UUID], as_of: date
) -> dict[UUID, list[UpcomingCommitmentRow]]:
    if not account_ids:
        return {}
    cur.execute(
        """SELECT id, account_id, label, amount, next_due_date, archived_at
             FROM commitment
            WHERE household_id = %s AND account_id = ANY(%s) AND archived_at IS NULL
              AND next_due_date IS NOT NULL AND next_due_date <= %s
            ORDER BY next_due_date""",
        (household_id, account_ids, as_of + timedelta(days=_UPCOMING_WINDOW_DAYS)),
    )
    rows = cur.fetchall()
    progress = _commitment_progress(
        cur, household_id, as_of, [(r[0], r[5], r[4], Decimal(r[3])) for r in rows]
    )
    by_account: dict[UUID, list[UpcomingCommitmentRow]] = {}
    for cid, account_id, label, _amount, due_date, _archived in rows:
        commitment_id = UUID(str(cid))
        _status, _partly_paid, _paid, remaining = progress[commitment_id]
        by_account.setdefault(UUID(str(account_id)), []).append(
            UpcomingCommitmentRow(
                commitmentId=commitment_id, label=label, dueDate=due_date, remainingNext=remaining
            )
        )
    return by_account


def _emergency_response(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date, base: str, secondary: str
) -> EmergencyReserveResponse:
    raw_settings = read_emergency_reserve_settings(cur, household_id)
    settings: EmergencyReserveSettings | None = None
    baseline: Baseline | None = None
    if raw_settings is not None:
        settings = EmergencyReserveSettings(
            targetMonths=str(raw_settings.targetMonths),
            baselineMethod=raw_settings.baselineMethod,
            referenceMonths=raw_settings.referenceMonths,
            buckets=raw_settings.buckets,
            manualMonthlyBase=_out(raw_settings.manualMonthlyBase),
        )
        baseline = emergency_baseline(cur, household_id, raw_settings, as_of)

    cur.execute(
        """SELECT e.account_id, a.name, e.access_days, e.restriction_note
             FROM emergency_resource_account e
             JOIN account a ON a.household_id = e.household_id AND a.id = e.account_id
            WHERE e.household_id = %s""",
        (household_id,),
    )
    eligible_rows = cur.fetchall()
    eligible_ids = [UUID(str(row[0])) for row in eligible_rows]

    values = account_native_values(cur, household_id, base, secondary, as_of)
    active_reservations = household_reservations(cur, household_id, active_only=True)
    commitments_by_account = _upcoming_commitments(cur, household_id, eligible_ids, as_of)

    accounts: list[EligibleAccountRow] = []
    eligible_bases: list[Decimal | None] = []
    currency_totals: dict[str, Decimal] = {}
    for account_id, name, access_days, note in eligible_rows:
        aid = UUID(str(account_id))
        native = values.get(aid)
        goal_reserved, emergency_reserved, eligible, eligible_base = eligible_native_and_base(
            cur, aid, native, active_reservations, base, secondary, as_of
        )
        value = native.value if native else None
        if native is not None and eligible is not None:
            eligible_bases.append(eligible_base)
            currency_totals[native.currency] = (
                currency_totals.get(native.currency, Decimal("0.0000")) + eligible
            )
        accounts.append(
            EligibleAccountRow(
                accountId=aid,
                accountName=name,
                currency=native.currency if native else "",
                valuationMode=native.valuationMode if native else "ledger_balance",
                value=_out(value),
                valueDate=native.valueDate if native else None,
                reason=native.reason if native else None,
                goalReserved=str(goal_reserved),
                emergencyReserved=str(emergency_reserved),
                eligible=_out(eligible),
                accessDays=access_days,
                restrictionNote=note,
                upcomingCommitments=commitments_by_account.get(aid, []),
            )
        )
    accounts.sort(key=lambda row: row.accountName)

    currencies: list[CurrencyTotalRow] = []
    for currency, native_total in sorted(currency_totals.items()):
        rate = current_rate_pair(cur, currency, base, secondary, as_of).to_base
        currencies.append(
            CurrencyTotalRow(
                currency=currency,
                eligibleNative=str(native_total),
                rate=_out(None if rate is None else rate.rate),
                rateDate=None if rate is None else rate.real_date,
            )
        )

    emergency_reserved_base = Decimal("0.0000")
    for r in active_reservations:
        if r.purpose != "emergency":
            continue
        account_value = values.get(r.accountId)
        native_now = (
            r.nativeAmount
            if r.kind == "amount"
            else (account_value.value if account_value else None)
        )
        if native_now is None:
            continue
        rate = current_rate_pair(cur, r.currency, base, secondary, as_of).to_base
        if rate is None:
            continue
        emergency_reserved_base += translate(native_now, rate.rate)

    reservation_rows = [
        _reservation_row(cur, household_id, r.id, as_of, base, secondary)
        for r in sorted(active_reservations, key=lambda r: r.reservedOn)
        if r.purpose == "emergency"
    ]

    if settings is None or baseline is None:
        return EmergencyReserveResponse(
            asOf=as_of,
            configured=False,
            settings=settings,
            baseline=None,
            accounts=accounts,
            eligibleBase=None,
            coverageMonths=None,
            reservedMonths=None,
            targetBase=None,
            gapBase=None,
            coverageStatus="not_enough_information",
            reasons=["not_configured"],
            currencies=currencies,
            coverageGapMonths=0,
            baseCurrency=base,
            reservations=reservation_rows,
        )

    result = coverage(
        baseline=baseline,
        target_months=Decimal(settings.targetMonths),
        eligible_bases=eligible_bases,
        emergency_reserved_base=emergency_reserved_base,
    )
    gap_months = _coverage_gap_months(
        cur, household_id, as_of, baseline.referenceStart, baseline.referenceEnd
    )
    return EmergencyReserveResponse(
        asOf=as_of,
        configured=True,
        settings=settings,
        baseline=BaselineOut(
            method=baseline.method,
            monthlyBase=_out(baseline.monthlyBase),
            referenceStart=baseline.referenceStart,
            referenceEnd=baseline.referenceEnd,
            uncategorisedBase=_out(baseline.uncategorisedBase),
            status=baseline.status,
        ),
        accounts=accounts,
        eligibleBase=_out(result.eligibleBase),
        coverageMonths=_out(result.coverageMonths),
        reservedMonths=_out(result.reservedMonths),
        targetBase=_out(result.targetBase),
        gapBase=_out(result.gapBase),
        coverageStatus=result.status,
        reasons=result.reasons,
        currencies=currencies,
        coverageGapMonths=gap_months,
        baseCurrency=base,
        reservations=reservation_rows,
    )


@router.get("/emergency-reserve", response_model=EmergencyReserveResponse)
def read_emergency_reserve(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> EmergencyReserveResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        base, secondary = household_currencies(cur, owner.household_id)
        return _emergency_response(cur, owner.household_id, as_of, base, secondary)


@router.put("/emergency-reserve", response_model=EmergencyReserveResponse)
def put_emergency_reserve(
    body: EmergencyReserveIn, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> EmergencyReserveResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        base, secondary = household_currencies(cur, owner.household_id)
        cur.execute(
            """INSERT INTO emergency_reserve (household_id, target_months, baseline_method,
                   reference_months, buckets, manual_monthly_base)
               VALUES (%s, %s, %s, %s, %s::category_bucket[], %s)
               ON CONFLICT (household_id) DO UPDATE
                 SET target_months = excluded.target_months,
                     baseline_method = excluded.baseline_method,
                     reference_months = excluded.reference_months,
                     buckets = excluded.buckets,
                     manual_monthly_base = excluded.manual_monthly_base,
                     updated_at = now()""",
            (
                owner.household_id,
                body.targetMonths,
                body.baselineMethod,
                body.referenceMonths,
                body.buckets,
                body.manualMonthlyBase,
            ),
        )
        return _emergency_response(cur, owner.household_id, date.today(), base, secondary)


@router.put("/emergency-reserve/accounts/{accountId}", response_model=EmergencyReserveResponse)
def put_eligible_account(
    accountId: UUID,
    body: EligibleAccountIn,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> EmergencyReserveResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        base, secondary = household_currencies(cur, owner.household_id)
        cur.execute(
            "SELECT is_active, account_class::text FROM account "
            "WHERE household_id = %s AND id = %s",
            (owner.household_id, accountId),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="account not found")
        is_active, account_class = found
        if not is_active or account_class != "asset":
            raise HTTPException(
                status_code=422, detail="only an active asset account can be an emergency resource"
            )
        cur.execute(
            """INSERT INTO emergency_resource_account
                   (household_id, account_id, access_days, restriction_note)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (household_id, account_id) DO UPDATE
                 SET access_days = excluded.access_days,
                     restriction_note = excluded.restriction_note""",
            (owner.household_id, accountId, body.accessDays, body.restrictionNote),
        )
        return _emergency_response(cur, owner.household_id, date.today(), base, secondary)


@router.delete("/emergency-reserve/accounts/{accountId}", status_code=204)
def delete_eligible_account(
    accountId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "SELECT 1 FROM emergency_resource_account WHERE household_id = %s AND account_id = %s",
            (owner.household_id, accountId),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="account not found")
        cur.execute(
            """SELECT 1 FROM resource_reservation
                WHERE household_id = %s AND account_id = %s AND purpose = 'emergency'
                  AND released_at IS NULL""",
            (owner.household_id, accountId),
        )
        if cur.fetchone() is not None:
            raise HTTPException(
                status_code=409, detail="release this account's emergency reservations first"
            )
        cur.execute(
            "DELETE FROM emergency_resource_account WHERE household_id = %s AND account_id = %s",
            (owner.household_id, accountId),
        )
    return Response(status_code=204)
