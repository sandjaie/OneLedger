"""Live owner-scoped tests for the commitment resolution routes (Phase 3.1 Task 1): settling an
occurrence by linking a posting, marking it done, dismissing it, and undoing the latest
resolution. Skipped when no database is reachable. Every name, label and amount is synthetic.
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


@dataclass(frozen=True)
class ResolutionFixture:
    connection: psycopg.Connection
    owner: auth.OwnerSession
    other_owner: auth.OwnerSession
    person_id: UUID
    account_id: UUID
    other_account_id: UUID
    currency: str


def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def live() -> Iterator[ResolutionFixture]:
    connection = connect_to_test_database()
    household_id, other_household_id = uuid4(), uuid4()
    person_id, other_person_id = uuid4(), uuid4()
    user_id, other_user_id = uuid4(), uuid4()
    owner = auth.OwnerSession(user_id, household_id, uuid4(), "resolutions-owner@example.invalid")
    other_owner = auth.OwnerSession(
        other_user_id, other_household_id, uuid4(), "other-resolutions-owner@example.invalid"
    )
    with connection.cursor() as cur:
        cur.execute("SELECT min(code), max(code) FROM currency")
        found = cur.fetchone()
        assert found is not None
        base = str(found[0]).strip()
        secondary = str(found[1]).strip()
        # `post_ledger_transaction` freezes a secondary-currency translation even for a
        # base-currency posting (money.py `_frozen`), so every booked date these tests use needs
        # a stored secondary/base rate — a wide daily range covers every date any test touches.
        cur.execute(
            """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
               SELECT d::date, %s, %s, 1.5000000000, 'manual'
                 FROM generate_series('2019-01-01'::date, '2027-12-31'::date, '1 day') AS d
               ON CONFLICT (rate_date, base_currency, quote_currency) DO NOTHING""",
            (secondary, base),
        )
        for household, name in (
            (household_id, "resolutions household"),
            (other_household_id, "other resolutions household"),
        ):
            cur.execute(
                "INSERT INTO household (id, name, base_currency, secondary_currency) "
                "VALUES (%s, %s, %s, (SELECT max(code) FROM currency))",
                (household, name, base),
            )
        for person, household, name in (
            (person_id, household_id, "resolutions owner"),
            (other_person_id, other_household_id, "other resolutions owner"),
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
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                     country, opening_balance, opening_balance_date)
               VALUES (%s, %s, 'Synthetic resolutions account', 'savings', %s, 'XZ',
                       10000, '2020-01-01') RETURNING id""",
            (household_id, person_id, base),
        )
        account_row = cur.fetchone()
        assert account_row is not None
        account_id = UUID(str(account_row[0]))
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                     country, opening_balance, opening_balance_date)
               VALUES (%s, %s, 'Other synthetic resolutions account', 'savings', %s, 'XZ',
                       10000, '2020-01-01') RETURNING id""",
            (other_household_id, other_person_id, base),
        )
        other_row = cur.fetchone()
        assert other_row is not None
        other_account_id = UUID(str(other_row[0]))
    connection.commit()
    app.dependency_overrides[auth.require_owner] = lambda: owner
    try:
        yield ResolutionFixture(
            connection=connection,
            owner=owner,
            other_owner=other_owner,
            person_id=person_id,
            account_id=account_id,
            other_account_id=other_account_id,
            currency=base,
        )
    finally:
        app.dependency_overrides.clear()
        with connection.cursor() as cur:
            cur.execute("TRUNCATE commitment, transaction CASCADE")
        connection.commit()
        connection.close()


def _commitment(fixture: ResolutionFixture, **overrides: object) -> dict[str, Any]:
    body: dict[str, object] = {
        "accountId": str(fixture.account_id),
        "label": "Synthetic rent",
        "amount": "-1000.0000",
        "currency": fixture.currency,
        "cadence": "monthly",
        "firstDate": "2026-01-01",
        "endDate": None,
        "nextDueDate": "2026-02-01",
        "idempotencyKey": str(uuid4()),
    }
    body.update(overrides)
    response = client().post("/commitments", json=body, headers=ORIGIN)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _txn(
    fixture: ResolutionFixture,
    amount: str,
    booked_date: str,
    txn_type: str = "expense",
    account_id: UUID | None = None,
) -> str:
    response = client().post(
        "/transactions",
        json={
            "accountId": str(account_id or fixture.account_id),
            "bookedDate": booked_date,
            "description": "Synthetic posting",
            "amount": amount,
            "txnType": txn_type,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _raw_txn(
    fixture: ResolutionFixture,
    amount: str,
    booked_date: str,
    *,
    txn_type: str = "expense",
    account_id: UUID | None = None,
    is_excluded: bool = False,
) -> UUID:
    """A posting the manual-entry route refuses to create directly (excluded, opening_balance,
    adjustment) — inserted straight into the ledger, the same pattern test_plans_live.py uses for
    states only reachable by direct manipulation."""
    with fixture.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                       amount, currency, fx_rate_date, fx_rate_to_base, fx_rate_to_secondary,
                       amount_base, amount_secondary, txn_type, source, dedupe_hash, is_excluded)
               VALUES (%s, %s, %s, 'SYNTHETIC RAW POSTING', %s, %s, %s, 1, 1, %s, %s, %s,
                       'manual', %s, %s)
               RETURNING id""",
            (
                fixture.owner.household_id,
                account_id or fixture.account_id,
                booked_date,
                Decimal(amount),
                fixture.currency,
                booked_date,
                Decimal(amount),
                Decimal(amount),
                txn_type,
                f"raw-{uuid4()}",
                is_excluded,
            ),
        )
        row = cur.fetchone()
        assert row is not None
    fixture.connection.commit()
    return UUID(str(row[0]))


def _resolution_count(fixture: ResolutionFixture) -> int:
    with fixture.connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM commitment_resolution WHERE household_id = %s",
            (fixture.owner.household_id,),
        )
        found = cur.fetchone()
        assert found is not None
        return int(found[0])


def test_full_payment_advances_next_due_date(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    txn_id = _txn(live, "-1000.0000", "2026-02-01")
    response = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn_id, "closes": True},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["nextDueDate"] == "2026-03-01"
    assert row["status"] == "upcoming" or row["status"] == "due" or row["status"] == "overdue"
    assert row["paidTowardNext"] == "0.0000"
    assert row["partlyPaid"] is False


def test_part_payment_then_closing_payment_exact_amounts(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    txn1 = _txn(live, "-400.0000", "2026-02-01")
    part = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn1, "closes": False},
        headers=ORIGIN,
    )
    assert part.status_code == 201, part.text
    part_row = part.json()
    assert part_row["partlyPaid"] is True
    assert part_row["paidTowardNext"] == "-400.0000"
    assert part_row["remainingNext"] == "-600.0000"
    assert part_row["nextDueDate"] == "2026-02-01"

    txn2 = _txn(live, "-600.0000", "2026-02-05")
    closing = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn2, "closes": True},
        headers=ORIGIN,
    )
    assert closing.status_code == 201, closing.text
    closing_row = closing.json()
    assert closing_row["nextDueDate"] == "2026-03-01"
    assert closing_row["paidTowardNext"] == "0.0000"
    assert closing_row["partlyPaid"] is False
    assert closing_row["remainingNext"] == "-1000.0000"


def test_completed_and_dismissed_close(live: ResolutionFixture) -> None:
    completed_commitment = _commitment(live, idempotencyKey=str(uuid4()))
    completed = client().post(
        f"/commitments/{completed_commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert completed.status_code == 201, completed.text
    assert completed.json()["nextDueDate"] == "2026-03-01"

    dismissed_commitment = _commitment(live, idempotencyKey=str(uuid4()))
    dismissed = client().post(
        f"/commitments/{dismissed_commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "dismissed", "reason": "not needed this month"},
        headers=ORIGIN,
    )
    assert dismissed.status_code == 201, dismissed.text
    assert dismissed.json()["nextDueDate"] == "2026-03-01"


def test_once_commitment_finishes(live: ResolutionFixture) -> None:
    commitment = _commitment(
        live, cadence="once", firstDate="2026-02-01", nextDueDate="2026-02-01", endDate=None
    )
    finished = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert finished.status_code == 201, finished.text
    row = finished.json()
    assert row["nextDueDate"] is None
    assert row["status"] == "finished"
    assert row["remainingNext"] is None

    still_finished = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-03-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert still_finished.status_code == 409
    assert "no open occurrence" in still_finished.json()["detail"]


def test_replays_return_200(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    txn_id = _txn(live, "-1000.0000", "2026-02-01")
    body = {"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn_id, "closes": True}
    first = client().post(f"/commitments/{commitment['id']}/resolutions", json=body, headers=ORIGIN)
    assert first.status_code == 201
    replay = client().post(
        f"/commitments/{commitment['id']}/resolutions", json=body, headers=ORIGIN
    )
    assert replay.status_code == 200
    assert replay.json()["nextDueDate"] == first.json()["nextDueDate"]

    dismiss_commitment = _commitment(live, idempotencyKey=str(uuid4()))
    dismiss_body = {"dueDate": "2026-02-01", "kind": "dismissed", "reason": "same reason"}
    first_dismiss = client().post(
        f"/commitments/{dismiss_commitment['id']}/resolutions", json=dismiss_body, headers=ORIGIN
    )
    assert first_dismiss.status_code == 201
    replay_dismiss = client().post(
        f"/commitments/{dismiss_commitment['id']}/resolutions", json=dismiss_body, headers=ORIGIN
    )
    assert replay_dismiss.status_code == 200


def test_stale_due_date_archived_finished_already_resolved(live: ResolutionFixture) -> None:
    commitment = _commitment(live)

    stale = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-01-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert stale.status_code == 409
    assert "next open occurrence" in stale.json()["detail"]

    archived_commitment = _commitment(live, idempotencyKey=str(uuid4()))
    client().post(f"/commitments/{archived_commitment['id']}/archive", headers=ORIGIN)
    archived = client().post(
        f"/commitments/{archived_commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert archived.status_code == 409
    assert "archived commitment is fixed" in archived.json()["detail"]

    closed = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert closed.status_code == 201
    already = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "dismissed", "reason": "different"},
        headers=ORIGIN,
    )
    assert already.status_code == 409
    assert "already resolved" in already.json()["detail"]


def test_reused_posting_is_409(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    txn_id = _txn(live, "-1000.0000", "2026-02-01")
    first = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn_id, "closes": False},
        headers=ORIGIN,
    )
    assert first.status_code == 201
    reused = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn_id, "closes": True},
        headers=ORIGIN,
    )
    assert reused.status_code == 409
    assert "already settles a commitment" in reused.json()["detail"]


def test_ineligible_postings_are_422_with_no_row(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    before = _resolution_count(live)

    with live.connection.cursor() as cur:
        cur.execute(
            """INSERT INTO account (household_id, owner_person_id, name, account_type, currency,
                                     country, opening_balance, opening_balance_date)
               VALUES (%s, %s, 'Synthetic second resolutions account', 'savings', %s, 'XZ',
                       0, '2020-01-01') RETURNING id""",
            (live.owner.household_id, live.person_id, live.currency),
        )
        second_account_row = cur.fetchone()
        assert second_account_row is not None
        second_account_id = UUID(str(second_account_row[0]))
    live.connection.commit()

    wrong_sign = _txn(live, "1000.0000", "2026-02-01", txn_type="income")
    wrong_account = _txn(live, "-1000.0000", "2026-02-01", account_id=second_account_id)
    excluded_txn = _raw_txn(live, "-1000.0000", "2026-02-01", is_excluded=True)
    opening_txn = _raw_txn(live, "-1000.0000", "2026-02-01", txn_type="opening_balance")

    for txn_id in (str(wrong_sign), str(wrong_account), str(excluded_txn), str(opening_txn)):
        response = client().post(
            f"/commitments/{commitment['id']}/resolutions",
            json={
                "dueDate": "2026-02-01",
                "kind": "payment",
                "transactionId": txn_id,
                "closes": False,
            },
            headers=ORIGIN,
        )
        assert response.status_code == 422, response.text
        assert "cannot settle this commitment" in response.json()["detail"]

    # A currency mismatch, isolated from account by pointing the commitment's own currency at
    # the secondary code directly (the create-time route already refuses this combination; the
    # V48 trigger is exercised on its own here, matching db/tests/0045's approach).
    with live.connection.cursor() as cur:
        cur.execute("SELECT max(code) FROM currency WHERE code != %s", (live.currency,))
        secondary_row = cur.fetchone()
        assert secondary_row is not None
        secondary = str(secondary_row[0]).strip()
        cur.execute(
            "UPDATE commitment SET currency = %s WHERE household_id = %s AND id = %s",
            (secondary, live.owner.household_id, commitment["id"]),
        )
    live.connection.commit()
    currency_txn = _txn(live, "-1000.0000", "2026-02-05")
    currency_mismatch = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={
            "dueDate": "2026-02-01",
            "kind": "payment",
            "transactionId": currency_txn,
            "closes": False,
        },
        headers=ORIGIN,
    )
    assert currency_mismatch.status_code == 422, currency_mismatch.text
    assert "cannot settle this commitment" in currency_mismatch.json()["detail"]

    assert _resolution_count(live) == before


def test_undo_part_payment(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    txn_id = _txn(live, "-400.0000", "2026-02-01")
    created = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn_id, "closes": False},
        headers=ORIGIN,
    )
    assert created.status_code == 201
    resolutions = client().get(f"/commitments/{commitment['id']}/resolutions", headers=ORIGIN)
    assert resolutions.status_code == 200
    row = resolutions.json()["resolutions"][0]
    assert row["undoable"] is True
    undo = client().delete(
        f"/commitments/{commitment['id']}/resolutions/{row['id']}", headers=ORIGIN
    )
    assert undo.status_code == 200, undo.text
    undone = undo.json()
    assert undone["paidTowardNext"] == "0.0000"
    assert undone["partlyPaid"] is False


def test_undo_latest_closing_row_and_refuse_an_older_one(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    first_close = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert first_close.status_code == 201
    first_resolution_id = (
        client()
        .get(f"/commitments/{commitment['id']}/resolutions", headers=ORIGIN)
        .json()["resolutions"][0]["id"]
    )

    second_close = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-03-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert second_close.status_code == 201

    listed = client().get(f"/commitments/{commitment['id']}/resolutions", headers=ORIGIN).json()
    by_id = {r["id"]: r for r in listed["resolutions"]}
    assert by_id[first_resolution_id]["undoable"] is False

    refused = client().delete(
        f"/commitments/{commitment['id']}/resolutions/{first_resolution_id}", headers=ORIGIN
    )
    assert refused.status_code == 409
    assert "only the latest resolution" in refused.json()["detail"]

    second_resolution_id = next(
        r["id"] for r in listed["resolutions"] if r["id"] != first_resolution_id
    )
    assert by_id[second_resolution_id]["undoable"] is True
    undone = client().delete(
        f"/commitments/{commitment['id']}/resolutions/{second_resolution_id}", headers=ORIGIN
    )
    assert undone.status_code == 200
    assert undone.json()["nextDueDate"] == "2026-03-01"


def test_undo_closing_row_refused_while_next_occurrence_has_a_payment(
    live: ResolutionFixture,
) -> None:
    commitment = _commitment(live)
    closed = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "completed"},
        headers=ORIGIN,
    )
    assert closed.status_code == 201
    closing_id = (
        client()
        .get(f"/commitments/{commitment['id']}/resolutions", headers=ORIGIN)
        .json()["resolutions"][0]["id"]
    )

    txn_id = _txn(live, "-200.0000", "2026-03-05")
    client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-03-01", "kind": "payment", "transactionId": txn_id, "closes": False},
        headers=ORIGIN,
    )

    listed = client().get(f"/commitments/{commitment['id']}/resolutions", headers=ORIGIN).json()
    closing_row = next(r for r in listed["resolutions"] if r["id"] == closing_id)
    assert closing_row["undoable"] is False

    refused = client().delete(
        f"/commitments/{commitment['id']}/resolutions/{closing_id}", headers=ORIGIN
    )
    assert refused.status_code == 409
    assert "remove the payments" in refused.json()["detail"]


def test_overpayment_leaves_remaining_next_zero(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    txn_id = _txn(live, "-1500.0000", "2026-02-01")
    response = client().post(
        f"/commitments/{commitment['id']}/resolutions",
        json={"dueDate": "2026-02-01", "kind": "payment", "transactionId": txn_id, "closes": False},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["remainingNext"] == "0.0000"
    assert row["partlyPaid"] is True


def test_candidates_window_edges_and_read_only(live: ResolutionFixture) -> None:
    commitment = _commitment(live)
    due = date(2026, 2, 1)
    inside_before = _txn(live, "-1000.0000", str(due - timedelta(days=45)))
    outside_before = _txn(live, "-1000.0000", str(due - timedelta(days=46)))
    inside_after = _txn(live, "-1000.0000", str(due + timedelta(days=45)))
    outside_after = _txn(live, "-1000.0000", str(due + timedelta(days=46)))

    before_count = _resolution_count(live)
    response = client().get(f"/commitments/{commitment['id']}/payment-candidates", headers=ORIGIN)
    assert response.status_code == 200
    body = response.json()
    assert body["dueDate"] == "2026-02-01"
    ids = {c["transactionId"] for c in body["candidates"]}
    assert inside_before in ids
    assert inside_after in ids
    assert outside_before not in ids
    assert outside_after not in ids
    assert _resolution_count(live) == before_count

    empty_commitment = _commitment(live, idempotencyKey=str(uuid4()))
    client().post(f"/commitments/{empty_commitment['id']}/archive", headers=ORIGIN)
    cancelled = client().get(
        f"/commitments/{empty_commitment['id']}/payment-candidates", headers=ORIGIN
    )
    assert cancelled.status_code == 200
    assert cancelled.json() == {"dueDate": None, "candidates": []}


def test_foreign_household_is_404_on_every_route(live: ResolutionFixture) -> None:
    own = _commitment(live)
    own_payment = _txn(live, "-100.0000", "2026-02-01")
    assert (
        client()
        .post(
            f"/commitments/{own['id']}/resolutions",
            json={
                "dueDate": "2026-02-01",
                "kind": "payment",
                "transactionId": own_payment,
                "closes": False,
            },
            headers=ORIGIN,
        )
        .status_code
        == 201
    )

    # Real rows in the other household: a commitment, an eligible posting, a part payment and a
    # second eligible posting left unlinked, so every route has something it must not reveal.
    app.dependency_overrides[auth.require_owner] = lambda: live.other_owner
    foreign = _commitment(live, accountId=str(live.other_account_id))
    foreign_paid = _txn(live, "-100.0000", "2026-02-01", account_id=live.other_account_id)
    _txn(live, "-200.0000", "2026-02-02", account_id=live.other_account_id)
    created = client().post(
        f"/commitments/{foreign['id']}/resolutions",
        json={
            "dueDate": "2026-02-01",
            "kind": "payment",
            "transactionId": foreign_paid,
            "closes": False,
        },
        headers=ORIGIN,
    )
    assert created.status_code == 201, created.text
    history = client().get(f"/commitments/{foreign['id']}/resolutions", headers=ORIGIN)
    foreign_resolution_id = history.json()["resolutions"][0]["id"]
    app.dependency_overrides[auth.require_owner] = lambda: live.owner

    def foreign_state() -> list[tuple[Any, ...]]:
        with live.connection.cursor() as cur:
            cur.execute(
                """SELECT c.next_due_date, c.label, r.id, r.kind::text, r.closes, r.transaction_id
                     FROM commitment c
                     LEFT JOIN commitment_resolution r ON r.commitment_id = c.id
                    WHERE c.household_id = %s ORDER BY r.id""",
                (live.other_owner.household_id,),
            )
            return cur.fetchall()

    before = foreign_state()
    base = f"/commitments/{foreign['id']}"
    assert client().get(f"{base}/resolutions", headers=ORIGIN).status_code == 404
    assert client().get(f"{base}/payment-candidates", headers=ORIGIN).status_code == 404
    for body in (
        {"dueDate": "2026-02-01", "kind": "completed"},
        {"dueDate": "2026-02-01", "kind": "payment", "transactionId": own_payment, "closes": True},
    ):
        assert client().post(f"{base}/resolutions", json=body, headers=ORIGIN).status_code == 404
    assert (
        client().delete(f"{base}/resolutions/{foreign_resolution_id}", headers=ORIGIN).status_code
        == 404
    )
    # The owner's own commitment cannot reach the foreign resolution either.
    assert (
        client()
        .delete(f"/commitments/{own['id']}/resolutions/{foreign_resolution_id}", headers=ORIGIN)
        .status_code
        == 404
    )
    # Nor can the owner settle their own commitment with the other household's posting.
    refused = client().post(
        f"/commitments/{own['id']}/resolutions",
        json={
            "dueDate": "2026-02-01",
            "kind": "payment",
            "transactionId": foreign_paid,
            "closes": True,
        },
        headers=ORIGIN,
    )
    assert refused.status_code in (404, 422), refused.text
    # And the owner's own candidates never list a foreign posting.
    own_candidates = client().get(f"/commitments/{own['id']}/payment-candidates", headers=ORIGIN)
    assert own_candidates.status_code == 200
    assert all(c["transactionId"] != foreign_paid for c in own_candidates.json()["candidates"])
    assert foreign_state() == before
