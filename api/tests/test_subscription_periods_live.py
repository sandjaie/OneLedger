"""Phase 4.1 Task 5: billing periods, the owner's lifecycle and custom cadence, cancel, resume,
automatic lapsing, and a subscription added from one charge (spec "A subscription has billing
periods"). Reuses `test_recurring_live.py`'s `live` fixture and helpers — the same database, the
same truncation, the same `TODAY`-relative dating so a fixed date never drifts into a different
status as the calendar moves. Every name, label and amount is invented.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, cast
from uuid import uuid4

from oneledger import auth
from oneledger.main import app

from .test_ingest_route_live import ORIGIN
from .test_recurring_live import (
    TODAY,
    a_monthly_group,
    live,  # noqa: F401 - fixture import
    patch_series,
    refresh,
    the_recurring,
)
from .test_spending_live import _another_household, a_spend
from .test_transfer_writes_live import Owner
from .test_transfers_live import client


def _ago(days: int) -> date:
    return TODAY - timedelta(days=days)


def cancel_series(series_id: str, **fields: str) -> dict[str, Any]:
    response = client().post(f"/recurring/{series_id}/cancel", json=fields, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def resume_series(series_id: str, **fields: str) -> dict[str, Any]:
    response = client().post(f"/recurring/{series_id}/resume", json=fields, headers=ORIGIN)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def from_transaction(**fields: Any) -> dict[str, Any]:  # noqa: ANN401 - a JSON body, any shape
    response = client().post("/recurring/from-transaction", json=fields, headers=ORIGIN)
    assert response.status_code in (200, 201), response.text
    body: dict[str, Any] = response.json()
    return body


# ---------------------------------------------------------------------------------------


def test_cancel_closes_the_period_and_a_later_charge_reopens_the_same_series(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription cancel and resume"
    a_monthly_group(
        live, description=description, amount="-12", dates=[_ago(120), _ago(90), _ago(60)]
    )
    refresh()
    row = _first(the_recurring(), description)
    series_id = row["id"]
    assert row["periodCount"] == 1
    assert row["isCurrent"] is True

    patched = patch_series(series_id, isSubscription=False, label="Owner kept this name")
    assert patched["isSubscription"] is False

    cancelled = cancel_series(series_id, cancelledOn=str(_ago(55)))
    assert cancelled["isCurrent"] is False
    assert cancelled["status"] == "ended"
    assert cancelled["lastEndReason"] == "cancelled"
    assert cancelled["lastPeriodEndedOn"] == str(_ago(55))
    assert cancelled["periodCount"] == 1

    a_spend(live, live.account_id, "-12", TODAY, "expense", description=description)
    refresh()
    # The label was just renamed by the owner, so look the row up by id, not by the original
    # description.
    after = _by_id(the_recurring(), series_id)
    assert after["id"] == series_id
    assert after["isCurrent"] is True
    assert after["periodCount"] == 2
    assert after["resumed"] is True
    assert after["isSubscription"] is False
    assert after["label"] == "Owner kept this name"


def test_a_series_that_stops_charging_lapses_and_a_later_charge_opens_a_new_period(
    live: Owner,  # noqa: F811
) -> None:  # noqa: F811
    description = "Synthetic subscription lapses"
    a_monthly_group(
        live, description=description, amount="-9", dates=[_ago(210), _ago(180), _ago(150)]
    )
    refresh()
    row = _first(the_recurring(), description)
    assert row["isCurrent"] is False
    assert row["status"] == "ended"
    assert row["lastEndReason"] == "lapsed"
    # Paid through last_seen_on (offset 150) plus one interval (30): offset 120.
    assert row["lastPeriodEndedOn"] == str(_ago(120))
    assert row["periodCount"] == 1

    a_spend(live, live.account_id, "-9", _ago(50), "expense", description=description)
    refresh()
    after = _first(the_recurring(), description, series_id=row["id"])
    assert after["isCurrent"] is True
    assert after["periodCount"] == 2
    assert after["currentPeriodStartedOn"] == str(_ago(50))


def test_a_late_import_within_two_cadences_restores_the_same_period_and_a_second_refresh_is_a_noop(
    live: Owner,  # noqa: F811
) -> None:  # noqa: F811
    description = "Synthetic subscription late restore"
    # last_seen_on (offset 75) is stale enough to lapse on its own first refresh (paid-through
    # offset 45); a charge dated offset 40 arrives within that old last_seen_on's two-cadence
    # window (threshold offset 15) AND is itself recent enough (<= 60 days) to stay current once
    # restored — proving a restore, not merely a lapse followed by an unrelated new period.
    a_monthly_group(
        live, description=description, amount="-14", dates=[_ago(135), _ago(105), _ago(75)]
    )
    refresh()
    row = _first(the_recurring(), description)
    assert row["status"] == "ended"
    assert row["lastEndReason"] == "lapsed"

    a_spend(live, live.account_id, "-14", _ago(40), "expense", description=description)
    refresh()
    restored = _first(the_recurring(), description, series_id=row["id"])
    assert restored["periodCount"] == 1, "a late-but-in-time charge must restore, not add, a period"
    assert restored["resumed"] is False, "never shown as resumed for a merely late statement"
    assert restored["isCurrent"] is True

    assert refresh() == {"seriesCreated": 0, "seriesTouched": 0, "membersAdded": 0}
    again = _first(the_recurring(), description, series_id=row["id"])
    assert again == restored


def test_one_batch_holding_a_gap_over_two_cadences_produces_two_periods(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription batch gap"
    # last_seen_on (offset 100) lapses on its own first refresh. A charge at offset 90 arrives
    # within the old two-cadence window (threshold offset 40) and restores the period; a second
    # charge at offset 20, more than two cadences (60 days) after that restored last_seen_on
    # (offset 90), lapses it again and opens a fresh one — both in the SAME refresh call.
    a_monthly_group(
        live, description=description, amount="-22", dates=[_ago(160), _ago(130), _ago(100)]
    )
    refresh()
    row = _first(the_recurring(), description)
    assert row["status"] == "ended"

    a_spend(live, live.account_id, "-22", _ago(90), "expense", description=description)
    a_spend(live, live.account_id, "-22", _ago(20), "expense", description=description)
    refresh()
    after = _first(the_recurring(), description, series_id=row["id"])
    assert after["periodCount"] == 2
    assert after["isCurrent"] is True
    assert after["currentPeriodStartedOn"] == str(_ago(20))


def test_a_charge_before_the_open_periods_start_moves_the_start_back(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription start moves back"
    a_monthly_group(
        live, description=description, amount="-8", dates=[_ago(90), _ago(60), _ago(30)]
    )
    refresh()
    row = _first(the_recurring(), description)
    assert row["currentPeriodStartedOn"] == str(_ago(90))

    a_spend(live, live.account_id, "-8", _ago(120), "expense", description=description)
    refresh()
    after = _first(the_recurring(), description, series_id=row["id"])
    assert after["currentPeriodStartedOn"] == str(_ago(120))
    assert after["periodCount"] == 1


def test_a_paused_series_never_lapses(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription paused"
    transaction_id = a_spend(
        live, live.account_id, "-30", _ago(150), "expense", description=description
    )
    created = from_transaction(
        transactionId=str(transaction_id),
        cadence="monthly",
        label=description,
        idempotencyKey=str(uuid4()),
    )
    patched = patch_series(created["id"], lifecycle="paused")
    assert patched["lifecycle"] == "paused"

    refreshed = refresh()
    assert refreshed == {"seriesCreated": 0, "seriesTouched": 0, "membersAdded": 0}
    row = _first(the_recurring(), description, series_id=created["id"])
    assert row["isCurrent"] is True, "a paused series must never lapse, however stale"
    assert row["periodCount"] == 1
    assert row["lifecycle"] == "paused"


def test_resume_then_refresh_keeps_the_period_open_and_measures_from_the_resume(
    live: Owner,  # noqa: F811
) -> None:  # noqa: F811
    description = "Synthetic subscription resume measured"
    a_monthly_group(
        live, description=description, amount="-16", dates=[_ago(120), _ago(90), _ago(60)]
    )
    refresh()
    row = _first(the_recurring(), description)
    cancel_series(row["id"], cancelledOn=str(_ago(55)))

    resumed = resume_series(row["id"], resumedOn=str(_ago(10)))
    assert resumed["currentPeriodStartedOn"] == str(_ago(10))

    refreshed = refresh()
    assert refreshed == {"seriesCreated": 0, "seriesTouched": 0, "membersAdded": 0}
    after = _first(the_recurring(), description, series_id=row["id"])
    assert after["isCurrent"] is True
    assert after["periodCount"] == 2
    # Measured from the resume (offset 10 + 30 days), not from the last charge (offset 60 + 30).
    assert after["nextExpectedOn"] == str(TODAY + timedelta(days=20))
    assert after["status"] == "active"


def test_resume_refuses_an_open_period_and_cancel_refuses_a_future_or_too_early_date(
    live: Owner,  # noqa: F811
) -> None:
    description = "Synthetic subscription cancel refusals"
    a_monthly_group(
        live, description=description, amount="-19", dates=[_ago(90), _ago(60), _ago(30)]
    )
    refresh()
    row = _first(the_recurring(), description)
    series_id = row["id"]

    resume_open = client().post(f"/recurring/{series_id}/resume", json={}, headers=ORIGIN)
    assert resume_open.status_code == 409, resume_open.text

    future = client().post(
        f"/recurring/{series_id}/cancel",
        json={"cancelledOn": str(TODAY + timedelta(days=5))},
        headers=ORIGIN,
    )
    assert future.status_code == 422, future.text

    # cancelledOn far before the last charge must not move ended_on earlier than that charge.
    cancelled = cancel_series(series_id, cancelledOn=str(_ago(200)))
    assert cancelled["lastPeriodEndedOn"] == str(_ago(30))

    already_cancelled = client().post(f"/recurring/{series_id}/cancel", json={}, headers=ORIGIN)
    assert already_cancelled.status_code == 409, already_cancelled.text


def test_cancel_relabels_an_already_lapsed_period(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription relabel lapsed"
    a_monthly_group(
        live, description=description, amount="-11", dates=[_ago(210), _ago(180), _ago(150)]
    )
    refresh()
    row = _first(the_recurring(), description)
    assert row["lastEndReason"] == "lapsed"
    lapsed_ended_on = row["lastPeriodEndedOn"]

    cancelled = cancel_series(row["id"])
    assert cancelled["lastEndReason"] == "cancelled"
    assert cancelled["lastPeriodEndedOn"] == lapsed_ended_on, "relabelling keeps the inferred date"
    assert cancelled["periodCount"] == 1


def test_a_backfilled_cancelled_series_and_a_current_one_extend_only_the_current(
    live: Owner,  # noqa: F811
) -> None:  # noqa: F811
    description = "Synthetic subscription coexisting periods"
    match_key = description.lower()
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO recurring_series (household_id, idempotency_key, label, match_key,
                   cadence, expected_amount_base, currency, first_seen_on, last_seen_on,
                   native_amount, native_currency)
               VALUES (%s, gen_random_uuid(), %s, %s, 'monthly', 10, %s, %s, %s, 10, %s)
               RETURNING id""",
            (live.household_id, description, match_key, live.base, _ago(200), _ago(140), live.base),
        )
        old_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO recurring_series_period (household_id, series_id, started_on, ended_on,
                   end_reason)
               VALUES (%s, %s, %s, %s, 'cancelled')""",
            (live.household_id, old_id, _ago(200), _ago(140)),
        )
        cur.execute(
            """INSERT INTO recurring_series (household_id, idempotency_key, label, match_key,
                   cadence, expected_amount_base, currency, first_seen_on, last_seen_on,
                   native_amount, native_currency)
               VALUES (%s, gen_random_uuid(), %s, %s, 'monthly', 10, %s, %s, %s, 10, %s)
               RETURNING id""",
            (live.household_id, description, match_key, live.base, _ago(90), _ago(30), live.base),
        )
        current_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO recurring_series_period (household_id, series_id, started_on)
               VALUES (%s, %s, %s)""",
            (live.household_id, current_id, _ago(90)),
        )
    live.conn.commit()

    a_spend(live, live.account_id, "-10", TODAY, "expense", description=description)
    refresh()

    rows = [row for row in the_recurring()["series"] if row["label"] == description]
    current_row = next(row for row in rows if row["id"] == str(current_id))
    old_row = next(row for row in rows if row["id"] == str(old_id))
    assert current_row["lastSeenOn"] == str(TODAY)
    assert current_row["periodCount"] == 1
    assert old_row["isCurrent"] is False
    assert old_row["periodCount"] == 1
    assert old_row["lastSeenOn"] == str(_ago(140)), "the cancelled series must be untouched"


def test_a_resume_at_a_different_price_after_a_long_gap_does_not_rejoin(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription re-priced after a gap"
    a_monthly_group(
        live, description=description, amount="-18", dates=[_ago(200), _ago(170), _ago(140)]
    )
    refresh()
    old_row = _first(the_recurring(), description)
    assert old_row["isCurrent"] is False, "the old series must have lapsed on its own refresh"

    for on in (_ago(90), _ago(60), _ago(30)):
        a_spend(live, live.account_id, "-40", on, "expense", description=description)
    refresh()

    rows = [row for row in the_recurring()["series"] if row["label"] == description]
    assert len(rows) == 2
    new_row = next(row for row in rows if row["id"] != old_row["id"])
    assert new_row["nativeAmount"] == "40.0000"
    assert new_row["isCurrent"] is True
    assert new_row["periodCount"] == 1
    old_after = next(row for row in rows if row["id"] == old_row["id"])
    assert old_after["nativeAmount"] == "18.0000", (
        "the old series must not have absorbed the new price"
    )


def test_a_single_charge_added_as_a_yearly_subscription(live: Owner) -> None:  # noqa: F811
    description = "Synthetic yearly plan seen once"
    charge_date = _ago(30)
    transaction_id = a_spend(
        live, live.account_id, "-120", charge_date, "expense", description=description
    )
    key = str(uuid4())
    created = from_transaction(
        transactionId=str(transaction_id),
        cadence="annual",
        label=description,
        idempotencyKey=key,
    )
    assert created["cadence"] == "annual"
    assert created["nativeAmount"] == "120.0000"
    assert created["isCurrent"] is True
    assert created["periodCount"] == 1
    assert created["currentPeriodStartedOn"] == str(charge_date)
    assert created["nextExpectedOn"] == str(charge_date + timedelta(days=365))

    replay = from_transaction(
        transactionId=str(transaction_id),
        cadence="annual",
        label=description,
        idempotencyKey=key,
    )
    assert replay["id"] == created["id"]

    conflict = client().post(
        "/recurring/from-transaction",
        json={
            "transactionId": str(transaction_id),
            "cadence": "annual",
            "label": "a different label",
            "idempotencyKey": key,
        },
        headers=ORIGIN,
    )
    assert conflict.status_code == 409, conflict.text

    # Extended by next year's charge, through the ordinary within-band join.
    a_spend(
        live,
        live.account_id,
        "-120",
        charge_date + timedelta(days=365),
        "expense",
        description=description,
    )
    refresh()
    after = _first(the_recurring(), description, series_id=created["id"])
    assert after["lastSeenOn"] == str(charge_date + timedelta(days=365))
    assert after["periodCount"] == 1


def test_from_transaction_refuses_another_household_income_and_an_already_member_transaction(
    live: Owner,  # noqa: F811
) -> None:  # noqa: F811
    theirs, their_account = _another_household(live)
    their_transaction = a_spend(
        live, their_account, "-50", _ago(10), "expense", household=theirs, description="foreign"
    )
    foreign = client().post(
        "/recurring/from-transaction",
        json={
            "transactionId": str(their_transaction),
            "cadence": "monthly",
            "label": "foreign",
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert foreign.status_code == 404, foreign.text

    income_transaction = a_spend(
        live, live.account_id, "50", _ago(10), "income", description="synthetic income"
    )
    income = client().post(
        "/recurring/from-transaction",
        json={
            "transactionId": str(income_transaction),
            "cadence": "monthly",
            "label": "income",
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert income.status_code == 422, income.text

    description = "Synthetic subscription already a member"
    a_monthly_group(
        live, description=description, amount="-13", dates=[_ago(90), _ago(60), _ago(30)]
    )
    refresh()
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT m.transaction_id FROM recurring_series_member m
                 JOIN recurring_series s ON s.id = m.series_id
                WHERE s.household_id = %s AND s.label = %s LIMIT 1""",
            (live.household_id, description),
        )
        member_transaction = cur.fetchone()[0]  # type: ignore[index]
    already_member = client().post(
        "/recurring/from-transaction",
        json={
            "transactionId": str(member_transaction),
            "cadence": "monthly",
            "label": "already a member",
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert already_member.status_code == 409, already_member.text


def test_patch_sets_lifecycle_custom_cadence_and_category_another_households_category_is_422(
    live: Owner,  # noqa: F811
) -> None:  # noqa: F811
    description = "Synthetic subscription patch fields"
    a_monthly_group(
        live, description=description, amount="-17", dates=[_ago(90), _ago(60), _ago(30)]
    )
    refresh()
    row = _first(the_recurring(), description)

    patched = patch_series(row["id"], lifecycle="trial")
    assert patched["lifecycle"] == "trial"

    custom = client().patch(
        f"/recurring/{row['id']}",
        json={"cadence": "custom", "customIntervalDays": 45},
        headers=ORIGIN,
    )
    assert custom.status_code == 200, custom.text
    assert custom.json()["cadence"] == "custom"
    assert custom.json()["customIntervalDays"] == 45

    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM category WHERE slug = 'entertainment-streaming' "
            "AND household_id IS NULL"
        )
        streaming = cur.fetchone()[0]  # type: ignore[index]
    categorised = patch_series(row["id"], categoryId=str(streaming))
    assert categorised["categoryId"] == str(streaming)

    theirs, _their_account = _another_household(live)
    with live.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO category (household_id, name, slug, kind, bucket) VALUES (%s, %s, %s, "
            "'expense', 'want') RETURNING id",
            (theirs, "Synthetic recurring foreign category", "synthetic-recurring-foreign-t5"),
        )
        foreign_category = cur.fetchone()[0]  # type: ignore[index]
    live.conn.commit()
    foreign_patch = client().patch(
        f"/recurring/{row['id']}",
        json={"categoryId": str(foreign_category)},
        headers=ORIGIN,
    )
    assert foreign_patch.status_code == 422, foreign_patch.text


def test_every_new_route_refuses_another_household(live: Owner) -> None:  # noqa: F811
    description = "Synthetic subscription foreign household routes"
    a_monthly_group(
        live, description=description, amount="-21", dates=[_ago(90), _ago(60), _ago(30)]
    )
    refresh()
    row = _first(the_recurring(), description)
    series_id = row["id"]
    theirs, _their_account = _another_household(live)

    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        user_id=uuid4(),
        household_id=theirs,
        session_id=uuid4(),
        email="foreign-subscriptions-owner@example.invalid",
    )
    try:
        cancelled = client().post(f"/recurring/{series_id}/cancel", json={}, headers=ORIGIN)
        assert cancelled.status_code == 404, cancelled.text
        resumed = client().post(f"/recurring/{series_id}/resume", json={}, headers=ORIGIN)
        assert resumed.status_code == 404, resumed.text
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner


def _first(body: dict[str, Any], label: str, *, series_id: str | None = None) -> dict[str, Any]:
    matches = [row for row in body["series"] if row["label"] == label]
    if series_id is not None:
        matches = [row for row in matches if row["id"] == series_id]
    assert len(matches) == 1, f"expected exactly one row for {label!r}, found {len(matches)}"
    return cast(dict[str, Any], matches[0])


def _by_id(body: dict[str, Any], series_id: str) -> dict[str, Any]:
    return cast(dict[str, Any], next(row for row in body["series"] if row["id"] == series_id))
