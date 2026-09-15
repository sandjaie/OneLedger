"""Phase 3.4 Task 1: register record reads `routes/actions.py`'s `action_list` needs — kept here,
not in `routes/register.py`, so `routes/actions.py` still imports no route module (the Phase 3.2
import-cycle deviation `resources.py` already follows for goals and the emergency reserve).
`routes/register.py` imports `review_due` from here too, so the one rule that decides whether a
record is due for review is written once."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

RENEWAL_WINDOW_DAYS = 30
MATURITY_WINDOW_DAYS = 30
DEADLINE_WINDOW_DAYS = 14
REVIEW_DUE_DAYS = 365

# Spec "Register (V53)": only these three kinds carry a review date — a claim or an administration
# deadline closes instead of being reviewed.
_REVIEWED_KINDS = frozenset({"insurance_policy", "deposit", "nominee"})


def review_due(kind: str, last_reviewed_on: date | None, created_at: datetime, as_of: date) -> bool:
    """Spec `RegisterRow.reviewDue`: `coalesce(last_reviewed_on, created_at::date)` more than 365
    days before `asOf`, so a record just created is never due at once."""
    if kind not in _REVIEWED_KINDS:
        return False
    reviewed_from = last_reviewed_on or created_at.date()
    return reviewed_from < as_of - timedelta(days=REVIEW_DUE_DAYS)


@dataclass(frozen=True)
class RegisterActionItem:
    recordId: UUID
    subKind: str  # "renewal" | "maturity" | "deadline" | "review"
    title: str
    dueDate: date | None
    overdue: bool
    accountId: UUID | None
    accountName: str | None
    amount: Decimal | None
    currency: str | None


_RECORDS = """
    SELECT r.id, r.kind::text, r.title, r.key_date, r.premium_amount, r.currency, r.account_id,
           a.name, r.completed_at, r.last_reviewed_on, r.created_at,
           EXISTS (SELECT FROM commitment c WHERE c.household_id = r.household_id
                     AND c.source_register_record_id = r.id AND c.archived_at IS NULL) AS has_plan
      FROM register_record r
      LEFT JOIN account a ON a.household_id = r.household_id AND a.id = r.account_id
     WHERE r.household_id = %s AND r.archived_at IS NULL"""


def register_action_items(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date
) -> list[RegisterActionItem]:
    """`action_list`'s four record item kinds (spec `action_list`, items 1-4): a policy's renewal,
    a deposit's maturity, an incomplete administration deadline, and a review due — each within its
    own window, an unarchived record only. Only the renewal item carries an amount (the premium);
    Task 2 omits the maturity item once an unarchived commitment is sourced from the record."""
    items: list[RegisterActionItem] = []
    cur.execute(_RECORDS, (household_id,))
    for (
        rid,
        kind,
        title,
        key_date,
        premium_amount,
        currency,
        account_id,
        account_name,
        completed_at,
        last_reviewed_on,
        created_at,
        has_plan,
    ) in cur.fetchall():
        record_id = UUID(str(rid))
        account_uuid = None if account_id is None else UUID(str(account_id))
        if kind == "insurance_policy" and key_date <= as_of + timedelta(days=RENEWAL_WINDOW_DAYS):
            items.append(
                RegisterActionItem(
                    recordId=record_id,
                    subKind="renewal",
                    title=title,
                    dueDate=key_date,
                    overdue=key_date < as_of,
                    accountId=account_uuid,
                    accountName=account_name,
                    amount=None if premium_amount is None else Decimal(premium_amount),
                    currency=str(currency).strip() if currency is not None else None,
                )
            )
        elif (
            kind == "deposit"
            and not has_plan
            and key_date <= as_of + timedelta(days=MATURITY_WINDOW_DAYS)
        ):
            items.append(
                RegisterActionItem(
                    recordId=record_id,
                    subKind="maturity",
                    title=title,
                    dueDate=key_date,
                    overdue=key_date < as_of,
                    accountId=account_uuid,
                    accountName=account_name,
                    amount=None,
                    currency=None,
                )
            )
        elif (
            kind == "administration"
            and completed_at is None
            and key_date <= as_of + timedelta(days=DEADLINE_WINDOW_DAYS)
        ):
            items.append(
                RegisterActionItem(
                    recordId=record_id,
                    subKind="deadline",
                    title=title,
                    dueDate=key_date,
                    overdue=key_date < as_of,
                    accountId=None,
                    accountName=None,
                    amount=None,
                    currency=None,
                )
            )
        if review_due(kind, last_reviewed_on, created_at, as_of):
            items.append(
                RegisterActionItem(
                    recordId=record_id,
                    subKind="review",
                    title=title,
                    dueDate=None,
                    overdue=False,
                    accountId=account_uuid,
                    accountName=account_name,
                    amount=None,
                    currency=None,
                )
            )
    return items
