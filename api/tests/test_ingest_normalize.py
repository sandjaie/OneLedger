"""M2 Task 4: normalization — dates, signs, type, description and currency.

Pure values, no database and no network. `statement-ingestion.html` section 5 is the contract;
the plan's Task 4 states the traps, and every one of them is asserted here rather than assumed.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from itertools import product

import pytest

from oneledger.ingest import normalize, reconcile
from oneledger.ingest.engine import ExtractionResult, RawRow
from oneledger.routes.layouts import LayoutCreate

ACCOUNT_CURRENCY = "XAA"


def profile(**changes: object) -> LayoutCreate:
    values: dict[str, object] = {
        "name": "layout A",
        "fingerprints": [r"ACCOUNT SUMMARY"],
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "amount", "xMin": 400.0, "xMax": 600.0},
        ],
        "dateFormat": "dd/MM/yyyy",
        "signConvention": "signed",
        "chronologicalOrder": "ascending",
        "rowStartRule": "has_date_and_amount",
        "closingBalancePattern": r"Closing balance:\s*([0-9,.-]+)",
    }
    values.update(changes)
    return LayoutCreate.model_validate(values)


def extraction(
    rows: list[RawRow],
    *,
    declared_opening: str | None = None,
    declared_closing: str | None = None,
    metadata: dict[str, str] | None = None,
) -> ExtractionResult:
    return ExtractionResult(
        rows=rows,
        declared_opening=declared_opening,
        declared_closing=declared_closing,
        metadata=metadata or {},
        account_number_raw=None,
        page_count=1,
        has_text=True,
        words_filtered=0,
        lines_unparsed=0,
    )


def row(number: int, **fields: str) -> RawRow:
    return RawRow(
        page_number=1,
        row_number=number,
        raw_text=" ".join(fields.values()),
        fields=dict(fields),
    )


def test_the_declared_date_format_is_used_and_never_inferred() -> None:
    rows = [row(1, booked_date="03/09/2026", description="ALPHA", amount="-10.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.booked_date for line in batch.rows] == [date(2026, 9, 3)]

    # The same string under a different declared format is not reinterpreted to fit. (The
    # plan's example pairs dd/MM/yyyy with MM/dd/yyyy; Task 3's save-time validation has
    # since restricted dateFormat to the engine's supported set, so month-first cannot be
    # declared at all — a stronger form of the same rule.)
    with pytest.raises(normalize.NormalizationError, match="dd-MM-yyyy"):
        normalize.normalize(
            profile(dateFormat="dd-MM-yyyy"), extraction(rows), account_currency=ACCOUNT_CURRENCY
        )

    # With a captured period the statement still has one, so the same row fails on its own and
    # names the format rather than being re-read under another.
    layout = profile(
        dateFormat="dd-MM-yyyy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
    )
    windowed = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/09/2026", "period_end": "30/09/2026"}),
        account_currency=ACCOUNT_CURRENCY,
    )
    assert windowed.rows == []
    assert [failure.reason for failure in windowed.failures] == [
        "booked_date is not a dd-MM-yyyy date"
    ]


def test_a_date_outside_the_captured_period_fails_the_row_and_names_the_period() -> None:
    rows = [
        row(1, booked_date="15/07/2024", description="INSIDE", amount="-10.00"),
        row(2, booked_date="15/09/2024", description="OUTSIDE", amount="-20.00"),
    ]
    layout = profile(
        metadataPatterns={
            "period_start": r"From (\d{2}/\d{2}/\d{4})",
            "period_end": r"To (\d{2}/\d{2}/\d{4})",
        }
    )

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/07/2024", "period_end": "31/07/2024"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.period.start == date(2024, 7, 1)
    assert batch.period.end == date(2024, 7, 31)
    assert batch.period.is_derived is False
    assert [line.description_raw for line in batch.rows] == ["INSIDE"]
    assert batch.failures[0].source_row_number == 2
    assert batch.failures[0].reason == "booked_date is outside the statement period"


def test_a_period_derived_from_row_dates_is_labelled_derived_and_excludes_nothing() -> None:
    rows = [
        row(1, booked_date="15/07/2024", description="ALPHA", amount="-10.00"),
        row(2, booked_date="15/09/2024", description="BETA", amount="-20.00"),
    ]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert batch.period == normalize.StatementPeriod(
        start=date(2024, 7, 15), end=date(2024, 9, 15), is_derived=True
    )
    assert [line.source_row_number for line in batch.rows] == [1, 2]
    assert batch.failures == []


def test_a_two_digit_year_windows_against_the_statement_period() -> None:
    # Python's own %y pivot would read '99' as 1999; the period says otherwise, and the
    # period is what the statement states.
    rows = [
        row(1, booked_date="15/07/99", description="ALPHA", amount="-10.00"),
        row(2, booked_date="02/08/99", description="BETA", amount="-20.00"),
    ]
    layout = profile(
        dateFormat="dd/MM/yy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
    )

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/07/2099", "period_end": "31/08/2099"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert [line.booked_date for line in batch.rows] == [date(2099, 7, 15), date(2099, 8, 2)]


@pytest.mark.parametrize(
    ("convention", "fields", "expected"),
    [
        ("debit_credit_columns", {"debit": "310.00"}, Decimal("-310.0000")),
        ("debit_credit_columns", {"credit": "310.00"}, Decimal("310.0000")),
        ("signed", {"amount": "-310.00"}, Decimal("-310.0000")),
        ("signed", {"amount": "310.00"}, Decimal("310.0000")),
        ("cr_suffix", {"amount": "310.00 Dr"}, Decimal("-310.0000")),
        ("cr_suffix", {"amount": "310.00 Cr"}, Decimal("310.0000")),
        # A bare amount under the Dr/Cr convention is a debit: the suffix marks the credit.
        ("cr_suffix", {"amount": "310.00"}, Decimal("-310.0000")),
    ],
)
def test_each_sign_convention_produces_the_documented_sign(
    convention: str, fields: dict[str, str], expected: Decimal
) -> None:
    columns = [
        {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
        {"field": "description", "xMin": 100.0, "xMax": 300.0},
        {"field": "debit", "xMin": 300.0, "xMax": 450.0},
        {"field": "credit", "xMin": 450.0, "xMax": 600.0},
    ]
    changes: dict[str, object] = {"signConvention": convention}
    if convention == "debit_credit_columns":
        changes["columns"] = columns
    layout = profile(**changes)

    batch = normalize.normalize(
        layout,
        extraction([row(1, booked_date="15/07/2024", description="ALPHA", **fields)]),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert [line.amount for line in batch.rows] == [expected]


def test_balance_delta_derives_each_amount_from_consecutive_balances() -> None:
    rows = [
        row(1, booked_date="15/07/2024", description="ALPHA", running_balance="4,690.00"),
        row(2, booked_date="16/07/2024", description="BETA", running_balance="4,890.00"),
    ]
    layout = profile(
        signConvention="balance_delta",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout,
        extraction(rows, declared_opening="5,000.00"),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert [line.amount for line in batch.rows] == [Decimal("-310.0000"), Decimal("200.0000")]


def test_balance_delta_without_an_opening_balance_invents_nothing() -> None:
    rows = [row(1, booked_date="15/07/2024", description="ALPHA", running_balance="4,690.00")]
    layout = profile(
        signConvention="balance_delta",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    with pytest.raises(normalize.NormalizationError, match="opening balance"):
        normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)


def test_inverts_sign_is_applied_last_and_flips_a_card_purchase_and_its_payment() -> None:
    # A card statement presents a purchase as a positive debit because it increases what you
    # owe. In the ledger a purchase is negative (Invariant I2). Getting this wrong leaves a
    # batch that is internally consistent and entirely wrong, which is why it is asserted.
    # The card presents a purchase as a positive 310.00 because it increases what is owed, and
    # a payment as -500.00 because it reduces it. The ledger stores the opposite of both.
    rows = [
        row(1, booked_date="15/07/2024", description="PURCHASE", amount="310.00"),
        row(2, booked_date="16/07/2024", description="PAYMENT", amount="-500.00"),
    ]

    plain = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)
    assert [line.amount for line in plain.rows] == [Decimal("310.0000"), Decimal("-500.0000")]

    inverted = normalize.normalize(
        profile(invertsSign=True), extraction(rows), account_currency=ACCOUNT_CURRENCY
    )
    assert [line.amount for line in inverted.rows] == [Decimal("-310.0000"), Decimal("500.0000")]
    assert [line.proposed_txn_type for line in inverted.rows] == ["expense", "income"]


def test_the_proposed_type_follows_the_ledger_sign() -> None:
    rows = [
        row(1, booked_date="15/07/2024", description="OUT", amount="-10.00"),
        row(2, booked_date="16/07/2024", description="IN", amount="10.00"),
    ]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.proposed_txn_type for line in batch.rows] == ["expense", "income"]


def test_description_raw_is_untouched_and_the_cleanup_lists_what_it_removed() -> None:
    raw = "POS 4821 STARBUCKS DUBAI MALL      AE  030926 REF 887262819"
    rows = [row(1, booked_date="15/07/2024", description=raw, amount="-10.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    line = batch.rows[0]
    assert line.description_raw == raw
    assert line.description_clean == "STARBUCKS DUBAI MALL"
    assert line.description_rules_applied == (
        "pos_atm_prefix",
        "reference_number",
        "trailing_date",
        "trailing_country_or_currency_code",
    )


def test_repeated_whitespace_inside_a_name_is_collapsed_and_reported() -> None:
    rows = [row(1, booked_date="15/07/2024", description="BLUE   BOTTLE   COFFEE", amount="-1.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert batch.rows[0].description_clean == "BLUE BOTTLE COFFEE"
    assert batch.rows[0].description_rules_applied == ("repeated_whitespace",)


def test_a_description_the_rules_would_empty_keeps_the_raw_text() -> None:
    # Cleaning is for matching and display. A rule set that removes everything has found no
    # merchant, and an empty label helps nobody.
    rows = [row(1, booked_date="15/07/2024", description="POS 4821 REF 887262819", amount="-1.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert batch.rows[0].description_clean == "POS 4821 REF 887262819"
    assert batch.rows[0].description_rules_applied == ()


def test_the_account_currency_is_the_rows_currency_unless_a_foreign_amount_is_mapped() -> None:
    rows = [row(1, booked_date="15/07/2024", description="ALPHA", amount="-10.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert batch.rows[0].currency == ACCOUNT_CURRENCY
    assert batch.rows[0].original_amount is None
    assert batch.rows[0].original_currency is None


def test_a_mapped_foreign_amount_travels_beside_the_accounts_own_amount() -> None:
    # The bank's own conversion is authoritative: the ledger records what it charged in the
    # account's currency and carries the original beside it, never a market rate of its own.
    rows = [
        row(
            1,
            booked_date="15/07/2024",
            description="ALPHA",
            amount="-10.00",
            original_amount="-2.50",
            original_currency="XBB",
        )
    ]
    layout = profile(
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "amount", "xMin": 300.0, "xMax": 450.0},
            {"field": "original_amount", "xMin": 450.0, "xMax": 520.0},
            {"field": "original_currency", "xMin": 520.0, "xMax": 600.0},
        ]
    )

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    line = batch.rows[0]
    assert line.currency == ACCOUNT_CURRENCY
    assert line.amount == Decimal("-10.0000")
    assert line.original_amount == Decimal("-2.5000")
    assert line.original_currency == "XBB"


def test_the_raw_row_and_its_coordinates_survive_normalization() -> None:
    rows = [row(7, booked_date="15/07/2024", description="ALPHA", amount="-10.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    line = batch.rows[0]
    assert line.source_page_number == 1
    assert line.source_row_number == 7
    assert line.raw_row == {
        "booked_date": "15/07/2024",
        "description": "ALPHA",
        "amount": "-10.00",
        "raw_text": "15/07/2024 ALPHA -10.00",
    }


def test_an_unparseable_amount_fails_its_row_rather_than_staging_a_zero() -> None:
    rows = [
        row(1, booked_date="15/07/2024", description="ALPHA", amount="1O.OO"),
        row(2, booked_date="16/07/2024", description="BETA", amount="-20.00"),
    ]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.description_raw for line in batch.rows] == ["BETA"]
    assert batch.failures[0].reason == "amount is not a number"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # An over-cleaned name silently renames the merchant every later match is made
        # against, so each rule refuses the cases it cannot tell apart from a name.
        ("THE PUB", "THE PUB"),
        ("STARBUCKS DUBAI MALL AE", "STARBUCKS DUBAI MALL AE"),
        ("PAYMENT TO JOHN", "PAYMENT TO JOHN"),
        ("REF LOUNGE", "REF LOUNGE"),
        ("STORE 1234567", "STORE 1234567"),
    ],
)
def test_the_cleanup_rules_leave_a_name_they_cannot_distinguish_from_metadata(
    raw: str, expected: str
) -> None:
    assert normalize.clean_description(raw)[0] == expected


def test_a_trailing_code_is_removed_only_beside_the_metadata_that_identifies_it() -> None:
    # The same two letters: tail metadata in the first, the last word of a name in the second.
    assert normalize.clean_description("SOUQ WAQIF QA 030926")[0] == "SOUQ WAQIF"
    assert normalize.clean_description("SOUQ WAQIF QA")[0] == "SOUQ WAQIF QA"


@pytest.mark.parametrize("convention", ["signed", "debit_credit_columns"])
def test_a_dr_cr_suffix_outside_its_own_convention_fails_the_row(convention: str) -> None:
    fields = {"amount": "310.00 Cr"}
    changes: dict[str, object] = {"signConvention": convention}
    if convention == "debit_credit_columns":
        fields = {"debit": "310.00 Cr"}
        changes["columns"] = [
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "debit", "xMin": 300.0, "xMax": 450.0},
            {"field": "credit", "xMin": 450.0, "xMax": 600.0},
        ]

    batch = normalize.normalize(
        profile(**changes),
        extraction([row(1, booked_date="15/07/2024", description="ALPHA", **fields)]),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.rows == []
    assert "Dr/Cr suffix" in batch.failures[0].reason


def test_a_descending_statement_derives_its_balance_deltas_chronologically() -> None:
    # balanceₙ = balanceₙ₋₁ + amountₙ reads the previous *chronological* row. Walking a
    # descending statement in display order turns every amount into its neighbour's.
    rows = [
        row(1, booked_date="16/07/2024", description="LATER", running_balance="4,890.00"),
        row(2, booked_date="15/07/2024", description="EARLIER", running_balance="4,690.00"),
    ]
    layout = profile(
        signConvention="balance_delta",
        chronologicalOrder="descending",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout, extraction(rows, declared_opening="5,000.00"), account_currency=ACCOUNT_CURRENCY
    )

    # Document order is preserved; the amounts belong to their own rows.
    assert [(line.source_row_number, line.amount) for line in batch.rows] == [
        (1, Decimal("200.0000")),
        (2, Decimal("-310.0000")),
    ]


@pytest.mark.parametrize("amount", ["1" * 25, "99999999999999999.0000"])
def test_an_amount_beyond_the_ledgers_precision_fails_its_row(amount: str) -> None:
    # NUMERIC(20,4) holds sixteen integer digits. A longer OCR value must fail its own row,
    # not abort the statement with a decimal error from inside the parser.
    rows = [
        row(1, booked_date="15/07/2024", description="HUGE", amount=amount),
        row(2, booked_date="16/07/2024", description="BETA", amount="-20.00"),
    ]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.description_raw for line in batch.rows] == ["BETA"]
    assert "is not a number" in batch.failures[0].reason
    assert normalize.parse_amount(amount) is None


def test_a_populated_value_date_that_does_not_parse_fails_its_row() -> None:
    # Returning None would silently discard what the statement actually said, and the row
    # would look exactly like one whose value-date cell was empty.
    rows = [
        row(1, booked_date="15/07/2024", value_date="not-a-date", description="A", amount="-1.00"),
        row(2, booked_date="16/07/2024", description="B", amount="-2.00"),
    ]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.description_raw for line in batch.rows] == ["B"]
    assert batch.failures[0].reason == "value_date is not a dd/MM/yyyy date"


def test_a_value_date_after_the_period_is_normal_and_stages() -> None:
    # The period rule belongs to booked_date. A value date days after the statement closes is
    # ordinary — settlement follows booking — and failing it would reject sound rows.
    layout = profile(
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "value_date", "xMin": 100.0, "xMax": 200.0},
            {"field": "description", "xMin": 200.0, "xMax": 400.0},
            {"field": "amount", "xMin": 400.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(
            1,
            booked_date="31/07/2024",
            value_date="02/08/2024",
            description="A",
            amount="-1.00",
        )
    ]

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/07/2024", "period_end": "31/07/2024"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.failures == []
    assert batch.rows[0].value_date == date(2024, 8, 2)


def test_a_zero_amount_takes_the_only_type_the_ledger_lets_it_commit_under() -> None:
    # transaction.amount_not_zero permits a zero amount only for 'adjustment'. Typing it
    # expense would stage a row an owner can accept and the commit must then reject.
    rows = [row(1, booked_date="15/07/2024", description="FEE WAIVED", amount="0.00")]

    batch = normalize.normalize(profile(), extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert batch.rows[0].amount == Decimal("0.0000")
    assert batch.rows[0].proposed_txn_type == "adjustment"


@pytest.mark.parametrize("convention", ["cr_suffix", "debit_credit_columns"])
def test_inversion_never_reverses_a_convention_that_already_states_direction(
    convention: str,
) -> None:
    # A Dr/Cr suffix and a debit/credit column pair each state the direction themselves, and
    # the generic rule already lands a card purchase on the ledger's negative side. Inverting
    # on top of that stores every purchase as income and every payment as expense — reversing
    # the whole card while staying internally consistent.
    fields_purchase = {"amount": "310.00"}
    fields_payment = {"amount": "500.00 CR"}
    changes: dict[str, object] = {"signConvention": convention}
    if convention == "debit_credit_columns":
        fields_purchase = {"debit": "310.00"}
        fields_payment = {"credit": "500.00"}
        changes["columns"] = [
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "debit", "xMin": 300.0, "xMax": 450.0},
            {"field": "credit", "xMin": 450.0, "xMax": 600.0},
        ]
    rows = [
        row(1, booked_date="15/07/2024", description="PURCHASE", **fields_purchase),
        row(2, booked_date="16/07/2024", description="PAYMENT", **fields_payment),
    ]
    # model_copy bypasses validation on purpose: LayoutCreate now refuses this combination, and
    # this proves the engine does not depend on that refusal to stay in the ledger frame.
    layout = profile(**changes).model_copy(update={"invertsSign": True})

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.amount for line in batch.rows] == [Decimal("-310.0000"), Decimal("500.0000")]
    assert [line.proposed_txn_type for line in batch.rows] == ["expense", "income"]


def test_inversion_applies_to_a_card_running_balance() -> None:
    # A card balance rising from 4,500 to 4,810 means 310 more is owed, so the delta is a
    # purchase: negative in the ledger. Here the statement's frame really is the issuer's, and
    # inversion is what carries it into the ledger's.
    rows = [
        row(1, booked_date="15/07/2024", description="PURCHASE", running_balance="4,810.00"),
        row(2, booked_date="16/07/2024", description="PAYMENT", running_balance="4,310.00"),
    ]
    layout = profile(
        signConvention="balance_delta",
        invertsSign=True,
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout, extraction(rows, declared_opening="4,500.00"), account_currency=ACCOUNT_CURRENCY
    )

    assert [line.amount for line in batch.rows] == [Decimal("-310.0000"), Decimal("500.0000")]


def test_a_balance_is_signed_by_its_own_dr_cr_suffix() -> None:
    # A balance is a position rather than a movement, so the suffix means one thing wherever it
    # appears — unlike an amount's, which means something only under `cr_suffix`. `parse_amount`
    # is right to strip it and this is the place that is wrong to.
    assert normalize.parse_balance("1,234.56 Cr") == (
        normalize.SignedAmount(Decimal("1234.5600"), True),
        None,
    )
    assert normalize.parse_balance("100.00 Dr") == (
        normalize.SignedAmount(Decimal("-100.0000"), True),
        None,
    )
    assert normalize.parse_balance("100.00") == (
        normalize.SignedAmount(Decimal("100.0000"), False),
        None,
    )
    # Not a number at all carries no reason: every caller already has a word for that.
    assert normalize.parse_balance("Closing balance") == (None, None)


def test_a_suffixed_running_balance_reaches_the_row_signed_and_labelled() -> None:
    # An account that falls into overdraft crosses from Cr to Dr, and a balance read a suffix
    # short reports a false reconciliation break on every statement that does. The label travels
    # with the value because a figure that stated its direction has nothing left to reframe.
    rows = [
        row(
            1,
            booked_date="15/07/2024",
            description="ALPHA",
            amount="-310.00",
            running_balance="4,690.00 Cr",
        ),
        row(
            2,
            booked_date="16/07/2024",
            description="BETA",
            amount="-4,790.00",
            running_balance="100.00 Dr",
        ),
    ]
    layout = profile(
        hasRunningBalance=True,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "amount", "xMin": 300.0, "xMax": 450.0},
            {"field": "running_balance", "xMin": 450.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.running_balance for line in batch.rows] == [
        Decimal("4690.0000"),
        Decimal("-100.0000"),
    ]
    assert [line.balance_direction_is_stated for line in batch.rows] == [True, True]


def test_a_balance_delta_crosses_into_overdraft_without_losing_forty() -> None:
    # 100 Cr to 50 Cr to 20 Dr is a fall of 50 and then of 70. Read the suffixes short and the
    # second becomes a fall of 30: the derived amount is wrong by twice the overdrawn balance,
    # and the row would commit that figure.
    rows = [
        row(1, booked_date="15/07/2024", description="ALPHA", running_balance="50.00 Cr"),
        row(2, booked_date="16/07/2024", description="BETA", running_balance="20.00 Dr"),
    ]
    layout = profile(
        signConvention="balance_delta",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout, extraction(rows, declared_opening="100.00 Cr"), account_currency=ACCOUNT_CURRENCY
    )

    assert [line.amount for line in batch.rows] == [Decimal("-50.0000"), Decimal("-70.0000")]


def test_a_delta_between_two_suffixed_balances_is_not_inverted_again() -> None:
    # The balances said which way they go, so the movement between them is already in the
    # ledger's frame and `inverts_sign` has nothing left to reframe. The chain has to *cross*
    # from Dr to Cr for the two readings to differ: a same-suffix chain gives the same two
    # numbers whether the suffix is read or stripped, so it would pass on a revert and prove
    # nothing. Here the account clears 4,810 owed and ends 50 in credit — a movement of 4,860.
    # Stripped and then inverted it reads 4,760, short by twice the credit balance.
    rows = [
        row(1, booked_date="15/07/2024", description="SETTLEMENT", running_balance="50.00 Cr"),
        row(2, booked_date="16/07/2024", description="PURCHASE", running_balance="310.00 Dr"),
    ]
    layout = profile(
        signConvention="balance_delta",
        invertsSign=True,
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout,
        extraction(rows, declared_opening="4,810.00 Dr"),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert [line.amount for line in batch.rows] == [Decimal("4860.0000"), Decimal("-360.0000")]


def test_a_chain_mixing_suffixed_and_bare_balances_is_framed_before_it_is_subtracted() -> None:
    # An owner's anchor pattern capturing `([0-9,.-]+)` takes the number without its suffix
    # while the running-balance column keeps it, and nothing prevents that — so a bare declared
    # opening beside `Dr` column cells needs no exotic layout. Subtracting first and framing the
    # difference takes it between two frames and leaves it in neither: the first amount comes
    # out as -9,310 where the card was charged 310.
    rows = [
        row(1, booked_date="15/07/2024", description="PURCHASE", running_balance="4,810.00 Dr"),
        row(2, booked_date="16/07/2024", description="PAYMENT", running_balance="4,310.00 Dr"),
    ]
    layout = profile(
        signConvention="balance_delta",
        invertsSign=True,
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout, extraction(rows, declared_opening="4,500.00"), account_currency=ACCOUNT_CURRENCY
    )

    assert [line.amount for line in batch.rows] == [Decimal("-310.0000"), Decimal("500.0000")]


def test_a_balance_that_states_its_direction_twice_and_disagrees_is_refused() -> None:
    # `-100.00 Cr` says debit with its sign and credit with its suffix. Every sibling path here
    # fails the row rather than picking one, because resolving it silently is the invisible sign
    # error Invariant I2 exists to prevent. Where the two agree there is nothing to resolve.
    reason = "states a sign and a Dr/Cr suffix that disagree"
    assert normalize.parse_balance("-100.00 Cr") == (None, reason)
    assert normalize.parse_balance("(100.00) Cr") == (None, reason)
    # Where the two agree there is nothing to resolve, and nothing to refuse.
    assert normalize.parse_balance("-100.00 Dr") == (
        normalize.SignedAmount(Decimal("-100.0000"), True),
        None,
    )


@pytest.mark.parametrize("contradicting_row", [1, 2])
def test_a_contradicting_running_balance_stops_the_statement(contradicting_row: int) -> None:
    """Refusing the cell is right, and refusing it by dropping the row is not.

    `reconcile` is handed the surviving rows and cannot tell that one was dropped, so a
    statement whose *last* line was dropped reconciled `matched` against the line before it —
    I8 reporting satisfaction over a truncated statement. Both positions are asserted because
    only the last one produced that verdict, and a rule that holds for one row of two is not a
    rule. The message names the cell and the remedy: `-20.00 Cr` is plainly a number, and an
    owner told it is not goes looking for the wrong thing in a cell they can read.
    """
    balances = ["100.00 Cr", "80.00 Cr"]
    balances[contradicting_row - 1] = "-20.00 Cr"
    rows = [
        row(
            number,
            booked_date=f"1{number}/07/2024",
            description="ALPHA",
            amount="-10.00",
            running_balance=balance,
        )
        for number, balance in enumerate(balances, start=1)
    ]
    layout = profile(
        hasRunningBalance=True,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "amount", "xMin": 300.0, "xMax": 450.0},
            {"field": "running_balance", "xMin": 450.0, "xMax": 600.0},
        ],
    )

    with pytest.raises(normalize.NormalizationError) as refusal:
        normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert f"row {contradicting_row}'s running_balance" in str(refusal.value)
    assert "states a sign and a Dr/Cr suffix that disagree" in str(refusal.value)
    assert "remap the layout" in str(refusal.value)
    assert "not a number" not in str(refusal.value)


@pytest.mark.parametrize(
    ("opening", "second_balance", "named"),
    [
        ("100.00 Cr", "-20.00 Cr", "row 2's running_balance"),
        ("-100.00 Cr", "20.00 Cr", "the statement's opening balance"),
    ],
)
def test_a_contradicting_balance_stops_a_balance_delta_statement(
    opening: str, second_balance: str, named: str
) -> None:
    # Same rule where the balance *is* the amount, and the declared opening is a balance too:
    # deriving amounts from a chain that contradicts itself puts wrong figures in the ledger.
    rows = [
        row(1, booked_date="15/07/2024", description="ALPHA", running_balance="50.00 Cr"),
        row(2, booked_date="16/07/2024", description="BETA", running_balance=second_balance),
    ]
    layout = profile(
        signConvention="balance_delta",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    with pytest.raises(normalize.NormalizationError) as refusal:
        normalize.normalize(
            layout, extraction(rows, declared_opening=opening), account_currency=ACCOUNT_CURRENCY
        )

    assert named in str(refusal.value)
    assert "states a sign and a Dr/Cr suffix that disagree" in str(refusal.value)


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("suffixed", list(product([False, True], repeat=3)))
def test_normalization_and_reconciliation_read_every_balance_in_the_same_frame(
    suffixed: tuple[bool, bool, bool], invert: bool
) -> None:
    """The task's acceptance criterion, swept rather than sampled.

    An account falls from 100 to 60 and then into an overdraft of 30. Each of the three balances
    is rendered either bare or with its own `Dr`/`Cr` suffix, under both inversions — sixteen
    statements, each internally consistent and read differently.

    `_balance_deltas` derives the amounts and `reconcile` walks the same balances, so `matched`
    is the assertion that the two agree about the frame. They did not: framing the *difference*
    instead of each balance was right only while every balance in a chain agreed about stating
    its direction, and a mixed chain then belonged to neither frame.

    What this cannot catch is an error both modules make identically — they read one
    `frame_balance`, so breaking it breaks both sides and all sixteen still match. The amounts
    themselves are pinned by the mixed-chain test above.
    """

    def render(value: str, is_suffixed: bool) -> str:
        if not is_suffixed:
            return value
        number = Decimal(value)
        return f"{-number if number < 0 else number} {'Dr' if number < 0 else 'Cr'}"

    opening, first, second = (
        render(v, flag) for v, flag in zip(["100.00", "60.00", "-30.00"], suffixed, strict=True)
    )
    rows = [
        row(1, booked_date="15/07/2024", description="ALPHA", running_balance=first),
        row(2, booked_date="16/07/2024", description="BETA", running_balance=second),
    ]
    layout = profile(
        signConvention="balance_delta",
        invertsSign=invert,
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout, extraction(rows, declared_opening=opening), account_currency=ACCOUNT_CURRENCY
    )
    assert not batch.failures

    verdict = reconcile.reconcile(
        layout, batch.rows, declared_opening=opening, declared_closing=None
    )
    assert verdict.status == "matched", verdict.message


def test_a_populated_original_amount_that_does_not_parse_fails_its_row() -> None:
    # Both halves absent looks exactly like a statement that stated no foreign amount, so the
    # bank's own conversion would be discarded without a word.
    layout = profile(
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "amount", "xMin": 300.0, "xMax": 450.0},
            {"field": "original_amount", "xMin": 450.0, "xMax": 520.0},
            {"field": "original_currency", "xMin": 520.0, "xMax": 600.0},
        ]
    )
    rows = [
        row(
            1,
            booked_date="15/07/2024",
            description="FOREIGN",
            amount="-310.00",
            original_amount="7S.OO",
        ),
        row(2, booked_date="16/07/2024", description="PLAIN", amount="-20.00"),
    ]

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.description_raw for line in batch.rows] == ["PLAIN"]
    assert batch.failures[0].reason == "original_amount is not a number"


def test_an_amount_that_only_crosses_the_ledger_limit_when_rounded_fails_its_row() -> None:
    # Below the ceiling before rounding, above it after: the row would be normalized and then
    # rejected by Postgres, which is precisely the failure the limit check exists to prevent.
    assert normalize.parse_amount("9999999999999999.99999") is None
    assert normalize.parse_amount("9999999999999999.9999") == Decimal("9999999999999999.9999")


def test_a_foreign_amount_takes_the_direction_of_the_row_it_belongs_to() -> None:
    # The two representations describe one movement. Storing 310 out of the account beside a
    # positive 75 in its original currency would contradict itself permanently.
    layout = profile(
        signConvention="cr_suffix",
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "amount", "xMin": 300.0, "xMax": 450.0},
            {"field": "original_amount", "xMin": 450.0, "xMax": 520.0},
            {"field": "original_currency", "xMin": 520.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(
            1,
            booked_date="15/07/2024",
            description="PURCHASE",
            amount="310.00",
            original_amount="75.00",
            original_currency="XBB",
        ),
        row(
            2,
            booked_date="16/07/2024",
            description="REFUND",
            amount="310.00 CR",
            original_amount="75.00",
            original_currency="XBB",
        ),
    ]

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [(line.amount, line.original_amount) for line in batch.rows] == [
        (Decimal("-310.0000"), Decimal("-75.0000")),
        (Decimal("310.0000"), Decimal("75.0000")),
    ]


def test_a_two_digit_value_date_after_the_period_still_resolves_its_century() -> None:
    # The period anchors the century; it does not confine the value date. Resolving by
    # containment alone would drop a September settlement back to Python's 1999 pivot.
    layout = profile(
        dateFormat="dd/MM/yy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "value_date", "xMin": 100.0, "xMax": 200.0},
            {"field": "description", "xMin": 200.0, "xMax": 400.0},
            {"field": "amount", "xMin": 400.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(
            1,
            booked_date="31/08/99",
            value_date="02/09/99",
            description="LATE SETTLEMENT",
            amount="-10.00",
        )
    ]

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/07/2099", "period_end": "31/08/2099"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.failures == []
    assert batch.rows[0].booked_date == date(2099, 8, 31)
    assert batch.rows[0].value_date == date(2099, 9, 2)


@pytest.mark.parametrize("convention", ["signed", "cr_suffix"])
def test_a_debit_credit_pair_is_an_amount_under_every_convention_that_accepts_one(
    convention: str,
) -> None:
    # LayoutCreate accepts these conventions with a debit/credit pair and no amount column,
    # and the engine counts those cells as an amount when starting a row. Reading only the
    # amount cell here failed every row of a layout the rest of the system had accepted.
    layout = profile(
        signConvention=convention,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "debit", "xMin": 300.0, "xMax": 450.0},
            {"field": "credit", "xMin": 450.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(1, booked_date="15/07/2024", description="OUT", debit="310.00"),
        row(2, booked_date="16/07/2024", description="IN", credit="500.00"),
    ]

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert batch.failures == []
    assert [line.amount for line in batch.rows] == [Decimal("-310.0000"), Decimal("500.0000")]


def test_a_column_stated_direction_is_never_inverted_even_under_an_invertible_convention() -> None:
    # `signed` may be declared with inverts_sign, but a row whose direction came from a debit
    # or credit column has already stated it — inverting that reverses the row.
    layout = profile(
        signConvention="signed",
        invertsSign=True,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 300.0},
            {"field": "debit", "xMin": 300.0, "xMax": 450.0},
            {"field": "credit", "xMin": 450.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(1, booked_date="15/07/2024", description="OUT", debit="310.00"),
        row(2, booked_date="16/07/2024", description="IN", credit="500.00"),
    ]

    batch = normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)

    assert [line.amount for line in batch.rows] == [Decimal("-310.0000"), Decimal("500.0000")]


def test_a_broken_running_balance_chain_derives_nothing_after_the_break() -> None:
    # Every balance_delta amount is a difference between consecutive balances. One unusable
    # balance makes the next difference span two rows, so the surviving row would silently
    # carry the failed row's money under its own description — and the batch could still
    # reconcile, because the total is right and only the attribution is wrong.
    rows = [
        row(1, booked_date="15/07/2024", description="FIRST", running_balance="4,690.00"),
        row(2, booked_date="16/07/2024", description="UNREADABLE", running_balance="4,89O.OO"),
        row(3, booked_date="17/07/2024", description="THIRD", running_balance="5,000.00"),
    ]
    layout = profile(
        signConvention="balance_delta",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout, extraction(rows, declared_opening="5,000.00"), account_currency=ACCOUNT_CURRENCY
    )

    assert [line.description_raw for line in batch.rows] == ["FIRST"]
    assert [failure.source_row_number for failure in batch.failures] == [2, 3]
    assert "is not a number" in batch.failures[0].reason
    assert "chain" in batch.failures[1].reason


def test_no_failure_reason_carries_a_digit_sequence_from_the_statement() -> None:
    # Plan section 5.6: no error message may contain a digit sequence of seven or more digits
    # from the document, contiguous or separator-formatted. These reasons are parse diagnostics
    # and reach an HTTP response, and a date alone trips the rule before any card number does.
    layout = profile(
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "value_date", "xMin": 100.0, "xMax": 200.0},
            {"field": "description", "xMin": 200.0, "xMax": 300.0},
            {"field": "amount", "xMin": 300.0, "xMax": 450.0},
            {"field": "original_amount", "xMin": 450.0, "xMax": 520.0},
            {"field": "original_currency", "xMin": 520.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(1, booked_date="4111-1111-1111-1111", description="A", amount="-1.00"),
        row(2, booked_date="15/07/2024", description="B", amount="4111111111111111X"),
        row(
            3,
            booked_date="16/07/2024",
            value_date="4111 1111 1111 1111",
            description="C",
            amount="-1.00",
        ),
        row(
            4,
            booked_date="17/07/2024",
            description="D",
            amount="-1.00",
            original_amount="4111.1111.1111.1111",
        ),
        # Out of period: the reason must not carry the row's date or the period's endpoints
        # either — an ISO date is eight digits once its separators are ignored.
        row(5, booked_date="15/09/2024", description="E", amount="-1.00"),
    ]

    batch = normalize.normalize(
        layout,
        extraction(
            rows,
            metadata={"period_start": "01/07/2024", "period_end": "31/07/2024"},
        ),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert len(batch.failures) == 5
    for failure in batch.failures:
        normalized = re.sub(r"(?<=\d)[\W_]+(?=\d)", "", failure.reason)
        assert re.search(r"\d{7,}", normalized) is None, failure.reason
        assert "4111" not in failure.reason


def test_the_audit_row_keeps_the_whole_physical_line() -> None:
    # `fields` holds only what fell inside a band; the physical line holds everything. Keeping
    # the split alone would drop whatever sat outside every column before Task 5 stores it,
    # and "what did the statement actually say?" is the question raw_row exists to answer.
    extracted = RawRow(
        page_number=1,
        row_number=1,
        raw_text="15/07/2024  ALPHA   UNBANDED NOTE   -10.00",
        fields={"booked_date": "15/07/2024", "description": "ALPHA", "amount": "-10.00"},
    )

    batch = normalize.normalize(
        profile(), extraction([extracted]), account_currency=ACCOUNT_CURRENCY
    )

    assert batch.rows[0].raw_row["raw_text"] == "15/07/2024  ALPHA   UNBANDED NOTE   -10.00"
    assert batch.rows[0].raw_row["description"] == "ALPHA"


def test_a_balance_delta_beyond_the_ledgers_range_fails_its_row() -> None:
    # Both balances parse, and their difference still cannot be stored: the subtraction is its
    # own arithmetic and needs its own check.
    rows = [
        row(1, booked_date="15/07/2024", description="HUGE", running_balance="9999999999999999"),
    ]
    layout = profile(
        signConvention="balance_delta",
        hasRunningBalance=True,
        closingBalancePattern=None,
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "running_balance", "xMin": 400.0, "xMax": 600.0},
        ],
    )

    batch = normalize.normalize(
        layout,
        extraction(rows, declared_opening="-9999999999999999"),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.rows == []
    assert "range" in batch.failures[0].reason


def test_century_windowing_crosses_the_rollover() -> None:
    # A December 2099 statement settling on 2 January: the period's own century yields only
    # year 2000, a hundred years early. The neighbouring centuries have to be candidates too,
    # or the nearest-century rule cannot reach across the rollover it exists to handle.
    layout = profile(
        dateFormat="dd/MM/yy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
        columns=[
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "value_date", "xMin": 100.0, "xMax": 200.0},
            {"field": "description", "xMin": 200.0, "xMax": 400.0},
            {"field": "amount", "xMin": 400.0, "xMax": 600.0},
        ],
    )
    rows = [
        row(
            1,
            booked_date="31/12/99",
            value_date="02/01/00",
            description="ROLLOVER",
            amount="-10.00",
        )
    ]

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/12/2099", "period_end": "31/12/2099"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.failures == []
    assert batch.rows[0].booked_date == date(2099, 12, 31)
    assert batch.rows[0].value_date == date(2100, 1, 2)


def test_a_date_the_anchored_century_cannot_hold_fails_rather_than_moving_century() -> None:
    # 2100 is not a leap year and 2000 was. Choosing the nearest *valid* candidate would file a
    # 29 February a century away from the statement that printed it; the century is anchored
    # first, and a date that century cannot represent is simply not a date.
    layout = profile(
        dateFormat="dd/MM/yy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
    )
    rows = [row(1, booked_date="29/02/00", description="LEAP", amount="-10.00")]

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01/02/2100", "period_end": "28/02/2100"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.rows == []
    assert batch.failures[0].reason == "booked_date is not a dd/MM/yy date"


@pytest.mark.parametrize("mapped", ["period_start", "period_end"])
def test_a_one_sided_period_mapping_is_refused_rather_than_derived(mapped: str) -> None:
    # Deriving both bounds from the row dates would let an OCR date outside the captured bound
    # widen the period and survive — the captured bound is authoritative precisely so that
    # cannot happen, and half a period cannot be authoritative.
    # model_copy bypasses validation on purpose: LayoutCreate refuses this shape at save, and
    # this proves the engine does not depend on that refusal.
    layout = profile().model_copy(update={"metadataPatterns": {mapped: r"From (.+)$"}})
    rows = [row(1, booked_date="15/07/2024", description="ALPHA", amount="-10.00")]

    with pytest.raises(normalize.NormalizationError, match="period"):
        normalize.normalize(
            layout,
            extraction(rows, metadata={mapped: "01/07/2024"}),
            account_currency=ACCOUNT_CURRENCY,
        )


def test_a_two_digit_year_without_a_captured_period_is_refused() -> None:
    # An auto-proposed draft may carry dd/MM/yy before period mapping (recorded in the
    # progress tracker), and with no period there is nothing to window against: Python's pivot
    # reads 99 as 1999, and those dates then become the derived period that legitimizes them.
    # A century-early preview is worse than a refusal that says what is missing.
    #
    # LayoutCreate refuses to *save* this shape; normalization is a pure function over a
    # profile-shaped value and does not depend on that refusal, so the profile is built valid
    # and then stripped past validation to reach the engine as a draft would.
    layout = profile(
        dateFormat="dd/MM/yy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
    ).model_copy(update={"metadataPatterns": None})
    rows = [row(1, booked_date="15/07/99", description="ALPHA", amount="-10.00")]

    with pytest.raises(normalize.NormalizationError, match="two-digit year"):
        normalize.normalize(layout, extraction(rows), account_currency=ACCOUNT_CURRENCY)


def test_both_period_bounds_are_read_under_one_format() -> None:
    # The bounds are one statement's own statement of its period. Reading each under whichever
    # format happens to fit lets a mis-captured fragment pair with a real date and gate the
    # statement; requiring one shared format makes that pairing impossible.
    layout = profile(
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        }
    )
    rows = [row(1, booked_date="15/07/2024", description="ALPHA", amount="-10.00")]

    with pytest.raises(normalize.NormalizationError, match="one date format"):
        normalize.normalize(
            layout,
            extraction(rows, metadata={"period_start": "01/07/2024", "period_end": "31-07-2024"}),
            account_currency=ACCOUNT_CURRENCY,
        )


def test_a_period_written_differently_from_the_rows_still_reads() -> None:
    # A statement's header often writes its period differently from its transaction lines.
    # Both bounds agree on one format, so the period is unambiguous and the layout is usable.
    layout = profile(
        dateFormat="dd-MM-yyyy",
        metadataPatterns={
            "period_start": r"From (.+?) to",
            "period_end": r"to (.+)$",
        },
    )
    rows = [row(1, booked_date="15-07-2024", description="ALPHA", amount="-10.00")]

    batch = normalize.normalize(
        layout,
        extraction(rows, metadata={"period_start": "01 Jul 2024", "period_end": "31 Jul 2024"}),
        account_currency=ACCOUNT_CURRENCY,
    )

    assert batch.period.start == date(2024, 7, 1)
    assert batch.period.end == date(2024, 7, 31)
    assert [line.booked_date for line in batch.rows] == [date(2024, 7, 15)]
