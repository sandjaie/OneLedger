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
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.db import connect, scope_to_owner
from oneledger.ingest.dedupe import OCCURRENCE, fingerprint
from oneledger.main import app
from oneledger.routes import ingest as ingest_routes

from .fixtures.synthetic_pdf import Placement, build_pdf
from .test_ingest_route_live import LAYOUT_ID, ORIGIN, SALT, Fixture, _statement


def _dated_statement() -> bytes:
    """The same statement, plus the period the layout can capture.

    The default fixture's layout maps no period pattern, so every batch it stages has a *derived*
    period. A flag that is only ever true proves nothing — this is the other value.
    """
    return build_pdf(
        [
            Placement(page=0, x=20, y=770, text="Statement Period 01/03/2026 to 31/03/2026"),
            Placement(page=0, x=20, y=760, text="Acme Bank Statement"),
            Placement(page=0, x=20, y=750, text="Account Number 1234567890"),
            Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
            Placement(page=0, x=40, y=700, text="Date"),
            Placement(page=0, x=140, y=700, text="Description"),
            Placement(page=0, x=420, y=700, text="Amount"),
            Placement(page=0, x=500, y=700, text="Balance"),
            Placement(page=0, x=40, y=670, text="01/03/2026"),
            Placement(page=0, x=140, y=670, text="COFFEE SHOP"),
            Placement(page=0, x=420, y=670, text="-10.00"),
            Placement(page=0, x=500, y=670, text="990.00"),
            Placement(page=0, x=40, y=650, text="02/03/2026"),
            Placement(page=0, x=140, y=650, text="SALARY MARCH"),
            Placement(page=0, x=420, y=650, text="500.00"),
            Placement(page=0, x=500, y=650, text="1,490.00"),
            Placement(page=0, x=20, y=600, text="Closing Balance 1,490.00"),
        ]
    )


def _twin_statement() -> bytes:
    """A statement whose two rows are genuinely identical — same date, same amount, same words.

    The default fixture's rows differ in their descriptions, so their fingerprints differ
    whatever the occurrence index is and they can never collide with each other. Only a pair
    like this one can prove that two overrides in the same batch land on different indices.

    Opening 1,000, two withdrawals of 10, closing 980. Same account number, so it resolves the
    same way.
    """
    return build_pdf(
        [
            Placement(page=0, x=20, y=760, text="Acme Bank Statement"),
            Placement(page=0, x=20, y=750, text="Account Number 1234567890"),
            Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
            Placement(page=0, x=40, y=700, text="Date"),
            Placement(page=0, x=140, y=700, text="Description"),
            Placement(page=0, x=420, y=700, text="Amount"),
            Placement(page=0, x=500, y=700, text="Balance"),
            Placement(page=0, x=40, y=670, text="01/03/2026"),
            Placement(page=0, x=140, y=670, text="COFFEE SHOP"),
            Placement(page=0, x=420, y=670, text="-10.00"),
            Placement(page=0, x=500, y=670, text="990.00"),
            Placement(page=0, x=40, y=650, text="01/03/2026"),
            Placement(page=0, x=140, y=650, text="COFFEE SHOP"),
            Placement(page=0, x=420, y=650, text="-10.00"),
            Placement(page=0, x=500, y=650, text="980.00"),
            Placement(page=0, x=20, y=600, text="Closing Balance 980.00"),
        ]
    )


def _connect() -> psycopg.Connection:
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("no database credentials in the environment")

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

    # Spec §5.6's own example, and the reason the review payload is screened at all: a card
    # payment line carries the number in the narration, separator-formatted.
    CARD_NARRATION = "PAYMENT RECEIVED CARD 4111 1111 1111 1111"

    def stage(self, description: str = CARD_NARRATION, statement: bytes | None = None) -> str:
        response = self.post(
            _statement(description=description) if statement is None else statement, stage="true"
        )
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


def _all_row_ids(live: Staged, batch: str) -> list[str]:
    return [row["id"] for group in live.get(batch).json()["groups"] for row in group["rows"]]


def _commit_racing_a_rejection(live: Staged, batch: str, rows: list[str]) -> httpx.Response:
    """Commit with every accepted row rejected inside the route's own race window.

    `scope_to_owner` is called after the accepted-row count and before `commit_import_batch`,
    which is exactly the gap a second tab commits into. Rejecting there on a separate
    connection is the race, made deterministic — the route's transaction is READ COMMITTED, so
    the function sees the rejection the same way it would see a genuine concurrent one.
    """
    # The same object the route module imported, named from its home: `mypy --strict` will
    # not read it back off `ingest_routes`, which does not re-export it.
    real = scope_to_owner

    def reject_then_scope(conn: psycopg.Connection, user_id: UUID) -> None:
        with connect() as other, other.cursor() as cur:
            cur.execute(
                """UPDATE staged_transaction SET status = 'rejected'
                    WHERE import_batch_id = %s AND id = ANY(%s)""",
                (batch, [UUID(row) for row in rows]),
            )
        real(conn, user_id)

    with patch.object(ingest_routes, "scope_to_owner", reject_then_scope):
        return live.commit(batch)


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
    # Derived: the fixture layout captures no period, so the dates came from the rows.
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


def test_a_card_number_in_the_narration_is_screened_out_of_the_review_payload(
    live: Staged,
) -> None:
    """Spec §5.6 leaves this to 1.1b explicitly, so 1.1b has to assert it.

    `staged_transaction.description_raw` is stored unscreened — it is the audit record. Whether
    the *payload* screens it was named as this slice's call rather than inherited, and the
    answer is yes: statement narration is where a card number actually appears, and the reviewer
    having already opened the PDF does not put one in a response body.

    The assertion is on the response, not on the redactor: deleting the screening from
    `read_import_batch` leaves every other test in this file green.
    """
    batch = live.stage()

    row = next(
        row
        for group in live.get(batch).json()["groups"]
        for row in group["rows"]
        if row["sourceRowNumber"] == 2
    )

    assert "4111" not in row["descriptionRaw"], row["descriptionRaw"]
    assert "[redacted]" in row["descriptionRaw"]
    # The stored row keeps it: the payload is screened, the audit record is not.
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT description_raw FROM staged_transaction WHERE import_batch_id = %s"
            " AND source_row_number = 2",
            (batch,),
        )
        assert "4111 1111 1111 1111" in cur.fetchone()[0]  # type: ignore[index]


def test_a_batch_that_never_reconciled_is_readable_and_says_so(live: Staged) -> None:
    """`reconciliation_status`, `period_start` and `period_end` are all nullable (V12), and a
    batch that failed before reconciling carries none of them.

    `str()` over the null put the literal `"None"` on the wire — a value outside the column's
    own CHECK vocabulary — and a required `date` made the whole batch a 500. The batch the owner
    most needs to look at is the one that did not finish, so it has to be readable.
    """
    batch = live.stage()
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE import_batch
                  SET reconciliation_status = NULL, period_start = NULL, period_end = NULL,
                      period_is_derived = NULL
                WHERE id = %s""",
            (batch,),
        )
    live.conn.commit()

    response = live.get(batch)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reconciliation"]["status"] == "not reconciled"
    assert body["period"] == {"start": None, "end": None, "isDerived": None}


def test_a_batch_that_recorded_no_provenance_says_so(live: Staged) -> None:
    """Invariant I7, for the one case that still has no answer: a batch staged before V17.

    Its column is null and stays null. Filling it in would label a provenance nobody observed —
    `true` calls a declared period derived and `false` calls a computed one stated — and the two
    are indistinguishable from the dates alone, which is why the column exists.
    """
    batch = live.stage()
    with live.conn.cursor() as cur:
        cur.execute("UPDATE import_batch SET period_is_derived = NULL WHERE id = %s", (batch,))
    live.conn.commit()

    body = live.get(batch).json()

    assert body["period"]["isDerived"] is None
    # The dates are still there, so nothing could be inferred from their absence either.
    assert body["period"]["start"] == "2026-03-01"


def test_groups_come_back_most_attention_needed_first(live: Staged) -> None:
    """`statement-ingestion.html` §10 orders the groups; alphabetical would put `duplicate`
    above `needs_review`, which is the sort the grouping exists to avoid."""
    batch = live.stage()
    with live.conn.cursor() as cur:
        # One row of each, so the order is observable at all.
        cur.execute(
            """UPDATE staged_transaction SET status = 'duplicate'
                WHERE import_batch_id = %s AND source_row_number = 1""",
            (batch,),
        )
        cur.execute(
            """UPDATE staged_transaction SET status = 'needs_review'
                WHERE import_batch_id = %s AND source_row_number = 2""",
            (batch,),
        )
    live.conn.commit()

    statuses = [group["status"] for group in live.get(batch).json()["groups"]]

    assert statuses == ["needs_review", "duplicate"], "duplicates are the last thing to read"


def test_the_commit_reports_the_status_the_database_actually_set(live: Staged) -> None:
    """The route re-implements none of V14's rules, and the status it writes is one of them. A
    literal `"completed"` here goes on reading `completed` the day the function stops writing
    it."""
    batch = live.stage()
    rows = _all_row_ids(live, batch)
    assert live.patch(batch, {"rowIds": rows, "decision": "accepted"}).status_code == 200

    body = live.commit(batch).json()

    with live.conn.cursor() as cur:
        cur.execute("SELECT status, rows_imported FROM import_batch WHERE id = %s", (batch,))
        status, imported = cur.fetchone()  # type: ignore[misc]
    assert body["status"] == status
    assert body["rowsImported"] == imported


def test_a_commit_that_imports_nothing_is_rolled_back_whole(live: Staged) -> None:
    """The accepted-row count is read before V14 takes the household and batch rows FOR UPDATE,
    so it answers for a moment that has passed.

    Simulated here by rejecting the rows between the count and the call — which is what a second
    tab does — rather than by racing two connections, because the outcome is what matters: the
    batch must not end up `completed` having imported nothing. Only the count V14 returns is
    race-free, and raising on it rolls the whole commit back.
    """
    # The narrow case the route's own comment names: the account already stands at the figure
    # the statement declares, so V14's post-commit balance check passes having imported nothing
    # and the batch completes empty. With any other opening balance the reconciliation refuses
    # first and the defect is invisible.
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE account SET opening_balance = 1490, current_balance = 1490 WHERE id = %s",
            (live.account_id,),
        )
    live.conn.commit()
    batch = live.stage()
    rows = _all_row_ids(live, batch)
    assert live.patch(batch, {"rowIds": rows, "decision": "accepted"}).status_code == 200

    response = _commit_racing_a_rejection(live, batch, rows)

    assert response.status_code == 409, response.text
    assert "no rows" in response.json()["detail"].lower()
    assert live.ledger_rows() == 0
    with live.conn.cursor() as cur:
        cur.execute("SELECT status, rows_imported FROM import_batch WHERE id = %s", (batch,))
        assert cur.fetchone() == ("awaiting_review", 0), "nothing half-applied"


# --------------------------------------------------------------------------------------
# The confirmed-distinct override (spec §4.7, §7 item 12)
# --------------------------------------------------------------------------------------


def _committed_twin(live: Staged, batch: str) -> str:
    """Commit the batch, stage the same statement again, and return the second batch.

    The second staging sees the first's rows already in `transaction` and marks both
    `duplicate` — a genuine collision, produced the way one actually arises, rather than by
    writing a status into the table by hand.
    """
    rows = _all_row_ids(live, batch)
    assert live.patch(batch, {"rowIds": rows, "decision": "accepted"}).status_code == 200
    assert live.commit(batch).status_code == 200
    with live.conn.cursor() as cur:
        # Layer 1 rejects identical bytes outright, so the checksum is moved aside rather than
        # the document deleted — `import_batch` references it, and the first import has to
        # stand for its rows to be the collision this exercises. What is being staged is the
        # real case: a *different* file carrying the same transactions, which is what an
        # overlapping Jul–Aug / Aug–Sep pair is.
        cur.execute("UPDATE document SET sha256 = %s", (repeat_hex("f"),))
    live.conn.commit()
    return live.stage()


def repeat_hex(ch: str) -> str:
    return ch * 64


def _hashes(live: Staged, batch: str) -> list[str]:
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT dedupe_hash FROM staged_transaction WHERE import_batch_id = %s"
            " ORDER BY source_row_number",
            (batch,),
        )
        return [row[0] for row in cur.fetchall()]


def test_a_re_import_stages_its_rows_as_duplicates(live: Staged) -> None:
    """The precondition the override exists for, asserted rather than assumed."""
    second = _committed_twin(live, live.stage())

    groups = {group["status"]: group["count"] for group in live.get(second).json()["groups"]}

    assert groups == {"duplicate": 2}


def test_accepting_a_duplicate_recomputes_its_hash_to_the_next_free_index(
    live: Staged,
) -> None:
    """Spec §4.7: accepting a duplicate is a confirmed-distinct override.

    V10's unique `(household_id, account_id, dedupe_hash)` index means the colliding hash cannot
    commit unchanged, so the accept recomputes it with the occurrence index advanced to the next
    free index among committed collisions — deterministic, not a random suffix.
    """
    second = _committed_twin(live, live.stage())
    before = _hashes(live, second)
    rows = _all_row_ids(live, second)

    assert live.patch(second, {"rowIds": rows, "decision": "accepted"}).status_code == 200

    after = _hashes(live, second)
    assert after != before, "a colliding hash cannot commit unchanged"
    assert len(set(after)) == 2, "and the two overrides do not collide with each other"
    with live.conn.cursor() as cur:
        cur.execute("SELECT dedupe_hash FROM transaction")
        committed = {row[0] for row in cur.fetchall()}
    assert not committed.intersection(after), "nor with anything already in the ledger"


def _expected_occurrences(live: Staged, batch: str) -> list[int]:
    """For each staged row in order, the `occ:` index its stored hash actually encodes.

    Derived by recomputing the fingerprint at each index and finding which one the row holds —
    the only way to name the index from outside, and it re-derives none of the route's search.
    """
    found = []
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT account_id, booked_date, amount, description_raw, dedupe_hash
                 FROM staged_transaction WHERE import_batch_id = %s
                ORDER BY source_row_number""",
            (batch,),
        )
        for account_id, booked_date, amount, description_raw, stored in cur.fetchall():
            for index in range(16):
                if stored == fingerprint(
                    account_id=account_id,
                    booked_date=booked_date,
                    amount=amount,
                    description_raw=description_raw,
                    disambiguator=f"{OCCURRENCE}{index}",
                ):
                    found.append(index)
                    break
            else:
                found.append(-1)
    return found


def test_the_override_advances_to_the_next_free_index_and_no_further(live: Staged) -> None:
    """The exact indices, not merely "some free index".

    That the index is the *next* one is the whole reason section 4.7 advances it rather than
    marking the row exempt: a later statement carrying both occurrences hashes them 0 and 1 and
    must recognise both as duplicates. An override that jumped to 5 would satisfy "changed" and
    "does not collide" while breaking exactly that, and the overlapping month would import twice.

    Two identical rows, so occurrences 0 and 1 are genuinely committed and the override has
    something to advance past — and the second row must advance past the first's new index too.
    """
    first = live.stage(statement=_twin_statement())
    assert (
        live.patch(first, {"rowIds": _all_row_ids(live, first), "decision": "accepted"}).status_code
        == 200
    )
    assert live.commit(first).status_code == 200
    with live.conn.cursor() as cur:
        cur.execute("UPDATE document SET sha256 = %s", (repeat_hex("d"),))
        cur.execute("UPDATE account SET current_balance = 980 WHERE id = %s", (live.account_id,))
    live.conn.commit()
    second = live.stage(statement=_twin_statement())
    assert _expected_occurrences(live, second) == [0, 1], "staged where the ledger already sits"

    assert (
        live.patch(
            second, {"rowIds": _all_row_ids(live, second), "decision": "accepted"}
        ).status_code
        == 200
    )

    assert _expected_occurrences(live, second) == [2, 3], (
        "0 and 1 are committed, so the first free is 2 — and the second row advances past it"
    )


def test_the_override_recomputes_in_the_occurrence_namespace(live: Staged) -> None:
    """Spec section 7 item 12: the override recomputes in the `occ:` form whatever disambiguated
    the row before.

    The fixture references its first row and not its second, so the two arrive here from
    different namespaces and must land differently. The referenced row was fingerprinted
    `ref:<reference>#0`, so **no `occ:` index is taken on its account** and the override lands on
    0 — which is only possible because the namespace switched: advancing a `ref:` counter would
    change a value the fingerprint does not contain and the hash would not move at all. The
    unreferenced row was already `occ:0`, which the ledger now holds, so it advances to 1.
    """
    second = _committed_twin(live, live.stage())
    before = _hashes(live, second)

    live.patch(second, {"rowIds": _all_row_ids(live, second), "decision": "accepted"})

    assert _expected_occurrences(live, second) == [0, 1], (
        "the referenced row crosses into a free occ: index; the occ: row advances past the ledger"
    )
    assert _hashes(live, second) != before, "and both stored hashes moved"


def test_repeating_the_override_changes_nothing(live: Staged) -> None:
    """A second accept must not advance the index again: the row is already accepted, and an
    override that moved every time would make the same review produce a different ledger."""
    second = _committed_twin(live, live.stage())
    rows = _all_row_ids(live, second)
    live.patch(second, {"rowIds": rows, "decision": "accepted"})
    once = _hashes(live, second)

    assert live.patch(second, {"rowIds": rows, "decision": "accepted"}).status_code == 200

    assert _hashes(live, second) == once


def test_an_accepted_duplicate_reaches_the_ledger_as_its_own_row(live: Staged) -> None:
    """The acceptance criterion: a genuinely distinct transaction that collides with a committed
    one can be imported."""
    second = _committed_twin(live, live.stage())
    with live.conn.cursor() as cur:
        # The ledger already holds the first import, so the second's closing target is the
        # statement's own figure plus what is already there.
        cur.execute(
            "UPDATE import_batch SET declared_closing_balance = 1980 WHERE id = %s", (second,)
        )
    live.conn.commit()
    rows = _all_row_ids(live, second)
    live.patch(second, {"rowIds": rows, "decision": "accepted"})

    response = live.commit(second)

    assert response.status_code == 200, response.text
    assert response.json()["rowsImported"] == 2
    assert live.ledger_rows() == 4, "both imports stand, and neither overwrote the other"


def test_rejecting_a_duplicate_leaves_its_hash_alone(live: Staged) -> None:
    """The override is what accepting means, not what deciding means. A rejected row keeps the
    fingerprint that recognised it, which is what a later statement carrying both occurrences
    matches against."""
    second = _committed_twin(live, live.stage())
    before = _hashes(live, second)
    rows = _all_row_ids(live, second)

    live.patch(second, {"rowIds": rows, "decision": "rejected"})

    assert _hashes(live, second) == before


def test_accepting_a_row_with_no_proposed_type_is_refused_by_the_database(live: Staged) -> None:
    """V13's `accepted_row_is_committable`. A row that can be accepted but never committed is a
    dead end the database refuses to represent — and the route must not turn that refusal into
    a 500."""
    batch = live.stage()
    rows = _all_row_ids(live, batch)
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE staged_transaction SET proposed_txn_type = NULL WHERE id = %s",
            (UUID(rows[0]),),
        )
    live.conn.commit()

    response = live.patch(batch, {"rowIds": rows, "decision": "accepted"})

    assert response.status_code == 422, response.text
    assert "type" in response.json()["detail"].lower()
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM staged_transaction WHERE import_batch_id = %s"
            " AND status = 'accepted'",
            (batch,),
        )
        assert cur.fetchone()[0] == 0, "nothing was decided"  # type: ignore[index]


def test_two_overrides_in_one_batch_do_not_collide_with_each_other(live: Staged) -> None:
    """The staged half of the search, which the committed half alone does not cover.

    Two identical rows commit at occurrence 0 and 1. Re-imported, both stage as duplicates, and
    both search the same candidate list — so a search that consults only the ledger hands both
    of them occurrence 2, and V10's unique index rejects the whole commit a moment later, having
    passed every check that looked at the ledger alone.
    """
    first = live.stage(statement=_twin_statement())
    rows = _all_row_ids(live, first)
    assert live.patch(first, {"rowIds": rows, "decision": "accepted"}).status_code == 200
    assert live.commit(first).status_code == 200, "two identical rows import as two"
    with live.conn.cursor() as cur:
        cur.execute("UPDATE document SET sha256 = %s", (repeat_hex("e"),))
        cur.execute("UPDATE account SET current_balance = 980 WHERE id = %s", (live.account_id,))
    live.conn.commit()
    second = live.stage(statement=_twin_statement())
    assert len(set(_hashes(live, second))) == 2, "staging already tells them apart"

    assert (
        live.patch(
            second, {"rowIds": _all_row_ids(live, second), "decision": "accepted"}
        ).status_code
        == 200
    )

    after = _hashes(live, second)
    assert len(set(after)) == 2, "the second override must not land where the first did"
    with live.conn.cursor() as cur:
        cur.execute("SELECT dedupe_hash FROM transaction")
        assert not {row[0] for row in cur.fetchall()}.intersection(after)


def test_the_override_gives_up_rather_than_searching_forever(
    live: Staged, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sixty-four identical transactions on one day is a statement to look at, not a number to
    raise — and the refusal must leave the batch exactly as it was."""
    second = _committed_twin(live, live.stage())
    before = _hashes(live, second)
    monkeypatch.setattr(ingest_routes, "OVERRIDE_LIMIT", 1)

    response = live.patch(second, {"rowIds": _all_row_ids(live, second), "decision": "accepted"})

    assert response.status_code == 409, response.text
    assert "too many times" in response.json()["detail"]
    assert _hashes(live, second) == before, "and nothing was rewritten"
    groups = {group["status"]: group["count"] for group in live.get(second).json()["groups"]}
    assert groups == {"duplicate": 2}, "nor decided"


# --------------------------------------------------------------------------------------
# The period's provenance (Invariant I7)
# --------------------------------------------------------------------------------------


def test_a_derived_period_reads_back_as_derived(live: Staged) -> None:
    """The fixture layout captures no period, so the pipeline computed one from the row dates.

    That is a labelled fact, not a stated one, and the batch must carry the label — the review
    screen is where it is read, and a computed period shown as a declared one is the direction
    Invariant I7 forbids.
    """
    batch = live.stage()

    assert live.get(batch).json()["period"]["isDerived"] is True


def test_a_stated_period_reads_back_as_stated(live: Staged) -> None:
    """The other value, which is what makes the flag mean anything.

    A flag that is only ever true is a constant with a longer name.
    """
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = metadata_patterns || %s::jsonb
                WHERE id = %s""",
            (
                psycopg.types.json.Json(
                    {
                        "period_start": r"Statement\s*Period\s*([0-9/]+)\s*to",
                        "period_end": r"Statement\s*Period\s*[0-9/]+\s*to\s*([0-9/]+)",
                    }
                ),
                LAYOUT_ID,
            ),
        )
    live.conn.commit()

    batch = live.stage(statement=_dated_statement())

    body = live.get(batch).json()
    assert body["period"] == {"start": "2026-03-01", "end": "2026-03-31", "isDerived": False}


def test_the_period_provenance_survives_the_round_trip(live: Staged) -> None:
    """Stored on the batch, not recomputed from the rows it happens to hold.

    A period derived from two rows dated 1 and 2 March looks identical to one a statement
    stated as 1–2 March; only the recorded flag tells them apart, which is why it is a column.
    """
    batch = live.stage()

    with live.conn.cursor() as cur:
        cur.execute("SELECT period_is_derived FROM import_batch WHERE id = %s", (batch,))
        stored = cur.fetchone()[0]  # type: ignore[index]

    assert stored is True
    assert live.get(batch).json()["period"]["isDerived"] == stored
