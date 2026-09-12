"""Exact wire contracts for owner-entered acquisition events."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

AcquisitionKind = Literal["purchase", "recurring_purchase"]

_UNITS_PATTERN = r"^\d{1,20}(?:\.\d{1,8})?$"
_PRICE_PATTERN = r"^\d{1,14}(?:\.\d{1,6})?$"
_MONEY_PATTERN = r"^\d{1,16}(?:\.\d{1,4})?$"
_NEGATIVE_MONEY_PATTERN = r"^-\d{1,16}(?:\.\d{1,4})?$"
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _decimal(value: str) -> Decimal:
    # Every field reaches here only after StrictStr and its fixed-point pattern have matched.
    return Decimal(value)


class AcquisitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accountId: UUID
    securityId: UUID
    eventDate: date
    eventKind: AcquisitionKind
    units: StrictStr = Field(pattern=_UNITS_PATTERN)
    price: StrictStr = Field(pattern=_PRICE_PATTERN)
    grossAmount: StrictStr = Field(pattern=_MONEY_PATTERN)
    charges: StrictStr = Field(pattern=_MONEY_PATTERN)
    withheldTax: StrictStr = Field(pattern=_MONEY_PATTERN)
    netAmount: StrictStr = Field(pattern=_NEGATIVE_MONEY_PATTERN)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    idempotencyKey: UUID

    @field_validator("eventDate", mode="before")
    @classmethod
    def canonical_event_date(cls, value: object) -> date:
        try:
            if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
                raise ValueError
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError
        except ValueError:
            raise ValueError("must be a canonical YYYY-MM-DD date string") from None
        return parsed

    @model_validator(mode="after")
    def acquisition_fields_agree(self) -> AcquisitionCreate:
        units = _decimal(self.units)
        price = _decimal(self.price)
        gross = _decimal(self.grossAmount)
        charges = _decimal(self.charges)
        withheld = _decimal(self.withheldTax)
        net = _decimal(self.netAmount)
        if units <= 0 or price <= 0 or gross <= 0:
            raise ValueError("acquisition units, price and gross amount must be positive")
        if charges < 0 or withheld < 0:
            raise ValueError("acquisition charges and withheld tax cannot be negative")
        if net != -(gross + charges + withheld):
            raise ValueError("acquisition net amount does not match gross, charges and tax")
        return self


class AcquisitionCorrectionCreate(AcquisitionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("correctionReason")
    @classmethod
    def normalized_correction_reason(cls, value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class AcquisitionLotRow(BaseModel):
    id: UUID
    acquisitionDate: date
    acquiredUnits: str
    costBasisNative: str
    currency: str


class InvestmentEventRow(BaseModel):
    id: UUID
    holdingId: UUID
    accountId: UUID
    securityId: UUID
    eventDate: date
    eventKind: AcquisitionKind
    units: str
    price: str
    grossAmount: str
    charges: str
    withheldTax: str
    netAmount: str
    currency: str
    fxRateDate: date
    fxRateToBase: str
    fxRateToSecondary: str
    netAmountBase: str
    netAmountSecondary: str
    sourceKind: Literal["owner_entered"]
    correctsEventId: UUID | None
    correctionState: Literal["uncorrected", "corrected"]
    currentUnits: str
    lot: AcquisitionLotRow
