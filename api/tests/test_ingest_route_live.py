"""`POST /parse` end to end: a real PDF, a real layout row, a real database.

The pipeline's own suites prove what it decides and what it writes. This proves the route around
it — that a multipart upload reaches the engine, that the wire shape is the one
`api-contracts.html` §6 requires (camelCase, money as strings, dates as dates), and that a
blocked statement comes back as a 200 carrying its verdict rather than as an HTTP error
(spec §7.5).

Skipped when no database is reachable. Every figure is invented.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf

ORIGIN = {"Origin": "http://localhost:3000"}
LAYOUT_ID = UUID("66666666-6666-6666-6666-666666666666")

# Bands wide enough that a glyph-width estimate cannot push a cell into its neighbour: what is
# under test here is the route, not the engine's geometry, which `test_ingest_engine.py` owns.
COLUMNS = [
    {"field": "booked_date", "x_min": 30.0, "x_max": 120.0, "label": "Date"},
    {"field": "description", "x_min": 120.0, "x_max": 400.0, "label": "Description"},
    {"field": "amount", "x_min": 400.0, "x_max": 480.0, "label": "Amount"},
    {"field": "running_balance", "x_min": 480.0, "x_max": 580.0, "label": "Balance"},
    {"field": "reference", "x_min": 580.0, "x_max": 700.0, "label": "Ref"},
]

# `\s*` rather than a literal space: a PDF's extracted text runs its characters together or not
# depending on whether the producer emitted space glyphs, and the anchor must not depend on it.
SALT = "route-test-salt-not-used-outside-this-suite"
ACCOUNT_NUMBER = "1234567890"
ACCOUNT_NUMBER_PATTERN = r"Account\s*Number\s*([0-9-]+)"
OPENING = r"Opening\s*Balance\s*([0-9,.]+)"
CLOSING = r"Closing\s*Balance\s*([0-9,.]+)"


def _account_hash(number: str) -> str:
    """The stored account identity: a salted digest, never the number (never-store list)."""
    return hashlib.sha256(f"{SALT}{number}".encode()).hexdigest()


def _statement(
    closing: str = "1,490.00",
    description: str = "SALARY MARCH",
    reference: str = "REF-9911-2233-4455",
) -> bytes:
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
            Placement(page=0, x=600, y=670, text=reference),
            Placement(page=0, x=420, y=670, text="-10.00"),
            Placement(page=0, x=500, y=670, text="990.00"),
            Placement(page=0, x=40, y=650, text="02/03/2026"),
            Placement(page=0, x=140, y=650, text=description),
            Placement(page=0, x=420, y=650, text="500.00"),
            Placement(page=0, x=500, y=650, text="1,490.00"),
            Placement(page=0, x=20, y=600, text=f"Closing Balance {closing}"),
        ]
    )


def _connect() -> psycopg.Connection:
    if "POSTGRES_PASSWORD" not in os.environ:
        pytest.skip("no database credentials in the environment")
    from oneledger.db import connect

    try:
        return connect()
    except psycopg.OperationalError:
        pytest.skip("database unreachable")


def _clean(cur: psycopg.Cursor[Any]) -> None:
    cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
    cur.execute("DELETE FROM statement_layout")
    cur.execute("DELETE FROM account WHERE name = 'route test'")
    cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")


class Fixture:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        with conn.cursor() as cur:
            _clean(cur)
            cur.execute("SELECT id, base_currency, secondary_currency FROM household LIMIT 1")
            row = cur.fetchone()
            if row is None:
                pytest.skip("no seeded household")
            self.household_id, self.base, self.secondary = row

            cur.execute(
                "SELECT id FROM person WHERE household_id = %s LIMIT 1", (self.household_id,)
            )
            person = cur.fetchone()
            if person is None:
                pytest.skip("no seeded person")
            cur.execute(
                """INSERT INTO account (household_id, owner_person_id, name, account_type,
                                        currency, country, opening_balance_date, current_balance,
                                        account_number_hash)
                   VALUES (%s, %s, 'route test', 'savings', %s, 'ZZ', DATE '2026-01-01', 0, %s)
                   RETURNING id""",
                (self.household_id, person[0], self.base, _account_hash(ACCOUNT_NUMBER)),
            )
            self.account_id = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                """INSERT INTO statement_layout
                     (id, household_id, name, version, fingerprints, columns, date_format,
                      sign_convention, inverts_sign, chronological_order, row_start_rule,
                      opening_balance_pattern, closing_balance_pattern, has_running_balance,
                      metadata_patterns)
                   VALUES (%s, %s, 'Acme current account', 1, %s, %s, 'dd/MM/yyyy', 'signed',
                           false, 'ascending', 'has_date_and_amount', %s, %s, true, %s)""",
                (
                    LAYOUT_ID,
                    self.household_id,
                    ["Acme Bank Statement"],
                    psycopg.types.json.Json(COLUMNS),
                    OPENING,
                    CLOSING,
                    psycopg.types.json.Json({"account_number": ACCOUNT_NUMBER_PATTERN}),
                ),
            )
            for on in (date(2026, 3, 1), date(2026, 3, 2)):
                cur.execute(
                    """INSERT INTO fx_rate (rate_date, base_currency, quote_currency, rate, source)
                       VALUES (%s, %s, %s, 4.0000000000, 'manual') ON CONFLICT DO NOTHING""",
                    (on, self.secondary, self.base),
                )
        conn.commit()

    def owner(self) -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=UUID(int=1),
            household_id=self.household_id,
            session_id=UUID(int=3),
            email="o@e.com",
        )

    def post(self, pdf: bytes, **form: str) -> httpx.Response:
        response: httpx.Response = TestClient(app).post(
            "/parse",
            files={"file": ("statement-4111-1111-1111-1234.pdf", pdf)},
            data={"accountId": str(self.account_id), **form},
            headers=ORIGIN,
        )
        return response

    def counts(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            found = {}
            for table in ("document", "import_batch", "staged_transaction"):
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
                found[table] = cur.fetchone()[0]  # type: ignore[index]
            return found


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    fixture = Fixture(conn)
    app.dependency_overrides[auth.require_owner] = fixture.owner
    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            _clean(cur)
        conn.commit()
        conn.close()


def test_a_dry_run_returns_the_summary_and_writes_nothing(live: Fixture) -> None:
    response = live.post(_statement())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["staged"] is False
    assert body["batchId"] is None and body["documentId"] is None
    assert body["reconciliation"]["status"] == "matched"
    assert body["rowsExtracted"] == 2
    assert body["counts"] == {"new": 2}
    assert live.counts() == {"document": 0, "import_batch": 0, "staged_transaction": 0}


def test_money_crosses_the_wire_as_a_string_never_a_json_number(live: Fixture) -> None:
    """`api-contracts.html` §6. A JSON number would be an IEEE-754 float by the time React saw
    it, and no check downstream could tell that the precision had already gone."""
    body = live.post(_statement()).json()

    amounts = [row["amount"] for row in body["rows"]]
    assert amounts == ["-10.0000", "500.0000"]
    assert all(isinstance(value, str) for value in amounts)
    assert isinstance(body["reconciliation"]["declaredClosing"], str)
    assert body["reconciliation"]["declaredClosing"] == "1490.0000"


def test_staging_returns_the_batch_and_writes_it(live: Fixture) -> None:
    body = live.post(_statement(), stage="true").json()

    assert body["staged"] is True
    assert body["batchId"] is not None
    assert body["documentId"] is not None
    assert live.counts() == {"document": 1, "import_batch": 1, "staged_transaction": 2}


def test_a_statement_that_does_not_reconcile_is_a_200_carrying_its_verdict(
    live: Fixture,
) -> None:
    """Spec §7.5: blocking conditions are not HTTP errors. The panel needs the delta and the
    divergent row, and a `detail` string could carry neither."""
    response = live.post(_statement(closing="1,500.00"), stage="true")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["staged"] is False, "asked to stage, and it did not"
    assert body["reconciliation"]["status"] == "mismatched"
    assert body["reconciliation"]["delta"] == "10.0000"
    assert live.counts() == {"document": 0, "import_batch": 0, "staged_transaction": 0}


def test_an_unidentifiable_statement_names_the_layouts_and_their_scores(live: Fixture) -> None:
    """`api-contracts.html` §4 asks the picker to list the layouts with their scores."""
    blank = build_pdf([Placement(page=0, x=20, y=760, text="Some Other Institution")])

    response = live.post(blank)

    assert response.status_code == 422
    assert "Acme current account (0.00)" in response.json()["detail"]


def test_no_response_ever_echoes_the_uploaded_filename_or_its_digits(live: Fixture) -> None:
    """Spec §5.6: the filename carries a card number and the unmasked value exists only as a
    local variable. Asserted on the whole body, not on one field, because a leak would arrive
    through whichever field nobody thought to check."""
    for form in ({}, {"stage": "true"}):
        response = live.post(_statement(), **form)
        assert "4111" not in response.text
        assert "statement-4111-1111-1111-1234.pdf" not in response.text


def _many_rows(count: int) -> bytes:
    """`count` rows of +1.00 each, with a running balance that walks and a closing that agrees.

    Built rather than written out because the property under test is a bound of fifty, and a
    fixture that stops at two can never reach it.
    """
    placements = [
        Placement(page=0, x=20, y=760, text="Acme Bank Statement"),
        Placement(page=0, x=20, y=755, text="Opening Balance 1,000.00"),
        Placement(page=0, x=40, y=740, text="Date"),
        Placement(page=0, x=140, y=740, text="Description"),
        Placement(page=0, x=420, y=740, text="Amount"),
        Placement(page=0, x=500, y=740, text="Balance"),
    ]
    top = 730.0
    for index in range(count):
        top -= 10.0
        placements += [
            Placement(page=0, x=40, y=top, text="01/03/2026"),
            Placement(page=0, x=140, y=top, text=f"ROW {index}"),
            Placement(page=0, x=420, y=top, text="1.00"),
            Placement(page=0, x=500, y=top, text=f"{1000 + index + 1}.00"),
        ]
    placements.append(
        Placement(page=0, x=20, y=top - 20, text=f"Closing Balance {1000 + count}.00")
    )
    return build_pdf(placements)


def test_an_unauthenticated_parse_is_refused(live: Fixture) -> None:
    """ADR-0013/0016: `require_owner()` is the wall, and `/parse` writes to three tables and
    returns household financial data. `test_health.py` enumerates every route's dependency, but
    the refusal itself is worth asserting here the way `test_layouts.py` does for its own."""
    app.dependency_overrides.clear()
    try:
        response = TestClient(app).post(
            "/parse", files={"file": ("s.pdf", _statement())}, headers=ORIGIN
        )
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
    assert response.status_code == 401


def test_a_missing_account_hash_salt_refuses_naming_the_variable(
    live: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLAUDE.md: a missing salt refuses the operation naming the variable rather than
    proceeding with an empty value.

    Returning "no account matched" instead would report a misconfigured deployment as a fact
    about the owner's data — and send them to create an account they already hold, which is the
    one action that cannot help.
    """
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)

    response = TestClient(app).post(
        "/parse",
        files={"file": ("s.pdf", _statement())},
        data={},  # no accountId, so the statement's own number must be resolved
        headers=ORIGIN,
    )

    assert response.status_code == 503
    assert "ACCOUNT_HASH_SALT" in response.json()["detail"]


def test_a_long_digit_run_in_a_description_never_reaches_the_response(live: Fixture) -> None:
    """Spec §5.6 keeps a digit run of seven or more out of every response, whichever field it
    sat in — and statement narration is where a card number actually appears.

    `POST /layouts/propose` already screens its own preview rows this way; a preview that did
    not would be the one surface in the milestone that returns the document's text verbatim.
    The typed fields must survive: the preview exists to show that the columns landed right.
    """
    response = live.post(_statement(description="PAYMENT CARD 4111 1111 1111 1111"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert "4111" not in response.text
    assert "1111" not in response.text
    assert "[redacted]" in body["rows"][1]["descriptionRaw"]
    assert body["rows"][1]["descriptionRaw"].startswith("PAYMENT CARD")
    # `referenceRaw` is document text too, and it is the field a bank reference lands in. Without
    # a reference band on the fixture layout this is None on every row and dropping its screen
    # would leave the suite green.
    assert body["rows"][0]["referenceRaw"] == "REF-[redacted]", "the reference is screened too"
    assert "9911" not in response.text, "and its digit run is what went"
    # The figures are typed and are what the preview is for.
    assert [row["amount"] for row in body["rows"]] == ["-10.0000", "500.0000"]
    assert body["rows"][1]["bookedDate"] == "2026-03-02"


def test_the_preview_is_capped_at_fifty_rows(live: Fixture) -> None:
    """Spec §4.3: "the first 50 normalized rows for preview". The cap is what keeps a 900-row
    statement from returning a megabyte to a screen that shows a dozen lines."""
    response = live.post(_many_rows(60))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rowsExtracted"] == 60, "every row is counted"
    assert len(body["rows"]) == 50, "and fifty are shown"
    assert body["rows"][0]["sourceRowNumber"] == 1
    assert body["rows"][49]["sourceRowNumber"] == 50


def test_the_duplicate_refusal_names_the_batch_the_screen_links_to(live: Fixture) -> None:
    """`api-contracts.html` §4 asks for "Already imported on 3 Aug 2026" *with a link to that
    batch*. Asserting only the year would survive the batch id being dropped."""
    first = live.post(_statement(), stage="true").json()

    response = live.post(_statement(), stage="true")

    assert response.status_code == 409
    assert first["batchId"] in response.json()["detail"]


def test_a_statements_own_account_number_resolves_the_account(live: Fixture) -> None:
    """The whole reason the account lookup is inverted through a resolver (spec §8).

    With no `accountId` the statement's own capture is hashed with `ACCOUNT_HASH_SALT` and
    matched against `account.account_number_hash` — the salted digest is the stored identity,
    and the number itself is on the never-store list. Every other test supplies `accountId`,
    so without this one the path the inversion exists for is never executed at all.
    """
    response = TestClient(app).post(
        "/parse", files={"file": ("s.pdf", _statement())}, headers=ORIGIN
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountId"] == str(live.account_id), "resolved from the statement, not passed in"
    assert ACCOUNT_NUMBER not in response.text, "and the number itself never comes back"


def test_a_number_matching_no_account_says_so_without_echoing_it(live: Fixture) -> None:
    """The other half of the resolver: a genuine miss, distinguished from a missing salt."""
    other = _statement().replace(b"1234567890", b"9876543210")

    response = TestClient(app).post("/parse", files={"file": ("s.pdf", other)}, headers=ORIGIN)

    assert response.status_code == 422
    assert "not one this household holds" in response.json()["detail"]
    assert "9876543210" not in response.text


def test_the_statements_own_account_number_is_screened_by_its_capture(live: Fixture) -> None:
    """The redactor is seeded with the account-number capture, not just the generic digit rule.

    A long number is caught either way, so a fixture using one proves nothing about the seeding.
    This account number is short enough that `IDENTIFIER_RUN`'s "more than four digits" rule
    leaves it alone — only the capture the pipeline extracted removes it. That is what stops a
    statement quoting its own account number in a narration line from returning it.
    """
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE account SET account_number_hash = %s WHERE id = %s",
            (_account_hash("4021"), live.account_id),
        )
    live.conn.commit()

    response = TestClient(app).post(
        "/parse",
        files={
            "file": (
                "s.pdf",
                _statement(description="TRANSFER TO 4021 THANKS").replace(
                    b"Account Number 1234567890", b"Account Number 4021      "
                ),
            )
        },
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountId"] == str(live.account_id), "resolved by the short number"
    assert "4021" not in body["rows"][1]["descriptionRaw"], "the capture screened it"
    assert "[redacted]" in body["rows"][1]["descriptionRaw"]


def test_a_currency_column_that_is_not_a_code_fails_its_row_rather_than_the_request(
    live: Fixture,
) -> None:
    """`staged_transaction.original_currency` is CHAR(3) and `StagedTransaction` says so.

    A mis-mapped band — or an issuer spelling the word out — puts `EURO` there. Refused in
    normalization the owner sees a named row failure on the dry run; refused inside the staging
    transaction it is a pydantic validator, whose message quotes the cell back into a 500 and
    into the log (spec §5.6 forbids exactly that).
    """
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET columns = columns || %s::jsonb WHERE id = %s""",
            (
                psycopg.types.json.Json(
                    [
                        {"field": "original_amount", "x_min": 700.0, "x_max": 780.0},
                        {"field": "original_currency", "x_min": 780.0, "x_max": 880.0},
                    ]
                ),
                LAYOUT_ID,
            ),
        )
    live.conn.commit()

    spelled_out = build_pdf(
        [
            Placement(page=0, x=20, y=760, text="Acme Bank Statement"),
            Placement(page=0, x=20, y=750, text="Account Number 1234567890"),
            Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
            Placement(page=0, x=40, y=670, text="01/03/2026"),
            Placement(page=0, x=140, y=670, text="COFFEE SHOP"),
            Placement(page=0, x=420, y=670, text="-10.00"),
            Placement(page=0, x=500, y=670, text="990.00"),
            Placement(page=0, x=710, y=670, text="-2.50"),
            Placement(page=0, x=800, y=670, text="EURO"),
            Placement(page=0, x=20, y=600, text="Closing Balance 990.00"),
        ]
    )

    response = live.post(spelled_out, stage="true")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["staged"] is False, "a batch missing a row does not stage"
    assert [f["reason"] for f in body["failures"]] == [
        "original_currency is not a three-letter code"
    ]
    assert "EURO" not in response.text, "the refusal names the field, never the cell"
