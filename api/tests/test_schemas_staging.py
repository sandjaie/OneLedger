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


@pytest.mark.parametrize(
    "field",
    ["amount", "fx_rate_to_base", "fx_rate_to_secondary", "amount_base", "amount_secondary"],
)
def test_a_float_is_refused_at_the_money_boundary(field: str) -> None:
    # A float has already lost precision by the time it arrives; this is the boundary that is
    # supposed to catch it, and pydantic's default Decimal coercion would let it through.
    with pytest.raises(ValidationError, match="fixed-point"):
        staged(**{field: 1.5})


def test_the_translation_check_matches_the_ledgers_own_precision() -> None:
    # transaction.frozen_amounts_derive permits 0.0001 against the *rounded* product. A row
    # staged inside a looser tolerance would be accepted in review and then fail the whole
    # all-or-nothing commit, which is the worst place to discover it.
    with pytest.raises(ValidationError, match="inconsistent with the stated rate"):
        staged(amount_base=Decimal("-310.0099"))

    assert staged(amount_base=Decimal("-310.0001")).amount_base == Decimal("-310.0001")


def test_the_rounded_product_is_what_the_translation_is_measured_against() -> None:
    # 100.005 * 1.5 is 150.0075; the ledger compares against round(amount * rate, 4), and
    # Postgres rounds half away from zero.
    row = staged(
        amount=Decimal("100.0050"),
        fx_rate_to_base=Decimal("1.5"),
        fx_rate_to_secondary=Decimal("1.5"),
        amount_base=Decimal("150.0075"),
        amount_secondary=Decimal("150.0075"),
    )
    assert row.amount_base == Decimal("150.0075")


@pytest.mark.parametrize(
    ("rate_date", "carried"),
    [
        # V10's carried_rate_dates: fx_rate_date <= booked_date, and carried is true exactly
        # when the rate is older. A rate from the future, an unflagged earlier rate and a
        # flagged same-day one all mislabel the conversion (Invariant I7).
        (date(2024, 7, 16), False),
        (date(2024, 7, 16), True),
        (date(2024, 7, 15), True),
        (date(2024, 7, 14), False),
    ],
)
def test_a_rate_date_that_contradicts_its_carried_flag_is_refused(
    rate_date: date, carried: bool
) -> None:
    with pytest.raises(ValidationError, match="fx_rate_date"):
        staged(fx_rate_date=rate_date, fx_rate_is_carried=carried)


@pytest.mark.parametrize(
    ("rate_date", "carried"),
    [(date(2024, 7, 15), False), (date(2024, 7, 14), True)],
)
def test_a_rate_date_that_agrees_with_its_carried_flag_validates(
    rate_date: date, carried: bool
) -> None:
    assert staged(fx_rate_date=rate_date, fx_rate_is_carried=carried).fx_rate_date == rate_date


@pytest.mark.parametrize("value", [1, True])
def test_a_json_number_is_refused_at_the_money_boundary(value: object) -> None:
    # Money is a fixed-point string on the wire, never a JSON number. An integer loses no
    # precision on the way in, but accepting it makes the wire contract untrue and invites the
    # float that does.
    with pytest.raises(ValidationError, match="fixed-point"):
        staged(amount=value)
