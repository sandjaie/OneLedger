# api/oneledger/routes/layouts.py
"""M2 Task 2: statement layout profiles (ADR-0012). A layout is a row, created through a mapping
UI — no bank name enters application code. `POST /layouts` never mutates an existing version;
re-saving a name inserts `version + 1`, so a row already imported keeps pointing at the version
that produced it."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from psycopg.types.json import Json
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..ingest import engine, normalize
from ..ingest.words import ExtractionError

router = APIRouter(tags=["layouts"])

# statement-ingestion.html section 4: the fields a column band may claim. The two original_*
# bands are section 5's currency rule made mappable — a statement that states a foreign amount
# beside the account's own puts it in its own column, and without a band for it the bank's
# authoritative conversion could never be captured (M2 Task 4).
COLUMN_FIELDS = frozenset(
    {
        "booked_date",
        "value_date",
        "description",
        "reference",
        "amount",
        "debit",
        "credit",
        "running_balance",
        "original_amount",
        "original_currency",
    }
)
# The small, additive vocabulary (ADR-0012). A third entry is a considered addition, not a reflex.
# `metadata_text:words` is that addition: a card's summary block fuses its credit limit onto the
# available credit in the raw run, and only the words, split on the gap between cells, part them
# (db/seeds/README.md).
QUIRKS_VOCABULARY = frozenset(
    {"filter_script:arabic", "continuation:no_date_and_no_amount", "metadata_text:words"}
)
# metadata_patterns keys the pipeline understands itself (plan section 4.4).
# The card cycle's four facts are reserved too: each is stored as one normalised value, so
# each must capture exactly one — and they are named once, where they are read. So are the three
# a remittance's narration states, which the engine reads per row rather than per statement.
RESERVED_METADATA_KEYS = (
    frozenset({"account_number", "currency", "period_start", "period_end"})
    | normalize.CARD_FACT_KEYS
    | engine.ROW_CAPTURE_KEYS
)

# The extraction contract's complete row-assembly vocabulary (plan section 4.4): the columns are
# unconstrained TEXT in the database, so the service is what keeps garbage out of them.
ROW_START_RULES = frozenset({"has_date_and_amount", "has_date"})
CONTINUATION_RULES = frozenset({"no_date_and_no_amount", "blank_date_and_money_columns"})
SIGN_CONVENTIONS = frozenset({"debit_credit_columns", "signed", "cr_suffix", "balance_delta"})
CHRONOLOGICAL_ORDERS = frozenset({"ascending", "descending"})
# A run of exactly two 'y's, not part of a longer run — dd/MM/yy has one, dd/MM/yyyy does not.
TWO_DIGIT_YEAR = re.compile(r"(?<!y)yy(?!y)")


def _compiled(pattern: str) -> re.Pattern[str]:
    """A profile is executed as regexes at parse time; an uncompilable one must die at save."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression: {pattern!r} ({exc})") from exc


def _first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0]


class LayoutColumn(BaseModel):
    field: str
    xMin: float
    xMax: float
    label: str | None = None

    @field_validator("field")
    @classmethod
    def known_field(cls, value: str) -> str:
        if value not in COLUMN_FIELDS:
            raise ValueError("column field must be one of: " + ", ".join(sorted(COLUMN_FIELDS)))
        return value

    @model_validator(mode="after")
    def band_is_ordered(self) -> LayoutColumn:
        if self.xMin >= self.xMax:
            raise ValueError(f"column {self.field!r}: xMin must be less than xMax")
        return self


class LayoutProfile(BaseModel):
    """GET /layouts item, POST /layouts response."""

    id: UUID
    name: str
    version: int
    institutionId: UUID | None
    accountType: str | None = None
    fingerprints: list[str]
    headerRowPattern: str | None
    columns: list[LayoutColumn]
    dateFormat: str
    signConvention: str
    invertsSign: bool
    chronologicalOrder: str
    rowStartRule: str
    continuationRule: str | None
    skipLinePatterns: list[str]
    openingBalancePattern: str | None
    closingBalancePattern: str | None
    hasRunningBalance: bool
    quirks: list[str]
    metadataPatterns: dict[str, str] | None


class LayoutCreate(BaseModel):
    """POST /layouts body. Validation the database cannot express — everything the has_a_balance
    anchor CHECK and the sign_convention/chronological_order enums leave uncovered."""

    name: str = Field(min_length=1, max_length=120)
    institutionId: UUID | None = None
    accountType: str | None = Field(default=None, min_length=1, max_length=40)
    fingerprints: list[str]
    headerRowPattern: str | None = None
    columns: list[LayoutColumn]
    dateFormat: str = Field(min_length=1, max_length=40)
    signConvention: str
    # Reframes every figure the statement wrote without a direction of its own, and only those:
    # an amount under `signed`, and any declared or running balance carrying no Dr/Cr suffix.
    # A figure that stated its direction has nothing left to reframe, so this is withheld from
    # rows under `cr_suffix` and `debit_credit_columns` — which is what lets a card declare it.
    # Such a card needs it: its rows say which way they go and its anchors do not, so without
    # it the anchors stay in the issuer's frame and every statement mismatches. Under
    # `balance_delta` it acts on the balances the delta is taken between, not on the delta,
    # which is already directed by the subtraction.
    #
    # Applied in `normalize.normalize` (a row's amount), `normalize._balance_deltas` (each
    # balance, before the subtraction) and `reconcile`'s two framing helpers (the declared
    # anchors and a row's running balance). `normalize.frame_balance` is the rule itself.
    invertsSign: bool = False
    chronologicalOrder: str = "ascending"
    rowStartRule: str = "has_date_and_amount"
    continuationRule: str | None = None
    skipLinePatterns: list[str] = Field(default_factory=list)
    openingBalancePattern: str | None = None
    closingBalancePattern: str | None = None
    hasRunningBalance: bool = False
    quirks: list[str] = Field(default_factory=list)
    metadataPatterns: dict[str, str] | None = None

    @field_validator("fingerprints", "skipLinePatterns")
    @classmethod
    def patterns_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            _compiled(pattern)
        return value

    @field_validator("openingBalancePattern", "closingBalancePattern")
    @classmethod
    def balance_pattern_compiles(cls, value: str | None) -> str | None:
        if value is not None:
            _compiled(value)
        return value

    @field_validator("quirks")
    @classmethod
    def known_quirks(cls, value: list[str]) -> list[str]:
        unknown = sorted(set(value) - QUIRKS_VOCABULARY)
        if unknown:
            raise ValueError("unknown quirks: " + ", ".join(unknown))
        return value

    @field_validator("metadataPatterns")
    @classmethod
    def metadata_patterns_are_valid(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return value
        for key, pattern in value.items():
            compiled = _compiled(pattern)
            if key in RESERVED_METADATA_KEYS and compiled.groups != 1:
                raise ValueError(
                    f"reserved metadata key {key!r} must have exactly one capture group"
                )
        return value

    @field_validator("dateFormat")
    @classmethod
    def date_format_is_supported(cls, value: str) -> str:
        if value not in engine.DATE_FORMATS:
            raise ValueError("dateFormat must be one of: " + ", ".join(sorted(engine.DATE_FORMATS)))
        return value

    @field_validator("signConvention")
    @classmethod
    def sign_convention_is_supported(cls, value: str) -> str:
        if value not in SIGN_CONVENTIONS:
            raise ValueError(
                "signConvention must be one of: " + ", ".join(sorted(SIGN_CONVENTIONS))
            )
        return value

    @field_validator("chronologicalOrder")
    @classmethod
    def chronological_order_is_supported(cls, value: str) -> str:
        if value not in CHRONOLOGICAL_ORDERS:
            raise ValueError(
                "chronologicalOrder must be one of: " + ", ".join(sorted(CHRONOLOGICAL_ORDERS))
            )
        return value

    @model_validator(mode="after")
    def columns_are_ordered_and_non_overlapping(self) -> LayoutCreate:
        ordered = sorted(self.columns, key=lambda c: c.xMin)
        for prev, curr in zip(ordered, ordered[1:], strict=False):
            if curr.xMin < prev.xMax:
                raise ValueError("column bands must be ordered and must not overlap")
        if sum(1 for c in self.columns if c.field == "booked_date") != 1:
            raise ValueError("exactly one column must be booked_date")
        return self

    @model_validator(mode="after")
    def sign_convention_matches_its_columns(self) -> LayoutCreate:
        present = {c.field for c in self.columns}
        if self.signConvention == "balance_delta":
            if "running_balance" not in present:
                raise ValueError("signConvention 'balance_delta' requires a running_balance column")
            if present & {"amount", "debit", "credit"}:
                raise ValueError(
                    "signConvention 'balance_delta' forbids amount, debit and credit columns"
                )
        elif self.signConvention == "debit_credit_columns":
            if not {"debit", "credit"} <= present:
                raise ValueError(
                    "signConvention 'debit_credit_columns' requires debit and credit columns"
                )
            if "amount" in present:
                raise ValueError("signConvention 'debit_credit_columns' forbids an amount column")
        elif "amount" not in present and not {"debit", "credit"} <= present:
            raise ValueError(
                f"signConvention {self.signConvention!r} requires an amount column, "
                "or both debit and credit columns"
            )
        return self

    @field_validator("rowStartRule")
    @classmethod
    def row_start_rule_is_supported(cls, value: str) -> str:
        if value not in ROW_START_RULES:
            raise ValueError("rowStartRule must be one of: " + ", ".join(sorted(ROW_START_RULES)))
        return value

    @field_validator("continuationRule")
    @classmethod
    def continuation_rule_is_supported(cls, value: str | None) -> str | None:
        if value is not None and value not in CONTINUATION_RULES:
            supported = ", ".join(sorted(CONTINUATION_RULES))
            raise ValueError("continuationRule must be omitted or one of: " + supported)
        return value

    @model_validator(mode="after")
    def running_balance_flag_matches_its_column(self) -> LayoutCreate:
        # The has_a_balance_anchor CHECK trusts this boolean, so a flag that disagrees with the
        # mapped columns could save a profile with no usable anchor (or hide one it has).
        has_column = any(c.field == "running_balance" for c in self.columns)
        if self.hasRunningBalance != has_column:
            raise ValueError(
                "hasRunningBalance must agree with the columns: "
                "true exactly when a running_balance column is mapped"
            )
        return self

    @model_validator(mode="after")
    def period_captures_come_as_a_pair(self) -> LayoutCreate:
        """A captured bound is authoritative, and half a period cannot be.

        With one bound captured and the other derived from the row dates, a date outside the
        captured bound widens the derived period and survives — the opposite of what capturing
        it is for (M2 Task 4).
        """
        captured = set(self.metadataPatterns or {}) & {"period_start", "period_end"}
        if len(captured) == 1:
            raise ValueError(
                "metadataPatterns must capture both period_start and period_end, or neither"
            )
        return self

    @model_validator(mode="after")
    def two_digit_year_needs_both_period_captures(self) -> LayoutCreate:
        if TWO_DIGIT_YEAR.search(self.dateFormat):
            captured = set(self.metadataPatterns or {})
            if not {"period_start", "period_end"} <= captured:
                raise ValueError(
                    "a dateFormat with a two-digit year requires metadataPatterns to capture "
                    "both period_start and period_end, or the year cannot be windowed"
                )
        return self


class ProposedRawRow(BaseModel):
    """Public raw extraction row. Sensitive transient extraction fields are absent by design."""

    pageNumber: int
    rowNumber: int
    rawText: str
    fields: dict[str, str]


class LayoutProposal(BaseModel):
    """POST /layouts/propose response. Task 5 later adds normalized preview and reconciliation."""

    draft: engine.LayoutDraft | LayoutCreate
    rows: list[ProposedRawRow]


COLUMNS = """id, name, version, institution_id, account_type::text, fingerprints,
             header_row_pattern, columns,
             date_format, sign_convention::text, inverts_sign, chronological_order,
             row_start_rule, continuation_rule, skip_line_patterns, opening_balance_pattern,
             closing_balance_pattern, has_running_balance, quirks, metadata_patterns"""


def _row_to_profile(row: tuple[Any, ...]) -> LayoutProfile:
    (
        id_,
        name,
        version,
        institution_id,
        account_type,
        fingerprints,
        header_row_pattern,
        columns_json,
        date_format,
        sign_convention,
        inverts_sign,
        chronological_order,
        row_start_rule,
        continuation_rule,
        skip_line_patterns,
        opening_balance_pattern,
        closing_balance_pattern,
        has_running_balance,
        quirks,
        metadata_patterns,
    ) = row
    return LayoutProfile(
        id=id_,
        name=name,
        version=version,
        institutionId=institution_id,
        accountType=account_type,
        fingerprints=list(fingerprints or []),
        headerRowPattern=header_row_pattern,
        columns=[
            LayoutColumn(field=c["field"], xMin=c["x_min"], xMax=c["x_max"], label=c.get("label"))
            for c in (columns_json or [])
        ],
        dateFormat=date_format,
        signConvention=sign_convention,
        invertsSign=inverts_sign,
        chronologicalOrder=chronological_order,
        rowStartRule=row_start_rule,
        continuationRule=continuation_rule,
        skipLinePatterns=list(skip_line_patterns or []),
        openingBalancePattern=opening_balance_pattern,
        closingBalancePattern=closing_balance_pattern,
        hasRunningBalance=has_running_balance,
        quirks=list(quirks or []),
        metadataPatterns=metadata_patterns,
    )


def _assert_institution_allowed(
    cur: psycopg.Cursor[Any], institution_id: UUID | None, household_id: UUID
) -> None:
    """Shared seeds or this household's own — the pattern routes/accounts.py already uses."""
    if institution_id is None:
        return
    cur.execute(
        """SELECT 1 FROM institution
            WHERE id = %s AND (household_id IS NULL OR household_id = %s)""",
        (institution_id, household_id),
    )
    if cur.fetchone() is None:
        raise HTTPException(
            status_code=422, detail="institution is not available to this household"
        )


def _one(cur: psycopg.Cursor[Any], layout_id: UUID, household_id: UUID) -> LayoutProfile:
    cur.execute(
        f"SELECT {COLUMNS} FROM statement_layout WHERE id = %s AND household_id = %s",
        (layout_id, household_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such layout")
    return _row_to_profile(row)


def _parse_draft(value: str) -> LayoutCreate:
    try:
        return LayoutCreate.model_validate_json(value)
    except ValidationError as exc:
        redact = _preview_redactor(None)
        detail = [
            {
                "type": error["type"],
                "loc": [
                    "draft",
                    *[
                        redact(segment) if isinstance(segment, str) else segment
                        for segment in error["loc"]
                    ],
                ],
                "msg": redact(error["msg"]),
            }
            for error in exc.errors(include_input=False, include_url=False)
        ]
        raise HTTPException(status_code=422, detail=detail) from exc


# Grouping punctuation is not an enumerable set: a hyphen, a non-breaking hyphen, an Arabic
# thousands separator and a space all group one identifier. Anything that is neither a letter
# nor a digit may therefore join two digit runs, and only a letter ends one.
IDENTIFIER_SEPARATOR = r"[\W_]"
IDENTIFIER_RUN = re.compile(rf"\d(?:(?:\d|{IDENTIFIER_SEPARATOR})*\d)?")
PAN_SHAPED_IDENTIFIER = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)
FINANCIAL_FIELDS = frozenset(
    {
        "booked_date",
        "value_date",
        "amount",
        "debit",
        "credit",
        "running_balance",
        "original_amount",
    }
)


def _capture_redactor(capture: str | None) -> Callable[[str], str]:
    normalized = re.sub(IDENTIFIER_SEPARATOR, "", capture) if capture else ""
    separator = f"{IDENTIFIER_SEPARATOR}*"
    pattern = re.compile(separator.join(map(re.escape, normalized))) if normalized else None
    return lambda value: pattern.sub("[redacted]", value) if pattern else value


def _preview_redactor(capture: str | None) -> Callable[[str], str]:
    """Unconditionally screen raw/untyped text; never restore financial-looking tokens."""
    redact_capture = _capture_redactor(capture)

    def redact(value: str) -> str:
        screened = PAN_SHAPED_IDENTIFIER.sub("[redacted]", redact_capture(value))
        screened = engine.CONTEXTUAL_SECRET.sub(
            lambda match: f"{match.group('label')}{match.group('separator')}[redacted]",
            screened,
        )
        return IDENTIFIER_RUN.sub(
            lambda match: (
                "[redacted]" if sum(c.isdigit() for c in match.group()) > 4 else match.group()
            ),
            screened,
        )

    return redact


def _validated_financial_field(key: str, value: str) -> bool:
    if key not in FINANCIAL_FIELDS:
        return False
    if key in {"booked_date", "value_date"}:
        return any(
            engine._date(value, date_format) is not None for date_format in engine.DATE_FORMATS
        )
    if not engine._is_number(value):
        return False
    digit_count = sum(character.isdigit() for character in value)
    if digit_count <= 4:
        return True
    return re.fullmatch(r"\d{5,}", value.strip()) is None


def _redact_draft(value: object, redact: Callable[[str], str]) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_draft(item, redact) for item in value]
    if isinstance(value, dict):
        return {redact(key): _redact_draft(item, redact) for key, item in value.items()}
    return value


def _public_rows(
    rows: list[engine.RawRow], sensitive_account_capture: str | None = None
) -> list[ProposedRawRow]:
    redact = _preview_redactor(sensitive_account_capture)
    redact_capture = _capture_redactor(sensitive_account_capture)

    return [
        ProposedRawRow(
            pageNumber=row.page_number,
            rowNumber=row.row_number,
            rawText=redact(row.raw_text),
            fields={
                key: redact_capture(value)
                if _validated_financial_field(key, value)
                else redact(value)
                for key, value in row.fields.items()
            },
        )
        for row in rows[:20]
    ]


@router.post("/layouts/propose")
def propose_layout(
    file: Annotated[UploadFile, File()],
    owner: Annotated[OwnerSession, Depends(require_owner)],
    password: Annotated[str | None, Form()] = None,
    draft: Annotated[str | None, Form()] = None,
) -> LayoutProposal:
    del owner
    parsed_draft = _parse_draft(draft) if draft is not None else None
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(prefix="oneledger-layout-", suffix=".pdf", delete=False) as temp:
            temporary_path = Path(temp.name)
            copyfileobj(file.file, temp)
        if parsed_draft is None:
            proposed, rows = engine.propose(temporary_path, password)
            response_draft: engine.LayoutDraft | LayoutCreate = proposed
            sensitive_account_capture = None
        else:
            response_draft = parsed_draft
            extraction = engine.extract(parsed_draft, temporary_path, password)
            rows = extraction.rows
            sensitive_account_capture = extraction.account_number_raw
        public_draft = _redact_draft(
            response_draft.model_dump()
            if isinstance(response_draft, LayoutCreate)
            else asdict(response_draft),
            _preview_redactor(sensitive_account_capture),
        )
        return LayoutProposal.model_validate(
            {
                "draft": public_draft,
                "rows": _public_rows(rows, sensitive_account_capture),
            }
        )
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@router.get("/layouts")
def list_layouts(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    allVersions: Annotated[bool, Query()] = False,
) -> list[LayoutProfile]:
    with connect() as conn, conn.cursor() as cur:
        if allVersions:
            cur.execute(
                f"SELECT {COLUMNS} FROM statement_layout"
                " WHERE household_id = %s ORDER BY name, version",
                (owner.household_id,),
            )
        else:
            # The highest version per name: schema.md's UNIQUE(household_id, name, version)
            # makes version a plain ascending counter, so DISTINCT ON picks the newest per name.
            cur.execute(
                f"SELECT DISTINCT ON (name) {COLUMNS} FROM statement_layout"
                " WHERE household_id = %s ORDER BY name, version DESC",
                (owner.household_id,),
            )
        return [_row_to_profile(row) for row in cur.fetchall()]


@router.post("/layouts", status_code=201)
def create_layout(
    body: LayoutCreate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> LayoutProfile:
    columns_payload = [
        {"field": c.field, "x_min": c.xMin, "x_max": c.xMax, "label": c.label} for c in body.columns
    ]
    # Two concurrent saves of one name can read the same MAX(version); the
    # UNIQUE(household_id, name, version) index rejects the loser, which retries against the
    # fresh MAX in a new transaction instead of surfacing a 500.
    for attempts_left in (2, 1, 0):
        try:
            return _create_layout_once(body, owner, columns_payload)
        except psycopg.errors.UniqueViolation as exc:
            if attempts_left == 0:
                raise HTTPException(
                    status_code=409, detail="the layout version could not be allocated; retry"
                ) from exc
    raise AssertionError("unreachable")  # ponytail: the loop always returns or raises


def _create_layout_once(
    body: LayoutCreate, owner: OwnerSession, columns_payload: list[dict[str, Any]]
) -> LayoutProfile:
    try:
        with connect() as conn, conn.cursor() as cur:
            _assert_institution_allowed(cur, body.institutionId, owner.household_id)
            # version = COALESCE(MAX(version), 0) + 1 for this (household, name): re-saving an
            # existing name inserts the next version and never mutates an earlier one
            # (ADR-0012). The aggregate over zero matching rows still yields one row, so a new
            # name inserts version 1 in the same statement.
            cur.execute(
                """INSERT INTO statement_layout (
                       household_id, institution_id, account_type, name, version, fingerprints,
                       header_row_pattern, columns, date_format, sign_convention, inverts_sign,
                       chronological_order, row_start_rule, continuation_rule,
                       skip_line_patterns, opening_balance_pattern, closing_balance_pattern,
                       has_running_balance, quirks, metadata_patterns)
                   SELECT %s, %s, %s::account_type, %s, COALESCE(MAX(version), 0) + 1,
                          %s, %s, %s::jsonb, %s,
                          %s::sign_convention, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                     FROM statement_layout
                    WHERE household_id = %s AND name = %s
                   RETURNING id""",
                (
                    owner.household_id,
                    body.institutionId,
                    body.accountType,
                    body.name,
                    body.fingerprints,
                    body.headerRowPattern,
                    Json(columns_payload),
                    body.dateFormat,
                    body.signConvention,
                    body.invertsSign,
                    body.chronologicalOrder,
                    body.rowStartRule,
                    body.continuationRule,
                    body.skipLinePatterns,
                    body.openingBalancePattern,
                    body.closingBalancePattern,
                    body.hasRunningBalance,
                    body.quirks,
                    Json(body.metadataPatterns) if body.metadataPatterns is not None else None,
                    owner.household_id,
                    body.name,
                ),
            )
            created = cur.fetchone()
            if created is None:
                raise HTTPException(status_code=500, detail="the layout could not be created")
            return _one(cur, UUID(str(created[0])), owner.household_id)
    except (
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
    ) as exc:
        raise HTTPException(status_code=422, detail=_first_line(exc)) from exc
