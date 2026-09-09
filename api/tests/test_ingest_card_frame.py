"""Slice 1.1: a card whose rows state their direction and whose balances do not.

The layout in hand presents every purchase bare and every credit with a `CR` suffix, so
`cr_suffix` already lands each row on the ledger's correct side. Its declared anchors are
written bare — a previous statement due of 6,140.40 is money owed, and nothing in the figure
says so — and only `inverts_sign` carries them into the ledger's frame. The two behaviours have
to hold together on one profile, which is what these tests pin: the frame switch applies to the
figures that stated no direction and is withheld from the ones that did.

The profile under test is `db/seeds/card-layout.json` itself, so the seed cannot drift away
from the behaviour proved here. The amounts are the shape of a real statement's arithmetic,
not its contents.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.engine import extract_pages
from oneledger.ingest.normalize import normalize
from oneledger.ingest.reconcile import STATUS_MATCHED, STATUS_MISMATCHED, Verdict, reconcile
from oneledger.ingest.words import Line, Page, Word
from oneledger.routes.layouts import LayoutCreate

SEED = Path(__file__).resolve().parents[2] / "db" / "seeds" / "card-layout.json"

# The six figures the statement summary prints, in its own order. Sigma of the rows below in
# the ledger's frame is +6,140.40 - 36.50 - 4,442.68 = +1,661.22, and the anchors are owed, so
# the identity closes only once both are negative: -6,140.40 + 1,661.22 = -4,479.18.
PREVIOUS_DUE = "6,140.40"
PURCHASES = "4,479.18"
INTEREST = "0.00"
PAYMENTS = "6,140.40"
TOTAL_DUE = "4,479.18"
# Total payment due plus the remaining installment principal. It reconciles against nothing,
# which is why the closing anchor captures the fifth figure and not the last.
CURRENT_BALANCE = "13,728.34"

LABELLED_ANCHORS: dict[str, object] = {
    "openingBalancePattern": r"Previous Statement Due\s+([0-9,.]+)",
    "closingBalancePattern": r"Total Payment Due\s+([0-9,.]+)",
}


def card_profile(**changes: object) -> LayoutCreate:
    """The seeded profile, with whatever this test needs changed about it."""
    values = json.loads(SEED.read_text())
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


def rows() -> list[Line]:
    return [
        transaction(100.0, "26/07/2026", "TRANSFER PAYMENT RECEIVED", "6,140.40CR"),
        transaction(120.0, "29/07/2026", "TAXI RIDE", "36.50"),
        transaction(140.0, "30/07/2026", "SUBSCRIPTION RENEWAL", "4,442.68"),
    ]


def run_together_summary(opening: str = PREVIOUS_DUE, closing: str = TOTAL_DUE) -> list[Line]:
    """The summary as the producer writes it: six figures, one line, no separator."""
    figures = f"{opening}{PURCHASES}{INTEREST}{PAYMENTS}{closing}{CURRENT_BALANCE}"
    return [line(200.0, word(30.0, 260.0, figures, 200.0))]


def labelled_summary(opening: str, closing: str) -> list[Line]:
    return [
        line(200.0, word(30.0, 220.0, f"Previous Statement Due {opening}", 200.0)),
        line(220.0, word(30.0, 220.0, f"Total Payment Due {closing}", 220.0)),
    ]


def pages(summary: list[Line]) -> list[Page]:
    return [Page(lines=rows() + summary, has_text=True)]


def verdict_for(profile: LayoutCreate, statement: list[Page]) -> Verdict:
    result = extract_pages(profile, statement)
    batch = normalize(profile, result, account_currency="AED")
    assert not batch.failures, batch.failures
    return reconcile(
        profile,
        batch.rows,
        declared_opening=result.declared_opening,
        declared_closing=result.declared_closing,
    )


def test_a_card_reconciles_when_its_bare_anchors_are_reframed_and_its_rows_are_not() -> None:
    verdict = verdict_for(card_profile(), pages(run_together_summary()))

    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_opening == Decimal("-6140.4000")
    assert verdict.declared_closing == Decimal("-4479.1800")
    assert verdict.computed_closing == Decimal("-4479.1800")


def test_the_suffix_directs_each_row_and_the_frame_switch_leaves_it_alone() -> None:
    # If inverts_sign reached the rows as well, the payment would land negative and every
    # purchase positive — a card reversed end to end while still summing consistently.
    profile = card_profile()
    batch = normalize(
        profile, extract_pages(profile, pages(run_together_summary())), account_currency="AED"
    )

    assert [row.amount for row in batch.rows] == [
        Decimal("6140.4000"),
        Decimal("-36.5000"),
        Decimal("-4442.6800"),
    ]


def test_a_card_read_without_the_frame_switch_mismatches() -> None:
    # The regression this branch exists to stop: with no inversion the anchors stay in the
    # issuer's frame and the batch computes a closing balance nothing declared.
    verdict = verdict_for(card_profile(invertsSign=False), pages(run_together_summary()))

    assert verdict.status == STATUS_MISMATCHED
    assert verdict.declared_closing == Decimal("4479.1800")
    assert verdict.computed_closing == Decimal("7801.6200")


def test_an_anchor_that_states_its_own_direction_is_not_reframed_again() -> None:
    # A statement mixing a suffixed anchor with a bare one must read each in its own terms;
    # reframing the suffixed one as well would reverse the half that had already spoken.
    verdict = verdict_for(
        card_profile(**LABELLED_ANCHORS),
        pages(labelled_summary("6,140.40 Dr", TOTAL_DUE)),
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


# --- the seeded profile ------------------------------------------------------------------
#
# db/seeds/card-layout.json is a POST /layouts body, so it is validated here on every run: a
# seed the endpoint would reject is a seed that cannot be loaded, and nothing else in the tree
# would notice until someone tried to load it.


def test_the_seeded_card_layout_is_a_body_the_endpoint_accepts() -> None:
    profile = card_profile()

    assert profile.signConvention == "cr_suffix"
    assert profile.invertsSign is True
    assert [column.field for column in profile.columns] == [
        "booked_date",
        "value_date",
        "description",
        "amount",
    ]


def test_the_seeded_layout_names_no_institution_and_captures_no_period() -> None:
    profile = card_profile()

    # ADR-0012: a layout is data, and no institution name belongs in a file this repository
    # tracks. The fingerprints are what the statement prints about itself.
    assert profile.name == "Card statement"
    assert profile.fingerprints == ["Credit Card Statement", "Card Type:"]
    # The installment block prints each plan's original purchase date, so rows dated outside
    # the printed period are legitimate and part of the total the anchors reconcile against.
    assert profile.metadataPatterns is None


def test_the_seeded_layout_takes_no_continuation_line() -> None:
    # With the rule on, the last row absorbs every trailing line of the document — 2,232
    # characters of page furniture in one description_raw against 46 with it off. The dropped
    # lines are counted in lines_unparsed, so the loss is reported rather than silent (I7).
    assert card_profile().continuationRule is None


def test_the_seeded_anchors_segment_a_line_of_run_together_figures() -> None:
    # The producer emits no spaces between cells, so the summary extracts as one run. Each
    # figure ends in two decimals, which is what makes the split deterministic.
    profile = card_profile()
    summary = f"{PREVIOUS_DUE}{PURCHASES}{INTEREST}{PAYMENTS}{TOTAL_DUE}{CURRENT_BALANCE}"

    opening = re.search(profile.openingBalancePattern or "", summary, re.MULTILINE)
    closing = re.search(profile.closingBalancePattern or "", summary, re.MULTILINE)

    assert opening is not None and opening.group(1) == PREVIOUS_DUE
    assert closing is not None and closing.group(1) == TOTAL_DUE
