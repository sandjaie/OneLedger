"""M2 Task 3: profile-driven extraction and proposal behavior."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oneledger.ingest import engine
from oneledger.ingest.engine import extract, extract_pages, matches
from oneledger.ingest.words import Line, Page, Word
from oneledger.routes.layouts import LayoutCreate
from tests.fixtures.synthetic_pdf import Placement, build_pdf


def profile(**changes: object) -> LayoutCreate:
    values: dict[str, object] = {
        "name": "layout A",
        "fingerprints": [r"ACCOUNT SUMMARY", r"ACTIVITY"],
        "columns": [
            {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
            {"field": "description", "xMin": 100.0, "xMax": 400.0},
            {"field": "amount", "xMin": 400.0, "xMax": 600.0},
        ],
        "dateFormat": "dd/MM/yyyy",
        "signConvention": "signed",
        "chronologicalOrder": "ascending",
        "rowStartRule": "has_date_and_amount",
        "skipLinePatterns": [r"^Date Description Amount$"],
        "openingBalancePattern": r"Opening balance:\s*([0-9,.]+)",
        "closingBalancePattern": None,
        "hasRunningBalance": False,
    }
    values.update(changes)
    return LayoutCreate.model_validate(values)


def word(x0: float, x1: float, text: str, top: float = 10.0) -> Word:
    return Word(x0=x0, x1=x1, top=top, bottom=top + 10.0, text=text)


def page(*lines: list[Word]) -> Page:
    return Page(
        lines=[Line(top=line[0].top, words=line) for line in lines],
        has_text=bool(lines),
    )


def test_midpoint_assigns_an_overhanging_amount_to_the_amount_band() -> None:
    result = extract_pages(
        profile(),
        [
            page(
                [
                    word(20, 70, "01/02/2024"),
                    word(120, 180, "Purchase"),
                    # x0 overhangs the band; its midpoint is inside it.
                    word(390, 450, "-12.50"),
                ]
            )
        ],
    )

    assert result.rows[0].fields == {
        "booked_date": "01/02/2024",
        "description": "Purchase",
        "amount": "-12.50",
    }


def test_real_pdf_raw_lines_preserve_whitespace_and_filtered_arabic(tmp_path: Path) -> None:
    path = tmp_path / "bilingual.pdf"
    path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="01/02/2024  "),
                Placement(page=0, x=130, y=700, text="ALPHA   مرحبا  BETA"),
                Placement(page=0, x=445, y=700, text="17.25"),
            ]
        )
    )
    filtered = extract(profile(quirks=["filter_script:arabic"]), path, None)
    unfiltered = extract(profile(), path, None)
    assert filtered.words_filtered == 1
    assert filtered.rows[0].raw_text == "01/02/2024  ALPHA   مرحبا  BETA17.25"
    assert filtered.rows[0].raw_text == unfiltered.rows[0].raw_text
    assert filtered.rows[0].fields["description"] == "ALPHA BETA"
    assert "مرحبا" in unfiltered.rows[0].fields["description"]


def test_spaceless_producer_crosses_pdf_boundary(tmp_path: Path) -> None:
    path = tmp_path / "spaceless.pdf"
    path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="01/02/2024"),
                Placement(page=0, x=130, y=700, text="ALPHA"),
                Placement(page=0, x=180, y=700, text="BETA"),
                Placement(page=0, x=445, y=700, text="17.25"),
            ]
        )
    )
    result = extract(profile(), path, None)
    assert result.rows[0].raw_text == "01/02/2024ALPHABETA17.25"
    assert result.rows[0].fields["description"] == "ALPHA BETA"
    shifted = profile(
        columns=[
            {"field": "booked_date", "xMin": 0, "xMax": 100},
            {"field": "description", "xMin": 100, "xMax": 490},
            {"field": "amount", "xMin": 490, "xMax": 600},
        ]
    )
    assert extract(shifted, path, None).rows == []


def test_spaceless_tokens_are_split_by_bands_and_shifted_bands_change_cells() -> None:
    statement_page = page(
        [
            word(20, 76, "01/02/2024"),
            word(130, 174, "ALPHA"),
            word(176, 210, "BETA"),
            word(445, 482, "17.25"),
        ]
    )

    correct = extract_pages(profile(), [statement_page])
    shifted = extract_pages(
        profile(
            columns=[
                {"field": "booked_date", "xMin": 0.0, "xMax": 100.0},
                {"field": "description", "xMin": 100.0, "xMax": 470.0},
                {"field": "amount", "xMin": 470.0, "xMax": 600.0},
            ]
        ),
        [statement_page],
    )

    assert correct.rows[0].fields["description"] == "ALPHA BETA"
    assert correct.rows[0].fields["amount"] == "17.25"
    assert shifted.rows == []
    assert shifted.lines_unparsed == 1


def test_arabic_filter_removes_only_matching_words_and_reports_the_count() -> None:
    mixed = page(
        [
            word(20, 76, "01/02/2024"),
            word(130, 174, "ALPHA"),
            word(180, 225, "مرحبا"),
            word(445, 482, "17.25"),
        ]
    )

    filtered = extract_pages(profile(quirks=["filter_script:arabic"]), [mixed])
    unfiltered = extract_pages(profile(), [mixed])

    assert filtered.words_filtered == 1
    assert filtered.rows[0].raw_text == "01/02/2024 ALPHA مرحبا 17.25"
    assert filtered.rows[0].fields["description"] == "ALPHA"
    assert filtered.rows[0].fields["amount"] == "17.25"
    assert unfiltered.words_filtered == 0
    assert unfiltered.rows[0].fields["description"] == "ALPHA مرحبا"


def test_real_pdf_capture_uses_original_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "capture.pdf"
    path.write_bytes(build_pdf([Placement(page=0, x=20, y=700, text="Opening:   17.25  !")]))
    result = extract(profile(openingBalancePattern=r"Opening:(   17.25  )!"), path, None)
    assert result.declared_opening == "   17.25  "


def test_continuation_joins_description_fragments_in_physical_order() -> None:
    wrapped = page(
        [word(20, 76, "01/02/2024"), word(130, 170, "FIRST"), word(445, 482, "17.25")],
        [word(130, 185, "SECOND", top=30.0)],
        [word(130, 180, "THIRD", top=50.0)],
    )

    joined = extract_pages(
        profile(
            rowStartRule="has_date",
            continuationRule="no_date_and_no_amount",
            quirks=["continuation:no_date_and_no_amount"],
        ),
        [wrapped],
    )
    dropped = extract_pages(profile(rowStartRule="has_date"), [wrapped])

    assert len(joined.rows) == 1
    assert joined.rows[0].fields == {
        "booked_date": "01/02/2024",
        "description": "FIRST SECOND THIRD",
        "amount": "17.25",
    }
    assert joined.rows[0].raw_text == "01/02/2024 FIRST 17.25\nSECOND\nTHIRD"
    assert joined.lines_unparsed == 0
    assert len(dropped.rows) == 1
    assert dropped.rows[0].fields["description"] == "FIRST"
    assert dropped.lines_unparsed == 2


def test_nearest_dated_row_owns_leading_description_fragments() -> None:
    centered_cells = page(
        [
            word(20, 76, "01/02/2024", top=10.0),
            word(130, 170, "FIRST", top=10.0),
            word(445, 482, "17.25", top=10.0),
        ],
        [word(130, 180, "LEADING", top=29.0)],
        [word(130, 180, "DETAIL", top=32.0)],
        [
            word(20, 76, "02/02/2024", top=40.0),
            word(130, 180, "TRAILING", top=40.0),
            word(445, 482, "9.00", top=40.0),
        ],
    )

    nearest = extract_pages(
        profile(
            rowStartRule="has_date",
            continuationRule="blank_date_and_money_columns",
            quirks=["row_assembly:nearest_dated_row"],
        ),
        [centered_cells],
    )
    legacy = extract_pages(
        profile(rowStartRule="has_date", continuationRule="blank_date_and_money_columns"),
        [centered_cells],
    )

    assert [row.fields["description"] for row in nearest.rows] == [
        "FIRST",
        "LEADING DETAIL TRAILING",
    ]
    assert [row.fields["description"] for row in legacy.rows] == [
        "FIRST LEADING DETAIL",
        "TRAILING",
    ]
    assert nearest.rows[1].raw_text == ("LEADING\nDETAIL\n02/02/2024 TRAILING 9.00")
    assert nearest.lines_unparsed == legacy.lines_unparsed == 0


def test_nearest_dated_row_tie_stays_with_the_earlier_row() -> None:
    tied = page(
        [
            word(20, 76, "01/02/2024", top=10.0),
            word(130, 170, "FIRST", top=10.0),
            word(445, 482, "17.25", top=10.0),
        ],
        [word(130, 180, "TIED", top=30.0)],
        [
            word(20, 76, "02/02/2024", top=50.0),
            word(130, 180, "SECOND", top=50.0),
            word(445, 482, "9.00", top=50.0),
        ],
    )

    result = extract_pages(
        profile(
            rowStartRule="has_date",
            continuationRule="blank_date_and_money_columns",
            quirks=["row_assembly:nearest_dated_row"],
        ),
        [tied],
    )

    assert [row.fields["description"] for row in result.rows] == ["FIRST TIED", "SECOND"]


def test_the_stricter_continuation_rule_separates_wrapped_narration_from_a_paragraph() -> None:
    """M2 spec §7 item 13: "no date" and "the date column is blank" are different questions.

    A wrapped narration sits inside the description column and leaves the others empty. A closing
    paragraph is set across the page, so it fills the date and money columns with prose that
    merely fails to parse — and `no_date_and_no_amount` cannot tell the two apart, appending the
    whole legal block to the last transaction of the statement.
    """
    document = page(
        [word(20, 76, "01/02/2024"), word(130, 170, "FIRST"), word(445, 482, "17.25")],
        # Wrapped narration: the description column only.
        [word(130, 185, "SECOND", top=30.0)],
        # A closing paragraph, set across the page: prose in the date and amount columns too.
        [
            word(20, 90, "Nothing", top=50.0),
            word(130, 300, "in this paragraph is a sentence", top=50.0),
            word(410, 520, "any producer wrote", top=50.0),
        ],
    )

    strict = extract_pages(
        profile(rowStartRule="has_date", continuationRule="blank_date_and_money_columns"),
        [document],
    )
    loose = extract_pages(
        profile(rowStartRule="has_date", continuationRule="no_date_and_no_amount"),
        [document],
    )

    assert len(strict.rows) == 1
    assert strict.rows[0].fields["description"] == "FIRST SECOND"
    # The paragraph is dropped and counted, never silently discarded (Invariant I7).
    assert strict.lines_unparsed == 1

    # The looser rule takes the paragraph into the transaction, and says nothing was lost.
    assert loose.rows[0].fields["description"] == ("FIRST SECOND in this paragraph is a sentence")
    assert loose.lines_unparsed == 0


def test_the_stricter_rule_still_needs_a_description_and_no_amount() -> None:
    """It narrows `no_date_and_no_amount`; it does not replace its two conditions."""
    priced = page(
        [word(20, 76, "01/02/2024"), word(130, 170, "FIRST"), word(445, 482, "17.25")],
        # A blank date column, but the amount column carries a figure: a row that lost its date,
        # not a continuation of the one above, and appending it would hide the loss.
        [word(130, 185, "SECOND", top=30.0), word(445, 482, "9.00", top=30.0)],
    )

    result = extract_pages(
        profile(rowStartRule="has_date", continuationRule="blank_date_and_money_columns"),
        [priced],
    )

    assert result.rows[0].fields["description"] == "FIRST"
    assert result.lines_unparsed == 1


def test_matches_uses_fingerprints_as_evidence_and_never_the_filename_alone() -> None:
    candidate = profile()

    assert matches(candidate, "ACCOUNT SUMMARY\nACTIVITY", "unrelated.pdf") == 1.0
    assert matches(candidate, "something else", "layout-A-account-summary.pdf") == 0.0
    assert matches(candidate, "ACCOUNT SUMMARY", "unrelated.pdf") == 0.5


def test_reserved_account_capture_is_transient_and_other_metadata_is_returned() -> None:
    result = extract_pages(
        profile(
            metadataPatterns={
                "account_number": r"Account:\s*([0-9 -]+)",
                "owner_label": r"Owner:\s*([A-Z ]+)",
            }
        ),
        [
            page(
                [word(10, 70, "Account:"), word(80, 170, "1234-5678")],
                [word(10, 60, "Owner:", top=30), word(80, 140, "PRIMARY", top=30)],
                [
                    word(20, 76, "01/02/2024", top=50),
                    word(130, 174, "ALPHA", top=50),
                    word(445, 482, "17.25", top=50),
                ],
            )
        ],
    )

    assert result.account_number_raw == "1234-5678"
    assert result.metadata == {"owner_label": "PRIMARY"}
    assert "account_number" not in result.metadata


def test_statement_institution_is_transient_page_one_pdf_evidence() -> None:
    result = extract_pages(
        profile(metadataPatterns={"institution_name": r"Issuer:\s*(Synthetic\s+Bank)"}),
        [page([word(20, 90, "Issuer:"), word(95, 160, "Synthetic"), word(165, 210, "Bank")])],
    )

    assert result.institution_name_raw == "Synthetic Bank"
    assert "institution_name" not in result.metadata

    later_page_only = extract_pages(
        profile(metadataPatterns={"institution_name": r"Issuer:\s*(Synthetic\s+Bank)"}),
        [
            page([word(20, 90, "ACCOUNT SUMMARY")]),
            page([word(20, 90, "Issuer:"), word(95, 160, "Synthetic"), word(165, 210, "Bank")]),
        ],
    )
    assert later_page_only.institution_name_raw is None
    assert "institution_name" not in later_page_only.metadata


def test_extract_refuses_a_pdf_with_zero_text_objects(tmp_path: Path) -> None:
    pdf_path = tmp_path / "empty.pdf"
    pdf_path.write_bytes(build_pdf([], page_count=1))

    with pytest.raises(ValueError, match="scanned PDFs are unsupported"):
        extract(profile(), pdf_path, None)


def test_extract_reads_a_generated_pdf_without_parser_coupling(tmp_path: Path) -> None:
    pdf_path = tmp_path / "layout-a.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="01/02/2024"),
                Placement(page=0, x=130, y=700, text="ALPHA BETA"),
                Placement(page=0, x=445, y=700, text="17.25"),
            ]
        )
    )

    result = extract(profile(), pdf_path, None)

    assert result.page_count == 1
    assert result.has_text is True
    assert result.rows[0].fields["description"] == "ALPHA BETA"


def test_propose_refuses_a_page_without_a_qualifying_header(tmp_path: Path) -> None:
    pdf_path = tmp_path / "unknown-layout.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=720, text="ACCOUNT SUMMARY"),
                Placement(page=0, x=20, y=690, text="Description"),
            ]
        )
    )

    with pytest.raises(ValueError, match="header"):
        engine.propose(pdf_path, None)


@pytest.mark.parametrize("different", [False, True])
def test_repeated_headers_are_deduplicated_only_when_equivalent(
    tmp_path: Path,
    different: bool,
) -> None:
    placements = []
    for index in range(2):
        header = (
            ("Date", "Description", "Amount") if index == 0 else ("DATE", "DESCRIPTION", "AMOUNT")
        )
        placements.extend(
            [
                Placement(page=index, x=20, y=700, text=header[0]),
                Placement(page=index, x=180, y=700, text=header[1]),
                Placement(
                    page=index,
                    x=500 if not different or index == 0 else 450,
                    y=700,
                    text=header[2],
                ),
                Placement(page=index, x=20, y=670, text=f"{13 + index}/02/2024"),
                Placement(page=index, x=180, y=670, text="ITEM"),
                Placement(page=index, x=500, y=670, text="17.25"),
            ]
        )
    path = tmp_path / "multipage.pdf"
    path.write_bytes(build_pdf(placements))
    if different:
        with pytest.raises(ValueError, match="ambiguous header"):
            engine.propose(path, None)
    else:
        _, rows = engine.propose(path, None)
        assert [row.page_number for row in rows] == [1, 2]


def test_propose_excludes_non_row_text_from_date_inference(tmp_path: Path) -> None:
    placements = [
        Placement(page=0, x=20, y=700, text="Date"),
        Placement(page=0, x=180, y=700, text="Description"),
        Placement(page=0, x=500, y=700, text="Amount"),
        Placement(page=0, x=20, y=670, text="13/02/2024"),
        Placement(page=0, x=180, y=670, text="FIRST"),
        Placement(page=0, x=500, y=670, text="17.25"),
        Placement(page=0, x=20, y=640, text="Closing Balance"),
        Placement(page=0, x=500, y=640, text="17.25"),
        Placement(page=1, x=20, y=730, text="ACCOUNT SUMMARY"),
        Placement(page=1, x=20, y=700, text="Date"),
        Placement(page=1, x=180, y=700, text="Description"),
        Placement(page=1, x=500, y=700, text="Amount"),
        Placement(page=1, x=20, y=670, text="14/02/2024"),
        Placement(page=1, x=180, y=670, text="SECOND"),
        Placement(page=1, x=500, y=670, text="20.00"),
    ]
    path = tmp_path / "multipage-with-non-rows.pdf"
    path.write_bytes(build_pdf(placements))

    draft, rows = engine.propose(path, None)

    assert draft.dateFormat == "dd/MM/yyyy"
    assert [row.fields["description"] for row in rows] == ["FIRST", "SECOND"]


def test_proposed_header_pattern_skips_case_equivalent_repeated_headers(tmp_path: Path) -> None:
    placements = []
    for page_number, first_word, day, description in (
        (0, "Transaction", 13, "FIRST"),
        (1, "TRANSACTION", 14, "SECOND"),
    ):
        placements.extend(
            [
                Placement(page=page_number, x=20, y=700, text=first_word),
                Placement(page=page_number, x=100, y=700, text="Date"),
                Placement(page=page_number, x=180, y=700, text="Description"),
                Placement(page=page_number, x=500, y=700, text="Amount"),
                Placement(page=page_number, x=20, y=670, text=f"{day}/02/2024"),
                Placement(page=page_number, x=180, y=670, text=description),
                Placement(page=page_number, x=500, y=670, text="17.25"),
            ]
        )
    path = tmp_path / "case-equivalent-headers.pdf"
    path.write_bytes(build_pdf(placements))

    draft, _ = engine.propose(path, None)
    result = engine.extract(
        profile(
            skipLinePatterns=draft.skipLinePatterns,
            continuationRule="no_date_and_no_amount",
        ),
        path,
        None,
    )

    assert [row.fields["description"] for row in result.rows] == ["FIRST", "SECOND"]


def test_dash_dates_do_not_silently_choose_day_first(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous-dashes.pdf"
    path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Date"),
                Placement(page=0, x=180, y=700, text="Description"),
                Placement(page=0, x=500, y=700, text="Amount"),
                Placement(page=0, x=20, y=670, text="03-04-2024"),
                Placement(page=0, x=180, y=670, text="FIRST"),
                Placement(page=0, x=500, y=670, text="17.25"),
                Placement(page=0, x=20, y=640, text="05-06-2024"),
                Placement(page=0, x=180, y=640, text="SECOND"),
                Placement(page=0, x=500, y=640, text="18.25"),
            ]
        )
    )
    with pytest.raises(ValueError, match="date format is ambiguous"):
        engine.propose(path, None)


def test_propose_builds_midpoint_bands_that_extract_the_statement(tmp_path: Path) -> None:
    pdf_path = tmp_path / "unseen-layout.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=10, y=750, text="ACCOUNT SUMMARY"),
                Placement(page=0, x=10, y=730, text="ACTIVITY"),
                Placement(page=0, x=20, y=700, text="Transaction Date"),
                Placement(page=0, x=180, y=700, text="Details"),
                Placement(page=0, x=410, y=700, text="Debit"),
                Placement(page=0, x=500, y=700, text="Credit"),
                Placement(page=0, x=20, y=670, text="13/02/2024"),
                Placement(page=0, x=180, y=670, text="ALPHA"),
                Placement(page=0, x=425, y=670, text="17.25"),
                Placement(page=0, x=20, y=640, text="14/02/2024"),
                Placement(page=0, x=180, y=640, text="BETA"),
                Placement(page=0, x=525, y=640, text="20.00"),
            ]
        )
    )

    draft, rows = engine.propose(pdf_path, None)

    assert [column.field for column in draft.columns] == [
        "booked_date",
        "description",
        "debit",
        "credit",
    ]
    assert draft.columns[0].xMin == 10
    assert draft.columns[-1].xMax > 550
    assert [row.fields for row in rows] == [
        {"booked_date": "13/02/2024", "description": "ALPHA", "debit": "17.25"},
        {"booked_date": "14/02/2024", "description": "BETA", "credit": "20.00"},
    ]


def test_propose_maps_a_second_date_header_to_value_date(tmp_path: Path) -> None:
    pdf_path = tmp_path / "dual-date-layout.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Transaction Date"),
                Placement(page=0, x=135, y=700, text="Posting Date"),
                Placement(page=0, x=260, y=700, text="Description"),
                Placement(page=0, x=500, y=700, text="Amount"),
                Placement(page=0, x=20, y=670, text="13/02/2024"),
                Placement(page=0, x=140, y=670, text="14/02/2024"),
                Placement(page=0, x=260, y=670, text="ALPHA"),
                Placement(page=0, x=500, y=670, text="17.25"),
                Placement(page=0, x=20, y=640, text="15/02/2024"),
                Placement(page=0, x=140, y=640, text="16/02/2024"),
                Placement(page=0, x=260, y=640, text="BETA"),
                Placement(page=0, x=500, y=640, text="20.00"),
            ]
        )
    )

    draft, rows = engine.propose(pdf_path, None)

    assert [column.field for column in draft.columns] == [
        "booked_date",
        "value_date",
        "description",
        "amount",
    ]
    assert [row.fields for row in rows] == [
        {
            "booked_date": "13/02/2024",
            "value_date": "14/02/2024",
            "description": "ALPHA",
            "amount": "17.25",
        },
        {
            "booked_date": "15/02/2024",
            "value_date": "16/02/2024",
            "description": "BETA",
            "amount": "20.00",
        },
    ]


@pytest.mark.parametrize("amount_header", ["Debit", "Credit"])
def test_propose_refuses_a_lone_directional_amount_column(
    tmp_path: Path,
    amount_header: str,
) -> None:
    pdf_path = tmp_path / "lone-directional-amount.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Date"),
                Placement(page=0, x=180, y=700, text="Description"),
                Placement(page=0, x=500, y=700, text=amount_header),
                Placement(page=0, x=20, y=670, text="13/02/2024"),
                Placement(page=0, x=180, y=670, text="ALPHA"),
                Placement(page=0, x=500, y=670, text="17.25"),
                Placement(page=0, x=20, y=640, text="14/02/2024"),
                Placement(page=0, x=180, y=640, text="BETA"),
                Placement(page=0, x=500, y=640, text="20.00"),
            ]
        )
    )

    with pytest.raises(ValueError, match="lone debit or credit"):
        engine.propose(pdf_path, None)


def test_propose_infers_suffix_dates_and_descending_order(tmp_path: Path) -> None:
    pdf_path = tmp_path / "suffix-layout.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Date"),
                Placement(page=0, x=180, y=700, text="Narration"),
                Placement(page=0, x=500, y=700, text="Amount"),
                Placement(page=0, x=20, y=670, text="14-02-2024"),
                Placement(page=0, x=180, y=670, text="ALPHA"),
                Placement(page=0, x=500, y=670, text="17.25 CR"),
                Placement(page=0, x=20, y=640, text="13-02-2024"),
                Placement(page=0, x=180, y=640, text="BETA"),
                Placement(page=0, x=500, y=640, text="20.00 DR"),
            ]
        )
    )

    draft, rows = engine.propose(pdf_path, None)

    assert draft.dateFormat == "dd-MM-yyyy"
    assert draft.signConvention == "cr_suffix"
    assert draft.chronologicalOrder == "descending"
    assert [row.fields["amount"] for row in rows] == ["17.25 CR", "20.00 DR"]


def test_propose_screens_normalized_identifiers_and_returns_only_twenty_rows(
    tmp_path: Path,
) -> None:
    placements = [
        Placement(page=0, x=10, y=770, text="ACCOUNT SUMMARY"),
        Placement(page=0, x=10, y=750, text="Customer reference 123-456-789"),
        Placement(page=0, x=10, y=730, text="ACTIVITY"),
        Placement(page=0, x=20, y=700, text="Date"),
        Placement(page=0, x=180, y=700, text="Description"),
        Placement(page=0, x=500, y=700, text="Amount"),
    ]
    for day in range(1, 22):
        y = 700 - day * 20
        placements.extend(
            [
                Placement(page=0, x=20, y=y, text=f"{day:02d}/01/2024"),
                Placement(page=0, x=180, y=y, text=f"ITEM {day:02d}"),
                Placement(page=0, x=500, y=y, text=f"{day}.00"),
            ]
        )
    pdf_path = tmp_path / "fingerprint-layout.pdf"
    pdf_path.write_bytes(build_pdf(placements))

    draft, rows = engine.propose(pdf_path, None)

    assert len(rows) == 20
    assert rows[-1].fields["booked_date"] == "20/01/2024"
    assert all("123" not in fingerprint for fingerprint in draft.fingerprints)
    for fingerprint in draft.fingerprints:
        unescaped = fingerprint.replace("\\", "")
        normalized = re.sub(r"(?<=\d)[\s\-–./]+(?=\d)", "", unescaped)
        assert re.search(r"\d{4,}", normalized) is None
    assert draft.openingBalancePattern is None
    assert draft.closingBalancePattern is None
    assert draft.metadataPatterns is None


def test_propose_refuses_when_numeric_date_order_is_ambiguous(tmp_path: Path) -> None:
    pdf_path = tmp_path / "ambiguous-dates.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Date"),
                Placement(page=0, x=180, y=700, text="Details"),
                Placement(page=0, x=500, y=700, text="Amount"),
                Placement(page=0, x=20, y=670, text="01/02/2024"),
                Placement(page=0, x=180, y=670, text="ALPHA"),
                Placement(page=0, x=500, y=670, text="17.25"),
                Placement(page=0, x=20, y=640, text="02/03/2024"),
                Placement(page=0, x=180, y=640, text="BETA"),
                Placement(page=0, x=500, y=640, text="20.00"),
            ]
        )
    )

    with pytest.raises(ValueError, match="date format is ambiguous"):
        engine.propose(pdf_path, None)


def test_filtering_precedes_grouping_so_removed_words_cannot_anchor_a_line() -> None:
    # The early Arabic word anchored the date at top=10, splitting it from the
    # amount at top=14. Removing it before regrouping must reunite the transaction.
    source = page(
        [word(180, 225, "مرحبا", top=10), word(20, 76, "01/02/2024", top=12)],
        [word(130, 174, "ALPHA", top=14), word(445, 482, "17.25", top=14)],
    )
    result = extract_pages(profile(quirks=["filter_script:arabic"]), [source])
    assert result.words_filtered == 1
    assert len(result.rows) == 1
    assert result.rows[0].fields["amount"] == "17.25"
    assert result.rows[0].fields["booked_date"] == "01/02/2024"


def test_capture_groups_preserve_whitespace_verbatim() -> None:
    result = extract_pages(
        profile(
            openingBalancePattern=r"Opening:( 17.25 )!",
            closingBalancePattern=r"Closing:( 20.00 )!",
            metadataPatterns={
                "account_number": r"Account:( 1234-5678 )!",
                "owner_label": r"Owner:( PRIMARY )!",
            },
        ),
        [
            page(
                [
                    word(
                        10,
                        500,
                        "Opening: 17.25 ! Closing: 20.00 ! Account: 1234-5678 ! Owner: PRIMARY !",
                    )
                ]
            )
        ],
    )
    assert result.declared_opening == " 17.25 "
    assert result.declared_closing == " 20.00 "
    assert result.account_number_raw == " 1234-5678 "
    assert result.metadata == {"owner_label": " PRIMARY "}


@pytest.mark.parametrize("convention", ["signed", "cr_suffix", "debit_credit_columns"])
def test_balance_alone_does_not_start_an_ordinary_amount_row(convention: str) -> None:
    amount_columns = (
        [
            {"field": "debit", "xMin": 300, "xMax": 400},
            {"field": "credit", "xMin": 400, "xMax": 500},
        ]
        if convention == "debit_credit_columns"
        else [{"field": "amount", "xMin": 300, "xMax": 500}]
    )
    candidate = profile(
        signConvention=convention,
        hasRunningBalance=True,
        columns=[
            {"field": "booked_date", "xMin": 0, "xMax": 100},
            {"field": "description", "xMin": 100, "xMax": 300},
            *amount_columns,
            {"field": "running_balance", "xMin": 500, "xMax": 600},
        ],
    )
    result = extract_pages(
        candidate,
        [page([word(20, 76, "01/02/2024"), word(130, 170, "BALANCE"), word(520, 570, "17.25")])],
    )
    assert result.rows == []
    assert result.lines_unparsed == 1


def test_out_of_period_rows_are_retained_for_normalization_to_reject() -> None:
    candidate = profile(
        metadataPatterns={
            "period_start": r"From: (01/02/2024)",
            "period_end": r"To: (29/02/2024)",
        }
    )
    result = extract_pages(
        candidate,
        [
            page(
                [word(10, 300, "From: 01/02/2024 To: 29/02/2024")],
                [
                    word(20, 76, "01/03/2024", top=30),
                    word(130, 180, "LATE", top=30),
                    word(445, 482, "17.25", top=30),
                ],
            )
        ],
    )
    assert len(result.rows) == 1
    assert result.rows[0].fields["booked_date"] == "01/03/2024"


@pytest.mark.parametrize("amount", ["1,,2", "12,", "1,2", "1,2,3", "(12.00", "12.00)"])
def test_malformed_amount_does_not_start_a_row(amount: str) -> None:
    result = extract_pages(
        profile(),
        [page([word(20, 76, "01/02/2024"), word(130, 170, "INVALID"), word(445, 482, amount)])],
    )
    assert result.rows == []
    assert result.lines_unparsed == 1


def test_an_amount_grouped_in_twos_starts_a_row() -> None:
    """`1,23,456.7890` — grouped in twos after the final three, not in threes throughout.

    Named for the shape rather than the country whose convention it is: the grouping style is a
    property of a currency row (`currency.number_format`), and a country adjective in a name
    here is what ADR-0012's identifier rule refuses.
    """
    result = extract_pages(
        profile(),
        [
            page(
                [
                    word(20, 76, "01/02/2024"),
                    word(130, 170, "VALID"),
                    word(445, 500, "1,23,456.7890"),
                ]
            )
        ],
    )
    assert result.rows[0].fields["amount"] == "1,23,456.7890"


@pytest.mark.parametrize(
    "convention,field,amount",
    [
        ("signed", "amount", "(1,234.50)"),
        ("cr_suffix", "amount", "17.25 CR"),
        ("debit_credit_columns", "debit", "17.25"),
        ("debit_credit_columns", "credit", "17.25"),
        ("balance_delta", "running_balance", "1,234.50"),
    ],
)
def test_sign_conventions_use_their_amount_columns(
    convention: str, field: str, amount: str
) -> None:
    candidate = profile(
        signConvention=convention,
        hasRunningBalance=convention == "balance_delta",
        columns=[
            {"field": "booked_date", "xMin": 0, "xMax": 100},
            {"field": "description", "xMin": 100, "xMax": 400},
            *(
                [
                    {"field": "debit", "xMin": 400, "xMax": 500},
                    {"field": "credit", "xMin": 500, "xMax": 600},
                ]
                if convention == "debit_credit_columns"
                else [{"field": field, "xMin": 400, "xMax": 600}]
            ),
        ],
    )
    x0 = 520 if field == "credit" else 445
    result = extract_pages(
        candidate,
        [page([word(20, 76, "01/02/2024"), word(130, 170, "VALID"), word(x0, x0 + 35, amount)])],
    )
    assert result.rows[0].fields[field] == amount


@pytest.mark.parametrize("convention", ["signed", "cr_suffix"])
def test_single_amount_conventions_accept_mapped_debit_credit_pairs(convention: str) -> None:
    candidate = profile(
        signConvention=convention,
        columns=[
            {"field": "booked_date", "xMin": 0, "xMax": 100},
            {"field": "description", "xMin": 100, "xMax": 400},
            {"field": "debit", "xMin": 400, "xMax": 500},
            {"field": "credit", "xMin": 500, "xMax": 600},
        ],
    )
    result = extract_pages(
        candidate,
        [page([word(20, 76, "01/02/2024"), word(130, 170, "VALID"), word(445, 482, "17.25")])],
    )

    assert result.rows[0].fields["debit"] == "17.25"


def test_account_capture_is_not_exposed_in_result_repr() -> None:
    result = extract_pages(
        profile(metadataPatterns={"account_number": r"Account: (1234-5678)"}),
        [page([word(10, 300, "Account: 1234-5678")])],
    )
    assert result.account_number_raw == "1234-5678"
    assert "1234-5678" not in repr(result)


def test_cells_sort_by_x0_keep_out_of_band_raw_text_and_number_across_pages() -> None:
    source = [
        page(
            [
                word(445, 482, "17.25"),
                word(180, 225, "SECOND"),
                word(130, 174, "FIRST"),
                word(20, 76, "01/02/2024"),
                word(610, 640, "OUTSIDE"),
            ]
        ),
        page(
            [word(10, 300, "Date Description Amount")],
            [
                word(20, 76, "02/02/2024", top=30),
                word(130, 174, "NEXT", top=30),
                word(445, 482, "20.00", top=30),
            ],
        ),
    ]
    result = extract_pages(profile(), source)
    assert result.rows[0].fields == {
        "booked_date": "01/02/2024",
        "description": "FIRST SECOND",
        "amount": "17.25",
    }
    assert result.rows[0].raw_text == "01/02/2024 FIRST SECOND 17.25 OUTSIDE"
    assert [(row.page_number, row.row_number) for row in result.rows] == [(1, 1), (2, 2)]
    assert result.lines_unparsed == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"continuationRule": "no_date_and_no_amount"},
        {"quirks": ["continuation:no_date_and_no_amount"]},
    ],
)
def test_continuation_accepts_each_flag_but_never_a_date_or_amount(
    settings: dict[str, object],
) -> None:
    result = extract_pages(
        profile(**settings),
        [
            page(
                [word(130, 174, "ORPHAN")],
                [
                    word(20, 76, "01/02/2024", top=30),
                    word(130, 174, "FIRST", top=30),
                    word(445, 482, "17.25", top=30),
                ],
                [word(130, 174, "SECOND", top=50)],
                [word(130, 174, "AMOUNT", top=70), word(445, 482, "19.00", top=70)],
                [word(20, 76, "02/02/2024", top=90), word(130, 174, "DATE", top=90)],
            )
        ],
    )
    assert len(result.rows) == 1
    assert result.rows[0].fields["description"] == "FIRST SECOND"
    assert result.lines_unparsed == 3


@pytest.mark.parametrize(
    ("debit_header", "credit_header"),
    [("Withdrawal Amount", "Deposit Amount"), ("Debit Amount", "Credit Amount")],
)
def test_propose_never_reads_a_directional_header_as_a_generic_amount(
    tmp_path: Path,
    debit_header: str,
    credit_header: str,
) -> None:
    pdf_path = tmp_path / "directional-amount-layout.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Date"),
                Placement(page=0, x=100, y=700, text="Description"),
                Placement(page=0, x=300, y=700, text=debit_header),
                Placement(page=0, x=400, y=700, text=credit_header),
                Placement(page=0, x=500, y=700, text="Balance"),
                Placement(page=0, x=20, y=670, text="13/02/2024"),
                Placement(page=0, x=100, y=670, text="ALPHA"),
                Placement(page=0, x=300, y=670, text="17.25"),
                Placement(page=0, x=500, y=670, text="82.75"),
                Placement(page=0, x=20, y=640, text="14/02/2024"),
                Placement(page=0, x=100, y=640, text="BETA"),
                Placement(page=0, x=400, y=640, text="20.00"),
                Placement(page=0, x=500, y=640, text="102.75"),
            ]
        )
    )

    draft, rows = engine.propose(pdf_path, None)

    assert [column.field for column in draft.columns] == [
        "booked_date",
        "description",
        "debit",
        "credit",
        "running_balance",
    ]
    assert draft.signConvention == "debit_credit_columns"
    assert [row.fields for row in rows] == [
        {
            "booked_date": "13/02/2024",
            "description": "ALPHA",
            "debit": "17.25",
            "running_balance": "82.75",
        },
        {
            "booked_date": "14/02/2024",
            "description": "BETA",
            "credit": "20.00",
            "running_balance": "102.75",
        },
    ]
    # A proposal the mapping UI cannot save is not a proposal.
    LayoutCreate.model_validate(
        {
            **{key: value for key, value in vars(draft).items() if key != "columns"},
            "columns": [vars(column) for column in draft.columns],
        }
    )


def test_propose_refuses_an_amount_column_beside_debit_and_credit(tmp_path: Path) -> None:
    pdf_path = tmp_path / "three-amount-columns.pdf"
    pdf_path.write_bytes(
        build_pdf(
            [
                Placement(page=0, x=20, y=700, text="Date"),
                Placement(page=0, x=120, y=700, text="Amount"),
                Placement(page=0, x=300, y=700, text="Debit"),
                Placement(page=0, x=420, y=700, text="Credit"),
                Placement(page=0, x=20, y=670, text="13/02/2024"),
                Placement(page=0, x=120, y=670, text="17.25"),
                Placement(page=0, x=300, y=670, text="17.25"),
                Placement(page=0, x=20, y=640, text="14/02/2024"),
                Placement(page=0, x=120, y=640, text="20.00"),
                Placement(page=0, x=420, y=640, text="20.00"),
            ]
        )
    )

    with pytest.raises(ValueError, match="amount column beside debit and credit"):
        engine.propose(pdf_path, None)
