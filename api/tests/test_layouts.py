from __future__ import annotations

import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from oneledger import auth
from oneledger.main import app
from oneledger.routes import layouts

from .fixtures.synthetic_pdf import Placement, build_pdf

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
HOUSEHOLD_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
LAYOUT_ID = UUID("44444444-4444-4444-4444-444444444444")
INSTITUTION_ID = UUID("55555555-5555-5555-5555-555555555555")
EMAIL = "owner@example.com"
ORIGIN = {"Origin": "http://localhost:3000"}

VALID_COLUMNS = [
    {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
    {"field": "description", "xMin": 50.0, "xMax": 200.0},
    {"field": "amount", "xMin": 200.0, "xMax": 260.0},
]

VALID_CREATE = {
    "name": "Layout A",
    "fingerprints": ["FOO BANK"],
    "columns": VALID_COLUMNS,
    "dateFormat": "dd/MM/yyyy",
    "signConvention": "signed",
}

# The full write body, used for round-trip and end-to-end tests. Every field is set to a
# non-default value so a byte-identical round trip actually proves something.
CREATE_BODY = {
    "name": "Layout A savings",
    "institutionId": str(INSTITUTION_ID),
    "fingerprints": ["EXAMPLE BANK", "Statement of Account"],
    "headerRowPattern": "Date\\s+Description\\s+Amount",
    "columns": [
        {"field": "booked_date", "xMin": 40.0, "xMax": 110.0, "label": "Date"},
        {"field": "description", "xMin": 110.0, "xMax": 430.0, "label": "Description"},
        {"field": "amount", "xMin": 430.0, "xMax": 560.0, "label": "Amount"},
    ],
    "dateFormat": "dd/MM/yyyy",
    "signConvention": "signed",
    "invertsSign": False,
    "chronologicalOrder": "ascending",
    "rowStartRule": "has_date_and_amount",
    "continuationRule": None,
    "skipLinePatterns": ["Page \\d+ of \\d+"],
    "openingBalancePattern": None,
    "closingBalancePattern": "Closing Balance ([\\d,.]+)",
    "hasRunningBalance": False,
    "quirks": [],
    "metadataPatterns": None,
}

# The row the database would hand back for CREATE_BODY, in layouts.COLUMNS order — snake_case
# JSONB keys, matching what the migration and the live smoke test store.
FETCHED_ROW: tuple[object, ...] = (
    LAYOUT_ID,
    "Layout A savings",
    1,
    INSTITUTION_ID,
    ["EXAMPLE BANK", "Statement of Account"],
    "Date\\s+Description\\s+Amount",
    [
        {"field": "booked_date", "x_min": 40.0, "x_max": 110.0, "label": "Date"},
        {"field": "description", "x_min": 110.0, "x_max": 430.0, "label": "Description"},
        {"field": "amount", "x_min": 430.0, "x_max": 560.0, "label": "Amount"},
    ],
    "dd/MM/yyyy",
    "signed",
    False,
    "ascending",
    "has_date_and_amount",
    None,
    ["Page \\d+ of \\d+"],
    None,
    "Closing Balance ([\\d,.]+)",
    False,
    [],
    None,
    0,
    None,
)


def owner() -> auth.OwnerSession:
    return auth.OwnerSession(
        user_id=USER_ID, household_id=HOUSEHOLD_ID, session_id=SESSION_ID, email=EMAIL
    )


def proposal_pdf() -> bytes:
    return build_pdf(
        [
            Placement(page=0, x=20, y=700, text="Date"),
            Placement(page=0, x=180, y=700, text="Details"),
            Placement(page=0, x=500, y=700, text="Amount"),
            Placement(page=0, x=20, y=670, text="13/02/2024"),
            Placement(page=0, x=180, y=670, text="ALPHA"),
            Placement(page=0, x=500, y=670, text="17.25"),
            Placement(page=0, x=20, y=640, text="14/02/2024"),
            Placement(page=0, x=180, y=640, text="BETA"),
            Placement(page=0, x=500, y=640, text="20.00"),
        ]
    )


def proposal_pdf_with_sensitive_capture() -> bytes:
    return build_pdf(
        [
            Placement(page=0, x=20, y=700, text="Date"),
            Placement(page=0, x=180, y=700, text="Details"),
            Placement(page=0, x=500, y=700, text="Amount"),
            Placement(page=0, x=20, y=670, text="13/02/2024"),
            Placement(page=0, x=180, y=670, text="Ref 9999-8888-7777"),
            Placement(page=0, x=500, y=670, text="17.25"),
            Placement(page=0, x=20, y=640, text="14/02/2024"),
            Placement(page=0, x=180, y=640, text="BETA"),
            Placement(page=0, x=500, y=640, text="20.00"),
        ]
    )


class LayoutsCursor:
    def __init__(self, connection: LayoutsConnection) -> None:
        self.connection = connection
        self._one: tuple[object, ...] | None = None
        self._many: list[tuple[object, ...]] = []

    def __enter__(self) -> LayoutsCursor:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.connection.statements.append(sql)
        self.connection.params.append(params or ())
        if "SELECT 1 FROM institution" in sql:
            self._one = None if self.connection.fail_institution else (1,)
        elif "INSERT INTO statement_layout" in sql:
            if self.connection.fail_unique_inserts > 0:
                self.connection.fail_unique_inserts -= 1
                raise psycopg.errors.UniqueViolation(
                    "duplicate key value violates unique constraint "
                    '"statement_layout_household_id_name_version_key"'
                )
            self._one = (self.connection.inserted_id,)
        elif "WHERE id = %s AND household_id = %s" in sql:
            self._one = self.connection.rows[0] if self.connection.rows else None
        else:
            self._many = list(self.connection.rows)

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


class LayoutsConnection:
    def __init__(
        self,
        *,
        fail_institution: bool = False,
        rows: list[tuple[object, ...]] | None = None,
        inserted_id: UUID = LAYOUT_ID,
        fail_unique_inserts: int = 0,
    ) -> None:
        self.fail_institution = fail_institution
        self.rows = rows if rows is not None else [FETCHED_ROW]
        self.inserted_id = inserted_id
        self.fail_unique_inserts = fail_unique_inserts
        self.statements: list[str] = []
        self.params: list[tuple[object, ...]] = []

    def __enter__(self) -> LayoutsConnection:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def cursor(self) -> LayoutsCursor:
        return LayoutsCursor(self)


def test_unauthenticated_requests_are_rejected() -> None:
    client = TestClient(app)
    assert client.get("/layouts").status_code == 401
    assert client.post("/layouts", json=CREATE_BODY, headers=ORIGIN).status_code == 401
    assert (
        client.post(
            "/layouts/propose",
            files={"file": ("private-document.pdf", proposal_pdf(), "application/pdf")},
            headers=ORIGIN,
        ).status_code
        == 401
    )


def test_two_digit_year_auto_draft_previews_before_period_mapping() -> None:
    pdf = build_pdf(
        [
            Placement(page=0, x=20, y=700, text="Date"),
            Placement(page=0, x=180, y=700, text="Description"),
            Placement(page=0, x=500, y=700, text="Amount"),
            Placement(page=0, x=20, y=670, text="13/02/24"),
            Placement(page=0, x=180, y=670, text="FIRST"),
            Placement(page=0, x=500, y=670, text="17.25"),
            Placement(page=0, x=20, y=640, text="14/02/24"),
            Placement(page=0, x=180, y=640, text="SECOND"),
            Placement(page=0, x=500, y=640, text="18.25"),
        ]
    )
    app.dependency_overrides[auth.require_owner] = owner
    client = TestClient(app)
    try:
        response = client.post(
            "/layouts/propose", files={"file": ("draft.pdf", pdf)}, headers=ORIGIN
        )
        assert response.status_code == 200
        draft = response.json()["draft"]
        assert draft["dateFormat"] == "dd/MM/yy"
        assert draft["metadataPatterns"] is None
        assert len(response.json()["rows"]) == 2
        assert client.post("/layouts", json=draft, headers=ORIGIN).status_code == 422
        assert (
            client.post(
                "/layouts/propose",
                files={"file": ("draft.pdf", pdf)},
                data={"draft": json.dumps(draft)},
                headers=ORIGIN,
            ).status_code
            == 422
        )
    finally:
        app.dependency_overrides.clear()


def test_auto_propose_redacts_short_secrets_before_escaping_fingerprints() -> None:
    pdf = build_pdf(
        [
            Placement(page=0, x=20, y=760, text="CVV 123"),
            Placement(page=0, x=20, y=740, text="PIN 1234"),
            Placement(page=0, x=20, y=700, text="Date"),
            Placement(page=0, x=180, y=700, text="Description"),
            Placement(page=0, x=500, y=700, text="Amount"),
            Placement(page=0, x=20, y=670, text="13/02/2024"),
            Placement(page=0, x=180, y=670, text="FIRST"),
            Placement(page=0, x=500, y=670, text="17.25"),
            Placement(page=0, x=20, y=640, text="14/02/2024"),
            Placement(page=0, x=180, y=640, text="SECOND"),
            Placement(page=0, x=500, y=640, text="18.25"),
        ]
    )
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("draft.pdf", pdf)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "123" not in response.text
    assert "1234" not in response.text


def test_auto_propose_never_returns_an_identifier_grouped_by_unlisted_separators() -> None:
    identifier = "4111\u20111111\u20111111\u20111111"
    pdf = build_pdf(
        [
            Placement(page=0, x=20, y=760, text=f"Card {identifier}"),
            Placement(page=0, x=20, y=700, text="Date"),
            Placement(page=0, x=180, y=700, text="Description"),
            Placement(page=0, x=500, y=700, text="Amount"),
            Placement(page=0, x=20, y=670, text="13/02/2024"),
            Placement(page=0, x=180, y=670, text=f"FIRST {identifier}"),
            Placement(page=0, x=500, y=670, text="17.25"),
            Placement(page=0, x=20, y=640, text="14/02/2024"),
            Placement(page=0, x=180, y=640, text="SECOND"),
            Placement(page=0, x=500, y=640, text="18.25"),
        ]
    )
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("draft.pdf", pdf)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert identifier not in response.text
    # The escaped fingerprint spelling of the same line must not survive either.
    assert "1111" not in response.text
    assert [row["fields"]["amount"] for row in response.json()["rows"]] == ["17.25", "18.25"]


@pytest.mark.parametrize("draft_mode", [False, True])
def test_malformed_pdf_is_sanitized_and_temporary_file_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    draft_mode: bool,
) -> None:
    monkeypatch.setattr(
        layouts, "NamedTemporaryFile", lambda **kwargs: NamedTemporaryFile(dir=tmp_path, **kwargs)
    )
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/layouts/propose",
            files={"file": ("secret-123456.pdf", b"invalid 123456")},
            data={
                "password": "secret-password",
                **({"draft": json.dumps(VALID_CREATE)} if draft_mode else {}),
            },
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "123456" not in response.text
    assert "secret-password" not in response.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("draft_mode", [False, True])
def test_encryption_error_is_sanitized_and_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    draft_mode: bool,
) -> None:
    import pdfplumber
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    from pdfplumber.utils.exceptions import PdfminerException

    def encrypted(*args: object, **kwargs: object) -> None:
        raise PdfminerException(PDFPasswordIncorrect("private password 999988887777"))

    monkeypatch.setattr(pdfplumber, "open", encrypted)
    monkeypatch.setattr(
        layouts, "NamedTemporaryFile", lambda **kwargs: NamedTemporaryFile(dir=tmp_path, **kwargs)
    )
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/layouts/propose",
            files={"file": ("secret.pdf", proposal_pdf())},
            data={"draft": json.dumps(VALID_CREATE)} if draft_mode else {},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "password" in response.json()["detail"]
    assert "999988887777" not in response.text
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invalid", [False, True])
def test_sensitive_literal_in_echoed_draft_or_validation_error_is_redacted(invalid: bool) -> None:
    draft = {**VALID_CREATE, "fingerprints": ["Account 9999-8888-7777" + ("(" if invalid else "")]}
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("test.pdf", proposal_pdf())},
            data={"draft": json.dumps(draft)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == (422 if invalid else 200)
    assert "9999-8888-7777" not in response.text


def test_propose_returns_a_draft_and_raw_rows_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        layouts,
        "connect",
        lambda: (_ for _ in ()).throw(AssertionError("proposal must not persist")),
    )
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("private-document.pdf", proposal_pdf(), "application/pdf")},
            data={"password": "not-for-output"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["name"] == "Proposed layout"
    assert [row["fields"] for row in body["rows"]] == [
        {"booked_date": "13/02/2024", "description": "ALPHA", "amount": "17.25"},
        {"booked_date": "14/02/2024", "description": "BETA", "amount": "20.00"},
    ]
    assert body["rows"][0]["pageNumber"] == 1
    assert body["rows"][0]["rowNumber"] == 1
    serialized = response.text
    assert "account_number_raw" not in serialized
    assert "accountNumberRaw" not in serialized
    assert "private-document.pdf" not in serialized
    assert "not-for-output" not in serialized


def test_propose_with_an_edited_draft_extracts_with_that_draft() -> None:
    app.dependency_overrides[auth.require_owner] = owner
    client = TestClient(app)
    try:
        automatic = client.post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf(), "application/pdf")},
            headers=ORIGIN,
        ).json()
        edited = automatic["draft"]
        edited["columns"] = [
            {"field": "booked_date", "xMin": 20.0, "xMax": 109.0, "label": None},
            {"field": "description", "xMin": 109.0, "xMax": 150.0, "label": None},
            {"field": "reference", "xMin": 150.0, "xMax": 400.0, "label": None},
            {"field": "amount", "xMin": 400.0, "xMax": 540.0, "label": None},
        ]
        response = client.post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf(), "application/pdf")},
            data={"draft": json.dumps(edited)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["draft"] == edited
    assert [row["fields"] for row in body["rows"]] == [
        {"booked_date": "13/02/2024", "reference": "ALPHA", "amount": "17.25"},
        {"booked_date": "14/02/2024", "reference": "BETA", "amount": "20.00"},
    ]


@pytest.mark.parametrize("draft_mode", [False, True])
def test_proposal_privacy_covers_both_modes_and_identifier_spellings(draft_mode: bool) -> None:
    identifiers = ["9999-8888-7777", "999988887777", "9999 8888 7777", "9999/8888/7777"]
    placements = [
        Placement(page=0, x=20, y=700, text="Date"),
        Placement(page=0, x=180, y=700, text="Details"),
        Placement(page=0, x=500, y=700, text="Amount"),
    ]
    for index, identifier in enumerate(identifiers):
        y = 670 - index * 30
        placements.extend(
            [
                Placement(page=0, x=20, y=y, text=f"{13 + index}/02/2024"),
                Placement(page=0, x=180, y=y, text=f"Ref {identifier} "),
                Placement(page=0, x=500, y=y, text="12345.67"),
            ]
        )
    pdf = build_pdf(placements)
    app.dependency_overrides[auth.require_owner] = owner
    client = TestClient(app)
    try:
        automatic = client.post(
            "/layouts/propose", files={"file": ("statement.pdf", pdf)}, headers=ORIGIN
        )
        assert automatic.status_code == 200
        if draft_mode:
            draft = automatic.json()["draft"]
            draft["metadataPatterns"] = {"account_number": r"Ref ([0-9-]+)"}
            response = client.post(
                "/layouts/propose",
                files={"file": ("statement.pdf", pdf)},
                data={"draft": json.dumps(draft)},
                headers=ORIGIN,
            )
        else:
            response = automatic
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    for identifier in identifiers:
        assert identifier not in response.text
    assert [row["fields"]["booked_date"] for row in response.json()["rows"]] == [
        "13/02/2024",
        "14/02/2024",
        "15/02/2024",
        "16/02/2024",
    ]
    assert all(row["fields"]["amount"] == "12345.67" for row in response.json()["rows"])


def test_only_validated_financial_fields_are_exempt_from_preview_redaction() -> None:
    from oneledger.ingest.engine import RawRow

    # Controller ruling: raw/untyped text is screened unconditionally. Mapping
    # utility is retained only in validated date and amount cells.
    rows = [
        RawRow(
            1,
            1,
            "13/02/2024 REF 14/02/2024 12345.67",
            {
                "booked_date": "13/02/2024",
                "reference": "14/02/2024 12345.67",
                "amount": "12345.67",
                "value_date": "invalid 999988887777",
            },
        )
    ]
    public = layouts._public_rows(rows)
    assert public[0].fields["booked_date"] == "13/02/2024"
    assert public[0].fields["amount"] == "12345.67"
    assert "14/02/2024" not in public[0].fields["reference"]
    assert "999988887777" not in public[0].fields["value_date"]
    assert "13/02/2024" not in public[0].rawText
    assert "12345.67" not in public[0].rawText


def test_long_integer_identifiers_are_redacted_even_in_financial_fields() -> None:
    from oneledger.ingest.engine import RawRow

    identifier = "999988887777"
    public = layouts._public_rows(
        [
            RawRow(
                1,
                1,
                identifier,
                {
                    "booked_date": "13/02/2024",
                    "amount": identifier,
                    "debit": identifier,
                    "credit": identifier,
                    "running_balance": identifier,
                },
            )
        ]
    )

    assert public[0].fields["booked_date"] == "13/02/2024"
    for field in ("amount", "debit", "credit", "running_balance"):
        assert public[0].fields[field] == "[redacted]"


def test_clearly_formatted_financial_values_remain_visible() -> None:
    from oneledger.ingest.engine import RawRow

    values = {
        "amount": "(12345.67)",
        "debit": "12345.6789",
        "credit": "12,345.67",
        "running_balance": "12345.67 CR",
    }
    public = layouts._public_rows([RawRow(1, 1, "money", values)])

    assert public[0].fields == values


def test_unicode_digit_identifiers_are_redacted_from_untyped_preview_text() -> None:
    from oneledger.ingest.engine import RawRow

    identifier = "٤١١١ ١١١١ ١١١١ ١١١١"
    public = layouts._public_rows(
        [RawRow(1, 1, f"Reference {identifier}", {"reference": identifier})]
    )

    assert identifier not in public[0].rawText
    assert identifier not in public[0].fields["reference"]


def test_identifiers_grouped_by_any_separator_are_redacted() -> None:
    from oneledger.ingest.engine import RawRow

    # Grouping punctuation is not an enumerable set: a non-breaking hyphen or an
    # Arabic thousands separator groups an identifier just as a plain hyphen does.
    identifiers = (
        "4111\u20111111\u20111111\u20111111",  # non-breaking hyphens
        "\u0664\u0661\u0661\u0661\u066c\u0661\u0661\u0661\u0661",  # Arabic thousands separator
    )
    for identifier in identifiers:
        public = layouts._public_rows(
            [RawRow(1, 1, f"Card {identifier} paid", {"reference": identifier})]
        )

        assert identifier not in public[0].rawText
        assert identifier not in public[0].fields["reference"]


def test_contextual_and_pan_shaped_secrets_are_redacted_from_preview_text() -> None:
    from oneledger.ingest.engine import RawRow

    secrets = ("1234", "123", "١٢٣٤", "١٢٣", "ABCDE1234F")
    text = "PIN 1234 CVV 123 PIN ١٢٣٤ CVV ١٢٣ Tax ID ABCDE1234F"
    public = layouts._public_rows([RawRow(1, 1, text, {"reference": text})])

    for secret in secrets:
        assert secret not in public[0].rawText
        assert secret not in public[0].fields["reference"]


def test_reserved_capture_overrides_typed_field_protection() -> None:
    from oneledger.ingest.engine import RawRow

    public = layouts._public_rows([RawRow(1, 1, "12345.67", {"amount": "12345.67"})], "12345-67")
    assert public[0].fields["amount"] == "[redacted]"


def test_propose_never_serializes_the_sensitive_account_capture() -> None:
    app.dependency_overrides[auth.require_owner] = owner
    client = TestClient(app)
    try:
        automatic = client.post(
            "/layouts/propose",
            files={
                "file": (
                    "statement.pdf",
                    proposal_pdf_with_sensitive_capture(),
                    "application/pdf",
                )
            },
            headers=ORIGIN,
        ).json()
        draft = automatic["draft"]
        draft["metadataPatterns"] = {"account_number": r"Ref (\d{4}-\d{4}-\d{4})"}
        response = client.post(
            "/layouts/propose",
            files={
                "file": (
                    "statement.pdf",
                    proposal_pdf_with_sensitive_capture(),
                    "application/pdf",
                )
            },
            data={"draft": json.dumps(draft)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(response.json()["rows"]) == 2
    assert "9999-8888-7777" not in response.text


def test_malformed_draft_redacts_sensitive_dictionary_keys_in_error_locations() -> None:
    identifier = "9999 – 8888.7777"
    malformed = {**VALID_CREATE, "metadataPatterns": {identifier: 123}}
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf())},
            data={"draft": json.dumps(malformed)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    normalized = re.sub(r"[\s–./-]", "", json.dumps(response.json(), ensure_ascii=False))
    assert "999988887777" not in normalized
    assert response.json()["detail"][0]["loc"][:2] == ["draft", "metadataPatterns"]


def test_propose_rejects_malformed_draft_json_and_names_the_form_field() -> None:
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf(), "application/pdf")},
            data={"draft": "{not-json"},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][0] == "draft"


def test_propose_draft_uses_the_same_layout_validation_as_save() -> None:
    invalid = {
        **VALID_CREATE,
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 60.0},
            {"field": "description", "xMin": 50.0, "xMax": 200.0},
            {"field": "amount", "xMin": 200.0, "xMax": 260.0},
        ],
    }
    app.dependency_overrides[auth.require_owner] = owner
    client = TestClient(app)
    try:
        saved = client.post("/layouts", json=invalid, headers=ORIGIN)
        proposed = client.post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf(), "application/pdf")},
            data={"draft": json.dumps(invalid)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert saved.status_code == 422
    assert proposed.status_code == 422
    assert "overlap" in saved.text
    assert "overlap" in proposed.text
    assert proposed.json()["detail"][0]["loc"][0] == "draft"


@pytest.mark.parametrize(
    ("field", "value"),
    [("signConvention", "bogus"), ("chronologicalOrder", "bogus")],
)
def test_propose_draft_rejects_database_backed_enum_values(field: str, value: str) -> None:
    invalid = {**VALID_CREATE, field: value}
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf(), "application/pdf")},
            data={"draft": json.dumps(invalid)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["draft", field]


def test_propose_draft_rejects_an_unsupported_date_format() -> None:
    invalid = {**VALID_CREATE, "dateFormat": "MM/dd/yyyy"}
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts/propose",
            files={"file": ("statement.pdf", proposal_pdf(), "application/pdf")},
            data={"draft": json.dumps(invalid)},
            headers=ORIGIN,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:2] == ["draft", "dateFormat"]


def test_a_session_for_another_household_sees_no_layouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service connects privileged and bypasses RLS; the household_id predicate is the
    boundary, exactly as routes/accounts.py's own scoping test proves."""
    connection = LayoutsConnection(rows=[])
    monkeypatch.setattr(layouts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        body = TestClient(app).get("/layouts").json()
    finally:
        app.dependency_overrides.clear()
    assert body == []
    assert "household_id = %s" in connection.statements[0]
    assert HOUSEHOLD_ID in connection.params[0]


def test_a_profile_round_trips_every_field_through_save_and_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = LayoutsConnection(rows=[FETCHED_ROW])
    monkeypatch.setattr(layouts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        saved = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN).json()
        listed = TestClient(app).get("/layouts").json()
    finally:
        app.dependency_overrides.clear()
    assert saved["id"] == str(LAYOUT_ID)
    assert saved["version"] == 1
    for key, value in CREATE_BODY.items():
        assert saved[key] == value, key
    assert listed == [saved]


def test_saving_the_same_name_twice_yields_versions_one_and_two_and_never_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_connection = LayoutsConnection(rows=[FETCHED_ROW], inserted_id=LAYOUT_ID)
    monkeypatch.setattr(layouts, "connect", lambda: first_connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        first = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN).json()
    finally:
        app.dependency_overrides.clear()
    assert first["version"] == 1
    assert not any("UPDATE" in s for s in first_connection.statements)

    v2_id = uuid4()
    v2_row = (v2_id, *FETCHED_ROW[1:2], 2, *FETCHED_ROW[3:])
    second_connection = LayoutsConnection(rows=[v2_row], inserted_id=v2_id)
    monkeypatch.setattr(layouts, "connect", lambda: second_connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        second = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN).json()
    finally:
        app.dependency_overrides.clear()
    assert second["version"] == 2
    assert second["id"] != first["id"]
    assert not any("UPDATE" in s for s in second_connection.statements)
    # version is never a bound parameter: it is computed by the same INSERT ... SELECT that
    # reads the prior rows, so an earlier version is never touched, let alone rewritten.
    insert_statement = next(
        s for s in second_connection.statements if "INSERT INTO statement_layout" in s
    )
    assert "COALESCE(MAX(version), 0) + 1" in insert_statement


def test_institution_must_be_a_shared_seed_or_this_households(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = LayoutsConnection(fail_institution=True)
    monkeypatch.setattr(layouts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "institution" in response.json()["detail"].lower()
    assert not any("INSERT INTO statement_layout" in s for s in connection.statements)


def test_a_database_check_violation_is_a_422_not_a_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """has_a_balance_anchor and chronological_order are enforced by the database; the route
    must translate a CheckViolation, not let it become an unhandled 500."""

    class FailingCursor(LayoutsCursor):
        def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
            if "INSERT INTO statement_layout" in sql:
                raise psycopg.errors.CheckViolation("has_a_balance_anchor")
            super().execute(sql, params)

    class FailingConnection(LayoutsConnection):
        def cursor(self) -> FailingCursor:
            return FailingCursor(self)

    connection = FailingConnection()
    monkeypatch.setattr(layouts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_a_foreign_amount_can_be_mapped_beside_the_accounts_own() -> None:
    # statement-ingestion.html section 5's currency rule needs somewhere to put the bank's own
    # conversion: the amount that left the account, and the original beside it (M2 Task 4).
    mapped = {
        **VALID_CREATE,
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
            {"field": "description", "xMin": 50.0, "xMax": 200.0},
            {"field": "amount", "xMin": 200.0, "xMax": 260.0},
            {"field": "original_amount", "xMin": 260.0, "xMax": 320.0},
            {"field": "original_currency", "xMin": 320.0, "xMax": 360.0},
        ],
    }
    layout = layouts.LayoutCreate.model_validate(mapped)

    assert [column.field for column in layout.columns][-2:] == [
        "original_amount",
        "original_currency",
    ]


def test_an_unknown_column_field_is_still_refused() -> None:
    bad = {
        **VALID_CREATE,
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
            {"field": "merchant", "xMin": 50.0, "xMax": 200.0},
            {"field": "amount", "xMin": 200.0, "xMax": 260.0},
        ],
    }
    with pytest.raises(ValidationError, match="column field must be one of"):
        layouts.LayoutCreate.model_validate(bad)


@pytest.mark.parametrize("convention", ["cr_suffix", "debit_credit_columns"])
def test_inversion_is_refused_where_the_convention_already_states_direction(
    convention: str,
) -> None:
    # inverts_sign carries a statement written in the issuer's frame into the ledger's. A
    # Dr/Cr suffix and a debit/credit pair already state direction, so inverting them reverses
    # every row on the card — internally consistent and entirely wrong.
    body = {**VALID_CREATE, "signConvention": convention, "invertsSign": True}
    if convention == "debit_credit_columns":
        body["columns"] = [
            {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
            {"field": "description", "xMin": 50.0, "xMax": 200.0},
            {"field": "debit", "xMin": 200.0, "xMax": 260.0},
            {"field": "credit", "xMin": 260.0, "xMax": 320.0},
        ]
    with pytest.raises(ValidationError, match="invertsSign"):
        layouts.LayoutCreate.model_validate(body)


@pytest.mark.parametrize("convention", ["signed", "balance_delta"])
def test_inversion_is_accepted_where_the_amount_carries_the_issuers_frame(
    convention: str,
) -> None:
    body = {**VALID_CREATE, "signConvention": convention, "invertsSign": True}
    if convention == "balance_delta":
        body["columns"] = [
            {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
            {"field": "description", "xMin": 50.0, "xMax": 200.0},
            {"field": "running_balance", "xMin": 200.0, "xMax": 260.0},
        ]
        body["hasRunningBalance"] = True

    assert layouts.LayoutCreate.model_validate(body).invertsSign is True


def test_a_column_band_must_have_xmin_less_than_xmax() -> None:
    bad = {
        **VALID_CREATE,
        "columns": [
            {"field": "booked_date", "xMin": 50.0, "xMax": 50.0},
            {"field": "description", "xMin": 50.0, "xMax": 200.0},
            {"field": "amount", "xMin": 200.0, "xMax": 260.0},
        ],
    }
    with pytest.raises(ValidationError, match="xMin"):
        layouts.LayoutCreate.model_validate(bad)


def test_column_bands_must_be_ordered_and_non_overlapping() -> None:
    bad = {
        **VALID_CREATE,
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 60.0},
            {"field": "description", "xMin": 50.0, "xMax": 200.0},
            {"field": "amount", "xMin": 200.0, "xMax": 260.0},
        ],
    }
    with pytest.raises(ValidationError, match="overlap"):
        layouts.LayoutCreate.model_validate(bad)


def test_exactly_one_booked_date_column_is_required() -> None:
    no_date = {
        **VALID_CREATE,
        "columns": [
            {"field": "description", "xMin": 0.0, "xMax": 200.0},
            {"field": "amount", "xMin": 200.0, "xMax": 260.0},
        ],
    }
    with pytest.raises(ValidationError, match="booked_date"):
        layouts.LayoutCreate.model_validate(no_date)

    two_dates = {
        **VALID_CREATE,
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
            {"field": "value_date", "xMin": 50.0, "xMax": 100.0},
            {"field": "booked_date", "xMin": 100.0, "xMax": 150.0},
            {"field": "amount", "xMin": 150.0, "xMax": 210.0},
        ],
    }
    with pytest.raises(ValidationError, match="booked_date"):
        layouts.LayoutCreate.model_validate(two_dates)


def test_balance_delta_requires_running_balance_and_forbids_amount_debit_credit() -> None:
    with pytest.raises(ValidationError, match="running_balance"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "signConvention": "balance_delta"})

    with pytest.raises(ValidationError, match="forbids"):
        layouts.LayoutCreate.model_validate(
            {
                **VALID_CREATE,
                "signConvention": "balance_delta",
                "columns": [
                    {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
                    {"field": "running_balance", "xMin": 50.0, "xMax": 110.0},
                    {"field": "amount", "xMin": 110.0, "xMax": 170.0},
                ],
            }
        )

    assert layouts.LayoutCreate.model_validate(
        {
            **VALID_CREATE,
            "signConvention": "balance_delta",
            "hasRunningBalance": True,
            "columns": [
                {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
                {"field": "description", "xMin": 50.0, "xMax": 150.0},
                {"field": "running_balance", "xMin": 150.0, "xMax": 210.0},
            ],
        }
    )


def test_other_conventions_require_an_amount_column_or_debit_and_credit() -> None:
    columns_no_amount = [
        {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
        {"field": "description", "xMin": 50.0, "xMax": 200.0},
    ]
    with pytest.raises(ValidationError, match="requires an amount column"):
        layouts.LayoutCreate.model_validate(
            {**VALID_CREATE, "signConvention": "signed", "columns": columns_no_amount}
        )
    assert layouts.LayoutCreate.model_validate(
        {
            **VALID_CREATE,
            "signConvention": "signed",
            "columns": [
                *columns_no_amount,
                {"field": "debit", "xMin": 200.0, "xMax": 260.0},
                {"field": "credit", "xMin": 260.0, "xMax": 320.0},
            ],
        }
    )


def test_debit_credit_columns_requires_both_and_forbids_amount() -> None:
    with pytest.raises(ValidationError, match="requires debit and credit"):
        layouts.LayoutCreate.model_validate(
            {**VALID_CREATE, "signConvention": "debit_credit_columns"}
        )

    with pytest.raises(ValidationError, match="forbids an amount column"):
        layouts.LayoutCreate.model_validate(
            {
                **VALID_CREATE,
                "signConvention": "debit_credit_columns",
                "columns": [
                    {"field": "booked_date", "xMin": 0.0, "xMax": 50.0},
                    {"field": "debit", "xMin": 50.0, "xMax": 110.0},
                    {"field": "credit", "xMin": 110.0, "xMax": 170.0},
                    {"field": "amount", "xMin": 170.0, "xMax": 230.0},
                ],
            }
        )


def test_quirks_are_restricted_to_the_small_vocabulary() -> None:
    with pytest.raises(ValidationError, match="unknown quirks"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "quirks": ["made_up_quirk"]})
    assert layouts.LayoutCreate.model_validate(
        {
            **VALID_CREATE,
            "quirks": ["filter_script:arabic", "continuation:no_date_and_no_amount"],
        }
    )


def test_every_regex_must_compile() -> None:
    for field, value in [
        ("fingerprints", ["("]),
        ("skipLinePatterns", ["("]),
        ("openingBalancePattern", "("),
        ("closingBalancePattern", "("),
    ]:
        with pytest.raises(ValidationError, match="not a valid regular expression"):
            layouts.LayoutCreate.model_validate({**VALID_CREATE, field: value})
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "metadataPatterns": {"note": "("}})


def test_an_uncompilable_regex_is_a_422_not_a_500() -> None:
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post(
            "/layouts", json={**CREATE_BODY, "fingerprints": ["("]}, headers=ORIGIN
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def test_reserved_metadata_keys_need_exactly_one_capture_group() -> None:
    with pytest.raises(ValidationError, match="exactly one capture group"):
        layouts.LayoutCreate.model_validate(
            {**VALID_CREATE, "metadataPatterns": {"account_number": "no group here"}}
        )
    with pytest.raises(ValidationError, match="exactly one capture group"):
        layouts.LayoutCreate.model_validate(
            {**VALID_CREATE, "metadataPatterns": {"period_start": r"(\d+)-(\d+)"}}
        )
    assert layouts.LayoutCreate.model_validate(
        {**VALID_CREATE, "metadataPatterns": {"account_number": r"Account (\d+)"}}
    )
    # A non-reserved key may carry any pattern, capture group or not — only the three reserved
    # keys are constrained.
    assert layouts.LayoutCreate.model_validate(
        {**VALID_CREATE, "metadataPatterns": {"credit_limit": r"Limit [\d,.]+"}}
    )


def test_two_digit_year_date_format_requires_both_period_captures() -> None:
    with pytest.raises(ValidationError, match="period_start and period_end"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "dateFormat": "dd/MM/yy"})
    with pytest.raises(ValidationError, match="period_start and period_end"):
        layouts.LayoutCreate.model_validate(
            {
                **VALID_CREATE,
                "dateFormat": "dd/MM/yy",
                "metadataPatterns": {"period_start": r"From (\d{2}/\d{2}/\d{2})"},
            }
        )
    assert layouts.LayoutCreate.model_validate(
        {
            **VALID_CREATE,
            "dateFormat": "dd/MM/yy",
            "metadataPatterns": {
                "period_start": r"From (\d{2}/\d{2}/\d{2})",
                "period_end": r"To (\d{2}/\d{2}/\d{2})",
            },
        }
    )
    # A four-digit year needs no period captures.
    assert layouts.LayoutCreate.model_validate({**VALID_CREATE, "dateFormat": "yyyy-MM-dd"})


def test_row_assembly_rules_outside_the_vocabulary_are_rejected() -> None:
    with pytest.raises(ValidationError, match="rowStartRule"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "rowStartRule": "always"})
    with pytest.raises(ValidationError, match="continuationRule"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "continuationRule": "join_everything"})
    # The documented vocabulary still passes.
    assert layouts.LayoutCreate.model_validate({**VALID_CREATE, "rowStartRule": "has_date"})
    assert layouts.LayoutCreate.model_validate(
        {**VALID_CREATE, "continuationRule": "no_date_and_no_amount"}
    )


def test_the_running_balance_flag_must_agree_with_the_mapped_columns() -> None:
    # Flag set with no running_balance column: the has_a_balance_anchor CHECK would trust it.
    with pytest.raises(ValidationError, match="hasRunningBalance"):
        layouts.LayoutCreate.model_validate({**VALID_CREATE, "hasRunningBalance": True})
    # Column mapped with the flag unset: an anchor the profile denies having.
    with pytest.raises(ValidationError, match="hasRunningBalance"):
        layouts.LayoutCreate.model_validate(
            {
                **VALID_CREATE,
                "columns": VALID_COLUMNS
                + [{"field": "running_balance", "xMin": 260.0, "xMax": 320.0}],
            }
        )
    # Agreement in both directions passes.
    assert layouts.LayoutCreate.model_validate(
        {
            **VALID_CREATE,
            "hasRunningBalance": True,
            "columns": VALID_COLUMNS + [{"field": "running_balance", "xMin": 260.0, "xMax": 320.0}],
        }
    )


def test_a_concurrent_version_race_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two saves of one name can read the same MAX(version); the loser's UniqueViolation is
    retried against the fresh MAX rather than surfacing a 500."""
    connection = LayoutsConnection(rows=[FETCHED_ROW], fail_unique_inserts=1)
    monkeypatch.setattr(layouts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 201
    inserts = [s for s in connection.statements if "INSERT INTO statement_layout" in s]
    assert len(inserts) == 2


def test_a_version_race_that_never_resolves_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = LayoutsConnection(rows=[FETCHED_ROW], fail_unique_inserts=99)
    monkeypatch.setattr(layouts, "connect", lambda: connection, raising=False)
    app.dependency_overrides[auth.require_owner] = owner
    try:
        response = TestClient(app).post("/layouts", json=CREATE_BODY, headers=ORIGIN)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 409
