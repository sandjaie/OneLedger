"""Slice 1.1: a card whose rows state their direction and whose balances do not.

The layout in hand presents every purchase bare and every credit with a `CR` suffix, so
`cr_suffix` already lands each row on the ledger's correct side. Its declared anchors are
written bare — a previous statement due is money owed, and nothing in the figure says so — and
only `inverts_sign` carries them into the ledger's frame. The two behaviours have to hold
together on one profile, which is what these tests pin: the frame switch applies to the figures
that stated no direction and is withheld from the ones that did.

The profile under test is `db/seeds/card-layout.json` itself, so the seed cannot drift away
from the behaviour proved here.

**Every figure below is invented.** They reproduce the arithmetic of a card statement — the
identity, the six-figure summary, the sixth figure that reconciles against nothing — and none
of them is a real balance. Real statements are git-ignored and never enter this repository.
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

# The six figures a statement summary prints, in its own order. Sigma of the rows below in the
# ledger's frame is +2,000.00 - 310.00 - 1,000.00 = +690.00, and the anchors are owed, so the
# identity closes only once both are negative: -4,500.00 + 690.00 = -3,810.00.
PREVIOUS_DUE = "4,500.00"
PURCHASES = "1,310.00"
INTEREST = "0.00"
PAYMENTS = "2,000.00"
TOTAL_DUE = "3,810.00"
# Total payment due plus a remaining installment principal. It reconciles against nothing,
# which is why the closing anchor captures the fifth figure and not the last.
CURRENT_BALANCE = "9,050.00"

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
    """A line carrying the raw text the producer would emit: characters joined with nothing.

    `with_raw_text` builds it that way in production and `engine._page_text` prefers it over
    the words, so the balance anchors are matched against the same string the 38 real files
    present — not against a space-joined convenience only these tests would ever see.
    """
    return Line(top=top, words=list(words), raw_text="".join(w.text for w in words))


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
        transaction(100.0, "26/07/2026", "TRANSFER PAYMENT RECEIVED", "2,000.00CR"),
        transaction(120.0, "29/07/2026", "TAXI RIDE", "310.00"),
        transaction(140.0, "30/07/2026", "SUBSCRIPTION RENEWAL", "1,000.00"),
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


def pages(summary: list[Line], extra: list[Line] | None = None) -> list[Page]:
    return [Page(lines=rows() + summary + (extra or []), has_text=True)]


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
    assert verdict.declared_opening == Decimal("-4500.0000")
    assert verdict.declared_closing == Decimal("-3810.0000")
    assert verdict.computed_closing == Decimal("-3810.0000")


def test_the_suffix_directs_each_row_and_the_frame_switch_leaves_it_alone() -> None:
    # If inverts_sign reached the rows as well, the payment would land negative and every
    # purchase positive — a card reversed end to end while still summing consistently.
    profile = card_profile()
    batch = normalize(
        profile, extract_pages(profile, pages(run_together_summary())), account_currency="AED"
    )

    assert [row.amount for row in batch.rows] == [
        Decimal("2000.0000"),
        Decimal("-310.0000"),
        Decimal("-1000.0000"),
    ]


def test_a_card_read_without_the_frame_switch_mismatches() -> None:
    # The regression this branch exists to stop: with no inversion the anchors stay in the
    # issuer's frame and the batch computes a closing balance nothing declared.
    verdict = verdict_for(card_profile(invertsSign=False), pages(run_together_summary()))

    assert verdict.status == STATUS_MISMATCHED
    assert verdict.declared_closing == Decimal("3810.0000")
    assert verdict.computed_closing == Decimal("5190.0000")


def test_an_anchor_that_states_its_own_direction_is_not_reframed_again() -> None:
    # A statement mixing a suffixed anchor with a bare one must read each in its own terms;
    # reframing the suffixed one as well would reverse the half that had already spoken.
    verdict = verdict_for(
        card_profile(**LABELLED_ANCHORS),
        pages(labelled_summary(f"{PREVIOUS_DUE} Dr", TOTAL_DUE)),
    )

    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_opening == Decimal("-4500.0000")


def test_a_debit_credit_pair_directs_its_rows_and_the_bare_anchors_still_reframe() -> None:
    # The other convention the removed validator refused. The columns state each row's
    # direction, so the frame switch must leave the amounts alone and reframe only the
    # anchors — the same split as cr_suffix, proved end to end rather than at the model.
    profile = card_profile(
        signConvention="debit_credit_columns",
        columns=[
            {"field": "booked_date", "xMin": 30.0, "xMax": 120.0},
            {"field": "description", "xMin": 195.0, "xMax": 460.0},
            {"field": "debit", "xMin": 460.0, "xMax": 520.0},
            {"field": "credit", "xMin": 520.0, "xMax": 580.0},
        ],
    )

    def paired(top: float, day: str, description: str, debit: str, credit: str) -> Line:
        return line(
            top,
            word(52.0, 98.0, day, top),
            word(203.0, 400.0, description, top),
            word(470.0, 510.0, debit, top),
            word(530.0, 570.0, credit, top),
        )

    statement = [
        Page(
            lines=[
                paired(100.0, "26/07/2026", "TRANSFER PAYMENT RECEIVED", "", PAYMENTS),
                paired(120.0, "29/07/2026", "TAXI RIDE", "310.00", ""),
                paired(140.0, "30/07/2026", "SUBSCRIPTION RENEWAL", "1,000.00", ""),
                *run_together_summary(),
            ],
            has_text=True,
        )
    ]
    result = extract_pages(profile, statement)
    batch = normalize(profile, result, account_currency="AED")
    verdict = reconcile(
        profile,
        batch.rows,
        declared_opening=result.declared_opening,
        declared_closing=result.declared_closing,
    )

    assert [row.amount for row in batch.rows] == [
        Decimal("2000.0000"),
        Decimal("-310.0000"),
        Decimal("-1000.0000"),
    ]
    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_opening == Decimal("-4500.0000")


# --- the seeded profile ------------------------------------------------------------------
#
# db/seeds/card-layout.json is a POST /layouts body, so it is validated here on every run: a
# seed the endpoint would reject is a seed that cannot be loaded, and nothing else in the tree
# would notice until someone tried to load it. These pin the file's values; the tests above
# are what prove the behaviour those values buy.


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
    # ADR-0012: a layout is data, and no institution name belongs in a file this repository
    # tracks. The fingerprints are what the statement prints about itself. The period is
    # derived rather than captured, because an installment block prints each plan's original
    # purchase date and those rows are part of the total the anchors reconcile against.
    profile = card_profile()

    assert profile.name == "Card statement"
    assert profile.fingerprints == ["Credit Card Statement", "Card Type:"]
    assert profile.metadataPatterns is None


def test_the_seeded_layout_drops_a_trailing_line_instead_of_absorbing_it() -> None:
    # With continuation on, page furniture after the transaction table lands in the last row's
    # description_raw. Off, the line is dropped and counted, so the loss is reported rather
    # than silent (Invariant 7) — and the row above it keeps the description the bank wrote.
    profile = card_profile()
    furniture = [line(300.0, word(30.0, 400.0, "Interest is charged on overdue balances", 300.0))]

    result = extract_pages(profile, pages(run_together_summary(), extra=furniture))

    assert profile.continuationRule is None
    assert result.lines_unparsed == 2  # the summary line and the furniture line
    assert result.rows[-1].fields["description"] == "SUBSCRIPTION RENEWAL"


def test_the_seeded_anchors_segment_a_line_of_run_together_figures() -> None:
    # The producer emits no spaces between cells, so the summary extracts as one run. Each
    # figure ends in two decimals, which is what makes the split deterministic.
    profile = card_profile()
    summary = f"{PREVIOUS_DUE}{PURCHASES}{INTEREST}{PAYMENTS}{TOTAL_DUE}{CURRENT_BALANCE}"

    opening = re.search(profile.openingBalancePattern or "", summary, re.MULTILINE)
    closing = re.search(profile.closingBalancePattern or "", summary, re.MULTILINE)

    assert opening is not None and opening.group(1) == PREVIOUS_DUE
    assert closing is not None and closing.group(1) == TOTAL_DUE


@pytest.mark.parametrize("figures", ["4,500.003,810.00", f"{PREVIOUS_DUE}{PURCHASES}" * 4])
def test_a_summary_line_of_the_wrong_length_yields_no_anchor_rather_than_a_wrong_one(
    figures: str,
) -> None:
    # The count is what makes the anchors safe: a line of five or eight figures must fail to
    # match, so reconciliation reports unavailable instead of anchoring to the wrong figure.
    profile = card_profile()

    assert re.search(profile.openingBalancePattern or "", figures, re.MULTILINE) is None
    assert re.search(profile.closingBalancePattern or "", figures, re.MULTILINE) is None
