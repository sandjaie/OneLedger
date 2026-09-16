# api/oneledger/routes/recurring.py
"""Phase 4.1 Task 3: recurring discovery, confirmation, price changes and inactive candidates
(spec "Discovery proposes, the owner decides", `docs/superpowers/plans/
2026-09-16-phase-4-1-budgets-recurring.md` Task 3).

Discovery reads the ledger and proposes; it writes nothing on its own. `api/oneledger/recurring.py`
holds the pure grouping and cadence detection this route reads from `v_transaction_reportable`
over the last 18 months; only `POST /recurring/confirm` and `POST /recurring/refresh` write. A
candidate's or a confirmed series' identity is `merchant_id` when classification resolved one,
otherwise the normalized description (`recurring.identity_for`) — the same rule everywhere, so a
candidate, a confirmed series and a dismissal are always compared on the same terms.

`merchant` and `category` are shared seed tables with no `UNIQUE (household_id, id)` (V56's own
comment, and V55's before it), so `recurring_series.merchant_id`/`category_id` are plain
`REFERENCES merchant(id)`/`REFERENCES category(id)` and the tenant boundary is this module's job:
`_check_recurring_merchant` mirrors `_check_recurring_category`, which reuses
`routes/transactions.py::_assert_category_allowed` for the combined tenant-and-active check after
a cheap existence lookup, exactly as `routes/budgets.py::_check_budget_category` does.

`POST /recurring/{seriesId}/end` reads `ended_on` itself and returns 409 when the series has
already ended; `preserve_ended_recurring_series()` (V56) is defence in depth and, if ever reached,
maps to 422 rather than 409 — a deliberate difference from `routes/goals.py`'s and
`routes/register.py`'s own preserve-trigger mapping, because the route's own check is the primary
guard here and a trigger hit means a race the route's read did not see, not the ordinary case.

Every GET opens `SET TRANSACTION READ ONLY` and adds no member — members are written only by
`POST /recurring/refresh`, mirroring `POST /wealth-history/refresh`."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import IDEMPOTENCY_REUSED, connect, first_line
from ..money import _decimal_eq
from ..recurring import (
    Candidate,
    Identity,
    SpendRow,
    detect_candidates,
    identity_for,
    is_overdue,
    next_expected_on,
)
from ..resources import household_currencies
from ..text import normalize_bounded
from .goals import _positive_money
from .transactions import _assert_category_allowed

router = APIRouter(tags=["recurring"])

Cadence = Literal["weekly", "monthly", "quarterly", "annual"]

_LOOKBACK_MONTHS = 18

# The identity SQL for a description-keyed group, matching `recurring.normalized_description`
# exactly — `POST /recurring/refresh` matches over the whole ledger and cannot afford to load
# every row into Python first, so it recomputes identity in SQL rather than reusing the pure
# function; `GET /recurring` and `POST /recurring/confirm` both stay on the Python path.
_MATCH_KEY_SQL = (
    "lower(regexp_replace(btrim(coalesce(t.description_clean, t.description_raw)),"
    " '\\s+', ' ', 'g'))"
)


def _months_back(on: date, months: int) -> date:
    total = on.year * 12 + (on.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(on.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class IdentityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchantId: UUID | None = None
    matchKey: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> IdentityIn:
        if (self.merchantId is not None) == (self.matchKey is not None):
            raise ValueError("exactly one of merchantId or matchKey is required")
        return self

    def to_identity(self) -> Identity:
        return Identity(merchant_id=self.merchantId, match_key=self.matchKey)


class IdentityOut(BaseModel):
    merchantId: UUID | None
    matchKey: str | None


def _out_identity(identity: Identity) -> IdentityOut:
    return IdentityOut(merchantId=identity.merchant_id, matchKey=identity.match_key)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotencyKey: UUID
    identity: IdentityIn
    label: str
    cadence: Cadence
    expectedAmountBase: str
    categoryId: UUID | None = None

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return normalize_bounded(value, "label", 120)

    @field_validator("expectedAmountBase")
    @classmethod
    def _amount(cls, value: str) -> str:
        return _positive_money(value)


class DismissBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity: IdentityIn


class AcceptPriceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedAmountBase: str

    @field_validator("expectedAmountBase")
    @classmethod
    def _amount(cls, value: str) -> str:
        return _positive_money(value)


class EndBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endedReason: str

    @field_validator("endedReason")
    @classmethod
    def _reason(cls, value: str) -> str:
        return normalize_bounded(value, "endedReason", 200)


class ConfirmedSeriesRow(BaseModel):
    id: UUID
    label: str
    identity: IdentityOut
    cadence: str
    categoryId: UUID | None
    categoryName: str | None
    status: Literal["active", "price_changed", "overdue"]
    expectedAmountBase: str
    lastAmountBase: str
    lastSeenOn: date
    nextExpectedOn: date
    memberCount: int
    currency: str


class CandidateRow(BaseModel):
    identity: IdentityOut
    label: str
    cadence: str
    occurrences: int
    firstSeenOn: date
    lastSeenOn: date
    lastAmountBase: str
    typicalAmountBase: str
    categoryId: UUID | None
    categoryName: str | None
    transactionIds: list[UUID]


class RecurringResponse(BaseModel):
    confirmed: list[ConfirmedSeriesRow]
    candidates: list[CandidateRow]


class RecurringRefreshCounts(BaseModel):
    seriesTouched: int
    membersAdded: int


def _translate_recurring_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if constraint == "uq_recurring_series_idempotency":
        return HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
    if constraint in ("uq_recurring_series_open_merchant", "uq_recurring_series_open_match_key"):
        return HTTPException(
            status_code=409, detail="this identity is already a confirmed recurring series"
        )
    if constraint == "recurring_series_identity_shape":
        return HTTPException(
            status_code=422, detail="exactly one of a merchant or a match key is required"
        )
    if constraint == "recurring_series_label_is_bounded":
        return HTTPException(status_code=422, detail="label must be 1-120 characters")
    if constraint == "recurring_series_amount_is_positive":
        return HTTPException(status_code=422, detail="amount must be positive")
    if constraint == "recurring_series_ended_reason_is_bounded":
        return HTTPException(status_code=422, detail="ended reason must be at most 200 characters")
    if exc.sqlstate == "23514" and "an ended recurring series is immutable" in first_line(exc):
        # Defence in depth only: the route itself already checked ended_on before this write, so
        # reaching the trigger means a race the route's own read did not see — 422, not the 409
        # the route's own check returns (plan review's non-negotiable ruling).
        return HTTPException(status_code=422, detail="this recurring series has already ended")
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(
            status_code=422, detail="recurring series violates its required field matrix"
        )
    raise exc


def _check_recurring_category(
    cur: psycopg.Cursor[Any], category_id: UUID | None, household_id: UUID
) -> None:
    if category_id is None:
        return
    cur.execute("SELECT household_id FROM category WHERE id = %s", (category_id,))
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=422, detail="unknown category")
    category_household = found[0]
    if category_household is not None and UUID(str(category_household)) != household_id:
        raise HTTPException(status_code=404, detail="category not found")
    _assert_category_allowed(cur, category_id, household_id)


def _check_recurring_merchant(
    cur: psycopg.Cursor[Any], merchant_id: UUID | None, household_id: UUID
) -> None:
    if merchant_id is None:
        return
    cur.execute("SELECT household_id FROM merchant WHERE id = %s", (merchant_id,))
    found = cur.fetchone()
    if found is None:
        raise HTTPException(status_code=422, detail="unknown merchant")
    merchant_household = found[0]
    if merchant_household is not None and UUID(str(merchant_household)) != household_id:
        raise HTTPException(status_code=404, detail="merchant not found")


def _category_name(cur: psycopg.Cursor[Any], category_id: UUID | None) -> str | None:
    if category_id is None:
        return None
    cur.execute("SELECT name FROM category WHERE id = %s", (category_id,))
    row = cur.fetchone()
    return None if row is None else str(row[0])


_SPEND_ROWS = """
    SELECT t.id, t.booked_date, -t.amount_base, t.merchant_id,
           COALESCE(m.display_name, m.canonical_name), t.description_clean, t.description_raw,
           t.category_id
      FROM v_transaction_reportable t
      LEFT JOIN merchant m ON m.id = t.merchant_id
     WHERE t.household_id = %s AND t.txn_type <> 'income' AND t.booked_date >= %s
     ORDER BY t.booked_date
"""


def _spend_rows(cur: psycopg.Cursor[Any], household_id: UUID, since: date) -> list[SpendRow]:
    cur.execute(_SPEND_ROWS, (household_id, since))
    return [
        SpendRow(
            transaction_id=UUID(str(row[0])),
            booked_date=row[1],
            amount_base=Decimal(row[2]),
            merchant_id=None if row[3] is None else UUID(str(row[3])),
            merchant_display_name=row[4],
            description_clean=row[5],
            description_raw=row[6],
            category_id=None if row[7] is None else UUID(str(row[7])),
        )
        for row in cur.fetchall()
    ]


def _load_confirmed(
    cur: psycopg.Cursor[Any], household_id: UUID, series_id: UUID
) -> ConfirmedSeriesRow:
    cur.execute(
        """SELECT merchant_id, match_key, label, cadence::text, expected_amount_base, currency,
                  category_id, last_seen_on
             FROM recurring_series WHERE household_id = %s AND id = %s""",
        (household_id, series_id),
    )
    row = cur.fetchone()
    assert row is not None
    merchant_id, match_key, label, cadence, expected_amount, currency, category_id, last_seen_on = (
        row
    )

    cur.execute(
        """SELECT amount_base FROM recurring_series_member
            WHERE household_id = %s AND series_id = %s
            ORDER BY observed_on DESC, transaction_id DESC LIMIT 1""",
        (household_id, series_id),
    )
    latest = cur.fetchone()
    expected = Decimal(expected_amount)
    latest_amount = expected if latest is None else Decimal(latest[0])

    cur.execute(
        "SELECT count(*) FROM recurring_series_member WHERE household_id = %s AND series_id = %s",
        (household_id, series_id),
    )
    member_count = int(cur.fetchone()[0])  # type: ignore[index]

    status: Literal["active", "price_changed", "overdue"] = "active"
    if latest_amount != expected:
        status = "price_changed"
    elif is_overdue(last_seen_on, cadence, today=date.today()):
        status = "overdue"

    return ConfirmedSeriesRow(
        id=series_id,
        label=str(label),
        identity=_out_identity(
            Identity(
                merchant_id=None if merchant_id is None else UUID(str(merchant_id)),
                match_key=match_key,
            )
        ),
        cadence=cadence,
        categoryId=None if category_id is None else UUID(str(category_id)),
        categoryName=_category_name(cur, category_id),
        status=status,
        expectedAmountBase=str(expected),
        lastAmountBase=str(latest_amount),
        lastSeenOn=last_seen_on,
        nextExpectedOn=next_expected_on(last_seen_on, cadence),
        memberCount=member_count,
        currency=str(currency).strip(),
    )


def _candidate_row(cur: psycopg.Cursor[Any], candidate: Candidate) -> CandidateRow:
    return CandidateRow(
        identity=_out_identity(candidate.identity),
        label=candidate.label,
        cadence=candidate.cadence,
        occurrences=candidate.occurrences,
        firstSeenOn=candidate.first_seen_on,
        lastSeenOn=candidate.last_seen_on,
        lastAmountBase=str(candidate.last_amount_base),
        typicalAmountBase=str(candidate.typical_amount_base),
        categoryId=candidate.category_id,
        categoryName=_category_name(cur, candidate.category_id),
        transactionIds=candidate.transaction_ids,
    )


@router.get("/recurring", response_model=RecurringResponse)
def read_recurring(owner: Annotated[OwnerSession, Depends(require_owner)]) -> RecurringResponse:
    since = _months_back(date.today(), _LOOKBACK_MONTHS)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        detected = detect_candidates(_spend_rows(cur, owner.household_id, since))

        cur.execute(
            """SELECT id FROM recurring_series
                WHERE household_id = %s AND ended_on IS NULL ORDER BY label""",
            (owner.household_id,),
        )
        confirmed = [
            _load_confirmed(cur, owner.household_id, UUID(str(r[0]))) for r in cur.fetchall()
        ]
        open_identities = {
            Identity(row.identity.merchantId, row.identity.matchKey) for row in confirmed
        }

        cur.execute(
            "SELECT merchant_id, match_key FROM recurring_dismissal WHERE household_id = %s",
            (owner.household_id,),
        )
        dismissed_identities = {
            Identity(None if r[0] is None else UUID(str(r[0])), r[1]) for r in cur.fetchall()
        }

        candidates = [
            _candidate_row(cur, candidate)
            for candidate in detected
            if candidate.identity not in open_identities
            and candidate.identity not in dismissed_identities
        ]
    return RecurringResponse(confirmed=confirmed, candidates=candidates)


@router.post("/recurring/confirm", response_model=ConfirmedSeriesRow)
def confirm_recurring(
    body: ConfirmBody,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> ConfirmedSeriesRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT id, merchant_id, match_key, label, cadence::text, expected_amount_base,
                          category_id
                     FROM recurring_series WHERE household_id = %s AND idempotency_key = %s""",
                (owner.household_id, body.idempotencyKey),
            )
            existing = cur.fetchone()
            if existing is not None:
                eid, emerchant, ematch, elabel, ecadence, eamount, ecategory = existing
                existing_identity = Identity(
                    None if emerchant is None else UUID(str(emerchant)), ematch
                )
                matches = (
                    existing_identity == body.identity.to_identity()
                    and str(elabel) == body.label
                    and ecadence == body.cadence
                    and _decimal_eq(Decimal(eamount), body.expectedAmountBase)
                    and (None if ecategory is None else UUID(str(ecategory))) == body.categoryId
                )
                if not matches:
                    raise HTTPException(status_code=409, detail=IDEMPOTENCY_REUSED)
                response.status_code = status.HTTP_200_OK
                return _load_confirmed(cur, owner.household_id, UUID(str(eid)))

            _check_recurring_merchant(cur, body.identity.merchantId, owner.household_id)
            _check_recurring_category(cur, body.categoryId, owner.household_id)

            base, _secondary = household_currencies(cur, owner.household_id)
            since = _months_back(date.today(), _LOOKBACK_MONTHS)
            rows = _spend_rows(cur, owner.household_id, since)
            target = body.identity.to_identity()
            matching = [
                row
                for row in rows
                if identity_for(row.merchant_id, row.description_clean, row.description_raw)
                == target
            ]
            if not matching:
                raise HTTPException(
                    status_code=422, detail="no matching spending rows found for this identity"
                )
            first_seen = min(row.booked_date for row in matching)
            last_seen = max(row.booked_date for row in matching)

            cur.execute(
                """INSERT INTO recurring_series (household_id, idempotency_key, label, merchant_id,
                       match_key, cadence, expected_amount_base, currency, category_id,
                       first_seen_on, last_seen_on)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    body.idempotencyKey,
                    body.label,
                    body.identity.merchantId,
                    body.identity.matchKey,
                    body.cadence,
                    body.expectedAmountBase,
                    base,
                    body.categoryId,
                    first_seen,
                    last_seen,
                ),
            )
            created = cur.fetchone()
            assert created is not None
            series_id = UUID(str(created[0]))
            response.status_code = status.HTTP_201_CREATED
            for row in matching:
                cur.execute(
                    """INSERT INTO recurring_series_member (household_id, series_id,
                           transaction_id, observed_on, amount_base)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        owner.household_id,
                        series_id,
                        row.transaction_id,
                        row.booked_date,
                        row.amount_base,
                    ),
                )
            return _load_confirmed(cur, owner.household_id, series_id)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/dismiss")
def dismiss_recurring(
    body: DismissBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> dict[str, bool]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        _check_recurring_merchant(cur, body.identity.merchantId, owner.household_id)
        if body.identity.merchantId is not None:
            cur.execute(
                """INSERT INTO recurring_dismissal (household_id, merchant_id)
                   VALUES (%s, %s)
                   ON CONFLICT (household_id, merchant_id) WHERE merchant_id IS NOT NULL
                   DO NOTHING""",
                (owner.household_id, body.identity.merchantId),
            )
        else:
            cur.execute(
                """INSERT INTO recurring_dismissal (household_id, match_key)
                   VALUES (%s, %s)
                   ON CONFLICT (household_id, match_key) WHERE match_key IS NOT NULL
                   DO NOTHING""",
                (owner.household_id, body.identity.matchKey),
            )
    return {"dismissed": True}


@router.post("/recurring/{seriesId}/accept-price", response_model=ConfirmedSeriesRow)
def accept_price(
    seriesId: UUID, body: AcceptPriceBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> ConfirmedSeriesRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT expected_amount_base, ended_on FROM recurring_series
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, seriesId),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="recurring series not found")
            current_expected, ended_on = found
            if ended_on is not None:
                raise HTTPException(
                    status_code=409, detail="this recurring series has already ended"
                )
            if not _decimal_eq(Decimal(current_expected), body.expectedAmountBase):
                raise HTTPException(
                    status_code=409,
                    detail="the expected amount has changed; reload and try again",
                )
            cur.execute(
                """SELECT amount_base FROM recurring_series_member
                    WHERE household_id = %s AND series_id = %s
                    ORDER BY observed_on DESC, transaction_id DESC LIMIT 1""",
                (owner.household_id, seriesId),
            )
            latest = cur.fetchone()
            if latest is None:
                raise HTTPException(
                    status_code=422, detail="this series has no observed members yet"
                )
            cur.execute(
                """UPDATE recurring_series SET expected_amount_base = %s, updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (latest[0], owner.household_id, seriesId),
            )
            return _load_confirmed(cur, owner.household_id, seriesId)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/{seriesId}/end", response_model=ConfirmedSeriesRow)
def end_recurring(
    seriesId: UUID, body: EndBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> ConfirmedSeriesRow:
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
            cur.execute(
                """UPDATE recurring_series SET ended_on = %s, ended_reason = %s, updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (date.today(), body.endedReason, owner.household_id, seriesId),
            )
            return _load_confirmed(cur, owner.household_id, seriesId)
    except psycopg.Error as exc:
        raise _translate_recurring_error(exc) from None


@router.post("/recurring/refresh", response_model=RecurringRefreshCounts)
def refresh_recurring(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> RecurringRefreshCounts:
    """Adds members to every open series — never a GET's job (module docstring). Idempotent: each
    series' own `last_seen_on` only ever advances, so a matching row already added is never
    fetched again on a later call."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            """SELECT id, merchant_id, match_key, last_seen_on FROM recurring_series
                WHERE household_id = %s AND ended_on IS NULL""",
            (owner.household_id,),
        )
        open_series = cur.fetchall()

        series_touched = 0
        members_added = 0
        for series_id, merchant_id, match_key, last_seen_on in open_series:
            series_id = UUID(str(series_id))
            if merchant_id is not None:
                cur.execute(
                    """SELECT t.id, t.booked_date, -t.amount_base
                         FROM v_transaction_reportable t
                        WHERE t.household_id = %s AND t.txn_type <> 'income'
                          AND t.merchant_id = %s AND t.booked_date > %s
                        ORDER BY t.booked_date""",
                    (owner.household_id, merchant_id, last_seen_on),
                )
            else:
                cur.execute(
                    f"""SELECT t.id, t.booked_date, -t.amount_base
                          FROM v_transaction_reportable t
                         WHERE t.household_id = %s AND t.txn_type <> 'income'
                           AND t.merchant_id IS NULL AND t.booked_date > %s
                           AND {_MATCH_KEY_SQL} = %s
                         ORDER BY t.booked_date""",
                    (owner.household_id, last_seen_on, match_key),
                )
            new_rows = cur.fetchall()
            if not new_rows:
                continue
            for transaction_id, booked_date, amount_base in new_rows:
                cur.execute(
                    """INSERT INTO recurring_series_member (household_id, series_id,
                           transaction_id, observed_on, amount_base)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (household_id, series_id, transaction_id) DO NOTHING""",
                    (owner.household_id, series_id, transaction_id, booked_date, amount_base),
                )
                members_added += cur.rowcount
            cur.execute(
                """UPDATE recurring_series SET last_seen_on = %s, updated_at = now()
                    WHERE household_id = %s AND id = %s""",
                (max(row[1] for row in new_rows), owner.household_id, series_id),
            )
            series_touched += 1
    return RecurringRefreshCounts(seriesTouched=series_touched, membersAdded=members_added)
