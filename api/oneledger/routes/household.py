from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner

router = APIRouter(tags=["household"])


class HouseholdCurrency(BaseModel):
    code: str = Field(min_length=3, max_length=3)
    symbol: str = Field(min_length=1)
    decimals: int = Field(ge=0)
    numberFormat: Literal["international", "indian"]


class HouseholdResponse(BaseModel):
    id: UUID
    name: str
    ownerDisplayName: str
    ownerTaxResidency: str | None
    baseCurrency: str = Field(min_length=3, max_length=3)
    secondaryCurrency: str = Field(min_length=3, max_length=3)
    reportingPairLocked: bool
    currencies: list[HouseholdCurrency]


class HouseholdUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ownerDisplayName: str = Field(min_length=1, max_length=120)
    ownerTaxResidency: str | None = Field(default=None, min_length=2, max_length=2)
    baseCurrency: str = Field(min_length=3, max_length=3)
    secondaryCurrency: str = Field(min_length=3, max_length=3)


LOCKED = (
    "The reporting pair is fixed once amounts are frozen. Changing it is a backfill from stored "
    "FX history (ADR-0002), not a settings change."
)


def fetch_household(owner: OwnerSession) -> HouseholdResponse | None:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT h.id, h.name, p.display_name, p.tax_residency,
                      h.base_currency, h.secondary_currency,
                      reporting_pair_is_locked(),
                      b.symbol, b.decimals, b.number_format,
                      s.symbol, s.decimals, s.number_format
                 FROM household h
                 JOIN app_user u ON u.household_id = h.id AND u.id = %s AND u.role = 'owner'
                 JOIN person p ON p.id = u.person_id
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
        name,
        display_name,
        tax_residency,
        base,
        secondary,
        locked,
        base_symbol,
        base_decimals,
        base_format,
        sec_symbol,
        sec_decimals,
        sec_format,
    ) = row
    return HouseholdResponse(
        id=hid,
        name=name,
        ownerDisplayName=display_name,
        ownerTaxResidency=tax_residency,
        baseCurrency=base,
        secondaryCurrency=secondary,
        reportingPairLocked=locked,
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


@router.patch("/household", status_code=204)
def update_household(
    body: HouseholdUpdate, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> None:
    try:
        with connect() as conn:
            scope_to_owner(conn, owner.user_id)
            conn.execute(
                "SELECT update_household_configuration(%s, %s, %s, %s, %s)",
                (
                    body.name,
                    body.ownerDisplayName,
                    body.ownerTaxResidency,
                    body.baseCurrency,
                    body.secondaryCurrency,
                ),
            )
    except psycopg.errors.ObjectInUse as exc:
        raise HTTPException(status_code=409, detail=LOCKED) from exc
    except (psycopg.errors.InvalidParameterValue, psycopg.errors.ForeignKeyViolation) as exc:
        raise HTTPException(
            status_code=422,
            detail="The reporting pair must name two different seeded currencies.",
        ) from exc
    except psycopg.errors.InsufficientPrivilege as exc:
        raise HTTPException(status_code=403, detail="not authorized") from exc
