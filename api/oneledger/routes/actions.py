"""Phase 3.1 Task 2: one Needs attention list gathering every item that asks the owner to act — a
commitment occurrence due within `commitments.DUE_WINDOW_DAYS` days or overdue, a card statement
with an amount due and no planned payment, and owner-entered follow-ups. `action_list()` builds
all three kinds from one household's data and is the single place Phase 3.2 will extend (spec
"Card dues and actions (Task 2)"); every write here still locks the household row first, the same
convention as `routes/plans.py` and `routes/commitment_resolutions.py`. A commitment item's own
completion and dismissal are its occurrence's resolution (`POST /commitments/{id}/resolutions`,
Task 1) — no route here writes `commitment_resolution`; a card item's are planning its payment (it
becomes a commitment item) or a dismissal mark. `action_mark` is the one further lifecycle every
kind shares: snooze (any kind) and dismiss (card only, the database's own
`action_mark_dismissal_is_card` check)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, model_validator

from ..auth import OwnerSession, require_owner
from ..commitments import DUE_WINDOW_DAYS, Certainty, commitment_progress
from ..coverage import previous_month
from ..db import IDEMPOTENCY_REUSED, connect
from ..emergency import coverage as emergency_coverage
from ..register_records import register_action_items
from ..resources import (
    account_native_values,
    eligible_account_ids,
    eligible_native_and_base,
    emergency_baseline,
    goal_shortfalls,
    household_currencies,
    household_reservations,
    read_emergency_reserve_settings,
)
from ..text import normalize_bounded

router = APIRouter(tags=["actions"])

ActionKind = Literal["commitment", "card_due", "follow_up", "goal", "reserve", "record"]
ActionReason = Literal[
    "commitment_overdue",
    "commitment_due",
    "card_payment_unplanned",
    "follow_up",
    "goal_shortfall",
    "reserve_below_target",
    "renewal_due",
    "maturity_due",
    "deadline_due",
    "review_due",
]
_RECORD_REASON: dict[str, ActionReason] = {
    "renewal": "renewal_due",
    "maturity": "maturity_due",
    "deadline": "deadline_due",
    "review": "review_due",
}
FollowUpState = Literal["open", "completed", "dismissed"]

_SNOOZE_MAX_DAYS = 366
_RESOLVED_FOLLOW_UP_LIMIT = 20

_MALFORMED_KEY = "this is not a valid action key"
_REASON_IS_CARD_ONLY = "a snooze reason applies to a card action only"
_SNOOZE_WINDOW = f"snooze date must be after today and within {_SNOOZE_MAX_DAYS} days"

# Mirrors V52's action_mark_key_shape CHECK in db/migrations/V52__emergency_reserve.sql — keep
# both in sync. goal./reserve. are Phase 3.3's items; record.* are Phase 3.4's register items.
_ACTION_KEY_RE = re.compile(
    r"^(?:follow-up\.(?P<followup_id>[0-9a-f-]{36})"
    r"|commitment\.(?P<commitment_id>[0-9a-f-]{36})\.(?P<due_date>\d{4}-\d{2}-\d{2})"
    r"|card\.(?P<card_id>[0-9a-f-]{36})"
    r"|goal\.(?P<goal_id>[0-9a-f-]{36})"
    r"|reserve\.(?P<reserve_id>[0-9a-f-]{36})"
    r"|record\.(?P<record_id>[0-9a-f-]{36})\.(?:review"
    r"|(?:renewal|maturity|deadline)\.(?P<record_date>\d{4}-\d{2}-\d{2})))$",
    re.ASCII,
)


def _normalize_reason(value: str) -> str:
    return normalize_bounded(value, "reason", 200)


def _normalize_title(value: str) -> str:
    return normalize_bounded(value, "title", 120)


class ActionRow(BaseModel):
    actionKey: str
    kind: ActionKind
    reason: ActionReason
    title: str
    dueDate: date | None
    overdue: bool
    snoozedUntil: date | None
    accountId: UUID | None
    accountName: str | None
    amount: str | None
    remaining: str | None
    partlyPaid: bool
    currency: str | None
    certainty: Certainty | None
    commitmentId: UUID | None
    cardStatementId: UUID | None
    actionItemId: UUID | None
    registerRecordId: UUID | None


class FollowUpRow(BaseModel):
    id: UUID
    title: str
    dueDate: date | None
    accountId: UUID | None
    accountName: str | None
    state: FollowUpState
    dismissReason: str | None
    resolvedAt: datetime | None
    createdAt: datetime
    updatedAt: datetime


class PreviousMonthReview(BaseModel):
    """Phase 3.2: whether the calendar month before `asOf` is marked reviewed, from one
    `monthly_review` primary-key read — so Overview shows the review state without running the
    review's own six-step computation."""

    month: date
    completedAt: datetime | None


class ActionsResponse(BaseModel):
    asOf: date
    items: list[ActionRow]
    snoozed: list[ActionRow]
    resolvedFollowUps: list[FollowUpRow]
    previousMonthReview: PreviousMonthReview


class FollowUpCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    dueDate: date | None = None
    accountId: UUID | None = None
    idempotencyKey: UUID

    @model_validator(mode="after")
    def _title(self) -> FollowUpCreate:
        self.title = _normalize_title(self.title)
        return self


class FollowUpDismiss(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str

    @model_validator(mode="after")
    def _reason(self) -> FollowUpDismiss:
        self.reason = _normalize_reason(self.reason)
        return self


class MarkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    until: date | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> MarkBody:
        if (self.until is None) == (self.reason is None):
            raise ValueError("provide exactly one of until or reason")
        if self.reason is not None:
            self.reason = _normalize_reason(self.reason)
        return self


def _sort_key(row: ActionRow) -> tuple[int, bool, date, str]:
    """Spec "Card dues and actions (Task 2)": overdue first, then dueDate ascending with no date
    last, then title."""
    return (
        0 if row.overdue else 1,
        row.dueDate is None,
        row.dueDate or date.max,
        row.title,
    )


_COMMITMENT_ACTION_ITEMS = """
    SELECT c.id, c.account_id, a.name, c.label, c.amount, c.currency, c.next_due_date,
           c.certainty::text
      FROM commitment c
      JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
     WHERE c.household_id = %s AND c.archived_at IS NULL AND c.next_due_date IS NOT NULL
       AND c.next_due_date <= %s"""

# The latest cycle per active credit-card account dated on or before asOf, then spec item 2's own
# membership rule (an amount actually due, not already planned): a newer statement supersedes an
# older cycle even when the newer one has nothing due. The dismissal clause is appended only when
# marks apply (see action_list()).
_CARD_ACTION_ITEMS = """
    SELECT latest.id, latest.account_id, latest.name, latest.currency, latest.payment_due_date,
           latest.statement_balance
      FROM (
        SELECT DISTINCT ON (c.account_id)
               c.id, c.account_id, a.name, c.currency, c.payment_due_date, c.statement_balance,
               c.is_paid_in_full
          FROM card_statement c
          JOIN account a ON a.household_id = c.household_id AND a.id = c.account_id
         WHERE c.household_id = %s AND a.is_active AND c.statement_date <= %s
         ORDER BY c.account_id, c.period_end DESC
      ) latest
     WHERE latest.statement_balance > 0 AND latest.is_paid_in_full IS NOT TRUE
       AND latest.payment_due_date >= %s
       AND NOT EXISTS (
       SELECT FROM commitment m
        WHERE m.household_id = %s AND m.source_card_statement_id = latest.id
          AND m.archived_at IS NULL)"""
_CARD_NOT_DISMISSED = """
       AND NOT EXISTS (
       SELECT FROM action_mark k
        WHERE k.household_id = %s AND k.action_key = 'card.' || latest.id::text
          AND k.dismiss_reason IS NOT NULL)"""

_FOLLOWUP_ACTION_ITEMS = """
    SELECT i.id, i.title, i.due_date, i.account_id, a.name
      FROM action_item i
      LEFT JOIN account a ON a.household_id = i.household_id AND a.id = i.account_id
     WHERE i.household_id = %s AND i.state = 'open'"""


def action_list(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date, *, apply_marks: bool
) -> list[ActionRow]:
    """Every open action item — a commitment occurrence, a card due, a follow-up — as one list
    (spec "Card dues and actions (Task 2)" items 1-3). With `apply_marks` True (`GET /actions`) a
    dismissed card due is omitted and each item carries `snoozedUntil` from its snooze mark; the
    caller splits `items`/`snoozed` on that. With `apply_marks` False (Phase 3.2 spec rule 8) no
    mark is read: every item is listed, unsnoozed, whether snoozed or dismissed."""
    marks: dict[str, date] = {}
    if apply_marks:
        cur.execute(
            """SELECT action_key, snoozed_until FROM action_mark
                WHERE household_id = %s AND snoozed_until IS NOT NULL""",
            (household_id,),
        )
        marks = {key: until for key, until in cur.fetchall()}

    rows: list[ActionRow] = []

    cur.execute(_COMMITMENT_ACTION_ITEMS, (household_id, as_of + timedelta(days=DUE_WINDOW_DAYS)))
    commitment_rows = cur.fetchall()
    progress = commitment_progress(
        cur, household_id, as_of, [(r[0], None, r[6], Decimal(r[4])) for r in commitment_rows]
    )
    for row in commitment_rows:
        cid, account_id, account_name, label, amount, currency, next_due, certainty = row
        commitment_id = UUID(str(cid))
        _status, partly_paid, _paid, remaining = progress[commitment_id]
        overdue = next_due < as_of
        action_key = f"commitment.{commitment_id}.{next_due.isoformat()}"
        rows.append(
            ActionRow(
                actionKey=action_key,
                kind="commitment",
                reason="commitment_overdue" if overdue else "commitment_due",
                title=label,
                dueDate=next_due,
                overdue=overdue,
                snoozedUntil=marks.get(action_key),
                accountId=UUID(str(account_id)),
                accountName=account_name,
                amount=str(amount),
                remaining=remaining,
                partlyPaid=partly_paid,
                currency=str(currency).strip(),
                certainty=certainty,
                commitmentId=commitment_id,
                cardStatementId=None,
                actionItemId=None,
                registerRecordId=None,
            )
        )

    card_params: tuple[object, ...] = (
        household_id,
        as_of,
        as_of - timedelta(days=DUE_WINDOW_DAYS),
        household_id,
    )
    if apply_marks:
        cur.execute(_CARD_ACTION_ITEMS + _CARD_NOT_DISMISSED, (*card_params, household_id))
    else:
        cur.execute(_CARD_ACTION_ITEMS, card_params)
    for sid, account_id, account_name, currency, due_date, balance in cur.fetchall():
        statement_id = UUID(str(sid))
        overdue = due_date < as_of
        action_key = f"card.{statement_id}"
        amount = str(Decimal(balance))
        rows.append(
            ActionRow(
                actionKey=action_key,
                kind="card_due",
                reason="card_payment_unplanned",
                title=account_name,
                dueDate=due_date,
                overdue=overdue,
                snoozedUntil=marks.get(action_key),
                accountId=UUID(str(account_id)),
                accountName=account_name,
                amount=amount,
                remaining=amount,
                partlyPaid=False,
                currency=str(currency).strip(),
                certainty=None,
                commitmentId=None,
                cardStatementId=statement_id,
                actionItemId=None,
                registerRecordId=None,
            )
        )

    cur.execute(_FOLLOWUP_ACTION_ITEMS, (household_id,))
    for iid, title, due_date, account_id, account_name in cur.fetchall():
        item_id = UUID(str(iid))
        overdue = due_date is not None and due_date < as_of
        action_key = f"follow-up.{item_id}"
        rows.append(
            ActionRow(
                actionKey=action_key,
                kind="follow_up",
                reason="follow_up",
                title=title,
                dueDate=due_date,
                overdue=overdue,
                snoozedUntil=marks.get(action_key),
                accountId=UUID(str(account_id)) if account_id is not None else None,
                accountName=account_name,
                amount=None,
                remaining=None,
                partlyPaid=False,
                currency=None,
                certainty=None,
                commitmentId=None,
                cardStatementId=None,
                actionItemId=item_id,
                registerRecordId=None,
            )
        )

    base, secondary = household_currencies(cur, household_id)

    for shortfall in goal_shortfalls(cur, household_id, as_of, base, secondary):
        action_key = f"goal.{shortfall.goalId}"
        overdue = shortfall.targetDate < as_of
        rows.append(
            ActionRow(
                actionKey=action_key,
                kind="goal",
                reason="goal_shortfall",
                title=shortfall.name,
                dueDate=shortfall.targetDate,
                overdue=overdue,
                snoozedUntil=marks.get(action_key),
                accountId=None,
                accountName=None,
                amount=str(shortfall.shortfall),
                remaining=str(shortfall.shortfall),
                partlyPaid=False,
                currency=shortfall.currency,
                certainty=None,
                commitmentId=None,
                cardStatementId=None,
                actionItemId=None,
                registerRecordId=None,
            )
        )

    reserve_gap = _reserve_gap(cur, household_id, as_of, base, secondary)
    if reserve_gap is not None:
        action_key = f"reserve.{household_id}"
        rows.append(
            ActionRow(
                actionKey=action_key,
                kind="reserve",
                reason="reserve_below_target",
                title="Emergency reserve",
                dueDate=None,
                overdue=False,
                snoozedUntil=marks.get(action_key),
                accountId=None,
                accountName=None,
                amount=str(reserve_gap),
                remaining=str(reserve_gap),
                partlyPaid=False,
                currency=base,
                certainty=None,
                commitmentId=None,
                cardStatementId=None,
                actionItemId=None,
                registerRecordId=None,
            )
        )

    # Phase 3.4 Task 1: a policy's renewal, a deposit's maturity, an incomplete administration
    # deadline, and a review due (spec `action_list`, items 1-4) — register_records.py computes the
    # windows and the review rule so this module reads no register_record row directly.
    for item in register_action_items(cur, household_id, as_of):
        action_key = (
            f"record.{item.recordId}.review"
            if item.subKind == "review"
            else f"record.{item.recordId}.{item.subKind}.{item.dueDate.isoformat()}"  # type: ignore[union-attr]
        )
        rows.append(
            ActionRow(
                actionKey=action_key,
                kind="record",
                reason=_RECORD_REASON[item.subKind],
                title=item.title,
                dueDate=item.dueDate,
                overdue=item.overdue,
                snoozedUntil=marks.get(action_key),
                accountId=item.accountId,
                accountName=item.accountName,
                amount=None if item.amount is None else str(item.amount),
                remaining=None if item.amount is None else str(item.amount),
                partlyPaid=False,
                currency=item.currency,
                certainty=None,
                commitmentId=None,
                cardStatementId=None,
                actionItemId=None,
                registerRecordId=item.recordId,
            )
        )

    return rows


def _reserve_gap(
    cur: psycopg.Cursor[Any], household_id: UUID, as_of: date, base: str, secondary: str
) -> Decimal | None:
    """The emergency reserve's `gapBase` when configured, coverage is available and coverage
    falls short of the target (spec `action_list` "reserve.<householdId>" item) — `None`
    otherwise, so `action_list` adds no item."""
    settings = read_emergency_reserve_settings(cur, household_id)
    if settings is None:
        return None
    baseline = emergency_baseline(cur, household_id, settings, as_of)
    if baseline.status != "available":
        return None

    # No eligible account is zero coverage, the whole target short — still an item.
    values = account_native_values(cur, household_id, base, secondary, as_of)
    active_reservations = household_reservations(cur, household_id, active_only=True)
    eligible_bases: list[Decimal | None] = []
    for account_id in eligible_account_ids(cur, household_id):
        native = values.get(account_id)
        if native is None or native.value is None:
            continue
        *_reserved, eligible_base = eligible_native_and_base(
            cur, account_id, native, active_reservations, base, secondary, as_of
        )
        eligible_bases.append(eligible_base)

    result = emergency_coverage(
        baseline=baseline,
        target_months=settings.targetMonths,
        eligible_bases=eligible_bases,
        emergency_reserved_base=Decimal("0.0000"),
    )
    if (
        result.status == "available"
        and result.coverageMonths is not None
        and result.coverageMonths < settings.targetMonths
        and result.gapBase is not None
        and result.gapBase > 0
    ):
        return result.gapBase
    return None


def _followup_row(row: tuple[Any, ...]) -> FollowUpRow:
    return FollowUpRow(
        id=row[0],
        title=row[1],
        dueDate=row[2],
        accountId=row[3],
        accountName=row[4],
        state=row[5],
        dismissReason=row[6],
        resolvedAt=row[7],
        createdAt=row[8],
        updatedAt=row[9],
    )


_FOLLOWUP_COLUMNS = """i.id, i.title, i.due_date, i.account_id, a.name, i.state::text,
                        i.dismiss_reason, i.resolved_at, i.created_at, i.updated_at"""
_FOLLOWUP_FROM = """FROM action_item i
                     LEFT JOIN account a
                       ON a.household_id = i.household_id AND a.id = i.account_id"""


def _followup(cur: psycopg.Cursor[Any], household_id: UUID, item_id: UUID) -> FollowUpRow:
    cur.execute(
        f"SELECT {_FOLLOWUP_COLUMNS} {_FOLLOWUP_FROM} WHERE i.household_id = %s AND i.id = %s",
        (household_id, item_id),
    )
    row = cur.fetchone()
    assert row is not None
    return _followup_row(row)


def _translate_action_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_action_item_idempotency":
        return HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
    if constraint == "action_item_title_is_bounded":
        return HTTPException(status_code=422, detail="title must be 1-120 characters")
    if constraint == "fk_action_item_account_scope":
        return HTTPException(status_code=404, detail="account not found")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(status_code=422, detail="follow-up violates its required field matrix")
    raise exc


@router.get("/actions", response_model=ActionsResponse)
def list_actions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: date | None = None,
) -> ActionsResponse:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        rows = action_list(cur, owner.household_id, as_of, apply_marks=True)
        items = sorted(
            (r for r in rows if r.snoozedUntil is None or r.snoozedUntil <= as_of), key=_sort_key
        )
        snoozed = sorted(
            (r for r in rows if r.snoozedUntil is not None and r.snoozedUntil > as_of),
            key=_sort_key,
        )
        cur.execute(
            f"""SELECT {_FOLLOWUP_COLUMNS} {_FOLLOWUP_FROM}
                 WHERE i.household_id = %s AND i.state IN ('completed', 'dismissed')
                 ORDER BY i.resolved_at DESC LIMIT %s""",
            (owner.household_id, _RESOLVED_FOLLOW_UP_LIMIT),
        )
        resolved = [_followup_row(row) for row in cur.fetchall()]

        review_month = previous_month(as_of)
        cur.execute(
            "SELECT completed_at FROM monthly_review WHERE household_id = %s AND review_month = %s",
            (owner.household_id, review_month),
        )
        found = cur.fetchone()
        previous_review = PreviousMonthReview(
            month=review_month, completedAt=found[0] if found else None
        )
    return ActionsResponse(
        asOf=as_of,
        items=items,
        snoozed=snoozed,
        resolvedFollowUps=resolved,
        previousMonthReview=previous_review,
    )


@router.post("/actions/follow-ups", response_model=FollowUpRow)
def create_follow_up(
    body: FollowUpCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> FollowUpRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT id, title, due_date, account_id FROM action_item
                    WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                eid, etitle, edue, eaccount = existing
                if (
                    etitle == body.title
                    and edue == body.dueDate
                    and (UUID(str(eaccount)) if eaccount is not None else None) == body.accountId
                ):
                    response.status_code = status.HTTP_200_OK
                    return _followup(cur, owner.household_id, UUID(str(eid)))
                raise HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
            if body.accountId is not None:
                cur.execute(
                    "SELECT 1 FROM account WHERE household_id = %s AND id = %s",
                    (owner.household_id, body.accountId),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="account not found")
            cur.execute(
                """INSERT INTO action_item (household_id, idempotency_key, title, due_date,
                                             account_id)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (owner.household_id, body.idempotencyKey, body.title, body.dueDate, body.accountId),
            )
            created = cur.fetchone()
            assert created is not None
            response.status_code = status.HTTP_201_CREATED
            return _followup(cur, owner.household_id, UUID(str(created[0])))
    except psycopg.Error as exc:
        raise _translate_action_error(exc) from None


def _locked_follow_up(
    cur: psycopg.Cursor[Any], household_id: UUID, item_id: UUID
) -> tuple[str, str | None]:
    cur.execute(
        "SELECT state::text, dismiss_reason FROM action_item WHERE household_id = %s AND id = %s "
        "FOR UPDATE",
        (household_id, item_id),
    )
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="follow-up not found")
    return found[0], found[1]


@router.post("/actions/follow-ups/{action_item_id}/complete", response_model=FollowUpRow)
def complete_follow_up(
    action_item_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> FollowUpRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        state, _reason = _locked_follow_up(cur, owner.household_id, action_item_id)
        if state == "dismissed":
            raise HTTPException(status_code=409, detail="a dismissed follow-up cannot be completed")
        if state == "open":
            cur.execute(
                """UPDATE action_item SET state = 'completed', resolved_at = now(),
                          updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (owner.household_id, action_item_id),
            )
        return _followup(cur, owner.household_id, action_item_id)


@router.post("/actions/follow-ups/{action_item_id}/dismiss", response_model=FollowUpRow)
def dismiss_follow_up(
    action_item_id: UUID,
    body: FollowUpDismiss,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> FollowUpRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        state, reason = _locked_follow_up(cur, owner.household_id, action_item_id)
        if state == "completed":
            raise HTTPException(status_code=409, detail="a completed follow-up cannot be dismissed")
        if state == "dismissed":
            if reason != body.reason:
                raise HTTPException(
                    status_code=409,
                    detail="this follow-up is already dismissed with a different reason",
                )
            return _followup(cur, owner.household_id, action_item_id)
        cur.execute(
            """UPDATE action_item SET state = 'dismissed', dismiss_reason = %s,
                      resolved_at = now(), updated_at = now()
                WHERE household_id = %s AND id = %s""",
            (body.reason, owner.household_id, action_item_id),
        )
        return _followup(cur, owner.household_id, action_item_id)


@router.post("/actions/follow-ups/{action_item_id}/reopen", response_model=FollowUpRow)
def reopen_follow_up(
    action_item_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> FollowUpRow:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        state, _reason = _locked_follow_up(cur, owner.household_id, action_item_id)
        if state != "open":
            cur.execute(
                """UPDATE action_item SET state = 'open', dismiss_reason = NULL,
                          resolved_at = NULL, updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (owner.household_id, action_item_id),
            )
        return _followup(cur, owner.household_id, action_item_id)


def _parse_action_key(action_key: str) -> re.Match[str]:
    match = _ACTION_KEY_RE.match(action_key)
    if match is None:
        raise HTTPException(status_code=422, detail=_MALFORMED_KEY)
    # The pattern admits any 36 of [0-9a-f-] and any 4-2-2 digits; only the canonical UUID text
    # (what GET /actions emits) and a real calendar date name an item.
    try:
        item_id = (
            match["followup_id"]
            or match["commitment_id"]
            or match["card_id"]
            or match["goal_id"]
            or match["reserve_id"]
            or match["record_id"]
        )
        canonical = item_id is not None and str(UUID(item_id)) == item_id
        for group in ("due_date", "record_date"):
            if match[group] is not None:
                date.fromisoformat(match[group])
    except ValueError:
        canonical = False
    if not canonical:
        raise HTTPException(status_code=422, detail=_MALFORMED_KEY)
    return match


def _assert_key_target_exists(
    cur: psycopg.Cursor[Any], household_id: UUID, match: re.Match[str]
) -> None:
    """404 unless the marked object exists in the household (spec "Card dues and actions (Task
    2)", widened by "Emergency reserve and action keys (V52)" and Phase 3.4's register): a
    follow-up by id, a commitment whose `next_due_date` is still the key's date, a card statement
    by id, an active goal, the owner's own household id for `reserve.`, or an unarchived register
    record whose `key_date` still matches a dated `record.` key (spec "snooze key target
    existence")."""
    if match["followup_id"] is not None:
        cur.execute(
            "SELECT 1 FROM action_item WHERE household_id = %s AND id = %s",
            (household_id, UUID(match["followup_id"])),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="follow-up not found")
    elif match["commitment_id"] is not None:
        cur.execute(
            """SELECT 1 FROM commitment WHERE household_id = %s AND id = %s
                AND archived_at IS NULL AND next_due_date = %s""",
            (household_id, UUID(match["commitment_id"]), date.fromisoformat(match["due_date"])),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="commitment not found")
    elif match["card_id"] is not None:
        cur.execute(
            "SELECT 1 FROM card_statement WHERE household_id = %s AND id = %s",
            (household_id, UUID(match["card_id"])),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="card statement not found")
    elif match["goal_id"] is not None:
        cur.execute(
            "SELECT 1 FROM goal WHERE household_id = %s AND id = %s AND state = 'active'",
            (household_id, UUID(match["goal_id"])),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="goal not found")
    elif match["reserve_id"] is not None:
        if UUID(match["reserve_id"]) != household_id:
            raise HTTPException(status_code=404, detail="reserve not found")
    else:
        record_id = UUID(match["record_id"])
        if match["record_date"] is not None:
            cur.execute(
                """SELECT 1 FROM register_record WHERE household_id = %s AND id = %s
                    AND archived_at IS NULL AND key_date = %s""",
                (household_id, record_id, date.fromisoformat(match["record_date"])),
            )
        else:
            cur.execute(
                "SELECT 1 FROM register_record WHERE household_id = %s AND id = %s "
                "AND archived_at IS NULL",
                (household_id, record_id),
            )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="record not found")


@router.put("/actions/marks/{action_key}", status_code=204)
def put_mark(
    action_key: str, body: MarkBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    match = _parse_action_key(action_key)
    if body.reason is not None and match["card_id"] is None:
        raise HTTPException(status_code=422, detail=_REASON_IS_CARD_ONLY)
    if body.until is not None:
        today = date.today()
        if body.until <= today or body.until > today + timedelta(days=_SNOOZE_MAX_DAYS):
            raise HTTPException(status_code=422, detail=_SNOOZE_WINDOW)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        _assert_key_target_exists(cur, owner.household_id, match)
        cur.execute(
            """INSERT INTO action_mark (household_id, action_key, snoozed_until, dismiss_reason)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (household_id, action_key) DO UPDATE
                 SET snoozed_until = excluded.snoozed_until,
                     dismiss_reason = excluded.dismiss_reason,
                     updated_at = now()""",
            (owner.household_id, action_key, body.until, body.reason),
        )
    return Response(status_code=204)


@router.delete("/actions/marks/{action_key}", status_code=204)
def delete_mark(
    action_key: str, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    _parse_action_key(action_key)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "DELETE FROM action_mark WHERE household_id = %s AND action_key = %s",
            (owner.household_id, action_key),
        )
    return Response(status_code=204)
