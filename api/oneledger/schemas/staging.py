"""M2 Task 4: the staged row contract (api-contracts.html section 4).

The model validators are the contract's teeth. A float that has lost precision, or an
internally inconsistent FX translation, fails here — in the service, where the error can name
the offending row — rather than in Postgres, where it names a constraint.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

# Money is a Decimal all the way from the parser to the database, and a fixed-point string on
# the wire. A float here would already have lost precision before anyone could check.
Money = Annotated[Decimal, Field(max_digits=20, decimal_places=4)]
Rate = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=10)]

# The translation is checked against the rate the row itself carries, to the cent. Rounding a
# rate to ten places and an amount to four cannot reproduce the product exactly, so the check
# is for a wrong rate or a wrong amount, not for the last hundredth.
TRANSLATION_TOLERANCE = Decimal("0.01")


class StagedTransaction(BaseModel):
    """One row of an import, normalized into the ledger's shape and ready to insert.

    `proposed_txn_type` and `status` are plain strings, validated by the database's `txn_type`
    enum and `staged_transaction`'s status CHECK rather than by a second copy of those
    vocabularies here — the same decision `TransactionCreate` records for `txnType`.
    """

    account_id: UUID
    booked_date: date
    value_date: date | None = None
    description_raw: str = Field(min_length=1)
    description_clean: str | None = None
    reference_raw: str | None = None

    amount: Money  # signed, per Invariant I2
    currency: str = Field(min_length=3, max_length=3)
    original_amount: Money | None = None
    original_currency: str | None = Field(default=None, min_length=3, max_length=3)

    # frozen FX, computed before staging — named for the role, never for a currency
    fx_rate_date: date
    fx_rate_is_carried: bool
    fx_rate_to_base: Rate
    fx_rate_to_secondary: Rate
    amount_base: Money
    amount_secondary: Money

    dedupe_hash: str = Field(min_length=64, max_length=64)
    source_page_number: int | None = None
    source_row_number: int
    raw_row: dict[str, Any]

    # what the staging insert needs beyond the contract's shape
    proposed_txn_type: str = Field(min_length=1, max_length=40)
    status: str = Field(default="new", min_length=1, max_length=40)
    duplicate_of_transaction_id: UUID | None = None
    near_duplicate_of_id: UUID | None = None

    @model_validator(mode="after")
    def translations_agree_with_the_stated_rate(self) -> Self:
        if abs(self.amount * self.fx_rate_to_base - self.amount_base) > TRANSLATION_TOLERANCE:
            raise ValueError("amount_base is inconsistent with the stated rate")
        if (
            abs(self.amount * self.fx_rate_to_secondary - self.amount_secondary)
            > TRANSLATION_TOLERANCE
        ):
            raise ValueError("amount_secondary is inconsistent with the stated rate")
        return self

    @model_validator(mode="after")
    def original_amount_and_currency_travel_together(self) -> Self:
        if (self.original_amount is None) != (self.original_currency is None):
            raise ValueError("original_amount and original_currency must be set together")
        return self
