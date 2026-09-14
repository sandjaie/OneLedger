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
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from oneledger import auth
from oneledger.ingest import storage as ingest_storage
from oneledger.main import app

from .fixtures.investment_pdf import InvestmentEventLine, build_detailed_investment_pdf
from .fixtures.synthetic_pdf import Placement, build_pdf
from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database

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


def _human_readable_response_text(value: object) -> str:
    """Flatten response text while excluding opaque digests that can match a sentinel by chance."""
    found: list[str] = []

    def visit(node: object, key: str | None = None) -> None:
        if key == "dedupeHash":
            return
        if isinstance(node, str):
            found.append(node)
        elif isinstance(node, dict):
            for child_key, child in node.items():
                visit(child, str(child_key))
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return "\n".join(found)


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
    """See `tests/live_database.py`: the test database, and only the test database."""
    return connect_to_test_database()


def _clean(cur: psycopg.Cursor[Any]) -> None:
    # `classification_rule` joins the list with slice 1.3: a staged batch now arrives
    # pre-classified, so a rule left behind by one test decides the rows of the next — which is
    # exactly how three tests in this file first failed.
    cur.execute(
        """TRUNCATE investment_evidence, investment_review_item,
                         investment_position_checkpoint, investment_event_draft,
                         investment_import_group, investment_import,
                         staged_transaction, import_batch, document, transaction CASCADE"""
    )
    cur.execute("DELETE FROM classification_rule")
    cur.execute("DELETE FROM statement_layout")
    cur.execute(
        """DELETE FROM account_identifier ai USING account a
             WHERE ai.account_id = a.id
               AND a.name IN ('route test', 'investment route test')"""
    )
    clear_synthetic_account_valuation_periods(cur)
    cur.execute("DELETE FROM security WHERE name LIKE 'Synthetic route security%'")
    cur.execute("DELETE FROM account WHERE name IN ('route test', 'investment route test')")
    cur.execute("DELETE FROM account WHERE name = 'foreign investment route test'")
    cur.execute("DELETE FROM institution WHERE name = 'Acme Bank'")
    cur.execute("DELETE FROM fx_rate WHERE source = 'manual'")
    cur.execute("DELETE FROM app_user WHERE email LIKE 'route-other-%@example.invalid'")
    cur.execute("DELETE FROM person WHERE display_name = 'route other owner'")
    cur.execute("DELETE FROM household WHERE name = 'route other household'")


class Fixture:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        with conn.cursor() as cur:
            _clean(cur)
            # The household that has a person, not whichever row comes back first. `LIMIT 1`
            # over an unordered table picks arbitrarily, and a test elsewhere that leaves a
            # second household behind would otherwise make this fixture skip — silently, and
            # only sometimes.
            cur.execute(
                """SELECT h.id, h.base_currency, h.secondary_currency, p.id
                     FROM household h
                     JOIN person p ON p.household_id = h.id AND p.is_self
                    ORDER BY h.created_at
                    LIMIT 1"""
            )
            row = cur.fetchone()
            if row is None:
                pytest.skip("no seeded household with a person")
            self.household_id, self.base, self.secondary = row[0], row[1], row[2]
            person = (row[3],)
            cur.execute(
                "SELECT id FROM app_user WHERE household_id = %s AND person_id = %s LIMIT 1",
                (self.household_id, person[0]),
            )
            user = cur.fetchone()
            if user is None:
                cur.execute(
                    """INSERT INTO app_user (id, household_id, person_id, email, role)
                       VALUES (%s, %s, %s, %s, 'owner') RETURNING id""",
                    (
                        uuid4(),
                        self.household_id,
                        person[0],
                        f"route-owner-{self.household_id.hex[:8]}@example.invalid",
                    ),
                )
                user = cur.fetchone()
            assert user is not None
            self.user_id = user[0]
            cur.execute(
                """INSERT INTO institution
                       (household_id, name, short_name, country, institution_type)
                     VALUES (%s, 'Acme Bank', 'Acme', 'ZZ', 'bank') RETURNING id""",
                (self.household_id,),
            )
            self.institution_id: UUID = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO account (household_id, owner_person_id, name, account_type,
                                        institution_id, currency, country, opening_balance_date,
                                        current_balance)
                   VALUES (%s, %s, 'route test', 'savings', %s, %s, 'ZZ', DATE '2026-01-01', 0)
                   RETURNING id""",
                (self.household_id, person[0], self.institution_id, self.base),
            )
            self.account_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO account_identifier
                       (household_id, account_id, kind, masked_value, visible_suffix,
                        full_value, normalized_value, provenance)
                     VALUES (%s, %s, 'bank_account', '****7890', '7890', %s, %s,
                             'owner_entered')""",
                (self.household_id, self.account_id, ACCOUNT_NUMBER, ACCOUNT_NUMBER),
            )

            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
                   VALUES (%s, %s, 'investment route test', 'mutual_fund_folio', %s, 'ZZ',
                           0, DATE '2026-01-01') RETURNING id""",
                (self.household_id, person[0], self.base),
            )
            self.investment_account_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO account_identifier (
                       household_id, account_id, kind, masked_value, visible_suffix,
                       full_value, normalized_value, provenance)
                   VALUES (%s, %s, 'folio', '****A1B2', 'A1B2',
                           'SYNTHETICFOLIOA1B2', 'SYNTHETICFOLIOA1B2', 'owner_entered')""",
                (self.household_id, self.investment_account_id),
            )
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', 'Synthetic route security', %s, 'ZZ', 'equity')
                   RETURNING id""",
                (self.household_id, self.base),
            )
            self.security_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', 'Synthetic route security second', %s, 'ZZ',
                           'equity') RETURNING id""",
                (self.household_id, self.base),
            )
            self.second_security_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', 'Synthetic route security other currency', %s,
                           'ZZ', 'equity') RETURNING id""",
                (self.household_id, self.secondary),
            )
            self.other_currency_security_id = cur.fetchone()[0]  # type: ignore[index]

            self.other_household_id, other_person_id, self.other_user_id = uuid4(), uuid4(), uuid4()
            cur.execute(
                """INSERT INTO household (id, name, base_currency, secondary_currency)
                   VALUES (%s, 'route other household', %s, %s)""",
                (self.other_household_id, self.base, self.secondary),
            )
            cur.execute(
                """INSERT INTO person (id, household_id, display_name, is_self)
                   VALUES (%s, %s, 'route other owner', true)""",
                (other_person_id, self.other_household_id),
            )
            cur.execute(
                """INSERT INTO app_user (id, household_id, person_id, email, role)
                   VALUES (%s, %s, %s, %s, 'owner')""",
                (
                    self.other_user_id,
                    self.other_household_id,
                    other_person_id,
                    f"route-other-{self.other_household_id.hex[:8]}@example.invalid",
                ),
            )
            cur.execute(
                """INSERT INTO account (
                       household_id, owner_person_id, name, account_type, currency, country,
                       opening_balance, opening_balance_date)
                   VALUES (%s, %s, 'foreign investment route test', 'brokerage', %s, 'ZZ',
                           0, DATE '2026-01-01') RETURNING id""",
                (self.other_household_id, other_person_id, self.base),
            )
            self.other_account_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO security (
                       household_id, security_type, name, currency, country, asset_class)
                   VALUES (%s, 'mutual_fund', 'Synthetic route security foreign', %s, 'ZZ',
                           'equity') RETURNING id""",
                (self.other_household_id, self.base),
            )
            self.other_security_id = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                """INSERT INTO statement_layout
                     (id, household_id, account_type, name, version, fingerprints, columns,
                      date_format, sign_convention, inverts_sign, chronological_order,
                      row_start_rule, opening_balance_pattern, closing_balance_pattern,
                      has_running_balance, metadata_patterns)
                   VALUES (%s, %s, 'savings', 'Acme current account', 1, %s, %s,
                           'dd/MM/yyyy', 'signed', false, 'ascending', 'has_date_and_amount',
                           %s, %s, true, %s)""",
                (
                    LAYOUT_ID,
                    self.household_id,
                    ["Acme Bank Statement"],
                    psycopg.types.json.Json(COLUMNS),
                    OPENING,
                    CLOSING,
                    psycopg.types.json.Json(
                        {
                            "institution_name": r"(Acme\s*Bank)",
                            "account_number": ACCOUNT_NUMBER_PATTERN,
                        }
                    ),
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
            user_id=self.user_id,
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

    def investment_counts(self) -> dict[str, int]:
        with self.conn.cursor() as cur:
            found = {}
            for table in ("document", "investment_import", "investment_event_draft"):
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
                found[table] = cur.fetchone()[0]  # type: ignore[index]
            return found


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = _connect()
    try:
        # Constructed inside the try: `Fixture.__init__` truncates before it can `pytest.skip`,
        # and a skip that escaped this block would leave the connection open holding that
        # TRUNCATE's lock, blocking every test after it rather than skipping with it.
        fixture = Fixture(conn)
        app.dependency_overrides[auth.require_owner] = fixture.owner
        yield fixture
    finally:
        app.dependency_overrides.clear()
        conn.rollback()
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


def test_the_layout_lists_against_the_migrated_schema(live: Fixture) -> None:
    """V22 drops `times_used` and `last_used_at`. The `GET /layouts` unit tests mock the
    connection; this is the one that runs the route's SELECT against the schema V22 leaves."""
    response = TestClient(app).get("/layouts")

    assert response.status_code == 200, response.text
    [layout] = response.json()
    assert layout["id"] == str(LAYOUT_ID)
    assert not {"timesUsed", "lastUsedAt"} & layout.keys()


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
        Placement(page=0, x=20, y=750, text="Account Number 1234567890"),
        Placement(page=0, x=20, y=740, text="Opening Balance 1,000.00"),
        Placement(page=0, x=40, y=720, text="Date"),
        Placement(page=0, x=140, y=720, text="Description"),
        Placement(page=0, x=420, y=720, text="Amount"),
        Placement(page=0, x=500, y=720, text="Balance"),
    ]
    top = 710.0
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


def test_a_missing_legacy_hash_salt_does_not_gate_normalized_matching(
    live: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Current normalized identities remain usable without the transitional legacy salt."""
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)

    response = TestClient(app).post(
        "/parse",
        files={"file": ("s.pdf", _statement())},
        data={},  # no accountId, so the statement's own number must be resolved
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["accountId"] == str(live.account_id)


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
    readable = _human_readable_response_text(body)
    assert "4111" not in readable
    assert "1111" not in readable
    assert "[redacted]" in body["rows"][1]["descriptionRaw"]
    assert body["rows"][1]["descriptionRaw"].startswith("PAYMENT CARD")
    # `referenceRaw` is document text too, and it is the field a bank reference lands in. Without
    # a reference band on the fixture layout this is None on every row and dropping its screen
    # would leave the suite green.
    assert body["rows"][0]["referenceRaw"] == "REF-[redacted]", "the reference is screened too"
    assert "9911" not in readable, "and its digit run is what went"
    # The figures are typed and are what the preview is for.
    assert [row["amount"] for row in body["rows"]] == ["-10.0000", "500.0000"]
    assert body["rows"][1]["bookedDate"] == "2026-03-02"


def test_privacy_scan_ignores_opaque_ids_but_keeps_human_readable_fields() -> None:
    response = {
        "rows": [
            {
                "descriptionRaw": "PAYMENT CARD [redacted]",
                "dedupeHash": "opaque-digest-containing-4111-by-chance",
            }
        ]
    }

    assert "4111" not in _human_readable_response_text(response)
    response["rows"][0]["descriptionRaw"] = "PAYMENT CARD 4111"
    assert "4111" in _human_readable_response_text(response)


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

    With no `accountId` the statement's complete capture is normalized and matched against the
    active typed identifier. Every other test supplies `accountId`, so without this one the path
    the inversion exists for is never executed at all.
    """
    response = TestClient(app).post(
        "/parse", files={"file": ("s.pdf", _statement())}, headers=ORIGIN
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountId"] == str(live.account_id), "resolved from the statement, not passed in"
    assert ACCOUNT_NUMBER not in response.text, "and the number itself never comes back"


def test_a_migrated_legacy_fingerprint_remains_resolvable_until_backfill(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE account_identifier
                  SET full_value = NULL, normalized_value = NULL,
                      legacy_fingerprint = decode(%s, 'hex'),
                      legacy_fingerprint_scheme = 'legacy_sha256_v1',
                      provenance = 'legacy_migration'
                WHERE account_id = %s""",
            (_account_hash(ACCOUNT_NUMBER), live.account_id),
        )
    live.conn.commit()

    response = TestClient(app).post(
        "/parse", files={"file": ("s.pdf", _statement())}, headers=ORIGIN
    )

    assert response.status_code == 200, response.text
    assert response.json()["accountId"] == str(live.account_id)
    assert ACCOUNT_NUMBER not in response.text


def test_a_number_matching_no_account_is_proposed_without_echoing_it(live: Fixture) -> None:
    """A genuine miss is a read-only masked proposal, distinct from a missing salt."""
    other = _statement().replace(b"1234567890", b"9876543210")

    response = TestClient(app).post("/parse", files={"file": ("s.pdf", other)}, headers=ORIGIN)

    assert response.status_code == 200, response.text
    assert response.json()["accountId"] is None
    assert response.json()["identityResolution"]["accountNumberMasked"] == "****3210"
    assert response.json()["rows"] == []
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
            """UPDATE statement_layout
                  SET metadata_patterns = (metadata_patterns - 'account_number') || %s::jsonb
                WHERE id = %s""",
            (
                psycopg.types.json.Json({"account_number_masked": r"Account\s*Number\s*([X0-9]+)"}),
                LAYOUT_ID,
            ),
        )
    live.conn.commit()

    response = TestClient(app).post(
        "/parse",
        files={
            "file": (
                "s.pdf",
                _statement(description="TRANSFER TO 7890 THANKS").replace(
                    b"Account Number 1234567890", b"Account Number 7890      "
                ),
            )
        },
        data={"accountId": str(live.account_id)},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountId"] == str(live.account_id), "resolved by the short number"
    assert "7890" not in body["rows"][1]["descriptionRaw"], "the capture screened it"
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


def _investment_statement(currency: str, *, security: str = "SYNTHETIC-SECURITY-A") -> bytes:
    return build_detailed_investment_pdf(
        [
            InvestmentEventLine(
                "2026-03-01",
                "PURCHASE",
                "1.23456789",
                "10.123456",
                "12.4999",
                "0.0001",
                "0.0000",
                "-12.5000",
                currency,
                "PRIVATE-SOURCE-EVENT",
            )
        ],
        security_reference=security,
    )


def _two_group_investment_statement(currency: str) -> bytes:
    return build_detailed_investment_pdf(
        [
            InvestmentEventLine(
                "2026-03-01",
                "PURCHASE",
                "1.00000000",
                "10.000000",
                "10.0000",
                "0.0000",
                "0.0000",
                "-10.0000",
                currency,
                "SYNTHETIC-SOURCE-FIRST",
            ),
            InvestmentEventLine(
                "2026-03-02",
                "PURCHASE",
                "2.00000000",
                "10.000000",
                "20.0000",
                "0.0000",
                "0.0000",
                "-20.0000",
                currency,
                "SYNTHETIC-SOURCE-SECOND",
            ),
        ],
        rows_per_page=1,
        page_targets=[
            ("folio MASKED ****A1B2", "SYNTHETIC-SECURITY-FIRST"),
            ("folio MASKED ****C3D4", "SYNTHETIC-SECURITY-SECOND"),
        ],
    )


def _investment_resolutions(live: Fixture, **changes: object) -> str:
    resolution: dict[str, object] = {
        "sourceGroupNumber": 1,
        "accountId": str(live.investment_account_id),
        "securityId": str(live.security_id),
    }
    resolution.update(changes)
    return json.dumps([resolution])


def _investment_post(live: Fixture, pdf: bytes, **form: str) -> httpx.Response:
    response: httpx.Response = TestClient(app).post(
        "/investment-imports/parse",
        files={"file": ("private-4111-1111-1111-9876.pdf", pdf)},
        data=form,
        headers=ORIGIN,
    )
    return response


def test_investment_preview_is_bounded_exact_private_and_write_free(live: Fixture) -> None:
    private_security = "ＳＥＣＵＲＩＴＹ ١٢٣٤٥٦٧٨"
    response = _investment_post(live, _investment_statement(live.base, security=private_security))

    assert response.status_code == 200, response.text
    assert response.json() == {
        "supported": True,
        "staged": False,
        "importId": None,
        "status": "preview",
        "groups": [
            {
                "groupNumber": 1,
                "stageable": False,
                "status": None,
                "targets": [
                    {
                        "sourceGroupNumber": 1,
                        "accountId": None,
                        "securityId": None,
                        "accountMask": "****A1B2",
                        "securityLabel": "ＳＥＣＵＲＩＴＹ [redacted]",
                        "isin": None,
                        "securityCandidates": [],
                        "drafts": [
                            {
                                "eventDate": "2026-03-01",
                                "eventKind": "purchase",
                                "units": "1.23456789",
                                "unitRatio": None,
                                "price": "10.123456",
                                "grossAmount": "12.4999",
                                "charges": "0.0001",
                                "withheldTax": "0.0000",
                                "netAmount": "-12.5000",
                                "currency": live.base,
                                "pageNumber": 1,
                                "rowNumber": 6,
                            }
                        ],
                        "checkpoints": [],
                        "reasons": [],
                    }
                ],
                "reasons": [],
            }
        ],
        "reasons": [],
        "totalGroupCount": 1,
        "totalDraftCount": 1,
        "totalBlockerCount": 0,
        "previewTruncated": False,
        "expectedState": None,
    }
    assert private_security not in response.text
    assert "PRIVATE-SOURCE-EVENT" not in response.text
    assert "4111" not in response.text and "9876" not in response.text
    assert live.investment_counts() == {
        "document": 0,
        "investment_import": 0,
        "investment_event_draft": 0,
    }


def test_investment_preview_hint_redacts_digits_and_matches_isin_candidate(
    live: Fixture,
) -> None:
    isin = "US0000000010"
    hostile_security = f"ＦＵＮＤ ７８９ {isin}"  # fullwidth letters + digit run + embedded ISIN
    with live.conn.cursor() as cur:
        cur.execute("UPDATE security SET isin = %s WHERE id = %s", (isin, live.security_id))
    live.conn.commit()

    response = _investment_post(live, _investment_statement(live.base, security=hostile_security))

    assert response.status_code == 200, response.text
    target = response.json()["groups"][0]["targets"][0]
    assert target["isin"] == isin
    assert target["securityCandidates"] == [str(live.security_id)]
    assert target["securityLabel"] is not None
    assert isin not in target["securityLabel"]  # the digit-run redactor also screens the ISIN
    assert "[redacted]" in target["securityLabel"]
    assert "ＦＵＮＤ" in target["securityLabel"]
    assert hostile_security not in response.text
    with live.conn.cursor() as cur:
        cur.execute("UPDATE security SET isin = NULL WHERE id = %s", (live.security_id,))
    live.conn.commit()


def test_investment_resolved_preview_returns_only_uuid_and_safe_mask(live: Fixture) -> None:
    response = _investment_post(
        live,
        _investment_statement(live.base),
        resolutions=_investment_resolutions(live),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    target = body["groups"][0]["targets"][0]
    assert body["staged"] is False and body["status"] == "preview"
    assert body["groups"][0]["stageable"] is True
    assert target["accountId"] == str(live.investment_account_id)
    assert target["securityId"] == str(live.security_id)
    assert target["accountMask"] == "****A1B2"
    assert live.investment_counts()["document"] == 0


def test_investment_preview_accepts_one_resolution_and_preserves_other_target(
    live: Fixture,
) -> None:
    response = _investment_post(
        live,
        _two_group_investment_statement(live.base),
        resolutions=_investment_resolutions(live),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["staged"] is False
    assert [group["stageable"] for group in body["groups"]] == [True, False]
    resolved, unresolved = [group["targets"][0] for group in body["groups"]]
    assert resolved["accountId"] == str(live.investment_account_id)
    assert resolved["securityId"] == str(live.security_id)
    assert resolved["accountMask"] == "****A1B2"
    assert unresolved["accountId"] is None
    assert unresolved["securityId"] is None
    # Unresolved targets still surface the statement's own mask, exactly what an owner would see
    # while confirming which account this second group belongs to.
    assert unresolved["accountMask"] == "****C3D4"
    assert live.investment_counts() == {
        "document": 0,
        "investment_import": 0,
        "investment_event_draft": 0,
    }


def test_investment_routes_require_the_authenticated_owner(live: Fixture) -> None:
    app.dependency_overrides.clear()
    try:
        preview = _investment_post(live, _investment_statement(live.base))
        cancel = TestClient(app).post(f"/investment-imports/{uuid4()}/cancel", headers=ORIGIN)
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner

    assert preview.status_code == cancel.status_code == 401


def test_investment_stage_reextracts_and_identical_retry_reuses_rows(live: Fixture) -> None:
    pdf = _investment_statement(live.base)
    form = {"stage": "true", "resolutions": _investment_resolutions(live)}

    first = _investment_post(live, pdf, **form)
    retried = _investment_post(live, pdf, **form)

    assert first.status_code == 201, first.text
    assert retried.status_code == 200, retried.text
    assert first.json()["importId"] == retried.json()["importId"]
    assert first.json()["status"] == retried.json()["status"] == "staged"
    assert first.json()["staged"] is True and retried.json()["staged"] is True
    assert live.investment_counts() == {
        "document": 1,
        "investment_import": 1,
        "investment_event_draft": 1,
    }
    with live.conn.cursor() as cur:
        cur.execute("SELECT units::text, price::text, net_amount::text FROM investment_event_draft")
        assert cur.fetchone() == ("1.23456789", "10.123456", "-12.5000")


def test_investment_stage_uses_a_fixed_safe_document_label(live: Fixture) -> None:
    private_identifier = "SYNTHETICFOLIOA1B2C3D4E5F6"
    response: httpx.Response = TestClient(app).post(
        "/investment-imports/parse",
        files={"file": (f"statement-{private_identifier}.pdf", _investment_statement(live.base))},
        data={"stage": "true", "resolutions": _investment_resolutions(live)},
        headers=ORIGIN,
    )

    assert response.status_code == 201, response.text
    assert private_identifier not in response.text
    with live.conn.cursor() as cur:
        cur.execute("SELECT original_filename FROM document")
        assert cur.fetchone() == ("investment-statement.pdf",)


def test_investment_stage_response_uses_persisted_blocker_state(
    live: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from oneledger.investments.staging import StagedGroupResult, StagedImportResult
    from oneledger.routes import investment_imports

    import_id, group_id = uuid4(), uuid4()
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        investment_imports,
        "_document",
        lambda *_args, **_kwargs: StagedImportResult(
            import_id,
            "awaiting_review",
            (group_id,),
            True,
            (StagedGroupResult(1, "blocked", ("duplicate_conflict",)),),
            "synthetic-expected-state",
        ),
    )

    response = _investment_post(
        live,
        _investment_statement(live.base),
        stage="true",
        resolutions=_investment_resolutions(live),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "awaiting_review"
    assert body["groups"][0]["stageable"] is False
    assert body["groups"][0]["reasons"] == [
        {"code": "duplicate_conflict", "pageNumber": None, "rowNumber": None}
    ]
    assert body["totalBlockerCount"] == 1


def test_investment_stage_counts_persisted_blocker_beyond_preview_limit() -> None:
    from oneledger.investments.staging import (
        PreparedGroup,
        PreparedImport,
        PreparedTarget,
        StagedGroupResult,
        StagedImportResult,
    )
    from oneledger.routes.investment_imports import _response

    account_id, security_id = uuid4(), uuid4()
    groups = tuple(
        PreparedGroup(
            number,
            (PreparedTarget(number, account_id, security_id, (), (), ()),),
            (),
            True,
        )
        for number in range(1, 52)
    )
    prepared = PreparedImport(
        True,
        "synthetic_source_contract",
        "synthetic_extraction_version",
        groups,
        (),
        51,
        0,
        0,
        True,
        True,
    )
    staged = StagedImportResult(
        uuid4(),
        "awaiting_review",
        tuple(uuid4() for _ in groups),
        True,
        tuple(
            StagedGroupResult(
                number,
                "blocked" if number == 51 else "ready",
                ("duplicate_conflict",) if number == 51 else (),
            )
            for number in range(1, 52)
        ),
        "synthetic-expected-state",
    )

    response = _response(prepared, {}, {}, {}, staged)

    assert len(response.groups) == 50
    assert response.previewTruncated is True
    assert response.totalBlockerCount == 1


@pytest.mark.parametrize(
    "resolutions",
    [
        "{}",
        "not-json",
        '[{"sourceGroupNumber":true,"accountId":"00000000-0000-0000-0000-000000000001",'
        '"securityId":"00000000-0000-0000-0000-000000000002"}]',
        '[{"sourceGroupNumber":2,"accountId":"00000000-0000-0000-0000-000000000001",'
        '"securityId":"00000000-0000-0000-0000-000000000002"}]',
        '[{"sourceGroupNumber":1,"accountId":"00000000-0000-0000-0000-000000000001",'
        '"securityId":"00000000-0000-0000-0000-000000000002"},'
        '{"sourceGroupNumber":1,"accountId":"00000000-0000-0000-0000-000000000003",'
        '"securityId":"00000000-0000-0000-0000-000000000004"}]',
        '[{"sourceGroupNumber":1,"accountId":"private-value","securityId":"private-value"}]',
        '[{"sourceGroupNumber":1,"accountId":"00000000-0000-0000-0000-000000000001",'
        '"securityId":"00000000-0000-0000-0000-000000000002","extra":"PRIVATE"}]',
    ],
)
def test_investment_resolution_json_is_strict_and_never_echoed(
    live: Fixture, resolutions: str
) -> None:
    response = _investment_post(
        live, _investment_statement(live.base), stage="true", resolutions=resolutions
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "investment target resolutions are invalid"}
    assert "private-value" not in response.text and "PRIVATE" not in response.text
    assert live.investment_counts()["document"] == 0


@pytest.mark.parametrize("invalid_target", ["foreign", "wrong_kind", "wrong_currency"])
def test_investment_invalid_target_fails_before_blob_or_database_write(
    live: Fixture, tmp_path: Path, invalid_target: str
) -> None:
    pdf = _investment_statement(live.base)
    account_id, security_id = live.investment_account_id, live.security_id
    if invalid_target == "foreign":
        account_id, security_id = live.other_account_id, live.other_security_id
    elif invalid_target == "wrong_kind":
        account_id = live.account_id
    else:
        security_id = live.other_currency_security_id
    invalid = _investment_post(
        live,
        pdf,
        stage="true",
        resolutions=_investment_resolutions(
            live,
            accountId=str(account_id),
            securityId=str(security_id),
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "investment target is invalid"}
    assert live.investment_counts()["document"] == 0
    assert list(tmp_path.rglob("*.pdf")) == []


def test_investment_stale_target_retry_fails_without_changing_rows(live: Fixture) -> None:
    pdf = _investment_statement(live.base)

    accepted = _investment_post(live, pdf, stage="true", resolutions=_investment_resolutions(live))
    changed = _investment_post(
        live,
        pdf,
        stage="true",
        resolutions=_investment_resolutions(live, securityId=str(live.second_security_id)),
    )
    assert accepted.status_code == 201
    assert changed.status_code == 409
    assert changed.json() == {"detail": "investment import retry conflicts"}
    assert live.investment_counts()["investment_event_draft"] == 1


def test_investment_cancel_is_owner_scoped_idempotent_and_terminal(live: Fixture) -> None:
    pdf = _investment_statement(live.base)
    staged = _investment_post(
        live, pdf, stage="true", resolutions=_investment_resolutions(live)
    ).json()
    import_id = staged["importId"]
    client = TestClient(app)

    first = client.post(f"/investment-imports/{import_id}/cancel", headers=ORIGIN)
    second = client.post(f"/investment-imports/{import_id}/cancel", headers=ORIGIN)
    retry = _investment_post(live, pdf, stage="true", resolutions=_investment_resolutions(live))

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "cancelled"
    assert retry.status_code == 409
    assert retry.json() == {"detail": "investment import is terminal"}

    app.dependency_overrides[auth.require_owner] = lambda: auth.OwnerSession(
        live.other_user_id,
        live.other_household_id,
        uuid4(),
        "other@example.invalid",
    )
    try:
        foreign = client.post(f"/investment-imports/{import_id}/cancel", headers=ORIGIN)
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
    assert foreign.status_code == 404
    assert foreign.json() == {"detail": "investment import not found"}


def test_investment_storage_and_late_database_failures_are_atomic(
    live: Fixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from oneledger.routes import investment_imports

    pdf = _investment_statement(live.base)
    form = {"stage": "true", "resolutions": _investment_resolutions(live)}
    monkeypatch.setattr(
        ingest_storage,
        "store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ingest_storage.StorageError("investment document could not be stored")
        ),
    )
    failed_storage = _investment_post(live, pdf, **form)
    assert failed_storage.status_code == 422
    assert live.investment_counts()["document"] == 0

    monkeypatch.undo()
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        investment_imports,
        "stage_prepared_import_in_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(psycopg.IntegrityError()),
    )
    failed_database = _investment_post(live, pdf, **form)
    assert failed_database.status_code == 422
    assert failed_database.json() == {"detail": "investment import could not be staged"}
    assert live.investment_counts()["document"] == 0
    assert list(tmp_path.rglob("*.pdf")), "the only permitted residue is the inert blob"


def test_investment_retry_and_cancel_are_serialized(live: Fixture) -> None:
    pdf = _investment_statement(live.base)
    form = {"stage": "true", "resolutions": _investment_resolutions(live)}
    import_id = _investment_post(live, pdf, **form).json()["importId"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        retry_future = pool.submit(_investment_post, live, pdf, **form)
        cancel_future = pool.submit(
            TestClient(app).post,
            f"/investment-imports/{import_id}/cancel",
            headers=ORIGIN,
        )
        retry, cancelled = retry_future.result(), cancel_future.result()

    assert retry.status_code in {200, 409}
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with live.conn.cursor() as cur:
        cur.execute("SELECT status::text FROM investment_import WHERE id = %s", (import_id,))
        assert cur.fetchone() == ("cancelled",)
        cur.execute("SELECT count(*) FROM investment_event_draft")
        assert cur.fetchone() == (1,)
