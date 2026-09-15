"""Owner-only official price status and bounded refresh."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner
from ..investments.prices import RefreshOutcome, refresh

router = APIRouter(prefix="/investment-prices", tags=["investment prices"])


def get_price_client() -> Iterator[httpx.Client]:
    with httpx.Client() as client:
        yield client


@router.post("/refresh", response_model=RefreshOutcome)
def price_refresh(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    client: Annotated[httpx.Client, Depends(get_price_client)],
) -> RefreshOutcome:
    """A no-op within 24 hours of a successful check; an outage keeps cached prices."""
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        return refresh(cur, owner.household_id, client)
