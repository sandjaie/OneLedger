"""Slice 1.2: a current account whose narration wraps and whose rows run newest-first.

Four things about this producer are measured rather than assumed, and each one is asserted
below because getting any of them wrong still parses — into the wrong answer, with no error
anywhere:

1. It writes **no space character between cells**, so every pattern the profile carries is
   written against the run-together form and uses `\\s*`, never `\\s+`.
2. Its rows are **newest first**, so the running-balance walk only closes read in reverse.
3. Its debit figures **state their own sign** in a column the header names, so one `amount`
   band spans both money columns and the sign is read from the figure.
4. The column header **repeats on every page** and a footer legend sits under the last row, and
   with narration continuation switched on both would otherwise be appended to a transaction.

**Every figure below is invented.** They reproduce the arithmetic of a current-account
statement — a running balance that carries its own `Cr`, a walk that closes only oldest-first,
an opening no line of the statement states — and none of them is a real balance. Real
statements are git-ignored and never enter this repository.

The profile under test is `db/seeds/account-layout.json` itself, so the seed cannot drift away
from the behaviour proved here.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.engine import ExtractionResult, extract_pages
from oneledger.ingest.normalize import NormalizationError, NormalizedBatch, normalize
from oneledger.ingest.reconcile import STATUS_MATCHED, Verdict, reconcile
from oneledger.ingest.words import extract_pages as read_pdf
from oneledger.routes.layouts import LayoutCreate
from tests.fixtures.synthetic_pdf import Placement, build_pdf

SEED = Path(__file__).resolve().parents[2] / "db" / "seeds" / "account-layout.json"
CURRENCY = "AED"

# The x of each cell, taken from the producer's own geometry: a date at the left margin, the
# narration beside it, the two money columns sharing one band, and the balance at the right.
X_DATE, X_TEXT, X_DEBIT, X_CREDIT, X_BALANCE, X_MARK = 37.0, 102.0, 302.0, 390.0, 496.0, 531.0

# Four transactions, newest first, as the statement prints them. Read oldest-first the walk is
# 1,275.50 - 125.50 = 1,150.00 - 400.00 = 750.00 + 1,000.00 = 1,750.00 - 250.00 = 1,500.00.
# Read in the order printed it closes on nothing, which is what makes `descending` load-bearing.
Row = tuple[str, str, str, str | None, str | None, str]

ROWS: tuple[Row, ...] = (
    ("12 Mar 2026", "CARD PURCHASE REF", "GROCERY", "-250.00", None, "1,500.00"),
    ("11 Mar 2026", "INWARD TRANSFER REF", "SALARY", None, "1,000.00", "1,750.00"),
    ("10 Mar 2026", "STANDING ORDER REF", "300.00 UNIT RENT", "-400.00", None, "750.00"),
    ("09 Mar 2026", "ATM WITHDRAWAL REF", "TERMINAL", "-125.50", None, "1,150.00"),
)
OPENING = Decimal("1275.50")
CLOSING = Decimal("1500.00")


def account_profile(**changes: object) -> LayoutCreate:
    """The seeded profile, with whatever a test needs changed about it."""
    values = json.loads(SEED.read_text())
    values.update(changes)
    return LayoutCreate.model_validate(values)


def _row_placements(page: int, y: float, row: Row) -> list[Placement]:
    """One transaction: its cells on one baseline, its narration continued on the next.

    The cells are separate placements with no space between them, which is what makes the
    extracted raw text run together exactly as the producer's does.
    """
    booked, head, tail, debit, credit, balance = row
    placements = [
        Placement(page=page, x=X_DATE, y=y, text=booked),
        Placement(page=page, x=X_TEXT, y=y, text=head),
        Placement(page=page, x=X_BALANCE, y=y, text=balance),
        Placement(page=page, x=X_MARK, y=y, text="Cr"),
        # The continuation: narration only, on its own baseline, in the description band.
        Placement(page=page, x=X_TEXT, y=y - 9.2, text=tail),
    ]
    if debit is not None:
        placements.append(Placement(page=page, x=X_DEBIT, y=y, text=debit))
    if credit is not None:
        placements.append(Placement(page=page, x=X_CREDIT, y=y, text=credit))
    return placements


def _header(page: int, y: float) -> list[Placement]:
    return [
        Placement(page=page, x=52.0, y=y, text="Date"),
        Placement(page=page, x=167.0, y=y, text="Description"),
        Placement(page=page, x=306.0, y=y, text="Debits"),
        Placement(page=page, x=390.0, y=y, text="Credits"),
        Placement(page=page, x=502.0, y=y, text="Balance"),
    ]


def _footer(page: int, number: str) -> list[Placement]:
    return [
        Placement(page=page, x=489.0, y=99.0, text=number),
        Placement(
            page=page,
            x=20.0,
            y=42.0,
            text="This is an electronically generated statement, hence does not require a",
        ),
        # The legend's second line sits in the date band, so it is dropped without a pattern.
        Placement(page=page, x=20.0, y=33.0, text="signature"),
    ]


def _placements() -> list[Placement]:
    """Two pages: the header block, the period line, a repeated column header and four rows."""
    return [
        Placement(page=0, x=85.0, y=668.0, text="Account Statement"),
        Placement(page=0, x=291.0, y=515.0, text="Account Type"),
        Placement(page=0, x=409.0, y=515.0, text="CURRENT ACCOUNT"),
        # The period, written as four cells with no space between them.
        Placement(page=0, x=58.0, y=479.0, text="STATEMENT OF ACCOUNT FOR THE PERIOD OF"),
        Placement(page=0, x=289.0, y=479.0, text="01 Mar 2026"),
        Placement(page=0, x=390.0, y=479.0, text="to"),
        Placement(page=0, x=491.0, y=479.0, text="31 Mar 2026"),
        *_header(0, 439.0),
        *_row_placements(0, 421.0, ROWS[0]),
        *_row_placements(0, 383.0, ROWS[1]),
        *_row_placements(0, 345.0, ROWS[2]),
        *_footer(0, "1 / 2"),
        # Page two repeats the column header above its first row.
        *_header(1, 652.0),
        *_row_placements(1, 634.0, ROWS[3]),
        *_footer(1, "2 / 2"),
    ]


def statement_bytes() -> bytes:
    return build_pdf(_placements())


@pytest.fixture
def statement(tmp_path: Path) -> Path:
    path = tmp_path / "account-statement.pdf"
    path.write_bytes(statement_bytes())
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
    """The golden output: four rows, their figures, the period, and the verdict."""
    result, batch, verdict = parsed(statement, account_profile())

    assert batch.failures == []
    assert [
        (
            row.source_page_number,
            row.source_row_number,
            str(row.booked_date),
            row.description_raw,
            str(row.amount),
            str(row.running_balance),
            row.balance_direction_is_stated,
            row.proposed_txn_type,
        )
        for row in batch.rows
    ] == [
        (
            1,
            1,
            "2026-03-12",
            "CARD PURCHASE REF GROCERY",
            "-250.0000",
            "1500.0000",
            True,
            "expense",
        ),
        (
            1,
            2,
            "2026-03-11",
            "INWARD TRANSFER REF SALARY",
            "1000.0000",
            "1750.0000",
            True,
            "income",
        ),
        (
            1,
            3,
            "2026-03-10",
            "STANDING ORDER REF 300.00 UNIT RENT",
            "-400.0000",
            "750.0000",
            True,
            "expense",
        ),
        (
            2,
            4,
            "2026-03-09",
            "ATM WITHDRAWAL REF TERMINAL",
            "-125.5000",
            "1150.0000",
            True,
            "expense",
        ),
    ]

    # The statement states its own period, so nothing is derived from the row dates.
    assert (batch.period.start, batch.period.end, batch.period.is_derived) == (
        __import__("datetime").date(2026, 3, 1),
        __import__("datetime").date(2026, 3, 31),
        False,
    )

    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_closing == CLOSING
    # No line of this statement states an opening; it is the oldest row's balance less that
    # row's own amount, and it must be labelled derived (Invariant I7).
    assert (verdict.declared_opening, verdict.opening_is_derived) == (OPENING, True)
    assert verdict.delta == Decimal("0.0000")


def test_narration_wraps_into_one_row_per_transaction(statement: Path) -> None:
    """Four transactions written across eight physical lines stay four rows."""
    result, batch, _ = parsed(statement, account_profile())

    assert len(batch.rows) == 4
    # Two physical lines behind each row, kept verbatim for the diagnostics panel.
    assert [row.raw_text.count("\n") + 1 for row in result.rows] == [2, 2, 2, 2]
    # Each row's narration carries both of its physical lines, joined by a single space.
    assert [row.description_raw.count(" ") for row in batch.rows] == [3, 3, 5, 3]

    without = account_profile(continuationRule=None)
    lost, dropped, _ = parsed(statement, without)
    # The same four rows, but every second physical line is now lost — and counted rather than
    # silently discarded (Invariant I7), which is the whole difference the rule makes here.
    assert len(dropped.rows) == 4
    assert lost.lines_unparsed == result.lines_unparsed + 4
    assert [row.description_raw for row in dropped.rows] == [
        "CARD PURCHASE REF",
        "INWARD TRANSFER REF",
        "STANDING ORDER REF",
        "ATM WITHDRAWAL REF",
    ]


def test_a_money_shaped_word_in_the_narration_does_not_end_a_continuation(
    statement: Path,
) -> None:
    """Row three's second line opens with a figure, and it is still narration.

    `no_date_and_no_amount` asks whether the *amount band* holds a number, not whether the line
    contains one anywhere. A line whose narration happens to start with a figure is the ordinary
    case on this producer, and reading it as an amount would strand the rest of the row.
    """
    _, batch, verdict = parsed(statement, account_profile())

    assert batch.rows[2].description_raw == "STANDING ORDER REF 300.00 UNIT RENT"
    assert batch.rows[2].amount == Decimal("-400.00")
    assert verdict.status == STATUS_MATCHED


def test_the_repeated_header_and_the_footer_never_join_a_transaction(statement: Path) -> None:
    """Both are skipped by pattern; without them they would extend the row above."""
    _, batch, _ = parsed(statement, account_profile())

    assert not any("Description" in row.description_raw for row in batch.rows)
    assert not any("generated statement" in row.description_raw for row in batch.rows)

    unskipped = account_profile(skipLinePatterns=[])
    _, absorbed, _ = parsed(statement, unskipped)
    # The last row of each page takes the footer legend, and the last row of page one takes
    # page two's column header as well. Each is a corrupted narration on a batch that still
    # reconciles, which is why the patterns are not cosmetic.
    # Only the words that fall in the description band are appended: "electronically" sits in
    # the date band and never arrives, which is why the pattern is matched against the whole
    # line rather than against what a row would have absorbed.
    assert [row.description_raw for row in absorbed.rows][2] == (
        "STANDING ORDER REF 300.00 UNIT RENT generated statement, hence does not Description"
    )


def test_a_dated_line_this_layout_cannot_price_blocks_the_batch(tmp_path: Path) -> None:
    """Why the rule is `has_date`, not `has_date_and_amount`.

    No line of the twenty-one statements measured carries a date without an amount, so the two
    rules read them identically. They differ on the line neither has met: `has_date` makes it a
    row that fails to normalize and blocks the batch, while `has_date_and_amount` cannot start a
    row with it and cannot continue one either — it has a date — so the line is dropped into
    `lines_unparsed` and the remaining rows reconcile without it. A batch missing a row
    reconciles only by coincidence, so the rule that stops is the one this layout takes.
    """
    stray = Placement(page=0, x=X_DATE, y=364.0, text="10 Mar 2026")
    path = tmp_path / "dated-without-amount.pdf"
    path.write_bytes(build_pdf([*_placements(), stray]))

    _, batch, _ = parsed(path, account_profile())
    assert [failure.reason for failure in batch.failures] == ["the row states no amount"]

    _, ignored, _ = parsed(path, account_profile(rowStartRule="has_date_and_amount"))
    assert ignored.failures == []
    assert len(ignored.rows) == 4


def test_read_in_the_printed_order_the_balance_walk_does_not_close(statement: Path) -> None:
    """`descending` is load-bearing: the same rows read top-down reconcile against nothing."""
    _, _, verdict = parsed(statement, account_profile(chronologicalOrder="ascending"))

    assert verdict.status != STATUS_MATCHED
    assert verdict.first_divergent_row is not None


def test_the_periods_patterns_survive_a_producer_that_writes_no_spaces(statement: Path) -> None:
    """The captured period is the evidence: `\\s+` between the cells would capture nothing."""
    pages = read_pdf(statement)
    joined = "\n".join(
        line.raw_text if line.raw_text is not None else " ".join(w.text for w in line.words)
        for page in pages
        for line in page.lines
    )
    # The producer's own shape: no space survives between two cells of one line.
    assert "PERIOD OF01 Mar 2026to31 Mar 2026" in joined
    assert "DateDescriptionDebitsCreditsBalance" in joined

    spaced = account_profile(
        metadataPatterns={
            "period_start": r"PERIOD\s+OF\s+(\d{2} \w{3} \d{4})\s+to",
            "period_end": r"PERIOD\s+OF\s+\d{2} \w{3} \d{4}\s+to\s+(\d{2} \w{3} \d{4})",
        }
    )
    result = extract_pages(spaced, pages)
    with pytest.raises(NormalizationError) as refusal:
        normalize(spaced, result, account_currency=CURRENCY)
    assert str(refusal.value) == (
        "the layout captures the statement period but this statement did not yield it"
    )
