"""Owner-only acquisition position, portfolio summary and holding-detail read models."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..investments.portfolio import (
    HoldingDetail,
    PortfolioSummary,
    read_holding_detail,
    read_summary,
)
from ..investments.positions import AcquisitionPosition, PortfolioPositions, read_positions
from ..investments.returns import holding_flows, xirr

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _with_xirr(
    cur: psycopg.Cursor[Any], position: AcquisitionPosition, as_of: date
) -> AcquisitionPosition:
    """A holding's own realised-plus-open XIRR, computed from the same engine `/holdings` uses."""
    flows = holding_flows(cur, position.holdingId, as_of)
    if flows.flows is None:
        return position.model_copy(update={"xirrPercent": None, "xirrReason": flows.reason})
    result = xirr(flows.flows)
    percent = None if result.rate_percent is None else str(result.rate_percent)
    return position.model_copy(update={"xirrPercent": percent, "xirrReason": result.reason})


def _household_currencies(cur: psycopg.Cursor[Any], household_id: UUID) -> tuple[str, str]:
    cur.execute(
        "SELECT base_currency, secondary_currency FROM household WHERE id = %s",
        (household_id,),
    )
    row = cur.fetchone()
    assert row is not None
    return str(row[0]).strip(), str(row[1]).strip()


@router.get("/positions", response_model=PortfolioPositions)
def portfolio_positions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
    includeClosed: Annotated[bool, Query()] = False,
) -> PortfolioPositions:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        base, secondary = _household_currencies(cur, owner.household_id)
        positions = read_positions(
            cur, owner.household_id, base, secondary, as_of, include_closed=includeClosed
        )
        enriched = [_with_xirr(cur, position, as_of) for position in positions]
        return PortfolioPositions(
            asOf=as_of, baseCurrency=base, secondaryCurrency=secondary, positions=enriched
        )


@router.get("/summary", response_model=PortfolioSummary)
def portfolio_summary(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> PortfolioSummary:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        base, secondary = _household_currencies(cur, owner.household_id)
        return read_summary(cur, owner.household_id, base, secondary, as_of)


@router.get("/holdings/{holdingId}", response_model=HoldingDetail)
def holding_detail(
    holdingId: UUID,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> HoldingDetail:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        detail = read_holding_detail(cur, owner.household_id, holdingId)
        if detail is None:
            raise HTTPException(status_code=404, detail="holding not found")
        return detail
