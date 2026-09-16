"""Phase 4.1 Task 1: `GET/PATCH /recurring`, `POST /recurring/refresh`, `.../remove` and
`.../dismiss` (spec "Recurring payments are simply just not a subscription").

Discovery is opt-out: `POST /recurring/refresh` is the only writer, and the owner never confirms
anything — every scenario below reads `GET /recurring` after writing straight to the ledger, then
calls refresh. Skipped when no database is reachable. Every name, label and amount is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from oneledger import auth
from oneledger.main import app

from .live_database import clear_synthetic_account_valuation_periods
from .test_ingest_route_live import ORIGIN, _clean, _connect
from .test_spending_live import _another_household, a_spend
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
            # recurring_series_period (V58) also references recurring_series and must truncate
            # in the same statement, the same rule this fixture's own teardown already follows
            # for recurring_series_member.
            cur.execute(
                "TRUNCATE recurring_series_period, recurring_series_member, recurring_series, "
                "recurring_dismissal"
            )
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
            cur.execute(
                "TRUNCATE recurring_series_period, recurring_series_member, recurring_series, "
                "recurring_dismissal"
            )
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


def _seed_category(live: Owner, slug: str) -> UUID:
    with live.conn.cursor() as cur:
        cur.execute("SELECT id FROM category WHERE slug = %s AND household_id IS NULL", (slug,))
        found = cur.fetchone()[0]  # type: ignore[index]
    return UUID(str(found))


def _foreign_spend(
    live: Owner,
    native_amount: str,
    native_currency: str,
    posted_amount: str,
    on: date,
    description: str,
) -> UUID:
    """A charge whose statement recorded a foreign original pair distinct from the account's own
    posted currency — `a_spend` (test_spending_live.py) carries no such pair, so this stays local
    to this file rather than widening a shared helper other suites also use."""
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                   amount, currency, original_amount, original_currency, fx_rate_date,
                   fx_rate_to_base, fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
                   source, dedupe_hash)
               VALUES (%s, %s, %s, %s, %s,
                       (SELECT currency FROM account WHERE id = %s), %s, %s, %s, 1, 0.25, %s, %s,
                       'expense', 'manual', %s)
               RETURNING id""",
            (
                live.household_id,
                live.account_id,
                on,
                description,
                posted_amount,
                live.account_id,
                native_amount,
                native_currency,
                on,
                posted_amount,
                (Decimal(posted_amount) * Decimal("0.25")).quantize(Decimal("0.0001")),
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    return UUID(str(found))


def the_recurring() -> dict[str, Any]:
    response = client().get("/recurring", headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def refresh() -> dict[str, Any]:
    response = client().post("/recurring/refresh", headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def patch_series(series_id: str, **fields: bool | str) -> dict[str, Any]:
    response = client().patch(f"/recurring/{series_id}", json=fields, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def remove_series(series_id: str) -> httpx.Response:
    response: httpx.Response = client().post(f"/recurring/{series_id}/remove", headers=ORIGIN)
    return response


def _row_for(body: dict[str, Any], label: str) -> dict[str, Any] | None:
    return next((row for row in body["series"] if row["label"] == label), None)


# Every date below is relative to `TODAY` rather than a fixed calendar date: `status` depends on
# how far `last_seen_on` sits from the moment the test actually runs (`recurring.is_overdue`), so
# a fixed date would drift into "overdue" as the calendar moves past it.
TODAY = date.today()
# Ends 30 days before TODAY, leaving room for a still-not-future "later" row in extend scenarios.
MONTHLY = [TODAY - timedelta(days=d) for d in (120, 90, 60, 30)]
LATER = TODAY
# 50 days stale: more than half a monthly cadence (15 days) past next-expected, so "overdue", but
# short of the two-cadence (60-day) lapse threshold Task 5 adds, so still current.
OVERDUE_MONTHLY = [TODAY - timedelta(days=d) for d in (140, 110, 80, 50)]
# Well past the two-cadence lapse threshold: no longer current, so "ended".
LAPSED_MONTHLY = [TODAY - timedelta(days=d) for d in (210, 180, 150, 120)]


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


def test_refresh_creates_a_subscription_from_streaming_and_not_from_utilities(live: Owner) -> None:
    streaming = _seed_category(live, "entertainment-streaming")
    utilities = _seed_category(live, "utilities")
    a_monthly_group(
        live, description="Synthetic recurring streaming", amount="-15", category_id=streaming
    )
    a_monthly_group(
        live, description="Synthetic recurring utility", amount="-60", category_id=utilities
    )

    result = refresh()
    assert result["seriesCreated"] == 2

    body = the_recurring()
    streaming_row = _row_for(body, "Synthetic recurring streaming")
    utility_row = _row_for(body, "Synthetic recurring utility")
    assert streaming_row is not None and streaming_row["isSubscription"] is True
    assert utility_row is not None and utility_row["isSubscription"] is False


def test_an_uncategorised_stable_group_is_a_subscription_and_a_varying_one_is_not(
    live: Owner,
) -> None:
    a_monthly_group(live, description="Synthetic recurring stable", amount="-12")
    for on, amount in zip(MONTHLY, ["-100", "-100", "-100", "-112"], strict=True):
        a_spend(
            live, live.account_id, amount, on, "expense", description="Synthetic recurring varying"
        )

    refresh()
    body = the_recurring()
    stable_row = _row_for(body, "Synthetic recurring stable")
    varying_row = _row_for(body, "Synthetic recurring varying")
    assert stable_row is not None and stable_row["isSubscription"] is True
    assert varying_row is not None and varying_row["isSubscription"] is False


def test_a_second_refresh_changes_nothing(live: Owner) -> None:
    a_monthly_group(live, description="Synthetic recurring idempotent", amount="-25")
    first = refresh()
    assert first["seriesCreated"] == 1
    second = refresh()
    assert second == {"seriesCreated": 0, "seriesTouched": 0, "membersAdded": 0}


def test_get_recurring_changes_nothing(live: Owner) -> None:
    a_monthly_group(live, description="Synthetic recurring get-only", amount="-25")
    assert the_recurring()["series"] == []
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM recurring_series WHERE household_id = %s", (live.household_id,)
        )
        assert cur.fetchone()[0] == 0  # type: ignore[index]


def test_an_owner_flip_and_a_rename_survive_refresh(live: Owner) -> None:
    description = "Synthetic recurring flip"
    a_monthly_group(live, description=description, amount="-30")
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    series_id = row["id"]
    assert row["isSubscription"] is True

    patched = patch_series(series_id, isSubscription=False, label="Owner renamed subscription")
    assert patched["isSubscription"] is False
    assert patched["label"] == "Owner renamed subscription"

    a_spend(live, live.account_id, "-30", LATER, "expense", description=description)
    refresh()
    after = next(r for r in the_recurring()["series"] if r["id"] == series_id)
    assert after["isSubscription"] is False
    assert after["label"] == "Owner renamed subscription"


def test_a_removed_payment_is_never_recreated(live: Owner) -> None:
    description = "Synthetic recurring removed"
    a_monthly_group(live, description=description, amount="-45")
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    removed = remove_series(row["id"])
    assert removed.status_code == 200, removed.text
    assert _row_for(the_recurring(), description) is None

    # Fresh charges on the same identity and amount, well after removal — the anchored dismissal
    # blocks Create regardless of how "aged out" the original evidence now is.
    for on in (LATER + timedelta(days=d) for d in (30, 60, 90)):
        a_spend(live, live.account_id, "-45", on, "expense", description=description)
    refresh()
    assert _row_for(the_recurring(), description) is None

    again = remove_series(row["id"])
    assert again.status_code == 409, again.text


def test_two_overlapping_services_become_two_series_and_extend_the_nearest_one(
    live: Owner,
) -> None:
    description = "Synthetic recurring overlap"
    base = MONTHLY[0]
    dates = [base + timedelta(days=15 * i) for i in range(8)]
    amounts = ["-10", "-50", "-10", "-50", "-10", "-50", "-10", "-50"]
    for on, amount in zip(dates, amounts, strict=True):
        a_spend(live, live.account_id, amount, on, "expense", description=description)

    refresh()
    both = [row for row in the_recurring()["series"] if row["label"] == description]
    assert len(both) == 2
    assert sorted(row["nativeAmount"] for row in both) == ["10.0000", "50.0000"]

    later_on = dates[-1] + timedelta(days=15)
    a_spend(live, live.account_id, "-11", later_on, "expense", description=description)
    refresh()
    after = [row for row in the_recurring()["series"] if row["label"] == description]
    assert sorted(row["nativeAmount"] for row in after) == ["11.0000", "50.0000"]


def test_a_price_change_over_20_percent_still_extends_the_single_series(live: Owner) -> None:
    description = "Synthetic recurring big price change"
    a_monthly_group(live, description=description, amount="-20")
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    series_id = row["id"]

    a_spend(live, live.account_id, "-35", LATER, "expense", description=description)
    refresh()
    after = next(r for r in the_recurring()["series"] if r["id"] == series_id)
    assert after["nativeAmount"] == "35.0000"


def test_several_new_price_charges_in_one_import_all_extend_the_series(live: Owner) -> None:
    """Each joined charge moves the series forward, so the second and third new-price charges are
    measured from the one before them, not from the old last date."""
    description = "Synthetic recurring price change batch"
    a_monthly_group(live, description=description, amount="-20")
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None

    for days in (30, 60, 90):
        a_spend(
            live,
            live.account_id,
            "-35",
            MONTHLY[-1] + timedelta(days=days),
            "expense",
            description=description,
        )
    refresh()
    rows = [r for r in the_recurring()["series"] if r["label"] == description]
    assert [r["id"] for r in rows] == [row["id"]]
    assert rows[0]["nativeAmount"] == "35.0000"
    assert refresh() == {"seriesCreated": 0, "seriesTouched": 0, "membersAdded": 0}


def test_a_cancelled_then_resumed_payment_is_detected_again_from_new_charges_only(
    live: Owner,
) -> None:
    description = "Synthetic recurring resumed"
    a_monthly_group(live, description=description, amount="-18")
    refresh()
    first_row = _row_for(the_recurring(), description)
    assert first_row is not None
    remove_series(first_row["id"])

    for on in (LATER + timedelta(days=d) for d in (30, 60, 90)):
        a_spend(live, live.account_id, "-40", on, "expense", description=description)
    refresh()
    rows = [row for row in the_recurring()["series"] if row["label"] == description]
    assert len(rows) == 1
    assert rows[0]["id"] != first_row["id"]
    assert rows[0]["nativeAmount"] == "40.0000"


def test_a_series_created_before_v57_gets_its_flag_and_amounts_on_refresh(live: Owner) -> None:
    streaming = _seed_category(live, "entertainment-streaming")
    description = "Synthetic recurring legacy"
    txn_ids = a_monthly_group(live, description=description, amount="-9", category_id=streaming)
    with live.conn.cursor() as cur:
        cur.execute("SELECT base_currency FROM household WHERE id = %s", (live.household_id,))
        base = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO recurring_series (household_id, idempotency_key, label, match_key,
                   cadence, expected_amount_base, currency, category_id, first_seen_on,
                   last_seen_on)
               VALUES (%s, %s, %s, %s, 'monthly', 9, %s, %s, %s, %s) RETURNING id""",
            (
                live.household_id,
                uuid4(),
                description,
                description.lower(),
                base,
                streaming,
                MONTHLY[0],
                MONTHLY[-1],
            ),
        )
        series_id = cur.fetchone()[0]  # type: ignore[index]
        for txn_id, on in zip(txn_ids, MONTHLY, strict=True):
            cur.execute(
                """INSERT INTO recurring_series_member (household_id, series_id, transaction_id,
                       observed_on, amount_base)
                   VALUES (%s, %s, %s, %s, 9)""",
                (live.household_id, series_id, txn_id, on),
            )
    live.conn.commit()

    # Review OL-003: later charges arriving before the first refresh must extend this series, not
    # form a second one while its native amount is still null.
    for days in (30, 60, 90):
        a_spend(
            live,
            live.account_id,
            "-9",
            MONTHLY[-1] + timedelta(days=days),
            "expense",
            description=description,
            category_id=streaming,
        )

    refresh()
    rows = [r for r in the_recurring()["series"] if r["label"] == description]
    assert [r["id"] for r in rows] == [str(series_id)]
    assert rows[0]["nativeAmount"] == "9.0000"
    assert rows[0]["isSubscription"] is True


def test_a_charge_in_a_foreign_currency_keeps_its_original_pair(live: Owner) -> None:
    description = "Synthetic recurring foreign currency"
    for on in MONTHLY:
        _foreign_spend(live, "-18.5000", "EUR", "-20.0000", on, description)

    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    assert row["nativeAmount"] == "18.5000"
    assert row["nativeCurrency"] == "EUR"


def test_overdue_status(live: Owner) -> None:
    description = "Synthetic recurring overdue"
    a_monthly_group(live, description=description, amount="-15", dates=OVERDUE_MONTHLY)
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    assert row["status"] == "overdue"
    assert row["isCurrent"] is True


def test_a_second_refresh_ignores_one_offs_that_overlapped_a_subscription(live: Owner) -> None:
    """Review OL-001. The first refresh turns the monthly charges into members. A second refresh
    that only saw the leftover one-off purchases would find they no longer overlap anything, merge
    them, and propose a false monthly payment; detection must cluster the members too."""
    description = "Synthetic recurring with one-offs"
    a_monthly_group(live, description=description, amount="-10")
    for days, amount in ((15, "-50"), (45, "-100"), (75, "-200")):
        a_spend(
            live,
            live.account_id,
            amount,
            MONTHLY[0] + timedelta(days=days),
            "expense",
            description=description,
        )

    first = refresh()
    assert first["seriesCreated"] == 1
    assert refresh() == {"seriesCreated": 0, "seriesTouched": 0, "membersAdded": 0}
    rows = [row for row in the_recurring()["series"] if row["label"] == description]
    assert [row["nativeAmount"] for row in rows] == ["10.0000"]


def test_a_removed_sibling_never_joins_the_remaining_series(live: Owner) -> None:
    """Review OL-002. With one of two services on a descriptor removed, the other is the only
    series left; the removed one's later charges must not join it as a large price change."""
    description = "Synthetic recurring removed sibling"
    base = MONTHLY[0]
    dates = [base + timedelta(days=15 * i) for i in range(8)]
    amounts = ["-10", "-50", "-10", "-50", "-10", "-50", "-10", "-50"]
    for on, amount in zip(dates, amounts, strict=True):
        a_spend(live, live.account_id, amount, on, "expense", description=description)
    refresh()
    both = {
        row["nativeAmount"]: row for row in the_recurring()["series"] if row["label"] == description
    }
    assert remove_series(both["50.0000"]["id"]).status_code == 200

    a_spend(
        live,
        live.account_id,
        "-50",
        dates[-2] + timedelta(days=30),
        "expense",
        description=description,
    )
    refresh()
    remaining = [row for row in the_recurring()["series"] if row["label"] == description]
    assert [row["nativeAmount"] for row in remaining] == ["10.0000"]


def test_a_new_service_beside_a_single_series_becomes_its_own_series(live: Owner) -> None:
    """Review OL-002. A second service starting on a descriptor that has one series is charged days
    after the existing one, not a cycle later, so it is never read as that series' price change —
    including the newest charge of the batch, which no later old-price charge follows."""
    description = "Synthetic recurring second service"
    a_monthly_group(live, description=description, amount="-10")
    refresh()
    single = [row for row in the_recurring()["series"] if row["label"] == description]
    assert len(single) == 1

    for days in (30, 60, 90):
        a_spend(
            live,
            live.account_id,
            "-10",
            MONTHLY[-1] + timedelta(days=days),
            "expense",
            description=description,
        )
        a_spend(
            live,
            live.account_id,
            "-50",
            MONTHLY[-1] + timedelta(days=days + 3),
            "expense",
            description=description,
        )
    refresh()
    rows = [row for row in the_recurring()["series"] if row["label"] == description]
    assert sorted(row["nativeAmount"] for row in rows) == ["10.0000", "50.0000"]


def test_foreign_household_refusals_on_patch_and_remove(live: Owner) -> None:
    description = "Synthetic recurring foreign household"
    a_monthly_group(live, description=description, amount="-10")
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    series_id = row["id"]

    theirs, _account = _another_household(live)
    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(),
        household_id=theirs,
        session_id=uuid4(),
        email="other-recurring-owner@example.invalid",
    )
    try:
        assert the_recurring()["series"] == []
        patched = client().patch(
            f"/recurring/{series_id}", json={"isSubscription": False}, headers=ORIGIN
        )
        assert patched.status_code == 404, patched.text
        removed = remove_series(series_id)
        assert removed.status_code == 404, removed.text
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner


def test_an_ended_series_refuses_patch(live: Owner) -> None:
    description = "Synthetic recurring ended patch"
    a_monthly_group(live, description=description, amount="-10")
    refresh()
    row = _row_for(the_recurring(), description)
    assert row is not None
    series_id = row["id"]
    assert remove_series(series_id).status_code == 200

    refused = client().patch(
        f"/recurring/{series_id}", json={"isSubscription": True}, headers=ORIGIN
    )
    assert refused.status_code == 409, refused.text


def test_routes_require_owner_session() -> None:
    app.dependency_overrides.clear()
    assert client().get("/recurring", headers=ORIGIN).status_code == 401
    assert client().post("/recurring/refresh", headers=ORIGIN).status_code == 401
    assert (
        client()
        .patch(f"/recurring/{uuid4()}", json={"isSubscription": True}, headers=ORIGIN)
        .status_code
        == 401
    )
