"""Phase 3.3 Task 1: goals and resource reservations (spec "API boundary", `routes/goals.py`).
A reservation is a plan, not a ledger transaction — it never changes a balance, net worth or
report (proven by `GET /dashboard` staying unchanged in the live tests). Every write locks the
household row first and filters explicitly by it; `resources.py` holds the database reads this
module orchestrates and `goals.py` holds the pure Decimal math, so this module stays thin."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect, first_line
from ..goals import (
    ReservationProgress,
    ResourceTotals,
    goal_totals,
    reservation_progress,
    resource_totals,
)
from ..investments.positions import current_rate_pair
from ..money import _money, _out, translate
from ..resources import (
    NativeValue,
    NativeValueReason,
    ReservationFact,
    ReservationKind,
    ReservationPurpose,
    ValuationMode,
    account_native_values,
    holdings_inflow_native,
    household_reservations,
)

router = APIRouter(tags=["goals"])

_GOAL_CURRENCY = "goal currency must be the household's base or secondary currency"


def _normalize_name(value: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(collapsed) <= 80:
        raise ValueError("name must be 1-80 characters")
    return collapsed


def _positive_money(value: str) -> str:
    checked = _money(value)
    assert checked is not None
    if Decimal(checked) <= 0:
        raise ValueError("amount must be positive")
    return checked


def _nonnegative_money(value: str) -> str:
    checked = _money(value)
    assert checked is not None
    if Decimal(checked) < 0:
        raise ValueError("amount must not be negative")
    return checked


class _GoalFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    targetAmount: str
    targetCurrency: str = Field(pattern=r"^[A-Z]{3}$")
    targetDate: date
    priority: int = Field(ge=1, le=5)
    plannedMonthly: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _normalize_name(value)

    @field_validator("targetAmount")
    @classmethod
    def _target_amount(cls, value: str) -> str:
        return _positive_money(value)

    @field_validator("plannedMonthly")
    @classmethod
    def _planned_monthly(cls, value: str | None) -> str | None:
        return None if value is None else _nonnegative_money(value)


class GoalCreate(_GoalFields):
    idempotencyKey: UUID


class GoalUpdate(_GoalFields):
    pass


class GoalClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Annotated[str, Field(pattern=r"^(achieved|abandoned)$")]


class ReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: ReservationPurpose
    goalId: UUID | None = None
    accountId: UUID
    kind: ReservationKind
    amount: str | None = None
    idempotencyKey: UUID

    @model_validator(mode="after")
    def _shape(self) -> ReservationCreate:
        if (self.purpose == "goal") != (self.goalId is not None):
            raise ValueError("goalId is required for a goal reservation and forbidden otherwise")
        if (self.kind == "amount") != (self.amount is not None):
            raise ValueError(
                "amount is required for an amount reservation and forbidden for a whole account"
            )
        if self.amount is not None:
            self.amount = _positive_money(self.amount)
        return self


class ReservationRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
        if not 1 <= len(collapsed) <= 200:
            raise ValueError("reason must be 1-200 characters")
        return collapsed


class ReservationRow(BaseModel):
    id: UUID
    purpose: ReservationPurpose
    goalId: UUID | None
    accountId: UUID
    accountName: str
    kind: ReservationKind
    nativeAmount: str
    currency: str
    reservedOn: date
    rateAtReservation: str
    nativeNow: str | None
    currentValue: str | None
    releasedAt: datetime | None
    releaseReason: str | None


class ResourceAccountRow(BaseModel):
    accountId: UUID
    accountName: str
    currency: str
    valuationMode: ValuationMode
    value: str | None
    valueDate: date | None
    reason: NativeValueReason | None
    reserved: str | None
    unreserved: str | None
    overReserved: bool
    wholeAccountReserved: bool


class GoalRow(BaseModel):
    id: UUID
    name: str
    targetAmount: str
    targetCurrency: str
    targetDate: date
    priority: int
    plannedMonthly: str | None
    state: str
    closedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime
    progressStatus: str
    reasons: list[str]
    current: str | None
    contributed: str | None
    market: str | None
    fx: str | None
    projected: str | None
    shortfall: str | None
    surplus: str | None
    percent: str | None
    monthsRemaining: int
    reservations: list[ReservationRow]


class GoalsResponse(BaseModel):
    asOf: date
    goals: list[GoalRow]
    accounts: list[ResourceAccountRow]


def _translate_goal_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_goal_idempotency":
        return HTTPException(
            status_code=409, detail="idempotency key reused with a different request"
        )
    if constraint == "goal_name_is_bounded":
        return HTTPException(status_code=422, detail="name must be 1-80 characters")
    if constraint == "goal_target_is_positive":
        return HTTPException(status_code=422, detail="target amount must be positive")
    if constraint == "goal_priority_is_bounded":
        return HTTPException(status_code=422, detail="priority must be between 1 and 5")
    if constraint == "goal_planned_is_nonnegative":
        return HTTPException(status_code=422, detail="planned monthly must not be negative")
    if exc.sqlstate == "23514" and "closed goal is immutable" in first_line(exc):
        return HTTPException(status_code=409, detail="a closed goal cannot be edited")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(status_code=422, detail="goal violates its required field matrix")
    raise exc


def _translate_reservation_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_reservation_idempotency":
        return HTTPException(
            status_code=409, detail="idempotency key reused with a different request"
        )
    if constraint == "fk_reservation_goal_scope":
        return HTTPException(status_code=404, detail="goal not found")
    if constraint == "fk_reservation_account_scope":
        return HTTPException(status_code=404, detail="account not found")
    if constraint == "reservation_amount_is_positive":
        return HTTPException(status_code=422, detail="reservation amount must be positive")
    if constraint == "uq_reservation_whole_account":
        return HTTPException(status_code=409, detail="this account is already reserved")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="reservation violates its required field matrix"
        )
    raise exc


def _household_currencies(cur: psycopg.Cursor[Any], household_id: UUID) -> tuple[str, str]:
    cur.execute(
        "SELECT base_currency, secondary_currency FROM household WHERE id = %s", (household_id,)
    )
    row = cur.fetchone()
    assert row is not None
    return str(row[0]).strip(), str(row[1]).strip()


def _assert_goal_currency(base: str, secondary: str, currency: str) -> None:
    if currency not in (base, secondary):
        raise HTTPException(status_code=422, detail=_GOAL_CURRENCY)


def _account_names(
    cur: psycopg.Cursor[Any], household_id: UUID, account_ids: list[UUID]
) -> dict[UUID, str]:
    if not account_ids:
        return {}
    cur.execute(
        "SELECT id, name FROM account WHERE household_id = %s AND id = ANY(%s)",
        (household_id, account_ids),
    )
    return {UUID(str(row[0])): row[1] for row in cur.fetchall()}


def _resource_state(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date, base: str, secondary: str
) -> tuple[dict[UUID, NativeValue], list[ReservationFact], dict[UUID, ResourceTotals], set[UUID]]:
    """Every active asset account's value, every reservation in the household, each reservable
    account's reserved/unreserved totals, and which accounts are over-reserved — computed once
    per request (spec "Reservation rules") and shared by every goal and account row it touches."""
    values = account_native_values(cur, household_id, base, secondary, as_of)
    all_reservations = household_reservations(cur, household_id)
    active_by_account: dict[UUID, list[ReservationFact]] = {}
    for reservation in all_reservations:
        if reservation.releasedAt is None:
            active_by_account.setdefault(reservation.accountId, []).append(reservation)
    totals_by_account: dict[UUID, ResourceTotals] = {}
    for account_id, native in values.items():
        active = active_by_account.get(account_id, [])
        whole_active = any(r.kind == "whole_account" for r in active)
        totals_by_account[account_id] = resource_totals(
            native.value,
            [r.nativeAmount for r in active if r.kind == "amount"],
            whole_account_active=whole_active,
        )
    over_reserved = {aid for aid, totals in totals_by_account.items() if totals.overReserved}
    return values, all_reservations, totals_by_account, over_reserved


def _goal_rate(
    cur: psycopg.Cursor[Any], currency: str, goal_currency: str, base: str, secondary: str, on: date
) -> Decimal | None:
    """The rate from `currency` into a goal currency. fx_rate quotes only into the household base,
    so a secondary-currency goal needs `current_rate_pair`'s cross leg; any other currency is
    unavailable rather than guessed."""
    pair = current_rate_pair(cur, currency, base, secondary, on)
    leg = {base: pair.to_base, secondary: pair.to_secondary}.get(goal_currency)
    return None if leg is None else leg.rate


def _native_now_and_current(
    cur: psycopg.Cursor[Any],
    currency: str,
    kind: str,
    native_amount: Decimal,
    native: NativeValue | None,
    target_currency: str,
    base: str,
    secondary: str,
    as_of: date,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    native_now = native_amount if kind == "amount" else (native.value if native else None)
    rate_now = _goal_rate(cur, currency, target_currency, base, secondary, as_of)
    current_value = (
        None if native_now is None or rate_now is None else translate(native_now, rate_now)
    )
    return native_now, rate_now, current_value


def _reservation_row(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    reservation_id: UUID,
    as_of: date,
    base: str,
    secondary: str,
) -> ReservationRow:
    cur.execute(
        """SELECT id, purpose::text, goal_id, account_id, kind::text, native_amount, currency,
                  reserved_on, rate_currency, rate_at_reservation, released_at, release_reason
             FROM resource_reservation WHERE household_id = %s AND id = %s""",
        (household_id, reservation_id),
    )
    row = cur.fetchone()
    assert row is not None
    (
        rid,
        purpose,
        goal_id,
        account_id,
        kind,
        native_amount,
        currency,
        reserved_on,
        _rate_currency,
        rate_at_reservation,
        released_at,
        release_reason,
    ) = row
    account_id = UUID(str(account_id))
    goal_id_uuid = None if goal_id is None else UUID(str(goal_id))
    currency = str(currency).strip()

    if goal_id_uuid is not None:
        cur.execute(
            "SELECT target_currency FROM goal WHERE household_id = %s AND id = %s",
            (household_id, goal_id_uuid),
        )
        found = cur.fetchone()
        target_currency = str(found[0]).strip() if found else currency
    else:
        # Task 2: an emergency reservation always rates into the household base currency. Task 1
        # never inserts one (the route refuses every emergency reservation), so this is unreached.
        target_currency = base

    values = account_native_values(cur, household_id, base, secondary, as_of)
    native_now, _rate_now, current_value = _native_now_and_current(
        cur,
        currency,
        kind,
        Decimal(native_amount),
        values.get(account_id),
        target_currency,
        base,
        secondary,
        as_of,
    )
    names = _account_names(cur, household_id, [account_id])
    return ReservationRow(
        id=UUID(str(rid)),
        purpose=purpose,
        goalId=goal_id_uuid,
        accountId=account_id,
        accountName=names.get(account_id, ""),
        kind=kind,
        nativeAmount=str(native_amount),
        currency=currency,
        reservedOn=reserved_on,
        rateAtReservation=str(rate_at_reservation),
        nativeNow=_out(native_now),
        currentValue=_out(current_value),
        releasedAt=released_at,
        releaseReason=release_reason,
    )


def _goal_row(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    goal_id: UUID,
    as_of: date,
    base: str,
    secondary: str,
    values: dict[UUID, NativeValue],
    all_reservations: list[ReservationFact],
    over_reserved_accounts: set[UUID],
) -> GoalRow:
    cur.execute(
        """SELECT name, target_amount, target_currency, target_date, priority, planned_monthly,
                  state::text, closed_at, created_at, updated_at
             FROM goal WHERE household_id = %s AND id = %s""",
        (household_id, goal_id),
    )
    row = cur.fetchone()
    assert row is not None
    (
        name,
        target_amount,
        target_currency_raw,
        target_date,
        priority,
        planned_monthly,
        state,
        closed_at,
        created_at,
        updated_at,
    ) = row
    target_currency = str(target_currency_raw).strip()

    reservations = [r for r in all_reservations if r.goalId == goal_id]
    account_ids = {r.accountId for r in reservations}
    names = _account_names(cur, household_id, list(account_ids))

    reservation_rows: list[ReservationRow] = []
    progresses: list[ReservationProgress | None] = []
    for reservation in sorted(
        reservations, key=lambda r: (r.releasedAt is not None, r.reservedOn, r.id)
    ):
        native = values.get(reservation.accountId)
        native_now, rate_now, current_value = _native_now_and_current(
            cur,
            reservation.currency,
            reservation.kind,
            reservation.nativeAmount,
            native,
            target_currency,
            base,
            secondary,
            as_of,
        )
        reservation_rows.append(
            ReservationRow(
                id=reservation.id,
                purpose=reservation.purpose,
                goalId=reservation.goalId,
                accountId=reservation.accountId,
                accountName=names.get(reservation.accountId, ""),
                kind=reservation.kind,
                nativeAmount=str(reservation.nativeAmount),
                currency=reservation.currency,
                reservedOn=reservation.reservedOn,
                rateAtReservation=str(reservation.rateAtReservation),
                nativeNow=_out(native_now),
                currentValue=_out(current_value),
                releasedAt=reservation.releasedAt,
                releaseReason=reservation.releaseReason,
            )
        )
        if reservation.releasedAt is not None:
            continue
        if reservation.kind == "amount" or native_now is None or native is None:
            # ponytail: an amount reservation never tracks its account's holdings, so it has no
            # inflow to measure — the same degenerate case as a missing account value.
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

    # Only an active reservation is "reserving" an account (spec "Goal progress").
    active_ids = {r.accountId for r in reservations if r.releasedAt is None}
    over_reserved = bool(active_ids & over_reserved_accounts)
    planned = None if planned_monthly is None else Decimal(planned_monthly)
    progress = goal_totals(
        target_amount=Decimal(target_amount),
        planned_monthly=planned,
        target_date=target_date,
        as_of=as_of,
        reservation_progresses=progresses,
        over_reserved=over_reserved,
    )
    return GoalRow(
        id=goal_id,
        name=name,
        targetAmount=str(target_amount),
        targetCurrency=target_currency,
        targetDate=target_date,
        priority=priority,
        plannedMonthly=_out(planned),
        state=state,
        closedAt=closed_at,
        createdAt=created_at,
        updatedAt=updated_at,
        progressStatus=progress.status,
        reasons=progress.reasons,
        current=_out(progress.current),
        contributed=_out(progress.contributed),
        market=_out(progress.market),
        fx=_out(progress.fx),
        projected=_out(progress.projected),
        shortfall=_out(progress.shortfall),
        surplus=_out(progress.surplus),
        percent=_out(progress.percent),
        monthsRemaining=progress.monthsRemaining,
        reservations=reservation_rows,
    )


def _goal_row_today(
    cur: psycopg.Cursor[Any], household_id: UUID, goal_id: UUID, base: str, secondary: str
) -> GoalRow:
    today = date.today()
    values, reservations, _totals, over = _resource_state(cur, household_id, today, base, secondary)
    return _goal_row(cur, household_id, goal_id, today, base, secondary, values, reservations, over)


@router.get("/goals", response_model=GoalsResponse)
def read_goals(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
    includeClosed: Annotated[bool, Query()] = False,
) -> GoalsResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        base, secondary = _household_currencies(cur, owner.household_id)
        values, all_reservations, totals_by_account, over_reserved = _resource_state(
            cur, owner.household_id, as_of, base, secondary
        )
        names = _account_names(cur, owner.household_id, list(values.keys()))
        accounts = [
            ResourceAccountRow(
                accountId=account_id,
                accountName=names.get(account_id, ""),
                currency=native.currency,
                valuationMode=native.valuationMode,
                value=_out(native.value),
                valueDate=native.valueDate,
                reason=native.reason,
                reserved=_out(totals_by_account[account_id].reserved),
                unreserved=_out(totals_by_account[account_id].unreserved),
                overReserved=totals_by_account[account_id].overReserved,
                wholeAccountReserved=totals_by_account[account_id].wholeAccountReserved,
            )
            for account_id, native in sorted(values.items(), key=lambda kv: names.get(kv[0], ""))
        ]

        state_filter = "" if includeClosed else " AND state = 'active'"
        cur.execute(
            f"""SELECT id FROM goal WHERE household_id = %s{state_filter}
                 ORDER BY priority, target_date, name""",
            (owner.household_id,),
        )
        goals = [
            _goal_row(
                cur,
                owner.household_id,
                UUID(str(row[0])),
                as_of,
                base,
                secondary,
                values,
                all_reservations,
                over_reserved,
            )
            for row in cur.fetchall()
        ]
    return GoalsResponse(asOf=as_of, goals=goals, accounts=accounts)


@router.post("/goals", response_model=GoalRow)
def create_goal(
    body: GoalCreate, response: Response, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> GoalRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            base, secondary = _household_currencies(cur, owner.household_id)
            cur.execute(
                """SELECT id, name, target_amount, target_currency, target_date, priority,
                          planned_monthly
                     FROM goal WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                eid, ename, eamount, ecurrency, edate, epriority, eplanned = existing
                matches = (
                    ename == body.name
                    and Decimal(eamount) == Decimal(body.targetAmount)
                    and str(ecurrency).strip() == body.targetCurrency
                    and edate == body.targetDate
                    and epriority == body.priority
                    and (eplanned is None) == (body.plannedMonthly is None)
                    and (
                        eplanned is None or Decimal(eplanned) == Decimal(body.plannedMonthly)  # type: ignore[arg-type]
                    )
                )
                if matches:
                    response.status_code = status.HTTP_200_OK
                    return _goal_row_today(cur, owner.household_id, UUID(str(eid)), base, secondary)
                raise HTTPException(
                    status_code=409, detail="idempotency key reused with a different request"
                )

            _assert_goal_currency(base, secondary, body.targetCurrency)
            cur.execute(
                """INSERT INTO goal (household_id, idempotency_key, name, target_amount,
                       target_currency, target_date, priority, planned_monthly)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.name,
                    body.targetAmount,
                    body.targetCurrency,
                    body.targetDate,
                    body.priority,
                    body.plannedMonthly,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            return _goal_row_today(cur, owner.household_id, UUID(str(created[0])), base, secondary)
    except psycopg.Error as exc:
        raise _translate_goal_error(exc) from None


@router.put("/goals/{goalId}", response_model=GoalRow)
def update_goal(
    goalId: UUID, body: GoalUpdate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> GoalRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            base, secondary = _household_currencies(cur, owner.household_id)
            cur.execute(
                "SELECT state::text, target_currency FROM goal WHERE household_id = %s AND id = %s "
                "FOR UPDATE",
                (owner.household_id, goalId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="goal not found")
            state, current_currency = found
            if state != "active":
                raise HTTPException(status_code=409, detail="a closed goal cannot be edited")
            _assert_goal_currency(base, secondary, body.targetCurrency)
            if str(current_currency).strip() != body.targetCurrency:
                cur.execute(
                    "SELECT 1 FROM resource_reservation WHERE household_id = %s AND goal_id = %s "
                    "LIMIT 1",
                    (owner.household_id, goalId),
                )
                if cur.fetchone() is not None:
                    raise HTTPException(
                        status_code=409, detail="a goal with reservations keeps its currency"
                    )
            cur.execute(
                """UPDATE goal SET name = %s, target_amount = %s, target_currency = %s,
                          target_date = %s, priority = %s, planned_monthly = %s, updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (
                    body.name,
                    body.targetAmount,
                    body.targetCurrency,
                    body.targetDate,
                    body.priority,
                    body.plannedMonthly,
                    owner.household_id,
                    goalId,
                ),
            )
            return _goal_row_today(cur, owner.household_id, goalId, base, secondary)
    except psycopg.Error as exc:
        raise _translate_goal_error(exc) from None


@router.post("/goals/{goalId}/close", response_model=GoalRow)
def close_goal(
    goalId: UUID, body: GoalClose, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> GoalRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        base, secondary = _household_currencies(cur, owner.household_id)
        cur.execute(
            "SELECT state::text FROM goal WHERE household_id = %s AND id = %s FOR UPDATE",
            (owner.household_id, goalId),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="goal not found")
        state = found[0]
        if state == "active":
            cur.execute(
                "UPDATE goal SET state = %s, closed_at = now(), updated_at = now() "
                "WHERE household_id = %s AND id = %s",
                (body.state, owner.household_id, goalId),
            )
            cur.execute(
                """UPDATE resource_reservation SET released_at = now(),
                          release_reason = 'goal closed'
                    WHERE household_id = %s AND goal_id = %s AND released_at IS NULL""",
                (owner.household_id, goalId),
            )
        elif state != body.state:
            raise HTTPException(
                status_code=409, detail="this goal is already closed with a different outcome"
            )
        return _goal_row_today(cur, owner.household_id, goalId, base, secondary)


@router.post("/reservations", response_model=ReservationRow)
def create_reservation(
    body: ReservationCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> ReservationRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            base, secondary = _household_currencies(cur, owner.household_id)

            cur.execute(
                """SELECT id, purpose::text, goal_id, account_id, kind::text, native_amount
                     FROM resource_reservation WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                eid, epurpose, egoal, eaccount, ekind, enative = existing
                matches = (
                    epurpose == body.purpose
                    and (None if egoal is None else UUID(str(egoal))) == body.goalId
                    and UUID(str(eaccount)) == body.accountId
                    and ekind == body.kind
                    and (body.amount is None or Decimal(enative) == Decimal(body.amount))
                )
                if matches:
                    response.status_code = status.HTTP_200_OK
                    return _reservation_row(
                        cur, owner.household_id, UUID(str(eid)), date.today(), base, secondary
                    )
                raise HTTPException(
                    status_code=409, detail="idempotency key reused with a different request"
                )

            cur.execute(
                "SELECT currency, is_active, account_class::text FROM account "
                "WHERE household_id = %s AND id = %s",
                (owner.household_id, body.accountId),
            )
            account = cur.fetchone()
            if account is None:
                raise HTTPException(status_code=404, detail="account not found")
            currency, is_active, account_class = account
            currency = str(currency).strip()
            if not is_active or account_class != "asset":
                raise HTTPException(
                    status_code=422, detail="only an active asset account can be reserved"
                )

            as_of = date.today()
            values = account_native_values(cur, owner.household_id, base, secondary, as_of)
            native = values.get(body.accountId)
            value = native.value if native else None
            if value is None or (body.kind == "whole_account" and value <= 0):
                raise HTTPException(
                    status_code=422, detail="this account has no current value to reserve"
                )

            active = [
                r
                for r in household_reservations(cur, owner.household_id, active_only=True)
                if r.accountId == body.accountId
            ]
            whole_active = any(r.kind == "whole_account" for r in active)
            totals = resource_totals(
                value,
                [r.nativeAmount for r in active if r.kind == "amount"],
                whole_account_active=whole_active,
            )
            # Exclusivity first: an active whole-account reservation leaves zero unreserved, so the
            # amount check below would otherwise mask the 409 the spec names for it.
            if whole_active or (body.kind == "whole_account" and active):
                raise HTTPException(status_code=409, detail="this account is already reserved")
            amount_decimal: Decimal | None = None
            if body.kind == "amount":
                assert body.amount is not None
                amount_decimal = Decimal(body.amount)
                if totals.unreserved is None or amount_decimal > totals.unreserved:
                    raise HTTPException(
                        status_code=422,
                        detail="this reservation is more than the account's unreserved value",
                    )

            if body.purpose == "goal":
                assert body.goalId is not None
                cur.execute(
                    "SELECT state::text, target_currency FROM goal "
                    "WHERE household_id = %s AND id = %s FOR UPDATE",
                    (owner.household_id, body.goalId),
                )
                goal = cur.fetchone()
                if goal is None:
                    raise HTTPException(status_code=404, detail="goal not found")
                goal_state, goal_currency = goal
                if goal_state != "active":
                    raise HTTPException(
                        status_code=409, detail="a closed goal takes no reservation"
                    )
                rate_currency = str(goal_currency).strip()
            else:
                # Task 2 wires eligibility; until then no account is eligible for an emergency
                # reservation.
                raise HTTPException(
                    status_code=422, detail="mark this account as an emergency resource first"
                )

            rate = _goal_rate(cur, currency, rate_currency, base, secondary, as_of)
            if rate is None:
                raise HTTPException(
                    status_code=422, detail="no exchange rate is available for this reservation"
                )

            native_amount = amount_decimal if body.kind == "amount" else value
            cur.execute(
                """INSERT INTO resource_reservation (household_id, idempotency_key, purpose,
                       goal_id, account_id, kind, native_amount, currency, reserved_on,
                       rate_currency, rate_at_reservation)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.purpose,
                    body.goalId,
                    body.accountId,
                    body.kind,
                    native_amount,
                    currency,
                    as_of,
                    rate_currency,
                    rate,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            return _reservation_row(
                cur, owner.household_id, UUID(str(created[0])), as_of, base, secondary
            )
    except psycopg.Error as exc:
        raise _translate_reservation_error(exc) from None


@router.post("/reservations/{reservationId}/release", response_model=ReservationRow)
def release_reservation(
    reservationId: UUID,
    body: ReservationRelease,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> ReservationRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        base, secondary = _household_currencies(cur, owner.household_id)
        cur.execute(
            "SELECT released_at FROM resource_reservation WHERE household_id = %s AND id = %s "
            "FOR UPDATE",
            (owner.household_id, reservationId),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="reservation not found")
        if found[0] is None:
            cur.execute(
                """UPDATE resource_reservation SET released_at = now(), release_reason = %s
                    WHERE household_id = %s AND id = %s""",
                (body.reason, owner.household_id, reservationId),
            )
        return _reservation_row(
            cur, owner.household_id, reservationId, date.today(), base, secondary
        )
