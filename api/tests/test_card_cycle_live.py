"""A card statement's cycle, written by the commit that imports its rows.

Committing a card batch writes one `card_statement` inside the same transaction as the ledger rows,
so the cycle and the rows land together or not at all (`statement-ingestion.html` §11). Its facts
come from the statement's own metadata block, normalised once at staging; its balances come from
the batch's declared anchors, which I8 has already reconciled.

`statement_balance` is the magnitude of what was due — positive — beside the signed closing
balance, which is negative on a card the owner owes on (ADR-0010, and V21 refuses the swap). The
plan's contract reads "statement_balance is the batch's declared closing"; that figure is signed,
so the literal reading is refused by V21. `schema.md` `0011` is right. The plan is imprecise.

And a card statement whose metadata did not yield its two dates still commits its rows: V21 needs
`statement_date` and `payment_due_date`, inventing either would break Invariant 7, and refusing the
import would lose the transactions over a due date. So the rows commit, no cycle is written, and
the response says which facts were missing.

These live here rather than in `test_import_review_live.py`, which the plan names: that file is
past twelve hundred lines, and the card fixture is a layout and an account of its own.

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

from oneledger import auth
from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf
from .test_import_review_live import Staged, _all_row_ids, _connect
from .test_ingest_route_live import COLUMNS, SALT

CARD_LAYOUT_ID = UUID("77777777-7777-4777-8777-777777777777")
STATEMENT_DATE = "15/03/2026"
DUE_DATE = "04/04/2026"

# Signed balances, captured with their sign: the statement prints the card's closing as the
# household sees it, negative when owed, so there is no inversion to apply.
CARD_OPENING = r"Opening\s*Balance\s*(-?[0-9,.]+)"
CARD_CLOSING = r"Closing\s*Balance\s*(-?[0-9,.]+)"
CARD_METADATA = {
    "statement_date": r"Statement\s*Date\s*(\d{2}/\d{2}/\d{4})",
    "payment_due_date": r"Payment\s*Due\s*Date\s*(\d{2}/\d{2}/\d{4})",
    "minimum_due": r"Minimum\s*Due\s*([0-9,.]+)",
    "credit_limit": r"Credit\s*Limit\s*([0-9,.]+)",
}


def _card_statement(*, due: str | None = DUE_DATE, dated: bool = True) -> bytes:
    """One cycle: 310 and 90 spent on a card that opened at nothing, closing 400 owed.

    The metadata lines sit in the description band, right of the date column, so none of them
    reads as a transaction row.
    """
    header = [Placement(page=0, x=20, y=770, text="Acme Card Statement")]
    if dated:
        header.append(Placement(page=0, x=150, y=760, text=f"Statement Date {STATEMENT_DATE}"))
        if due is not None:
            header.append(Placement(page=0, x=150, y=750, text=f"Payment Due Date {due}"))
    header += [
        Placement(page=0, x=150, y=740, text="Minimum Due 40.00"),
        Placement(page=0, x=150, y=730, text="Credit Limit 20,000.00"),
        Placement(page=0, x=20, y=720, text="Opening Balance 0.00"),
    ]
    rows = [
        Placement(page=0, x=40, y=700, text="Date"),
        Placement(page=0, x=140, y=700, text="Description"),
        Placement(page=0, x=420, y=700, text="Amount"),
        Placement(page=0, x=500, y=700, text="Balance"),
        Placement(page=0, x=40, y=670, text="01/03/2026"),
        Placement(page=0, x=140, y=670, text="GROCER"),
        Placement(page=0, x=420, y=670, text="-310.00"),
        Placement(page=0, x=500, y=670, text="-310.00"),
        Placement(page=0, x=40, y=650, text="02/03/2026"),
        Placement(page=0, x=140, y=650, text="FUEL"),
        Placement(page=0, x=420, y=650, text="-90.00"),
        Placement(page=0, x=500, y=650, text="-400.00"),
        Placement(page=0, x=20, y=600, text="Closing Balance -400.00"),
    ]
    return build_pdf(header + rows)


class CardStaged(Staged):
    """The review fixture — an owner, a bank account and its layout — plus a credit card and a
    card layout that captures the cycle's four facts."""

    def __init__(self, conn: psycopg.Connection) -> None:
        super().__init__(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM person WHERE household_id = %s LIMIT 1", (self.household_id,)
            )
            person = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO account (household_id, owner_person_id, name, account_type,
                                        currency, country, opening_balance_date, opening_balance,
                                        current_balance, credit_limit)
                   VALUES (%s, %s, 'route test', 'credit_card', %s, 'ZZ', DATE '2026-01-01', 0,
                           0, 20000)
                   RETURNING id""",
                (self.household_id, person, self.base),
            )
            self.card_id: UUID = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO statement_layout
                     (id, household_id, name, version, fingerprints, columns, date_format,
                      sign_convention, inverts_sign, chronological_order, row_start_rule,
                      opening_balance_pattern, closing_balance_pattern, has_running_balance,
                      metadata_patterns)
                   VALUES (%s, %s, 'Acme card', 1, %s, %s, 'dd/MM/yyyy', 'signed', false,
                           'ascending', 'has_date_and_amount', %s, %s, true, %s)""",
                (
                    CARD_LAYOUT_ID,
                    self.household_id,
                    ["Acme Card Statement"],
                    psycopg.types.json.Json(COLUMNS),
                    CARD_OPENING,
                    CARD_CLOSING,
                    psycopg.types.json.Json(CARD_METADATA),
                ),
            )
        conn.commit()

    def stage_card(self, statement: bytes | None = None) -> str:
        response = self.post(
            _card_statement() if statement is None else statement,
            stage="true",
            accountId=str(self.card_id),
        )
        assert response.status_code == 200, response.text
        self.parsed = response.json()
        assert self.parsed["staged"], self.parsed
        batch: str = self.parsed["batchId"]
        return batch

    def accept_all(self, batch: str) -> None:
        decided = self.patch(batch, {"rowIds": _all_row_ids(self, batch), "decision": "accepted"})
        assert decided.status_code == 200, decided.text

    def cycles(self) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT account_id, period_start, period_end, statement_date, payment_due_date,
                          currency, opening_balance, closing_balance, statement_balance,
                          minimum_due, credit_limit, source_document_id
                     FROM card_statement ORDER BY period_end"""
            )
            names = [column.name for column in cur.description or []]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def extracted(self, batch: str) -> dict[str, str]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT d.extracted_metadata FROM import_batch b JOIN document d
                     ON d.id = b.document_id WHERE b.id = %s""",
                (batch,),
            )
            found: dict[str, str] = cur.fetchone()[0] or {}  # type: ignore[index]
            return found


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CardStaged]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        fixture = CardStaged(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            # `card_statement` references `document` and `transaction`, so the cascade takes it.
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute("DELETE FROM statement_layout")
            cur.execute("DELETE FROM account WHERE name = 'route test'")
            cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
            cur.execute("DELETE FROM app_user WHERE email LIKE 'review-%@example.invalid'")
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------------------
# The acceptance: one cycle whose facts match the statement exactly.


def test_a_card_statement_commits_one_cycle_whose_facts_match_the_statement(
    live: CardStaged,
) -> None:
    batch = live.stage_card()
    live.accept_all(batch)

    committed = live.commit(batch)

    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert body["rowsImported"] == 2
    assert body["cardStatementId"] is not None
    assert body["cardStatementMissing"] == []
    [cycle] = live.cycles()
    assert cycle["account_id"] == live.card_id
    assert (cycle["period_start"], cycle["period_end"]) == (date(2026, 3, 1), date(2026, 3, 2))
    assert cycle["statement_date"] == date(2026, 3, 15)
    assert cycle["payment_due_date"] == date(2026, 4, 4)
    assert cycle["minimum_due"] == Decimal("40.0000")
    assert cycle["credit_limit"] == Decimal("20000.0000")
    assert cycle["opening_balance"] == Decimal("0.0000")
    # ADR-0010: the owed closing is negative, and what was due is its positive magnitude.
    assert cycle["closing_balance"] == Decimal("-400.0000")
    assert cycle["statement_balance"] == Decimal("400.0000")
    assert cycle["currency"] == live.base
    assert str(cycle["source_document_id"]) == live.parsed["documentId"]


def test_the_facts_are_stored_once_in_canonical_form(live: CardStaged) -> None:
    """Nothing downstream re-reads a producer's formatting: `2026-03-15`, not `15/03/2026`."""
    batch = live.stage_card()

    stored = live.extracted(batch)

    assert {key: stored[key] for key in CARD_METADATA} == {
        "statement_date": "2026-03-15",
        "payment_due_date": "2026-04-04",
        "minimum_due": "40.0000",
        "credit_limit": "20000.0000",
    }
    assert live.parsed["metadataFailures"] == []


def test_a_bank_batch_writes_no_cycle(live: CardStaged) -> None:
    batch = live.stage()
    live.accept_all(batch)

    committed = live.commit(batch)

    assert committed.status_code == 200, committed.text
    assert committed.json()["cardStatementId"] is None
    assert committed.json()["cardStatementMissing"] == []
    assert live.cycles() == []


# ---------------------------------------------------------------------------------------
# One transaction: the cycle and the rows together, or neither.


def test_a_cycle_that_cannot_be_written_rolls_the_whole_commit_back(live: CardStaged) -> None:
    """Committing the same period twice is refused by V21's unique index rather than holding
    two cycles — and the refusal takes the ledger rows back with it."""
    batch = live.stage_card()
    live.accept_all(batch)
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO card_statement (household_id, account_id, period_start, period_end,
                                           statement_date, payment_due_date, currency,
                                           opening_balance, closing_balance, statement_balance)
               VALUES (%s, %s, DATE '2026-03-01', DATE '2026-03-02', DATE '2026-03-15',
                       DATE '2026-04-04', %s, 0, -1, 1)""",
            (live.household_id, live.card_id, live.base),
        )
    live.conn.commit()

    refused = live.commit(batch)

    assert refused.status_code == 409, refused.text
    assert live.ledger_rows() == 0
    assert len(live.cycles()) == 1
    assert live.get(batch).json()["status"] == "awaiting_review"


# ---------------------------------------------------------------------------------------
# A statement that did not yield its facts still commits its rows.


def test_a_card_statement_without_its_dates_still_commits_its_rows(live: CardStaged) -> None:
    batch = live.stage_card(_card_statement(dated=False))
    live.accept_all(batch)

    committed = live.commit(batch)

    assert committed.status_code == 200, committed.text
    body = committed.json()
    assert body["rowsImported"] == 2
    assert body["cardStatementId"] is None
    assert body["cardStatementMissing"] == ["payment_due_date", "statement_date"]
    assert live.cycles() == []
    assert live.parsed["metadataFailures"] == [], "absent is not a failure"


def test_a_capture_that_will_not_read_is_reported_and_does_not_block(live: CardStaged) -> None:
    """31/31 matches the pattern and is not a date. It is reported, it is absent, and the
    statement still stages — a due date is not worth the transactions."""
    batch = live.stage_card(_card_statement(due="31/31/2026"))

    reported = live.parsed["metadataFailures"]

    assert [failure["key"] for failure in reported] == ["payment_due_date"]
    assert "31/31/2026" not in reported[0]["reason"]
    assert "payment_due_date" not in live.extracted(batch)
    live.accept_all(batch)
    committed = live.commit(batch)
    assert committed.status_code == 200, committed.text
    assert committed.json()["cardStatementMissing"] == ["payment_due_date"]
    assert live.ledger_rows() == 2


# ---------------------------------------------------------------------------------------
# A statement cancelled and imported again commits the cycle the second parse read.


def test_a_re_imported_card_statement_commits_the_cycle_the_second_parse_read(
    live: CardStaged,
) -> None:
    """The reasons an owner cancels: the wrong account, a mapping that missed the summary.

    The same bytes are the same document row, and a re-import reuses it — so the row has to
    describe the second attempt. The commit reads the document type and the cycle's facts back
    from it, and a row still describing the first commits the rows with no cycle and nothing
    saying why.
    """
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET metadata_patterns = NULL WHERE id = %s", (CARD_LAYOUT_ID,)
        )
    live.conn.commit()
    wrong = live.post(_card_statement(), stage="true")  # into the bank account
    assert wrong.status_code == 200, wrong.text
    assert wrong.json()["staged"], wrong.text
    assert live.cancel(wrong.json()["batchId"]).status_code == 200
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET metadata_patterns = %s WHERE id = %s",
            (psycopg.types.json.Json(CARD_METADATA), CARD_LAYOUT_ID),
        )
    live.conn.commit()

    batch = live.stage_card()
    live.accept_all(batch)
    committed = live.commit(batch)

    assert committed.status_code == 200, committed.text
    assert committed.json()["cardStatementId"] is not None
    [cycle] = live.cycles()
    assert cycle["account_id"] == live.card_id
    assert cycle["payment_due_date"] == date(2026, 4, 4)
