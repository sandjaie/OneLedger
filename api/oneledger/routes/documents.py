# api/oneledger/routes/documents.py
"""Phase 1.8 Task 2: the document library and the owner-only original download.

One query lists every stored document with its parse and import status, whatever produced it —
a statement batch, an investment import or a payslip — through lateral lookups that each repeat
the household predicate (schema.md 0018's tenancy rule applies to a join the same as a table).
The row never carries a private column: no filename, storage location, hash, size, notes, tags,
parse error or extracted metadata reaches the wire (security-and-privacy.html).

`GET /documents/{id}/original` is the one route that reads the bytes back, through
`description_reparse.verified_original` — the same path and hash checks the description re-read
already trusted its original to.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect
from .description_reparse import verified_original

router = APIRouter(tags=["documents"])

# schema.md 0008's `document_type` enum, restated as a closed set so an unknown value is this
# route's own 422 rather than a database CHECK failure with a less legible message.
_DOCUMENT_TYPES = (
    "bank_statement",
    "card_statement",
    "payslip",
    "loan_statement",
    "investment_statement",
    "cas",
    "tax_document",
    "insurance_policy",
    "property_document",
    "fd_certificate",
    "receipt",
    "other",
)
# The union of `import_status` (statements) and `investment_import_status` (investment imports):
# a filter names either vocabulary, and the computed `import_status` column below can hold either.
_IMPORT_STATUSES = (
    "uploaded",
    "extracting",
    "normalizing",
    "deduping",
    "classifying",
    "matching_transfers",
    "reconciling",
    "awaiting_review",
    "importing",
    "completed",
    "failed",
    "cancelled",
    "staged",
    "partially_committed",
)


class DocumentPeriod(BaseModel):
    start: date | None
    end: date | None
    isDerived: bool | None


class DocumentRow(BaseModel):
    id: UUID
    documentType: str
    pageCount: int | None
    isPasswordProtected: bool
    parsedStatus: str
    uploadedOn: datetime
    accountId: UUID | None
    accountName: str | None
    accountNumberMasked: str | None
    period: DocumentPeriod
    importKind: str
    importId: UUID | None
    importStatus: str | None
    reconciliationStatus: str | None
    layoutId: UUID | None
    layoutVersion: int | None
    newerLayoutId: UUID | None


class DocumentsResponse(BaseModel):
    documents: list[DocumentRow]
    total: int


# The document's own kind, and where its import identity and status come from — never a filename,
# a title or anything else the document table would otherwise carry off this route.
_ROW = """
    d.id, d.document_type::text, d.page_count, d.is_password_protected,
    d.parsed_status::text, d.uploaded_at, d.account_id, a.name,
    mask.masked_value, d.period_start, d.period_end, batch.period_is_derived,
    CASE WHEN d.document_type = 'payslip' THEN 'payslip'
         WHEN d.document_type = 'investment_statement' THEN 'investment'
         ELSE 'statement' END,
    CASE WHEN d.document_type = 'payslip' THEN NULL
         WHEN d.document_type = 'investment_statement' THEN inv.import_id
         ELSE batch.import_id END,
    CASE WHEN d.document_type = 'payslip'
              THEN (CASE WHEN payslip.referenced THEN 'completed' END)
         WHEN d.document_type = 'investment_statement' THEN inv.import_status
         ELSE batch.import_status END,
    CASE WHEN d.document_type NOT IN ('payslip', 'investment_statement')
              THEN batch.reconciliation_status END,
    d.statement_layout_id, d.layout_version, newer.newer_layout_id"""

_COLUMN_NAMES = (
    "id, document_type, page_count, is_password_protected, parsed_status, uploaded_on, "
    "account_id, account_name, account_number_masked, period_start, period_end, "
    "period_is_derived, import_kind, import_id, import_status, reconciliation_status, "
    "layout_id, layout_version, newer_layout_id"
)

_FROM = """
    FROM document d
    LEFT JOIN account a ON a.id = d.account_id AND a.household_id = d.household_id
    LEFT JOIN LATERAL (
        SELECT ai.masked_value FROM account_identifier ai
         WHERE ai.household_id = d.household_id AND ai.account_id = d.account_id AND ai.is_active
         ORDER BY ai.created_at DESC LIMIT 1
    ) mask ON d.account_id IS NOT NULL
    LEFT JOIN LATERAL (
        SELECT b.id AS import_id, b.status::text AS import_status,
               b.reconciliation_status, b.period_is_derived
          FROM import_batch b
         WHERE b.household_id = d.household_id AND b.document_id = d.id
         ORDER BY b.created_at DESC LIMIT 1
    ) batch ON true
    LEFT JOIN LATERAL (
        SELECT ii.id AS import_id, ii.status::text AS import_status
          FROM investment_import ii
         WHERE ii.household_id = d.household_id AND ii.document_id = d.id
         LIMIT 1
    ) inv ON true
    LEFT JOIN LATERAL (
        SELECT true AS referenced
          FROM payslip p
         WHERE p.household_id = d.household_id AND p.source_document_id = d.id
         LIMIT 1
    ) payslip ON true
    LEFT JOIN LATERAL (
        SELECT newer.id AS newer_layout_id
          FROM statement_layout mine
          JOIN statement_layout newer
            ON newer.household_id = mine.household_id AND newer.name = mine.name
           AND newer.version > mine.version
         WHERE mine.id = d.statement_layout_id AND mine.household_id = d.household_id
         ORDER BY newer.version DESC LIMIT 1
    ) newer ON d.statement_layout_id IS NOT NULL"""

_FIELDS = [
    "id",
    "documentType",
    "pageCount",
    "isPasswordProtected",
    "parsedStatus",
    "uploadedOn",
    "accountId",
    "accountName",
    "accountNumberMasked",
    "periodStart",
    "periodEnd",
    "periodIsDerived",
    "importKind",
    "importId",
    "importStatus",
    "reconciliationStatus",
    "layoutId",
    "layoutVersion",
    "newerLayoutId",
]


def _document_row(values: tuple[Any, ...]) -> DocumentRow:
    fields = dict(zip(_FIELDS, values, strict=True))
    return DocumentRow(
        id=fields["id"],
        documentType=fields["documentType"],
        pageCount=fields["pageCount"],
        isPasswordProtected=fields["isPasswordProtected"],
        parsedStatus=fields["parsedStatus"],
        uploadedOn=fields["uploadedOn"],
        accountId=fields["accountId"],
        accountName=fields["accountName"],
        accountNumberMasked=fields["accountNumberMasked"],
        period=DocumentPeriod(
            start=fields["periodStart"],
            end=fields["periodEnd"],
            isDerived=fields["periodIsDerived"],
        ),
        importKind=fields["importKind"],
        importId=fields["importId"],
        importStatus=fields["importStatus"],
        reconciliationStatus=fields["reconciliationStatus"],
        layoutId=fields["layoutId"],
        layoutVersion=fields["layoutVersion"],
        newerLayoutId=fields["newerLayoutId"],
    )


@router.get("/documents")
def list_documents(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    documentType: Annotated[
        str | None, Query(pattern="^(" + "|".join(_DOCUMENT_TYPES) + ")$")
    ] = None,
    accountId: Annotated[UUID | None, Query()] = None,
    periodFrom: Annotated[date | None, Query()] = None,
    periodTo: Annotated[date | None, Query()] = None,
    importStatus: Annotated[
        str | None, Query(pattern="^(" + "|".join(_IMPORT_STATUSES) + ")$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentsResponse:
    if periodFrom is not None and periodTo is not None and periodFrom > periodTo:
        raise HTTPException(status_code=422, detail="periodFrom is after periodTo")

    clauses = ["d.household_id = %s"]
    params: list[Any] = [owner.household_id]
    if documentType is not None:
        clauses.append("d.document_type = %s::document_type")
        params.append(documentType)
    if accountId is not None:
        clauses.append("d.account_id = %s")
        params.append(accountId)
    if periodFrom is not None or periodTo is not None:
        clauses.append("d.period_start IS NOT NULL AND d.period_end IS NOT NULL")
        if periodFrom is not None:
            clauses.append("d.period_end >= %s")
            params.append(periodFrom)
        if periodTo is not None:
            clauses.append("d.period_start <= %s")
            params.append(periodTo)
    inner = f"SELECT {_ROW} {_FROM} WHERE {' AND '.join(clauses)}"

    outer_clauses = []
    outer_params: list[Any] = []
    if importStatus is not None:
        outer_clauses.append("import_status = %s")
        outer_params.append(importStatus)
    outer_where = " AND ".join(outer_clauses) if outer_clauses else "true"

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM ({inner}) resolved({_COLUMN_NAMES}) WHERE {outer_where}",
            (*params, *outer_params),
        )
        counted = cur.fetchone()
        total = int(counted[0]) if counted else 0
        cur.execute(
            f"""SELECT * FROM ({inner}) resolved({_COLUMN_NAMES})
                 WHERE {outer_where}
                 ORDER BY uploaded_on DESC, id
                 LIMIT %s OFFSET %s""",
            (*params, *outer_params, limit, offset),
        )
        rows = [_document_row(row) for row in cur.fetchall()]
    return DocumentsResponse(documents=rows, total=total)


@router.get("/documents/{document_id}/original")
def download_original(
    document_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT storage_bucket, storage_path, sha256
                 FROM document WHERE id = %s AND household_id = %s""",
            (document_id, owner.household_id),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such document")
    bucket, storage_path, sha256 = row
    if bucket != "documents":
        raise HTTPException(status_code=409, detail="the original uses an unsupported store")
    data = verified_original(str(storage_path), str(sha256))
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="document.pdf"',
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
