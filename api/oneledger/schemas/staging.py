"""M2 Task 4: the staged row contract (api-contracts.html section 4).

The model validators are the contract's teeth. A float that has lost precision, or an
internally inconsistent FX translation, fails here — in the service, where the error can name
the offending row — rather than in Postgres, where it names a constraint.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field, model_validator

from ..money import translate


def _not_a_json_number(value: object) -> object:
    """Refuse a JSON number before pydantic coerces one into a Decimal.

    Pydantic's Decimal validation is lax by default and would accept a float here — after the
    binary-precision loss has already happened, where nothing downstream can detect it. This
    boundary is the whole reason the repository's money rule can be enforced at all.

    An integer loses no precision on the way in, but money is a fixed-point string on the wire
    and accepting `1` makes that contract untrue at the only place it is checked — which is how
    the float that does lose precision arrives next. A `Decimal` built in the service passes.
    """
    if isinstance(value, float | int) and not isinstance(value, Decimal):
        raise ValueError("money is a Decimal or a fixed-point string, never a JSON number")
    return value


# Money is a Decimal all the way from the parser to the database, and a fixed-point string on
# the wire. A float here would already have lost precision before anyone could check.
Money = Annotated[
    Decimal, BeforeValidator(_not_a_json_number), Field(max_digits=20, decimal_places=4)
]
Rate = Annotated[
    Decimal, BeforeValidator(_not_a_json_number), Field(gt=0, max_digits=20, decimal_places=10)
]

# The ledger's own precision, not a looser one: transaction.frozen_amounts_derive permits
# 0.0001 against round(amount * rate, 4). api-contracts.html section 4's sketch says 0.01,
# which would stage a row an owner could accept and the all-or-nothing commit would then have
# to reject — the worst place to find out. The doc is corrected to match the constraint.
TRANSLATION_TOLERANCE = Decimal("0.0001")
CENTS = Decimal("0.0001")


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

    # The cascade's proposal, carried from staging through to the ledger. V13 created these
    # columns and V14 already carries the three provenance ones into `transaction`, so a row
    # can answer "why is this Food & Dining?" from the moment it is staged (classification.html
    # §1). Nothing here commits: the review click does.
    proposed_category_id: UUID | None = None
    proposed_merchant_id: UUID | None = None
    proposed_payment_method: str | None = Field(default=None, max_length=40)
    proposed_country: str | None = Field(default=None, min_length=2, max_length=2)
    classified_by: str = Field(default="unclassified", min_length=1, max_length=40)
    classification_rule_id: UUID | None = None
    classification_confidence: Decimal = Decimal("0.000")

    # what the staging insert needs beyond the contract's shape
    proposed_txn_type: str = Field(min_length=1, max_length=40)
    status: str = Field(default="new", min_length=1, max_length=40)
    duplicate_of_transaction_id: UUID | None = None
    near_duplicate_of_id: UUID | None = None

    @model_validator(mode="after")
    def translations_agree_with_the_stated_rate(self) -> Self:
        # Postgres rounds half away from zero, so the same rounding is used here: a row that
        # passes this check is a row frozen_amounts_derive will also accept.
        for name, rate, translated in (
            ("amount_base", self.fx_rate_to_base, self.amount_base),
            ("amount_secondary", self.fx_rate_to_secondary, self.amount_secondary),
        ):
            try:
                expected = translate(self.amount, rate)
            except InvalidOperation as exc:
                # Each operand fits its column and the product does not fit the Decimal
                # context. That is still a translation this row cannot support, and it must
                # name the row like every other refusal rather than escape as a raw exception.
                raise ValueError(f"{name} is inconsistent with the stated rate") from exc
            if abs(translated - expected) > TRANSLATION_TOLERANCE:
                raise ValueError(f"{name} is inconsistent with the stated rate")
        return self

    @model_validator(mode="after")
    def the_rate_date_agrees_with_the_carried_flag(self) -> Self:
        """V10's `carried_rate_dates`, enforced where the offending row can still be named.

        A rate never comes from the future, and carried means exactly "from an earlier date":
        an unflagged earlier rate and a flagged same-day one both mislabel the conversion
        (Invariant I7). Staging a row that breaks this lets review accept it and leaves the
        all-or-nothing commit to discover it.
        """
        if self.fx_rate_date > self.booked_date:
            raise ValueError("fx_rate_date cannot be later than booked_date")
        if self.fx_rate_is_carried != (self.fx_rate_date < self.booked_date):
            raise ValueError(
                "fx_rate_is_carried must be true exactly when fx_rate_date is earlier than "
                "booked_date"
            )
        return self

    @model_validator(mode="after")
    def original_amount_and_currency_travel_together(self) -> Self:
        if (self.original_amount is None) != (self.original_currency is None):
            raise ValueError("original_amount and original_currency must be set together")
        return self
