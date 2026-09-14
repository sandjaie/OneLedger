"""Phase 1.7 Task 1: payslips, entered or imported from a supported pay-advice PDF, and the
net-pay link. `record_payslip` (V42) is the one write path for entry, correction and import;
this module maps its constraint names and SQLSTATEs to the specification's HTTP statuses and
never writes a transaction, balance or classification (spec "Failure behaviour and privacy")."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..ingest import storage
from ..ingest.uploads import uploaded_pages
from ..ingest.words import ExtractionError
from ..money import _money
from ..payslip_pdf import EXTRACTION_VERSION, PayslipMonthDraft, PayslipReason, extract_payslips
from .layouts import _preview_redactor
from .transactions import (
    _assert_category_allowed,
    delete_ledger_transaction,
    post_ledger_transaction,
)

router = APIRouter(tags=["payslips"])

# A fixed constant, not a real namespace anyone resolves — uuid5 only needs it to be stable.
PAYSLIP_IMPORT_NAMESPACE = UUID("6f2b6b4e-6f0a-4a8b-9e3a-2f9c8f6d9a11")
_SAFE_DOCUMENT_LABEL = "payslip.pdf"
_LIST_LIMIT = 200
_CANDIDATE_LIMIT = 20
_PAY_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _unsigned(value: str) -> str:
    checked = _money(value)
    assert checked is not None
    if checked.startswith("-"):
        raise ValueError("amount must not be negative")
    return checked


def _positive(value: str) -> str:
    checked = _unsigned(value)
    if Decimal(checked) <= 0:
        raise ValueError("amount must be positive")
    return checked


def _normalize_label(value: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFKC", value).split())
    if not 1 <= len(collapsed) <= 80:
        raise ValueError("label must be 1-80 characters")
    return collapsed


def _pay_month_str(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _pay_month_date(value: str) -> date:
    year, month = value.split("-")
    return date(int(year), int(month), 1)


def _next_month_tenth(pay_month: date) -> date:
    return (
        date(pay_month.year + 1, 1, 10)
        if pay_month.month == 12
        else date(pay_month.year, pay_month.month + 1, 10)
    )


class PayslipLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["earning", "deduction"]
    label: str
    amount: str

    @field_validator("label")
    @classmethod
    def _label(cls, value: str) -> str:
        return _normalize_label(value)

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: str) -> str:
        return _positive(value)


class PayslipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payMonth: str = Field(pattern=_PAY_MONTH.pattern)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    grossEarnings: str
    totalDeductions: str
    netPay: str
    lines: list[PayslipLineIn] = Field(min_length=1, max_length=40)
    idempotencyKey: UUID

    @field_validator("grossEarnings")
    @classmethod
    def _gross(cls, value: str) -> str:
        return _positive(value)

    @field_validator("totalDeductions", "netPay")
    @classmethod
    def _nonneg(cls, value: str) -> str:
        return _unsigned(value)

    @field_validator("lines")
    @classmethod
    def _has_earning(cls, value: list[PayslipLineIn]) -> list[PayslipLineIn]:
        if not any(line.kind == "earning" for line in value):
            raise ValueError("a payslip needs at least one earning line")
        return value


class PayslipLineOut(BaseModel):
    lineNo: int
    kind: Literal["earning", "deduction"]
    label: str
    amount: str
    countedAsSpending: bool
    categoryId: UUID | None


class DepositOut(BaseModel):
    transactionId: UUID
    accountId: UUID
    accountName: str
    bookedDate: date
    amount: str
    eligible: bool


class PayslipRow(BaseModel):
    id: UUID
    payMonth: str
    currency: str
    grossEarnings: str
    totalDeductions: str
    netPay: str
    lines: list[PayslipLineOut]
    supersedesPayslipId: UUID | None
    supersededByPayslipId: UUID | None
    imported: bool
    createdAt: datetime
    deposit: DepositOut | None


class PayslipsResponse(BaseModel):
    payslips: list[PayslipRow]
    total: int


class DepositCandidate(BaseModel):
    transactionId: UUID
    accountId: UUID
    accountName: str
    bookedDate: date
    amount: str
    currency: str
    description: str


class DepositCandidatesResponse(BaseModel):
    candidates: list[DepositCandidate]


class DepositLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactionId: UUID


class PayslipImportMonth(BaseModel):
    payMonth: str | None
    currency: str
    grossEarnings: str
    totalDeductions: str
    netPay: str
    lines: list[PayslipLineOut]
    reason: PayslipReason | None
    status: Literal["imported", "already_imported", "already_recorded", "not_reconciled"] | None
    payslipId: UUID | None


class PayslipImportResponse(BaseModel):
    supported: bool
    months: list[PayslipImportMonth]


class DeductionRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoryId: UUID


class DeductionRuleRow(BaseModel):
    label: str
    categoryId: UUID


class DeductionRulesResponse(BaseModel):
    rules: list[DeductionRuleRow]


class LineSpendingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoryId: UUID


def _translate_database_error(exc: psycopg.Error) -> HTTPException:
    constraint = exc.diag.constraint_name
    if exc.sqlstate == "42501":
        return HTTPException(status_code=404, detail="payslip, account or credit not found")
    if exc.sqlstate == "P0002":
        return HTTPException(status_code=404, detail="payslip not found")
    if constraint == "payslip_idempotency_conflict":
        return HTTPException(
            status_code=409, detail="idempotency key reused with a different request"
        )
    if constraint == "payslip_is_linked":
        return HTTPException(
            status_code=409, detail="a linked payslip must be unlinked before it can be corrected"
        )
    if constraint == "payslip_duplicate":
        return HTTPException(
            status_code=409,
            detail="a payslip for this month, currency and net pay is already recorded",
        )
    if constraint == "uq_payslip_supersedes":
        return HTTPException(status_code=409, detail="this payslip has already been corrected")
    if constraint == "payslip_totals_agree":
        return HTTPException(
            status_code=422, detail="payslip lines do not reconcile with the stated totals"
        )
    if constraint == "payslip_is_superseded":
        return HTTPException(status_code=409, detail="a superseded payslip cannot be linked")
    if constraint == "payslip_deposit_is_eligible":
        return HTTPException(
            status_code=422, detail="the selected credit is not an eligible net-pay deposit"
        )
    if constraint == "payslip_line_posting_is_deduction":
        return HTTPException(status_code=422, detail="only a deduction line can count as spending")
    if constraint == "uq_payslip_deposit":
        return HTTPException(
            status_code=409, detail="that credit is already linked to another payslip"
        )
    if exc.sqlstate in {"22003", "22023", "23502", "23503", "23514"}:
        return HTTPException(status_code=422, detail="payslip violates its required field matrix")
    raise exc


def _record(
    cur: psycopg.Cursor[Any],
    idempotency_key: UUID,
    supersedes: UUID | None,
    source_document_id: UUID | None,
    pay_month: date,
    currency: str,
    gross: str,
    deductions: str,
    net: str,
    lines: list[PayslipLineIn],
) -> tuple[UUID, bool]:
    cur.execute(
        """SELECT recorded_payslip_id, created FROM record_payslip(
               %s, %s, %s, %s, %s, %s, %s, %s, %s::payslip_line_kind[], %s, %s)""",
        (
            str(idempotency_key),
            supersedes,
            source_document_id,
            pay_month,
            currency,
            Decimal(gross),
            Decimal(deductions),
            Decimal(net),
            [line.kind for line in lines],
            [line.label for line in lines],
            [Decimal(line.amount) for line in lines],
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0])), bool(row[1])


def _load_payslips(
    cur: psycopg.Cursor[Any], household_id: UUID, ids: list[UUID] | None
) -> list[PayslipRow]:
    if ids is not None and not ids:
        return []
    params: list[Any] = [household_id]
    if ids is None:
        where, order_limit = (
            "p.household_id = %s",
            (f" ORDER BY p.pay_month DESC, p.created_at DESC LIMIT {_LIST_LIMIT}"),
        )
    else:
        params.append(ids)
        where, order_limit = "p.household_id = %s AND p.id = ANY(%s)", ""
    cur.execute(
        f"""SELECT p.id, p.pay_month, p.currency, p.gross_earnings, p.total_deductions,
                   p.net_pay, p.supersedes_payslip_id, sup.id,
                   (p.source_document_id IS NOT NULL), p.created_at
              FROM payslip p
              LEFT JOIN payslip sup
                     ON sup.household_id = p.household_id AND sup.supersedes_payslip_id = p.id
             WHERE {where}{order_limit}""",
        params,
    )
    headers = cur.fetchall()
    payslip_ids = [UUID(str(row[0])) for row in headers]
    lines_by_id: dict[UUID, list[PayslipLineOut]] = {pid: [] for pid in payslip_ids}
    # countedAsSpending is whether a pair is actually posted for this line; categoryId comes
    # from that posting, else from a matching but not-yet-posted rule (spec "Deductions counted
    # as spending": "categoryId (from the posting, else the matching rule)") — a rule surviving
    # a DELETE toggle (so a later payslip still auto-posts) must not make the toggle look on.
    spending: dict[UUID, tuple[bool, UUID | None]] = {}
    if payslip_ids:
        cur.execute(
            """SELECT l.id, (pp.payslip_line_id IS NOT NULL), t.category_id, r.category_id
                 FROM payslip_line l
                 LEFT JOIN payslip_line_posting pp
                        ON pp.household_id = l.household_id AND pp.payslip_line_id = l.id
                 LEFT JOIN v_transaction_posting t
                        ON t.household_id = l.household_id AND t.id = pp.expense_transaction_id
                 LEFT JOIN payslip_deduction_rule r
                        ON r.household_id = l.household_id AND r.label = l.label
                WHERE l.household_id = %s AND l.payslip_id = ANY(%s) AND l.kind = 'deduction'""",
            (household_id, payslip_ids),
        )
        for line_id, posted, posting_category, rule_category in cur.fetchall():
            category = posting_category if posted else rule_category
            spending[UUID(str(line_id))] = (
                bool(posted),
                None if category is None else UUID(str(category)),
            )
    if payslip_ids:
        cur.execute(
            """SELECT id, payslip_id, line_no, kind::text, label, amount FROM payslip_line
                WHERE household_id = %s AND payslip_id = ANY(%s) ORDER BY payslip_id, line_no""",
            (household_id, payslip_ids),
        )
        for line_id, payslip_id, line_no, kind, label, amount in cur.fetchall():
            counted, category_id = spending.get(UUID(str(line_id)), (False, None))
            lines_by_id[UUID(str(payslip_id))].append(
                PayslipLineOut(
                    lineNo=line_no,
                    kind=kind,
                    label=label,
                    amount=str(amount),
                    countedAsSpending=counted,
                    categoryId=category_id,
                )
            )
    deposits: dict[UUID, DepositOut] = {}
    if payslip_ids:
        cur.execute(
            """SELECT p.id, t.id, t.account_id, a.name, t.booked_date, t.amount,
                      (r.id IS NOT NULL)
                 FROM payslip p
                 JOIN v_transaction_posting t
                   ON t.household_id = p.household_id AND t.id = p.deposit_transaction_id
                 JOIN account a ON a.household_id = t.household_id AND a.id = t.account_id
                 LEFT JOIN v_transaction_reportable r
                   ON r.household_id = p.household_id AND r.id = t.id
                  AND r.txn_type = 'income' AND r.currency = p.currency AND r.amount = p.net_pay
                WHERE p.household_id = %s AND p.id = ANY(%s)
                  AND p.deposit_transaction_id IS NOT NULL""",
            (household_id, payslip_ids),
        )
        for (
            payslip_id,
            txn_id,
            account_id,
            account_name,
            booked,
            amount,
            eligible,
        ) in cur.fetchall():
            deposits[UUID(str(payslip_id))] = DepositOut(
                transactionId=txn_id,
                accountId=account_id,
                accountName=account_name,
                bookedDate=booked,
                amount=str(amount),
                eligible=bool(eligible),
            )
    return [
        PayslipRow(
            id=row[0],
            payMonth=_pay_month_str(row[1]),
            currency=str(row[2]).strip(),
            grossEarnings=str(row[3]),
            totalDeductions=str(row[4]),
            netPay=str(row[5]),
            lines=lines_by_id[UUID(str(row[0]))],
            supersedesPayslipId=row[6],
            supersededByPayslipId=row[7],
            imported=bool(row[8]),
            createdAt=row[9],
            deposit=deposits.get(UUID(str(row[0]))),
        )
        for row in headers
    ]


@router.get("/payslips", response_model=PayslipsResponse)
def list_payslips(owner: Annotated[OwnerSession, Depends(require_owner)]) -> PayslipsResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        rows = _load_payslips(cur, owner.household_id, None)
    return PayslipsResponse(payslips=rows, total=len(rows))


@router.post("/payslips", response_model=PayslipRow)
def create_payslip(
    body: PayslipCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> PayslipRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            payslip_id, created = _record(
                cur,
                body.idempotencyKey,
                None,
                None,
                _pay_month_date(body.payMonth),
                body.currency,
                body.grossEarnings,
                body.totalDeductions,
                body.netPay,
                body.lines,
            )
            response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            rows = _load_payslips(cur, owner.household_id, [payslip_id])
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None
    assert rows
    return rows[0]


@router.post("/payslips/{payslip_id}/corrections", response_model=PayslipRow)
def correct_payslip(
    payslip_id: UUID,
    body: PayslipCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> PayslipRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            # Defensive: `record_payslip` already refuses to correct a linked payslip, and
            # unlinking already removes its pairs, so this is normally a no-op — but the spec
            # states superseding removes pairs too, and a no-op is cheap to guarantee it.
            _remove_pairs_for_payslip(cur, owner.household_id, payslip_id)
            new_id, created = _record(
                cur,
                body.idempotencyKey,
                payslip_id,
                None,
                _pay_month_date(body.payMonth),
                body.currency,
                body.grossEarnings,
                body.totalDeductions,
                body.netPay,
                body.lines,
            )
            response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            rows = _load_payslips(cur, owner.household_id, [new_id])
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None
    assert rows
    return rows[0]


def _canonical(household_id: UUID, month: PayslipMonthDraft) -> str:
    assert month.pay_month is not None
    parts = [
        EXTRACTION_VERSION,
        str(household_id),
        month.pay_month.isoformat(),
        month.currency,
        format(month.net_pay.quantize(Decimal("0.0001")), "f"),
        *(
            f"{line.kind}:{line.label}:{format(line.amount.quantize(Decimal('0.0001')), 'f')}"
            for line in month.lines
        ),
    ]
    return "|".join(parts)


def _import_key(household_id: UUID, month: PayslipMonthDraft) -> UUID:
    return uuid5(PAYSLIP_IMPORT_NAMESPACE, _canonical(household_id, month))


def _month_lines(month: PayslipMonthDraft) -> list[PayslipLineOut]:
    # A preview or freshly imported month has no posting or rule state of its own yet.
    return [
        PayslipLineOut(
            lineNo=index,
            kind=line.kind,
            label=line.label,
            amount=str(line.amount),
            countedAsSpending=False,
            categoryId=None,
        )
        for index, line in enumerate(month.lines, start=1)
    ]


def _month_out(
    month: PayslipMonthDraft,
    reason: PayslipReason | None,
    import_status: Literal["imported", "already_imported", "already_recorded", "not_reconciled"]
    | None,
    payslip_id: UUID | None,
) -> PayslipImportMonth:
    return PayslipImportMonth(
        payMonth=None if month.pay_month is None else _pay_month_str(month.pay_month),
        currency=month.currency,
        grossEarnings=str(month.gross_earnings),
        totalDeductions=str(month.total_deductions),
        netPay=str(month.net_pay),
        lines=_month_lines(month),
        reason=reason,
        status=import_status,
        payslipId=payslip_id,
    )


def _commit_month(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    month: PayslipMonthDraft,
    index: int,
    document_id: UUID | None,
) -> tuple[Literal["imported", "already_imported", "already_recorded"], UUID | None]:
    savepoint = f"payslip_import_month_{index}"
    cur.execute(f"SAVEPOINT {savepoint}")  # noqa: S608 - fixed identifier, not user input
    try:
        assert month.pay_month is not None  # only a month with a resolved block reaches commit
        key = _import_key(household_id, month)
        payslip_id, created = _record(
            cur,
            key,
            None,
            document_id,
            month.pay_month,
            month.currency,
            str(month.gross_earnings),
            str(month.total_deductions),
            str(month.net_pay),
            [
                PayslipLineIn(kind=line.kind, label=line.label, amount=str(line.amount))
                for line in month.lines
            ],
        )
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")  # noqa: S608
        return ("imported" if created else "already_imported"), payslip_id
    except psycopg.Error as exc:
        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")  # noqa: S608
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")  # noqa: S608
        if exc.diag.constraint_name == "payslip_duplicate":
            return "already_recorded", None
        raise


def _known_currencies(cur: psycopg.Cursor[Any], codes: set[str]) -> set[str]:
    if not codes:
        return set()
    cur.execute("SELECT code FROM currency WHERE code = ANY(%s)", (list(codes),))
    return {str(row[0]).strip() for row in cur.fetchall()}


@router.post("/payslips/import", response_model=PayslipImportResponse)
def import_payslips(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    file: Annotated[UploadFile, File()],
    password: Annotated[str | None, Form()] = None,
    commit: Annotated[bool, Form()] = False,
) -> PayslipImportResponse:
    try:
        with uploaded_pages(file, password) as (data, pages):
            extraction = extract_payslips(pages)
            if not extraction.supported:
                return PayslipImportResponse(supported=False, months=[])
            with connect() as conn, conn.cursor() as cur:
                scope_to_owner(conn, owner.user_id)
                known_currencies = _known_currencies(
                    cur, {month.currency for month in extraction.months}
                )
                effective: list[tuple[PayslipMonthDraft, PayslipReason | None]] = [
                    (
                        month,
                        month.reason
                        or (None if month.currency in known_currencies else "unknown_currency"),
                    )
                    for month in extraction.months
                ]
                if not commit:
                    return PayslipImportResponse(
                        supported=True,
                        months=[
                            _month_out(month, reason, None, None) for month, reason in effective
                        ],
                    )
                reconciling = [
                    (index, month)
                    for index, (month, reason) in enumerate(effective)
                    if reason is None
                ]
                document_id: UUID | None = None
                created_new_document = False
                if reconciling:
                    uploaded = storage.store(data, owner.household_id, _SAFE_DOCUMENT_LABEL)
                    cur.execute(
                        """INSERT INTO document (
                               household_id, document_type, original_filename, storage_bucket,
                               storage_path, mime_type, byte_size, sha256, page_count,
                               parsed_status, is_password_protected, uploaded_by)
                           VALUES (%s, 'payslip', %s, 'documents', %s, 'application/pdf', %s,
                                   %s, %s, 'parsed', %s, %s)
                           ON CONFLICT (household_id, sha256) DO NOTHING
                           RETURNING id""",
                        (
                            owner.household_id,
                            uploaded.masked_filename,
                            str(uploaded.path),
                            uploaded.byte_size,
                            uploaded.sha256,
                            len(pages),
                            password is not None,
                            owner.user_id,
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        cur.execute(
                            "SELECT id FROM document WHERE household_id = %s AND sha256 = %s",
                            (owner.household_id, uploaded.sha256),
                        )
                        row = cur.fetchone()
                    else:
                        created_new_document = True
                    assert row is not None
                    document_id = UUID(str(row[0]))
                outcomes: dict[
                    int,
                    tuple[Literal["imported", "already_imported", "already_recorded"], UUID | None],
                ] = {
                    index: _commit_month(cur, owner.household_id, month, index, document_id)
                    for index, month in reconciling
                }
                if (
                    created_new_document
                    and document_id is not None
                    and not any(status == "imported" for status, _ in outcomes.values())
                ):
                    # Nothing in this transaction ended up referencing the document this upload
                    # would otherwise have stored on its own — every reconciling month was
                    # already recorded under a different idempotency key (OL-005). Leave no
                    # orphan document row behind for a file that recorded nothing new.
                    cur.execute(
                        "DELETE FROM document WHERE household_id = %s AND id = %s",
                        (owner.household_id, document_id),
                    )
                months_out = [
                    _month_out(month, None, *outcomes[index])
                    if reason is None
                    else _month_out(month, reason, "not_reconciled", None)
                    for index, (month, reason) in enumerate(effective)
                ]
                return PayslipImportResponse(supported=True, months=months_out)
    except (storage.StorageError, ExtractionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None


@router.get("/payslips/{payslip_id}/deposit-candidates", response_model=DepositCandidatesResponse)
def deposit_candidates(
    payslip_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> DepositCandidatesResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT p.currency, p.net_pay, p.pay_month,
                      EXISTS (SELECT FROM payslip s WHERE s.supersedes_payslip_id = p.id)
                 FROM payslip p WHERE p.household_id = %s AND p.id = %s""",
            (owner.household_id, payslip_id),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="payslip not found")
        currency, net_pay, pay_month, superseded = found
        if superseded:
            return DepositCandidatesResponse(candidates=[])
        redact = _preview_redactor(None)
        cur.execute(
            """SELECT r.id, r.account_id, a.name, r.booked_date, r.amount, r.currency,
                      r.description_clean, r.description_raw
                 FROM v_transaction_reportable r
                 JOIN account a ON a.household_id = r.household_id AND a.id = r.account_id
                WHERE r.household_id = %s AND r.txn_type = 'income'
                  AND r.currency = %s AND r.amount = %s
                  AND r.booked_date BETWEEN %s AND %s
                  AND NOT EXISTS (
                    SELECT FROM payslip p2
                     WHERE p2.household_id = r.household_id AND p2.deposit_transaction_id = r.id)
                ORDER BY r.booked_date ASC, r.id ASC
                LIMIT %s""",
            (
                owner.household_id,
                currency,
                net_pay,
                pay_month,
                _next_month_tenth(pay_month),
                _CANDIDATE_LIMIT,
            ),
        )
        candidates = [
            DepositCandidate(
                transactionId=row[0],
                accountId=row[1],
                accountName=row[2],
                bookedDate=row[3],
                amount=str(row[4]),
                currency=str(row[5]).strip(),
                description=redact(row[6] or row[7] or ""),
            )
            for row in cur.fetchall()
        ]
    return DepositCandidatesResponse(candidates=candidates)


_SALARY_SLUG = "salary"


def _salary_category_id(cur: psycopg.Cursor[Any]) -> UUID:
    cur.execute("SELECT id FROM category WHERE slug = %s AND household_id IS NULL", (_SALARY_SLUG,))
    row = cur.fetchone()
    assert row is not None  # V9 seeds this system category; its absence is a seed bug, not a 422
    return UUID(str(row[0]))


def _has_pair(cur: psycopg.Cursor[Any], household_id: UUID, line_id: UUID) -> bool:
    cur.execute(
        "SELECT 1 FROM payslip_line_posting WHERE household_id = %s AND payslip_line_id = %s",
        (household_id, line_id),
    )
    return cur.fetchone() is not None


def _assert_account_currency(
    cur: psycopg.Cursor[Any], account_id: UUID, currency: str, household_id: UUID
) -> None:
    """Spec 'Deductions counted as spending': native currency is the payslip's, which must equal
    the linked account's — already true by construction (the deposit itself was checked eligible
    in this currency and every transaction matches its own account's currency, V10), checked
    again here defensively rather than trusted silently."""
    cur.execute(
        "SELECT currency FROM account WHERE id = %s AND household_id = %s",
        (account_id, household_id),
    )
    row = cur.fetchone()
    if row is None or str(row[0]).strip() != currency:
        raise HTTPException(
            status_code=422,
            detail="the payslip currency must equal the linked account's currency",
        )


def _post_deduction_pair(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    line_id: UUID,
    label: str,
    amount: Decimal,
    category_id: UUID,
    account_id: UUID,
    booked_date: date,
) -> None:
    """The balanced pair spec section 'Deductions counted as spending' describes: an income leg
    for the deduction categorised Salary and an expense leg in the owner's chosen category, both
    on the linked credit's account and booked date. Both legs freeze through the same
    manual-posting helper `routes/transactions.py::post_ledger_transaction` uses, so I5 holds
    here exactly as it does for a manual entry. The expense leg is signed negative (ADR-0010),
    same as any other spending row, so the pair sums to exactly zero on the account."""
    income_id = post_ledger_transaction(
        cur,
        household_id,
        account_id,
        booked_date,
        f"Payroll: {label} withheld",
        amount,
        "income",
        _salary_category_id(cur),
    )
    expense_id = post_ledger_transaction(
        cur,
        household_id,
        account_id,
        booked_date,
        f"Payroll: {label}",
        -amount,
        "expense",
        category_id,
    )
    cur.execute(
        """INSERT INTO payslip_line_posting (
               household_id, payslip_line_id, income_transaction_id, expense_transaction_id)
           VALUES (%s, %s, %s, %s)""",
        (household_id, line_id, income_id, expense_id),
    )


def _apply_matching_rules(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    payslip_id: UUID,
    account_id: UUID,
    booked_date: date,
) -> None:
    """Linking net pay posts the pair for every deduction line with a saved rule; idempotent —
    a line that already has a pair is skipped (spec 'Deductions counted as spending')."""
    cur.execute(
        """SELECT l.id, l.label, l.amount, r.category_id
             FROM payslip_line l
             JOIN payslip_deduction_rule r
               ON r.household_id = l.household_id AND r.label = l.label
            WHERE l.household_id = %s AND l.payslip_id = %s AND l.kind = 'deduction'
              AND NOT EXISTS (
                    SELECT FROM payslip_line_posting p
                     WHERE p.household_id = l.household_id AND p.payslip_line_id = l.id)""",
        (household_id, payslip_id),
    )
    for line_id, label, amount, category_id in cur.fetchall():
        _post_deduction_pair(
            cur,
            household_id,
            UUID(str(line_id)),
            str(label),
            Decimal(amount),
            UUID(str(category_id)),
            account_id,
            booked_date,
        )


def _remove_pairs_for_payslip(
    cur: psycopg.Cursor[Any], household_id: UUID, payslip_id: UUID
) -> None:
    """Unlinking and superseding remove every pair this payslip posted; the rule that produced
    them is untouched, so a later payslip with a matching label still auto-posts."""
    cur.execute(
        """SELECT p.payslip_line_id, p.income_transaction_id, p.expense_transaction_id
             FROM payslip_line_posting p
             JOIN payslip_line l ON l.household_id = p.household_id AND l.id = p.payslip_line_id
            WHERE p.household_id = %s AND l.payslip_id = %s""",
        (household_id, payslip_id),
    )
    for line_id, income_id, expense_id in cur.fetchall():
        cur.execute(
            "DELETE FROM payslip_line_posting WHERE household_id = %s AND payslip_line_id = %s",
            (household_id, line_id),
        )
        delete_ledger_transaction(cur, household_id, UUID(str(income_id)))
        delete_ledger_transaction(cur, household_id, UUID(str(expense_id)))


@router.put("/payslips/{payslip_id}/deposit", response_model=PayslipRow)
def link_deposit(
    payslip_id: UUID,
    body: DepositLinkRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> PayslipRow:
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            cur.execute("SELECT id FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT deposit_transaction_id, currency FROM payslip
                    WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, payslip_id),
            )
            found = cur.fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="payslip not found")
            current_deposit, payslip_currency = found[0], str(found[1]).strip()
            if current_deposit is not None and UUID(str(current_deposit)) != body.transactionId:
                raise HTTPException(
                    status_code=409,
                    detail="this payslip is already linked to a different credit; unlink first",
                )
            cur.execute(
                """SELECT account_id, booked_date FROM v_transaction_posting
                    WHERE household_id = %s AND id = %s""",
                (owner.household_id, body.transactionId),
            )
            posting = cur.fetchone()
            if posting is None:
                raise HTTPException(status_code=404, detail="transaction not found")
            account_id, booked_date = UUID(str(posting[0])), posting[1]
            _assert_account_currency(cur, account_id, payslip_currency, owner.household_id)
            cur.execute(
                """UPDATE payslip SET deposit_transaction_id = %s
                    WHERE household_id = %s AND id = %s""",
                (body.transactionId, owner.household_id, payslip_id),
            )
            _apply_matching_rules(cur, owner.household_id, payslip_id, account_id, booked_date)
            rows = _load_payslips(cur, owner.household_id, [payslip_id])
    except psycopg.Error as exc:
        raise _translate_database_error(exc) from None
    assert rows
    return rows[0]


@router.delete("/payslips/{payslip_id}/deposit", response_model=PayslipRow)
def unlink_deposit(
    payslip_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> PayslipRow:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute("SELECT id FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        cur.execute(
            "SELECT id FROM payslip WHERE household_id = %s AND id = %s FOR UPDATE",
            (owner.household_id, payslip_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="payslip not found")
        _remove_pairs_for_payslip(cur, owner.household_id, payslip_id)
        cur.execute(
            "UPDATE payslip SET deposit_transaction_id = NULL WHERE household_id = %s AND id = %s",
            (owner.household_id, payslip_id),
        )
        rows = _load_payslips(cur, owner.household_id, [payslip_id])
    assert rows
    return rows[0]


@router.get("/payslips/deduction-rules", response_model=DeductionRulesResponse)
def list_deduction_rules(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> DeductionRulesResponse:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT label, category_id FROM payslip_deduction_rule
                WHERE household_id = %s ORDER BY label""",
            (owner.household_id,),
        )
        rules = [DeductionRuleRow(label=str(row[0]), categoryId=row[1]) for row in cur.fetchall()]
    return DeductionRulesResponse(rules=rules)


@router.put("/payslips/deduction-rules/{label}", response_model=DeductionRuleRow)
def save_deduction_rule(
    label: str,
    body: DeductionRuleIn,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> DeductionRuleRow:
    normalized = _normalize_label(label)
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        _assert_category_allowed(cur, body.categoryId, owner.household_id, kind="expense")
        cur.execute(
            """INSERT INTO payslip_deduction_rule (household_id, label, category_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (household_id, label)
               DO UPDATE SET category_id = EXCLUDED.category_id, updated_at = now()""",
            (owner.household_id, normalized, body.categoryId),
        )
    return DeductionRuleRow(label=normalized, categoryId=body.categoryId)


@router.delete("/payslips/deduction-rules/{label}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deduction_rule(
    label: str, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    normalized = _normalize_label(label)
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            "DELETE FROM payslip_deduction_rule WHERE household_id = %s AND label = %s",
            (owner.household_id, normalized),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _payslip_line(
    cur: psycopg.Cursor[Any], household_id: UUID, payslip_id: UUID, line_no: int
) -> tuple[UUID, str, Decimal, str]:
    """(id, label, amount, kind) of one line, addressed by its `lineNo` — the identifier the API
    already exposes on `PayslipLineOut`, so this route needs no new field. Scoped to this
    household and this payslip: a foreign or mismatched line reads as not found, matching every
    other payslip route."""
    cur.execute(
        """SELECT id, label, amount, kind::text FROM payslip_line
            WHERE household_id = %s AND payslip_id = %s AND line_no = %s""",
        (household_id, payslip_id, line_no),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="payslip line not found")
    return UUID(str(row[0])), str(row[1]), Decimal(row[2]), str(row[3])


@router.post("/payslips/{payslip_id}/lines/{line_no}/spending", response_model=PayslipRow)
def count_line_as_spending(
    payslip_id: UUID,
    line_no: int,
    body: LineSpendingIn,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> PayslipRow:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute("SELECT id FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        line_id, label, amount, kind = _payslip_line(cur, owner.household_id, payslip_id, line_no)
        if kind != "deduction":
            raise HTTPException(
                status_code=422, detail="only a deduction line can count as spending"
            )
        _assert_category_allowed(cur, body.categoryId, owner.household_id, kind="expense")
        cur.execute(
            """INSERT INTO payslip_deduction_rule (household_id, label, category_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (household_id, label)
               DO UPDATE SET category_id = EXCLUDED.category_id, updated_at = now()""",
            (owner.household_id, label, body.categoryId),
        )
        # Idempotent: a line already posted keeps its existing pair rather than doubling it.
        if not _has_pair(cur, owner.household_id, line_id):
            cur.execute(
                """SELECT deposit_transaction_id, currency FROM payslip
                    WHERE household_id = %s AND id = %s""",
                (owner.household_id, payslip_id),
            )
            header = cur.fetchone()
            assert header is not None  # _payslip_line above already proved the payslip exists
            deposit_id, currency = header[0], str(header[1]).strip()
            if deposit_id is not None:
                cur.execute(
                    """SELECT account_id, booked_date FROM v_transaction_posting
                        WHERE household_id = %s AND id = %s""",
                    (owner.household_id, deposit_id),
                )
                posting = cur.fetchone()
                assert posting is not None
                account_id, booked_date = UUID(str(posting[0])), posting[1]
                _assert_account_currency(cur, account_id, currency, owner.household_id)
                _post_deduction_pair(
                    cur,
                    owner.household_id,
                    line_id,
                    label,
                    amount,
                    body.categoryId,
                    account_id,
                    booked_date,
                )
        rows = _load_payslips(cur, owner.household_id, [payslip_id])
    assert rows
    return rows[0]


@router.delete("/payslips/{payslip_id}/lines/{line_no}/spending", response_model=PayslipRow)
def stop_counting_line_as_spending(
    payslip_id: UUID,
    line_no: int,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> PayslipRow:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute("SELECT id FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
        line_id, _label, _amount, kind = _payslip_line(cur, owner.household_id, payslip_id, line_no)
        if kind != "deduction":
            raise HTTPException(
                status_code=422, detail="only a deduction line can count as spending"
            )
        cur.execute(
            """SELECT income_transaction_id, expense_transaction_id FROM payslip_line_posting
                WHERE household_id = %s AND payslip_line_id = %s""",
            (owner.household_id, line_id),
        )
        pair = cur.fetchone()
        if pair is not None:
            cur.execute(
                """DELETE FROM payslip_line_posting
                    WHERE household_id = %s AND payslip_line_id = %s""",
                (owner.household_id, line_id),
            )
            delete_ledger_transaction(cur, owner.household_id, UUID(str(pair[0])))
            delete_ledger_transaction(cur, owner.household_id, UUID(str(pair[1])))
        rows = _load_payslips(cur, owner.household_id, [payslip_id])
    assert rows
    return rows[0]
