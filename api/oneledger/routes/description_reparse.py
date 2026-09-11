"""Preview-first repair of derived descriptions after a layout correction.

The imported ``description_raw`` remains the immutable audit record. This route will update only
the cleaned derivative after the stored original reproduces every immutable row fact exactly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..ingest import engine, normalize, reconcile
from ..ingest.normalize import NormalizedRow
from ..ingest.storage import _documents_root
from ..ingest.words import ExtractionError, PdfPasswordError
from ..ingest.words import extract_pages as read_pdf_pages
from . import layouts

router = APIRouter(tags=["import"])


class DescriptionReparseRequest(BaseModel):
    layoutId: UUID
    apply: bool = False
    password: str | None = Field(default=None, max_length=4096, repr=False)


class DescriptionReparseResult(BaseModel):
    batchId: UUID
    applied: bool
    rowsCompared: int
    descriptionsChanged: int
    rowsQueuedForReview: int


_BATCH = """SELECT b.document_id, b.account_id, b.status::text, b.statement_layout_id,
                   b.layout_version, used.name, d.storage_bucket, d.storage_path, d.sha256,
                   a.currency
              FROM import_batch b
              JOIN document d ON d.id = b.document_id AND d.household_id = b.household_id
              JOIN account a ON a.id = b.account_id AND a.household_id = b.household_id
              LEFT JOIN statement_layout used ON used.id = b.statement_layout_id
             WHERE b.id = %s AND b.household_id = %s"""

_STAGED = """SELECT source_page_number, source_row_number, booked_date, value_date,
                     amount, currency, original_amount, original_currency, status
                FROM staged_transaction
               WHERE import_batch_id = %s AND household_id = %s
               ORDER BY source_row_number"""

_COMMITTED = """SELECT id, source_row_number, description_clean, is_reviewed, transfer_id
                   FROM transaction
                  WHERE import_batch_id = %s AND household_id = %s
                    AND parent_transaction_id IS NULL
                  ORDER BY source_row_number"""


def _batch(
    cur: psycopg.Cursor[Any], batch_id: UUID, household_id: UUID, *, lock: bool
) -> tuple[Any, ...]:
    cur.execute(_BATCH + (" FOR UPDATE OF b" if lock else ""), (batch_id, household_id))
    row: tuple[Any, ...] | None = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="no such import batch")
    if row[2] != "completed":
        raise HTTPException(status_code=409, detail="only a completed import can be reparsed")
    if row[3] is None or row[4] is None or row[5] is None:
        raise HTTPException(status_code=409, detail="the import has no complete layout provenance")
    if row[6] != "documents":
        raise HTTPException(status_code=409, detail="the original uses an unsupported store")
    return row


def _stored_original(storage_path: str) -> Path:
    root = _documents_root().resolve()
    candidate = Path(storage_path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    if candidate.is_symlink():
        raise HTTPException(status_code=409, detail="the stored original failed its path check")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise HTTPException(
            status_code=409, detail="the stored original could not be read"
        ) from None
    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=409, detail="the stored original failed its path check")
    return resolved


@contextmanager
def _verified_pages(
    storage_path: str, expected_sha256: str, password: str | None
) -> Iterator[list[Any]]:
    try:
        data = _stored_original(storage_path).read_bytes()
    except OSError:
        raise HTTPException(
            status_code=409, detail="the stored original could not be read"
        ) from None
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise HTTPException(
            status_code=409, detail="the stored original failed its integrity check"
        )

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(prefix="oneledger-reparse-", suffix=".pdf", delete=False) as temp:
            temporary_path = Path(temp.name)
            temp.write(data)
        try:
            yield read_pdf_pages(temporary_path, password=password)
        except PdfPasswordError:
            raise HTTPException(
                status_code=422,
                detail="the stored original could not be parsed with the supplied password",
            ) from None
        except ExtractionError:
            raise HTTPException(
                status_code=422, detail="the stored original is malformed or unsupported"
            ) from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _same_source(staged: tuple[Any, ...], row: NormalizedRow) -> bool:
    return (
        staged[0] == row.source_page_number
        and staged[1] == row.source_row_number
        and staged[2] == row.booked_date
        and staged[3] == row.value_date
        and staged[4] == row.amount
        and str(staged[5]).strip() == row.currency
        and staged[6] == row.original_amount
        and (None if staged[7] is None else str(staged[7]).strip()) == row.original_currency
    )


def _validated_rows(
    cur: psycopg.Cursor[Any],
    batch_id: UUID,
    household_id: UUID,
    rows: list[NormalizedRow],
    *,
    lock: bool,
) -> tuple[list[tuple[Any, ...]], dict[int, NormalizedRow]]:
    cur.execute(_STAGED + (" FOR UPDATE" if lock else ""), (batch_id, household_id))
    staged = cur.fetchall()
    by_number = {row.source_row_number: row for row in rows}
    staged_numbers = [int(row[1]) for row in staged]
    if (
        len(staged) != len(rows)
        or len(by_number) != len(rows)
        or staged_numbers != sorted(by_number)
        or not all(_same_source(old, by_number[int(old[1])]) for old in staged)
    ):
        raise HTTPException(status_code=409, detail="the reparsed row structure does not match")

    accepted = {int(row[1]) for row in staged if row[8] == "accepted"}
    cur.execute(_COMMITTED + (" FOR UPDATE" if lock else ""), (batch_id, household_id))
    committed = cur.fetchall()
    committed_numbers = [int(row[1]) for row in committed]
    if len(committed_numbers) != len(set(committed_numbers)) or set(committed_numbers) != accepted:
        raise HTTPException(status_code=409, detail="the committed rows do not match the import")
    cur.execute(
        """SELECT EXISTS (
               SELECT 1 FROM transaction
                WHERE import_batch_id = %s AND household_id = %s
                  AND parent_transaction_id IS NOT NULL)""",
        (batch_id, household_id),
    )
    if bool(cur.fetchone()[0]):  # type: ignore[index]
        raise HTTPException(status_code=409, detail="a split import cannot be reparsed in place")
    return committed, by_number


@router.post("/import/{batch_id}/reparse-descriptions")
def reparse_descriptions(
    batch_id: UUID,
    body: DescriptionReparseRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> DescriptionReparseResult:
    with connect() as conn, conn.cursor() as cur:
        batch = _batch(cur, batch_id, owner.household_id, lock=body.apply)
        profile = layouts._one(cur, body.layoutId, owner.household_id)
        if profile.name != batch[5] or profile.version <= int(batch[4]):
            raise HTTPException(
                status_code=409,
                detail="the repair layout must be a newer version of the imported layout",
            )

        with _verified_pages(str(batch[7]), str(batch[8]), body.password) as pages:
            extraction = engine.extract_pages(profile, pages)
            normalized = normalize.normalize(
                profile, extraction, account_currency=str(batch[9]).strip()
            )
            if normalized.failures:
                raise HTTPException(
                    status_code=409, detail="the reparsed statement has normalization failures"
                )
            verdict = reconcile.reconcile(
                profile,
                normalized.rows,
                declared_opening=extraction.declared_opening,
                declared_closing=extraction.declared_closing,
            )
            if verdict.status != reconcile.STATUS_MATCHED:
                raise HTTPException(status_code=409, detail="the reparsed statement does not match")

        committed, by_number = _validated_rows(
            cur,
            batch_id,
            owner.household_id,
            normalized.rows,
            lock=body.apply,
        )
        changed = [
            (
                row_id,
                by_number[int(row_number)].description_clean,
                bool(is_reviewed),
                transfer_id is None,
            )
            for row_id, row_number, description_clean, is_reviewed, transfer_id in committed
            if description_clean != by_number[int(row_number)].description_clean
        ]
        queued = sum(1 for _, _, was_reviewed, may_queue in changed if was_reviewed and may_queue)
        if body.apply:
            cur.executemany(
                """UPDATE transaction
                      SET description_clean = %s,
                          is_reviewed = CASE WHEN %s THEN false ELSE is_reviewed END,
                          updated_at = now()
                    WHERE id = %s AND household_id = %s""",
                [
                    (description, may_queue, row_id, owner.household_id)
                    for row_id, description, _, may_queue in changed
                ],
            )

    return DescriptionReparseResult(
        batchId=batch_id,
        applied=body.apply,
        rowsCompared=len(normalized.rows),
        descriptionsChanged=len(changed),
        rowsQueuedForReview=queued if body.apply else 0,
    )
