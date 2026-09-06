# api/oneledger/routes/currencies.py
"""The currency catalogue. Options come from this table, never from a union type (ADR-0012)."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(tags=["reference"])


class CurrencyRow(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    name: str
    symbol: str
    decimals: int = Field(ge=0)
    numberFormat: Literal["international", "indian"]


@router.get("/currencies")
def currencies(_owner: Annotated[OwnerSession, Depends(require_owner)]) -> list[CurrencyRow]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT code, name, symbol, decimals, number_format FROM currency ORDER BY code"
        )
        rows = cur.fetchall()
    return [
        CurrencyRow(code=code, name=name, symbol=symbol, decimals=decimals, numberFormat=fmt)
        for code, name, symbol, decimals, fmt in rows
    ]
