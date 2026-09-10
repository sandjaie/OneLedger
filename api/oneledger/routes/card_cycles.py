"""Slice 1.4: card cycles for statements committed before their layout could read them.

A card statement committed while its layout captured none of the cycle's facts has its rows and
no `card_statement`. This re-reads each such statement's stored original — originals are
immutable (I1), so it is the same document — with the newest version of the layout it was
imported under, and writes the cycle through the commit's own `_write_card_cycle`. A statement it
cannot complete is named with a structural reason and left without a cycle: no fact is guessed
(Invariant 7). One statement per transaction, so one refusal blocks only itself, and only
statements with no cycle are read, so a second run writes nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..ingest.engine import extract_pages
from ..ingest.normalize import card_facts
from ..ingest.storage import _documents_root
from ..ingest.words import ExtractionError
from ..ingest.words import extract_pages as read_pdf
from . import ingest, layouts

router = APIRouter(tags=["import"])


class SkippedStatement(BaseModel):
    importBatchId: UUID
    # Composed here from fixed phrases and fact names, never from the document's text (§5.6).
    reason: str


class BackfillResult(BaseModel):
    written: int
    skipped: list[SkippedStatement]


# Committed card statements with no cycle, each with the newest version of its layout's name.
_WITHOUT_A_CYCLE = """
    SELECT b.id, d.storage_path, newest.id
      FROM import_batch b
      JOIN document d ON d.id = b.document_id
      LEFT JOIN statement_layout used ON used.id = b.statement_layout_id
      LEFT JOIN LATERAL (
          SELECT l.id FROM statement_layout l
           WHERE l.household_id = b.household_id AND l.name = used.name
           ORDER BY l.version DESC LIMIT 1
      ) newest ON true
     WHERE b.household_id = %s AND b.status = 'completed'
       AND d.document_type = 'card_statement'
       AND NOT EXISTS (SELECT 1 FROM card_statement c WHERE c.source_document_id = d.id)
     ORDER BY b.period_end, b.id"""


@router.post("/card-statements/backfill")
def backfill_card_cycles(
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> BackfillResult:
    root = _documents_root()
    written, skipped = 0, []
    with connect() as conn, conn.cursor() as cur:
        # Scoped before reading as well as before writing: the policies read the owner's identity.
        scope_to_owner(conn, owner.user_id)
        cur.execute(_WITHOUT_A_CYCLE, (owner.household_id,))
        candidates = cur.fetchall()
        conn.commit()
        for batch_id, storage_path, layout_id in candidates:
            reason = _backfill_one(conn, owner, batch_id, root, storage_path, layout_id)
            if reason is None:
                written += 1
            else:
                skipped.append(SkippedStatement(importBatchId=batch_id, reason=reason))
    return BackfillResult(written=written, skipped=skipped)


def _backfill_one(
    conn: psycopg.Connection,
    owner: OwnerSession,
    batch_id: UUID,
    root: Path,
    storage_path: str,
    layout_id: UUID | None,
) -> str | None:
    """None when the cycle was written; otherwise why it was not.

    The original is always tried: `is_password_protected` records that a password was offered at
    import, not that the file needs one, and every card statement in the ledger that carries the
    flag opens without it. Only a file that will not open is refused.
    """
    if layout_id is None:
        return "it names no layout to read its original with"
    stored = Path(storage_path)
    try:
        pages = read_pdf(stored if stored.is_absolute() else root / stored)
    except ExtractionError:
        return "its original will not open; if it is password-protected, import it again"
    except OSError:
        return "its original could not be read"
    with conn.cursor() as cur:
        try:
            scope_to_owner(conn, owner.user_id)
            profile = layouts._one(cur, layout_id, owner.household_id)
            facts, _ = card_facts(extract_pages(profile, pages).metadata, profile.dateFormat)
            batch = ingest._batch_row(cur, batch_id, owner.household_id)
            cycle_id, missing = ingest._write_card_cycle(cur, batch, owner.household_id, facts)
        except HTTPException as exc:
            conn.rollback()
            return str(exc.detail)
        if cycle_id is None:
            conn.rollback()
            return f"its cycle needs {', '.join(missing)}, which its original does not state"
        conn.commit()
        return None
