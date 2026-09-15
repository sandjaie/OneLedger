"""Live owner-scoped tests for the register (Phase 3.4 Task 1): create/update per kind,
kind-forbidden fields, the deposit account check, the reviewed/renewed/complete/reopen lifecycle,
terminal archiving and household isolation. Skipped when no database is reachable. Every name,
label and amount is synthetic."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
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


def _d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


@dataclass(frozen=True)
class RegisterFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    person_id: UUID
    household_id: UUID
    other_household_id: UUID
    asset_account_id: UUID
    deposit_account_id: UUID
    recurring_deposit_account_id: UUID
    inactive_deposit_account_id: UUID
    other_account_id: UUID
    currency: str


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
    active: bool = True,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, is_active,
                                 closed_on)
           VALUES (%s, %s, %s, %s, %s, 'XZ', 1000, '2026-01-01', %s, %s) RETURNING id""",
        (
            household_id,
            person_id,
            name,
            account_type,
            currency,
            active,
            None if active else date(2026, 2, 1),
        ),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


@pytest.fixture
def live() -> Iterator[RegisterFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "register-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-register-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base = str(found[0]).strip()
        for household, name in (
            (household_id, "register household"),
            (other_household_id, "other register household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, (SELECT max(code) FROM currency))",
                (household, name, base),
            )
        for person, household, name in (
            (person_id, household_id, "register owner"),
            (other_person_id, other_household_id, "other register owner"),
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
        asset_account_id = _account(cur, household_id, person_id, "Synthetic asset", base)
        deposit_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic fixed deposit",
            base,
            account_type="fixed_deposit",
        )
        recurring_deposit_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic recurring deposit",
            base,
            account_type="recurring_deposit",
        )
        inactive_deposit_account_id = _account(
            cur,
            household_id,
            person_id,
            "Synthetic closed deposit",
            base,
            account_type="fixed_deposit",
            active=False,
        )
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic asset", base
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield RegisterFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            person_id=person_id,
            household_id=household_id,
            other_household_id=other_household_id,
            asset_account_id=asset_account_id,
            deposit_account_id=deposit_account_id,
            recurring_deposit_account_id=recurring_deposit_account_id,
            inactive_deposit_account_id=inactive_deposit_account_id,
            other_account_id=other_account_id,
            currency=base,
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE register_record CASCADE")
        connection.commit()
        connection.close()


def _body(kind: str, **fields: object) -> dict[str, object]:
    body: dict[str, object] = {"kind": kind, "idempotencyKey": str(uuid4())}
    body.update(fields)
    return body


def _create(body: dict[str, object]) -> httpx.Response:
    response: httpx.Response = client().post("/register", json=body, headers=ORIGIN)
    return response


def _update(record_id: str, body: dict[str, object]) -> httpx.Response:
    response: httpx.Response = client().put(f"/register/{record_id}", json=body, headers=ORIGIN)
    return response


def _kind_bodies(live: RegisterFixture) -> dict[str, dict[str, object]]:
    return {
        "insurance_policy": _body(
            "insurance_policy",
            title="Synthetic policy",
            counterparty="Synthetic insurer",
            covered="Synthetic car",
            amount="500000.0000",
            currency=live.currency,
            premiumAmount="1200.0000",
            premiumCadence="annual",
            keyDate=_d(20),
        ),
        "deposit": _body(
            "deposit",
            title="Synthetic deposit",
            counterparty="Synthetic bank",
            amount="10000.0000",
            currency=live.currency,
            keyDate=_d(20),
            accountId=str(live.deposit_account_id),
            instruction="Synthetic renewal instruction",
        ),
        "claim": _body(
            "claim",
            title="Synthetic claim",
            counterparty="Synthetic insurer",
            amount="1000.0000",
            currency=live.currency,
            keyDate=_d(-5),
        ),
        "nominee": _body(
            "nominee",
            title="Synthetic nominee",
            accountId=str(live.asset_account_id),
            nomineeRecorded=True,
        ),
        "administration": _body("administration", title="Synthetic deadline", keyDate=_d(5)),
    }


# ---------------------------------------------------------------------------------------
# Create and update, one of each kind.


def test_create_and_update_each_kind(live: RegisterFixture) -> None:
    for kind, body in _kind_bodies(live).items():
        created = _create(body)
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["kind"] == kind
        assert row["archivedAt"] is None
        assert row["reviewDue"] is False

        replay = _create(body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == row["id"]

        updated_body = dict(body)
        del updated_body["kind"]
        del updated_body["idempotencyKey"]
        updated_body["title"] = f"{body['title']} (updated)"
        updated = _update(row["id"], updated_body)
        assert updated.status_code == 200, updated.text
        assert updated.json()["title"] == updated_body["title"]


def test_forbidden_field_for_each_kind_is_422(live: RegisterFixture) -> None:
    refusals = {
        "insurance_policy": {"accountId": str(live.asset_account_id)},
        "deposit": {"covered": "Synthetic car"},
        "claim": {"premiumAmount": "10.0000", "premiumCadence": "monthly"},
        "nominee": {"amount": "10.0000", "currency": live.currency},
        "administration": {"counterparty": "Synthetic party"},
    }
    for kind, body in _kind_bodies(live).items():
        bad = dict(body)
        bad.update(refusals[kind])
        response = _create(bad)
        assert response.status_code == 422, (kind, response.text)


def test_create_requires_a_key_date_for_a_policy(live: RegisterFixture) -> None:
    body = _kind_bodies(live)["insurance_policy"]
    del body["keyDate"]
    response = _create(body)
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------------------
# The deposit account check.


def test_deposit_account_must_be_an_active_fixed_or_recurring_deposit(
    live: RegisterFixture,
) -> None:
    wrong_type = _create({**_kind_bodies(live)["deposit"], "accountId": str(live.asset_account_id)})
    assert wrong_type.status_code == 422, wrong_type.text

    closed = _create(
        {**_kind_bodies(live)["deposit"], "accountId": str(live.inactive_deposit_account_id)}
    )
    assert closed.status_code == 422, closed.text

    recurring = _create(
        {**_kind_bodies(live)["deposit"], "accountId": str(live.recurring_deposit_account_id)}
    )
    assert recurring.status_code == 201, recurring.text

    missing = _create({**_kind_bodies(live)["deposit"], "accountId": str(uuid4())})
    assert missing.status_code == 404, missing.text


# ---------------------------------------------------------------------------------------
# reviewed, renewed, complete/reopen, archive.


def test_reviewed_is_idempotent_same_day(live: RegisterFixture) -> None:
    created = _create(_kind_bodies(live)["insurance_policy"])
    record_id = created.json()["id"]

    first = client().post(f"/register/{record_id}/reviewed", headers=ORIGIN)
    assert first.status_code == 200, first.text
    assert first.json()["lastReviewedOn"] == TODAY.isoformat()
    assert first.json()["reviewDue"] is False

    second = client().post(f"/register/{record_id}/reviewed", headers=ORIGIN)
    assert second.status_code == 200, second.text
    assert second.json()["lastReviewedOn"] == TODAY.isoformat()


def test_renewed_requires_the_policy_kind_and_a_later_date(live: RegisterFixture) -> None:
    policy = _create(_kind_bodies(live)["insurance_policy"]).json()
    claim = _create(_kind_bodies(live)["claim"]).json()

    wrong_kind = client().post(
        f"/register/{claim['id']}/renewed", json={"nextRenewalDate": _d(400)}, headers=ORIGIN
    )
    assert wrong_kind.status_code == 422, wrong_kind.text

    not_later = client().post(
        f"/register/{policy['id']}/renewed",
        json={"nextRenewalDate": policy["keyDate"]},
        headers=ORIGIN,
    )
    assert not_later.status_code == 422, not_later.text

    renewed = client().post(
        f"/register/{policy['id']}/renewed", json={"nextRenewalDate": _d(400)}, headers=ORIGIN
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["keyDate"] == _d(400)


def test_complete_and_reopen_are_claim_or_administration_only(live: RegisterFixture) -> None:
    policy = _create(_kind_bodies(live)["insurance_policy"]).json()
    claim = _create(_kind_bodies(live)["claim"]).json()
    deadline = _create(_kind_bodies(live)["administration"]).json()

    refused = client().post(f"/register/{policy['id']}/complete", headers=ORIGIN)
    assert refused.status_code == 422, refused.text

    for record in (claim, deadline):
        completed = client().post(f"/register/{record['id']}/complete", headers=ORIGIN)
        assert completed.status_code == 200, completed.text
        assert completed.json()["completedAt"] is not None

        replay = client().post(f"/register/{record['id']}/complete", headers=ORIGIN)
        assert replay.status_code == 200, replay.text
        assert replay.json()["completedAt"] == completed.json()["completedAt"]

        reopened = client().post(f"/register/{record['id']}/reopen", headers=ORIGIN)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["completedAt"] is None


def test_archive_is_terminal(live: RegisterFixture) -> None:
    record = _create(_kind_bodies(live)["administration"]).json()

    archived = client().post(f"/register/{record['id']}/archive", headers=ORIGIN)
    assert archived.status_code == 200, archived.text
    assert archived.json()["archivedAt"] is not None

    replay = client().post(f"/register/{record['id']}/archive", headers=ORIGIN)
    assert replay.status_code == 200, replay.text
    assert replay.json()["archivedAt"] == archived.json()["archivedAt"]

    body = dict(_kind_bodies(live)["administration"])
    del body["kind"]
    del body["idempotencyKey"]
    put_refused = _update(record["id"], body)
    assert put_refused.status_code == 409, put_refused.text

    reviewed_refused = client().post(f"/register/{record['id']}/reviewed", headers=ORIGIN)
    assert reviewed_refused.status_code == 409, reviewed_refused.text

    complete_refused = client().post(f"/register/{record['id']}/complete", headers=ORIGIN)
    assert complete_refused.status_code == 409, complete_refused.text


# ---------------------------------------------------------------------------------------
# Household isolation with real foreign rows, and GET changes no row.


def test_household_isolation_with_real_foreign_rows(live: RegisterFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign = _create(_body("administration", title="Foreign synthetic deadline", keyDate=_d(5)))
    assert foreign.status_code == 201, foreign.text
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    mine = _create(_kind_bodies(live)["administration"])
    assert mine.status_code == 201, mine.text

    view = client().get("/register", headers=ORIGIN).json()
    assert [r["id"] for r in view["records"]] == [mine.json()["id"]]

    not_found = client().put(
        f"/register/{foreign.json()['id']}",
        json={"title": "Hijacked", "keyDate": _d(5)},
        headers=ORIGIN,
    )
    assert not_found.status_code == 404, not_found.text

    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign_view = client().get("/register", headers=ORIGIN).json()
    assert [r["id"] for r in foreign_view["records"]] == [foreign.json()["id"]]
    app.dependency_overrides[auth.require_owner] = lambda: live.owner


def test_get_register_changes_no_row(live: RegisterFixture) -> None:
    _create(_kind_bodies(live)["administration"])
    before = client().get("/register", headers=ORIGIN).json()

    client().get("/register", headers=ORIGIN)

    after = client().get("/register", headers=ORIGIN).json()
    assert after == before
    with live.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM register_record WHERE household_id = %s", (live.household_id,)
        )
        row = cur.fetchone()
        assert row is not None and row[0] == 1
