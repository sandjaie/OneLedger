# api/oneledger/routes/recurring.py
"""Phase 4.1 Task 1: recurring discovery is opt-out (spec "Recurring payments are simply just not
a subscription", `docs/superpowers/plans/2026-09-16-phase-4-1-subscriptions.md` Task 1). Phase 4.1
Task 5 (`docs/superpowers/plans/2026-09-16-phase-4-1-subscription-registry.md`) adds billing
periods, the owner's lifecycle and custom cadence, cancel, resume, automatic lapsing, and a
subscription added from one charge.

`POST /recurring/refresh` is the only writer, and the owner never confirms a detection. It takes
the household lock first, then applies three rules in order:

  * **Sync** — every open series (ended_on IS NULL — V58: "removed as not recurring" only) has its
    `native_amount`/`native_currency`/`expected_amount_base`/`last_seen_on` moved to its latest
    member; a series whose latest period is open, not `paused`, and stale by more than two
    cadences from its anchor has that period closed `lapsed`; and, only where
    `subscription_set_by_owner` is false, `is_subscription` is recomputed.
  * **Extend** — a reportable charge on an identity and currency not yet in any series joins the
    series its amount is within 20% of, preferring a series whose latest period is open, then the
    nearest amount, then the later `last_seen_on`, then `id`; failing that, a charge outside every
    band may join the one *current* series on that identity and currency (or the only series at
    all, when none is current) as a price change, gated by Task 4's own timing and dismissal
    rules. Joining a series also advances its billing period: extends the open one, lapses and
    reopens after a long gap, restores a lapsed period a late charge actually paid on time, or
    opens a fresh one after a cancellation.
  * **Create** — `oneledger.recurring.detect_candidates` runs over charges still unassigned after
    Extend, within the 26-month lookback window. Each resulting group becomes a series, with one
    open billing period starting at its first charge, unless a dismissal on its identity refuses it.
  * **Sync** runs again last, so a series just created or extended is captured the same call.

A charge never joins two series (`uq_recurring_series_member_transaction`, V57) and refresh is
idempotent: a second call with nothing new creates, adds, opens, closes and reopens nothing.

`merchant` and `category` are shared seed tables with no `UNIQUE (household_id, id)`, so
`recurring_series.merchant_id`/`category_id` are plain `REFERENCES merchant(id)`/
`REFERENCES category(id)` (V56's own ruling). Every merchant and category a series carries from
refresh comes from the ledger's own classification, never a client body, so no tenant check on
either runs here; `PATCH .../categoryId` and `POST .../from-transaction` add a category one back,
through `routes/transactions.py::_assert_category_allowed` — the same 422-for-a-referenced-seed-
row rule the global execution contract requires. A dismissal is only ever written by
`POST /recurring/{seriesId}/remove`, anchored on that series' own latest amount.

Every GET opens `SET TRANSACTION READ ONLY` and writes nothing."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import IDEMPOTENCY_REUSED, connect, first_line
from ..recurring import (
    Candidate,
    Identity,
    SpendRow,
    amounts_are_stable,
    detect_candidates,
    identity_for,
    interval_days_for,
    is_overdue,
    next_expected_on,
    price_change_window,
)
from ..text import normalize_bounded
from .transactions import _assert_category_allowed

router = APIRouter(tags=["recurring"])

Cadence = Literal["weekly", "monthly", "quarterly", "semiannual", "annual", "custom"]
Lifecycle = Literal["active", "trial", "paused"]

# Long enough for three yearly charges at the widest annual spacing (two gaps of up to 380 days
# span 760 days, about 25 months); at 18 months a yearly subscription could never reach the three
# occurrences a candidate needs. Bounds Create's own detection scan only — Extend has no lower
# bound, so an old dormant series can still be extended by a much later charge.
_LOOKBACK_MONTHS = 26

# The band both Extend (nearest current native_amount) and Create's dismissal check (an anchor
# near the group's median) use — the same 20% `oneledger.recurring` clusters within.
_MATCH_BAND = Decimal("0.2")


def _months_back(on: date, months: int) -> date:
    total = on.year * 12 + (on.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(on.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class IdentityOut(BaseModel):
    merchantId: UUID | None
    matchKey: str | None


def _out_identity(identity: Identity) -> IdentityOut:
    return IdentityOut(merchantId=identity.merchant_id, matchKey=identity.match_key)


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isSubscription: bool | None = None
    label: str | None = None
    lifecycle: Lifecycle | None = None
    cadence: Cadence | None = None
    customIntervalDays: int | None = None
    categoryId: UUID | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        return value if value is None else normalize_bounded(value, "label", 120)

    @model_validator(mode="after")
    def _cadence_shape(self) -> PatchBody:
        if self.cadence == "custom" and self.customIntervalDays is None:
            raise ValueError("customIntervalDays is required when cadence is set to custom")
        if (
            self.cadence is not None
            and self.cadence != "custom"
            and self.customIntervalDays is not None
        ):
            raise ValueError("customIntervalDays is only valid alongside a custom cadence")
        return self


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancelledOn: date | None = None


class ResumeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumedOn: date | None = None


class FromTransactionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactionId: UUID
    cadence: Cadence
    customIntervalDays: int | None = None
    label: str
    isSubscription: bool = True
    idempotencyKey: UUID

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return normalize_bounded(value, "label", 120)

    @model_validator(mode="after")
    def _cadence_shape(self) -> FromTransactionBody:
        if self.cadence == "custom" and self.customIntervalDays is None:
            raise ValueError("customIntervalDays is required when cadence is custom")
        if self.cadence != "custom" and self.customIntervalDays is not None:
            raise ValueError("customIntervalDays is only valid alongside a custom cadence")
        return self


class RecurringSeriesRow(BaseModel):
    id: UUID
    label: str
    identity: IdentityOut
    cadence: str
    customIntervalDays: int | None
    lifecycle: Lifecycle
    categoryId: UUID | None
    categoryName: str | None
    isSubscription: bool
    detectionMethod: Literal["detected", "owner"]
    needsName: bool
    # Null only for a series created before V57 that no refresh has synced yet.
    nativeAmount: str | None
    nativeCurrency: str | None
    priceChangedFrom: str | None
    priceChangedOn: date | None
    lastSeenOn: date
    isCurrent: bool
    currentPeriodStartedOn: date | None
    lastPeriodEndedOn: date | None
    lastEndReason: Literal["cancelled", "lapsed"] | None
    periodCount: int
    resumed: bool
    nextExpectedOn: date | None
    status: Literal["active", "overdue", "ended"]


class RecurringResponse(BaseModel):
    series: list[RecurringSeriesRow]


class RecurringRefreshCounts(BaseModel):
    seriesCreated: int
    seriesTouched: int
    membersAdded: int


def _translate_recurring_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "recurring_series_label_is_bounded":
        return HTTPException(status_code=422, detail="label must be 1-120 characters")
    if constraint == "uq_recurring_series_idempotency":
        return HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
    if exc.sqlstate == "23514" and "an ended recurring series is immutable" in first_line(exc):
        # Defence in depth: the route itself already checked ended_on before this write, so
        # reaching the trigger means a race the route's own read did not see.
        return HTTPException(status_code=422, detail="this recurring series has already ended")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="recurring series violates its required field matrix"
        )
    raise exc


def _category_name(cur: psycopg.Cursor[Any], category_id: UUID | None) -> str | None:
    if category_id is None:
        return None
    cur.execute("SELECT name FROM category WHERE id = %s", (category_id,))
    row = cur.fetchone()
    return None if row is None else str(row[0])


def _native_pair(
    original_amount: Decimal | None,
    original_currency: str | None,
    amount: Decimal,
    currency: str,
) -> tuple[Decimal, str]:
    """The global execution contract's own rule: the original charge amount when the statement
    recorded one, otherwise the posted amount, always a positive magnitude."""
    native = original_amount if original_amount is not None else amount
    native_currency = original_currency if original_currency is not None else currency
    return abs(Decimal(native)), str(native_currency).strip()


def _subscription_category_mapping(
    cur: psycopg.Cursor[Any], category_id: UUID | None
) -> bool | None:
    """The seed subscription-category map, a category's own row else its parent's (spec: "a child
    category with no row of its own uses its parent's row"). `None` when neither maps it."""
    if category_id is None:
        return None
    cur.execute(
        """SELECT own.is_subscription, parent.is_subscription
             FROM category c
             LEFT JOIN recurring_subscription_category own ON own.category_id = c.id
             LEFT JOIN recurring_subscription_category parent ON parent.category_id = c.parent_id
            WHERE c.id = %s""",
        (category_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    own, parent = row
    if own is not None:
        return bool(own)
    if parent is not None:
        return bool(parent)
    return None


_SPEND_ROWS = """
    SELECT t.id, t.booked_date, -t.amount_base,
           abs(coalesce(t.original_amount, t.amount)), coalesce(t.original_currency, t.currency),
           t.merchant_id, COALESCE(m.display_name, m.canonical_name), t.description_clean,
           t.description_raw, t.category_id,
           -- Membership, not a filter: detection clusters every charge so a second refresh forms
           -- the same groups as the first, and only Extend and Create act on unassigned ones.
           EXISTS (SELECT 1 FROM recurring_series_member x
                    WHERE x.household_id = t.household_id AND x.transaction_id = t.id)
      FROM v_transaction_reportable t
      LEFT JOIN merchant m ON m.id = t.merchant_id
     WHERE t.household_id = %s AND t.txn_type <> 'income' AND t.amount_base < 0
       -- A charge only: a refund on the same counterparty is stored positive and is never an
       -- occurrence.
     ORDER BY t.booked_date, t.id
"""


def _all_spend_rows(cur: psycopg.Cursor[Any], household_id: UUID) -> list[SpendRow]:
    """Every reportable charge with its series membership — no date lower bound, so Extend can
    still grow a dormant series from a much later charge; detection filters its own 26-month
    window from this same set rather than re-querying."""
    cur.execute(_SPEND_ROWS, (household_id,))
    return [
        SpendRow(
            transaction_id=UUID(str(row[0])),
            booked_date=row[1],
            amount_base=Decimal(row[2]),
            amount=Decimal(row[3]),
            currency=str(row[4]).strip(),
            merchant_id=None if row[5] is None else UUID(str(row[5])),
            merchant_display_name=row[6],
            description_clean=row[7],
            description_raw=row[8],
            category_id=None if row[9] is None else UUID(str(row[9])),
            in_series=bool(row[10]),
        )
        for row in cur.fetchall()
    ]


@dataclass
class _Period:
    id: UUID
    started_on: date
    ended_on: date | None
    end_reason: str | None


def _latest_period(cur: psycopg.Cursor[Any], household_id: UUID, series_id: UUID) -> _Period | None:
    # The open period first: a period opened and closed on one day shares its started_on with a
    # period resumed that same day, and random ids must never decide which one is "latest". Every
    # period query in this module uses this same order.
    cur.execute(
        """SELECT id, started_on, ended_on, end_reason FROM recurring_series_period
            WHERE household_id = %s AND series_id = %s
            ORDER BY ended_on IS NULL DESC, started_on DESC, created_at DESC, id DESC LIMIT 1""",
        (household_id, series_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return _Period(UUID(str(row[0])), row[1], row[2], row[3])


def _open_period(
    cur: psycopg.Cursor[Any], household_id: UUID, series_id: UUID, started_on: date
) -> UUID:
    cur.execute(
        """INSERT INTO recurring_series_period (household_id, series_id, started_on)
           VALUES (%s, %s, %s) RETURNING id""",
        (household_id, series_id, started_on),
    )
    created = cur.fetchone()
    assert created is not None
    return UUID(str(created[0]))


def _load_series_row(
    cur: psycopg.Cursor[Any], household_id: UUID, series_id: UUID
) -> RecurringSeriesRow:
    cur.execute(
        """SELECT label, merchant_id, match_key, cadence::text, custom_interval_days, lifecycle,
                  category_id, is_subscription, detection_method, named_by_owner, native_amount,
                  native_currency, last_seen_on
             FROM recurring_series WHERE household_id = %s AND id = %s""",
        (household_id, series_id),
    )
    row = cur.fetchone()
    assert row is not None
    (
        label,
        merchant_id,
        match_key,
        cadence,
        custom_interval_days,
        lifecycle,
        category_id,
        is_subscription,
        detection_method,
        named_by_owner,
        native_amount,
        native_currency,
        last_seen_on,
    ) = row

    cur.execute(
        """SELECT t.original_amount, t.original_currency, t.amount, t.currency, m.observed_on
             FROM recurring_series_member m
             JOIN v_transaction_reportable t
               ON t.household_id = m.household_id AND t.id = m.transaction_id
            WHERE m.household_id = %s AND m.series_id = %s
            ORDER BY m.observed_on DESC, m.transaction_id DESC LIMIT 2""",
        (household_id, series_id),
    )
    members = cur.fetchall()
    price_changed_from: str | None = None
    price_changed_on: date | None = None
    if len(members) == 2:
        latest_amount, latest_currency = _native_pair(*members[0][:4])
        previous_amount, previous_currency = _native_pair(*members[1][:4])
        if previous_currency == latest_currency and previous_amount != latest_amount:
            price_changed_from = str(previous_amount)
            price_changed_on = members[0][4]

    cur.execute(
        """SELECT started_on, ended_on, end_reason FROM recurring_series_period
            WHERE household_id = %s AND series_id = %s
            ORDER BY ended_on IS NULL DESC, started_on DESC, created_at DESC, id DESC""",
        (household_id, series_id),
    )
    periods = cur.fetchall()
    period_count = len(periods)
    is_current = period_count > 0 and periods[0][1] is None
    current_period_started_on = periods[0][0] if is_current else None
    closed_periods = periods[1:] if is_current else periods
    last_closed = closed_periods[0] if closed_periods else None
    last_period_ended_on = last_closed[1] if last_closed else None
    last_end_reason = last_closed[2] if last_closed else None
    resumed = is_current and period_count > 1

    interval = interval_days_for(
        cadence, None if custom_interval_days is None else int(custom_interval_days)
    )
    next_expected: date | None = None
    status_value: Literal["active", "overdue", "ended"]
    if is_current:
        assert current_period_started_on is not None
        anchor = max(last_seen_on, current_period_started_on)
        next_expected = next_expected_on(anchor, interval)
        status_value = "overdue" if is_overdue(anchor, interval, today=date.today()) else "active"
    else:
        status_value = "ended"

    identity = Identity(None if merchant_id is None else UUID(str(merchant_id)), match_key)
    needs_name = (
        detection_method == "detected" and identity.merchant_id is None and not named_by_owner
    )
    return RecurringSeriesRow(
        id=series_id,
        label=str(label),
        identity=_out_identity(identity),
        cadence=cadence,
        customIntervalDays=None if custom_interval_days is None else int(custom_interval_days),
        lifecycle=lifecycle,
        categoryId=None if category_id is None else UUID(str(category_id)),
        categoryName=_category_name(cur, category_id),
        isSubscription=bool(is_subscription),
        detectionMethod=detection_method,
        needsName=needs_name,
        nativeAmount=None if native_amount is None else str(Decimal(native_amount)),
        nativeCurrency=None if native_currency is None else str(native_currency).strip(),
        priceChangedFrom=price_changed_from,
        priceChangedOn=price_changed_on,
        lastSeenOn=last_seen_on,
        isCurrent=is_current,
        currentPeriodStartedOn=current_period_started_on,
        lastPeriodEndedOn=last_period_ended_on,
        lastEndReason=last_end_reason,
        periodCount=period_count,
        resumed=resumed,
        nextExpectedOn=next_expected,
        status=status_value,
    )


def _insert_dismissal(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    merchant_id: UUID | None,
    match_key: str | None,
    anchor_amount: Decimal | None,
    anchor_currency: str | None,
) -> None:
    """A null anchor means the whole identity (V56's original meaning); a set anchor removes only
    the subscription near that amount, leaving a sibling on the same descriptor untouched (spec
    "one descriptor, several subscriptions"). The `ON CONFLICT` target matches V57's own
    `NULLS NOT DISTINCT` indexes exactly."""
    if merchant_id is not None:
        cur.execute(
            """INSERT INTO recurring_dismissal (household_id, merchant_id, anchor_amount,
                   anchor_currency)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (household_id, merchant_id, anchor_amount, anchor_currency)
               WHERE merchant_id IS NOT NULL DO NOTHING""",
            (household_id, merchant_id, anchor_amount, anchor_currency),
        )
    else:
        cur.execute(
            """INSERT INTO recurring_dismissal (household_id, match_key, anchor_amount,
                   anchor_currency)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (household_id, match_key, anchor_amount, anchor_currency)
               WHERE match_key IS NOT NULL DO NOTHING""",
            (household_id, match_key, anchor_amount, anchor_currency),
        )


_Dismissal = tuple[Identity, Decimal | None, str | None]


def _load_dismissals(cur: psycopg.Cursor[Any], household_id: UUID) -> list[_Dismissal]:
    cur.execute(
        """SELECT merchant_id, match_key, anchor_amount, anchor_currency
             FROM recurring_dismissal WHERE household_id = %s""",
        (household_id,),
    )
    return [
        (
            Identity(None if row[0] is None else UUID(str(row[0])), row[1]),
            None if row[2] is None else Decimal(row[2]),
            None if row[3] is None else str(row[3]).strip(),
        )
        for row in cur.fetchall()
    ]


def _dismissed(
    dismissals: list[_Dismissal], identity: Identity, amount: Decimal, currency: str
) -> bool:
    """A null anchor dismisses the whole identity; a set anchor dismisses amounts within 20% of it
    in the same currency — the anchor is the removed series' own latest amount."""
    for dismissed_identity, anchor_amount, anchor_currency in dismissals:
        if dismissed_identity != identity:
            continue
        if anchor_amount is None:
            return True
        if (
            anchor_currency == currency
            and abs(amount - anchor_amount) <= _MATCH_BAND * anchor_amount
        ):
            return True
    return False


@dataclass
class _ExtendTarget:
    """One open-or-past series eligible to receive a charge, and its latest billing period's
    state, tracked in memory through the whole Extend pass exactly as `native_amount`/
    `last_seen_on` already were (Task 5's "keeps each series' period state in memory")."""

    series_id: UUID
    native_amount: Decimal
    last_seen_on: date
    cadence: str
    custom_interval_days: int | None
    lifecycle: str
    period_id: UUID
    period_started_on: date
    period_ended_on: date | None
    period_end_reason: str | None

    @property
    def is_open(self) -> bool:
        return self.period_ended_on is None

    @property
    def interval_days(self) -> int:
        return interval_days_for(self.cadence, self.custom_interval_days)


def _load_extend_targets(
    cur: psycopg.Cursor[Any], household_id: UUID
) -> dict[tuple[Identity, str], list[_ExtendTarget]]:
    """Every series not removed (`ended_on IS NULL` — V58: past/cancelled/lapsed subscriptions
    keep this too), with its latest billing period, so a dormant cancelled or lapsed series stays
    reachable for a resume (spec "a later charge opens a new one on the same subscription")."""
    cur.execute(
        """SELECT s.id, s.merchant_id, s.match_key, s.native_amount, s.native_currency,
                  s.last_seen_on, s.cadence::text, s.custom_interval_days, s.lifecycle,
                  p.id, p.started_on, p.ended_on, p.end_reason
             FROM recurring_series s
             LEFT JOIN LATERAL (
               SELECT id, started_on, ended_on, end_reason FROM recurring_series_period
                WHERE household_id = s.household_id AND series_id = s.id
                ORDER BY ended_on IS NULL DESC, started_on DESC, created_at DESC, id DESC LIMIT 1
             ) p ON true
            WHERE s.household_id = %s AND s.ended_on IS NULL""",
        (household_id,),
    )
    by_identity_currency: dict[tuple[Identity, str], list[_ExtendTarget]] = {}
    for row in cur.fetchall():
        (
            series_id,
            merchant_id,
            match_key,
            native_amount,
            native_currency,
            last_seen_on,
            cadence,
            custom_interval_days,
            lifecycle,
            period_id,
            period_started_on,
            period_ended_on,
            period_end_reason,
        ) = row
        if native_amount is None or native_currency is None or period_id is None:
            continue
        identity = Identity(None if merchant_id is None else UUID(str(merchant_id)), match_key)
        key = (identity, str(native_currency).strip())
        by_identity_currency.setdefault(key, []).append(
            _ExtendTarget(
                series_id=UUID(str(series_id)),
                native_amount=Decimal(native_amount),
                last_seen_on=last_seen_on,
                cadence=str(cadence),
                custom_interval_days=None
                if custom_interval_days is None
                else int(custom_interval_days),
                lifecycle=str(lifecycle),
                period_id=UUID(str(period_id)),
                period_started_on=period_started_on,
                period_ended_on=period_ended_on,
                period_end_reason=period_end_reason,
            )
        )
    return by_identity_currency


def _join_period(
    cur: psycopg.Cursor[Any], household_id: UUID, target: _ExtendTarget, d: date
) -> None:
    """Advances `target`'s billing period for a charge dated `d`, exactly the state machine Task 5
    specifies ("Extend — periods"), and updates `target` in place so the next charge in this same
    pass sees the result."""
    interval = target.interval_days
    if target.period_ended_on is None:
        # Open: a real gap of more than two cadences lapses it (paid through the date its last
        # charge covered) and opens a fresh period at `d`; otherwise the charge joins, pulling the
        # period's start back if it is dated earlier than what was known.
        # Measured from the anchor, as Sync's lapse is: the first charge after a resume long past
        # the last one joins the resumed period instead of lapsing it at once.
        anchor = max(target.last_seen_on, target.period_started_on)
        if target.lifecycle != "paused" and d > anchor + timedelta(days=2 * interval):
            lapse_on = max(target.period_started_on, target.last_seen_on + timedelta(days=interval))
            cur.execute(
                """UPDATE recurring_series_period SET ended_on = %s, end_reason = 'lapsed'
                    WHERE household_id = %s AND id = %s""",
                (lapse_on, household_id, target.period_id),
            )
            new_id = _open_period(cur, household_id, target.series_id, d)
            target.period_id = new_id
            target.period_started_on = d
            target.period_ended_on = None
            target.period_end_reason = None
        elif d < target.period_started_on:
            cur.execute(
                """UPDATE recurring_series_period SET started_on = %s
                    WHERE household_id = %s AND id = %s""",
                (d, household_id, target.period_id),
            )
            target.period_started_on = d
    elif target.period_end_reason == "lapsed":
        # Lapsed: a charge that arrived within two cadences of the last one was simply late —
        # the statement, not the subscription — so it restores the same period rather than
        # starting a new one (spec: never shown as resumed just because a statement was late).
        if d <= target.last_seen_on + timedelta(days=2 * interval):
            cur.execute(
                """UPDATE recurring_series_period SET ended_on = NULL, end_reason = NULL
                    WHERE household_id = %s AND id = %s""",
                (household_id, target.period_id),
            )
            target.period_ended_on = None
            target.period_end_reason = None
            if d < target.period_started_on:
                cur.execute(
                    """UPDATE recurring_series_period SET started_on = %s
                        WHERE household_id = %s AND id = %s""",
                    (d, household_id, target.period_id),
                )
                target.period_started_on = d
        else:
            new_id = _open_period(cur, household_id, target.series_id, d)
            target.period_id = new_id
            target.period_started_on = d
            target.period_ended_on = None
            target.period_end_reason = None
    else:
        # Cancelled: a charge after the cancellation date resumes the subscription with a fresh
        # period; one dated within the cancelled period (a backfilled import) joins it as-is,
        # since the owner's own cancellation must never be silently reopened.
        assert target.period_ended_on is not None
        if d > target.period_ended_on:
            new_id = _open_period(cur, household_id, target.series_id, d)
            target.period_id = new_id
            target.period_started_on = d
            target.period_ended_on = None
            target.period_end_reason = None


def _extend_target(
    row: SpendRow,
    group: list[_ExtendTarget],
    old_band_rows: list[SpendRow],
    dismissals: list[_Dismissal],
    identity: Identity,
) -> _ExtendTarget | None:
    """Picks the series `row` should join, or `None` when it joins none. Within 20% of a
    candidate's own amount: prefer a series whose latest period is open, then the nearest amount
    (Task 4), then the later `last_seen_on`, then `id` (Task 5). Outside every band: a large price
    change may join the one *current* series on the identity when exactly one exists, else the
    only series at all — Task 4's own timing and dismissal rules, unchanged, are what refuse
    rejoining a long-dormant series at a different price (Task 5 "do not loosen the timing
    rule")."""
    within_band = [
        t for t in group if abs(row.amount - t.native_amount) <= _MATCH_BAND * t.native_amount
    ]
    if within_band:
        # Nearest amount after "open": two current services on one descriptor can sit within 20%
        # of each other's amount, and the later last_seen_on must not steal the other's charge.
        return max(
            within_band,
            key=lambda t: (
                t.is_open,
                -abs(row.amount - t.native_amount),
                t.last_seen_on,
                t.series_id,
            ),
        )

    current = [t for t in group if t.is_open]
    if len(current) == 1:
        only = current[0]
    elif len(group) == 1:
        only = group[0]
    else:
        return None

    if row.booked_date <= only.last_seen_on:
        return None

    window = price_change_window(only.cadence, only.custom_interval_days)
    old_band = [
        other
        for other in old_band_rows
        if other.transaction_id != row.transaction_id
        and abs(other.amount - only.native_amount) <= _MATCH_BAND * only.native_amount
    ]
    old_price_continues = any(other.booked_date >= row.booked_date for other in old_band)
    previous_old_charge = max(
        [only.last_seen_on, *(o.booked_date for o in old_band if o.booked_date < row.booked_date)]
    )
    gap = (row.booked_date - previous_old_charge).days
    lands_when_due = window is not None and window[0] <= gap <= window[1]
    if (
        not lands_when_due
        or old_price_continues
        or _dismissed(dismissals, identity, row.amount, row.currency)
    ):
        return None
    return only


def _extend(
    cur: psycopg.Cursor[Any], household_id: UUID, unassigned: list[SpendRow]
) -> tuple[int, set[UUID], set[UUID]]:
    """Returns (members_added, extended_transaction_ids, touched_series_ids)."""
    by_identity_currency = _load_extend_targets(cur, household_id)
    dismissals = _load_dismissals(cur, household_id)
    by_key: dict[tuple[Identity, str], list[SpendRow]] = {}
    for row in unassigned:
        key = (
            identity_for(row.merchant_id, row.description_clean, row.description_raw),
            row.currency,
        )
        by_key.setdefault(key, []).append(row)

    members_added = 0
    extended: set[UUID] = set()
    touched: set[UUID] = set()
    for row in unassigned:
        identity = identity_for(row.merchant_id, row.description_clean, row.description_raw)
        group = by_identity_currency.get((identity, row.currency))
        if not group:
            continue
        target = _extend_target(
            row, group, by_key.get((identity, row.currency), []), dismissals, identity
        )
        if target is None:
            continue
        cur.execute(
            """INSERT INTO recurring_series_member (household_id, series_id, transaction_id,
                   observed_on, amount_base)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (household_id, transaction_id) DO NOTHING""",
            (household_id, target.series_id, row.transaction_id, row.booked_date, row.amount_base),
        )
        if not cur.rowcount:
            continue
        _join_period(cur, household_id, target, row.booked_date)
        members_added += 1
        extended.add(row.transaction_id)
        touched.add(target.series_id)
        if row.booked_date >= target.last_seen_on:
            # The series now stands at this charge. Rows arrive in date order, so the next charge
            # is measured from here: without this, only the first of several new-price charges in
            # one import joined, and the rest failed the timing rule against the old last date.
            # Task 5 also lets an *earlier* charge join (moving an open period's start back), and
            # that must never pull native_amount/last_seen_on backward past what is already known.
            target.native_amount = row.amount
            target.last_seen_on = row.booked_date
    return members_added, extended, touched


def _create(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    unassigned_by_id: dict[UUID, SpendRow],
    candidates: list[Candidate],
) -> tuple[int, int]:
    """Returns (series_created, members_added)."""
    dismissals = _load_dismissals(cur, household_id)

    series_created = 0
    members_added = 0
    for candidate in candidates:
        if _dismissed(dismissals, candidate.identity, candidate.typical_amount, candidate.currency):
            continue

        latest_row = unassigned_by_id[candidate.transaction_ids[-1]]
        is_subscription = _subscription_category_mapping(cur, candidate.category_id)
        if is_subscription is None:
            is_subscription = candidate.amounts_are_stable

        cur.execute(
            """INSERT INTO recurring_series (household_id, idempotency_key, label, merchant_id,
                   match_key, cadence, expected_amount_base, currency, category_id, first_seen_on,
                   last_seen_on, is_subscription, detection_method, native_amount, native_currency)
               VALUES (%s, gen_random_uuid(), %s, %s, %s, %s, %s,
                       (SELECT base_currency FROM household WHERE id = %s), %s, %s, %s, %s,
                       'detected', %s, %s)
               RETURNING id""",
            (
                household_id,
                candidate.label,
                candidate.identity.merchant_id,
                candidate.identity.match_key,
                candidate.cadence,
                latest_row.amount_base,
                household_id,
                candidate.category_id,
                candidate.first_seen_on,
                candidate.last_seen_on,
                is_subscription,
                candidate.native_amount,
                candidate.currency,
            ),
        )
        created = cur.fetchone()
        assert created is not None
        series_id = UUID(str(created[0]))
        series_created += 1

        for transaction_id in candidate.transaction_ids:
            member_row = unassigned_by_id[transaction_id]
            cur.execute(
                """INSERT INTO recurring_series_member (household_id, series_id, transaction_id,
                       observed_on, amount_base)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    household_id,
                    series_id,
                    transaction_id,
                    member_row.booked_date,
                    member_row.amount_base,
                ),
            )
            members_added += 1

        cur.execute(
            """INSERT INTO recurring_series_period (household_id, series_id, started_on)
               VALUES (%s, %s, %s)""",
            (household_id, series_id, candidate.first_seen_on),
        )
    return series_created, members_added


def _sync(cur: psycopg.Cursor[Any], household_id: UUID) -> None:
    cur.execute(
        """SELECT s.id, s.category_id, s.subscription_set_by_owner, s.cadence::text,
                  s.custom_interval_days, s.lifecycle,
                  p.id, p.started_on, p.ended_on
             FROM recurring_series s
             LEFT JOIN LATERAL (
               SELECT id, started_on, ended_on FROM recurring_series_period
                WHERE household_id = s.household_id AND series_id = s.id
                ORDER BY ended_on IS NULL DESC, started_on DESC, created_at DESC, id DESC LIMIT 1
             ) p ON true
            WHERE s.household_id = %s AND s.ended_on IS NULL""",
        (household_id,),
    )
    open_series = cur.fetchall()
    today = date.today()
    for (
        series_id,
        category_id,
        subscription_set_by_owner,
        cadence,
        custom_interval_days,
        lifecycle,
        period_id,
        period_started_on,
        period_ended_on,
    ) in open_series:
        series_id = UUID(str(series_id))
        cur.execute(
            """SELECT t.original_amount, t.original_currency, t.amount, t.currency, m.amount_base,
                      m.observed_on
                 FROM recurring_series_member m
                 JOIN v_transaction_reportable t
               ON t.household_id = m.household_id AND t.id = m.transaction_id
                WHERE m.household_id = %s AND m.series_id = %s
                ORDER BY m.observed_on DESC, m.transaction_id DESC LIMIT 1""",
            (household_id, series_id),
        )
        latest = cur.fetchone()
        if latest is None:
            continue
        native_amount, native_currency = _native_pair(*latest[:4])
        last_seen_on = latest[5]
        cur.execute(
            """UPDATE recurring_series
                  SET native_amount = %s, native_currency = %s, expected_amount_base = %s,
                      last_seen_on = %s, updated_at = now()
                WHERE household_id = %s AND id = %s""",
            (
                native_amount,
                native_currency,
                Decimal(latest[4]),
                last_seen_on,
                household_id,
                series_id,
            ),
        )

        # Lapse: a series whose latest period is still open and not paused, stale by more than two
        # cadences from its anchor, pays through the date its last charge covered (spec). Closing
        # is never destructive — `_join_period`'s "lapsed" branch reopens it exactly.
        if period_id is not None and period_ended_on is None and str(lifecycle) != "paused":
            interval = interval_days_for(
                str(cadence), None if custom_interval_days is None else int(custom_interval_days)
            )
            anchor = max(last_seen_on, period_started_on)
            if today > anchor + timedelta(days=2 * interval):
                lapse_on = max(period_started_on, last_seen_on + timedelta(days=interval))
                cur.execute(
                    """UPDATE recurring_series_period SET ended_on = %s, end_reason = 'lapsed'
                        WHERE household_id = %s AND id = %s""",
                    (lapse_on, household_id, UUID(str(period_id))),
                )

        if subscription_set_by_owner:
            continue
        mapped = _subscription_category_mapping(
            cur, None if category_id is None else UUID(str(category_id))
        )
        if mapped is None:
            cur.execute(
                """SELECT abs(coalesce(t.original_amount, t.amount))
                     FROM recurring_series_member m
                     JOIN v_transaction_reportable t
                       ON t.household_id = m.household_id AND t.id = m.transaction_id
                    WHERE m.household_id = %s AND m.series_id = %s""",
                (household_id, series_id),
            )
            mapped = amounts_are_stable([Decimal(row[0]) for row in cur.fetchall()])
        cur.execute(
            """UPDATE recurring_series SET is_subscription = %s, updated_at = now()
                WHERE household_id = %s AND id = %s""",
            (mapped, household_id, series_id),
        )


@router.get("/recurring", response_model=RecurringResponse)
def read_recurring(owner: Annotated[OwnerSession, Depends(require_owner)]) -> RecurringResponse:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(
            """SELECT id FROM recurring_series
                WHERE household_id = %s AND ended_on IS NULL ORDER BY label, id""",
            (owner.household_id,),
        )
        series = [
            _load_series_row(cur, owner.household_id, UUID(str(row[0]))) for row in cur.fetchall()
        ]
    return RecurringResponse(series=series)


@router.patch("/recurring/{seriesId}", response_model=RecurringSeriesRow)
def patch_recurring(
    seriesId: UUID, body: PatchBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RecurringSeriesRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT ended_on FROM recurring_series
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, seriesId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="recurring series not found")
            if found[0] is not None:
                raise HTTPException(
                    status_code=409, detail="this recurring series has already ended"
                )
            if body.isSubscription is not None:
                cur.execute(
                    """UPDATE recurring_series
                          SET is_subscription = %s, subscription_set_by_owner = true,
                              updated_at = now()
                        WHERE household_id = %s AND id = %s""",
                    (body.isSubscription, owner.household_id, seriesId),
                )
            if body.label is not None:
                cur.execute(
                    """UPDATE recurring_series
                          SET label = %s, named_by_owner = true, updated_at = now()
                        WHERE household_id = %s AND id = %s""",
                    (body.label, owner.household_id, seriesId),
                )
            if body.lifecycle is not None:
                cur.execute(
                    """UPDATE recurring_series SET lifecycle = %s, updated_at = now()
                        WHERE household_id = %s AND id = %s""",
                    (body.lifecycle, owner.household_id, seriesId),
                )
            if body.cadence is not None:
                cur.execute(
                    """UPDATE recurring_series
                          SET cadence = %s, custom_interval_days = %s, updated_at = now()
                        WHERE household_id = %s AND id = %s""",
                    (body.cadence, body.customIntervalDays, owner.household_id, seriesId),
                )
            elif body.customIntervalDays is not None:
                cur.execute(
                    """UPDATE recurring_series SET custom_interval_days = %s, updated_at = now()
                        WHERE household_id = %s AND id = %s""",
                    (body.customIntervalDays, owner.household_id, seriesId),
                )
            if body.categoryId is not None:
                _assert_category_allowed(cur, body.categoryId, owner.household_id)
                cur.execute(
                    """UPDATE recurring_series SET category_id = %s, updated_at = now()
                        WHERE household_id = %s AND id = %s""",
                    (body.categoryId, owner.household_id, seriesId),
                )
            return _load_series_row(cur, owner.household_id, seriesId)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/{seriesId}/cancel", response_model=RecurringSeriesRow)
def cancel_recurring(
    seriesId: UUID, body: CancelBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RecurringSeriesRow:
    """Closes the open billing period, or relabels an already-lapsed one — the owner's record wins
    over the inference (spec)."""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT ended_on FROM recurring_series
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, seriesId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="recurring series not found")
            if found[0] is not None:
                raise HTTPException(
                    status_code=409, detail="this recurring series has already ended"
                )

            cancelled_on = body.cancelledOn or date.today()
            if cancelled_on > date.today():
                raise HTTPException(status_code=422, detail="cancelledOn must not be after today")

            period = _latest_period(cur, owner.household_id, seriesId)
            if period is None or period.end_reason == "cancelled":
                raise HTTPException(
                    status_code=409, detail="this subscription is already cancelled"
                )

            if period.ended_on is None:
                cur.execute(
                    """SELECT max(observed_on) FROM recurring_series_member
                        WHERE household_id = %s AND series_id = %s""",
                    (owner.household_id, seriesId),
                )
                latest_member_on = cur.fetchone()
                candidates = [cancelled_on, period.started_on]
                if latest_member_on and latest_member_on[0] is not None:
                    candidates.append(latest_member_on[0])
                ended_on = max(candidates)
                cur.execute(
                    """UPDATE recurring_series_period SET ended_on = %s, end_reason = 'cancelled'
                        WHERE household_id = %s AND id = %s""",
                    (ended_on, owner.household_id, period.id),
                )
            else:
                # Already lapsed: relabel in place, keeping the inferred ended_on — the owner is
                # recording that this was in fact a cancellation, not correcting its date.
                cur.execute(
                    """UPDATE recurring_series_period SET end_reason = 'cancelled'
                        WHERE household_id = %s AND id = %s""",
                    (owner.household_id, period.id),
                )
            return _load_series_row(cur, owner.household_id, seriesId)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/{seriesId}/resume", response_model=RecurringSeriesRow)
def resume_recurring(
    seriesId: UUID, body: ResumeBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RecurringSeriesRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT ended_on FROM recurring_series
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, seriesId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="recurring series not found")
            if found[0] is not None:
                raise HTTPException(
                    status_code=409, detail="this recurring series has already ended"
                )

            period = _latest_period(cur, owner.household_id, seriesId)
            if period is not None and period.ended_on is None:
                raise HTTPException(status_code=409, detail="this subscription is already current")

            resumed_on = body.resumedOn or date.today()
            if resumed_on > date.today():
                raise HTTPException(status_code=422, detail="resumedOn must not be after today")
            if period is not None and period.ended_on is not None and resumed_on < period.ended_on:
                raise HTTPException(
                    status_code=422, detail="resumedOn must not be before the latest period ended"
                )

            _open_period(cur, owner.household_id, seriesId, resumed_on)
            return _load_series_row(cur, owner.household_id, seriesId)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/from-transaction", response_model=RecurringSeriesRow)
def create_from_transaction(
    body: FromTransactionBody,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RecurringSeriesRow:
    """A single charge marked as a subscription (spec "a single charge marked as a subscription,
    with its cadence, for a yearly plan seen once"). Idempotency is checked before the transaction
    is even read, per the global execution contract's own order."""
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))

            cur.execute(
                """SELECT id, cadence::text, custom_interval_days, label, is_subscription
                     FROM recurring_series WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                eid, ecadence, ecustom, elabel, eis_sub = existing
                cur.execute(
                    """SELECT 1 FROM recurring_series_member
                        WHERE household_id = %s AND series_id = %s AND transaction_id = %s""",
                    (owner.household_id, eid, body.transactionId),
                )
                is_member = cur.fetchone() is not None
                matches = (
                    is_member
                    and ecadence == body.cadence
                    and (None if ecustom is None else int(ecustom)) == body.customIntervalDays
                    and elabel == body.label
                    and bool(eis_sub) == body.isSubscription
                )
                if matches:
                    response.status_code = status.HTTP_200_OK
                    return _load_series_row(cur, owner.household_id, UUID(str(eid)))
                raise HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)

            # v_transaction_posting, not the base table (scripts/check-identifiers.py I3): this
            # existence/ownership check must still see a row the reportable view drops by design
            # (income, a refund, a transfer leg), so "unknown or another household's" (404)
            # stays apart from "ours but not a reportable charge" (422).
            cur.execute(
                "SELECT 1 FROM v_transaction_posting WHERE household_id = %s AND id = %s",
                (owner.household_id, body.transactionId),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="transaction not found")

            cur.execute(
                """SELECT t.booked_date, t.amount_base, t.amount, t.currency, t.original_amount,
                          t.original_currency, t.merchant_id, t.description_clean,
                          t.description_raw, t.category_id
                     FROM v_transaction_reportable t
                    WHERE t.household_id = %s AND t.id = %s
                      AND t.txn_type <> 'income' AND t.amount_base < 0""",
                (owner.household_id, body.transactionId),
            )
            charge = cur.fetchone()
            if charge is None:
                raise HTTPException(
                    status_code=422, detail="transaction is not a reportable charge"
                )
            (
                booked_date,
                amount_base,
                amount,
                currency,
                original_amount,
                original_currency,
                merchant_id,
                description_clean,
                description_raw,
                category_id,
            ) = charge

            cur.execute(
                """SELECT 1 FROM recurring_series_member
                    WHERE household_id = %s AND transaction_id = %s""",
                (owner.household_id, body.transactionId),
            )
            if cur.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="this transaction already belongs to a recurring series",
                )

            identity = identity_for(
                None if merchant_id is None else UUID(str(merchant_id)),
                description_clean,
                description_raw,
            )
            native_amount, native_currency = _native_pair(
                original_amount, original_currency, Decimal(amount), str(currency).strip()
            )
            cur.execute(
                """INSERT INTO recurring_series (household_id, idempotency_key, label,
                       merchant_id, match_key, cadence, custom_interval_days,
                       expected_amount_base, currency, category_id, first_seen_on, last_seen_on,
                       is_subscription, detection_method, named_by_owner,
                       subscription_set_by_owner, native_amount, native_currency)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                           (SELECT base_currency FROM household WHERE id = %s), %s, %s, %s, %s,
                           'owner', true, true, %s, %s)
                   RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.label,
                    identity.merchant_id,
                    identity.match_key,
                    body.cadence,
                    body.customIntervalDays,
                    abs(Decimal(amount_base)),
                    owner.household_id,
                    category_id,
                    booked_date,
                    booked_date,
                    body.isSubscription,
                    native_amount,
                    native_currency,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            series_id = UUID(str(created[0]))

            cur.execute(
                """INSERT INTO recurring_series_member (household_id, series_id, transaction_id,
                       observed_on, amount_base)
                   VALUES (%s, %s, %s, %s, %s)""",
                # recurring_series_member.amount_base is stored positive throughout this module
                # (`_SPEND_ROWS`' own `-t.amount_base`); `amount_base` here is still the raw,
                # negative spend value the SELECT above read.
                (
                    owner.household_id,
                    series_id,
                    body.transactionId,
                    booked_date,
                    abs(Decimal(amount_base)),
                ),
            )
            cur.execute(
                """INSERT INTO recurring_series_period (household_id, series_id, started_on)
                   VALUES (%s, %s, %s)""",
                (owner.household_id, series_id, booked_date),
            )
            response.status_code = status.HTTP_201_CREATED
            return _load_series_row(cur, owner.household_id, series_id)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/{seriesId}/remove")
def remove_recurring(
    seriesId: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> dict[str, bool]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        # A series created before V57 has no native amount until Sync fills it; removing it
        # unsynced would anchor the dismissal on nothing and dismiss the whole identity.
        _sync(cur, owner.household_id)
        cur.execute(
            """SELECT merchant_id, match_key, native_amount, native_currency, ended_on
                 FROM recurring_series WHERE household_id = %s AND id = %s FOR UPDATE""",
            (owner.household_id, seriesId),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="recurring series not found")
        merchant_id, match_key, native_amount, native_currency, ended_on = found
        if ended_on is not None:
            raise HTTPException(status_code=409, detail="this recurring series has already ended")
        cur.execute(
            """UPDATE recurring_series
                  SET ended_on = %s, ended_reason = 'not_recurring', updated_at = now()
                WHERE household_id = %s AND id = %s""",
            (date.today(), owner.household_id, seriesId),
        )
        _insert_dismissal(
            cur,
            owner.household_id,
            None if merchant_id is None else UUID(str(merchant_id)),
            match_key,
            None if native_amount is None else Decimal(native_amount),
            None if native_currency is None else str(native_currency).strip(),
        )
    return {"removed": True}


@router.post("/recurring/refresh", response_model=RecurringRefreshCounts)
def refresh_recurring(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RecurringRefreshCounts:
    since = _months_back(date.today(), _LOOKBACK_MONTHS)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))

        # Sync first as well as last: a series created before V57 has no native amount until
        # Sync fills it, and Extend skips such a series, so on the first refresh after the
        # upgrade its later charges would otherwise form a duplicate series. Sync's own Lapse
        # step also runs here, before Extend, so a charge that in fact arrived on time can still
        # restore the period Lapse just closed (spec: a lapse is provisional).
        _sync(cur, owner.household_id)

        rows = _all_spend_rows(cur, owner.household_id)
        unassigned = [row for row in rows if not row.in_series]
        unassigned_by_id = {row.transaction_id: row for row in unassigned}

        extend_members, extended_ids, touched_series = _extend(cur, owner.household_id, unassigned)

        window = [
            replace(row, in_series=True) if row.transaction_id in extended_ids else row
            for row in rows
            if row.booked_date >= since
        ]
        candidates = detect_candidates(window)
        series_created, create_members = _create(
            cur, owner.household_id, unassigned_by_id, candidates
        )

        _sync(cur, owner.household_id)

    return RecurringRefreshCounts(
        seriesCreated=series_created,
        seriesTouched=len(touched_series),
        membersAdded=extend_members + create_members,
    )
