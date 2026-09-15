"""`GET /investment-reviews`: this household's open investment review queue."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..investments.adapters import ReviewReason

router = APIRouter(prefix="/investment-reviews", tags=["investment reviews"])

_LIST_SQL = """
    SELECT r.id, r.reason::text, r.is_blocking, r.holding_id, security.name, account.name,
           r.import_id, r.investment_transaction_id, r.created_at
      FROM investment_review_item r
      LEFT JOIN holding h ON h.household_id = r.household_id AND h.id = r.holding_id
      LEFT JOIN security ON security.id = h.security_id
      LEFT JOIN account ON account.id = h.account_id
     WHERE r.household_id = %s AND r.status = 'open'
     ORDER BY r.created_at DESC, r.id DESC
     LIMIT %s OFFSET %s
"""


class ReviewItemRow(BaseModel):
    id: UUID
    reason: ReviewReason
    isBlocking: bool
    holdingId: UUID | None
    securityName: str | None
    accountName: str | None
    importId: UUID | None
    investmentTransactionId: UUID | None
    createdAt: datetime


@router.get("")
def list_reviews(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ReviewItemRow]:
    """Every open review this household owns, newest first — a holding-scoped row (lot
    allocation) links from the holding, security and account joined here; an import-scoped row
    (no holding) carries only its `importId`."""
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(_LIST_SQL, (owner.household_id, limit, offset))
        return [
            ReviewItemRow(
                id=row[0],
                reason=row[1],
                isBlocking=row[2],
                holdingId=row[3],
                securityName=row[4],
                accountName=row[5],
                importId=row[6],
                investmentTransactionId=row[7],
                createdAt=row[8],
            )
            for row in cur.fetchall()
        ]
