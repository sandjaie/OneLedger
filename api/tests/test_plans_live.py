"""Live owner-scoped commitment and forecast tests (Phase 1.7 Task 2).

Skipped when no database is reachable. Every name, label and amount is synthetic. Forecast
balances come straight from `account.current_balance`/`current_balance_as_of` — no ledger
posting is needed to move them, so these fixtures set the opening balance directly rather than
inserting transactions.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}


@dataclass(frozen=True)
class PlansFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    person_id: UUID
    account_id: UUID
    other_account_id: UUID
    secondary_account_id: UUID
    inactive_account_id: UUID
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
    opening: str = "10000.0000",
    opening_date: date = date(2026, 1, 1),
    active: bool = True,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, is_active,
                                 closed_on)
           VALUES (%s, %s, %s, 'savings', %s, 'XZ', %s, %s, %s, %s) RETURNING id""",
        (
            household_id,
            person_id,
            name,
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
def live() -> Iterator[PlansFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "plans-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-plans-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base, secondary = found
        for household, name in (
            (household_id, "plans household"),
            (other_household_id, "other plans household"),
        ):
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, %s, %s, %s)""",
                (household, name, base, secondary),
            )
        for person, household, name in (
            (person_id, household_id, "plans owner"),
            (other_person_id, other_household_id, "other plans owner"),
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
        account_id = _account(cur, household_id, person_id, "Synthetic plans account", base)
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic plans account", base
        )
        secondary_account_id = _account(
            cur, household_id, person_id, "Synthetic secondary-currency account", secondary
        )
        inactive_account_id = _account(
            cur, household_id, person_id, "Synthetic closed account", base, active=False
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield PlansFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            person_id=person_id,
            account_id=account_id,
            other_account_id=other_account_id,
            secondary_account_id=secondary_account_id,
            inactive_account_id=inactive_account_id,
            currency=str(base).strip(),
            secondary_currency=str(secondary).strip(),
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            # commitment is this branch's new table; other suites delete accounts and
            # truncate transaction, so a fresh synthetic household per run is enough for
            # everything else.
            cur.execute("TRUNCATE commitment CASCADE")
        connection.commit()
        connection.close()


def _body(fixture: PlansFixture, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "accountId": str(fixture.account_id),
        "label": "Synthetic salary",
        "amount": "5000.0000",
        "currency": fixture.currency,
        "cadence": "monthly",
        "firstDate": "2026-01-01",
        "endDate": None,
        "idempotencyKey": str(uuid4()),
    }
    base.update(overrides)
    return base


def _commitment_count(fixture: PlansFixture) -> int:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM commitment WHERE household_id = %s", (fixture.owner.household_id,)
        )
        found = cur.fetchone()
        assert found is not None
        return int(found[0])


def test_create_replay_conflicting_key_and_exact_round_trip(live: PlansFixture) -> None:
    key = str(uuid4())
    body = _body(live, idempotencyKey=key, amount="-1234.5678")

    created = client().post("/commitments", json=body, headers=ORIGIN)
    assert created.status_code == 201
    row = created.json()
    assert row["amount"] == "-1234.5678"
    assert row["accountId"] == str(live.account_id)
    commitment_id = row["id"]

    replay = client().post("/commitments", json=body, headers=ORIGIN)
    assert replay.status_code == 200
    assert replay.json()["id"] == commitment_id

    conflicting = client().post(
        "/commitments", json=_body(live, idempotencyKey=key, amount="-9999.0000"), headers=ORIGIN
    )
    assert conflicting.status_code == 409
    assert _commitment_count(live) == 1

    listed = client().get("/commitments", headers=ORIGIN)
    assert listed.status_code == 200
    assert listed.json()["commitments"][0]["amount"] == "-1234.5678"

    updated = client().put(
        f"/commitments/{commitment_id}",
        json={k: v for k, v in _body(live, amount="-1234.5678").items() if k != "idempotencyKey"},
        headers=ORIGIN,
    )
    assert updated.status_code == 200
    assert updated.json()["amount"] == "-1234.5678"


def test_foreign_account_and_commitment_are_404(live: PlansFixture) -> None:
    foreign_account = client().post(
        "/commitments", json=_body(live, accountId=str(live.other_account_id)), headers=ORIGIN
    )
    assert foreign_account.status_code == 404

    foreign_commitment = client().put(
        f"/commitments/{uuid4()}",
        json={k: v for k, v in _body(live).items() if k != "idempotencyKey"},
        headers=ORIGIN,
    )
    assert foreign_commitment.status_code == 404

    foreign_archive = client().post(f"/commitments/{uuid4()}/archive", headers=ORIGIN)
    assert foreign_archive.status_code == 404


def test_currency_mismatch_is_422(live: PlansFixture) -> None:
    response = client().post(
        "/commitments",
        json=_body(live, currency=live.secondary_currency),
        headers=ORIGIN,
    )
    assert response.status_code == 422
    assert _commitment_count(live) == 0


def test_inactive_account_is_422(live: PlansFixture) -> None:
    response = client().post(
        "/commitments", json=_body(live, accountId=str(live.inactive_account_id)), headers=ORIGIN
    )
    assert response.status_code == 422
    assert _commitment_count(live) == 0


def test_archived_update_is_409(live: PlansFixture) -> None:
    created = client().post("/commitments", json=_body(live), headers=ORIGIN)
    commitment_id = created.json()["id"]
    archived = client().post(f"/commitments/{commitment_id}/archive", headers=ORIGIN)
    assert archived.status_code == 200
    assert archived.json()["archivedAt"] is not None
    # Archiving again is idempotent.
    archived_again = client().post(f"/commitments/{commitment_id}/archive", headers=ORIGIN)
    assert archived_again.status_code == 200
    assert archived_again.json()["archivedAt"] == archived.json()["archivedAt"]

    update = client().put(
        f"/commitments/{commitment_id}",
        json={k: v for k, v in _body(live).items() if k != "idempotencyKey"},
        headers=ORIGIN,
    )
    assert update.status_code == 409


def test_excess_precision_is_422_with_no_row(live: PlansFixture) -> None:
    too_many_decimals = client().post(
        "/commitments", json=_body(live, amount="1.00001"), headers=ORIGIN
    )
    assert too_many_decimals.status_code == 422
    too_many_digits = client().post(
        "/commitments", json=_body(live, amount="1" * 17 + ".0000"), headers=ORIGIN
    )
    assert too_many_digits.status_code == 422
    zero = client().post("/commitments", json=_body(live, amount="0.0000"), headers=ORIGIN)
    assert zero.status_code == 422
    assert _commitment_count(live) == 0


def test_forecast_start_balance_date_and_due_by_as_of(live: PlansFixture) -> None:
    as_of = date(2026, 3, 1)
    client().post(
        "/commitments",
        json=_body(live, amount="1000.0000", firstDate="2026-02-01", cadence="monthly"),
        headers=ORIGIN,
    )
    response = client().get(f"/forecast?asOf={as_of.isoformat()}", headers=ORIGIN)
    assert response.status_code == 200
    body = response.json()
    account = next(a for a in body["accounts"] if a["accountId"] == str(live.account_id))
    assert account["startBalance"] == "10000.0000"
    assert account["balanceDate"] == "2026-01-01"
    occurrence = account["occurrences"][0]
    assert occurrence["date"] == "2026-02-01"
    assert occurrence["dueByAsOf"] is True
    future = account["occurrences"][-1]
    assert future["dueByAsOf"] is False
    points = {p["days"]: p for p in account["points"]}
    assert points[30]["date"] == "2026-03-31"
    assert points[30]["balance"] == "12000.0000"  # Feb 1 and Mar 1 both applied by day 30
    assert points[60]["balance"] == "13000.0000"  # plus the Apr 1 occurrence
    assert points[90]["balance"] == "14000.0000"  # plus the May 1 occurrence


def test_not_ledger_valued_account_is_excluded(live: PlansFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute("SELECT set_config('app.user_id', %s, true)", (str(live.owner.user_id),))
        cur.execute(
            "SELECT transition_account_valuation_mode(%s, 'holdings', '2026-01-02')",
            (live.account_id,),
        )
    live.connection.commit()
    response = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
    assert response.status_code == 200
    body = response.json()
    assert not any(a["accountId"] == str(live.account_id) for a in body["accounts"])
    excluded = next(a for a in body["excludedAccounts"] if a["accountId"] == str(live.account_id))
    assert excluded["reason"] == "not_ledger_valued"


def test_no_balance_date_and_balance_after_as_of_are_excluded(live: PlansFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            "UPDATE account SET current_balance_as_of = NULL WHERE id = %s", (live.account_id,)
        )
    live.connection.commit()
    no_date = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
    excluded = next(
        a for a in no_date.json()["excludedAccounts"] if a["accountId"] == str(live.account_id)
    )
    assert excluded["reason"] == "no_balance_date"

    future = client().get("/forecast?asOf=2025-12-31", headers=ORIGIN)
    excluded_secondary = next(
        a
        for a in future.json()["excludedAccounts"]
        if a["accountId"] == str(live.secondary_account_id)
    )
    assert excluded_secondary["reason"] == "balance_after_as_of"


def test_closed_account_and_currency_mismatch_commitments_are_excluded(live: PlansFixture) -> None:
    created = client().post("/commitments", json=_body(live), headers=ORIGIN)
    commitment_id = created.json()["id"]
    with live.connection.cursor() as cur:
        # Force a live inconsistency that write-time validation would refuse: the account is
        # closed after the commitment was recorded.
        cur.execute(
            "UPDATE account SET is_active = false, closed_on = DATE '2026-02-01' WHERE id = %s",
            (live.account_id,),
        )
    live.connection.commit()
    response = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
    excluded = next(
        c for c in response.json()["excludedCommitments"] if c["commitmentId"] == commitment_id
    )
    assert excluded["reason"] == "account_closed"


def test_currency_mismatch_commitment_is_excluded(live: PlansFixture) -> None:
    created = client().post("/commitments", json=_body(live), headers=ORIGIN)
    commitment_id = created.json()["id"]
    with live.connection.cursor() as cur:
        cur.execute(
            "UPDATE commitment SET currency = %s WHERE id = %s",
            (live.secondary_currency, commitment_id),
        )
    live.connection.commit()
    response = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
    excluded = next(
        c for c in response.json()["excludedCommitments"] if c["commitmentId"] == commitment_id
    )
    assert excluded["reason"] == "currency_mismatch"


def test_archived_commitment_is_ignored_by_the_forecast(live: PlansFixture) -> None:
    created = client().post("/commitments", json=_body(live), headers=ORIGIN)
    commitment_id = created.json()["id"]
    client().post(f"/commitments/{commitment_id}/archive", headers=ORIGIN)
    response = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
    account = next(a for a in response.json()["accounts"] if a["accountId"] == str(live.account_id))
    assert account["occurrences"] == []
    assert account["points"][0]["balance"] == "10000.0000"


def test_another_households_commitments_are_invisible(live: PlansFixture) -> None:
    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO commitment (household_id, idempotency_key, account_id, label, amount,
                                        currency, cadence, first_date)
               VALUES (%s, %s, %s, 'Other synthetic rent', -500.0000, %s, 'monthly',
                       '2026-01-01')""",
            (live.other_owner.household_id, uuid4(), live.other_account_id, live.currency),
        )
    live.connection.commit()
    listed = client().get("/commitments", headers=ORIGIN)
    assert listed.json()["commitments"] == []
    forecast = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
    account = next(a for a in forecast.json()["accounts"] if a["accountId"] == str(live.account_id))
    assert account["occurrences"] == []


def test_forecast_writes_nothing(live: PlansFixture) -> None:
    client().post("/commitments", json=_body(live), headers=ORIGIN)
    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM commitment")
        commitments_before = cur.fetchone()
        cur.execute(
            "SELECT current_balance, current_balance_as_of FROM account WHERE id = %s",
            (live.account_id,),
        )
        balance_before = cur.fetchone()
    before_dashboard = client().get("/dashboard", headers=ORIGIN).json()

    for _ in range(2):
        response = client().get("/forecast?asOf=2026-03-01", headers=ORIGIN)
        assert response.status_code == 200

    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM commitment")
        commitments_after = cur.fetchone()
        cur.execute(
            "SELECT current_balance, current_balance_as_of FROM account WHERE id = %s",
            (live.account_id,),
        )
        balance_after = cur.fetchone()
    after_dashboard = client().get("/dashboard", headers=ORIGIN).json()

    assert commitments_after == commitments_before
    assert balance_after == balance_before
    assert after_dashboard == before_dashboard
