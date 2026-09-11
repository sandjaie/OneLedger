"""Slice 1.1a: the pipeline's two decisions that need no database.

`identify` chooses the layout a file was written against, or declines and hands the picker its
evidence. `analyse` runs extraction, normalization, dedupe and reconciliation over one profile
and one set of pages, in that order, and reports what came out. Both are pure functions over
values, which is what `testing-and-data-quality.html` §2 requires of everything under `ingest/`
except the staging transaction itself — that one needs a database and is proved in
`test_ingest_pipeline_live.py`.

Every figure below is invented. Real statements are git-ignored and never enter this repository.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from oneledger.ingest import pipeline
from oneledger.ingest.dedupe import STATUS_DUPLICATE, STATUS_NEW, CommittedRow
from oneledger.ingest.reconcile import STATUS_MATCHED, STATUS_MISMATCHED
from oneledger.ingest.words import Line, Page, Word
from oneledger.routes.layouts import LayoutProfile

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")

# Opening 1,000.00, two rows summing to +490.00, closing 1,490.00. The identity closes, so the
# fixture's default verdict is `matched` and a test that wants a different one perturbs it.
OPENING = "1,000.00"
CLOSING = "1,490.00"


def word(x0: float, x1: float, text: str, top: float) -> Word:
    return Word(x0=x0, x1=x1, top=top, bottom=top + 9.0, text=text)


def line(top: float, *words: Word) -> Line:
    """A line carrying the raw text the producer emits: its characters joined with nothing.

    `engine._page_text` prefers `raw_text` over the words, so the fingerprints and the balance
    anchors are matched against the string production actually sees.
    """
    return Line(top=top, words=list(words), raw_text="".join(w.text for w in words))


def spaced(top: float, text: str) -> Line:
    """A furniture line whose raw text is the text itself — a heading or an anchor."""
    return Line(top=top, words=[word(30.0, 300.0, text, top)], raw_text=text)


def transaction(top: float, day: str, description: str, amount: str, balance: str) -> Line:
    return line(
        top,
        word(40.0, 110.0, day, top),
        word(130.0, 380.0, description, top),
        word(410.0, 470.0, amount, top),
        word(490.0, 550.0, balance, top),
    )


def profile(**changes: object) -> LayoutProfile:
    """A running-balance layout with labelled anchors, saved — so it carries id and version."""
    values: dict[str, object] = {
        "id": UUID("22222222-2222-2222-2222-222222222222"),
        "name": "Acme current account",
        "version": 3,
        "institutionId": None,
        "accountType": "current",
        "fingerprints": ["Acme Bank Statement"],
        "headerRowPattern": r"Date\s*Description\s*Amount\s*Balance",
        "columns": [
            {"field": "booked_date", "xMin": 30.0, "xMax": 120.0, "label": "Date"},
            {"field": "description", "xMin": 120.0, "xMax": 400.0, "label": "Description"},
            {"field": "amount", "xMin": 400.0, "xMax": 480.0, "label": "Amount"},
            {"field": "running_balance", "xMin": 480.0, "xMax": 570.0, "label": "Balance"},
        ],
        "dateFormat": "dd/MM/yyyy",
        "signConvention": "signed",
        "invertsSign": False,
        "chronologicalOrder": "ascending",
        "rowStartRule": "has_date_and_amount",
        "continuationRule": None,
        "skipLinePatterns": [],
        "openingBalancePattern": r"Opening Balance\s+([0-9,.-]+)",
        "closingBalancePattern": r"Closing Balance\s+([0-9,.-]+)",
        "hasRunningBalance": True,
        "quirks": [],
        "metadataPatterns": None,
    }
    values.update(changes)
    return LayoutProfile.model_validate(values)


def statement(closing: str = CLOSING, opening: str = OPENING) -> list[Page]:
    return [
        Page(
            lines=[
                spaced(20.0, "Acme Bank Statement"),
                spaced(30.0, "Account Number 1234567890"),
                spaced(40.0, f"Opening Balance {opening}"),
                transaction(100.0, "01/03/2026", "COFFEE SHOP", "-10.00", "990.00"),
                transaction(120.0, "02/03/2026", "SALARY MARCH", "500.00", "1,490.00"),
                spaced(160.0, f"Closing Balance {closing}"),
            ],
            has_text=True,
        )
    ]


def test_an_unknown_captured_account_returns_analysis_without_staging() -> None:
    result = pipeline.parse(
        cast(Any, _ReadOnlyConnection()),
        pages=statement(),
        data=b"synthetic-statement",
        original_filename="statement.pdf",
        household=pipeline.HouseholdContext(
            id=UUID("33333333-3333-3333-3333-333333333333"),
            base_currency="AED",
            secondary_currency="INR",
        ),
        profiles=[profile(metadataPatterns={"account_number": r"Account Number ([0-9]+)"})],
        account=None,
        resolve_account=lambda _extraction, _profile: None,
        stage=True,
    )

    assert result.account is None
    assert result.staged is False
    assert result.analysis.rows_extracted == 2


class _ReadOnlyCursor:
    def __enter__(self) -> _ReadOnlyCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def execute(self, _sql: str, _params: object = None) -> None:
        pass

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def fetchone(self) -> tuple[object, ...] | None:
        return None


class _ReadOnlyConnection:
    def cursor(self) -> _ReadOnlyCursor:
        return _ReadOnlyCursor()

    def rollback(self) -> None:
        pass


def analyse(pages: list[Page] | None = None, **changes: object) -> pipeline.Analysis:
    return pipeline.analyse(
        profile(**changes),
        pages if pages is not None else statement(),
        account_id=ACCOUNT,
        account_currency="AED",
        committed=[],
    )


# --------------------------------------------------------------------------------------
# identify — text fingerprint, then ask (statement-ingestion.html §1)
# --------------------------------------------------------------------------------------


def test_the_layout_whose_fingerprint_appears_is_the_one_chosen() -> None:
    other = profile(id=uuid4(), name="Other bank", fingerprints=["Nothing In This Document"])
    identification = pipeline.identify([other, profile()], statement(), "statement.pdf")

    assert identification.chosen is not None
    assert identification.chosen.name == "Acme current account"
    # Every layout is scored and returned, best first: the picker renders this list when the
    # choice is declined, and a caller must never have to re-score to show it.
    assert [candidate.score for candidate in identification.candidates] == [1.0, 0.0]


def test_two_layouts_fingerprinting_equally_well_are_not_identified() -> None:
    """A tie is the picker's case, not the best-effort case.

    Picking either would be a coin toss whose losing side imports a statement under the wrong
    column bands — and every row would still parse, because the bands are geometry rather than
    meaning. Declining costs one click.
    """
    twin = profile(id=uuid4(), name="Acme savings account")
    identification = pipeline.identify([profile(), twin], statement(), "statement.pdf")

    assert identification.chosen is None
    assert {candidate.profile.name for candidate in identification.candidates} == {
        "Acme current account",
        "Acme savings account",
    }


def test_a_layout_nothing_matched_is_not_identified() -> None:
    unrelated = profile(fingerprints=["Some Other Institution"])
    identification = pipeline.identify([unrelated], statement(), "statement.pdf")

    assert identification.chosen is None
    assert identification.candidates[0].score == 0.0


def test_no_saved_layouts_is_not_identified_and_carries_no_candidates() -> None:
    identification = pipeline.identify([], statement(), "statement.pdf")

    assert identification.chosen is None
    assert identification.candidates == []


def test_the_filename_is_never_evidence_on_its_own() -> None:
    """`engine.matches` discards the filename deliberately; identification must not reinstate it.

    A statement's filename is owner-chosen and frequently carries a card number
    (`security-and-privacy.html` §1). Scoring on it would both mis-identify and read a value
    this pipeline works to keep out of everything it touches.
    """
    unrelated = profile(fingerprints=["Some Other Institution"])
    named = pipeline.identify([unrelated], statement(), "Acme Bank Statement march.pdf")
    anonymous = pipeline.identify([unrelated], statement(), "download.pdf")

    assert named.candidates[0].score == anonymous.candidates[0].score == 0.0
    assert named.chosen is anonymous.chosen is None


# --------------------------------------------------------------------------------------
# analyse — extraction, normalization, dedupe and reconciliation over one profile
# --------------------------------------------------------------------------------------


def test_a_clean_statement_normalizes_every_row_and_reconciles() -> None:
    result = analyse()

    assert [row.amount for row in result.rows] == [Decimal("-10.0000"), Decimal("500.0000")]
    assert [row.booked_date for row in result.rows] == [date(2026, 3, 1), date(2026, 3, 2)]
    assert result.failures == []
    assert result.reconciliation.status == STATUS_MATCHED
    assert result.reconciliation.declared_closing == Decimal("1490.0000")


def test_every_row_carries_a_dedupe_verdict_in_row_order() -> None:
    result = analyse()

    assert len(result.verdicts) == len(result.rows)
    assert {verdict.status for verdict in result.verdicts} == {STATUS_NEW}
    assert len({verdict.dedupe_hash for verdict in result.verdicts}) == 2


def test_a_row_already_committed_is_staged_as_a_duplicate_and_counted_as_one() -> None:
    first = analyse()
    already = CommittedRow(
        id=uuid4(),
        account_id=ACCOUNT,
        dedupe_hash=first.verdicts[0].dedupe_hash,
        booked_date=first.rows[0].booked_date,
        amount=first.rows[0].amount,
        description_raw=first.rows[0].description_raw,
    )

    result = pipeline.analyse(
        profile(),
        statement(),
        account_id=ACCOUNT,
        account_currency="AED",
        committed=[already],
    )

    assert result.verdicts[0].status == STATUS_DUPLICATE
    assert result.counts == {STATUS_DUPLICATE: 1, STATUS_NEW: 1}


def test_a_statement_whose_closing_disagrees_reconciles_mismatched() -> None:
    """The rows still come back: the diagnostics panel renders them beside the verdict.

    Reporting the verdict and withholding the evidence would leave the owner with a refusal and
    no way to see which row is wrong — which is the whole job of the panel §4.6 describes.
    """
    result = analyse(statement(closing="1,500.00"))

    assert result.reconciliation.status == STATUS_MISMATCHED
    assert result.reconciliation.delta == Decimal("10.0000")
    assert len(result.rows) == 2


def test_counts_are_taken_over_the_dedupe_verdicts_not_the_extracted_lines() -> None:
    """`rows_extracted` and the per-status counts answer different questions.

    A line that failed to normalize is extracted and has no verdict, so counting lines would
    overstate what is about to be staged — and `import_batch`'s row counts are what the review
    screen reports back to the owner.
    """
    # An amount past the ledger's own limit: it is a number, so the line starts a row and is
    # extracted, and normalization is where it is refused. A malformed date or amount would not
    # prove the same thing — neither starts a row, so neither ever reaches normalization.
    broken = statement()
    broken[0].lines.insert(
        3, transaction(130.0, "03/03/2026", "OVERFLOWS THE LEDGER", "99,999,999,999,999,999.00", "")
    )

    result = analyse(broken)

    assert result.failures != []
    assert sum(result.counts.values()) == len(result.verdicts) == len(result.rows)
    assert result.rows_extracted == len(result.rows) + len(result.failures)


def test_the_statement_period_is_reported_and_says_whether_it_was_derived() -> None:
    """`import_batch.period_start/period_end` have no companion flag, so the caller must be
    told — Invariant I7's rule that a computed number never arrives as a stated one."""
    result = analyse()

    assert result.period.start == date(2026, 3, 1)
    assert result.period.end == date(2026, 3, 2)
    assert result.period.is_derived is True


def test_a_layout_whose_declared_anchor_does_not_extract_blocks_rather_than_reconciles() -> None:
    """§4.6: a configured anchor that produced nothing is a remapping job, never "no balances".

    Letting it through would reconcile vacuously and commit rows that bypassed the check
    entirely, which is exactly what Invariant I8 exists to prevent.
    """
    result = analyse(statement(closing="not a number"))

    assert result.reconciliation.status == "unavailable"


def test_analysis_reports_the_profile_it_ran_so_the_batch_can_record_the_version() -> None:
    """`import_batch.statement_layout_id` and `layout_version` pin which mapping produced a row
    (Invariant I6). Re-reading them off the caller's variable would let the two drift."""
    result = analyse()

    assert result.layout_id == UUID("22222222-2222-2222-2222-222222222222")
    assert result.layout_version == 3


def test_a_scanned_page_is_refused_rather_than_reconciled_as_an_empty_statement() -> None:
    """Zero rows and no anchors would otherwise reconcile 0 == 0 and stage nothing, silently.

    The owner needs to know the file was an image, not that their statement was empty.
    """
    with pytest.raises(pipeline.PipelineError, match="scanned"):
        analyse([Page(lines=[], has_text=False)])


def test_a_reference_column_changes_every_hash_in_the_batch() -> None:
    """`_maps_reference` chooses the disambiguator namespace for every fingerprint in a batch.

    With no `reference` column the index is `occ:<n>`; with one it is `ref:<reference>#<seen>`
    (spec §7 item 12). The fixture profile maps no reference and its rows carry none, so both
    constants — `True` and `False` — produce identical hashes and neither mutation could fail.
    A profile that actually captures a reference is what makes the derivation observable.
    """
    referenced = profile(
        columns=[
            {"field": "booked_date", "xMin": 30.0, "xMax": 120.0, "label": "Date"},
            {"field": "description", "xMin": 120.0, "xMax": 380.0, "label": "Description"},
            {"field": "reference", "xMin": 380.0, "xMax": 400.0, "label": "Ref"},
            {"field": "amount", "xMin": 400.0, "xMax": 480.0, "label": "Amount"},
            {"field": "running_balance", "xMin": 480.0, "xMax": 570.0, "label": "Balance"},
        ]
    )
    with_reference = [
        Page(
            lines=[
                spaced(20.0, "Acme Bank Statement"),
                spaced(40.0, f"Opening Balance {OPENING}"),
                line(
                    100.0,
                    word(40.0, 110.0, "01/03/2026", 100.0),
                    word(130.0, 370.0, "COFFEE SHOP", 100.0),
                    word(382.0, 396.0, "A7", 100.0),
                    word(410.0, 470.0, "-10.00", 100.0),
                    word(490.0, 550.0, "990.00", 100.0),
                ),
                line(
                    120.0,
                    word(40.0, 110.0, "02/03/2026", 120.0),
                    word(130.0, 370.0, "SALARY MARCH", 120.0),
                    word(382.0, 396.0, "B8", 120.0),
                    word(410.0, 470.0, "500.00", 120.0),
                    word(490.0, 550.0, "1,490.00", 120.0),
                ),
                spaced(160.0, f"Closing Balance {CLOSING}"),
            ],
            has_text=True,
        )
    ]

    # The derivation itself, asserted directly. Comparing two batches' hashes instead would
    # pass for the wrong reason: a profile without the band puts the reference text into the
    # description column, so the hashes differ whatever the flag says.
    assert pipeline._maps_reference(referenced) is True
    assert pipeline._maps_reference(profile()) is False

    mapped = pipeline.analyse(
        referenced, with_reference, account_id=ACCOUNT, account_currency="AED", committed=[]
    )
    assert [row.reference_raw for row in mapped.rows] == ["A7", "B8"], "the column is captured"
    # And it reaches the fingerprint: `ref:` disambiguated rows, not `occ:` ones. Two rows that
    # are otherwise identical stay distinct only because their references differ.
    twins = [
        Page(
            lines=[
                spaced(20.0, "Acme Bank Statement"),
                spaced(40.0, f"Opening Balance {OPENING}"),
                *(
                    line(
                        top,
                        word(40.0, 110.0, "01/03/2026", top),
                        word(130.0, 370.0, "COFFEE SHOP", top),
                        word(382.0, 396.0, reference, top),
                        word(410.0, 470.0, "-10.00", top),
                        word(490.0, 550.0, balance, top),
                    )
                    for top, reference, balance in (
                        (100.0, "A7", "990.00"),
                        (120.0, "B8", "980.00"),
                    )
                ),
                spaced(160.0, "Closing Balance 980.00"),
            ],
            has_text=True,
        )
    ]
    distinct = pipeline.analyse(
        referenced, twins, account_id=ACCOUNT, account_currency="AED", committed=[]
    )
    assert len({v.dedupe_hash for v in distinct.verdicts}) == 2, "the reference told them apart"
