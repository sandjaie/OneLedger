from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(tags=["household"])


class HouseholdCurrency(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    symbol: str = Field(min_length=1)
    decimals: int = Field(ge=0)
    numberFormat: Literal["international", "indian"]


class HouseholdResponse(BaseModel):
    id: UUID
    baseCurrency: str = Field(min_length=3, max_length=3)
    secondaryCurrency: str = Field(min_length=3, max_length=3)
    currencies: list[HouseholdCurrency]


def fetch_household(owner: OwnerSession) -> HouseholdResponse | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT h.id, h.base_currency, h.secondary_currency,
                      b.symbol, b.decimals, b.number_format,
                      s.symbol, s.decimals, s.number_format
                 FROM household h
                 JOIN app_user u ON u.household_id = h.id AND u.id = %s AND u.role = 'owner'
                 JOIN currency b ON b.code = h.base_currency
                 JOIN currency s ON s.code = h.secondary_currency
                WHERE h.id = %s""",
            (owner.user_id, owner.household_id),
        )
        row = cur.fetchone()
    if row is None:
        return None

    (
        hid,
        base,
        secondary,
        base_symbol,
        base_decimals,
        base_format,
        sec_symbol,
        sec_decimals,
        sec_format,
    ) = row
    return HouseholdResponse(
        id=hid,
        baseCurrency=base,
        secondaryCurrency=secondary,
        currencies=[
            HouseholdCurrency(
                code=base,
                symbol=base_symbol,
                decimals=base_decimals,
                numberFormat=base_format,
            ),
            HouseholdCurrency(
                code=secondary,
                symbol=sec_symbol,
                decimals=sec_decimals,
                numberFormat=sec_format,
            ),
        ],
    )


@router.get("/household")
def household(owner: Annotated[OwnerSession, Depends(require_owner)]) -> HouseholdResponse:
    result = fetch_household(owner)
    if result is None:
        raise HTTPException(status_code=403, detail="not authorized")
    return result
