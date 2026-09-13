from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hmac import compare_digest
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from ..auth import OwnerSession
from ..db import connect, scope_to_owner

_MONEY_PATTERN = r"^\d{1,16}(?:\.\d{1,4})?$"
_UNITS_PATTERN = r"^\d{1,20}(?:\.\d{1,8})?$"
_ELIGIBILITY_ERROR = "funding allocation is not a full eligible cash-leaf decision"
CashRole = Literal["funding", "proceeds", "cash_distribution", "fee"]
CashTransactionType = Literal[
    "investment_buy", "investment_sell", "investment_income", "investment_fee"
]

_ROLE_BY_KIND: dict[str, CashRole] = {
    "purchase": "funding",
    "recurring_purchase": "funding",
    "rights": "funding",
    "sale": "proceeds",
    "redemption": "proceeds",
    "maturity": "proceeds",
    "cash_distribution": "cash_distribution",
    "interest": "cash_distribution",
    "charge": "fee",
    "withheld_tax": "fee",
}


class FundingError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FundingAllocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investmentEventId: UUID
    cashTransactionId: UUID
    allocatedAmount: StrictStr = Field(pattern=_MONEY_PATTERN)

    @model_validator(mode="after")
    def amount_is_positive(self) -> FundingAllocationInput:
        if Decimal(self.allocatedAmount) <= 0:
            raise ValueError("allocated amount must be positive")
        return self


class FundingProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allocations: list[FundingAllocationInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def pairs_are_unique(self) -> FundingProposalRequest:
        pairs = {(item.investmentEventId, item.cashTransactionId) for item in self.allocations}
        if len(pairs) != len(self.allocations):
            raise ValueError("allocation pairs must be unique")
        return self


class FundingConfirmationRequest(FundingProposalRequest):
    expectedState: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class FundingProposalAllocation(BaseModel):
    investmentEventId: UUID
    cashTransactionId: UUID
    allocatedAmount: str
    eventRemainingAmount: str
    cashAmount: str
    currency: str
    priorTransactionType: str
    role: CashRole


class FundingProposalResponse(BaseModel):
    state: Literal["ready"] = "ready"
    expectedState: str
    allocations: list[FundingProposalAllocation]


class ConfirmedFundingAllocation(BaseModel):
    id: UUID
    investmentEventId: UUID
    cashTransactionId: UUID
    role: CashRole
    allocatedAmount: str
    priorTransactionType: str
    cashTransactionType: CashTransactionType


class FundingConfirmationResponse(BaseModel):
    state: Literal["confirmed"] = "confirmed"
    allocations: list[ConfirmedFundingAllocation]


class FundingUnlinkResponse(BaseModel):
    state: Literal["unlinked"] = "unlinked"
    cashTransactionId: UUID
    restoredTransactionType: str
    removedAllocationCount: int


class LotAllocationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisitionLotId: UUID
    allocatedUnits: StrictStr = Field(pattern=_UNITS_PATTERN)

    @model_validator(mode="after")
    def units_are_positive(self) -> LotAllocationInput:
        if Decimal(self.allocatedUnits) <= 0:
            raise ValueError("allocated units must be positive")
        return self


class LotAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposalEventId: UUID
    allocations: list[LotAllocationInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def lots_are_unique(self) -> LotAllocationRequest:
        if len({item.acquisitionLotId for item in self.allocations}) != len(self.allocations):
            raise ValueError("acquisition lots must be unique")
        return self


class LotAllocationConfirmationRequest(LotAllocationRequest):
    expectedState: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    idempotencyKey: UUID


class ConfirmedLotAllocation(BaseModel):
    acquisitionLotId: UUID
    allocatedUnits: str


class LotAllocationProposalResponse(BaseModel):
    state: Literal["ready"] = "ready"
    expectedState: str
    disposalEventId: UUID
    allocations: list[ConfirmedLotAllocation]


class LotAllocationResponse(BaseModel):
    state: Literal["confirmed"] = "confirmed"
    disposalEventId: UUID
    allocations: list[ConfirmedLotAllocation]
    realisedCostNative: str | None
    realisedGainNative: str | None
    remainingBasisNative: str | None
    reviewReasons: list[Literal["incomplete_lot_allocation"]]


@dataclass(frozen=True)
class LotAllocationResult:
    response: LotAllocationResponse
    created: bool


class _Event(NamedTuple):
    kind: str
    net_amount: Decimal
    currency: str
    allocated: Decimal
    has_correction: bool


class _Cash(NamedTuple):
    amount: Decimal
    currency: str
    txn_type: str
    transfer_id: UUID | None
    updated_at: object
    has_children: bool
    has_allocation: bool


class _State(NamedTuple):
    events: dict[UUID, _Event]
    cash: dict[UUID, _Cash]


class _Lot(NamedTuple):
    holding_id: UUID
    origin_event_id: UUID
    acquisition_date: date
    acquired_units: Decimal
    cost_basis: Decimal
    allocated_units: Decimal
    has_correction: bool


class _LotState(NamedTuple):
    holding_id: UUID
    event_kind: str
    disposal_date: date
    disposed_units: Decimal
    net_amount: Decimal
    has_correction: bool
    allocated_units: Decimal
    review_status: str | None
    lots: dict[UUID, _Lot]


def _money(value: Decimal | str) -> str:
    return f"{Decimal(value):.4f}"


def _confirmed_lots(body: LotAllocationRequest) -> list[ConfirmedLotAllocation]:
    return [
        ConfirmedLotAllocation(
            acquisitionLotId=item.acquisitionLotId,
            allocatedUnits=f"{Decimal(item.allocatedUnits):.8f}",
        )
        for item in body.allocations
    ]


def _load_state(
    cur: psycopg.Cursor[Any], owner: OwnerSession, body: FundingProposalRequest, *, lock: bool
) -> _State:
    event_ids = sorted({item.investmentEventId for item in body.allocations}, key=str)
    cash_ids = sorted({item.cashTransactionId for item in body.allocations}, key=str)
    suffix = " ORDER BY id FOR UPDATE" if lock else ""
    cur.execute(
        "SELECT id FROM investment_transaction WHERE household_id = %s AND id = ANY(%s)" + suffix,
        (owner.household_id, event_ids),
    )
    if {UUID(str(row[0])) for row in cur.fetchall()} != set(event_ids):
        raise FundingError(404, "funding reference not found")
    cur.execute(
        "SELECT id FROM transaction WHERE household_id = %s AND id = ANY(%s)" + suffix,
        (owner.household_id, cash_ids),
    )
    if {UUID(str(row[0])) for row in cur.fetchall()} != set(cash_ids):
        raise FundingError(404, "funding reference not found")

    cur.execute(
        """SELECT event.id, event.event_kind::text, event.net_amount, event.currency,
                  COALESCE(sum(allocation.allocated_amount), 0),
                  EXISTS (SELECT FROM investment_transaction correction
                           WHERE correction.household_id = event.household_id
                             AND correction.corrects_transaction_id = event.id)
             FROM investment_transaction event
             LEFT JOIN investment_cash_allocation allocation
               ON allocation.household_id = event.household_id
              AND allocation.investment_transaction_id = event.id
            WHERE event.household_id = %s AND event.id = ANY(%s)
            GROUP BY event.id ORDER BY event.id""",
        (owner.household_id, event_ids),
    )
    events = {
        UUID(str(row[0])): _Event(
            row[1], Decimal(row[2]), str(row[3]).strip(), Decimal(row[4]), bool(row[5])
        )
        for row in cur.fetchall()
    }
    cur.execute(
        """SELECT cash.id, cash.amount, cash.currency, cash.txn_type::text,
                  cash.transfer_id, cash.updated_at,
                  EXISTS (SELECT FROM transaction child
                           WHERE child.parent_transaction_id = cash.id),
                  EXISTS (SELECT FROM investment_cash_allocation allocation
                           WHERE allocation.household_id = cash.household_id
                             AND allocation.cash_transaction_id = cash.id)
             FROM transaction cash
            WHERE cash.household_id = %s AND cash.id = ANY(%s)
            ORDER BY cash.id""",
        (owner.household_id, cash_ids),
    )
    cash = {
        UUID(str(row[0])): _Cash(
            Decimal(row[1]), str(row[2]).strip(), row[3], row[4], row[5], bool(row[6]), bool(row[7])
        )
        for row in cur.fetchall()
    }
    return _State(events, cash)


def _state_token(body: FundingProposalRequest, state: _State) -> str:
    facts = {
        "allocations": sorted(
            (str(item.investmentEventId), str(item.cashTransactionId), _money(item.allocatedAmount))
            for item in body.allocations
        ),
        "events": sorted(
            (
                str(key),
                value.kind,
                _money(value.net_amount),
                value.currency,
                _money(value.allocated),
                value.has_correction,
            )
            for key, value in state.events.items()
        ),
        "cash": sorted(
            (
                str(key),
                _money(value.amount),
                value.currency,
                value.txn_type,
                str(value.transfer_id or ""),
                str(value.updated_at),
                value.has_children,
                value.has_allocation,
            )
            for key, value in state.cash.items()
        ),
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def _validate(body: FundingProposalRequest, state: _State) -> None:
    cash_totals: defaultdict[UUID, Decimal] = defaultdict(Decimal)
    event_totals: defaultdict[UUID, Decimal] = defaultdict(Decimal)
    cash_roles: defaultdict[UUID, set[CashRole]] = defaultdict(set)
    for item in body.allocations:
        amount = Decimal(item.allocatedAmount)
        event = state.events[item.investmentEventId]
        cash = state.cash[item.cashTransactionId]
        role = _ROLE_BY_KIND.get(event.kind)
        cash_totals[item.cashTransactionId] += amount
        event_totals[item.investmentEventId] += amount
        if role is not None:
            cash_roles[item.cashTransactionId].add(role)
        if (
            role is None
            or event.has_correction
            or (cash.amount >= 0 if role in {"funding", "fee"} else cash.amount <= 0)
            or event.currency != cash.currency
            or cash.transfer_id is not None
            or cash.has_children
            or cash.has_allocation
            or cash.txn_type.startswith("investment_")
        ):
            raise FundingError(422, _ELIGIBILITY_ERROR)
    if any(len(roles) != 1 for roles in cash_roles.values()):
        raise FundingError(422, _ELIGIBILITY_ERROR)
    if any(total != abs(state.cash[key].amount) for key, total in cash_totals.items()):
        raise FundingError(422, _ELIGIBILITY_ERROR)
    if any(
        total > abs(state.events[key].net_amount) - state.events[key].allocated
        for key, total in event_totals.items()
    ):
        raise FundingError(422, _ELIGIBILITY_ERROR)


def _proposal_rows(body: FundingProposalRequest, state: _State) -> list[FundingProposalAllocation]:
    return [
        FundingProposalAllocation(
            investmentEventId=item.investmentEventId,
            cashTransactionId=item.cashTransactionId,
            allocatedAmount=_money(item.allocatedAmount),
            eventRemainingAmount=_money(
                abs(state.events[item.investmentEventId].net_amount)
                - state.events[item.investmentEventId].allocated
            ),
            cashAmount=_money(abs(state.cash[item.cashTransactionId].amount)),
            currency=state.cash[item.cashTransactionId].currency,
            priorTransactionType=state.cash[item.cashTransactionId].txn_type,
            role=_ROLE_BY_KIND[state.events[item.investmentEventId].kind],
        )
        for item in body.allocations
    ]


def propose_funding(owner: OwnerSession, body: FundingProposalRequest) -> FundingProposalResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        state = _load_state(cur, owner, body, lock=False)
        _validate(body, state)
        return FundingProposalResponse(
            expectedState=_state_token(body, state), allocations=_proposal_rows(body, state)
        )


def confirm_funding(
    owner: OwnerSession, body: FundingConfirmationRequest
) -> FundingConfirmationResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        state = _load_state(cur, owner, body, lock=True)
        if not compare_digest(_state_token(body, state), body.expectedState):
            raise FundingError(409, "funding proposal state changed")
        _validate(body, state)
        try:
            cur.execute(
                """SELECT confirm_investment_cash_allocations(
                     %s, %s, %s::investment_cash_allocation_role[], %s)""",
                (
                    [item.investmentEventId for item in body.allocations],
                    [item.cashTransactionId for item in body.allocations],
                    [
                        _ROLE_BY_KIND[state.events[item.investmentEventId].kind]
                        for item in body.allocations
                    ],
                    [Decimal(item.allocatedAmount) for item in body.allocations],
                ),
            )
        except psycopg.errors.InsufficientPrivilege:
            raise FundingError(404, "funding reference not found") from None
        except psycopg.errors.CheckViolation:
            raise FundingError(409, "funding proposal state changed") from None
        cur.execute(
            """SELECT allocation.id, allocation.investment_transaction_id,
                      allocation.cash_transaction_id, allocation.role::text,
                      allocation.allocated_amount, allocation.prior_txn_type::text,
                      cash.txn_type::text
                 FROM investment_cash_allocation allocation
                 JOIN transaction cash ON cash.household_id = allocation.household_id
                                      AND cash.id = allocation.cash_transaction_id
                WHERE allocation.household_id = %s
                  AND allocation.investment_transaction_id = ANY(%s)
                  AND allocation.cash_transaction_id = ANY(%s)
                ORDER BY allocation.created_at, allocation.id""",
            (
                owner.household_id,
                [item.investmentEventId for item in body.allocations],
                [item.cashTransactionId for item in body.allocations],
            ),
        )
        fields = (
            "id investmentEventId cashTransactionId role allocatedAmount "
            "priorTransactionType cashTransactionType".split()
        )
        found_allocations = [
            ConfirmedFundingAllocation(
                **dict(zip(fields, (*row[:4], _money(row[4]), *row[5:]), strict=True))
            )
            for row in cur.fetchall()
        ]
        by_pair = {
            (item.investmentEventId, item.cashTransactionId): item for item in found_allocations
        }
        allocations = [
            by_pair[(item.investmentEventId, item.cashTransactionId)] for item in body.allocations
        ]
        return FundingConfirmationResponse(allocations=allocations)


def _load_lot_state(
    cur: psycopg.Cursor[Any], owner: OwnerSession, body: LotAllocationRequest
) -> _LotState:
    cur.execute(
        """SELECT event.holding_id, event.event_kind::text, event.event_date,
                  abs(event.units), event.net_amount,
                  EXISTS (SELECT FROM investment_transaction correction
                           WHERE correction.household_id = event.household_id
                             AND correction.corrects_transaction_id = event.id),
                  COALESCE((SELECT sum(allocation.allocated_units)
                              FROM investment_lot_allocation allocation
                             WHERE allocation.household_id = event.household_id
                               AND allocation.disposal_transaction_id = event.id), 0),
                  (SELECT status::text FROM investment_review_item review
                    WHERE review.household_id = event.household_id
                      AND review.investment_transaction_id = event.id
                      AND review.reason = 'incomplete_lot_allocation')
             FROM investment_transaction event
            WHERE event.household_id = %s AND event.id = %s""",
        (owner.household_id, body.disposalEventId),
    )
    event = cur.fetchone()
    if event is None:
        raise FundingError(404, "lot allocation reference not found")
    lot_ids = [item.acquisitionLotId for item in body.allocations]
    cur.execute(
        """SELECT lot.id, lot.holding_id, lot.origin_transaction_id, lot.acquisition_date,
                  lot.acquired_units, lot.cost_basis_native,
                  COALESCE(sum(allocation.allocated_units), 0),
                  EXISTS (SELECT FROM investment_transaction correction
                           WHERE correction.household_id = origin.household_id
                             AND correction.corrects_transaction_id = origin.id)
             FROM acquisition_lot lot
             JOIN investment_transaction origin
               ON origin.household_id = lot.household_id AND origin.id = lot.origin_transaction_id
             LEFT JOIN investment_lot_allocation allocation
               ON allocation.household_id = lot.household_id
              AND allocation.acquisition_lot_id = lot.id
             LEFT JOIN investment_transaction alloc_disposal
               ON alloc_disposal.household_id = allocation.household_id
              AND alloc_disposal.id = allocation.disposal_transaction_id
              AND NOT EXISTS (
                SELECT FROM investment_transaction correction
                 WHERE correction.household_id = alloc_disposal.household_id
                   AND correction.corrects_transaction_id = alloc_disposal.id)
            WHERE lot.household_id = %s AND lot.id = ANY(%s::UUID[])
              AND (allocation.acquisition_lot_id IS NULL OR alloc_disposal.id IS NOT NULL)
            GROUP BY lot.id, origin.id ORDER BY lot.id""",
        (owner.household_id, lot_ids),
    )
    lots = {
        UUID(str(row[0])): _Lot(
            UUID(str(row[1])),
            UUID(str(row[2])),
            row[3],
            Decimal(row[4]),
            Decimal(row[5]),
            Decimal(row[6]),
            bool(row[7]),
        )
        for row in cur.fetchall()
    }
    if set(lots) != set(lot_ids):
        raise FundingError(404, "lot allocation reference not found")
    return _LotState(
        UUID(str(event[0])),
        str(event[1]),
        event[2],
        Decimal(event[3]),
        Decimal(event[4]),
        bool(event[5]),
        Decimal(event[6]),
        None if event[7] is None else str(event[7]),
        lots,
    )


def _validate_lot_state(body: LotAllocationRequest, state: _LotState) -> None:
    proposed_total = sum((Decimal(item.allocatedUnits) for item in body.allocations), Decimal(0))
    if (
        state.event_kind not in {"sale", "redemption", "maturity"}
        or state.has_correction
        or proposed_total + state.allocated_units > state.disposed_units
        or any(
            lot.holding_id != state.holding_id
            or lot.has_correction
            or lot.acquisition_date > state.disposal_date
            or Decimal(item.allocatedUnits) + lot.allocated_units > lot.acquired_units
            for item in body.allocations
            for lot in [state.lots[item.acquisitionLotId]]
        )
    ):
        raise FundingError(422, "lot allocation is not eligible")


def _lot_state_token(body: LotAllocationRequest, state: _LotState) -> str:
    facts = {
        "disposal": (
            str(body.disposalEventId),
            state.event_kind,
            state.disposal_date.isoformat(),
            f"{state.disposed_units:.8f}",
            _money(state.net_amount),
            state.has_correction,
            f"{state.allocated_units:.8f}",
            state.review_status,
        ),
        "lots": [
            (
                str(item.acquisitionLotId),
                str(state.lots[item.acquisitionLotId].origin_event_id),
                str(state.lots[item.acquisitionLotId].acquisition_date),
                f"{state.lots[item.acquisitionLotId].acquired_units:.8f}",
                _money(state.lots[item.acquisitionLotId].cost_basis),
                f"{state.lots[item.acquisitionLotId].allocated_units:.8f}",
                state.lots[item.acquisitionLotId].has_correction,
                f"{Decimal(item.allocatedUnits):.8f}",
            )
            for item in body.allocations
        ],
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def _lock_lot_state(
    cur: psycopg.Cursor[Any], owner: OwnerSession, body: LotAllocationRequest
) -> _LotState:
    try:
        cur.execute(
            "SELECT lock_investment_lot_allocation_state(%s, %s)",
            (body.disposalEventId, [item.acquisitionLotId for item in body.allocations]),
        )
    except psycopg.errors.InsufficientPrivilege:
        raise FundingError(404, "lot allocation reference not found") from None
    except psycopg.errors.CheckViolation:
        raise FundingError(422, "lot allocation is not eligible") from None
    return _load_lot_state(cur, owner, body)


def propose_lot_allocation(
    owner: OwnerSession, body: LotAllocationRequest
) -> LotAllocationProposalResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        state = _load_lot_state(cur, owner, body)
        _validate_lot_state(body, state)
        return LotAllocationProposalResponse(
            expectedState=_lot_state_token(body, state),
            disposalEventId=body.disposalEventId,
            allocations=_confirmed_lots(body),
        )


def _lot_response(
    body: LotAllocationRequest,
    realised_cost: Decimal | None,
    realised_gain: Decimal | None,
    remaining_basis: Decimal | None,
    complete: bool,
) -> LotAllocationResponse:
    return LotAllocationResponse(
        disposalEventId=body.disposalEventId,
        allocations=_confirmed_lots(body),
        realisedCostNative=None if realised_cost is None else _money(realised_cost),
        realisedGainNative=None if realised_gain is None else _money(realised_gain),
        remainingBasisNative=None if remaining_basis is None else _money(remaining_basis),
        reviewReasons=[] if complete else ["incomplete_lot_allocation"],
    )


def allocate_lots(
    owner: OwnerSession, body: LotAllocationConfirmationRequest
) -> LotAllocationResult:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{owner.household_id}:{body.idempotencyKey}",),
            )
            lot_ids = [item.acquisitionLotId for item in body.allocations]
            units = [Decimal(item.allocatedUnits) for item in body.allocations]
            cur.execute(
                "SELECT investment_lot_request_hash(%s, %s, %s, %s)",
                (body.disposalEventId, lot_ids, units, body.expectedState),
            )
            hash_row = cur.fetchone()
            if hash_row is None:
                raise RuntimeError("lot allocation operation returned no request hash")
            request_hash = str(hash_row[0])
            cur.execute(
                """SELECT request_hash, realised_cost_native, realised_gain_native,
                          remaining_basis_native, allocation_complete
                     FROM investment_lot_allocation_decision
                    WHERE household_id = %s AND idempotency_key = %s FOR UPDATE""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                if not compare_digest(str(existing[0]), request_hash):
                    raise FundingError(409, "lot allocation idempotency conflict")
                return LotAllocationResult(
                    _lot_response(
                        body,
                        None if existing[1] is None else Decimal(existing[1]),
                        None if existing[2] is None else Decimal(existing[2]),
                        None if existing[3] is None else Decimal(existing[3]),
                        bool(existing[4]),
                    ),
                    False,
                )
            try:
                state = _lock_lot_state(cur, owner, body)
            except FundingError as exc:
                if exc.status_code == 422:
                    raise FundingError(409, "lot allocation state changed") from None
                raise
            if not compare_digest(_lot_state_token(body, state), body.expectedState):
                raise FundingError(409, "lot allocation state changed")
            _validate_lot_state(body, state)
            cur.execute(
                """SELECT created, realised_cost_native, realised_gain_native,
                          remaining_basis_native, allocation_complete
                     FROM confirm_investment_lot_allocation(%s, %s, %s, %s, %s)""",
                (
                    body.disposalEventId,
                    lot_ids,
                    units,
                    body.expectedState,
                    body.idempotencyKey,
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("lot allocation operation returned no decision")
            created, realised_cost, realised_gain, remaining_basis, complete = row
            return LotAllocationResult(
                _lot_response(
                    body,
                    None if realised_cost is None else Decimal(realised_cost),
                    None if realised_gain is None else Decimal(realised_gain),
                    None if remaining_basis is None else Decimal(remaining_basis),
                    bool(complete),
                ),
                bool(created),
            )
    except psycopg.errors.InsufficientPrivilege:
        raise FundingError(404, "lot allocation reference not found") from None
    except psycopg.errors.UniqueViolation:
        raise FundingError(409, "lot allocation state changed") from None
    except psycopg.errors.CheckViolation:
        raise FundingError(422, "lot allocation is not eligible") from None


def unlink_funding(owner: OwnerSession, cash_transaction_id: UUID) -> FundingUnlinkResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            "SELECT id FROM transaction WHERE household_id = %s AND id = %s FOR UPDATE",
            (owner.household_id, cash_transaction_id),
        )
        if cur.fetchone() is None:
            raise FundingError(404, "funding allocation not found")
        cur.execute(
            """SELECT min(prior_txn_type::text), count(*)
                 FROM investment_cash_allocation
                WHERE household_id = %s AND cash_transaction_id = %s""",
            (owner.household_id, cash_transaction_id),
        )
        found = cur.fetchone()
        if found is None or found[0] is None:
            raise FundingError(404, "funding allocation not found")
        prior_type, removed_count = str(found[0]), int(found[1])
        try:
            cur.execute("SELECT unlink_investment_cash_allocations(%s)", (cash_transaction_id,))
        except psycopg.errors.InsufficientPrivilege:
            raise FundingError(404, "funding allocation not found") from None
        cur.execute(
            "SELECT txn_type::text FROM transaction WHERE household_id = %s AND id = %s",
            (owner.household_id, cash_transaction_id),
        )
        restored = cur.fetchone()
        if restored is None or restored[0] != prior_type:
            raise RuntimeError("funding unlink did not restore its recorded prior type")
        return FundingUnlinkResponse(
            cashTransactionId=cash_transaction_id,
            restoredTransactionType=prior_type,
            removedAllocationCount=removed_count,
        )
