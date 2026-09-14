"""Slice 1.8: an unresolved synthetic statement can create or adopt its account."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from unittest.mock import patch
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from oneledger import auth
from oneledger.ingest import pipeline
from oneledger.main import app

from .fixtures.synthetic_pdf import Placement, build_pdf
from .live_database import clear_synthetic_account_valuation_periods, connect_to_test_database

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
    second_number: str | None = None,
) -> bytes:
    placements = [
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
    if second_number is not None:
        placements.append(Placement(page=0, x=20, y=725, text=f"Account Number {second_number}"))
    return build_pdf(placements)


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
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE name LIKE 'proposal test%'")
            cur.execute(
                """DELETE FROM institution_alias
                    WHERE alias_normalized = 'unseen test institution'"""
            )
            cur.execute(
                """DELETE FROM institution
                    WHERE name IN ('Proposal Test Institution', 'New Canonical Institution')"""
            )
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
            cur.execute(
                """INSERT INTO fx_rate
                       (rate_date, base_currency, quote_currency, rate, source)
                     SELECT DATE '2026-04-01', secondary_currency, base_currency,
                            4.0000000000, 'manual'
                       FROM household WHERE id = %s
                     ON CONFLICT DO NOTHING""",
                (self.household_id,),
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
                cur.execute(f"SELECT count(*) FROM {table}{predicate}")
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
            clear_synthetic_account_valuation_periods(cur)
            cur.execute("DELETE FROM account WHERE name LIKE 'proposal test%'")
            cur.execute(
                "DELETE FROM institution_alias WHERE alias_normalized = 'unseen test institution'"
            )
            cur.execute("DELETE FROM institution WHERE name = 'New Canonical Institution'")
            cur.execute("DELETE FROM institution WHERE id = %s", (fixture.institution_id,))
            cur.execute("DELETE FROM fx_rate")
        conn.commit()
        conn.close()


def _create_confirmed_account(live: Fixture, name: str) -> Response:
    proposal = live.post_parse().json()["identityResolution"]
    return cast(
        Response,
        TestClient(app).post(
            "/parse/account",
            files={"file": ("statement.pdf", _statement())},
            data={
                "account": json.dumps(
                    {
                        "name": name,
                        "institutionId": proposal["institution"]["candidates"][0]["id"],
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


def _declare_masked_capture(live: Fixture, pattern: str = r"Account\s*Number\s*(\d{4})") -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE statement_layout
                  SET metadata_patterns = (metadata_patterns - 'account_number') || %s::jsonb
                WHERE id = %s""",
            (psycopg.types.json.Json({"account_number_masked": pattern}), LAYOUT_ID),
        )
    live.conn.commit()


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
    group_key = body["identityResolution"].pop("proposalGroupKey")
    assert group_key.startswith("pg1_")
    assert ACCOUNT_NUMBER not in group_key
    assert "accountProposal" not in body
    assert body["identityResolution"] == {
        "status": "needs_confirmation",
        "institutionAction": "reuse",
        "accountAction": "create",
        "institution": {
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
        "accountNumberMasked": "****3579",
        "candidates": [],
        "conflicts": [],
        "name": "Proposal Test",
        "accountType": "savings",
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


def test_statement_identifier_preview_reveal_is_owner_only_no_store_and_read_only(
    live: Fixture,
) -> None:
    before = live.counts()
    response = TestClient(app).post(
        "/parse/identifier/reveal",
        files={"file": ("statement.pdf", _statement())},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "status": "revealed",
        "value": ACCOUNT_NUMBER,
        "kind": "bank_account",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert live.counts() == before

    app.dependency_overrides.clear()
    try:
        unauthorized = TestClient(app).post(
            "/parse/identifier/reveal",
            files={"file": ("statement.pdf", _statement())},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides[auth.require_owner] = live.owner
    assert unauthorized.status_code == 401
    foreign_origin = TestClient(app).post(
        "/parse/identifier/reveal",
        files={"file": ("statement.pdf", _statement())},
        headers={
            "Origin": "https://attacker.example",
            "Cookie": "session=synthetic-cookie",
        },
    )
    assert foreign_origin.status_code == 403


def test_statement_identifier_preview_reveal_is_unavailable_for_a_source_mask(
    live: Fixture,
) -> None:
    _declare_masked_capture(live)
    response = TestClient(app).post(
        "/parse/identifier/reveal",
        files={"file": ("statement.pdf", _statement("3579"))},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "unavailable", "reason": "source_mask_only"}
    assert response.headers["cache-control"] == "no-store"


def test_statement_identifier_preview_reveal_refuses_multiple_captures_without_leaking(
    live: Fixture,
) -> None:
    other_number = "1357924680"
    response = TestClient(app).post(
        "/parse/identifier/reveal",
        files={
            "file": (
                "statement.pdf",
                _statement(second_number=other_number),
            )
        },
        headers=ORIGIN,
    )

    assert response.status_code == 409
    assert ACCOUNT_NUMBER not in response.text
    assert other_number not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_multiple_statement_identity_captures_never_select_or_stage_an_account(
    live: Fixture,
) -> None:
    existing = _create_confirmed_account(live, "proposal test ambiguous capture")
    assert existing.status_code == 201, existing.text
    other_number = "1357924680"
    before = live.counts()

    response = TestClient(app).post(
        "/parse",
        files={
            "file": (
                "statement.pdf",
                _statement(second_number=other_number),
            )
        },
        data={"stage": "true"},
        headers=ORIGIN,
    )

    assert response.status_code == 200, response.text
    assert response.json()["staged"] is False
    assert response.json()["accountId"] is None
    assert response.json()["identityResolution"]["status"] == "ambiguous"
    assert response.json()["identityResolution"]["proposalGroupKey"] is None
    assert ACCOUNT_NUMBER not in response.text
    assert other_number not in response.text
    assert live.counts() == before


def test_statement_issuer_no_match_proposes_confirmation_without_using_layout_institution(
    live: Fixture,
) -> None:
    before = live.counts()

    response = live.post_parse(institution="Unseen Test Institution")

    assert response.status_code == 200, response.text
    proposal = response.json()["identityResolution"]
    assert proposal["institution"]["candidates"] == []
    assert proposal["name"] == "Unseen Test Institution"
    assert proposal["institution"] == {
        "status": "needs_confirmation",
        "proposedName": "Unseen Test Institution",
        "country": None,
        "candidates": [],
    }
    assert live.counts() == before


def test_owner_corrected_institution_reuse_adds_alias_with_account_atomically(
    live: Fixture,
) -> None:
    account = {
        "name": "proposal test corrected institution",
        "institutionId": str(live.institution_id),
        "accountType": "savings",
        "currency": "AED",
        "country": "ZZ",
        "openingBalance": "100.00",
        "openingBalanceDate": "2026-04-01",
    }
    response = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement(institution="Unseen Test Institution"))},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )

    assert response.status_code == 201, response.text
    assert response.json()["institutionId"] == str(live.institution_id)
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT institution_id, alias, alias_normalized, provenance
                 FROM institution_alias
                WHERE household_id = %s AND alias_normalized = 'unseen test institution'""",
            (live.household_id,),
        )
        assert cur.fetchone() == (
            live.institution_id,
            "Unseen Test Institution",
            "unseen test institution",
            "owner_confirmed",
        )


def test_new_institution_alias_account_and_identifier_commit_together(live: Fixture) -> None:
    account = {
        "name": "proposal test first institution",
        "institutionId": None,
        "accountType": "savings",
        "currency": "AED",
        "country": "ZY",
        "openingBalance": "100.00",
        "openingBalanceDate": "2026-04-01",
    }
    response = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement(institution="Unseen Test Institution"))},
        data={
            "account": json.dumps(account),
            "institution": json.dumps(
                {
                    "name": "New Canonical Institution",
                    "shortName": "New Canonical",
                    "country": "ZY",
                    "institutionType": "bank",
                }
            ),
        },
        headers=ORIGIN,
    )

    assert response.status_code == 201, response.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT i.name, ia.alias, a.name, ai.masked_value
                 FROM account a
                 JOIN institution i ON i.id = a.institution_id
                 JOIN institution_alias ia ON ia.institution_id = i.id
                 JOIN account_identifier ai ON ai.account_id = a.id AND ai.is_active
                WHERE a.id = %s""",
            (response.json()["id"],),
        )
        assert cur.fetchone() == (
            "New Canonical Institution",
            "Unseen Test Institution",
            "proposal test first institution",
            "****3579",
        )


def test_conflicting_confirmation_rolls_back_new_institution_and_alias(live: Fixture) -> None:
    response = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement(institution="Unseen Test Institution"))},
        data={
            "account": json.dumps(
                {
                    "name": "proposal test invalid confirmation",
                    "institutionId": None,
                    "accountType": "current",
                    "currency": "AED",
                    "country": "ZY",
                    "openingBalance": "100.00",
                    "openingBalanceDate": "2026-04-01",
                }
            ),
            "institution": json.dumps(
                {
                    "name": "New Canonical Institution",
                    "country": "ZY",
                    "institutionType": "bank",
                }
            ),
        },
        headers=ORIGIN,
    )

    assert response.status_code == 409
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT
                 (SELECT count(*) FROM institution WHERE name = 'New Canonical Institution'),
                 (SELECT count(*) FROM institution_alias
                   WHERE alias_normalized = 'unseen test institution'),
                 (SELECT count(*) FROM account WHERE name = 'proposal test invalid confirmation')"""
        )
        assert cur.fetchone() == (0, 0, 0)


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
    assert body["identityResolution"]["institution"]["status"] == "needs_confirmation"
    assert live.counts() == before


@pytest.mark.parametrize(
    ("account_type", "currency", "expected_conflict"),
    [("current", "AED", "account_type"), ("savings", "USD", "currency")],
)
def test_exact_identity_with_conflicting_account_facts_writes_nothing(
    live: Fixture, account_type: str, currency: str, expected_conflict: str
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, institution_id, name, account_type,
                    currency, country, opening_balance_date)
                 VALUES (%s, %s, %s, 'proposal test conflicting facts', %s::account_type,
                         %s, 'ZZ', DATE '2026-01-01') RETURNING id""",
            (
                live.household_id,
                live.person_id,
                live.institution_id,
                account_type,
                currency,
            ),
        )
        account_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            """INSERT INTO account_identifier
                   (household_id, account_id, kind, masked_value, visible_suffix,
                    full_value, normalized_value, provenance)
                 VALUES (%s, %s, 'bank_account', '****3579', '3579', %s, %s,
                         'owner_entered')""",
            (live.household_id, account_id, ACCOUNT_NUMBER, ACCOUNT_NUMBER),
        )
    live.conn.commit()
    before = live.counts()

    response = live.post_parse(stage=True)

    assert response.status_code == 200, response.text
    assert response.json()["accountId"] is None
    assert response.json()["identityResolution"]["status"] == "ambiguous"
    assert response.json()["identityResolution"]["conflicts"] == [expected_conflict]
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
        cur.execute(
            """SELECT ai.source_document_id
                 FROM account_identifier ai
                 JOIN account a ON a.id = ai.account_id
                WHERE a.name = 'proposal test transient issuer' AND ai.is_active"""
        )
        attached = cur.fetchone()
    assert stored is not None
    assert "institution_name" not in stored[0]
    assert "Proposal Test Institution" not in json.dumps(stored[0])
    assert attached == (UUID(response.json()["documentId"]),)


def test_staging_does_not_attach_identifier_with_different_source_provenance(
    live: Fixture,
) -> None:
    created = _create_confirmed_account(live, "proposal test stale identity provenance")
    assert created.status_code == 201, created.text
    with live.conn.cursor() as cur:
        cur.execute(
            """UPDATE account_identifier
                  SET source_sha256 = repeat('0', 64)
                WHERE account_id = %s AND is_active""",
            (created.json()["id"],),
        )
    live.conn.commit()

    staged = live.post_parse(stage=True)

    assert staged.status_code == 200, staged.text
    assert staged.json()["staged"] is True
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT source_document_id FROM account_identifier WHERE account_id = %s AND is_active",
            (created.json()["id"],),
        )
        assert cur.fetchone() == (None,)


def test_staging_revalidates_identity_after_a_concurrent_account_edit(
    live: Fixture,
) -> None:
    created = _create_confirmed_account(live, "proposal test identity changed during parse")
    assert created.status_code == 201, created.text
    account_id = UUID(created.json()["id"])
    before = live.counts()

    def edit_through_the_account_route(*_args: object, **_kwargs: object) -> None:
        row = created.json()
        edited = TestClient(app).patch(
            f"/accounts/{account_id}",
            json={
                "name": row["name"],
                "institutionId": row["institutionId"],
                "accountType": row["accountType"],
                "currency": row["currency"],
                "country": row["country"],
                "accountNumber": "9753108642",
                "includeInNetWorth": row["includeInNetWorth"],
                "isLiquid": row["isLiquid"],
                "openingBalance": row["openingBalance"],
                "openingBalanceDate": row["openingBalanceDate"],
                "openingBalanceNote": row["openingBalanceNote"],
                "creditLimit": row["creditLimit"],
                "acknowledgeOpeningBalance": False,
            },
            headers=ORIGIN,
        )
        assert edited.status_code == 200, edited.text

    with patch.object(pipeline, "ensure_rates", edit_through_the_account_route):
        refused = live.post_parse(stage=True, account_id=account_id)

    assert refused.status_code == 422
    assert "changed" in refused.json()["detail"]
    assert live.counts() == before
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM account_identifier WHERE account_id = %s "
            "AND source_document_id IS NOT NULL",
            (account_id,),
        )
        assert cur.fetchone() == (0,)


def test_staging_rejects_a_concurrent_currency_edit_without_pdf_currency_evidence(
    live: Fixture,
) -> None:
    created = _create_confirmed_account(live, "proposal test currency changed during parse")
    assert created.status_code == 201, created.text
    account_id = UUID(created.json()["id"])
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET metadata_patterns = metadata_patterns - 'currency' "
            "WHERE id = %s",
            (LAYOUT_ID,),
        )
        cur.execute(
            "SELECT code FROM currency WHERE code <> %s ORDER BY code LIMIT 1",
            (live.currency,),
        )
        replacement = cur.fetchone()
        assert replacement is not None
    live.conn.commit()
    before = live.counts()

    def edit_through_the_account_route(*_args: object, **_kwargs: object) -> None:
        row = created.json()
        edited = TestClient(app).patch(
            f"/accounts/{account_id}",
            json={
                "name": row["name"],
                "institutionId": row["institutionId"],
                "accountType": row["accountType"],
                "currency": replacement[0],
                "country": row["country"],
                "includeInNetWorth": row["includeInNetWorth"],
                "isLiquid": row["isLiquid"],
                "openingBalance": row["openingBalance"],
                "openingBalanceDate": row["openingBalanceDate"],
                "openingBalanceNote": row["openingBalanceNote"],
                "creditLimit": row["creditLimit"],
                "acknowledgeOpeningBalance": False,
            },
            headers=ORIGIN,
        )
        assert edited.status_code == 200, edited.text

    with patch.object(pipeline, "ensure_rates", edit_through_the_account_route):
        refused = live.post_parse(stage=True, account_id=account_id)

    assert refused.status_code == 422
    assert "changed" in refused.json()["detail"]
    assert live.counts() == before
    with live.conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM account_identifier WHERE account_id = %s "
            "AND source_document_id IS NOT NULL",
            (account_id,),
        )
        assert cur.fetchone() == (0,)


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
    proposal = response.json()["identityResolution"]
    assert proposal["name"] == "[redacted]"
    assert proposal["institution"]["proposedName"] == "[redacted]"
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
        proposal = response.json()["identityResolution"]
        resolution = proposal["institution"]
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


def test_confirmation_can_choose_one_current_ambiguous_institution_candidate(
    live: Fixture,
) -> None:
    with live.conn.cursor() as cur:
        cur.execute(
            """INSERT INTO institution
                   (household_id, name, short_name, country, institution_type)
                 VALUES (%s, 'Proposal Test Institution', 'Proposal Test Other', 'ZY', 'bank'),
                        (%s, 'Unrelated Test Institution', 'Unrelated', 'ZX', 'bank')
                 RETURNING id""",
            (live.household_id, live.household_id),
        )
        other_id, unrelated_id = (row[0] for row in cur.fetchall())
    live.conn.commit()
    try:
        proposal = live.post_parse().json()["identityResolution"]
        assert proposal["institution"]["status"] == "ambiguous"
        assert str(live.institution_id) in {
            candidate["id"] for candidate in proposal["institution"]["candidates"]
        }
        body = {
            "name": "proposal test chosen institution",
            "institutionId": str(unrelated_id),
            "accountType": proposal["accountType"],
            "currency": proposal["currency"],
            "country": "ZZ",
            "openingBalance": proposal["openingBalance"],
            "openingBalanceDate": proposal["openingBalanceDate"],
        }
        before = live.counts()

        refused = TestClient(app).post(
            "/parse/account",
            files={"file": ("statement.pdf", _statement())},
            data={"account": json.dumps(body)},
            headers=ORIGIN,
        )
        assert refused.status_code == 409
        assert live.counts() == before

        body["institutionId"] = str(live.institution_id)
        created = TestClient(app).post(
            "/parse/account",
            files={"file": ("statement.pdf", _statement())},
            data={"account": json.dumps(body)},
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        assert created.json()["institutionId"] == str(live.institution_id)
        later = live.post_parse()
        assert later.status_code == 200, later.text
        assert later.json()["accountId"] == created.json()["id"]
        assert later.json()["identityResolution"]["status"] == "matched"
        with live.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM institution WHERE id IN (%s, %s, %s)",
                (live.institution_id, other_id, unrelated_id),
            )
            assert cur.fetchone() == (3,)
    finally:
        with live.conn.cursor() as cur:
            cur.execute(
                """DELETE FROM institution_alias
                    WHERE household_id = %s AND alias_normalized = 'proposal test institution'""",
                (live.household_id,),
            )
            cur.execute("DELETE FROM institution WHERE id IN (%s, %s)", (other_id, unrelated_id))
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
    assert response.json()["identityResolution"]["status"] != "matched"
    assert response.json()["rows"] == []
    assert response.json()["failures"]
    assert live.counts() == before


def test_confirming_creation_then_resolves_the_same_statement(live: Fixture) -> None:
    proposal = live.post_parse().json()["identityResolution"]
    account = {
        "name": "proposal test savings",
        "institutionId": proposal["institution"]["candidates"][0]["id"],
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
    assert created.json()["identifiers"][0]["masked"] == "****3579"
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
    assert "accountProposal" not in resolved.json()
    assert resolved.json()["identityResolution"]["status"] == "matched"
    assert resolved.json()["identityResolution"]["accountAction"] == "reuse"
    assert resolved.json()["identityResolution"]["proposalGroupKey"] is None

    duplicate = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement())},
        data={"account": json.dumps(account)},
        headers=ORIGIN,
    )
    assert duplicate.status_code == 409
    assert "proposal test savings" in duplicate.json()["detail"]


def test_same_institution_savings_then_current_stays_two_accounts_and_reuses_each(
    live: Fixture,
) -> None:
    savings = _create_confirmed_account(live, "proposal test savings identity")
    assert savings.status_code == 201, savings.text
    current_number = "1357924680"
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'current' WHERE id = %s", (LAYOUT_ID,)
        )
    live.conn.commit()

    proposed = TestClient(app).post(
        "/parse",
        files={"file": ("statement.pdf", _statement(current_number))},
        headers=ORIGIN,
    )
    assert proposed.status_code == 200, proposed.text
    identity = proposed.json()["identityResolution"]
    assert identity["institutionAction"] == "reuse"
    assert identity["accountAction"] == "create"

    proposal = proposed.json()["identityResolution"]
    current = TestClient(app).post(
        "/parse/account",
        files={"file": ("statement.pdf", _statement(current_number))},
        data={
            "account": json.dumps(
                {
                    "name": "proposal test current identity",
                    "institutionId": proposal["institution"]["candidates"][0]["id"],
                    "accountType": "current",
                    "currency": proposal["currency"],
                    "country": proposal["country"],
                    "openingBalance": proposal["openingBalance"],
                    "openingBalanceDate": proposal["openingBalanceDate"],
                }
            )
        },
        headers=ORIGIN,
    )
    assert current.status_code == 201, current.text
    assert current.json()["id"] != savings.json()["id"]

    later_current = TestClient(app).post(
        "/parse",
        files={"file": ("statement.pdf", _statement(current_number))},
        headers=ORIGIN,
    )
    assert later_current.json()["accountId"] == current.json()["id"]
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'savings' WHERE id = %s", (LAYOUT_ID,)
        )
    live.conn.commit()
    assert live.post_parse().json()["accountId"] == savings.json()["id"]
    with live.conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM institution WHERE id = %s", (live.institution_id,))
        assert cur.fetchone() == (1,)


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

    proposal = live.post_parse().json()["identityResolution"]
    assert [
        (candidate["id"], candidate["name"], candidate["accountNumberMasked"])
        for candidate in proposal["candidates"]
    ] == [(str(account_id), "proposal test adopted", "****3579")]
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
    _declare_masked_capture(live)
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
    assert created.json()["identifiers"][0]["masked"] == "****3579"
    adopted = TestClient(app).post(
        "/parse",
        files={"file": ("statement.pdf", _statement("3579"))},
        headers=ORIGIN,
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["accountId"] == created.json()["id"]
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
    _declare_masked_capture(live, r"Account\s*Number\s*([X0-9]+)")
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


def test_two_mask_fallback_accounts_are_ambiguous_and_write_nothing(live: Fixture) -> None:
    _declare_masked_capture(live)
    account_ids: list[UUID] = []
    with live.conn.cursor() as cur:
        for index in (1, 2):
            cur.execute(
                """INSERT INTO account
                       (household_id, owner_person_id, institution_id, name, account_type,
                        currency, country, opening_balance_date)
                     VALUES (%s, %s, %s, %s, 'savings', %s, 'ZZ', DATE '2026-01-01')
                     RETURNING id""",
                (
                    live.household_id,
                    live.person_id,
                    live.institution_id,
                    f"proposal test ambiguous mask {index}",
                    live.currency,
                ),
            )
            account_id = cur.fetchone()[0]  # type: ignore[index]
            account_ids.append(account_id)
            cur.execute(
                """INSERT INTO account_identifier
                       (household_id, account_id, kind, masked_value, visible_suffix, provenance)
                     VALUES (%s, %s, 'bank_account', '****3579', '3579', 'owner_entered')""",
                (live.household_id, account_id),
            )
    live.conn.commit()
    before = live.counts()

    parsed = TestClient(app).post(
        "/parse",
        files={"file": ("statement.pdf", _statement("3579"))},
        headers=ORIGIN,
    )

    assert parsed.status_code == 200, parsed.text
    identity = parsed.json()["identityResolution"]
    assert identity["status"] == "ambiguous"
    assert identity["accountAction"] == "choose"
    assert {UUID(candidate["id"]) for candidate in identity["candidates"]} == set(account_ids)
    assert live.counts() == before


def test_custom_layout_complete_card_capture_stores_revealable_source_identity(
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
    assert created.json()["identifiers"][0]["masked"] == "****1111"
    assert created.json()["identifiers"][0]["canReveal"] is True
    assert complete_card not in created.text
    with live.conn.cursor() as cur:
        cur.execute(
            """SELECT masked_value, full_value, normalized_value
                 FROM account_identifier WHERE account_id = %s AND is_active""",
            (created.json()["id"],),
        )
        assert cur.fetchone() == ("****1111", complete_card, complete_card)


def test_v28_card_mask_is_suggested_and_blocks_a_duplicate_create(live: Fixture) -> None:
    _declare_masked_capture(live)
    with live.conn.cursor() as cur:
        cur.execute(
            "UPDATE statement_layout SET account_type = 'credit_card' WHERE id = %s",
            (LAYOUT_ID,),
        )
        cur.execute(
            """INSERT INTO account
                   (household_id, owner_person_id, institution_id, name, account_type,
                    currency, country, opening_balance_date, credit_limit)
                 VALUES (%s, %s, %s, 'proposal test legacy card', 'credit_card', %s, 'ZZ',
                         DATE '2026-01-01', 1000) RETURNING id""",
            (live.household_id, live.person_id, live.institution_id, live.currency),
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
    assert parsed.json()["accountId"] == str(account_id)
    assert parsed.json()["identityResolution"]["status"] == "matched"
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
    _declare_masked_capture(live)
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
                   (household_id, owner_person_id, institution_id, name, account_type,
                    currency, country, opening_balance_date)
                 VALUES (%s, %s, %s, 'proposal test adoption race', 'savings', %s, 'ZZ',
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
