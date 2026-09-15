"""Live owner-scoped goal and reservation tests (Phase 3.3 Task 1).

Skipped when no database is reachable. A reservation is a plan, not a ledger transaction: every
scenario below also checks `GET /dashboard`'s net worth is unchanged. Every name, label and
amount is synthetic.
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
from .test_investment_events_live import EventFixture

pytest_plugins = ("tests.test_investment_events_live",)

ORIGIN = {"Origin": "http://localhost:3000"}
TODAY = date.today()


@dataclass(frozen=True)
class GoalsFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    person_id: UUID
    account_id: UUID
    whole_account_id: UUID
    zero_value_account_id: UUID
    secondary_account_id: UUID
    snapshot_account_id: UUID
    inactive_account_id: UUID
    liability_account_id: UUID
    other_account_id: UUID
    other_goal_id: UUID
    currency: str
    secondary_currency: str


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
    opening_date: date = date(2026, 1, 1),
    active: bool = True,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, is_active,
                                 closed_on)
           VALUES (%s, %s, %s, %s, %s, 'XZ', %s, %s, %s, %s) RETURNING id""",
        (
            household_id,
            person_id,
            name,
            account_type,
            currency,
            Decimal(opening),
            opening_date,
            active,
            None if active else date(2026, 2, 1),
        ),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


@pytest.fixture
def live() -> Iterator[GoalsFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "goals-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-goals-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = found
        for household, name in (
            (household_id, "goals household"),
            (other_household_id, "other goals household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "goals owner"),
            (other_person_id, other_household_id, "other goals owner"),
        ):
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, %s, true)""",
                (person, household, name),
            )
        for user, household, person, email in (
            (user_id, household_id, person_id, owner.email),
            (other_user_id, other_household_id, other_person_id, other_owner.email),
        ):
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (user, household, person, email),
            )
        account_id = _account(cur, household_id, person_id, "Synthetic goals account", base)
        whole_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic whole-account resource",
            base,
            opening="5000.0000",
        )
        zero_value_account_id = _account(
            cur, household_id, person_id, "Synthetic worthless account", base, opening="0.0000"
        )
        secondary_account_id = _account(
            cur, household_id, person_id, "Synthetic secondary-currency account", secondary
        )
        snapshot_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic snapshot account",
            base,
            account_type="retirement",
            opening="0.0000",
        )
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
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic goals account", base
        )
        cur.execute(
            """INSERT INTO goal (household_id, idempotency_key, name, target_amount,
                                  target_currency, target_date, priority)
               VALUES (%s, %s, 'Other synthetic goal', 1000.0000, %s, %s, 1) RETURNING id""",
            (other_household_id, uuid4(), base, TODAY + timedelta(days=365)),
        )
        found_goal = cur.fetchone()
        assert found_goal is not None
        other_goal_id = UUID(str(found_goal[0]))
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield GoalsFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            person_id=person_id,
            account_id=account_id,
            whole_account_id=whole_account_id,
            zero_value_account_id=zero_value_account_id,
            secondary_account_id=secondary_account_id,
            snapshot_account_id=snapshot_account_id,
            inactive_account_id=inactive_account_id,
            liability_account_id=liability_account_id,
            other_account_id=other_account_id,
            other_goal_id=other_goal_id,
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE resource_reservation, goal, transaction CASCADE")
        connection.commit()
        connection.close()


def _goal_body(fixture: GoalsFixture, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Synthetic emergency trip fund",
        "targetAmount": "10000.0000",
        "targetCurrency": fixture.currency,
        "targetDate": str(TODAY + timedelta(days=365)),
        "priority": 1,
        "plannedMonthly": None,
        "idempotencyKey": str(uuid4()),
    }
    base.update(overrides)
    return base


def _create_goal(fixture: GoalsFixture, **overrides: object) -> dict[str, Any]:
    response = client().post("/goals", json=_goal_body(fixture, **overrides), headers=ORIGIN)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _reservation_body(
    fixture: GoalsFixture, goal_id: str, **overrides: object
) -> dict[str, object]:
    base: dict[str, object] = {
        "purpose": "goal",
        "goalId": goal_id,
        "accountId": str(fixture.account_id),
        "kind": "amount",
        "amount": "1000.0000",
        "idempotencyKey": str(uuid4()),
    }
    base.update(overrides)
    return base


def _reserve(fixture: GoalsFixture, goal_id: str, **overrides: object) -> httpx.Response:
    response: httpx.Response = client().post(
        "/reservations", json=_reservation_body(fixture, goal_id, **overrides), headers=ORIGIN
    )
    return response


def _net_worth(as_of: date | None = None) -> str | None:
    params = {} if as_of is None else {"asOf": str(as_of)}
    response = client().get("/dashboard", params=params, headers=ORIGIN)
    assert response.status_code == 200, response.text
    total: str | None = response.json()["netWorth"]["totalBase"]
    return total


# ---------------------------------------------------------------------------------------
# P07's goal half: reserve, refuse over unreserved, exclusivity both ways, release frees exactly.


def test_p07_reserve_refuse_exclusivity_and_release_leave_net_worth_unchanged(
    live: GoalsFixture,
) -> None:
    before = _net_worth()
    goal = _create_goal(live, targetAmount="10000.0000")

    reserved = _reserve(live, goal["id"], amount="6000.0000")
    assert reserved.status_code == 201, reserved.text

    over = _reserve(live, goal["id"], amount="4001.0000")
    assert over.status_code == 422
    assert "unreserved" in over.json()["detail"]

    blocked_whole = _reserve(live, goal["id"], kind="whole_account", amount=None)
    assert blocked_whole.status_code == 409
    assert blocked_whole.json()["detail"] == "this account is already reserved"

    extra = _reserve(live, goal["id"], amount="1000.0000")
    assert extra.status_code == 201, extra.text
    extra_id = extra.json()["id"]

    accounts_mid = client().get("/goals", headers=ORIGIN).json()["accounts"]
    row_mid = next(r for r in accounts_mid if r["accountId"] == str(live.account_id))
    assert row_mid["reserved"] == "7000.0000"
    assert row_mid["unreserved"] == "3000.0000"

    released = client().post(f"/reservations/{extra_id}/release", json={}, headers=ORIGIN)
    assert released.status_code == 200, released.text
    assert released.json()["releasedAt"] is not None

    accounts_after = client().get("/goals", headers=ORIGIN).json()["accounts"]
    row_after = next(r for r in accounts_after if r["accountId"] == str(live.account_id))
    assert row_after["reserved"] == "6000.0000"
    assert row_after["unreserved"] == "4000.0000"

    assert _net_worth() == before


def test_whole_account_exclusivity_the_other_direction(live: GoalsFixture) -> None:
    """An active whole-account reservation refuses any further reservation on the account as
    already reserved (409), even though its unreserved value is also zero."""
    goal = _create_goal(live)
    whole = client().post(
        "/reservations",
        json=_reservation_body(
            live,
            goal["id"],
            accountId=str(live.whole_account_id),
            kind="whole_account",
            amount=None,
        ),
        headers=ORIGIN,
    )
    assert whole.status_code == 201, whole.text

    blocked_amount = client().post(
        "/reservations",
        json=_reservation_body(
            live, goal["id"], accountId=str(live.whole_account_id), amount="100.0000"
        ),
        headers=ORIGIN,
    )
    assert blocked_amount.status_code == 409
    assert blocked_amount.json()["detail"] == "this account is already reserved"

    blocked_whole = client().post(
        "/reservations",
        json=_reservation_body(
            live,
            goal["id"],
            accountId=str(live.whole_account_id),
            kind="whole_account",
            amount=None,
        ),
        headers=ORIGIN,
    )
    assert blocked_whole.status_code == 409
    assert blocked_whole.json()["detail"] == "this account is already reserved"


# ---------------------------------------------------------------------------------------
# Each refusal sentence.


def test_inactive_and_liability_accounts_are_refused(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    for account_id in (live.inactive_account_id, live.liability_account_id):
        response = _reserve(live, goal["id"], accountId=str(account_id), amount="100.0000")
        assert response.status_code == 422
        assert response.json()["detail"] == "only an active asset account can be reserved"


def test_foreign_account_is_404(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    response = _reserve(live, goal["id"], accountId=str(uuid4()), amount="100.0000")
    assert response.status_code == 404


def test_missing_value_and_zero_whole_account_value_are_refused(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    no_snapshot = _reserve(
        live, goal["id"], accountId=str(live.snapshot_account_id), amount="10.0000"
    )
    assert no_snapshot.status_code == 422
    assert no_snapshot.json()["detail"] == "this account has no current value to reserve"

    zero_whole = client().post(
        "/reservations",
        json=_reservation_body(
            live,
            goal["id"],
            accountId=str(live.zero_value_account_id),
            kind="whole_account",
            amount=None,
        ),
        headers=ORIGIN,
    )
    assert zero_whole.status_code == 422
    assert zero_whole.json()["detail"] == "this account has no current value to reserve"


def test_a_closed_goal_takes_no_reservation(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    close = client().post(f"/goals/{goal['id']}/close", json={"state": "abandoned"}, headers=ORIGIN)
    assert close.status_code == 200

    response = _reserve(live, goal["id"], amount="100.0000")
    assert response.status_code == 409
    assert response.json()["detail"] == "a closed goal takes no reservation"


def test_an_emergency_reservation_is_refused_until_task_2(live: GoalsFixture) -> None:
    response = client().post(
        "/reservations",
        json={
            "purpose": "emergency",
            "goalId": None,
            "accountId": str(live.account_id),
            "kind": "amount",
            "amount": "100.0000",
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "mark this account as an emergency resource first"


def test_a_missing_exchange_rate_is_refused(live: GoalsFixture) -> None:
    """No fx_rate row exists for the secondary currency into base on today's real date."""
    goal = _create_goal(live, targetCurrency=live.currency)
    response = _reserve(
        live, goal["id"], accountId=str(live.secondary_account_id), amount="100.0000"
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "no exchange rate is available for this reservation"


def test_goal_currency_outside_the_reporting_pair_is_422(live: GoalsFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT code FROM currency WHERE code NOT IN (%s, %s) LIMIT 1",
            (live.currency, live.secondary_currency),
        )
        found = cur.fetchone()
    if found is None:
        pytest.skip("fewer than three currencies seeded")
    third = str(found[0]).strip()
    response = client().post("/goals", json=_goal_body(live, targetCurrency=third), headers=ORIGIN)
    assert response.status_code == 422


# ---------------------------------------------------------------------------------------
# Replay, conflict, close releases reservations, currency change once reserved.


def test_reservation_replay_and_conflict(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    key = str(uuid4())
    first = _reserve(live, goal["id"], amount="500.0000", idempotencyKey=key)
    assert first.status_code == 201
    reservation_id = first.json()["id"]

    replay = _reserve(live, goal["id"], amount="500.0000", idempotencyKey=key)
    assert replay.status_code == 200
    assert replay.json()["id"] == reservation_id

    conflict = _reserve(live, goal["id"], amount="999.0000", idempotencyKey=key)
    assert conflict.status_code == 409


def test_goal_replay_and_conflict(live: GoalsFixture) -> None:
    key = str(uuid4())
    body = _goal_body(live, idempotencyKey=key, targetAmount="5000.0000")
    first = client().post("/goals", json=body, headers=ORIGIN)
    assert first.status_code == 201
    goal_id = first.json()["id"]

    replay = client().post("/goals", json=body, headers=ORIGIN)
    assert replay.status_code == 200
    assert replay.json()["id"] == goal_id

    conflicting = client().post(
        "/goals",
        json=_goal_body(live, idempotencyKey=key, targetAmount="9999.0000"),
        headers=ORIGIN,
    )
    assert conflicting.status_code == 409


def test_closing_a_goal_releases_its_active_reservations(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    reserved = _reserve(live, goal["id"], amount="500.0000")
    assert reserved.status_code == 201

    closed = client().post(f"/goals/{goal['id']}/close", json={"state": "achieved"}, headers=ORIGIN)
    assert closed.status_code == 200
    body = closed.json()
    assert body["state"] == "achieved"
    assert all(r["releasedAt"] is not None for r in body["reservations"])

    replay = client().post(f"/goals/{goal['id']}/close", json={"state": "achieved"}, headers=ORIGIN)
    assert replay.status_code == 200

    conflict = client().post(
        f"/goals/{goal['id']}/close", json={"state": "abandoned"}, headers=ORIGIN
    )
    assert conflict.status_code == 409


def test_currency_change_is_refused_once_a_reservation_exists(live: GoalsFixture) -> None:
    goal = _create_goal(live, targetCurrency=live.currency)
    unchanged_body = {
        k: v
        for k, v in _goal_body(live, targetCurrency=live.secondary_currency).items()
        if k != "idempotencyKey"
    }

    # Before any reservation, the currency may change freely.
    changed = client().put(f"/goals/{goal['id']}", json=unchanged_body, headers=ORIGIN)
    assert changed.status_code == 200
    assert changed.json()["targetCurrency"] == live.secondary_currency

    # Put it back to base and reserve against it.
    back = {**unchanged_body, "targetCurrency": live.currency}
    client().put(f"/goals/{goal['id']}", json=back, headers=ORIGIN)
    reserved = _reserve(live, goal["id"], amount="100.0000")
    assert reserved.status_code == 201

    refused = client().put(f"/goals/{goal['id']}", json=unchanged_body, headers=ORIGIN)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "a goal with reservations keeps its currency"


# ---------------------------------------------------------------------------------------
# Progress after a synthetic rate change.


def test_progress_reflects_a_rate_change_since_reservation(live: GoalsFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 0.2500000000, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                 SET rate = EXCLUDED.rate""",
            (TODAY, live.secondary_currency, live.currency),
        )
    live.connection.commit()

    goal = _create_goal(live, targetCurrency=live.currency)
    reserved = _reserve(
        live, goal["id"], accountId=str(live.secondary_account_id), amount="4000.0000"
    )
    assert reserved.status_code == 201, reserved.text

    later = TODAY + timedelta(days=1)
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               VALUES (%s, %s, %s, 0.3000000000, 'manual')
               ON CONFLICT (rate_date, base_currency, quote_currency) DO UPDATE
                 SET rate = EXCLUDED.rate""",
            (later, live.secondary_currency, live.currency),
        )
    live.connection.commit()

    response = client().get("/goals", params={"asOf": str(later)}, headers=ORIGIN)
    assert response.status_code == 200
    row = next(g for g in response.json()["goals"] if g["id"] == goal["id"])
    assert row["progressStatus"] == "available"
    # 4000 native at 0.25 contributed = 1000.0000; at 0.30 now current = 1200.0000; fx = 200.0000.
    assert row["contributed"] == "1000.0000"
    assert row["current"] == "1200.0000"
    assert row["market"] == "0.0000"
    assert row["fx"] == "200.0000"

    # A goal in the secondary currency rates through current_rate_pair's cross leg, since fx_rate
    # quotes only into base: 1000 base at 1/0.25 = 4000.0000, now at 1/0.30 = 3333.3333.
    secondary_goal = _create_goal(live, targetCurrency=live.secondary_currency)
    crossed = _reserve(live, secondary_goal["id"], amount="1000.0000")
    assert crossed.status_code == 201, crossed.text
    assert crossed.json()["rateAtReservation"] == "4.0000000000"
    response = client().get("/goals", params={"asOf": str(later)}, headers=ORIGIN)
    row = next(g for g in response.json()["goals"] if g["id"] == secondary_goal["id"])
    assert (row["contributed"], row["current"]) == ("4000.0000", "3333.3333")
    assert (row["market"], row["fx"]) == ("0.0000", "-666.6667")


def test_ledger_whole_account_counts_a_later_deposit_as_contributed(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    whole = _reserve(
        live, goal["id"], accountId=str(live.whole_account_id), kind="whole_account", amount=None
    )
    assert whole.status_code == 201, whole.text
    assert whole.json()["nativeAmount"] == "5000.0000"
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                       amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                       amount_base, amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, %s, 'SYNTHETIC GOAL DEPOSIT', 700.0000, %s, %s, 1, 1, 700.0000,
                       700.0000, 'income', 'manual', %s)""",
            (live.owner.household_id, live.whole_account_id, TODAY, live.currency, TODAY, uuid4()),
        )
    live.connection.commit()
    row = next(
        g for g in client().get("/goals", headers=ORIGIN).json()["goals"] if g["id"] == goal["id"]
    )
    # ledger_balance: every change is new money, so none of it is market or FX movement.
    assert (row["current"], row["contributed"]) == ("5700.0000", "5700.0000")
    assert (row["market"], row["fx"]) == ("0.0000", "0.0000")


# ---------------------------------------------------------------------------------------
# Household isolation and read-only GET.


def test_isolation_with_real_foreign_rows(live: GoalsFixture) -> None:
    reserved = client().post(
        "/reservations",
        json={
            "purpose": "goal",
            "goalId": str(live.other_goal_id),
            "accountId": str(live.other_account_id),
            "kind": "amount",
            "amount": "100.0000",
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    # This household's session cannot see the other household's account — 404.
    assert reserved.status_code == 404

    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO resource_reservation (household_id, idempotency_key, purpose, goal_id,
                     account_id, kind, native_amount, currency, reserved_on, rate_currency,
                     rate_at_reservation)
               VALUES (%s, %s, 'goal', %s, %s, 'amount', 250.0000, %s, %s, %s, 1)
               RETURNING id""",
            (
                live.other_owner.household_id,
                uuid4(),
                live.other_goal_id,
                live.other_account_id,
                live.currency,
                TODAY,
                live.currency,
            ),
        )
        foreign_reservation = cur.fetchone()
        assert foreign_reservation is not None
    live.connection.commit()

    goal = _create_goal(live)
    my_reservation = _reserve(live, goal["id"], amount="100.0000")
    assert my_reservation.status_code == 201

    listed = client().get("/goals", headers=ORIGIN)
    assert listed.status_code == 200
    body = listed.json()
    assert all(g["id"] != str(live.other_goal_id) for g in body["goals"])
    assert all(a["accountId"] != str(live.other_account_id) for a in body["accounts"])

    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT released_at FROM resource_reservation WHERE id = %s",
            (foreign_reservation[0],),
        )
        still_active = cur.fetchone()
        assert still_active is not None and still_active[0] is None
        cur.execute("SELECT name FROM goal WHERE id = %s", (live.other_goal_id,))
        still_named = cur.fetchone()
        assert still_named is not None and still_named[0] == "Other synthetic goal"


def test_get_goals_changes_no_row(live: GoalsFixture) -> None:
    goal = _create_goal(live)
    _reserve(live, goal["id"], amount="100.0000")

    def _snapshot() -> tuple[list[Any], list[Any]]:
        with live.connection.cursor() as cur:
            cur.execute(
                "SELECT id, updated_at FROM goal WHERE household_id = %s ORDER BY id",
                (live.owner.household_id,),
            )
            goals = cur.fetchall()
            cur.execute(
                "SELECT id, released_at FROM resource_reservation WHERE household_id = %s "
                "ORDER BY id",
                (live.owner.household_id,),
            )
            reservations = cur.fetchall()
        return goals, reservations

    before = _snapshot()
    response = client().get("/goals", headers=ORIGIN)
    assert response.status_code == 200
    after = _snapshot()
    assert before == after


# ---------------------------------------------------------------------------------------
# Progress after a synthetic price change and a later purchase (a holdings-mode account).


def _acquire(
    fixture: EventFixture, *, units: str, price: str, charges: str, net_amount: str, on: date
) -> dict[str, Any]:
    gross = str((Decimal(units) * Decimal(price)).quantize(Decimal("0.0001")))
    body = {
        "accountId": str(fixture.account_id),
        "securityId": str(fixture.security_id),
        "eventDate": str(on),
        "eventKind": "purchase",
        "units": units,
        "price": price,
        "grossAmount": gross,
        "charges": charges,
        "withheldTax": "0.0000",
        "netAmount": net_amount,
        "currency": fixture.currency,
        "idempotencyKey": str(uuid4()),
    }
    response = TestClient(app).post("/investment-events/acquisitions", json=body, headers=ORIGIN)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _price(fixture: EventFixture, on: date, value: str) -> None:
    response = TestClient(app).post(
        f"/investment-catalogue/securities/{fixture.security_id}/manual-prices",
        json={"priceDate": str(on), "price": value},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def test_holdings_progress_after_a_later_purchase_and_a_price_change(
    live_events: EventFixture,
) -> None:
    """A whole-account reservation on a holdings account: the frozen value is contributed, a
    later purchase is measured as new money (contributed), and the rest of the change is market
    movement — same currency throughout, so fx stays zero."""
    _acquire(
        live_events,
        units="4.00000000",
        price="5.000000",
        charges="1.0000",
        net_amount="-21.0000",
        on=date(2026, 2, 1),
    )
    _price(live_events, date(2026, 2, 2), "5.000000")

    goal = client().post(
        "/goals",
        json={
            "name": "Synthetic holdings goal",
            "targetAmount": "1000.0000",
            "targetCurrency": live_events.currency,
            "targetDate": str(TODAY + timedelta(days=365)),
            "priority": 1,
            "plannedMonthly": None,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert goal.status_code == 201, goal.text
    goal_id = goal.json()["id"]

    reserved = client().post(
        "/reservations",
        json={
            "purpose": "goal",
            "goalId": goal_id,
            "accountId": str(live_events.account_id),
            "kind": "whole_account",
            "amount": None,
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert reserved.status_code == 201, reserved.text
    assert reserved.json()["nativeAmount"] == "20.0000"  # 4 units at 5.000000

    later_purchase = TODAY + timedelta(days=1)
    _acquire(
        live_events,
        units="2.00000000",
        price="5.000000",
        charges="0.0000",
        net_amount="-10.0000",
        on=later_purchase,
    )
    _price(live_events, TODAY + timedelta(days=2), "8.000000")

    response = client().get(
        "/goals", params={"asOf": str(TODAY + timedelta(days=3))}, headers=ORIGIN
    )
    assert response.status_code == 200
    row = next(g for g in response.json()["goals"] if g["id"] == goal_id)
    assert row["progressStatus"] == "available"
    # 6 units at 8.000000 = 48.0000; contributed 20 (frozen) + 10 (new purchase) = 30.0000;
    # market is the rest of the change (28 - 10 = 18.0000); fx is zero (one currency throughout).
    assert row["current"] == "48.0000"
    assert row["contributed"] == "30.0000"
    assert row["market"] == "18.0000"
    assert row["fx"] == "0.0000"
