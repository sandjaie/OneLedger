"""M2 Task 4: the staged row contract's validators (api-contracts.html section 4).

The validators are the contract's teeth: an internally inconsistent FX translation fails here,
where the error can name the row, rather than in Postgres where it names a constraint.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from oneledger.schemas.staging import StagedTransaction

ACCOUNT = uuid4()


def staged(**changes: object) -> StagedTransaction:
    values: dict[str, object] = {
        "account_id": ACCOUNT,
        "booked_date": date(2024, 7, 15),
        "description_raw": "POS PURCHASE",
        "amount": Decimal("-310.0000"),
        "currency": "XAA",
        "fx_rate_date": date(2024, 7, 15),
        "fx_rate_is_carried": False,
        "fx_rate_to_base": Decimal("1.0000000000"),
        "fx_rate_to_secondary": Decimal("20.0000000000"),
        "amount_base": Decimal("-310.0000"),
        "amount_secondary": Decimal("-6200.0000"),
        "dedupe_hash": "a" * 64,
        "source_row_number": 1,
        "raw_row": {"amount": "-310.00"},
        "proposed_txn_type": "expense",
    }
    values.update(changes)
    return StagedTransaction.model_validate(values)


def test_a_consistent_row_validates() -> None:
    assert staged().amount_base == Decimal("-310.0000")


@pytest.mark.parametrize("field", ["amount_base", "amount_secondary"])
def test_a_translation_that_disagrees_with_its_own_rate_is_refused(field: str) -> None:
    with pytest.raises(ValidationError, match=f"{field} is inconsistent with the stated rate"):
        staged(**{field: Decimal("-1.0000")})


@pytest.mark.parametrize(
    "changes",
    [
        {"original_amount": Decimal("-75.0000")},
        {"original_currency": "XBB"},
    ],
)
def test_an_original_amount_and_its_currency_must_travel_together(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        staged(**changes)


def test_a_dedupe_hash_is_a_full_sha256() -> None:
    with pytest.raises(ValidationError):
        staged(dedupe_hash="a" * 63)


@settings(max_examples=200)
@given(
    amount=st.decimals(
        min_value=Decimal("-99999999.9999"),
        max_value=Decimal("99999999.9999"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
    rate=st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("1000.0000"),
        places=4,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_the_stated_rate_decides_whether_a_translation_is_accepted(
    amount: Decimal, rate: Decimal
) -> None:
    consistent = (amount * rate).quantize(Decimal("0.0001"))
    row = staged(
        amount=amount,
        fx_rate_to_base=rate,
        fx_rate_to_secondary=rate,
        amount_base=consistent,
        amount_secondary=consistent,
    )
    assert row.amount_base == consistent

    with pytest.raises(ValidationError, match="inconsistent with the stated rate"):
        staged(
            amount=amount,
            fx_rate_to_base=rate,
            fx_rate_to_secondary=rate,
            amount_base=consistent + Decimal("0.0200"),
            amount_secondary=consistent,
        )
