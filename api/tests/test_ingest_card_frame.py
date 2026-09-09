"""Slice 1.1: a card whose rows state their direction and whose balances do not.

The layout in hand presents every purchase bare and every credit with a `CR` suffix, so
`cr_suffix` already lands each row on the ledger's correct side. Its declared anchors are
written bare — "Previous Statement Due 6,140.40" is money owed, and nothing in the figure says
so — and only `inverts_sign` carries them into the ledger's frame. The two behaviours have to
hold together on one profile, which is what these tests pin: the frame switch applies to the
figures that stated no direction and is withheld from the ones that did.

The amounts are the shape of a real statement's arithmetic, not its contents.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from oneledger.ingest.engine import extract_pages
from oneledger.ingest.normalize import normalize
from oneledger.ingest.reconcile import (
    STATUS_MATCHED,
    STATUS_MISMATCHED,
    Verdict,
    reconcile,
)
from oneledger.ingest.words import Line, Page, Word
from oneledger.routes.layouts import LayoutCreate

# Σ of the rows below in the ledger's frame: +6,140.40 − 36.50 − 4,442.68 = +1,661.22.
# The anchors are owed, so the identity only closes once both are negative:
#   −6,140.40 + 1,661.22 = −4,479.18
OPENING = "6,140.40"
CLOSING = "4,479.18"


def card_profile(**changes: object) -> LayoutCreate:
    values: dict[str, object] = {
        "name": "Card statement",
        "fingerprints": ["Credit Card Statement", "Card Type:"],
        "columns": [
            {"field": "booked_date", "xMin": 30.0, "xMax": 120.0},
            {"field": "value_date", "xMin": 120.0, "xMax": 195.0},
            {"field": "description", "xMin": 195.0, "xMax": 460.0},
            {"field": "amount", "xMin": 460.0, "xMax": 580.0},
        ],
        "dateFormat": "dd/MM/yyyy",
        "signConvention": "cr_suffix",
        "invertsSign": True,
        "chronologicalOrder": "ascending",
        "rowStartRule": "has_date_and_amount",
        "continuationRule": "no_date_and_no_amount",
        "openingBalancePattern": r"Previous Statement Due\s+([0-9,.]+)",
        "closingBalancePattern": r"Total Payment Due\s+([0-9,.]+)",
        "hasRunningBalance": False,
    }
    values.update(changes)
    return LayoutCreate.model_validate(values)


def word(x0: float, x1: float, text: str, top: float) -> Word:
    return Word(x0=x0, x1=x1, top=top, bottom=top + 9.0, text=text)


def line(top: float, *words: Word) -> Line:
    return Line(top=top, words=list(words))


def transaction(top: float, day: str, description: str, amount: str) -> Line:
    return line(
        top,
        word(52.0, 98.0, day, top),
        word(139.0, 185.0, day, top),
        word(203.0, 400.0, description, top),
        word(500.0, 560.0, amount, top),
    )


def statement_pages(
    *, opening: str = OPENING, closing: str = CLOSING, rows: list[Line] | None = None
) -> list[Page]:
    body = (
        rows
        if rows is not None
        else [
            transaction(100.0, "26/07/2026", "TRANSFER PAYMENT RECEIVED", "6,140.40CR"),
            transaction(120.0, "29/07/2026", "TAXI RIDE", "36.50"),
            transaction(140.0, "30/07/2026", "SUBSCRIPTION RENEWAL", "4,442.68"),
        ]
    )
    summary = [
        line(200.0, word(30.0, 220.0, f"Previous Statement Due {opening}", 200.0)),
        line(220.0, word(30.0, 220.0, f"Total Payment Due {closing}", 220.0)),
    ]
    return [Page(lines=body + summary, has_text=True)]


def verdict_for(profile: LayoutCreate, pages: list[Page]) -> Verdict:
    result = extract_pages(profile, pages)
    batch = normalize(profile, result, account_currency="AED")
    assert not batch.failures, batch.failures
    return reconcile(
        profile,
        batch.rows,
        declared_opening=result.declared_opening,
        declared_closing=result.declared_closing,
    )


def test_a_card_reconciles_when_its_bare_anchors_are_reframed_and_its_rows_are_not() -> None:
    verdict = verdict_for(card_profile(), statement_pages())

    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_opening == Decimal("-6140.4000")
    assert verdict.declared_closing == Decimal("-4479.1800")
    assert verdict.computed_closing == Decimal("-4479.1800")


def test_the_suffix_directs_each_row_and_the_frame_switch_leaves_it_alone() -> None:
    # If inverts_sign reached the rows as well, the payment would land negative and every
    # purchase positive — a card reversed end to end while still summing consistently.
    result = extract_pages(card_profile(), statement_pages())
    batch = normalize(card_profile(), result, account_currency="AED")

    assert [row.amount for row in batch.rows] == [
        Decimal("6140.4000"),
        Decimal("-36.5000"),
        Decimal("-4442.6800"),
    ]


def test_a_card_read_without_the_frame_switch_mismatches() -> None:
    # The regression this branch exists to stop: with no inversion the anchors stay positive
    # and the batch reports a delta of exactly twice the opening balance.
    verdict = verdict_for(card_profile(invertsSign=False), statement_pages())

    assert verdict.status == STATUS_MISMATCHED
    assert verdict.declared_closing == Decimal("4479.1800")
    assert verdict.computed_closing == Decimal("7801.6200")


def test_an_anchor_that_states_its_own_direction_is_not_reframed_again() -> None:
    # A statement mixing a suffixed anchor with a bare one must read each in its own terms;
    # reframing the suffixed one as well would reverse the half that had already spoken.
    verdict = verdict_for(
        card_profile(),
        statement_pages(opening="6,140.40 Dr", closing="4,479.18"),
    )

    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_opening == Decimal("-6140.4000")


@pytest.mark.parametrize("convention", ["cr_suffix", "debit_credit_columns"])
def test_a_direction_stating_convention_still_accepts_the_frame_switch(convention: str) -> None:
    columns = [
        {"field": "booked_date", "xMin": 30.0, "xMax": 120.0},
        {"field": "description", "xMin": 195.0, "xMax": 460.0},
        {"field": "debit", "xMin": 460.0, "xMax": 520.0},
        {"field": "credit", "xMin": 520.0, "xMax": 580.0},
    ]
    profile = card_profile(
        signConvention=convention,
        **({"columns": columns} if convention == "debit_credit_columns" else {}),
    )

    assert profile.invertsSign is True
