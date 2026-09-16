"""The commitment domain shared across route modules: `routes/plans.py` (the commitments list and
forecast), `routes/actions.py` (a due/overdue occurrence as a Needs attention item),
`routes/emergency_reserve.py` (upcoming commitments against the reserve) and `routes/register.py`
(a register record's planned commitment). Hoisted out of `routes/plans.py`, which every one of
those modules imported this from at module load — `routes/actions.py` importing `routes/plans.py`
while `routes/plans.py` locally imported `routes/actions.py`'s `action_list` inside a function to
avoid the resulting cycle. Living here instead, alongside `resources.py` / `register_records.py`
/ `forecast.py` / `coverage.py`, removes both the cycle and the local import."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import psycopg

Certainty = Literal["confirmed", "estimated"]
CommitmentStatus = Literal["upcoming", "due", "overdue", "finished", "cancelled"]

# The open occurrence is "due" through this many days past `asOf` (Phase 3.1 spec "A commitment's
# lifecycle"); shared by the commitments list, the forecast and the action list.
DUE_WINDOW_DAYS = 7


def commitment_progress(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    as_of: date,
    commitments: list[tuple[UUID, datetime | None, date | None, Decimal]],
) -> dict[UUID, tuple[CommitmentStatus, bool, str, str | None]]:
    """One query over `commitment_resolution` joined to `transaction`: for each commitment (given
    as its id, `archived_at`, `next_due_date` and `amount`) its lifecycle status (spec "A
    commitment's lifecycle"), whether a payment is recorded against its open occurrence, the sum
    paid toward it and what remains. Shared by the commitments list, the resolutions router
    (after a write), the action list and the register."""
    open_ids = [cid for cid, _, next_due, _ in commitments if next_due is not None]
    open_dates = [next_due for _, _, next_due, _ in commitments if next_due is not None]
    paid_by_commitment: dict[UUID, Decimal] = {}
    if open_ids:
        cur.execute(
            """SELECT r.commitment_id, sum(t.amount)
                 FROM commitment_resolution r
                 JOIN v_transaction_posting t
                   ON t.household_id = r.household_id AND t.id = r.transaction_id
                 JOIN unnest(%s::uuid[], %s::date[]) AS open_next(commitment_id, next_due_date)
                   ON open_next.commitment_id = r.commitment_id
                  AND open_next.next_due_date = r.due_date
                WHERE r.household_id = %s AND r.kind = 'payment'
                GROUP BY r.commitment_id""",
            (open_ids, open_dates, household_id),
        )
        for commitment_id, total in cur.fetchall():
            paid_by_commitment[UUID(str(commitment_id))] = Decimal(total)

    result: dict[UUID, tuple[CommitmentStatus, bool, str, str | None]] = {}
    for commitment_id, archived_at, next_due, amount in commitments:
        status: CommitmentStatus
        if archived_at is not None:
            status = "cancelled"
        elif next_due is None:
            status = "finished"
        elif next_due < as_of:
            status = "overdue"
        elif next_due <= as_of + timedelta(days=DUE_WINDOW_DAYS):
            status = "due"
        else:
            status = "upcoming"

        if next_due is None:
            paid_toward_next = Decimal("0.0000")
            partly_paid = False
            remaining_next: str | None = None
        else:
            paid_toward_next = paid_by_commitment.get(commitment_id, Decimal("0.0000"))
            partly_paid = commitment_id in paid_by_commitment
            remaining_next = (
                str(amount - paid_toward_next) if abs(paid_toward_next) < abs(amount) else "0.0000"
            )
        result[commitment_id] = (status, partly_paid, str(paid_toward_next), remaining_next)
    return result
