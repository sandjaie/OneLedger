"""Live owner-scoped tests for the monthly review (Phase 3.2 Task 2): `GET /monthly-review` and
its completion routes. `routes/monthly_review.py` computes every step from existing tables and
views (`coverage_rows`, `transfers.candidates_for_window`, `wealth_history.month_totals`,
`actions.action_list`) plus the one fact `monthly_review` (V50) itself holds. Skipped when no
database is reachable. Every name, label and amount is synthetic.

Member 403 is covered once, for every route including these three, by
`test_health.test_every_current_non_bootstrap_route_depends_on_the_owner`.
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
from oneledger.coverage import month_end, previous_month
from oneledger.main import app

from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}
TODAY = date.today()


def _months_before(n: int) -> date:
    """The first day of the calendar month `n` months before today's — always safely in the
    past, whatever day of the month a test happens to run on."""
    total = TODAY.year * 12 + (TODAY.month - 1) - n
    return date(total // 12, total % 12 + 1, 1)


# Two months before today's, so it is never the default (last month) or a future month, and
# never collides with those explicit-boundary tests.
REVIEW_MONTH = _months_before(2)
REVIEW_END = month_end(REVIEW_MONTH)
PREVIOUS_MONTH = _months_before(3)
PREVIOUS_END = month_end(PREVIOUS_MONTH)


@dataclass(frozen=True)
class ReviewFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    household_id: UUID
    other_household_id: UUID
    account_id: UUID
    debit_account_id: UUID
    credit_account_id: UUID
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
    account_type: str = "savings",
) -> UUID:
    cur.execute(
        """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                 country, opening_balance, opening_balance_date)
           VALUES (%s, %s, %s, %s, %s, 'XZ', 0, '2020-01-01') RETURNING id""",
        (household_id, person_id, name, account_type, currency),
    )
    found = cur.fetchone()
    assert found is not None
    return UUID(str(found[0]))


def _txn(
    connection: psycopg.Connection,
    household_id: UUID,
    account_id: UUID,
    amount: str,
    on: date,
    currency: str,
    txn_type: str,
    classified_by: str = "unclassified",
) -> UUID:
    value = Decimal(amount)
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                       description_clean, amount, currency, fx_rate_date, fx_rate_to_base,
                       fx_rate_to_secondary, amount_base, amount_secondary, txn_type,
                       classified_by, source, dedupe_hash)
               VALUES (%s, %s, %s, 'Synthetic review row', 'Synthetic review row', %s, %s, %s,
                       1, 1, %s, %s, %s::txn_type, %s::classified_by, 'manual', %s)
               RETURNING id""",
            (
                household_id,
                account_id,
                on,
                value,
                currency,
                on,
                value,
                value,
                txn_type,
                classified_by,
                uuid4().hex + uuid4().hex,
            ),
        )
        found = cur.fetchone()
        assert found is not None
    connection.commit()
    return UUID(str(found[0]))


def _month_end_position(
    connection: psycopg.Connection,
    household_id: UUID,
    account_id: UUID,
    month: date,
    currency: str,
    value: str,
    status: str = "available",
) -> None:
    native = Decimal(value) if status == "available" else None
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO month_end_position (household_id, month_end, account_id,
                       valuation_mode, currency, native_value, value_base, value_secondary, status)
               VALUES (%s, %s, %s, 'ledger_balance', %s, %s, %s, %s,
                       %s::month_end_position_status)""",
            (household_id, month, account_id, currency, native, native, native, status),
        )
    connection.commit()


@pytest.fixture
def live() -> Iterator[ReviewFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "review-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-review-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base = str(found[0]).strip()
        for household, name in (
            (household_id, "review household"),
            (other_household_id, "other review household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, (SELECT max(code) FROM currency))",
                (household, name, base),
            )
        for person, household, name in (
            (person_id, household_id, "review owner"),
            (other_person_id, other_household_id, "other review owner"),
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
        account_id = _account(cur, household_id, person_id, "Synthetic review account", base)
        # Not a statement-sourced type (coverage.STATEMENT_SOURCED_TYPES): the transfer-pair
        # accounts must not each add their own coverage gap for the review month.
        debit_account_id = _account(
            cur, household_id, person_id, "Synthetic review debit", base, "brokerage"
        )
        credit_account_id = _account(
            cur, household_id, person_id, "Synthetic review credit", base, "brokerage"
        )
        other_account_id = _account(
            cur, other_household_id, other_person_id, "Other synthetic review account", base
        )
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield ReviewFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            household_id=household_id,
            other_household_id=other_household_id,
            account_id=account_id,
            debit_account_id=debit_account_id,
            credit_account_id=credit_account_id,
            other_account_id=other_account_id,
            currency=base,
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute(
                "TRUNCATE commitment, action_item, action_mark, monthly_review, "
                "coverage_gap_acknowledgement, month_end_position, transaction CASCADE"
            )
        connection.commit()
        connection.close()


def _review(
    fixture: ReviewFixture, month: str | None = None, as_owner: bool = True
) -> dict[str, Any]:
    if not as_owner:
        app.dependency_overrides[auth.require_owner] = lambda: fixture.other_owner
    try:
        params = {} if month is None else {"month": month}
        response = client().get("/monthly-review", params=params, headers=ORIGIN)
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        return result
    finally:
        if not as_owner:
            app.dependency_overrides[auth.require_owner] = lambda: fixture.owner


def _commitment(account_id: UUID, currency: str, due: date) -> None:
    response = client().post(
        "/commitments",
        json={
            "accountId": str(account_id),
            "label": "Synthetic review commitment",
            "amount": "-100.0000",
            "currency": currency,
            "cadence": "once",
            "firstDate": due.isoformat(),
            "nextDueDate": due.isoformat(),
            "idempotencyKey": str(uuid4()),
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text


def _follow_up(title: str) -> dict[str, Any]:
    response = client().post(
        "/actions/follow-ups", json={"title": title, "idempotencyKey": str(uuid4())}, headers=ORIGIN
    )
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()
    return result


def _acknowledge_payslip_gap(fixture: ReviewFixture, month: date) -> None:
    response = client().put(
        "/coverage/acknowledgements",
        json={"subject": "payslips", "month": month.isoformat(), "reason": "not_issued"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


# --- The full picture: one gap, one transfer pair, one unclassified row, a known change. --------


def test_counts_for_a_synthetic_month_with_a_known_change(live: ReviewFixture) -> None:
    # One coverage gap: the payslip row's own gap is acknowledged away, leaving exactly the one
    # statement-sourced account with no import evidence for the review month.
    _acknowledge_payslip_gap(live, REVIEW_MONTH)

    # One transfer candidate pair: same amount, same day, different accounts — a "weak" band
    # score at worst, which the shown bands include.
    pair_date = REVIEW_MONTH + timedelta(days=10)
    _txn(
        live.connection,
        live.household_id,
        live.debit_account_id,
        "-500.0000",
        pair_date,
        live.currency,
        "expense",
        classified_by="user_manual",
    )
    _txn(
        live.connection,
        live.household_id,
        live.credit_account_id,
        "500.0000",
        pair_date,
        live.currency,
        "income",
        classified_by="user_manual",
    )

    # One unclassified reportable row, left at the column's own default.
    _txn(
        live.connection,
        live.household_id,
        live.account_id,
        "-42.0000",
        REVIEW_MONTH + timedelta(days=3),
        live.currency,
        "expense",
    )

    # Two complete stored month totals with a known difference.
    _month_end_position(
        live.connection,
        live.household_id,
        live.account_id,
        PREVIOUS_END,
        live.currency,
        "1000.0000",
    )
    _month_end_position(
        live.connection,
        live.household_id,
        live.account_id,
        REVIEW_END,
        live.currency,
        "1500.0000",
    )

    # Commitments on both window edges: today and today + 30 are due next 30 days, today + 31 is
    # neither, today - 5 is overdue. An open and a snoozed follow-up.
    for offset in (0, 10, 30, 31, -5):
        _commitment(live.account_id, live.currency, TODAY + timedelta(days=offset))

    open_followup = _follow_up("Synthetic open follow-up")
    snoozed_followup = _follow_up("Synthetic snoozed follow-up")
    snooze = client().put(
        f"/actions/marks/follow-up.{snoozed_followup['id']}",
        json={"until": (TODAY + timedelta(days=5)).isoformat()},
        headers=ORIGIN,
    )
    assert snooze.status_code == 204, snooze.text
    assert open_followup["id"] != snoozed_followup["id"]

    result = _review(live, REVIEW_MONTH.strftime("%Y-%m"))
    assert result["month"] == REVIEW_MONTH.isoformat()
    assert result["completedAt"] is None
    steps = result["steps"]
    assert steps["coverage"] == {"gapCells": 1, "rowsWithGaps": 1}
    assert steps["transfers"] == {"candidateCount": 1}
    assert steps["classification"] == {"unclassifiedCount": 1}
    position = steps["position"]
    assert position["monthEnd"] == REVIEW_END.isoformat()
    assert position["previousMonthEnd"] == PREVIOUS_END.isoformat()
    assert position["complete"] is True
    assert position["previousComplete"] is True
    assert position["totalBase"] == "1500.0000"
    assert position["previousTotalBase"] == "1000.0000"
    assert position["changeBase"] == "500.0000"
    assert position["baseCurrency"] == live.currency
    assert steps["commitments"] == {"dueNext30Days": 3, "overdue": 1}
    # GET /actions items at today: the overdue and today's commitment (inside its seven-day
    # window) and the open follow-up; the snoozed follow-up alone is snoozed.
    assert steps["actions"] == {"open": 3, "snoozed": 1}


def test_incomplete_month_gives_null_change(live: ReviewFixture) -> None:
    _month_end_position(
        live.connection,
        live.household_id,
        live.account_id,
        PREVIOUS_END,
        live.currency,
        "1000.0000",
    )
    _month_end_position(
        live.connection,
        live.household_id,
        live.account_id,
        REVIEW_END,
        live.currency,
        "0.0000",
        status="no_snapshot",
    )

    result = _review(live, REVIEW_MONTH.strftime("%Y-%m"))
    position = result["steps"]["position"]
    assert position["complete"] is False
    assert position["totalBase"] is None
    assert position["previousComplete"] is True
    assert position["previousTotalBase"] == "1000.0000"
    assert position["changeBase"] is None


def test_no_stored_totals_gives_null_change(live: ReviewFixture) -> None:
    result = _review(live, REVIEW_MONTH.strftime("%Y-%m"))
    position = result["steps"]["position"]
    assert position["complete"] is False
    assert position["totalBase"] is None
    assert position["previousComplete"] is False
    assert position["previousTotalBase"] is None
    assert position["changeBase"] is None


# --- Month resolution and refusal. ---------------------------------------------------------------


def test_default_month_is_the_calendar_month_before_today(live: ReviewFixture) -> None:
    default_month = date(TODAY.year, TODAY.month, 1) - timedelta(days=1)
    default_month = default_month.replace(day=1)
    result = _review(live)
    assert result["month"] == default_month.isoformat()


def test_bad_month_format_is_422(live: ReviewFixture) -> None:
    response = client().get("/monthly-review", params={"month": "2026/07"}, headers=ORIGIN)
    assert response.status_code == 422, response.text


def test_future_month_is_422_on_get_and_completion(live: ReviewFixture) -> None:
    next_month = date(TODAY.year, TODAY.month, 1) + timedelta(days=32)
    future = next_month.replace(day=1).strftime("%Y-%m")
    get_response = client().get("/monthly-review", params={"month": future}, headers=ORIGIN)
    assert get_response.status_code == 422, get_response.text
    put_response = client().put(f"/monthly-review/{future}/completion", headers=ORIGIN)
    assert put_response.status_code == 422, put_response.text


_WRITTEN_TABLES = (
    "monthly_review",
    "commitment",
    "action_item",
    "action_mark",
    "transaction",
    "month_end_position",
    "coverage_gap_acknowledgement",
)


def _household_state(fixture: ReviewFixture) -> dict[str, Any]:
    with fixture.connection.cursor() as cur:
        state: dict[str, Any] = {}
        for table in _WRITTEN_TABLES:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE household_id = %s", (fixture.household_id,)
            )
            found = cur.fetchone()
            assert found is not None
            state[table] = found[0]
        cur.execute(
            "SELECT review_month, completed_at FROM monthly_review WHERE household_id = %s "
            "ORDER BY review_month",
            (fixture.household_id,),
        )
        state["completions"] = cur.fetchall()
    fixture.connection.commit()
    return state


def test_get_changes_no_row(live: ReviewFixture) -> None:
    month = REVIEW_MONTH.strftime("%Y-%m")
    completed = client().put(f"/monthly-review/{month}/completion", headers=ORIGIN)
    assert completed.status_code == 201, completed.text
    _txn(
        live.connection,
        live.household_id,
        live.account_id,
        "-42.0000",
        REVIEW_MONTH + timedelta(days=3),
        live.currency,
        "expense",
    )
    _commitment(live.account_id, live.currency, TODAY)
    _follow_up("Synthetic unchanged follow-up")
    before = _household_state(live)

    _review(live, month)
    _review(live)

    assert _household_state(live) == before
    assert before["monthly_review"] == 1


# --- Completion: idempotent 201/200, and reopen. -------------------------------------------------


def test_completion_is_201_then_200_with_the_same_completed_at(live: ReviewFixture) -> None:
    month = REVIEW_MONTH.strftime("%Y-%m")
    first = client().put(f"/monthly-review/{month}/completion", headers=ORIGIN)
    assert first.status_code == 201, first.text
    completed_at = first.json()["completedAt"]
    assert completed_at is not None

    second = client().put(f"/monthly-review/{month}/completion", headers=ORIGIN)
    assert second.status_code == 200, second.text
    assert second.json()["completedAt"] == completed_at
    assert second.json()["month"] == REVIEW_MONTH.isoformat()

    reread = _review(live, month)
    assert reread["completedAt"] == completed_at


def test_reopen_is_204_and_idempotent(live: ReviewFixture) -> None:
    month = REVIEW_MONTH.strftime("%Y-%m")
    client().put(f"/monthly-review/{month}/completion", headers=ORIGIN)

    first = client().delete(f"/monthly-review/{month}/completion", headers=ORIGIN)
    assert first.status_code == 204, first.text
    second = client().delete(f"/monthly-review/{month}/completion", headers=ORIGIN)
    assert second.status_code == 204, second.text

    reread = _review(live, month)
    assert reread["completedAt"] is None


# --- Household isolation, with real foreign rows. -------------------------------------------------


def test_foreign_household_data_does_not_leak_into_the_count(live: ReviewFixture) -> None:
    # Every step's household filter, against real rows in the other household: a transfer pair,
    # an unclassified row, two complete month totals, a due and an overdue commitment and an open
    # follow-up. The API role bypasses RLS, so only the explicit filters keep these out.
    with live.connection.cursor() as cur:
        cur.execute("SELECT owner_person_id FROM account WHERE id = %s", (live.other_account_id,))
        found = cur.fetchone()
        assert found is not None
        other_credit_id = _account(
            cur,
            live.other_household_id,
            found[0],
            "Other synthetic credit",
            live.currency,
            "brokerage",
        )
    live.connection.commit()
    pair_date = REVIEW_MONTH + timedelta(days=10)
    for account_id, amount, txn_type in (
        (live.other_account_id, "-500.0000", "expense"),
        (other_credit_id, "500.0000", "income"),
    ):
        _txn(
            live.connection,
            live.other_household_id,
            account_id,
            amount,
            pair_date,
            live.currency,
            txn_type,
        )
    for month_end_value, value in ((PREVIOUS_END, "1000.0000"), (REVIEW_END, "1500.0000")):
        _month_end_position(
            live.connection,
            live.other_household_id,
            live.other_account_id,
            month_end_value,
            live.currency,
            value,
        )
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        for offset in (0, -5):
            _commitment(live.other_account_id, live.currency, TODAY + timedelta(days=offset))
        _follow_up("Other synthetic follow-up")
        other = _review(live, REVIEW_MONTH.strftime("%Y-%m"))
        # The foreign rows are real: the other household's own review counts them.
        assert other["steps"]["transfers"] == {"candidateCount": 1}
        assert other["steps"]["classification"] == {"unclassifiedCount": 2}
        assert other["steps"]["position"]["changeBase"] == "500.0000"
        assert other["steps"]["commitments"] == {"dueNext30Days": 1, "overdue": 1}
        assert other["steps"]["actions"] == {"open": 3, "snoozed": 0}
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live.owner

    steps = _review(live, REVIEW_MONTH.strftime("%Y-%m"))["steps"]
    assert steps["transfers"] == {"candidateCount": 0}
    assert steps["classification"] == {"unclassifiedCount": 0}
    assert steps["position"]["complete"] is False
    assert steps["position"]["previousComplete"] is False
    assert steps["position"]["totalBase"] is None
    assert steps["position"]["changeBase"] is None
    assert steps["commitments"] == {"dueNext30Days": 0, "overdue": 0}
    assert steps["actions"] == {"open": 0, "snoozed": 0}


def test_foreign_household_completion_is_independent(live: ReviewFixture) -> None:
    month = REVIEW_MONTH.strftime("%Y-%m")
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        other_complete = client().put(f"/monthly-review/{month}/completion", headers=ORIGIN)
        assert other_complete.status_code == 201, other_complete.text
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live.owner

    mine = _review(live, month)
    assert mine["completedAt"] is None

    # The other household completing last month leaves this household's Overview status open.
    last_month = previous_month(TODAY).strftime("%Y-%m")
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    try:
        other_last = client().put(f"/monthly-review/{last_month}/completion", headers=ORIGIN)
        assert other_last.status_code == 201, other_last.text
    finally:
        app.dependency_overrides[auth.require_owner] = lambda: live.owner
    actions = client().get("/actions", headers=ORIGIN)
    assert actions.status_code == 200, actions.text
    assert actions.json()["previousMonthReview"]["completedAt"] is None
