"""Live owner-scoped tests for the Needs attention list (Phase 3.1 Task 2): the three action-item
kinds `GET /actions` gathers, snooze and dismissal marks, and the owner-entered follow-up
lifecycle. Skipped when no database is reachable. Every name, label and amount is synthetic.

Dates are relative to `date.today()` throughout — `PUT /actions/marks/{key}` validates a snooze
`until` against the real today (spec "after today"), independent of a test's chosen `asOf`, so a
fixed past or future literal would desynchronize the two.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
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
TODAY = date.today()
AS_OF = TODAY


def _d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


@dataclass(frozen=True)
class ActionsFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    person_id: UUID
    other_person_id: UUID
    household_id: UUID
    other_household_id: UUID
    account_id: UUID
    card_account_id: UUID
    other_account_id: UUID
    other_card_account_id: UUID
    currency: str


def client() -> TestClient:
    return TestClient(app)


def _account(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    person_id: UUID,
    name: str,
    currency: str,
    account_type: str = "savings",
    credit_limit: str | None = None,
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date, credit_limit)
           VALUES (%s, %s, %s, %s, %s, 'XZ', 0, '2020-01-01', %s) RETURNING id""",
        (household_id, person_id, name, account_type, currency, credit_limit),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


@pytest.fixture
def live() -> Iterator[ActionsFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "actions-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-actions-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base = str(found[0]).strip()
        for household, name in (
            (household_id, "actions household"),
            (other_household_id, "other actions household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, (SELECT max(code) FROM currency))",
                (household, name, base),
            )
        for person, household, name in (
            (person_id, household_id, "actions owner"),
            (other_person_id, other_household_id, "other actions owner"),
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
        account_id = _account(cur, household_id, person_id, "Synthetic actions account", base)
        card_account_id = _account(
            cur, household_id, person_id, "Synthetic actions card", base, "credit_card", "5000"
        )
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic actions account", base
        )
        other_card_account_id = _account(
            cur,
            other_household_id,
            other_person_id,
            "Other synthetic actions card",
            base,
            "credit_card",
            "5000",
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield ActionsFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            person_id=person_id,
            other_person_id=other_person_id,
            household_id=household_id,
            other_household_id=other_household_id,
            account_id=account_id,
            card_account_id=card_account_id,
            other_account_id=other_account_id,
            other_card_account_id=other_card_account_id,
            currency=base,
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE commitment, action_item, action_mark, transaction CASCADE")
        connection.commit()
        connection.close()


def _commitment(fixture: ActionsFixture, due: str, **overrides: object) -> dict[str, Any]:
    body: dict[str, object] = {
        "accountId": str(fixture.account_id),
        "label": "Synthetic commitment",
        "amount": "-500.0000",
        "currency": fixture.currency,
        "cadence": "once",
        "firstDate": due,
        "endDate": None,
        "nextDueDate": due,
        "idempotencyKey": str(uuid4()),
    }
    body.update(overrides)
    response = client().post("/commitments", json=body, headers=ORIGIN)
    assert response.status_code == 201, response.text
    row: dict[str, Any] = response.json()
    return row


def _card_statement(
    fixture: ActionsFixture,
    account_id: UUID,
    household_id: UUID | None = None,
    *,
    balance: str = "400.0000",
    due_date: str = "",
    statement_date: str = "",
    paid_in_full: bool = False,
) -> UUID:
    due_date = due_date or _d(5)
    statement_date = statement_date or _d(-14)
    period_end = date.fromisoformat(statement_date) - date.resolution
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO card_statement (household_id, account_id, period_start, period_end,
                       statement_date, payment_due_date, currency, opening_balance,
                       closing_balance, statement_balance, is_paid_in_full)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)
               RETURNING id""",
            (
                household_id or fixture.household_id,
                account_id,
                period_end.replace(day=1),
                period_end,
                statement_date,
                due_date,
                fixture.currency,
                -Decimal(balance),
                Decimal(balance),
                paid_in_full,
            ),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


def _actions(fixture: ActionsFixture, as_of: date = AS_OF) -> dict[str, Any]:
    response = client().get(f"/actions?asOf={as_of.isoformat()}", headers=ORIGIN)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _keys(rows: list[dict[str, Any]]) -> set[str]:
    return {row["actionKey"] for row in rows}


# --- Commitment items: the seven-day window and overdue ordering. -----------------------------


def test_commitment_items_window_and_overdue_ordering(live: ActionsFixture) -> None:
    overdue = _commitment(live, _d(-5), label="Synthetic overdue")
    due = _commitment(live, _d(7), label="Synthetic due")
    out = _commitment(live, _d(8), label="Synthetic out of window")

    result = _actions(live)
    keys = _keys(result["items"])
    assert f"commitment.{overdue['id']}.{_d(-5)}" in keys
    assert f"commitment.{due['id']}.{_d(7)}" in keys
    assert f"commitment.{out['id']}.{_d(8)}" not in keys

    ordered = [item for item in result["items"] if item["kind"] == "commitment"]
    assert [item["actionKey"] for item in ordered] == [
        f"commitment.{overdue['id']}.{_d(-5)}",
        f"commitment.{due['id']}.{_d(7)}",
    ]
    assert ordered[0]["overdue"] is True
    assert ordered[0]["reason"] == "commitment_overdue"
    assert ordered[1]["overdue"] is False
    assert ordered[1]["reason"] == "commitment_due"
    assert ordered[1]["remaining"] == "-500.0000"
    assert ordered[1]["accountId"] == str(live.account_id)


# --- Card due membership. ----------------------------------------------------------------------


def test_card_item_membership(live: ActionsFixture) -> None:
    visible = _card_statement(live, live.card_account_id, due_date=_d(5), statement_date=_d(-14))
    _card_statement(
        live, live.card_account_id, balance="0.0000", statement_date=_d(1), due_date=_d(20)
    )
    result = _actions(live)
    card_keys = _keys([row for row in result["items"] if row["kind"] == "card_due"])
    assert f"card.{visible}" in card_keys


def test_card_item_absent_when_paid_in_full_or_zero_balance(live: ActionsFixture) -> None:
    _card_statement(
        live,
        live.card_account_id,
        due_date=_d(5),
        statement_date=_d(-14),
        paid_in_full=True,
    )
    result = _actions(live)
    assert result["items"] == [] and result["snoozed"] == []


def test_card_item_absent_for_an_older_cycle_when_the_latest_is_not_yet_due(
    live: ActionsFixture,
) -> None:
    older = _card_statement(live, live.card_account_id, statement_date=_d(-14), due_date=_d(5))
    # The latest cycle's statement_date is after asOf, so it is filtered out and the older,
    # already-statemented cycle is picked instead.
    _card_statement(live, live.card_account_id, statement_date=_d(1), due_date=_d(20))
    result = _actions(live)
    card_keys = _keys([row for row in result["items"] if row["kind"] == "card_due"])
    assert card_keys == {f"card.{older}"}


def test_card_item_absent_when_due_more_than_seven_days_past(live: ActionsFixture) -> None:
    _card_statement(live, live.card_account_id, due_date=_d(-8), statement_date=_d(-30))
    result = _actions(live)
    assert result["items"] == [] and result["snoozed"] == []


def test_card_item_absent_when_planned_and_present_again_once_archived(
    live: ActionsFixture,
) -> None:
    statement = _card_statement(live, live.card_account_id, due_date=_d(5), statement_date=_d(-14))
    planned = client().post(
        "/commitments",
        json={
            "accountId": str(live.account_id),
            "label": "Synthetic card payment",
            "amount": "-400.0000",
            "currency": live.currency,
            "cadence": "once",
            "firstDate": _d(5),
            "endDate": None,
            "sourceCardStatementId": str(statement),
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert planned.status_code == 201, planned.text
    commitment_id = planned.json()["id"]

    after_planning = _actions(live)
    assert f"card.{statement}" not in _keys(after_planning["items"])
    assert f"commitment.{commitment_id}.{_d(5)}" in _keys(after_planning["items"])

    archived = client().post(f"/commitments/{commitment_id}/archive", headers=ORIGIN)
    assert archived.status_code == 200, archived.text

    after_archiving = _actions(live)
    assert f"card.{statement}" in _keys(after_archiving["items"])
    assert f"commitment.{commitment_id}.{_d(5)}" not in _keys(after_archiving["items"])


def test_card_item_absent_when_dismissed(live: ActionsFixture) -> None:
    statement = _card_statement(live, live.card_account_id, due_date=_d(5), statement_date=_d(-14))
    dismissed = client().put(
        f"/actions/marks/card.{statement}", json={"reason": "paid another way"}, headers=ORIGIN
    )
    assert dismissed.status_code == 204, dismissed.text
    result = _actions(live)
    assert result["items"] == [] and result["snoozed"] == []


# --- Snooze and dismissal marks. ----------------------------------------------------------------


def test_snooze_moves_an_item_to_snoozed_and_back_after_its_date(live: ActionsFixture) -> None:
    due = _commitment(live, _d(7))
    key = f"commitment.{due['id']}.{_d(7)}"

    snoozed = client().put(f"/actions/marks/{key}", json={"until": _d(3)}, headers=ORIGIN)
    assert snoozed.status_code == 204, snoozed.text

    while_snoozed = _actions(live)
    assert key not in _keys(while_snoozed["items"])
    snoozed_row = next(row for row in while_snoozed["snoozed"] if row["actionKey"] == key)
    assert snoozed_row["snoozedUntil"] == _d(3)

    after_wake = _actions(live, as_of=TODAY + timedelta(days=4))
    assert key in _keys(after_wake["items"])
    assert key not in _keys(after_wake["snoozed"])

    woken = client().delete(f"/actions/marks/{key}", headers=ORIGIN)
    assert woken.status_code == 204, woken.text
    # Idempotent — removing an already-removed mark is still 204.
    again = client().delete(f"/actions/marks/{key}", headers=ORIGIN)
    assert again.status_code == 204, again.text


def test_snooze_404_for_a_foreign_or_stale_key(live: ActionsFixture) -> None:
    # A real commitment in the other household — not a random uuid, per the repository's own
    # foreign-household convention (its due date and account belong to that household only).
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign_body = {
        "accountId": str(live.other_account_id),
        "label": "Synthetic foreign commitment",
        "amount": "-100.0000",
        "currency": live.currency,
        "cadence": "once",
        "firstDate": _d(7),
        "endDate": None,
        "nextDueDate": _d(7),
        "idempotencyKey": str(uuid4()),
    }
    foreign = client().post("/commitments", json=foreign_body, headers=ORIGIN)
    assert foreign.status_code == 201, foreign.text
    foreign_id = foreign.json()["id"]
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    foreign_key = f"commitment.{foreign_id}.{_d(7)}"
    refused = client().put(f"/actions/marks/{foreign_key}", json={"until": _d(3)}, headers=ORIGIN)
    assert refused.status_code == 404

    stale_key = f"follow-up.{uuid4()}"
    stale = client().put(f"/actions/marks/{stale_key}", json={"until": _d(3)}, headers=ORIGIN)
    assert stale.status_code == 404


def test_snooze_422_for_a_bad_date_or_a_reason_on_a_non_card_key(live: ActionsFixture) -> None:
    follow_up = client().post(
        "/actions/follow-ups",
        json={"title": "Synthetic follow-up", "idempotencyKey": str(uuid4())},
        headers=ORIGIN,
    )
    assert follow_up.status_code == 201, follow_up.text
    key = f"follow-up.{follow_up.json()['id']}"

    past = client().put(f"/actions/marks/{key}", json={"until": _d(-1)}, headers=ORIGIN)
    assert past.status_code == 422

    too_far = client().put(f"/actions/marks/{key}", json={"until": _d(400)}, headers=ORIGIN)
    assert too_far.status_code == 422

    reason_on_followup = client().put(
        f"/actions/marks/{key}", json={"reason": "not a card"}, headers=ORIGIN
    )
    assert reason_on_followup.status_code == 422

    malformed = client().put("/actions/marks/not-a-real-key", json={"until": _d(3)}, headers=ORIGIN)
    assert malformed.status_code == 422


# --- Follow-up lifecycle. ------------------------------------------------------------------------


def test_follow_up_create_replay_and_conflicting_key(live: ActionsFixture) -> None:
    key = str(uuid4())
    body = {
        "title": "Synthetic follow-up",
        "dueDate": _d(5),
        "accountId": str(live.account_id),
        "idempotencyKey": key,
    }
    created = client().post("/actions/follow-ups", json=body, headers=ORIGIN)
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    replay = client().post("/actions/follow-ups", json=body, headers=ORIGIN)
    assert replay.status_code == 200
    assert replay.json()["id"] == item_id

    conflict = client().post(
        "/actions/follow-ups",
        json={**body, "title": "Synthetic different title"},
        headers=ORIGIN,
    )
    assert conflict.status_code == 409


def test_follow_up_complete_dismiss_reopen_and_409_transitions(live: ActionsFixture) -> None:
    created = client().post(
        "/actions/follow-ups",
        json={"title": "Synthetic follow-up", "idempotencyKey": str(uuid4())},
        headers=ORIGIN,
    )
    item_id = created.json()["id"]

    completed = client().post(f"/actions/follow-ups/{item_id}/complete", headers=ORIGIN)
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "completed"

    # Idempotent — completing an already-completed follow-up is still 200.
    again = client().post(f"/actions/follow-ups/{item_id}/complete", headers=ORIGIN)
    assert again.status_code == 200

    dismiss_after_complete = client().post(
        f"/actions/follow-ups/{item_id}/dismiss", json={"reason": "too late"}, headers=ORIGIN
    )
    assert dismiss_after_complete.status_code == 409

    reopened = client().post(f"/actions/follow-ups/{item_id}/reopen", headers=ORIGIN)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["state"] == "open"
    assert reopened.json()["resolvedAt"] is None

    dismissed = client().post(
        f"/actions/follow-ups/{item_id}/dismiss",
        json={"reason": "no longer needed"},
        headers=ORIGIN,
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["state"] == "dismissed"

    same_reason = client().post(
        f"/actions/follow-ups/{item_id}/dismiss",
        json={"reason": "no longer needed"},
        headers=ORIGIN,
    )
    assert same_reason.status_code == 200

    different_reason = client().post(
        f"/actions/follow-ups/{item_id}/dismiss",
        json={"reason": "a different reason"},
        headers=ORIGIN,
    )
    assert different_reason.status_code == 409

    complete_after_dismiss = client().post(
        f"/actions/follow-ups/{item_id}/complete", headers=ORIGIN
    )
    assert complete_after_dismiss.status_code == 409

    reopened_again = client().post(f"/actions/follow-ups/{item_id}/reopen", headers=ORIGIN)
    assert reopened_again.status_code == 200
    assert reopened_again.json()["state"] == "open"

    result = _actions(live)
    resolved_ids = {row["id"] for row in result["resolvedFollowUps"]}
    assert item_id not in resolved_ids  # reopened, so no longer among the resolved ones


def test_follow_up_appears_as_an_item_and_then_among_resolved(live: ActionsFixture) -> None:
    created = client().post(
        "/actions/follow-ups",
        json={"title": "Synthetic follow-up", "dueDate": _d(5), "idempotencyKey": str(uuid4())},
        headers=ORIGIN,
    )
    item_id = created.json()["id"]
    open_result = _actions(live)
    assert f"follow-up.{item_id}" in _keys(open_result["items"])

    completed = client().post(f"/actions/follow-ups/{item_id}/complete", headers=ORIGIN)
    assert completed.status_code == 200

    resolved_result = _actions(live)
    assert f"follow-up.{item_id}" not in _keys(resolved_result["items"])
    assert item_id in {row["id"] for row in resolved_result["resolvedFollowUps"]}


def test_follow_up_foreign_and_unknown_ids_are_404(live: ActionsFixture) -> None:
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign = client().post(
        "/actions/follow-ups",
        json={"title": "Synthetic other household follow-up", "idempotencyKey": str(uuid4())},
        headers=ORIGIN,
    )
    assert foreign.status_code == 201, foreign.text
    foreign_id = foreign.json()["id"]
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    assert (
        client().post(f"/actions/follow-ups/{foreign_id}/complete", headers=ORIGIN).status_code
        == 404
    )
    assert (
        client()
        .post(f"/actions/follow-ups/{foreign_id}/dismiss", json={"reason": "x"}, headers=ORIGIN)
        .status_code
        == 404
    )
    assert (
        client().post(f"/actions/follow-ups/{foreign_id}/reopen", headers=ORIGIN).status_code == 404
    )

    unknown_id = uuid4()
    assert (
        client().post(f"/actions/follow-ups/{unknown_id}/complete", headers=ORIGIN).status_code
        == 404
    )


# --- GET /actions writes nothing. -----------------------------------------------------------------


def test_get_actions_changes_no_row(live: ActionsFixture) -> None:
    _commitment(live, _d(5))
    _card_statement(live, live.card_account_id, due_date=_d(5), statement_date=_d(-14))
    client().post(
        "/actions/follow-ups",
        json={"title": "Synthetic follow-up", "idempotencyKey": str(uuid4())},
        headers=ORIGIN,
    )
    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_item")
        before_items = cur.fetchone()
        cur.execute("SELECT count(*) FROM action_mark")
        before_marks = cur.fetchone()
    assert before_items is not None and before_marks is not None

    _actions(live)
    _actions(live)

    with live.connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM action_item")
        after_items = cur.fetchone()
        cur.execute("SELECT count(*) FROM action_mark")
        after_marks = cur.fetchone()
    assert after_items == before_items
    assert after_marks == before_marks
