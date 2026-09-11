"""Slice 1.2: the encrypted layout, whose year-end rows are booked after its period closes.

Four things about this producer are measured rather than assumed:

1. Its statements are **password-protected**, and the password reaches the parse from the
   owner and nowhere else — never from configuration, never to disk.
2. It writes **no space anywhere**, inside labels as well as between cells, so every pattern
   is written against the run-together form.
3. Its rows carry a **two-digit year**, which obliges the profile to capture the statement
   period (spec §4.4) — and its year-end interest and tax are then booked on 1 January, one
   day *after* the period it declares, and valued 31 December inside it.
4. Its page furniture is set as single run-together words centred in the narration column, so
   the geometric continuation rule cannot reach it and `skip_line_patterns` must.

**Every figure below is invented**, and so is the password. They reproduce the arithmetic of a
bank ledger — a walk through withdrawal and deposit columns against a closing balance, and a
year-end pair the statement books late and values inside — and none of them is real. Real
statements are git-ignored and never enter this repository.

The profile under test is `db/seeds/ledger-layout.json` itself, so the seed cannot drift away
from the behaviour proved here.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from oneledger.ingest.engine import ExtractionResult, extract_pages, matches
from oneledger.ingest.normalize import NormalizedBatch, normalize
from oneledger.ingest.pipeline import IDENTIFICATION_FLOOR
from oneledger.ingest.reconcile import STATUS_MATCHED, Verdict, reconcile
from oneledger.ingest.words import ExtractionError
from oneledger.ingest.words import extract_pages as read_pdf
from oneledger.routes.layouts import LayoutCreate
from tests.fixtures.synthetic_pdf import Placement, build_encrypted_pdf

SEED = Path(__file__).resolve().parents[2] / "db" / "seeds" / "ledger-layout.json"
CURRENCY = "INR"
# Invented, and the only place it exists. The application asks the owner for the real one and
# holds it for the length of a request (task 2a); nothing reads it from configuration.
PASSWORD = "not-a-real-password"
# The producer's own body size. Wider, and the date runs into the narration and the
# reference into the value date, which would test the fixture rather than the layout.
SIZE = 7.0

X_DATE, X_TEXT, X_REFERENCE, X_VALUE_DATE = 34.0, 68.0, 293.0, 362.0
X_DEBIT, X_CREDIT, X_BALANCE = 440.0, 518.0, 587.0

# booked, narration, its wrapped second line, reference, value date, withdrawal, deposit, balance
ROWS = (
    (
        "04/03/25",
        "UPIPAYMENT",
        "REF-GROCER",
        "000123456789012",
        "04/03/25",
        "820.00",
        None,
        "9180.00",
    ),
    (
        "18/03/25",
        "SALARYCREDIT",
        "REF-EMPLOYER",
        "000123456789013",
        "18/03/25",
        None,
        "4400.00",
        "13580.00",
    ),
    # Booked the day after the period closes and valued inside it: the year-end pair.
    (
        "01/04/25",
        "INTERESTCREDITED",
        "REF-QUARTER",
        "000123456789014",
        "31/03/25",
        None,
        "62.50",
        "13642.50",
    ),
    (
        "01/04/25",
        "TAXDEDUCTED",
        "REF-QUARTER",
        "000123456789015",
        "31/03/25",
        "6.25",
        None,
        "13636.25",
    ),
)
OPENING = Decimal("10000.00")
CLOSING = Decimal("13636.25")


def ledger_profile(**changes: object) -> LayoutCreate:
    """The seeded profile, with whatever a test needs changed about it."""
    values = json.loads(SEED.read_text())
    values.update(changes)
    return LayoutCreate.model_validate(values)


def _row(y: float, row: tuple[str | None, ...]) -> list[Placement]:
    booked, head, tail, reference, valued, debit, credit, balance = row
    placements = [
        Placement(page=0, x=X_DATE, y=y, text=str(booked), size=SIZE),
        Placement(page=0, x=X_TEXT, y=y, text=str(head), size=SIZE),
        Placement(page=0, x=X_REFERENCE, y=y, text=str(reference), size=SIZE),
        Placement(page=0, x=X_VALUE_DATE, y=y, text=str(valued), size=SIZE),
        Placement(page=0, x=X_BALANCE, y=y, text=str(balance), size=SIZE),
        # The wrapped narration: the description column alone.
        Placement(page=0, x=X_TEXT, y=y - 17.0, text=str(tail), size=SIZE),
    ]
    if debit is not None:
        placements.append(Placement(page=0, x=X_DEBIT, y=y, text=debit, size=SIZE))
    if credit is not None:
        placements.append(Placement(page=0, x=X_CREDIT, y=y, text=credit, size=SIZE))
    return placements


def _placements() -> list[Placement]:
    header = [
        Placement(page=0, x=28.0, y=810.0, text="HDFC", size=SIZE),
        Placement(page=0, x=292.0, y=790.0, text="PageNo.:1", size=SIZE),
        Placement(page=0, x=514.0, y=790.0, text="Statementofaccount", size=SIZE),
        # The period, as separate cells with no space character between them — which is what
        # makes the extracted text read `StatementFrom:01/01/2025To:31/03/2025`.
        Placement(page=0, x=340.0, y=700.0, text="StatementFrom", size=SIZE),
        Placement(page=0, x=397.0, y=700.0, text=":", size=SIZE),
        Placement(page=0, x=403.0, y=700.0, text="01/01/2025", size=SIZE),
        Placement(page=0, x=450.0, y=700.0, text="To", size=SIZE),
        Placement(page=0, x=463.0, y=700.0, text=":", size=SIZE),
        Placement(page=0, x=469.0, y=700.0, text="31/03/2025", size=SIZE),
        Placement(page=0, x=40.0, y=660.0, text="Date", size=SIZE),
        Placement(page=0, x=144.0, y=660.0, text="Narration", size=SIZE),
        Placement(page=0, x=284.0, y=660.0, text="Chq./Ref.No.", size=SIZE),
        Placement(page=0, x=362.0, y=660.0, text="ValueDt", size=SIZE),
        Placement(page=0, x=405.0, y=660.0, text="WithdrawalAmt.", size=SIZE),
        Placement(page=0, x=491.0, y=660.0, text="DepositAmt.", size=SIZE),
        Placement(page=0, x=564.0, y=660.0, text="ClosingBalance", size=SIZE),
    ]
    # The closing furniture: single run-together words centred in the narration column, which
    # is why no band and no geometric rule reaches them.
    footer = [
        Placement(page=0, x=68.0, y=180.0, text="STATEMENTSUMMARY", size=SIZE),
        Placement(page=0, x=28.0, y=160.0, text="ANOTHERBANKLIMITED", size=SIZE),
        Placement(
            page=0,
            x=28.0,
            y=145.0,
            text="*Closingbalanceincludesfundsearmarkedforholdandunclearedfunds",
            size=SIZE,
        ),
        Placement(page=0, x=28.0, y=130.0, text="RegisteredOfficeAddress:somewhere", size=SIZE),
    ]
    # What the producer repeats at the top of every page after the first: an address line
    # inside the narration column, and a detail line that runs across the money columns.
    repeated = [
        Placement(page=0, x=28.0, y=380.0, text="ALOCALITYNAME", size=SIZE),
        Placement(page=0, x=340.0, y=365.0, text="A/COpenDate", size=SIZE),
        Placement(page=0, x=403.0, y=365.0, text="somedate", size=SIZE),
        Placement(page=0, x=68.0, y=365.0, text="ADETAILLINE", size=SIZE),
    ]
    return [
        *header,
        *_row(620.0, ROWS[0]),
        *_row(560.0, ROWS[1]),
        *_row(500.0, ROWS[2]),
        *_row(440.0, ROWS[3]),
        *repeated,
        *footer,
    ]


@pytest.fixture
def statement(tmp_path: Path) -> Path:
    path = tmp_path / "ledger-statement.pdf"
    path.write_bytes(build_encrypted_pdf(_placements(), PASSWORD, page_size=(638.0, 842.0)))
    return path


def parsed(
    statement: Path, profile: LayoutCreate, password: str | None = PASSWORD
) -> tuple[ExtractionResult, NormalizedBatch, Verdict]:
    result = extract_pages(profile, read_pdf(statement, password=password))
    batch = normalize(profile, result, account_currency=CURRENCY)
    verdict = reconcile(
        profile,
        batch.rows,
        declared_opening=result.declared_opening,
        declared_closing=result.declared_closing,
    )
    return result, batch, verdict


def test_it_parses_with_its_password_and_is_refused_without_one(statement: Path) -> None:
    """The whole layout is unreadable until the owner supplies the password."""
    _, batch, _ = parsed(statement, ledger_profile())
    assert len(batch.rows) == 4

    with pytest.raises(ExtractionError) as refused:
        read_pdf(statement, password=None)
    assert str(refused.value) == "PDF is encrypted or the password is incorrect"

    # A wrong password is refused the same way, and says nothing more.
    with pytest.raises(ExtractionError) as wrong:
        read_pdf(statement, password="wrong")
    assert str(wrong.value) == str(refused.value)


def test_the_seeded_profile_reads_this_statement_exactly(statement: Path) -> None:
    """The golden output: four rows, their figures and references, the period, the verdict."""
    result, batch, verdict = parsed(statement, ledger_profile())

    assert batch.failures == []
    assert result.institution_name_raw == "HDFC"
    assert "institution_name" not in result.metadata
    assert [
        (
            row.source_row_number,
            str(row.booked_date),
            str(row.value_date),
            row.reference_raw,
            row.description_raw,
            str(row.amount),
            str(row.running_balance),
        )
        for row in batch.rows
    ] == [
        (
            1,
            "2025-03-04",
            "2025-03-04",
            "000123456789012",
            "UPIPAYMENT REF-GROCER",
            "-820.0000",
            "9180.0000",
        ),
        (
            2,
            "2025-03-18",
            "2025-03-18",
            "000123456789013",
            "SALARYCREDIT REF-EMPLOYER",
            "4400.0000",
            "13580.0000",
        ),
        (
            3,
            "2025-04-01",
            "2025-03-31",
            "000123456789014",
            "INTERESTCREDITED REF-QUARTER",
            "62.5000",
            "13642.5000",
        ),
        (
            4,
            "2025-04-01",
            "2025-03-31",
            "000123456789015",
            "TAXDEDUCTED REF-QUARTER",
            "-6.2500",
            "13636.2500",
        ),
    ]

    assert (batch.period.start, batch.period.end, batch.period.is_derived) == (
        date(2025, 1, 1),
        date(2025, 3, 31),
        False,
    )
    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_closing == CLOSING
    # No line states an opening, so it is the first row's balance less its own amount.
    assert (verdict.declared_opening, verdict.opening_is_derived) == (OPENING, True)


def test_the_year_end_rows_booked_after_the_period_are_kept(statement: Path) -> None:
    """M2 spec §7 item 14, and the reason this layout can be imported at all.

    Rows three and four are booked on 1 April — one day after the period the statement
    declares — and valued 31 March inside it. They carry money and the statement's own running
    balance carries them. Gating on the booked date alone drops both from the *end* of the
    statement, where the rows before them still walk consistently, so the batch reports itself
    reconciled while two money-bearing rows are missing.
    """
    _, batch, verdict = parsed(statement, ledger_profile())

    late = [row for row in batch.rows if row.booked_date > batch.period.end]
    assert [str(row.value_date) for row in late] == ["2025-03-31", "2025-03-31"]
    assert sum(row.amount for row in late) == Decimal("56.2500")
    assert verdict.status == STATUS_MATCHED
    assert verdict.declared_closing == CLOSING


def test_the_page_furniture_is_skipped_by_pattern_and_not_by_geometry(statement: Path) -> None:
    """Its closing block is set as single run-together words inside the narration column.

    They leave the date and money columns empty, so `blank_date_and_money_columns` reads them
    as wrapped narration exactly as it should — the geometry says they are. Only a pattern can
    say otherwise, which is what `skip_line_patterns` is for.
    """
    _, batch, _ = parsed(statement, ledger_profile())
    assert batch.rows[-1].description_raw == "TAXDEDUCTED REF-QUARTER"

    unskipped = ledger_profile(skipLinePatterns=[])
    _, absorbed, verdict = parsed(statement, unskipped)
    assert absorbed.rows[-1].description_raw == (
        "TAXDEDUCTED REF-QUARTER STATEMENTSUMMARY ANOTHERBANKLIMITED "
        "*Closingbalanceincludesfundsearmarkedforholdandunclearedfunds "
        "RegisteredOfficeAddress:somewhere"
    )
    # And the statement still reconciles, which is why the patterns are not cosmetic.
    assert verdict.status == STATUS_MATCHED


def test_a_repeated_header_line_across_the_money_columns_is_not_narration(
    statement: Path,
) -> None:
    """Why this layout takes the stricter continuation rule as well as its skip patterns.

    Skip patterns catch the closing block, which is named text. They cannot catch the header
    block the producer repeats at the top of every page: an account-detail line running across
    the money columns, which `no_date_and_no_amount` reads as wrapped narration. Measured on the
    six statements, the looser rule takes the longest narration from 120 characters to 240.
    """
    _, batch, _ = parsed(statement, ledger_profile())
    assert "ADETAILLINE" not in batch.rows[-1].description_raw

    _, absorbed, _ = parsed(statement, ledger_profile(continuationRule="no_date_and_no_amount"))
    assert "ADETAILLINE" in absorbed.rows[-1].description_raw


def test_a_dated_line_this_layout_cannot_price_blocks_the_batch(tmp_path: Path) -> None:
    """Why the rule is `has_date`, not `has_date_and_amount`.

    `has_date` makes a dated line with no withdrawal and no deposit a row that fails to
    normalize, which blocks the batch. `has_date_and_amount` cannot start a row with it and
    cannot continue one either — it has a date — so it vanishes into `lines_unparsed` and the
    rest reconcile without it.
    """
    stray = Placement(page=0, x=X_DATE, y=410.0, text="20/03/25", size=SIZE)
    path = tmp_path / "dated-without-amount.pdf"
    path.write_bytes(
        build_encrypted_pdf([*_placements(), stray], PASSWORD, page_size=(638.0, 842.0))
    )

    _, batch, _ = parsed(path, ledger_profile())
    assert [failure.reason for failure in batch.failures] == [
        "the row states neither a debit nor a credit"
    ]

    _, ignored, _ = parsed(path, ledger_profile(rowStartRule="has_date_and_amount"))
    assert ignored.failures == []
    assert len(ignored.rows) == 4


def test_this_statement_is_identified_as_this_layout_and_no_other(statement: Path) -> None:
    """The fingerprints are what choose the profile, so they are scored here too.

    A layout is identified on page-one text alone (`pipeline.identify`), and anything under
    half its fingerprints is not evidence. Every other seeded profile must stay under that
    floor on this statement, or a real upload becomes a tie the owner has to break by hand.
    """
    pages = read_pdf(statement, password=PASSWORD)
    page_one = "\n".join(
        line.raw_text if line.raw_text is not None else " ".join(w.text for w in line.words)
        for line in pages[0].lines
    )

    assert matches(ledger_profile(), page_one, "") == 1.0

    seeds = sorted(SEED.parent.glob("*.json"))
    others = [
        LayoutCreate.model_validate(json.loads(path.read_text())) for path in seeds if path != SEED
    ]
    assert others, "the other seeded profiles are what this is guarding against"
    assert all(matches(other, page_one, "") < IDENTIFICATION_FLOOR for other in others)


def test_the_period_capture_survives_a_producer_that_writes_no_spaces(statement: Path) -> None:
    """A two-digit year obliges the capture, so a pattern that misses refuses the statement."""
    pages = read_pdf(statement, password=PASSWORD)
    joined = "\n".join(
        line.raw_text if line.raw_text is not None else " ".join(w.text for w in line.words)
        for page in pages
        for line in page.lines
    )
    assert "StatementFrom:01/01/2025To:31/03/2025" in joined
    assert "DateNarrationChq./Ref.No.ValueDtWithdrawalAmt.DepositAmt.ClosingBalance" in joined
