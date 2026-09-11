"""Slice 1.2: a passbook-style account whose statement ends in forty lines of prose.

Three things about this producer are measured rather than assumed:

1. It maps a **cheque-reference column between the date and the narration**, and its values sit
   left of where the `Chq No` header prints. Folded into the date band they join the date into
   one unparsable cell and the row never starts — 25 rows extracted from an eleven-page
   statement instead of 240.
2. It declares **both balance anchors and a running balance**, so every row is checked against
   the one before it and the whole statement against two figures it states itself.
3. Its statements close with a legal and legend block that `no_date_and_no_amount` cannot tell
   from wrapped narration, which is why this layout takes
   `blank_date_and_money_columns` (M2 spec §7 item 13).

**Every figure below is invented.** They reproduce the arithmetic of a passbook statement — a
declared opening, a walk through debit and credit columns, a declared closing that agrees — and
none of them is a real balance. Real statements are git-ignored and never enter this repository.

The profile under test is `db/seeds/passbook-layout.json` itself, so the seed cannot drift away
from the behaviour proved here.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.engine import ExtractionResult, extract_pages
from oneledger.ingest.normalize import NormalizedBatch, normalize
from oneledger.ingest.reconcile import STATUS_MATCHED, STATUS_UNAVAILABLE, Verdict, reconcile
from oneledger.ingest.words import extract_pages as read_pdf
from oneledger.routes.layouts import LayoutCreate
from tests.fixtures.synthetic_pdf import Placement, build_pdf

# The producer's own body size. At 10pt the date cell runs into the cheque column
# and the balance into the branch column, which would test the fixture, not the layout.
SIZE = 9.0

SEED = Path(__file__).resolve().parents[2] / "db" / "seeds" / "passbook-layout.json"
CURRENCY = "INR"

# The x of each cell, from the producer's own geometry. The cheque values print at 86 and the
# `Chq No` header at 92, so the band has to hold both — that relationship is what the fixture
# reproduces. It sets 90 rather than 86 only because Helvetica is wider than the producer's face
# and a date ending at 84 would otherwise be extracted as one word with the reference beside it.
X_DATE, X_REFERENCE, X_TEXT = 38.0, 90.0, 132.0
X_DEBIT, X_CREDIT, X_BALANCE, X_BRANCH = 345.0, 408.0, 497.0, 545.0

OPENING = Decimal("12000.00")
CLOSING = Decimal("11350.75")
# date, cheque reference, narration, its wrapped second line, debit, credit, balance
ROWS = (
    ("02-03-2026", None, "OPENING FEE", "LEVY/MAR", "150.00", None, "11850.00"),
    ("05-03-2026", "417", "INWARD CLEARING", "CLG/REF", None, "2500.25", "14350.25"),
    ("11-03-2026", None, "STANDING INSTRUCTION", "SI/UTILITY", "2999.50", None, "11350.75"),
)


def passbook_profile(**changes: object) -> LayoutCreate:
    """The seeded profile, with whatever a test needs changed about it."""
    values = json.loads(SEED.read_text())
    values.update(changes)
    return LayoutCreate.model_validate(values)


def _row(page: int, y: float, row: tuple[str | None, ...]) -> list[Placement]:
    booked, reference, head, tail, debit, credit, balance = row
    placements = [
        Placement(page=page, x=X_DATE, y=y, text=str(booked), size=SIZE),
        Placement(page=page, x=X_TEXT, y=y, text=str(head), size=SIZE),
        Placement(page=page, x=X_BALANCE, y=y, text=str(balance), size=SIZE),
        Placement(page=page, x=X_BRANCH, y=y, text="2026", size=SIZE),
        # The wrapped narration: the description column only, and — as this producer does — a
        # reference fragment beside it, which is why `reference` is not one of the columns the
        # stricter continuation rule requires to be blank.
        Placement(page=page, x=X_REFERENCE, y=y - 9.0, text="908", size=SIZE),
        Placement(page=page, x=X_TEXT, y=y - 9.0, text=str(tail), size=SIZE),
    ]
    if reference is not None:
        placements.append(Placement(page=page, x=X_REFERENCE, y=y, text=reference, size=SIZE))
    if debit is not None:
        placements.append(Placement(page=page, x=X_DEBIT, y=y, text=debit, size=SIZE))
    if credit is not None:
        placements.append(Placement(page=page, x=X_CREDIT, y=y, text=credit, size=SIZE))
    return placements


def _closing_block(page: int) -> list[Placement]:
    """`CLOSING BALANCE`, then the prose that must not reach the last transaction."""
    return [
        Placement(page=page, x=X_TEXT, y=300.0, text="CLOSING BALANCE", size=SIZE),
        Placement(page=page, x=X_BALANCE, y=300.0, text=str(CLOSING), size=SIZE),
        # Set across the page: prose in the date, reference and money columns as well.
        Placement(page=page, x=20.0, y=280.0, text="Nothing", size=SIZE),
        Placement(
            page=page, x=X_TEXT, y=280.0, text="in this paragraph is a sentence any", size=SIZE
        ),
        Placement(page=page, x=400.0, y=280.0, text="producer wrote", size=SIZE),
        Placement(page=page, x=20.0, y=268.0, text="It sits", size=SIZE),
        Placement(
            page=page, x=X_TEXT, y=268.0, text="here for its shape alone, set right", size=SIZE
        ),
        Placement(page=page, x=400.0, y=268.0, text="across it.", size=SIZE),
        Placement(page=page, x=38.0, y=250.0, text="REGISTERED OFFICE - a road, a city", size=SIZE),
        # Centred, as the producer sets it: it starts inside the description column and runs
        # past it into the debit column, which is what the stricter rule reads.
        Placement(page=page, x=235.0, y=238.0, text="++++ End of Statement ++++", size=SIZE),
    ]


def _placements() -> list[Placement]:
    header = [
        Placement(page=0, x=95.0, y=610.0, text="Axis", size=SIZE),
        # One run of text, as the producer sets it — including the double space before "To",
        # which is why the capture patterns spell the gaps `\s*` rather than a literal space.
        Placement(
            page=0,
            x=95.0,
            y=583.0,
            text=(
                "Statement of Account No :55500011 for the period "
                "(From : 01-03-2026  To : 31-03-2026)"
            ),
            size=SIZE,
        ),
        Placement(page=0, x=40.0, y=561.0, text="Tran Date", size=SIZE),
        Placement(page=0, x=92.0, y=561.0, text="Chq No", size=SIZE),
        Placement(page=0, x=203.0, y=561.0, text="Particulars", size=SIZE),
        Placement(page=0, x=339.0, y=561.0, text="Debit", size=SIZE),
        Placement(page=0, x=400.0, y=561.0, text="Credit", size=SIZE),
        Placement(page=0, x=473.0, y=561.0, text="Balance", size=SIZE),
        Placement(page=0, x=538.0, y=561.0, text="Init. Br", size=SIZE),
        Placement(page=0, x=X_TEXT, y=540.0, text="OPENING BALANCE", size=SIZE),
        Placement(page=0, x=X_BALANCE, y=540.0, text=str(OPENING), size=SIZE),
    ]
    return [
        *header,
        *_row(0, 515.0, ROWS[0]),
        *_row(0, 475.0, ROWS[1]),
        *_row(0, 435.0, ROWS[2]),
        *_closing_block(0),
    ]


@pytest.fixture
def statement(tmp_path: Path) -> Path:
    path = tmp_path / "passbook-statement.pdf"
    path.write_bytes(build_pdf(_placements()))
    return path


def parsed(
    statement: Path, profile: LayoutCreate
) -> tuple[ExtractionResult, NormalizedBatch, Verdict]:
    result = extract_pages(profile, read_pdf(statement))
    batch = normalize(profile, result, account_currency=CURRENCY)
    verdict = reconcile(
        profile,
        batch.rows,
        declared_opening=result.declared_opening,
        declared_closing=result.declared_closing,
    )
    return result, batch, verdict


def test_the_seeded_profile_reads_this_statement_exactly(statement: Path) -> None:
    """The golden output: three rows, their figures and references, the period, the verdict."""
    result, batch, verdict = parsed(statement, passbook_profile())

    assert batch.failures == []
    assert result.institution_name_raw == "Axis"
    assert "institution_name" not in result.metadata
    assert [
        (
            row.source_row_number,
            str(row.booked_date),
            row.reference_raw,
            row.description_raw,
            str(row.amount),
            str(row.running_balance),
            row.proposed_txn_type,
        )
        for row in batch.rows
    ] == [
        (1, "2026-03-02", None, "OPENING FEE LEVY/MAR", "-150.0000", "11850.0000", "expense"),
        (
            2,
            "2026-03-05",
            "417",
            "INWARD CLEARING CLG/REF",
            "2500.2500",
            "14350.2500",
            "income",
        ),
        (
            3,
            "2026-03-11",
            None,
            "STANDING INSTRUCTION SI/UTILITY",
            "-2999.5000",
            "11350.7500",
            "expense",
        ),
    ]

    assert (batch.period.start, batch.period.end, batch.period.is_derived) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
        False,
    )

    assert verdict.status == STATUS_MATCHED
    # Both anchors are the statement's own, so neither is derived from the rows.
    assert (verdict.declared_opening, verdict.opening_is_derived) == (OPENING, False)
    assert verdict.declared_closing == CLOSING
    assert verdict.delta == Decimal("0.0000")


def test_the_closing_prose_never_reaches_the_last_transaction(statement: Path) -> None:
    """Why this layout takes `blank_date_and_money_columns` (M2 spec §7 item 13)."""
    strict, batch, _ = parsed(statement, passbook_profile())
    assert batch.rows[-1].description_raw == "STANDING INSTRUCTION SI/UTILITY"

    loose, absorbed, verdict = parsed(
        statement, passbook_profile(continuationRule="no_date_and_no_amount")
    )
    # Under the looser rule the whole closing block lands in the last transaction, and the
    # statement still reconciles — the corruption is invisible to every check but this one.
    assert absorbed.rows[-1].description_raw == (
        "STANDING INSTRUCTION SI/UTILITY in this paragraph is a sentence any "
        "here for its shape alone, set right - a road, a city ++++ End of Statement"
    )
    assert verdict.status == STATUS_MATCHED
    # And it reports nothing lost, because it lost nothing — it took it.
    assert loose.lines_unparsed < strict.lines_unparsed


def test_a_wrapped_line_is_kept_even_though_its_reference_cell_is_filled(
    statement: Path,
) -> None:
    """`reference` is deliberately not one of the columns the stricter rule requires blank.

    This producer prints a reference fragment beside the wrapped narration. Requiring every
    column but the description to be empty would drop all three of these lines along with the
    closing prose — measured at 276 of 1,639 continuation lines on the real corpus, against 48
    for the rule as written. The fragment itself is not captured: a continuation contributes its
    description column and nothing else.
    """
    _, batch, _ = parsed(statement, passbook_profile())

    assert [row.reference_raw for row in batch.rows] == [None, "417", None]
    # Every row still carries the second physical line of its narration.
    assert all("/" in row.description_raw for row in batch.rows)


def test_the_reference_column_must_not_be_folded_into_the_date_band(statement: Path) -> None:
    """The measured band, and what a plausible wrong one costs.

    The `Chq No` values print at x=86 and the date at x=38. A date band stretched to the
    `Particulars` column swallows both into one cell, `dd-MM-yyyy` cannot read it, and a row
    with a cheque reference simply never starts.
    """
    folded = passbook_profile(
        columns=[
            {"field": "booked_date", "xMin": 20.0, "xMax": 128.0, "label": "Tran Date"},
            {"field": "description", "xMin": 128.0, "xMax": 330.0, "label": "Particulars"},
            {"field": "debit", "xMin": 330.0, "xMax": 390.0, "label": "Debit"},
            {"field": "credit", "xMin": 390.0, "xMax": 455.0, "label": "Credit"},
            {"field": "running_balance", "xMin": 455.0, "xMax": 535.0, "label": "Balance"},
        ]
    )
    _, batch, verdict = parsed(statement, folded)

    # The row carrying a cheque reference is gone, and what remains no longer reconciles.
    assert [str(row.booked_date) for row in batch.rows] == ["2026-03-02", "2026-03-11"]
    assert verdict.status != STATUS_MATCHED


def test_the_closing_anchor_is_a_second_figure_and_not_the_last_balance(tmp_path: Path) -> None:
    """Why the closing anchor is mapped even though a running balance is.

    The walk proves the rows agree with each other. The anchor proves they agree with a figure
    the statement states independently — so a statement whose own two figures disagree is
    caught. Without the anchor, reconcile takes the terminal running balance as the closing and
    the disagreement has nothing left to show up against.
    """
    contradicting = [
        placement
        for placement in _placements()
        if not (placement.y == 300.0 and placement.x == X_BALANCE)
    ]
    contradicting.append(Placement(page=0, x=X_BALANCE, y=300.0, text="11350.00", size=SIZE))
    path = tmp_path / "contradicting-closing.pdf"
    path.write_bytes(build_pdf(contradicting))

    _, _, caught = parsed(path, passbook_profile())
    assert caught.status != STATUS_MATCHED
    assert caught.delta == Decimal("-0.7500")

    _, _, unchecked = parsed(path, passbook_profile(closingBalancePattern=None))
    assert unchecked.status == STATUS_MATCHED


def test_a_dated_line_this_layout_cannot_price_blocks_the_batch(tmp_path: Path) -> None:
    """Why the rule is `has_date`, not `has_date_and_amount`.

    No line of the six statements measured carries a date without a debit or a credit, so the
    two rules read them identically. They differ on the line neither has met: `has_date` makes a
    row that fails to normalize and blocks the batch, while `has_date_and_amount` cannot start a
    row with it and cannot continue one either — it has a date — so the line is dropped into
    `lines_unparsed` and the rest reconcile without it.
    """
    stray = Placement(page=0, x=X_DATE, y=455.0, text="09-03-2026", size=SIZE)
    path = tmp_path / "dated-without-amount.pdf"
    path.write_bytes(build_pdf([*_placements(), stray]))

    _, batch, _ = parsed(path, passbook_profile())
    assert [failure.reason for failure in batch.failures] == [
        "the row states neither a debit nor a credit"
    ]

    _, ignored, _ = parsed(path, passbook_profile(rowStartRule="has_date_and_amount"))
    assert ignored.failures == []
    assert len(ignored.rows) == 3


def test_the_balance_band_stops_short_of_the_branch_column(statement: Path) -> None:
    """The right edge is measured too: the branch code prints immediately beside the balance."""
    swallowed = passbook_profile(
        columns=[
            {"field": "booked_date", "xMin": 20.0, "xMax": 84.0},
            {"field": "reference", "xMin": 84.0, "xMax": 128.0},
            {"field": "description", "xMin": 128.0, "xMax": 330.0},
            {"field": "debit", "xMin": 330.0, "xMax": 390.0},
            {"field": "credit", "xMin": 390.0, "xMax": 455.0},
            {"field": "running_balance", "xMin": 455.0, "xMax": 580.0},
        ]
    )
    _, batch, verdict = parsed(statement, swallowed)

    # The branch code joins the balance cell, and the pair is no longer a number.
    assert verdict.status != STATUS_MATCHED
    assert all(row.running_balance is None for row in batch.rows)


def test_a_row_whose_balance_did_not_extract_blocks_the_statement(tmp_path: Path) -> None:
    """Invariant I8: a statement that cannot be verified is refused, never imported.

    One statement in the corpus this layout was measured against prints a balance whose integer
    part is absent from its own text layer — the characters are spaces, so no band and no
    tolerance can recover them. The engine must refuse the whole statement rather than walk past
    the row, because two amounts misread in opposite directions cancel before the next stated
    balance and report `matched` for rows that are wrong.
    """
    damaged = [
        placement
        for placement in _placements()
        if not (placement.y == 475.0 and placement.x == X_BALANCE)
    ]
    damaged.append(Placement(page=0, x=519.0, y=475.0, text=".25", size=SIZE))
    path = tmp_path / "damaged-balance.pdf"
    path.write_bytes(build_pdf(damaged))

    _, batch, verdict = parsed(path, passbook_profile())

    assert batch.failures == []
    assert verdict.status == STATUS_UNAVAILABLE
    assert "carried no balance" in verdict.message
