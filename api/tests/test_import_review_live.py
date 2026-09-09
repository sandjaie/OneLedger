"""The review path end to end: read a staged batch, decide its rows, commit it.

`POST /parse` stages; these three routes are what turns a staged batch into ledger rows.
`GET /import/{batch}` is the review screen's payload, `PATCH /import/{batch}/rows` records the
decisions, and `POST /import/{batch}/commit` calls V14's function — the one place where the
insert and the balance verification succeed or fail together.

The batch under test is produced by driving a real PDF through `POST /parse`, not by inserting
rows by hand: a fixture that staged its own rows would prove the review routes work against a
shape the pipeline does not actually produce.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .test_ingest_route_live import ORIGIN, SALT, Fixture, _statement


def _connect() -> psycopg.Connection:
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("no database credentials in the environment")
    from oneledger.db import connect

    try:
        return connect()
    except psycopg.OperationalError:
        pytest.skip("database unreachable")


class Staged(Fixture):
    """The route fixture, plus one batch actually staged through `POST /parse`.

    It also needs a real `app_user` row. The parse routes never call a SECURITY DEFINER
    function, so a synthetic session id was enough for them; the commit route does, and V14
    resolves the household through `current_household_id()`, which reads `app_user`. A session
    whose user does not exist there is nobody, and the commit is refused — correctly.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        super().__init__(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM person WHERE household_id = %s LIMIT 1", (self.household_id,)
            )
            person = cur.fetchone()[0]  # type: ignore[index]
            # The statement declares an opening of 1,000 and a close of 1,490. The parse
            # fixture's account opens at zero, which is fine for staging and wrong here: V14
            # verifies the ledger against the statement's own closing figure, so the account
            # has to start where the statement says the month started.
            cur.execute(
                "UPDATE account SET opening_balance = 1000 WHERE id = %s", (self.account_id,)
            )
            self.user_id = uuid4()
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (self.user_id, self.household_id, person, f"review-{self.user_id}@example.invalid"),
            )
        conn.commit()

    def owner(self) -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=self.user_id,
            household_id=self.household_id,
            session_id=UUID(int=3),
            email="o@e.com",
        )

    def stage(self) -> str:
        response = self.post(_statement(), stage="true")
        assert response.status_code == 200, response.text
        batch: str = response.json()["batchId"]
        return batch

    def get(self, batch: str) -> httpx.Response:
        response: httpx.Response = TestClient(app).get(f"/import/{batch}", headers=ORIGIN)
        return response

    def patch(self, batch: str, body: dict[str, Any]) -> httpx.Response:
        response: httpx.Response = TestClient(app).patch(
            f"/import/{batch}/rows", json=body, headers=ORIGIN
        )
        return response

    def commit(self, batch: str) -> httpx.Response:
        response: httpx.Response = TestClient(app).post(f"/import/{batch}/commit", headers=ORIGIN)
        return response

    def ledger_rows(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM transaction")
            return int(cur.fetchone()[0])  # type: ignore[index]


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Staged]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        # Constructed inside the try: `Fixture.__init__` can `pytest.skip`, and a skip that
        # escaped this block would leave the connection open with locks held, blocking every
        # test after it rather than skipping with it.
        fixture = Staged(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute("DELETE FROM statement_layout")
            cur.execute("DELETE FROM account WHERE name = 'route test'")
            cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
            cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
            cur.execute("DELETE FROM household WHERE name = 'other'")
        conn.commit()
        conn.close()


# --------------------------------------------------------------------------------------
# GET /import/{batch} — the review screen's payload
# --------------------------------------------------------------------------------------


def test_the_batch_comes_back_with_its_verdict_and_its_rows_grouped(live: Staged) -> None:
    """`ui-information-architecture.html` §3: grouped by attention needed, not chronologically.

    Chronological order means scrolling past the rows that are fine to reach the ones that are
    not. In M2 the groups the pipeline can produce are `new`, `duplicate` and `near_duplicate`;
    `needs_review` arrives with M4's classifier.
    """
    batch = live.stage()

    response = live.get(batch)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["batchId"] == batch
    assert body["accountId"] == str(live.account_id)
    assert body["status"] == "awaiting_review"
    assert body["reconciliation"]["status"] == "matched"
    assert body["reconciliation"]["declaredClosing"] == "1490.0000"
    assert body["period"] == {"start": "2026-03-01", "end": "2026-03-02", "isDerived": True}
    assert body["rowsExtracted"] == 2

    groups = {group["status"]: group for group in body["groups"]}
    assert set(groups) == {"new"}, "both rows are clean, so there is one group"
    assert groups["new"]["count"] == 2
    assert [row["sourceRowNumber"] for row in groups["new"]["rows"]] == [1, 2]
    assert [row["amount"] for row in groups["new"]["rows"]] == ["-10.0000", "500.0000"]


def test_every_row_carries_what_the_reviewer_needs_to_decide_it(live: Staged) -> None:
    """M2's traceability gate: the reviewer sees the page and row a line came from, and the
    statement's own words for it. Money is a string; the frozen pair travels with the row."""
    batch = live.stage()

    row = live.get(batch).json()["groups"][0]["rows"][0]

    assert row["sourcePageNumber"] == 1 and row["sourceRowNumber"] == 1
    assert row["bookedDate"] == "2026-03-01"
    assert row["descriptionRaw"].startswith("COFFEE SHOP")
    assert row["amount"] == "-10.0000" and isinstance(row["amount"], str)
    assert row["amountBase"] == "-10.0000"
    assert row["proposedTxnType"] == "expense"
    assert row["status"] == "new"
    assert UUID(row["id"])


def test_a_batch_belonging_to_another_household_is_not_readable(live: Staged) -> None:
    batch = live.stage()
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO household (name, base_currency, secondary_currency)
               VALUES ('other', %s, %s) RETURNING id""",
            (live.base, live.secondary),
        )
        other = cur.fetchone()[0]  # type: ignore[index]
        cur.execute("UPDATE import_batch SET household_id = %s WHERE id = %s", (other, batch))
    live.conn.commit()

    assert live.get(batch).status_code == 404


def test_an_unknown_batch_is_a_404_not_a_500(live: Staged) -> None:
    assert live.get(str(uuid4())).status_code == 404


# --------------------------------------------------------------------------------------
# PATCH /import/{batch}/rows — the decisions
# --------------------------------------------------------------------------------------


def test_rows_are_accepted_in_bulk(live: Staged) -> None:
    """Bulk over per-row: `ui-information-architecture.html` §3 names reviewing 200 rows one at a
    time as the reason people abandon these tools."""
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]

    response = live.patch(batch, {"rowIds": rows, "decision": "accepted"})

    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 2
    groups = {g["status"]: g["count"] for g in live.get(batch).json()["groups"]}
    assert groups == {"accepted": 2}


def test_a_row_can_be_rejected_and_the_decision_is_visible(live: Staged) -> None:
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]

    live.patch(batch, {"rowIds": rows[:1], "decision": "rejected"})

    groups = {g["status"]: g["count"] for g in live.get(batch).json()["groups"]}
    assert groups == {"rejected": 1, "new": 1}


def test_a_row_from_another_batch_is_refused_rather_than_silently_skipped(live: Staged) -> None:
    """A partial success reported as a success is how a reviewer comes to believe they accepted
    rows they did not. The count would say 1 and the caller asked for 2."""
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]

    response = live.patch(batch, {"rowIds": [rows[0], str(uuid4())], "decision": "accepted"})

    assert response.status_code == 422
    groups = {g["status"]: g["count"] for g in live.get(batch).json()["groups"]}
    assert groups == {"new": 2}, "nothing was decided"


def test_a_decision_outside_the_vocabulary_is_refused(live: Staged) -> None:
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]

    assert live.patch(batch, {"rowIds": rows, "decision": "imported"}).status_code == 422
    assert live.patch(batch, {"rowIds": rows, "decision": "new"}).status_code == 422


def test_a_completed_batch_cannot_be_re_decided(live: Staged) -> None:
    """Once the rows are in the ledger the staged copy is a record, not a working set."""
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]
    live.patch(batch, {"rowIds": rows, "decision": "accepted"})
    assert live.commit(batch).status_code == 200

    response = live.patch(batch, {"rowIds": rows, "decision": "rejected"})

    assert response.status_code == 409


# --------------------------------------------------------------------------------------
# POST /import/{batch}/commit — V14, through the route
# --------------------------------------------------------------------------------------


def test_accepted_rows_reach_the_ledger(live: Staged) -> None:
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]
    live.patch(batch, {"rowIds": rows, "decision": "accepted"})

    response = live.commit(batch)

    assert response.status_code == 200, response.text
    assert response.json() == {"batchId": batch, "rowsImported": 2, "status": "completed"}
    assert live.ledger_rows() == 2
    with live.conn.cursor() as cur:
        cur.execute("SELECT source, import_batch_id, source_row_number FROM transaction ORDER BY 3")
        committed = cur.fetchall()
    assert [r[0] for r in committed] == ["statement_import", "statement_import"]
    assert [str(r[1]) for r in committed] == [batch, batch]


def test_a_rejected_duplicate_is_left_behind_and_the_batch_still_reconciles(
    live: Staged,
) -> None:
    """The reason `rejected` exists (spec §5.3): a row the ledger already holds.

    Its amount is already in the balance, so dropping it is exactly what makes the batch reach
    the statement's declared close. The row is put into the ledger first, so this is the real
    shape — a duplicate rejected — rather than an arbitrary row dropped.
    """
    batch = live.stage()
    rows = live.get(batch).json()["groups"][0]["rows"]
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO transaction (household_id, account_id, booked_date, description_raw,
                                        amount, currency, fx_rate_date, fx_rate_is_carried,
                                        fx_rate_to_base, fx_rate_to_secondary, amount_base,
                                        amount_secondary, txn_type, source, dedupe_hash)
               VALUES (%s, %s, %s, %s, %s, %s, %s, false, 1, 1, %s, %s, 'expense', 'manual', %s)""",
            (
                live.household_id,
                live.account_id,
                rows[0]["bookedDate"],
                rows[0]["descriptionRaw"],
                rows[0]["amount"],
                rows[0]["currency"],
                rows[0]["bookedDate"],
                rows[0]["amount"],
                rows[0]["amount"],
                "f" * 64,
            ),
        )
    live.conn.commit()

    live.patch(batch, {"rowIds": [rows[0]["id"]], "decision": "rejected"})
    live.patch(batch, {"rowIds": [rows[1]["id"]], "decision": "accepted"})

    response = live.commit(batch)

    assert response.status_code == 200, response.text
    assert response.json()["rowsImported"] == 1
    assert live.ledger_rows() == 2, "the row already held, plus the one imported"


def test_dropping_a_row_the_ledger_does_not_hold_breaks_the_identity(live: Staged) -> None:
    """Invariant I8 does not stop applying because a human made the change.

    Rejecting a row the ledger does not already hold means the account cannot reach the closing
    balance the statement declares. The commit is refused and nothing is written — the reviewer
    has removed a real movement, and the arithmetic says so.
    """
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]
    live.patch(batch, {"rowIds": rows[:1], "decision": "rejected"})
    live.patch(batch, {"rowIds": rows[1:], "decision": "accepted"})

    response = live.commit(batch)

    assert response.status_code == 409, response.text
    assert "does not reconcile" in response.json()["detail"]
    assert live.ledger_rows() == 0


def test_a_batch_whose_balance_does_not_reconcile_commits_nothing(live: Staged) -> None:
    """V14's post-commit verification, reached through the route. The rollback is the database's;
    what this pins is that the route reports it rather than returning a 500."""
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]
    live.patch(batch, {"rowIds": rows, "decision": "accepted"})
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE import_batch SET declared_closing_balance = 99999 WHERE id = %s", (batch,)
        )
    live.conn.commit()

    response = live.commit(batch)

    assert response.status_code == 409, response.text
    assert "reconcile" in response.json()["detail"]
    assert live.ledger_rows() == 0, "a failed verification leaves the ledger untouched"


def test_committing_twice_imports_once(live: Staged) -> None:
    batch = live.stage()
    rows = [row["id"] for row in live.get(batch).json()["groups"][0]["rows"]]
    live.patch(batch, {"rowIds": rows, "decision": "accepted"})
    assert live.commit(batch).status_code == 200

    response = live.commit(batch)

    assert response.status_code == 409
    assert live.ledger_rows() == 2


def test_a_batch_with_no_accepted_rows_is_refused_before_it_reaches_the_ledger(
    live: Staged,
) -> None:
    """Committing nothing is never what the reviewer meant, and V14 would complete the batch
    with `rows_imported = 0` whenever the declared closing already matched the standing
    balance — a statement marked imported with none of it imported."""
    batch = live.stage()

    response = live.commit(batch)

    assert response.status_code == 422
    assert "no rows" in response.json()["detail"].lower()
    with live.conn.cursor() as cur:
        cur.execute("SELECT status FROM import_batch WHERE id = %s", (batch,))
        assert cur.fetchone()[0] == "awaiting_review"  # type: ignore[index]


def test_the_review_routes_are_behind_the_owner(live: Staged) -> None:
    """ADR-0013/0016. `test_health.py` enumerates every route's dependency; this asserts the
    refusal, the way `test_layouts.py` does for its own."""
    batch = live.stage()
    app.dependency_overrides.clear()
    try:
        client = TestClient(app)
        assert client.get(f"/import/{batch}", headers=ORIGIN).status_code == 401
        assert (
            client.patch(
                f"/import/{batch}/rows", json={"rowIds": [], "decision": "accepted"}, headers=ORIGIN
            ).status_code
            == 401
        )
        assert client.post(f"/import/{batch}/commit", headers=ORIGIN).status_code == 401
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
