# api/oneledger/routes/fx.py
from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..fxrange import fill_range

router = APIRouter(prefix="/fx", tags=["fx"])

# A caller mistyping a year rather than a day still asks for a range no owner backfills in one
# request; refusing it early names the fix (split the range) instead of running an ECB fetch
# that would take minutes.
_MAX_BACKFILL_DAYS = 400


class FxRateResponse(BaseModel):
    base: str = Field(min_length=3, max_length=3)
    quote: str = Field(min_length=3, max_length=3)
    rate: str
    source: str
    carried: bool
    carried_from: date | None


@router.post("/backfill")
def backfill(
    start: date, end: date, _: Annotated[OwnerSession, Depends(require_owner)]
) -> dict[str, int]:
    # carry_forward fills every calendar day in the range, so an end date in the future
    # writes the last known rate forward into it — years of invented history from one typo.
    # Nothing is silently invented (Invariant 7): refuse rather than clamp, so the caller
    # learns their range was wrong instead of quietly getting a different one.
    # One day of slack, deliberately. The service runs UTC and the caller may not: a request
    # for their genuine "today" from UTC+4 arrives a calendar day ahead of the server's, and
    # rejecting it would refuse a correct range. A typo like 2030-01-01 is still refused.
    if end > date.today() + timedelta(days=1):
        raise HTTPException(status_code=422, detail="end date is in the future")
    if start > end:
        raise HTTPException(status_code=422, detail="start date is after end date")
    if (end - start).days > _MAX_BACKFILL_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"range exceeds {_MAX_BACKFILL_DAYS} days; split it into smaller backfills",
        )

    with connect() as conn:
        return {"written": fill_range(conn, start, end)}


@router.get("/{on}")
def rate_on(on: date, _: Annotated[OwnerSession, Depends(require_owner)]) -> list[FxRateResponse]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT base_currency, quote_currency, rate, source,
                      is_carried_forward, carried_from
               FROM fx_rate WHERE rate_date = %s ORDER BY base_currency""",
            (on,),
        )
        return [
            FxRateResponse(
                base=b,
                quote=q,
                rate=str(r),
                source=s,
                carried=c,
                carried_from=cf,
            )
            for b, q, r, s, c, cf in cur.fetchall()
        ]
