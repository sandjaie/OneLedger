from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from hmac import compare_digest
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from ..auth import OwnerSession
from ..db import connect, scope_to_owner

_MONEY_PATTERN = r"^\d{1,16}(?:\.\d{1,4})?$"
_ELIGIBILITY_ERROR = "funding allocation is not a full eligible cash-leaf decision"


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


class FundingProposalResponse(BaseModel):
    state: Literal["ready"] = "ready"
    expectedState: str
    allocations: list[FundingProposalAllocation]


class ConfirmedFundingAllocation(BaseModel):
    id: UUID
    investmentEventId: UUID
    cashTransactionId: UUID
    role: Literal["funding"]
    allocatedAmount: str
    priorTransactionType: str
    cashTransactionType: Literal["investment_buy"]


class FundingConfirmationResponse(BaseModel):
    state: Literal["confirmed"] = "confirmed"
    allocations: list[ConfirmedFundingAllocation]


class FundingUnlinkResponse(BaseModel):
    state: Literal["unlinked"] = "unlinked"
    cashTransactionId: UUID
    restoredTransactionType: str
    removedAllocationCount: int


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


def _money(value: Decimal | str) -> str:
    return f"{Decimal(value):.4f}"


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
    for item in body.allocations:
        amount = Decimal(item.allocatedAmount)
        event = state.events[item.investmentEventId]
        cash = state.cash[item.cashTransactionId]
        cash_totals[item.cashTransactionId] += amount
        event_totals[item.investmentEventId] += amount
        if (
            event.kind not in {"purchase", "recurring_purchase"}
            or event.net_amount >= 0
            or event.has_correction
            or cash.amount >= 0
            or event.currency != cash.currency
            or cash.transfer_id is not None
            or cash.has_children
            or cash.has_allocation
            or cash.txn_type.startswith("investment_")
        ):
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
                    ["funding"] * len(body.allocations),
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
        allocations = [
            ConfirmedFundingAllocation(
                **dict(zip(fields, (*row[:4], _money(row[4]), *row[5:]), strict=True))
            )
            for row in cur.fetchall()
        ]
        return FundingConfirmationResponse(allocations=allocations)


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
