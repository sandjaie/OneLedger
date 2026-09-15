"""Live owner-scoped tests for the emergency reserve (Phase 3.3 Task 2). Skipped when no
database is reachable. A reservation is a plan, not a ledger transaction: P07 also checks
`GET /dashboard`'s net worth is unchanged throughout. Every name, label and amount is synthetic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}
TODAY = date.today()


@dataclass(frozen=True)
class EmergencyFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    person_id: UUID
    household_id: UUID
    other_household_id: UUID
    account_id: UUID
    inactive_account_id: UUID
    liability_account_id: UUID
    foreign_currency_account_id: UUID
    other_account_id: UUID
    currency: str
    secondary_currency: str
    foreign_currency: str
    groceries_category_id: UUID
    entertainment_category_id: UUID


def client() -> TestClient:
    return TestClient(app)


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    name: str,
    currency: str,
    *,
    account_type: str = "savings",
    opening: str = "10000.0000",
    active: bool = True,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, is_active,
                                 closed_on)
           VALUES (%s, %s, %s, %s, %s, 'XZ', %s, '2026-01-01', %s, %s) RETURNING id""",
        (
            household_id,
            person_id,
            name,
            account_type,
            currency,
            Decimal(opening),
            active,
            None if active else date(2026, 2, 1),
        ),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


def _txn(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    account_id: UUID,
    amount: str,
    on: date,
    currency: str,
    category_id: UUID | None,
) -> None:
    value = Decimal(amount)
    cur.execute(
        """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                   description_clean, amount, currency, fx_rate_date, fx_rate_to_base,
                   fx_rate_to_secondary, amount_base, amount_secondary, txn_type, category_id,
                   classified_by, source, dedupe_hash)
           VALUES (%s, %s, %s, 'Synthetic spending row', 'Synthetic spending row', %s, %s, %s,
                   1, 1, %s, %s, 'expense', %s, 'user_manual', 'manual', %s)""",
        (
            household_id,
            account_id,
            on,
            value,
            currency,
            on,
            value,
            value,
            category_id,
            uuid4().hex + uuid4().hex,
        ),
    )


@pytest.fixture
def live() -> Iterator[EmergencyFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "emergency-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-emergency-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = str(found[0]).strip(), str(found[1]).strip()
        cur.execute(
            "SELECT code FROM currency WHERE code NOT IN (%s, %s) ORDER BY code LIMIT 1",
            (base, secondary),
        )
        foreign_row = cur.fetchone()
        assert foreign_row is not None
        foreign_currency = str(foreign_row[0]).strip()

        for household, name in (
            (household_id, "emergency household"),
            (other_household_id, "other emergency household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, %s)",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "emergency owner"),
            (other_person_id, other_household_id, "other emergency owner"),
        ):
            cur.execute(
                "INSERT INTO person (id, household_id, display_name, is_self) "
                "VALUES (%s, %s, %s, true)",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, other_owner.email),
        ):
            cur.execute(
                "INSERT INTO app_user (id, household_id, person_id, email, role) "
                "VALUES (%s, %s, %s, %s, 'owner')",
                (user, household, person, email),
            )
        account_id = _account(cur, household_id, person_id, "Synthetic emergency account", base)
        inactive_account_id = _account(
            cur, household_id, person_id, "Synthetic closed account", base, active=False
        )
        liability_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic loan",
            base,
            account_type="personal_loan",
            opening="-1000.0000",
        )
        foreign_currency_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic foreign-currency account",
            foreign_currency,
            opening="1000.0000",
        )
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic emergency account", base
        )
        cur.execute("SELECT id FROM category WHERE slug = 'groceries'")
        groceries = cur.fetchone()
        assert groceries is not None
        cur.execute("SELECT id FROM category WHERE slug = 'entertainment'")
        entertainment = cur.fetchone()
        assert entertainment is not None
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield EmergencyFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            person_id=person_id,
            household_id=household_id,
            other_household_id=other_household_id,
            account_id=account_id,
            inactive_account_id=inactive_account_id,
            liability_account_id=liability_account_id,
            foreign_currency_account_id=foreign_currency_account_id,
            other_account_id=other_account_id,
            currency=base,
            secondary_currency=secondary,
            foreign_currency=foreign_currency,
            groceries_category_id=UUID(str(groceries[0])),
            entertainment_category_id=UUID(str(entertainment[0])),
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute(
                "TRUNCATE resource_reservation, goal, emergency_resource_account, "
                "emergency_reserve, action_mark, transaction CASCADE"
            )
        connection.commit()
        connection.close()


def _mark_eligible(
    account_id: UUID, *, access_days: int = 0, note: str | None = None
) -> dict[str, Any]:
    response = client().put(
        f"/emergency-reserve/accounts/{account_id}",
        json={"accessDays": access_days, "restrictionNote": note},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _put_settings(**overrides: object) -> dict[str, Any]:
    body: dict[str, object] = {
        "targetMonths": "6.0",
        "baselineMethod": "manual",
        "referenceMonths": 6,
        "buckets": ["need", "debt_service"],
        "manualMonthlyBase": "1000.0000",
    }
    body.update(overrides)
    response = client().put("/emergency-reserve", json=body, headers=ORIGIN)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _account_row(response: dict[str, Any], account_id: UUID) -> dict[str, Any]:
    row: dict[str, Any] = next(r for r in response["accounts"] if r["accountId"] == str(account_id))
    return row


def _net_worth(as_of: date | None = None) -> str | None:
    params = {} if as_of is None else {"asOf": str(as_of)}
    response = client().get("/dashboard", params=params, headers=ORIGIN)
    assert response.status_code == 200, response.text
    total: str | None = response.json()["netWorth"]["totalBase"]
    return total


def _reservation_body(
    live: EmergencyFixture, purpose: str, account_id: UUID, **overrides: object
) -> dict[str, object]:
    body: dict[str, object] = {
        "purpose": purpose,
        "goalId": None,
        "accountId": str(account_id),
        "kind": "amount",
        "amount": "1000.0000",
        "idempotencyKey": str(uuid4()),
    }
    body.update(overrides)
    return body


def _reserve_emergency(
    live: EmergencyFixture, account_id: UUID, **overrides: object
) -> httpx.Response:
    response: httpx.Response = client().post(
        "/reservations",
        json=_reservation_body(live, "emergency", account_id, **overrides),
        headers=ORIGIN,
    )
    return response


# ---------------------------------------------------------------------------------------
# P07 in full: goal and emergency reservations share one account's unreserved value.


def test_p07_in_full_goal_and_emergency_share_unreserved_and_release_frees_exactly(
    live: EmergencyFixture,
) -> None:
    before = _net_worth()
    _mark_eligible(live.account_id)

    goal = client().post(
        "/goals",
        json={
            "name": "Synthetic P07 goal",
            "targetAmount": "10000.0000",
            "targetCurrency": live.currency,
            "targetDate": str(TODAY + timedelta(days=365)),
            "priority": 1,
            "plannedMonthly": None,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert goal.status_code == 201, goal.text
    goal_reserved = client().post(
        "/reservations",
        json=_reservation_body(
            live, "goal", live.account_id, goalId=goal.json()["id"], amount="6000.0000"
        ),
        headers=ORIGIN,
    )
    assert goal_reserved.status_code == 201, goal_reserved.text

    emergency_reserved = _reserve_emergency(live, live.account_id, amount="4000.0000")
    assert emergency_reserved.status_code == 201, emergency_reserved.text

    further = _reserve_emergency(live, live.account_id, amount="1.0000")
    assert further.status_code == 422
    assert "unreserved" in further.json()["detail"]

    accounts = client().get("/goals", headers=ORIGIN).json()["accounts"]
    row = next(r for r in accounts if r["accountId"] == str(live.account_id))
    assert row["reserved"] == "10000.0000"
    assert row["unreserved"] == "0.0000"

    released = client().post(
        f"/reservations/{emergency_reserved.json()['id']}/release", json={}, headers=ORIGIN
    )
    assert released.status_code == 200, released.text

    reserve_view = client().get("/emergency-reserve", headers=ORIGIN).json()
    reserve_row = _account_row(reserve_view, live.account_id)
    assert reserve_row["emergencyReserved"] == "0.0000"

    partial_release = client().post(
        f"/reservations/{goal_reserved.json()['id']}/release",
        json={},
        headers=ORIGIN,
    )
    assert partial_release.status_code == 200, partial_release.text

    accounts_after = client().get("/goals", headers=ORIGIN).json()["accounts"]
    row_after = next(r for r in accounts_after if r["accountId"] == str(live.account_id))
    assert row_after["reserved"] == "0.0000"
    assert row_after["unreserved"] == "10000.0000"

    assert _net_worth() == before


def test_emergency_reservation_refused_without_eligibility(live: EmergencyFixture) -> None:
    refused = _reserve_emergency(live, live.account_id, amount="100.0000")
    assert refused.status_code == 422
    assert refused.json()["detail"] == "mark this account as an emergency resource first"


# ---------------------------------------------------------------------------------------
# P08: eligible less a goal reservation, over a manual baseline, is exact months; an emergency
# reservation on the same account does not reduce "eligible" a second time.


def test_p08_six_thousand_eligible_over_two_thousand_baseline_and_emergency_not_double_counted(
    live: EmergencyFixture,
) -> None:
    _mark_eligible(live.account_id)
    goal = client().post(
        "/goals",
        json={
            "name": "Synthetic P08 goal",
            "targetAmount": "10000.0000",
            "targetCurrency": live.currency,
            "targetDate": str(TODAY + timedelta(days=365)),
            "priority": 1,
            "plannedMonthly": None,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert goal.status_code == 201, goal.text
    goal_reserved = client().post(
        "/reservations",
        json=_reservation_body(
            live, "goal", live.account_id, goalId=goal.json()["id"], amount="4000.0000"
        ),
        headers=ORIGIN,
    )
    assert goal_reserved.status_code == 201, goal_reserved.text
    emergency_reserved = _reserve_emergency(live, live.account_id, amount="1000.0000")
    assert emergency_reserved.status_code == 201, emergency_reserved.text

    _put_settings(baselineMethod="manual", manualMonthlyBase="2000.0000", targetMonths="6.0")
    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    row = _account_row(view, live.account_id)
    # eligible = value(10000) - goalReserved(4000) = 6000, unaffected by the emergency reservation.
    assert row["eligible"] == "6000.0000"
    assert row["emergencyReserved"] == "1000.0000"
    assert view["coverageStatus"] == "available"
    assert view["eligibleBase"] == "6000.0000"
    assert view["coverageMonths"] == "3.00"


# ---------------------------------------------------------------------------------------
# Baseline: reference-period by bucket, an uncategorised row excluded, manual, and
# not-enough-information.


def test_reference_period_baseline_counts_only_configured_buckets_and_reports_uncategorised(
    live: EmergencyFixture,
) -> None:
    as_of = date(2026, 10, 1)
    with live.connection.cursor() as cur:
        # Groceries (need) is counted; entertainment (want) is not, since buckets=['need'];
        # the uncategorised row is reported but not counted in either.
        _txn(
            cur,
            live.household_id,
            live.account_id,
            "-300.0000",
            date(2026, 9, 15),
            live.currency,
            live.groceries_category_id,
        )
        _txn(
            cur,
            live.household_id,
            live.account_id,
            "-200.0000",
            date(2026, 9, 16),
            live.currency,
            live.entertainment_category_id,
        )
        _txn(
            cur,
            live.household_id,
            live.account_id,
            "-50.0000",
            date(2026, 9, 17),
            live.currency,
            None,
        )
    live.connection.commit()

    _put_settings(
        baselineMethod="reference_period",
        referenceMonths=1,
        buckets=["need"],
        manualMonthlyBase=None,
    )
    view = client().get("/emergency-reserve", params={"asOf": str(as_of)}, headers=ORIGIN).json()
    assert view["baseline"]["method"] == "reference_period"
    assert view["baseline"]["monthlyBase"] == "300.0000"
    assert view["baseline"]["uncategorisedBase"] == "50.0000"
    assert view["baseline"]["referenceStart"] == "2026-09-01"
    assert view["baseline"]["referenceEnd"] == "2026-09-30"
    assert view["baseline"]["status"] == "available"


def test_manual_baseline_uses_the_stated_amount(live: EmergencyFixture) -> None:
    _put_settings(baselineMethod="manual", manualMonthlyBase="2500.0000")
    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert view["baseline"]["method"] == "manual"
    assert view["baseline"]["monthlyBase"] == "2500.0000"


def test_zero_spending_reference_period_is_not_enough_information(live: EmergencyFixture) -> None:
    _put_settings(
        baselineMethod="reference_period",
        referenceMonths=6,
        buckets=["need"],
        manualMonthlyBase=None,
    )
    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert view["baseline"]["status"] == "not_enough_information"
    assert view["coverageStatus"] == "not_enough_information"


def test_unconfigured_reserve_reports_not_configured_with_accounts_listed(
    live: EmergencyFixture,
) -> None:
    _mark_eligible(live.account_id)
    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert view["configured"] is False
    assert view["baseline"] is None
    assert view["coverageStatus"] == "not_enough_information"
    assert len(view["accounts"]) == 1


# ---------------------------------------------------------------------------------------
# A missing rate makes coverage unavailable, never a silently dropped account.


def test_missing_rate_makes_coverage_unavailable(live: EmergencyFixture) -> None:
    _mark_eligible(live.foreign_currency_account_id)
    _put_settings(baselineMethod="manual", manualMonthlyBase="1000.0000")
    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert view["coverageStatus"] == "unavailable"
    assert "missing_rate" in view["reasons"]
    assert view["eligibleBase"] is None


# ---------------------------------------------------------------------------------------
# Eligibility: add, refuse an inactive or liability account, 409 while an emergency
# reservation is active, then release and remove.


def test_eligibility_add_refuse_ineligible_and_409_then_remove(live: EmergencyFixture) -> None:
    inactive = client().put(
        f"/emergency-reserve/accounts/{live.inactive_account_id}",
        json={"accessDays": 0, "restrictionNote": None},
        headers=ORIGIN,
    )
    assert inactive.status_code == 422
    assert inactive.json()["detail"] == "only an active asset account can be an emergency resource"

    liability = client().put(
        f"/emergency-reserve/accounts/{live.liability_account_id}",
        json={"accessDays": 0, "restrictionNote": None},
        headers=ORIGIN,
    )
    assert liability.status_code == 422

    added = _mark_eligible(live.account_id, access_days=3, note="Synthetic notice period")
    row = _account_row(added, live.account_id)
    assert row["accessDays"] == 3
    assert row["restrictionNote"] == "Synthetic notice period"

    reserved = _reserve_emergency(live, live.account_id, amount="500.0000")
    assert reserved.status_code == 201, reserved.text

    blocked = client().delete(f"/emergency-reserve/accounts/{live.account_id}", headers=ORIGIN)
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "release this account's emergency reservations first"

    released = client().post(
        f"/reservations/{reserved.json()['id']}/release", json={}, headers=ORIGIN
    )
    assert released.status_code == 200, released.text

    removed = client().delete(f"/emergency-reserve/accounts/{live.account_id}", headers=ORIGIN)
    assert removed.status_code == 204

    missing = client().delete(f"/emergency-reserve/accounts/{live.account_id}", headers=ORIGIN)
    assert missing.status_code == 404


# ---------------------------------------------------------------------------------------
# Upcoming commitments on an eligible account are shown, not subtracted.


def test_upcoming_commitments_are_listed_on_an_eligible_account(live: EmergencyFixture) -> None:
    _mark_eligible(live.account_id)
    due = (TODAY + timedelta(days=10)).isoformat()
    commitment = client().post(
        "/commitments",
        json={
            "accountId": str(live.account_id),
            "label": "Synthetic upcoming bill",
            "amount": "-200.0000",
            "currency": live.currency,
            "cadence": "once",
            "firstDate": due,
            "endDate": None,
            "nextDueDate": due,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert commitment.status_code == 201, commitment.text

    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    row = _account_row(view, live.account_id)
    upcoming = row["upcomingCommitments"]
    assert len(upcoming) == 1
    assert upcoming[0]["label"] == "Synthetic upcoming bill"
    assert upcoming[0]["dueDate"] == due
    assert upcoming[0]["remainingNext"] == "-200.0000"


# ---------------------------------------------------------------------------------------
# Household isolation with real foreign rows, and GET changes no row.


def test_household_isolation_with_real_foreign_rows(live: EmergencyFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    _mark_eligible(live.other_account_id)
    _put_settings(baselineMethod="manual", manualMonthlyBase="500.0000")
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    _mark_eligible(live.account_id)
    view = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert len(view["accounts"]) == 1
    assert view["accounts"][0]["accountId"] == str(live.account_id)
    assert view["configured"] is False

    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign_view = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert len(foreign_view["accounts"]) == 1
    assert foreign_view["accounts"][0]["accountId"] == str(live.other_account_id)
    app.dependency_overrides[auth.require_owner] = lambda: live.owner


def test_get_emergency_reserve_changes_no_row(live: EmergencyFixture) -> None:
    _mark_eligible(live.account_id)
    _put_settings(baselineMethod="manual", manualMonthlyBase="1000.0000")
    before = client().get("/emergency-reserve", headers=ORIGIN).json()

    client().get("/emergency-reserve", headers=ORIGIN)

    after = client().get("/emergency-reserve", headers=ORIGIN).json()
    assert after == before
    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM emergency_reserve WHERE household_id = %s", (live.household_id,)
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 1
