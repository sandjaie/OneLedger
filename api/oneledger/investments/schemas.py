"""Exact wire contracts for owner-entered acquisition events."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

AcquisitionKind = Literal["purchase", "recurring_purchase"]
CashLifecycleKind = Literal[
    "sale",
    "redemption",
    "maturity",
    "cash_distribution",
    "interest",
    "reinvested_distribution",
    "charge",
    "withheld_tax",
]

_UNITS_PATTERN = r"^\d{1,20}(?:\.\d{1,8})?$"
_PRICE_PATTERN = r"^\d{1,14}(?:\.\d{1,6})?$"
_MONEY_PATTERN = r"^\d{1,16}(?:\.\d{1,4})?$"
_NEGATIVE_MONEY_PATTERN = r"^-\d{1,16}(?:\.\d{1,4})?$"
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SIGNED_UNITS_PATTERN = r"^-?\d{1,20}(?:\.\d{1,8})?$"
_SIGNED_MONEY_PATTERN = r"^-?\d{1,16}(?:\.\d{1,4})?$"
SignedUnits = Annotated[StrictStr, Field(pattern=_SIGNED_UNITS_PATTERN)]
Price = Annotated[StrictStr, Field(pattern=_PRICE_PATTERN)]
Money = Annotated[StrictStr, Field(pattern=_MONEY_PATTERN)]
SignedMoney = Annotated[StrictStr, Field(pattern=_SIGNED_MONEY_PATTERN)]


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


class CashLifecycleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accountId: UUID
    securityId: UUID
    eventDate: date
    eventKind: CashLifecycleKind
    units: SignedUnits | None
    price: Price | None
    grossAmount: Money | None
    charges: Money
    withheldTax: Money
    netAmount: SignedMoney
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
    def lifecycle_fields_agree(self) -> CashLifecycleCreate:
        units = None if self.units is None else _decimal(self.units)
        price = None if self.price is None else _decimal(self.price)
        gross = None if self.grossAmount is None else _decimal(self.grossAmount)
        charges = _decimal(self.charges)
        withheld = _decimal(self.withheldTax)
        net = _decimal(self.netAmount)
        zero_is_canonical = not (net == 0 and self.netAmount.startswith("-"))
        if self.eventKind in {"sale", "redemption", "maturity"}:
            valid = (
                units is not None
                and units < 0
                and price is not None
                and price > 0
                and gross is not None
                and gross > 0
                and net == gross - charges - withheld
                and net > 0
            )
        elif self.eventKind in {"cash_distribution", "interest"}:
            valid = (
                units is None
                and price is None
                and gross is not None
                and gross > 0
                and net == gross - charges - withheld
                and net > 0
            )
        elif self.eventKind == "reinvested_distribution":
            valid = (
                units is not None
                and units > 0
                and price is not None
                and price > 0
                and gross is not None
                and gross > 0
                and charges == 0
                and net == 0
            )
        elif self.eventKind == "charge":
            valid = (
                units is None
                and price is None
                and gross is None
                and charges > 0
                and withheld == 0
                and net == -charges
            )
        else:
            valid = (
                units is None
                and price is None
                and gross is None
                and charges == 0
                and withheld > 0
                and net == -withheld
            )
        if charges < 0 or withheld < 0 or not zero_is_canonical or not valid:
            raise ValueError("cash lifecycle event fields do not match their kind")
        return self


class CashLifecycleCorrectionCreate(CashLifecycleCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    @field_validator("correctionReason")
    @classmethod
    def normalized_correction_reason(cls, value: str) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class CashLifecycleEventRow(BaseModel):
    id: UUID
    holdingId: UUID
    accountId: UUID
    securityId: UUID
    eventDate: date
    eventKind: CashLifecycleKind
    units: str | None
    price: str | None
    grossAmount: str | None
    charges: str
    withheldTax: str
    netAmount: str
    currency: str
    fxRateDate: date | None
    fxRateToBase: str | None
    fxRateToSecondary: str | None
    netAmountBase: str | None
    netAmountSecondary: str | None
    sourceKind: Literal["owner_entered"]
    correctsEventId: UUID | None
    correctionState: Literal["uncorrected", "corrected"]
    currentUnits: str
    lot: AcquisitionLotRow | None
    reviewReasons: list[Literal["incomplete_lot_allocation"]]


# Holding-level unit actions (Task 15.2): split, bonus, rights and the paired switch, systematic
# transfer and platform transfer legs. None of these carry a funding cash flow except rights.
UnitActionKind = Literal[
    "bonus", "rights", "split", "switch", "systematic_transfer", "platform_transfer"
]
UnitActionLegKind = Literal[
    "switch_in",
    "switch_out",
    "systematic_transfer_in",
    "systematic_transfer_out",
    "platform_transfer_in",
    "platform_transfer_out",
]
_RATIO_PATTERN = r"^\d{1,10}(?:\.\d{1,10})?$"
Ratio = Annotated[StrictStr, Field(pattern=_RATIO_PATTERN)]


def _canonical_date(value: object) -> date:
    try:
        if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
            raise ValueError
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
    except ValueError:
        raise ValueError("must be a canonical YYYY-MM-DD date string") from None
    return parsed


def _normalize_reason(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if not normalized:
        raise ValueError("must not be blank")
    return normalized


class BonusActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["bonus"]
    accountId: UUID
    securityId: UUID
    eventDate: date
    units: StrictStr = Field(pattern=_UNITS_PATTERN)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    idempotencyKey: UUID

    _validate_event_date = field_validator("eventDate", mode="before")(_canonical_date)

    @model_validator(mode="after")
    def bonus_units_are_positive(self) -> BonusActionCreate:
        if _decimal(self.units) <= 0:
            raise ValueError("bonus units must be positive")
        return self


class BonusActionCorrectionCreate(BonusActionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    _normalize_correction_reason = field_validator("correctionReason")(_normalize_reason)


class RightsActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["rights"]
    accountId: UUID
    securityId: UUID
    eventDate: date
    units: StrictStr = Field(pattern=_UNITS_PATTERN)
    price: StrictStr = Field(pattern=_PRICE_PATTERN)
    grossAmount: StrictStr = Field(pattern=_MONEY_PATTERN)
    charges: StrictStr = Field(pattern=_MONEY_PATTERN)
    withheldTax: StrictStr = Field(pattern=_MONEY_PATTERN)
    netAmount: StrictStr = Field(pattern=_NEGATIVE_MONEY_PATTERN)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    idempotencyKey: UUID

    _validate_event_date = field_validator("eventDate", mode="before")(_canonical_date)

    @model_validator(mode="after")
    def rights_fields_agree(self) -> RightsActionCreate:
        units = _decimal(self.units)
        price = _decimal(self.price)
        gross = _decimal(self.grossAmount)
        charges = _decimal(self.charges)
        withheld = _decimal(self.withheldTax)
        net = _decimal(self.netAmount)
        if units <= 0 or price <= 0 or gross <= 0:
            raise ValueError("rights units, price and gross amount must be positive")
        if charges < 0 or withheld < 0:
            raise ValueError("rights charges and withheld tax cannot be negative")
        if net != -(gross + charges + withheld):
            raise ValueError("rights net amount does not match gross, charges and tax")
        return self


class RightsActionCorrectionCreate(RightsActionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    _normalize_correction_reason = field_validator("correctionReason")(_normalize_reason)


class SplitActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["split"]
    accountId: UUID
    securityId: UUID
    eventDate: date
    unitRatio: Ratio
    idempotencyKey: UUID

    _validate_event_date = field_validator("eventDate", mode="before")(_canonical_date)

    @model_validator(mode="after")
    def ratio_is_valid(self) -> SplitActionCreate:
        ratio = _decimal(self.unitRatio)
        if ratio <= 0 or ratio == 1:
            raise ValueError("split ratio must be positive and not equal to one")
        return self


class SplitActionCorrectionCreate(SplitActionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    _normalize_correction_reason = field_validator("correctionReason")(_normalize_reason)


class SwitchActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["switch"]
    accountId: UUID
    outSecurityId: UUID
    inSecurityId: UUID
    eventDate: date
    outUnits: SignedUnits
    outPrice: Price
    outGrossAmount: Money
    inUnits: StrictStr = Field(pattern=_UNITS_PATTERN)
    inPrice: Price
    inGrossAmount: Money
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    idempotencyKey: UUID

    _validate_event_date = field_validator("eventDate", mode="before")(_canonical_date)

    @model_validator(mode="after")
    def switch_fields_agree(self) -> SwitchActionCreate:
        if self.outSecurityId == self.inSecurityId:
            raise ValueError("a switch must move between two different securities")
        if _decimal(self.outUnits) >= 0 or _decimal(self.inUnits) <= 0:
            raise ValueError("a switch out leg is negative and its in leg is positive")
        if _decimal(self.outGrossAmount) != _decimal(self.inGrossAmount):
            raise ValueError("a switch pair must carry equal gross value")
        return self


class SwitchActionCorrectionCreate(SwitchActionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    _normalize_correction_reason = field_validator("correctionReason")(_normalize_reason)


class SystematicTransferActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["systematic_transfer"]
    accountId: UUID
    outSecurityId: UUID
    inSecurityId: UUID
    eventDate: date
    outUnits: SignedUnits
    outPrice: Price
    outGrossAmount: Money
    inUnits: StrictStr = Field(pattern=_UNITS_PATTERN)
    inPrice: Price
    inGrossAmount: Money
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    idempotencyKey: UUID

    _validate_event_date = field_validator("eventDate", mode="before")(_canonical_date)

    @model_validator(mode="after")
    def systematic_transfer_fields_agree(self) -> SystematicTransferActionCreate:
        if self.outSecurityId == self.inSecurityId:
            raise ValueError("a systematic transfer must move between two different securities")
        if _decimal(self.outUnits) >= 0 or _decimal(self.inUnits) <= 0:
            raise ValueError("a systematic transfer out leg is negative and its in leg is positive")
        if _decimal(self.outGrossAmount) != _decimal(self.inGrossAmount):
            raise ValueError("a systematic transfer pair must carry equal gross value")
        return self


class SystematicTransferActionCorrectionCreate(SystematicTransferActionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    _normalize_correction_reason = field_validator("correctionReason")(_normalize_reason)


class PlatformTransferActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["platform_transfer"]
    outAccountId: UUID
    inAccountId: UUID
    securityId: UUID
    eventDate: date
    outUnits: SignedUnits
    inUnits: StrictStr = Field(pattern=_UNITS_PATTERN)
    currency: StrictStr = Field(pattern=r"^[A-Z]{3}$")
    idempotencyKey: UUID

    _validate_event_date = field_validator("eventDate", mode="before")(_canonical_date)

    @model_validator(mode="after")
    def platform_transfer_fields_agree(self) -> PlatformTransferActionCreate:
        if self.outAccountId == self.inAccountId:
            raise ValueError("a platform transfer must move between two different accounts")
        if _decimal(self.inUnits) <= 0 or _decimal(self.outUnits) != -_decimal(self.inUnits):
            raise ValueError("a platform transfer pair must carry exactly opposite units")
        return self


class PlatformTransferActionCorrectionCreate(PlatformTransferActionCreate):
    expectedCorrectionState: Literal["uncorrected"]
    correctionReason: StrictStr = Field(min_length=1, max_length=500)

    _normalize_correction_reason = field_validator("correctionReason")(_normalize_reason)


UnitActionCreate = Annotated[
    (
        BonusActionCreate
        | RightsActionCreate
        | SplitActionCreate
        | SwitchActionCreate
        | SystematicTransferActionCreate
        | PlatformTransferActionCreate
    ),
    Field(discriminator="kind"),
]
UnitActionCorrectionCreate = Annotated[
    (
        BonusActionCorrectionCreate
        | RightsActionCorrectionCreate
        | SplitActionCorrectionCreate
        | SwitchActionCorrectionCreate
        | SystematicTransferActionCorrectionCreate
        | PlatformTransferActionCorrectionCreate
    ),
    Field(discriminator="kind"),
]


class UnitActionLegRow(BaseModel):
    id: UUID
    holdingId: UUID
    accountId: UUID
    securityId: UUID
    eventDate: date
    eventKind: UnitActionLegKind
    units: str
    price: str | None
    grossAmount: str | None
    currency: str
    correctsEventId: UUID | None
    correctionState: Literal["uncorrected", "corrected"]
    currentUnits: str


class UnitActionEventRow(BaseModel):
    id: UUID
    holdingId: UUID
    accountId: UUID
    securityId: UUID
    eventDate: date
    eventKind: Literal["bonus", "rights", "split"]
    units: str
    unitRatio: str | None
    price: str | None
    grossAmount: str | None
    netAmount: str
    currency: str
    correctsEventId: UUID | None
    correctionState: Literal["uncorrected", "corrected"]
    currentUnits: str
    lot: AcquisitionLotRow | None


class UnitActionResult(BaseModel):
    kind: UnitActionKind
    event: UnitActionEventRow | None = None
    outEvent: UnitActionLegRow | None = None
    inEvent: UnitActionLegRow | None = None
