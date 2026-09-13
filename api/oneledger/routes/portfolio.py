"""Owner-only acquisition position read model."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..investments.positions import PortfolioPositions, read_positions

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/positions", response_model=PortfolioPositions)
def portfolio_positions(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    asOf: Annotated[date | None, Query()] = None,
) -> PortfolioPositions:
    as_of = asOf or date.today()
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            "SELECT base_currency, secondary_currency FROM household WHERE id = %s",
            (owner.household_id,),
        )
        row = cur.fetchone()
        assert row is not None
        base, secondary = str(row[0]).strip(), str(row[1]).strip()
        return PortfolioPositions(
            asOf=as_of,
            baseCurrency=base,
            secondaryCurrency=secondary,
            positions=read_positions(cur, owner.household_id, base, secondary, as_of),
        )
