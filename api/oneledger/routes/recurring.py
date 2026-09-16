# api/oneledger/routes/recurring.py
"""Phase 4.1 Task 1: recurring discovery is opt-out (spec "Recurring payments are simply just not
a subscription", `docs/superpowers/plans/2026-09-16-phase-4-1-subscriptions.md` Task 1).

`POST /recurring/refresh` is the only writer, and the owner never confirms a detection. It takes
the household lock first, then applies three rules in order:

  * **Extend** — a reportable charge on an identity and currency not yet in any series, dated
    after an open series' own `last_seen_on`, joins the open series whose *current* `native_
    amount` (read once, at the start of this call, never updated mid-pass) is nearest within 20%;
    when none is within 20% and exactly one open series exists on that identity and currency, it
    joins that one instead (a larger price change); otherwise the charge stays unassigned.
  * **Create** — `oneledger.recurring.detect_candidates` runs over charges still unassigned after
    Extend, within the 26-month lookback window. Each resulting group becomes a series unless a
    dismissal on its identity has a null anchor (the whole identity), or an anchor in the same
    currency within 20% of the group's median.
  * **Sync** — every open series (including one just created or extended this same call) has its
    `native_amount`/`native_currency`/`expected_amount_base`/`last_seen_on` moved to its latest
    member, and, only where `subscription_set_by_owner` is false, `is_subscription` recomputed:
    the effective subscription-category mapping when one applies (a category's own row, else its
    parent's), otherwise `amounts_are_stable` over the series' own members. This also gives a
    series created before V57 its flag and amounts on its first refresh since.

A charge never joins two series (`uq_recurring_series_member_transaction`, V57) and refresh is
idempotent: a second call with nothing new creates, extends and changes nothing.

`merchant` and `category` are shared seed tables with no `UNIQUE (household_id, id)`, so
`recurring_series.merchant_id`/`category_id` are plain `REFERENCES merchant(id)`/
`REFERENCES category(id)` (V56's own ruling). Every merchant and category a series carries now
comes from the ledger's own classification, never a client body, so no tenant check on either runs
here (Task 2's `PATCH .../categoryId` adds a category one back). A dismissal is only ever written by
`POST /recurring/{seriesId}/remove`, anchored on that series' own latest amount.

Every GET opens `SET TRANSACTION READ ONLY` and writes nothing."""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from ..auth import OwnerSession, require_owner
from ..db import connect, first_line
from ..recurring import (
    CADENCE_WINDOWS,
    Candidate,
    Identity,
    SpendRow,
    amounts_are_stable,
    detect_candidates,
    identity_for,
    is_overdue,
)
from ..text import normalize_bounded

router = APIRouter(tags=["recurring"])

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

    @field_validator("label")
    @classmethod
    def _label(cls, value: str | None) -> str | None:
        return value if value is None else normalize_bounded(value, "label", 120)


class RecurringSeriesRow(BaseModel):
    id: UUID
    label: str
    identity: IdentityOut
    cadence: str
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
    status: Literal["active", "overdue"]


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


def _load_series_row(
    cur: psycopg.Cursor[Any], household_id: UUID, series_id: UUID
) -> RecurringSeriesRow:
    cur.execute(
        """SELECT label, merchant_id, match_key, cadence::text, category_id, is_subscription,
                  detection_method, named_by_owner, native_amount, native_currency, last_seen_on
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

    identity = Identity(None if merchant_id is None else UUID(str(merchant_id)), match_key)
    needs_name = (
        detection_method == "detected" and identity.merchant_id is None and not named_by_owner
    )
    status: Literal["active", "overdue"] = (
        "overdue" if is_overdue(last_seen_on, cadence, today=date.today()) else "active"
    )
    return RecurringSeriesRow(
        id=series_id,
        label=str(label),
        identity=_out_identity(identity),
        cadence=cadence,
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
        status=status,
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


def _extend(
    cur: psycopg.Cursor[Any], household_id: UUID, unassigned: list[SpendRow]
) -> tuple[int, set[UUID], set[UUID]]:
    """Returns (members_added, extended_transaction_ids, touched_series_ids).

    A charge within 20% of an open series' amount joins the nearest such series. A charge outside
    every band joins the only open series on its identity and currency as a price change only when
    all three hold:

    - it lands where that series' next charge was due — the gap from the latest old-price charge
      before it (the series' own last charge, or an unassigned charge in the old band) fits the
      series' cadence window;
    - the old price has stopped — no unassigned charge in the old band is dated on or after it;
    - it is not within a dismissal's band.

    Otherwise it stays unassigned. The timing condition is what keeps a service started beside an
    existing one on a shared billing descriptor apart: its charges fall days, not a cycle, after
    the old price's, including the newest charge of a batch, which no later old-price charge
    follows. The dismissal condition keeps a removed payment's later charges out of its sibling."""
    cur.execute(
        """SELECT id, merchant_id, match_key, native_amount, native_currency, last_seen_on,
                  cadence::text
             FROM recurring_series WHERE household_id = %s AND ended_on IS NULL""",
        (household_id,),
    )
    by_identity_currency: dict[tuple[Identity, str], list[tuple[UUID, Decimal, date, str]]] = {}
    for (
        series_id,
        merchant_id,
        match_key,
        native_amount,
        native_currency,
        last_seen_on,
        cadence,
    ) in cur.fetchall():
        if native_amount is None or native_currency is None:
            continue
        identity = Identity(None if merchant_id is None else UUID(str(merchant_id)), match_key)
        key = (identity, str(native_currency).strip())
        by_identity_currency.setdefault(key, []).append(
            (UUID(str(series_id)), Decimal(native_amount), last_seen_on, str(cadence))
        )

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
        eligible = [entry for entry in group if row.booked_date > entry[2]]
        if not eligible:
            continue
        within_band = [
            entry for entry in eligible if abs(row.amount - entry[1]) <= _MATCH_BAND * entry[1]
        ]
        if within_band:
            target = min(within_band, key=lambda entry: abs(row.amount - entry[1]))
        elif len(group) == 1 and eligible:
            only = group[0]
            window = CADENCE_WINDOWS.get(only[3])
            old_band = [
                other
                for other in by_key.get((identity, row.currency), [])
                if other.transaction_id != row.transaction_id
                and abs(other.amount - only[1]) <= _MATCH_BAND * only[1]
            ]
            old_price_continues = any(other.booked_date >= row.booked_date for other in old_band)
            previous_old_charge = max(
                [
                    only[2],
                    *(
                        other.booked_date
                        for other in old_band
                        if other.booked_date < row.booked_date
                    ),
                ]
            )
            gap = (row.booked_date - previous_old_charge).days
            lands_when_due = window is not None and window[0] <= gap <= window[1]
            if (
                not lands_when_due
                or old_price_continues
                or _dismissed(dismissals, identity, row.amount, row.currency)
            ):
                continue
            target = only
        else:
            continue
        cur.execute(
            """INSERT INTO recurring_series_member (household_id, series_id, transaction_id,
                   observed_on, amount_base)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (household_id, transaction_id) DO NOTHING""",
            (household_id, target[0], row.transaction_id, row.booked_date, row.amount_base),
        )
        if cur.rowcount:
            members_added += 1
            extended.add(row.transaction_id)
            touched.add(target[0])
            # The series now stands at this charge. Rows arrive in date order, so the next charge
            # is measured from here: without this, only the first of several new-price charges in
            # one import joined, and the rest failed the timing rule against the old last date.
            position = group.index(target)
            group[position] = (target[0], row.amount, row.booked_date, target[3])
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
    return series_created, members_added


def _sync(cur: psycopg.Cursor[Any], household_id: UUID) -> None:
    cur.execute(
        """SELECT id, category_id, subscription_set_by_owner FROM recurring_series
            WHERE household_id = %s AND ended_on IS NULL""",
        (household_id,),
    )
    open_series = cur.fetchall()
    for series_id, category_id, subscription_set_by_owner in open_series:
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
        cur.execute(
            """UPDATE recurring_series
                  SET native_amount = %s, native_currency = %s, expected_amount_base = %s,
                      last_seen_on = %s, updated_at = now()
                WHERE household_id = %s AND id = %s""",
            (
                native_amount,
                native_currency,
                Decimal(latest[4]),
                latest[5],
                household_id,
                series_id,
            ),
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
            return _load_series_row(cur, owner.household_id, seriesId)
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
        # upgrade its later charges would otherwise form a duplicate series.
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
