"""Phase 4.1 Task 3: `GET/POST /recurring` and its sub-routes (spec "Discovery proposes, the
owner decides").

Discovery writes nothing until a candidate is confirmed: every candidate scenario below reads
`GET /recurring` after writing straight to the ledger, and only the confirm/dismiss/refresh
scenarios write through a route. Skipped when no database is reachable. Every name, label and
amount is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, _clean, _connect
from .test_spending_live import _another_household, a_category, a_spend
from .test_transfer_writes_live import Owner
from .test_transfers_live import client


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Owner]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM household")
        seeded: list[UUID] = [row[0] for row in cur.fetchall()]
    fixture: Owner | None = None
    try:
        fixture = Owner(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE recurring_series_member, recurring_series, recurring_dismissal")
        conn.commit()
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
            # Postgres refuses TRUNCATE on a table with an incoming FK unless the referencing
            # table is truncated in the very same statement — true even though `_clean`'s own
            # TRUNCATE ... transaction CASCADE already emptied recurring_series_member.
            cur.execute("TRUNCATE recurring_series_member, recurring_series, recurring_dismissal")
            cur.execute("DELETE FROM merchant WHERE canonical_name LIKE 'Synthetic recurring%'")
            cur.execute("DELETE FROM category WHERE name LIKE 'Synthetic recurring%'")
            if fixture is not None:
                cur.execute("DELETE FROM app_user WHERE id = %s", (fixture.user_id,))
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM person WHERE NOT (household_id = ANY(%s))", (seeded,))
            cur.execute("DELETE FROM household WHERE NOT (id = ANY(%s))", (seeded,))
        conn.commit()
        conn.close()


def a_merchant(live: Owner, name: str, *, household: UUID | None = None) -> UUID:
    owner = household or live.household_id
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO merchant (household_id, canonical_name) VALUES (%s, %s) RETURNING id",
            (owner, name),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def the_recurring() -> dict[str, Any]:
    response = client().get("/recurring", headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def candidate_for(
    body: dict[str, Any], *, merchant_id: UUID | None = None, match_key: str | None = None
) -> dict[str, Any] | None:
    key = None if merchant_id is None else str(merchant_id)
    return next(
        (
            row
            for row in body["candidates"]
            if row["identity"]["merchantId"] == key and row["identity"]["matchKey"] == match_key
        ),
        None,
    )


def confirm(
    idempotency_key: UUID,
    *,
    merchant_id: UUID | None = None,
    match_key: str | None = None,
    label: str = "Synthetic recurring confirm",
    cadence: str = "monthly",
    amount: str = "50.0000",
    category_id: UUID | None = None,
) -> httpx.Response:
    response: httpx.Response = client().post(
        "/recurring/confirm",
        json={
            "idempotencyKey": str(idempotency_key),
            "identity": {
                "merchantId": None if merchant_id is None else str(merchant_id),
                "matchKey": match_key,
            },
            "label": label,
            "cadence": cadence,
            "expectedAmountBase": amount,
            "categoryId": None if category_id is None else str(category_id),
        },
        headers=ORIGIN,
    )
    return response


# Every date below is relative to `TODAY` rather than a fixed calendar date: `status` depends on
# how far `last_seen_on` sits from the moment the test actually runs (`recurring.is_overdue`), so
# a fixed date would drift into "overdue" as the calendar moves past it.
TODAY = date.today()
# Ends 30 days before TODAY, leaving room for a still-not-future "later" row in the refresh and
# price-change scenarios below.
MONTHLY = [TODAY - timedelta(days=d) for d in (120, 90, 60, 30)]
LATER = TODAY
# The fixture's own account (`test_ingest_route_live.Fixture`) opens on 2026-01-01 and refuses
# any earlier booked_date, so the "overdue" scenario starts there rather than further back —
# `is_overdue` only needs its own gap from "now" to exceed half a cadence, not real history.
_ACCOUNT_ANCHOR = date(2026, 1, 1)
OLD_MONTHLY = [_ACCOUNT_ANCHOR + timedelta(days=d) for d in (0, 30, 60, 90)]


def a_monthly_group(
    live: Owner,
    *,
    merchant_id: UUID | None = None,
    description: str = "Synthetic recurring subscription",
    amount: str = "-50",
    dates: list[date] = MONTHLY,
    category_id: UUID | None = None,
) -> list[UUID]:
    return [
        a_spend(
            live,
            live.account_id,
            amount,
            on,
            "expense",
            merchant_id=merchant_id,
            description=description,
            category_id=category_id,
        )
        for on in dates
    ]


# ---------------------------------------------------------------------------------------


def test_regular_monthly_group_is_proposed_with_cadence_and_median_amount(live: Owner) -> None:
    merchant = a_merchant(live, "Synthetic recurring gym")
    a_monthly_group(live, merchant_id=merchant, amount="-50")

    body = the_recurring()
    row = candidate_for(body, merchant_id=merchant)
    assert row is not None
    assert row["cadence"] == "monthly"
    assert row["occurrences"] == 4
    assert row["typicalAmountBase"] == "50.0000"
    assert row["lastAmountBase"] == "50.0000"
    assert row["firstSeenOn"] == str(MONTHLY[0])
    assert row["lastSeenOn"] == str(MONTHLY[-1])


def test_irregular_group_is_not_proposed(live: Owner) -> None:
    merchant = a_merchant(live, "Synthetic recurring irregular")
    # Gaps of 5, 50 and 200 days: the median (50) fits no cadence window at all, so nothing about
    # the two-thirds rule even comes into play — this group is irregular on its own median.
    irregular = [date(2026, 1, 1), date(2026, 1, 6), date(2026, 2, 25), date(2026, 9, 12)]
    a_monthly_group(live, merchant_id=merchant, dates=irregular)

    body = the_recurring()
    assert candidate_for(body, merchant_id=merchant) is None


def test_two_occurrence_group_is_not_proposed(live: Owner) -> None:
    merchant = a_merchant(live, "Synthetic recurring too-few")
    a_monthly_group(live, merchant_id=merchant, dates=MONTHLY[:2])

    body = the_recurring()
    assert candidate_for(body, merchant_id=merchant) is None


def test_confirmation_writes_series_and_members(live: Owner) -> None:
    description = "Synthetic recurring gym membership"
    txn_ids = a_monthly_group(live, description=description, amount="-50")

    body = the_recurring()
    match_key = description.lower()
    row = candidate_for(body, match_key=match_key)
    assert row is not None
    assert sorted(row["transactionIds"]) == sorted(str(i) for i in txn_ids)

    response = confirm(
        uuid4(), match_key=match_key, cadence=row["cadence"], amount=row["typicalAmountBase"]
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["memberCount"] == 4
    assert created["status"] == "active"

    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM recurring_series_member
                WHERE household_id = %s AND series_id = %s""",
            (live.household_id, created["id"]),
        )
        assert cur.fetchone()[0] == 4  # type: ignore[index]

    # A confirmed identity is no longer offered as a candidate.
    after = the_recurring()
    assert candidate_for(after, match_key=match_key) is None
    assert any(c["id"] == created["id"] for c in after["confirmed"])


def test_replay_200_and_conflict_409(live: Owner) -> None:
    description = "Synthetic recurring replay"
    a_monthly_group(live, description=description, amount="-40")
    match_key = description.lower()
    key = uuid4()

    first = confirm(key, match_key=match_key, amount="40.0000")
    assert first.status_code == 201, first.text
    series_id = first.json()["id"]

    replay = confirm(key, match_key=match_key, amount="40.0000")
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == series_id

    conflict = confirm(key, match_key=match_key, amount="41.0000")
    assert conflict.status_code == 409, conflict.text


def test_already_confirmed_identity_is_refused(live: Owner) -> None:
    description = "Synthetic recurring double confirm"
    a_monthly_group(live, description=description, amount="-40")
    match_key = description.lower()
    assert confirm(uuid4(), match_key=match_key, amount="40.0000").status_code == 201
    second = confirm(uuid4(), match_key=match_key, amount="45.0000")
    assert second.status_code == 409, second.text


def test_dismissal_suppresses_a_candidate(live: Owner) -> None:
    merchant = a_merchant(live, "Synthetic recurring dismiss")
    a_monthly_group(live, merchant_id=merchant)
    assert candidate_for(the_recurring(), merchant_id=merchant) is not None

    response = client().post(
        "/recurring/dismiss", json={"identity": {"merchantId": str(merchant)}}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    assert candidate_for(the_recurring(), merchant_id=merchant) is None

    # Idempotent replay.
    replay = client().post(
        "/recurring/dismiss", json={"identity": {"merchantId": str(merchant)}}, headers=ORIGIN
    )
    assert replay.status_code == 200, replay.text


def test_refresh_adds_a_later_row_while_get_leaves_it_untouched(live: Owner) -> None:
    description = "Synthetic recurring refresh"
    a_monthly_group(live, description=description, amount="-30")
    match_key = description.lower()
    created = confirm(uuid4(), match_key=match_key, amount="30.0000").json()
    series_id = created["id"]

    a_spend(live, live.account_id, "-30", LATER, "expense", description=description)

    # GET never writes: the new row is not yet a member.
    unchanged = the_recurring()
    unchanged_row = next(c for c in unchanged["confirmed"] if c["id"] == series_id)
    assert unchanged_row["memberCount"] == 4
    assert unchanged_row["lastSeenOn"] == str(MONTHLY[-1])

    refreshed = client().post("/recurring/refresh", headers=ORIGIN)
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json() == {"seriesTouched": 1, "membersAdded": 1}

    after = the_recurring()
    after_row = next(c for c in after["confirmed"] if c["id"] == series_id)
    assert after_row["memberCount"] == 5
    assert after_row["lastSeenOn"] == str(LATER)

    # A second refresh with nothing new touches no series.
    again = client().post("/recurring/refresh", headers=ORIGIN)
    assert again.json() == {"seriesTouched": 0, "membersAdded": 0}


def test_price_change_reported_and_accepted_with_stale_refusal(live: Owner) -> None:
    description = "Synthetic recurring price change"
    a_monthly_group(live, description=description, amount="-20")
    match_key = description.lower()
    created = confirm(uuid4(), match_key=match_key, amount="20.0000").json()
    series_id = created["id"]

    a_spend(live, live.account_id, "-25", LATER, "expense", description=description)
    client().post("/recurring/refresh", headers=ORIGIN)

    changed = the_recurring()
    changed_row = next(c for c in changed["confirmed"] if c["id"] == series_id)
    assert changed_row["status"] == "price_changed"
    assert changed_row["lastAmountBase"] == "25.0000"
    assert changed_row["expectedAmountBase"] == "20.0000"

    stale = client().post(
        f"/recurring/{series_id}/accept-price",
        json={"expectedAmountBase": "19.0000"},
        headers=ORIGIN,
    )
    assert stale.status_code == 409, stale.text

    accepted = client().post(
        f"/recurring/{series_id}/accept-price",
        json={"expectedAmountBase": "20.0000"},
        headers=ORIGIN,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["expectedAmountBase"] == "25.0000"
    assert accepted.json()["status"] == "active"


def test_overdue_series_reported_and_ended(live: Owner) -> None:
    description = "Synthetic recurring overdue"
    a_monthly_group(live, description=description, amount="-15", dates=OLD_MONTHLY)
    match_key = description.lower()
    created = confirm(uuid4(), match_key=match_key, amount="15.0000").json()
    series_id = created["id"]

    overdue = the_recurring()
    row = next(c for c in overdue["confirmed"] if c["id"] == series_id)
    assert row["status"] == "overdue"

    ended = client().post(
        f"/recurring/{series_id}/end",
        json={"endedReason": "synthetic: cancelled the membership"},
        headers=ORIGIN,
    )
    assert ended.status_code == 200, ended.text

    after = the_recurring()
    assert not any(c["id"] == series_id for c in after["confirmed"])

    # Ending an already-ended series is refused.
    again = client().post(
        f"/recurring/{series_id}/end",
        json={"endedReason": "synthetic: already cancelled"},
        headers=ORIGIN,
    )
    assert again.status_code == 409, again.text


def test_foreign_and_unknown_category_are_refused(live: Owner) -> None:
    description = "Synthetic recurring category"
    a_monthly_group(live, description=description, amount="-10")
    match_key = description.lower()

    theirs, _account = _another_household(live)
    foreign = a_category(live, "Synthetic recurring foreign category", "need", household=theirs)
    refused = confirm(uuid4(), match_key=match_key, amount="10.0000", category_id=foreign)
    assert refused.status_code == 404, refused.text

    unknown = confirm(uuid4(), match_key=match_key, amount="10.0000", category_id=uuid4())
    assert unknown.status_code == 422, unknown.text


def test_foreign_household_refusals(live: Owner) -> None:
    description = "Synthetic recurring foreign household"
    a_monthly_group(live, description=description, amount="-10")
    match_key = description.lower()
    created = confirm(uuid4(), match_key=match_key, amount="10.0000").json()
    series_id = created["id"]

    theirs, _account = _another_household(live)
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(),
        household_id=theirs,
        session_id=uuid4(),
        email="other-recurring-owner@example.invalid",
    )
    try:
        assert the_recurring()["confirmed"] == []
        assert (
            client()
            .post(
                f"/recurring/{series_id}/end",
                json={"endedReason": "synthetic: not mine"},
                headers=ORIGIN,
            )
            .status_code
            == 404
        )
        assert (
            client()
            .post(
                f"/recurring/{series_id}/accept-price",
                json={"expectedAmountBase": "10.0000"},
                headers=ORIGIN,
            )
            .status_code
            == 404
        )
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner


def test_routes_require_owner_session() -> None:
    app.dependency_overrides.clear()
    assert client().get("/recurring", headers=ORIGIN).status_code == 401
    assert (
        client()
        .post(
            "/recurring/confirm",
            json={
                "idempotencyKey": str(uuid4()),
                "identity": {"matchKey": "synthetic"},
                "label": "synthetic",
                "cadence": "monthly",
                "expectedAmountBase": "10.0000",
                "categoryId": None,
            },
            headers=ORIGIN,
        )
        .status_code
        == 401
    )
