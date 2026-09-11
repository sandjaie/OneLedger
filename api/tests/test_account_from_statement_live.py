"""Slice 1.8: an unresolved synthetic statement can create or adopt its account."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from oneledger import auth
from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf
from .live_database import connect_to_test_database

ORIGIN = {"Origin": "http://localhost:3000"}
SALT = "account-proposal-test-salt"
LAYOUT_ID = UUID("77777777-7777-7777-7777-777777777777")
ACCOUNT_NUMBER = "2468013579"


def _statement(
    number: str = ACCOUNT_NUMBER,
    *,
    amount: str = "-10.00",
    description: str = "SYNTHETIC PURCHASE",
    institution: str = "Proposal Test Institution",
) -> bytes:
    return build_pdf(
        [
            Placement(page=0, x=20, y=760, text="Synthetic Account Statement"),
            Placement(page=0, x=20, y=775, text=f"Issuer {institution}"),
            Placement(page=0, x=20, y=745, text=f"Account Number {number}"),
            Placement(page=0, x=20, y=730, text="Currency AED"),
            Placement(page=0, x=20, y=715, text="Opening Balance 100.00"),
            Placement(page=0, x=20, y=700, text="Period 01/04/2026 to 01/04/2026"),
            Placement(page=0, x=30, y=680, text="Date"),
            Placement(page=0, x=140, y=680, text="Description"),
            Placement(page=0, x=420, y=680, text="Amount"),
            Placement(page=0, x=500, y=680, text="Balance"),
            Placement(page=0, x=30, y=650, text="01/04/2026"),
            Placement(page=0, x=140, y=650, text=description),
            Placement(page=0, x=420, y=650, text=amount),
            Placement(page=0, x=500, y=650, text="90.00"),
            Placement(page=0, x=20, y=610, text="Closing Balance 90.00"),
        ]
    )


class Fixture:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn
        with conn.cursor() as cur:
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute("DELETE FROM statement_layout WHERE id = %s", (LAYOUT_ID,))
            cur.execute(
                """DELETE FROM account_identifier ai USING account a
                     WHERE ai.account_id = a.id AND a.name LIKE 'proposal test%'"""
            )
            cur.execute("DELETE FROM account WHERE name LIKE 'proposal test%'")
            cur.execute("DELETE FROM institution WHERE name = 'Proposal Test Institution'")
            cur.execute(
                """SELECT h.id, h.base_currency, p.id
                     FROM household h
                     JOIN person p ON p.household_id = h.id AND p.is_self
                    ORDER BY h.created_at LIMIT 1"""
            )
            row = cur.fetchone()
            if row is None:
                pytest.skip("no seeded household with a self person")
            self.household_id, self.currency, self.person_id = row
            cur.execute(
                """INSERT INTO institution
                       (household_id, name, short_name, country, institution_type)
                     VALUES (%s, 'Proposal Test Institution', 'Proposal Test', 'ZZ', 'bank')
                     RETURNING id""",
                (self.household_id,),
            )
            institution = cur.fetchone()
            assert institution is not None
            self.institution_id = institution[0]
            cur.execute(
                """INSERT INTO statement_layout
                       (id, household_id, institution_id, account_type, name, version,
                        fingerprints, columns, date_format, sign_convention, inverts_sign,
                        chronological_order, row_start_rule, opening_balance_pattern,
                        closing_balance_pattern, has_running_balance, metadata_patterns)
                     VALUES (%s, %s, %s, 'savings', 'proposal test layout', 1, %s, %s,
                             'dd/MM/yyyy', 'signed', false, 'ascending', 'has_date_and_amount',
                             %s, %s, true, %s)""",
                (
                    LAYOUT_ID,
                    self.household_id,
                    self.institution_id,
                    ["Synthetic Account Statement"],
                    psycopg.types.json.Json(
                        [
                            {"field": "booked_date", "x_min": 20, "x_max": 120},
                            {"field": "description", "x_min": 120, "x_max": 400},
                            {"field": "amount", "x_min": 400, "x_max": 480},
                            {"field": "running_balance", "x_min": 480, "x_max": 580},
                        ]
                    ),
                    r"Opening\s*Balance\s*([0-9,.]+)",
                    r"Closing\s*Balance\s*([0-9,.]+)",
                    psycopg.types.json.Json(
                        {
                            "institution_name": (
                                r"Issuer\s*(Proposal Test Institution|"
                                r"Unseen Test Institution)"
                            ),
                            "account_number": r"Account\s*Number\s*([0-9]+)",
                            "currency": r"Currency\s*([A-Z]{3})",
                            "period_start": r"Period\s*([0-9/]+)\s*to",
                            "period_end": r"Period\s*[0-9/]+\s*to\s*([0-9/]+)",
                        }
                    ),
                ),
            )
        conn.commit()

    def owner(self) -> auth.OwnerSession:
        return auth.OwnerSession(
            user_id=UUID(int=1),
            household_id=self.household_id,
            session_id=UUID(int=2),
            email="owner@example.test",
        )

    def post_parse(
        self,
        *,
        stage: bool = False,
        institution: str = "Proposal Test Institution",
        account_id: UUID | None = None,
    ) -> Response:
        form = {"stage": str(stage).lower()}
        if account_id is not None:
            form["accountId"] = str(account_id)
        return cast(
            Response,
            TestClient(app).post(
                "/parse",
                files={"file": ("statement.pdf", _statement(institution=institution))},
                data=form,
                headers=ORIGIN,
            ),
        )

    def counts(self) -> tuple[int, ...]:
        with self.conn.cursor() as cur:
            values = []
            for table in (
                "account",
                "document",
                "import_batch",
                "staged_transaction",
                "fx_rate",
            ):
                predicate = " WHERE name LIKE 'proposal test%'" if table == "account" else ""
                cur.execute(f"SELECT count(*) FROM {table}{predicate}")  # noqa: S608
                count = cur.fetchone()
                assert count is not None
                values.append(count[0])
        return tuple(values)


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fixture]:
    monkeypatch.setenv("DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setenv("ACCOUNT_HASH_SALT", SALT)
    conn = connect_to_test_database()
    fixture = Fixture(conn)
    app.dependency_overrides[auth.require_owner] = fixture.owner
    try:
        yield fixture
    finally:
        app.dependency_overrides.clear()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE staged_transaction, import_batch, document, transaction CASCADE")
            cur.execute("DELETE FROM statement_layout WHERE id = %s", (LAYOUT_ID,))
            cur.execute(
                """DELETE FROM account_identifier ai USING account a
                     WHERE ai.account_id = a.id AND a.name LIKE 'proposal test%'"""
            )
            cur.execute("DELETE FROM account WHERE name LIKE 'proposal test%'")
            cur.execute("DELETE FROM institution WHERE id = %s", (fixture.institution_id,))
            cur.execute("DELETE FROM fx_rate")
        conn.commit()
        conn.close()


def _create_confirmed_account(live: Fixture, name: str) -> Response:
    proposal = live.post_parse().json()["accountProposal"]
    return cast(
        Response,
        TestClient(app).post(
            "/parse/account",
            files={"file": ("statement.pdf", _statement())},
            data={
                "account": json.dumps(
                    {
                        "name": name,
                        "institutionId": proposal["institutionId"],
                        "accountType": proposal["accountType"],
                        "currency": proposal["currency"],
                        "country": proposal["country"],
                        "openingBalance": proposal["openingBalance"],
                        "openingBalanceDate": proposal["openingBalanceDate"],
                        "openingBalanceNote": proposal["openingBalanceNote"],
                    }
                )
            },
            headers=ORIGIN,
        ),
    )


@pytest.mark.parametrize("stage", [False, True])
def test_unknown_account_is_a_read_only_prefilled_proposal(
    live: Fixture, caplog: pytest.LogCaptureFixture, stage: bool
) -> None:
    before = live.counts()
    response = live.post_parse(stage=stage)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountId"] is None and body["staged"] is False
    assert body["rows"] == []
    assert body["accountProposal"] == {
        "accountNumberMasked": "****3579",
        "suggestedAccounts": [],
        "name": "Proposal Test",
        "accountType": "savings",
        "institutionId": str(live.institution_id),
        "institutionResolution": {
            "status": "matched",
            "proposedName": "Proposal Test Institution",
            "country": None,
            "candidates": [
                {
                    "id": str(live.institution_id),
                    "name": "Proposal Test Institution",
                    "shortName": "Proposal Test",
                    "country": "ZZ",
                    "institutionType": "bank",
                }
            ],
        },
        "country": "ZZ",
        "currency": "AED",
        "openingBalance": "100.0000",
        "openingBalanceIsDerived": False,
        "openingBalanceDate": "2026-04-01",
        "openingBalanceNote": (
            "Declared opening balance of the statement for 2026-04-01 to 2026-04-01"
        ),
        "creditLimit": None,
    }
    assert ACCOUNT_NUMBER not in response.text
    assert all(ACCOUNT_NUMBER not in message for message in caplog.messages)
    assert live.counts() == before


def test_statement_issuer_no_match_proposes_confirmation_without_using_layout_institution(
    live: Fixture,
) -> None:
    before = live.counts()

    response = live.post_parse(institution="Unseen Test Institution")

    assert response.status_code == 200, response.text
    proposal = response.json()["accountProposal"]
    assert proposal["institutionId"] is None
    assert proposal["name"] == "Unseen Test Institution"
    assert proposal["institutionResolution"] == {
        "status": "needs_confirmation",
        "proposedName": "Unseen Test Institution",
        "country": None,
        "candidates": [],
    }
    assert live.counts() == before


@pytest.mark.parametrize("explicit_account", [False, True])
def test_conflicting_issuer_blocks_an_existing_account_before_staging(
    live: Fixture, explicit_account: bool
) -> None:
    created = _create_confirmed_account(live, "proposal test issuer conflict")
    assert created.status_code == 201, created.text
    before = live.counts()

    response = live.post_parse(
        stage=True,
        institution="Unseen Test Institution",
        account_id=UUID(created.json()["id"]) if explicit_account else None,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["staged"] is False
    assert body["accountId"] is None
    assert body["accountProposal"]["institutionResolution"]["status"] == "needs_confirmation"
    assert live.counts() == before


def test_page_one_issuer_evidence_is_never_persisted_as_document_metadata(live: Fixture) -> None:
    created = _create_confirmed_account(live, "proposal test transient issuer")
    assert created.status_code == 201, created.text

    response = live.post_parse(stage=True)

    assert response.status_code == 200, response.text
    assert response.json()["staged"] is True
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT extracted_metadata FROM document WHERE id = %s",
            (response.json()["documentId"],),
        )
        stored = cur.fetchone()
    assert stored is not None
    assert "institution_name" not in stored[0]
    assert "Proposal Test Institution" not in json.dumps(stored[0])


@pytest.mark.parametrize(
    ("pattern", "institution", "private_value"),
    [
        (r"Account\s*Number\s*([0-9]+)", "Proposal Test Institution", ACCOUNT_NUMBER),
        (r"Issuer\s*(.+)", "٤١١١‑١١١١‑١١١١‑١١١١", "٤١١١‑١١١١‑١١١١‑١١١١"),
    ],
)
def test_an_accidental_private_institution_capture_is_redacted_from_the_proposal(
    live: Fixture, pattern: str, institution: str, private_value: str
) -> None:
    created = _create_confirmed_account(live, "proposal test hostile issuer capture")
    assert created.status_code == 201, created.text
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = jsonb_set(
                    metadata_patterns,
                    '{institution_name}',
                    to_jsonb(%s::text)
                  )
                WHERE id = %s""",
            (pattern, LAYOUT_ID),
        )
    live.conn.commit()
    before = live.counts()

    response = live.post_parse(stage=True, institution=institution)

    assert response.status_code == 200, response.text
    assert response.json()["staged"] is False
    assert private_value not in response.text
    assert "4111111111111111" not in response.text
    proposal = response.json()["accountProposal"]
    assert proposal["name"] == "[redacted]"
    assert proposal["institutionResolution"]["proposedName"] == "[redacted]"
    assert live.counts() == before


def test_conflicting_institution_matches_are_returned_as_ambiguous_without_writing(
    live: Fixture,
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO institution
                   (household_id, name, short_name, country, institution_type)
                 VALUES (%s, 'Proposal Test Institution', 'Proposal Test Other', 'ZY', 'bank')
                 RETURNING id""",
            (live.household_id,),
        )
        created = cur.fetchone()
        assert created is not None
        other_id = created[0]
    live.conn.commit()
    before = live.counts()
    try:
        response = live.post_parse()

        assert response.status_code == 200, response.text
        proposal = response.json()["accountProposal"]
        assert proposal["institutionId"] is None
        resolution = proposal["institutionResolution"]
        assert resolution["status"] == "ambiguous"
        assert {candidate["id"] for candidate in resolution["candidates"]} == {
            str(live.institution_id),
            str(other_id),
        }
        assert live.counts() == before
    finally:
        with live.conn.cursor() as cur:
            cur.execute("DELETE FROM institution WHERE id = %s", (other_id,))
        live.conn.commit()


def test_an_all_failed_row_set_still_returns_the_proposal(live: Fixture) -> None:
    before = live.counts()
    response = cast(
        Response,
        TestClient(app).post(
            "/parse",
            files={"file": ("statement.pdf", _statement(description=""))},
            data={"stage": "true"},
            headers=ORIGIN,
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json()["accountProposal"] is not None
    assert response.json()["rows"] == []
    assert response.json()["failures"]
    assert live.counts() == before


def test_confirming_creation_then_resolves_the_same_statement(live: Fixture) -> None:
    proposal = live.post_parse().json()["accountProposal"]
    account = {
        "name": "proposal test savings",
        "institutionId": proposal["institutionId"],
        "accountType": proposal["accountType"],
        "currency": proposal["currency"],
        "country": proposal["country"],
        "openingBalance": proposal["openingBalance"],
        "openingBalanceDate": proposal["openingBalanceDate"],
        "openingBalanceNote": proposal["openingBalanceNote"],
    }
    created = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )

    assert created.status_code == 201, created.text
    assert created.json()["accountNumberMasked"] == "****3579"
    assert ACCOUNT_NUMBER not in created.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT full_value, normalized_value, provenance, source_sha256,
                      source_layout_id, source_layout_version, source_capture_key
                 FROM account_identifier
                WHERE account_id = %s AND is_active""",
            (created.json()["id"],),
        )
        assert cur.fetchone() == (
            ACCOUNT_NUMBER,
            ACCOUNT_NUMBER,
            "statement",
            hashlib.sha256(_statement()).hexdigest(),
            LAYOUT_ID,
            1,
            "account_number",
        )
    resolved = live.post_parse()
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["accountId"] == created.json()["id"]
    assert resolved.json()["accountProposal"] is None

    duplicate = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )
    assert duplicate.status_code == 409
    assert "proposal test savings" in duplicate.json()["detail"]


def test_a_matching_hashless_account_is_suggested_and_can_adopt(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, name, account_type, institution_id,
                    currency, country, opening_balance_date)
                 VALUES (%s, %s, 'proposal test adopted', 'savings', %s, %s, 'ZZ',
                         DATE '2026-01-01') RETURNING id""",
            (live.household_id, live.person_id, live.institution_id, live.currency),
        )
        created = cur.fetchone()
        assert created is not None
        account_id = created[0]
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix, provenance)
                 VALUES (%s, %s, 'bank_account', '****3579', '3579', 'owner_entered')""",
            (live.household_id, account_id),
        )
    live.conn.commit()

    proposal = live.post_parse().json()["accountProposal"]
    assert proposal["suggestedAccounts"] == [
        {
            "id": str(account_id),
            "name": "proposal test adopted",
            "accountNumberMasked": "****3579",
        }
    ]
    adopted = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"accountId": str(account_id)},
        headers=ORIGIN,
    )
    assert adopted.status_code == 200, adopted.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT full_value, normalized_value, provenance, source_sha256,
                      source_layout_id, source_layout_version, source_capture_key
                 FROM account_identifier
                WHERE account_id = %s AND is_active""",
            (account_id,),
        )
        identity = cur.fetchone()
        assert identity == (
            ACCOUNT_NUMBER,
            ACCOUNT_NUMBER,
            "statement",
            hashlib.sha256(_statement()).hexdigest(),
            LAYOUT_ID,
            1,
            "account_number",
        )
    assert live.post_parse().json()["accountId"] == str(account_id)


def test_card_creation_and_adoption_store_the_mask_only(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'credit_card' WHERE id = %s",
            (LAYOUT_ID,),
        )
    live.conn.commit()
    account = {
        "name": "proposal test card",
        "institutionId": str(live.institution_id),
        "accountType": "credit_card",
        "currency": "AED",
        "country": "ZZ",
        "openingBalance": "-100.00",
        "openingBalanceDate": "2026-04-01",
        "creditLimit": "1000.00",
    }
    created = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement("3579"))},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )

    assert created.status_code == 201, created.text
    assert created.json()["accountNumberMasked"] == "****3579"
    adopted = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement("3579"))},
        data={"accountId": created.json()["id"]},
        headers=ORIGIN,
    )
    assert adopted.status_code == 200, adopted.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT masked_value, full_value FROM account_identifier
                WHERE account_id = %s AND is_active""",
            (created.json()["id"],),
        )
        identity = cur.fetchone()
        assert identity == ("****3579", None)


def test_custom_layout_masked_bank_capture_remains_mask_only(live: Fixture) -> None:
    masked_source = "XXXX3579"
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = jsonb_set(
                        metadata_patterns, '{account_number}', to_jsonb(%s::text))
                WHERE id = %s""",
            (r"Account\s*Number\s*([X0-9]+)", LAYOUT_ID),
        )
    live.conn.commit()
    account = {
        "name": "proposal test masked savings",
        "institutionId": str(live.institution_id),
        "accountType": "savings",
        "currency": "AED",
        "country": "ZZ",
        "openingBalance": "100.00",
        "openingBalanceDate": "2026-04-01",
    }

    created = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement(masked_source))},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )

    assert created.status_code == 201, created.text
    assert created.json()["identifiers"][0]["canReveal"] is False
    assert masked_source not in created.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT masked_value, full_value, normalized_value
                 FROM account_identifier WHERE account_id = %s AND is_active""",
            (created.json()["id"],),
        )
        assert cur.fetchone() == ("****3579", None, None)


def test_custom_layout_complete_card_capture_keeps_pre_8c3_mask_only_behavior(
    live: Fixture,
) -> None:
    complete_card = "4111111111111111"
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'credit_card' WHERE id = %s",
            (LAYOUT_ID,),
        )
    live.conn.commit()
    account = {
        "name": "proposal test complete card",
        "institutionId": str(live.institution_id),
        "accountType": "credit_card",
        "currency": "AED",
        "country": "ZZ",
        "openingBalance": "-100.00",
        "openingBalanceDate": "2026-04-01",
        "creditLimit": "1000.00",
    }

    created = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement(complete_card))},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )

    assert created.status_code == 201, created.text
    assert created.json()["accountNumberMasked"] == "****1111"
    assert created.json()["identifiers"][0]["canReveal"] is False
    assert complete_card not in created.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT masked_value, full_value, normalized_value
                 FROM account_identifier WHERE account_id = %s AND is_active""",
            (created.json()["id"],),
        )
        assert cur.fetchone() == ("****1111", None, None)


def test_v28_card_mask_is_suggested_and_blocks_a_duplicate_create(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'credit_card' WHERE id = %s",
            (LAYOUT_ID,),
        )
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, name, account_type, currency, country,
                    opening_balance_date, credit_limit)
                 VALUES (%s, %s, 'proposal test legacy card', 'credit_card', %s, 'ZZ',
                         DATE '2026-01-01', 1000) RETURNING id""",
            (live.household_id, live.person_id, live.currency),
        )
        created = cur.fetchone()
        assert created is not None
        account_id = created[0]
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, provenance)
                 VALUES (%s, %s, 'card', 'XXXX3579', 'legacy_migration')""",
            (live.household_id, account_id),
        )
    live.conn.commit()

    parsed = TestClient(app).post(
        "/parse",
        files={"file": ("statement.pdf", _statement("3579"))},
        headers=ORIGIN,
    )
    assert parsed.status_code == 200, parsed.text
    proposal = parsed.json()["accountProposal"]
    assert proposal["suggestedAccounts"] == [
        {
            "id": str(account_id),
            "name": "proposal test legacy card",
            "accountNumberMasked": "XXXX3579",
        }
    ]
    duplicate = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement("3579"))},
        data={
            "account": json.dumps(
                {
                    "name": "proposal test duplicate card",
                    "institutionId": str(live.institution_id),
                    "accountType": "credit_card",
                    "currency": "AED",
                    "country": "ZZ",
                    "openingBalance": "-100.00",
                    "openingBalanceDate": "2026-04-01",
                    "creditLimit": "1000.00",
                }
            )
        },
        headers=ORIGIN,
    )
    assert duplicate.status_code == 409
    assert "proposal test legacy card" in duplicate.json()["detail"]


def test_concurrent_mask_only_creates_make_one_account(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'credit_card' WHERE id = %s",
            (LAYOUT_ID,),
        )
        cur.execute(
            """CREATE OR REPLACE FUNCTION proposal_test_delay_account_insert()
                 RETURNS trigger LANGUAGE plpgsql AS $$
                 BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$"""
        )
        cur.execute("DROP TRIGGER IF EXISTS proposal_test_delay_insert ON account")
        cur.execute(
            """CREATE TRIGGER proposal_test_delay_insert BEFORE INSERT ON account
                 FOR EACH ROW WHEN (NEW.name = 'proposal test race card')
                 EXECUTE FUNCTION proposal_test_delay_account_insert()"""
        )
    live.conn.commit()
    account = {
        "name": "proposal test race card",
        "institutionId": str(live.institution_id),
        "accountType": "credit_card",
        "currency": "AED",
        "country": "ZZ",
        "openingBalance": "-100.00",
        "openingBalanceDate": "2026-04-01",
        "creditLimit": "1000.00",
    }
    rendezvous = Barrier(2)

    def create() -> Response:
        rendezvous.wait()
        return cast(
            Response,
            TestClient(app).post(
                "/parse/account",
                files={"file": ("statement.pdf", _statement("3579"))},
                data={"account": json.dumps(account)},
                headers=ORIGIN,
            ),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [future.result() for future in [pool.submit(create), pool.submit(create)]]
        assert sorted(response.status_code for response in responses) == [201, 409]
        with live.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM account WHERE name = 'proposal test race card'")
            assert cur.fetchone() == (1,)
    finally:
        with live.conn.cursor() as cur:
            cur.execute("DROP TRIGGER IF EXISTS proposal_test_delay_insert ON account")
            cur.execute("DROP FUNCTION IF EXISTS proposal_test_delay_account_insert()")
        live.conn.commit()


def test_concurrent_adoption_cannot_overwrite_a_confirmed_identity(live: Fixture) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, name, account_type, currency, country,
                    opening_balance_date)
                 VALUES (%s, %s, 'proposal test adoption race', 'savings', %s, 'ZZ',
                         DATE '2026-01-01') RETURNING id""",
            (live.household_id, live.person_id, live.currency),
        )
        created = cur.fetchone()
        assert created is not None
        account_id = created[0]
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix, provenance)
                 VALUES (%s, %s, 'bank_account', '****3579', '3579', 'owner_entered')""",
            (live.household_id, account_id),
        )
    live.conn.commit()
    rendezvous = Barrier(2)

    def adopt(number: str) -> Response:
        rendezvous.wait()
        return cast(
            Response,
            TestClient(app).post(
                "/parse/account",
                files={"file": ("statement.pdf", _statement(number))},
                data={"accountId": str(account_id)},
                headers=ORIGIN,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [
            future.result()
            for future in [pool.submit(adopt, "1111113579"), pool.submit(adopt, "2222223579")]
        ]
    assert sorted(response.status_code for response in responses) == [200, 409]
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT full_value FROM account_identifier
                WHERE account_id = %s AND is_active""",
            (account_id,),
        )
        stored = cur.fetchone()
        assert stored is not None and stored[0] is not None


def test_confirmation_maps_an_unknown_account_type_to_422(live: Fixture) -> None:
    account = {
        "name": "proposal test invalid",
        "accountType": "not_an_account_type",
        "currency": "AED",
        "country": "ZZ",
        "openingBalance": "0.00",
        "openingBalanceDate": "2026-04-01",
    }
    response = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )

    assert response.status_code == 422
    assert ACCOUNT_NUMBER not in response.text


def test_confirmation_rejects_identity_fields_and_an_ambiguous_action(live: Fixture) -> None:
    invalid_identity = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"account": json.dumps({"accountNumberMasked": "****3579"})},
        headers=ORIGIN,
    )
    neither = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        headers=ORIGIN,
    )
    both = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"account": "{}", "accountId": str(UUID(int=7))},
        headers=ORIGIN,
    )

    assert invalid_identity.status_code == 422
    assert neither.status_code == 422
    assert both.status_code == 422
    assert ACCOUNT_NUMBER not in invalid_identity.text + neither.text + both.text


def test_account_confirmation_is_owner_only_and_origin_checked(live: Fixture) -> None:
    app.dependency_overrides.clear()
    unauthenticated = TestClient(app).post(
        "/parse/account", files={"file": ("statement.pdf", _statement())}
    )
    app.dependency_overrides[auth.require_owner] = live.owner
    foreign = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"accountId": str(UUID(int=9))},
        headers={"Origin": "https://not-the-owner.example", "Cookie": "session=fake"},
    )

    assert unauthenticated.status_code == 401
    assert foreign.status_code == 403


def test_account_confirmation_openapi_declares_both_success_statuses() -> None:
    responses = app.openapi()["paths"]["/parse/account"]["post"]["responses"]
    assert "200" in responses
    assert "201" in responses
