"""Owner-scoped investment catalogue resolution and immutable manual price observations."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(prefix="/investment-catalogue", tags=["investment catalogue"])

_PUBLIC_IDENTITY: Literal["exact_public_identity"] = "exact_public_identity"
_SECURITY_COLUMNS = """s.id, s.household_id, s.security_type::text, s.name, s.currency,
                       s.country, s.asset_class::text, s.sub_asset_class, s.isin, s.ticker,
                       s.exchange"""


class SecurityCandidate(BaseModel):
    id: UUID
    securityType: str
    name: str
    currency: str
    country: str
    assetClass: str
    subAssetClass: str | None
    isin: str | None
    ticker: str | None
    exchange: str | None


class SecurityResolution(BaseModel):
    status: Literal["matched", "needs_confirmation", "ambiguous"]
    reason: Literal[
        "exact_public_identity", "no_exact_identity", "name_only", "multiple_name_matches"
    ]
    candidates: list[SecurityCandidate]


class SecurityResolveRequest(BaseModel):
    """Only public identity values may make resolution automatic; names are a review hint."""

    model_config = ConfigDict(extra="forbid")

    securityId: UUID | None = None
    isin: StrictStr | None = Field(default=None, min_length=12, max_length=12)
    ticker: StrictStr | None = Field(default=None, min_length=1, max_length=32)
    exchange: StrictStr | None = Field(default=None, min_length=1, max_length=32)
    name: StrictStr | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def exactly_one_evidence_form(self) -> SecurityResolveRequest:
        identity_count = sum(value is not None for value in (self.securityId, self.isin, self.name))
        market_identity = self.ticker is not None or self.exchange is not None
        if market_identity and (self.ticker is None or self.exchange is None):
            raise ValueError("ticker and exchange must be supplied together")
        if identity_count + int(market_identity) != 1:
            raise ValueError("supply exactly one security identity or name")
        return self


class ManualPriceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priceDate: date
    price: StrictStr = Field(pattern=r"^\d{1,14}(\.\d{1,6})?$")

    @model_validator(mode="after")
    def price_is_positive(self) -> ManualPriceCreate:
        try:
            if Decimal(self.price) <= 0:
                raise ValueError("price must be positive")
        except InvalidOperation:
            raise ValueError("price must be a decimal string") from None
        return self


class ManualPriceRow(BaseModel):
    id: UUID
    securityId: UUID
    priceDate: date
    price: str
    currency: str
    status: Literal["recorded"] = "recorded"


class SecurityCursor(Protocol):
    def execute(self, statement: str, values: tuple[Any, ...]) -> object: ...

    def fetchall(self) -> list[tuple[Any, ...]]: ...


def _candidate(row: tuple[Any, ...]) -> SecurityCandidate:
    (
        identifier,
        _household_id,
        security_type,
        name,
        currency,
        country,
        asset_class,
        sub_asset_class,
        isin,
        ticker,
        exchange,
    ) = row
    return SecurityCandidate(
        id=identifier,
        securityType=security_type,
        name=name,
        currency=str(currency).strip(),
        country=country,
        assetClass=asset_class,
        subAssetClass=sub_asset_class,
        isin=isin,
        ticker=ticker,
        exchange=exchange,
    )


def resolve_security(
    cur: SecurityCursor,
    household_id: UUID,
    *,
    security_id: UUID | None = None,
    isin: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    name: str | None = None,
) -> SecurityResolution:
    """Resolve exact public evidence; a name is never enough to make a silent decision."""
    values: tuple[Any, ...]
    predicate: str
    is_name_only = name is not None
    if security_id is not None:
        predicate = "s.id = %s"
        values = (household_id, security_id)
    elif isin is not None:
        predicate = "s.isin = %s"
        values = (household_id, isin.strip().upper())
    elif ticker is not None and exchange is not None:
        predicate = "s.ticker = %s AND s.exchange = %s"
        values = (household_id, ticker.strip().upper(), exchange.strip().upper())
    elif name is not None:
        predicate = "s.name = %s"
        values = (household_id, name)
    else:  # Request validation prevents this; fail closed for direct callers too.
        return SecurityResolution(
            status="needs_confirmation", reason="no_exact_identity", candidates=[]
        )

    cur.execute(
        f"""SELECT {_SECURITY_COLUMNS}
              FROM security s
             WHERE (s.household_id IS NULL OR s.household_id = %s)
               AND {predicate}
             ORDER BY s.name, s.id""",
        values,
    )
    candidates = [_candidate(row) for row in cur.fetchall()]
    if not candidates:
        return SecurityResolution(
            status="needs_confirmation", reason="no_exact_identity", candidates=[]
        )
    if is_name_only:
        if len(candidates) == 1:
            return SecurityResolution(
                status="needs_confirmation", reason="name_only", candidates=candidates
            )
        return SecurityResolution(
            status="ambiguous", reason="multiple_name_matches", candidates=candidates
        )
    if len(candidates) == 1:
        return SecurityResolution(status="matched", reason=_PUBLIC_IDENTITY, candidates=candidates)
    return SecurityResolution(
        status="ambiguous", reason="multiple_name_matches", candidates=candidates
    )


@router.post("/securities/resolve", response_model=SecurityResolution)
def resolve_catalogue_security(
    body: SecurityResolveRequest,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> SecurityResolution:
    with connect() as conn, conn.cursor() as cur:
        return resolve_security(
            cur,
            owner.household_id,
            security_id=body.securityId,
            isin=body.isin,
            ticker=body.ticker,
            exchange=body.exchange,
            name=body.name,
        )


def _manual_price_mapping(
    cur: psycopg.Cursor[Any], security_id: UUID, household_id: UUID, price_date: date
) -> UUID:
    cur.execute(
        """SELECT id FROM security_price_source
             WHERE household_id = %s AND security_id = %s
               AND source_key = 'owner_manual' AND is_active
             FOR UPDATE""",
        (household_id, security_id),
    )
    existing = cur.fetchone()
    if existing is not None:
        return UUID(str(existing[0]))
    cur.execute(
        """INSERT INTO security_price_source (
                 household_id, security_id, source_key, source_version, source_security_code,
                 effective_from)
             VALUES (%s, %s, 'owner_manual', 'v1', %s, %s)
             RETURNING id""",
        (household_id, security_id, f"manual:{security_id}", date.min),
    )
    created = cur.fetchone()
    assert created is not None
    return UUID(str(created[0]))


@router.post(
    "/securities/{security_id}/manual-prices",
    response_model=ManualPriceRow,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ManualPriceRow, "description": "Identical retry"}},
)
def append_manual_price(
    security_id: UUID,
    body: ManualPriceCreate,
    response: Response,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> ManualPriceRow:
    """Append one explicit owner observation, accepting only an exact retry for its date."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT household_id, currency FROM security
                 WHERE id = %s AND (household_id = %s OR household_id IS NULL)
                 FOR UPDATE""",
            (security_id, owner.household_id),
        )
        security = cur.fetchone()
        if security is None:
            raise HTTPException(status_code=404, detail="no such security")
        household_id, currency = security
        if household_id is None:
            raise HTTPException(
                status_code=422, detail="manual prices require a household-owned security"
            )
        mapping_id = _manual_price_mapping(cur, security_id, owner.household_id, body.priceDate)
        cur.execute(
            """SELECT id, price, currency FROM price_snapshot
                 WHERE household_id = %s AND security_id = %s AND source_mapping_id = %s
                   AND price_date = %s
                 FOR UPDATE""",
            (owner.household_id, security_id, mapping_id, body.priceDate),
        )
        prior = cur.fetchone()
        if prior is not None:
            prior_id, prior_price, prior_currency = prior
            if Decimal(prior_price) != Decimal(body.price):
                raise HTTPException(
                    status_code=409,
                    detail="a different manual price already exists for this date",
                )
            response.status_code = status.HTTP_200_OK
            return ManualPriceRow(
                id=prior_id,
                securityId=security_id,
                priceDate=body.priceDate,
                price=str(prior_price),
                currency=str(prior_currency).strip(),
            )
        cur.execute(
            """INSERT INTO price_snapshot (
                 household_id, security_id, source_mapping_id, price_date, price, currency,
                 is_accepted, accepted_at)
               VALUES (%s, %s, %s, %s, %s, %s, true, now())
               RETURNING id, price, currency""",
            (
                owner.household_id,
                security_id,
                mapping_id,
                body.priceDate,
                Decimal(body.price),
                currency,
            ),
        )
        created = cur.fetchone()
        assert created is not None
        created_id, created_price, created_currency = created
        return ManualPriceRow(
            id=created_id,
            securityId=security_id,
            priceDate=body.priceDate,
            price=str(created_price),
            currency=str(created_currency).strip(),
        )
