"""M2 Task 4c: reconciliation — the four verdicts.

Pure values, no database and no network. The spec's section 4.6 is the contract, and its two
traps are asserted here rather than assumed: the identity is evaluated in **one frame**, and the
balance walk runs in **chronological** order. Either one half-applied produces a batch that is
internally consistent and entirely wrong, which is the failure this module exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from oneledger.ingest import reconcile
from oneledger.ingest.normalize import NormalizedRow

CURRENCY = "XAA"


@dataclass(frozen=True)
class Profile:
    """A stub of `ReconcilableProfile` — the five semantics reconciliation reads.

    The module declares a structural protocol rather than importing the layout model, so the
    tests can too — which is also what lets them run where importing the model cannot. The
    binding to the real model is asserted below, under `TYPE_CHECKING`, so a rename in either
    place fails the type check without the runtime import.
    """

    invertsSign: bool = False
    chronologicalOrder: str = "ascending"
    openingBalancePattern: str | None = None
    closingBalancePattern: str | None = r"Closing balance:\s*([0-9,.-]+)"
    hasRunningBalance: bool = False


def profile(**changes: object) -> Profile:
    return replace(Profile(), **changes)  # type: ignore[arg-type]


if TYPE_CHECKING:  # pragma: no cover - a type-level assertion, never executed
    from oneledger.routes.layouts import LayoutCreate

    def _the_layout_model_satisfies_the_protocol(
        model: LayoutCreate,
    ) -> reconcile.ReconcilableProfile:
        """A rename on either side fails `mypy` here rather than at the first call site."""
        return model


def row(number: int, amount: str, *, balance: str | None = None, day: int = 1) -> NormalizedRow:
    """One normalized row: `amount` is already in the ledger frame, `balance` is not.

    That asymmetry is real — `normalize.py` applies `invertsSign` to the amount and leaves the
    statement's own running balance verbatim — and it is the reason this module has to invert
    one side and not the other.
    """
    return NormalizedRow(
        source_page_number=1,
        source_row_number=number,
        raw_row={},
        booked_date=date(2026, 3, day),
        value_date=None,
        description_raw="ROW",
        description_clean="ROW",
        description_rules_applied=(),
        reference_raw=None,
        amount=Decimal(amount),
        currency=CURRENCY,
        original_amount=None,
        original_currency=None,
        proposed_txn_type="expense",
        running_balance=Decimal(balance) if balance is not None else None,
    )


def verdict(
    rows: list[NormalizedRow],
    *,
    opening: str | None = None,
    closing: str | None = None,
    **changes: object,
) -> reconcile.Verdict:
    return reconcile.reconcile(
        profile(**changes), rows, declared_opening=opening, declared_closing=closing
    )


# --- the single frame ------------------------------------------------------


def test_a_card_layout_reconciles_with_both_sides_inverted() -> None:
    """The spec's own worked example, and the trap it exists to name.

    Declared previous balance 4,500.00 owed is -4500 in the ledger; a purchase presented as
    310.00 is -310; declared new balance 4,810.00 is -4810. -4500 + -310 = -4810.
    """
    result = verdict(
        [row(1, "-310.0000")],
        opening="4,500.00",
        closing="4,810.00",
        invertsSign=True,
    )
    assert result.status == "matched"
    assert result.opening_is_derived is False
    # The string form, not just the value: money reaches the wire as `str(value)`, and a
    # literal zero here would send `"0"` where every other verdict sends four decimal places.
    assert str(result.delta) == "0.0000"
    assert result.declared_opening == Decimal("-4500.0000")
    assert result.declared_closing == Decimal("-4810.0000")
    assert result.computed_closing == Decimal("-4810.0000")


def test_the_declared_side_is_not_inverted_without_the_flag() -> None:
    """The same numbers with `invertsSign` off must not reconcile.

    If this passed, the inversion would not be load-bearing and a half-applied frame could go
    unnoticed — the failure mode the spec calls internally consistent and entirely wrong.
    """
    result = verdict([row(1, "-310.0000")], opening="4,500.00", closing="4,810.00")
    assert result.status == "mismatched"


def test_inverting_only_the_rows_does_not_reconcile() -> None:
    """A frame applied to one side only: rows in ledger sign, balances left as printed."""
    result = verdict(
        [row(1, "-310.0000")],
        opening="-4,500.00",
        closing="-4,810.00",
        invertsSign=True,
    )
    assert result.status == "mismatched"


# --- chronological order ---------------------------------------------------


def test_a_descending_statement_reconciles_when_walked_chronologically() -> None:
    rows = [
        row(1, "-30.0000", balance="70.0000", day=3),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-50.0000", balance="120.0000", day=1),
    ]
    result = verdict(
        rows,
        opening="170.0000",
        closing="70.0000",
        hasRunningBalance=True,
        chronologicalOrder="descending",
    )
    assert result.status == "matched"


def test_the_same_rows_walked_in_display_order_break_at_every_row() -> None:
    """Declaring the statement ascending when it is descending is the regression.

    Without the reversal the running-balance identity reads the wrong neighbour and every valid
    row is flagged, so this test fails if the reversal is dropped.
    """
    rows = [
        row(1, "-30.0000", balance="70.0000", day=3),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-50.0000", balance="120.0000", day=1),
    ]
    result = verdict(
        rows,
        opening="170.0000",
        closing="70.0000",
        hasRunningBalance=True,
        closingBalancePattern=None,
    )
    assert result.status == "running_balance_break"
    assert result.first_divergent_row == 1


# --- the running-balance walk ----------------------------------------------


def test_a_running_balance_break_names_the_row_and_both_balances() -> None:
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="95.0000", day=2),  # states 95, the walk expects 100
        row(3, "-30.0000", balance="70.0000", day=3),
    ]
    result = verdict(rows, opening="170.0000", hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "running_balance_break"
    assert result.first_divergent_row == 2
    assert result.computed_closing == Decimal("100.0000")
    assert result.declared_closing == Decimal("95.0000")
    assert result.previous_balance == Decimal("120.0000")
    # Section 4.6 names five figures on a break, and these are the two a sign error would
    # silently invert. Nothing downstream recomputes them: money is formatted, never arithmetic.
    assert result.amount == Decimal("-20.0000")
    assert result.delta == Decimal("-5.0000")
    assert "2" in result.message


def test_the_terminal_running_balance_becomes_the_declared_closing() -> None:
    """A running-balance-only layout must not hand a null closing downstream."""
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-30.0000", balance="70.0000", day=3),
    ]
    result = verdict(rows, opening="170.0000", hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "matched"
    assert result.declared_closing == Decimal("70.0000")


def test_the_terminal_balance_is_the_chronologically_last_row_not_the_last_printed() -> None:
    rows = [
        row(1, "-30.0000", balance="70.0000", day=3),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-50.0000", balance="120.0000", day=1),
    ]
    result = verdict(
        rows,
        opening="170.0000",
        chronologicalOrder="descending",
        hasRunningBalance=True,
        closingBalancePattern=None,
    )
    assert result.status == "matched"
    assert result.declared_closing == Decimal("70.0000")


def test_a_running_balance_is_inverted_with_the_rest_of_the_frame() -> None:
    """A card layout's stated balances are owed amounts, like its declared anchors."""
    rows = [
        row(1, "-310.0000", balance="4810.0000", day=1),
    ]
    result = verdict(
        rows,
        opening="4,500.00",
        invertsSign=True,
        hasRunningBalance=True,
        closingBalancePattern=None,
    )
    assert result.status == "matched"
    assert result.declared_closing == Decimal("-4810.0000")


def test_a_gap_in_a_configured_running_balance_column_is_unavailable() -> None:
    """Section 4.6 asks every row-to-row step to hold, and a gap is a step that cannot be checked.

    Rows 2 and 3 are misread by 20 in opposite directions and cancel before row 4's balance, so
    every stated anchor holds and the total agrees while two rows are wrong. A walk that checked
    only where a balance was stated would call this `matched`. Physical continuation lines never
    reach here as rows of their own, so the gap is a shifted band, not a line to walk past.
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-40.0000", day=2),
        row(3, "-10.0000", day=3),
        row(4, "-30.0000", balance="40.0000", day=4),
    ]
    result = verdict(rows, opening="170.0000", hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "unavailable"
    assert "running-balance" in result.message
    assert "2 rows" in result.message and "row 2" in result.message


def test_the_gap_is_named_by_document_row_number_on_a_descending_statement() -> None:
    """The walk may reverse the rows; the row it names never follows the reversal."""
    rows = [
        row(9, "-30.0000", day=3),
        row(8, "-20.0000", balance="95.0000", day=2),
        row(7, "-50.0000", balance="120.0000", day=1),
    ]
    result = verdict(
        rows,
        opening="170.0000",
        hasRunningBalance=True,
        closingBalancePattern=None,
        chronologicalOrder="descending",
    )
    assert result.status == "unavailable"
    assert "row 9" in result.message


def test_a_running_balance_column_over_no_rows_at_all_is_unavailable() -> None:
    """With the column as the only closing evidence, no rows leaves nothing to reconcile against."""
    result = verdict([], hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "unavailable"
    assert "no rows" in result.message


def test_an_empty_month_still_reconciles_against_two_anchors_that_agree() -> None:
    """A mapped balance column must not change the verdict on a statement that has no rows.

    Both anchors extracted and agree, so the statement has proved itself; refusing it would
    send the owner to remap a layout that mapped correctly. An extraction that lost every row
    leaves the two anchors disagreeing instead, which blocks as a mismatch.
    """
    agreeing = verdict([], opening="1,250.00", closing="1,250.00", hasRunningBalance=True)
    assert agreeing.status == "matched"
    disagreeing = verdict([], opening="1,250.00", closing="900.00", hasRunningBalance=True)
    assert disagreeing.status == "mismatched"


# --- mismatched, and what evidence may be named ----------------------------


def test_a_closing_anchor_alone_never_names_a_divergent_row() -> None:
    """I7 forbids naming *the* divergent row when only a closing anchor exists.

    A row is missing from the extraction: the rows total 80 against a statement that declares
    100 spent. No row present can be the culprit — the culprit is the one that is absent — so
    there is nothing honest to name, and the message says row-level evidence is unavailable.
    """
    rows = [row(1, "-50.0000", day=1), row(3, "-30.0000", day=3)]
    result = verdict(rows, opening="170.0000", closing="70.0000")
    assert result.status == "mismatched"
    assert result.delta == Decimal("-20.0000")
    assert result.first_divergent_row is None
    assert result.candidate_rows == []
    assert "unavailable" in result.message


def test_a_dropped_row_names_its_surviving_twin_as_the_candidate() -> None:
    """Section 4.6's dropped-row case, and the reason `candidate_rows` exists.

    The statement charged two identical 20s and extraction kept one. The shortfall *is* the
    missing row's amount, so a surviving row carrying the same amount is the evidence of what
    went missing — and a corrupted amount leaves no such row, which is how the two are told
    apart.
    """
    rows = [row(1, "-50.0000", day=1), row(2, "-20.0000", day=2), row(3, "-30.0000", day=3)]
    result = verdict(rows, opening="170.0000", closing="50.0000")
    assert result.status == "mismatched"
    assert result.delta == Decimal("-20.0000")
    assert result.candidate_rows == [2]
    assert result.first_divergent_row is None
    assert "dropped" in result.message
    # Named rows are candidates, not the culprit: 4.6 requires the caveat alongside them.
    assert "unavailable" in result.message


def test_a_candidate_is_never_named_without_the_caveat_that_it_is_only_a_candidate() -> None:
    """The walk held, so the rows agree with the balance column and the closing anchor does not.

    Row 2 carries the difference, but the walk corroborated it — the missing row is one the
    statement never printed a balance beside. Naming row 2 without saying it is a candidate
    presents a guess as a finding, which is what `candidate_rows` exists to avoid (I7).
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
    ]
    result = verdict(rows, opening="170.0000", closing="80.0000", hasRunningBalance=True)
    assert result.status == "mismatched"
    assert result.candidate_rows == [2]
    assert result.first_divergent_row is None
    assert "unavailable" in result.message


def test_a_corrupted_amount_leaves_no_candidates() -> None:
    rows = [row(1, "-50.0000", day=1), row(2, "-23.0000", day=2), row(3, "-30.0000", day=3)]
    result = verdict(rows, opening="170.0000", closing="70.0000")
    assert result.status == "mismatched"
    assert result.candidate_rows == []
    assert result.first_divergent_row is None


def test_there_is_no_tolerance() -> None:
    rows = [row(1, "-100.0001", day=1)]
    result = verdict(rows, opening="170.0000", closing="70.0000")
    assert result.status == "mismatched"
    assert result.delta == Decimal("0.0001")


def test_a_configured_opening_that_did_not_extract_is_unavailable() -> None:
    """The check that stops every batch reconciling vacuously.

    Deriving the opening from the first stated balance makes the walk's first step hold by
    construction, so a shifted opening band would silently delete the one independent check a
    running-balance statement offers and report `matched`.
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
    ]
    result = verdict(
        rows,
        openingBalancePattern=r"Opening:\s*([0-9,.-]+)",
        hasRunningBalance=True,
        closingBalancePattern=None,
    )
    assert result.status == "unavailable"
    assert "opening" in result.message


def test_a_trailing_row_without_a_balance_blocks_instead_of_using_an_interior_anchor() -> None:
    """The last stated balance is not the closing balance when rows follow it.

    Comparing a whole-statement total against a mid-statement anchor blocks a valid statement,
    accuses an innocent row, and would record a figure the statement never declared on
    `import_batch`.
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-30.0000", day=3),
    ]
    result = verdict(rows, opening="170.0000", hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "unavailable"
    assert result.declared_closing is None
    # The generic "no closing balance" fallback would also block, but it would not say why.
    # Naming the row is what tells the owner which band to remap.
    assert "1 row" in result.message and "row 3" in result.message


def test_a_first_row_without_a_balance_blocks_before_an_opening_is_derived() -> None:
    """Deriving from a later balance while ignoring the amounts before it would skew the opening.

    Under a configured running-balance column the missing anchor blocks first and names the row
    that lost it, which is what tells the owner where to look.
    """
    rows = [
        row(1, "-50.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-30.0000", balance="70.0000", day=3),
    ]
    result = verdict(rows, hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "unavailable"
    assert "row 1" in result.message


# --- the layout with both anchors ------------------------------------------


def test_a_divergence_blocks_even_when_the_totals_agree() -> None:
    """The totals reconcile and the batch still blocks, which is the point.

    Two amounts misread in opposite directions sum to the right closing balance while putting
    both wrong figures in the ledger. The walk departing is the only evidence of it. Section 4.6
    scopes `running_balance_break` to a layout with no declared closing, so with one declared the
    statement-level figures are the verdict and the walk's row is the evidence — which is also
    the only way a `mismatched` can carry a divergent row at all.
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="95.0000", day=2),
        row(3, "-30.0000", balance="70.0000", day=3),
    ]
    result = verdict(rows, opening="170.0000", closing="70.0000", hasRunningBalance=True)
    assert result.status == "mismatched"
    assert result.first_divergent_row == 2
    assert result.declared_closing == Decimal("70.0000")
    assert result.computed_closing == Decimal("70.0000")


def test_a_declared_closing_that_agrees_still_matches_with_a_running_balance() -> None:
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-30.0000", balance="70.0000", day=3),
    ]
    result = verdict(rows, opening="170.0000", closing="70.0000", hasRunningBalance=True)
    assert result.status == "matched"


def test_the_walk_checks_every_step_and_not_only_the_first() -> None:
    """Stopping after the opening step would wave every later divergence through."""
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
        row(3, "-30.0000", balance="60.0000", day=3),
    ]
    result = verdict(rows, opening="170.0000", hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "running_balance_break"
    assert result.first_divergent_row == 3


def test_a_break_carries_the_previous_balance_and_the_amount() -> None:
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="95.0000", day=2),
    ]
    result = verdict(rows, opening="170.0000", hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "running_balance_break"
    assert result.previous_balance == Decimal("120.0000")
    assert "-20.0000" in result.message


def test_a_named_divergent_row_leaves_no_candidates() -> None:
    """The divergent row and the candidates are alternatives, never a pair.

    With the totals agreeing the delta is zero, and a candidate rule keyed on the amount would
    name every zero-amount row as a dropped row's twin — a guess presented as a finding.
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "0.0000", balance="120.0000", day=2),
        row(3, "-20.0000", balance="95.0000", day=3),
        row(4, "-30.0000", balance="70.0000", day=4),
    ]
    result = verdict(rows, opening="170.0000", closing="70.0000", hasRunningBalance=True)
    assert result.status == "mismatched"
    assert result.delta == Decimal(0)
    assert result.first_divergent_row == 3
    assert result.candidate_rows == []


def test_a_walk_that_holds_still_mismatches_against_a_disagreeing_closing() -> None:
    """A row lost after the last stated balance: nothing diverges and nothing can be named."""
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
    ]
    result = verdict(rows, opening="170.0000", closing="70.0000", hasRunningBalance=True)
    assert result.status == "mismatched"
    assert result.delta == Decimal("-30.0000")
    assert result.first_divergent_row is None
    assert result.candidate_rows == []
    assert result.previous_balance is None
    assert "never departs" in result.message


# --- unavailable -----------------------------------------------------------


def test_a_declared_closing_that_did_not_extract_is_unavailable() -> None:
    result = verdict([row(1, "-50.0000")], opening="170.0000", closing=None)
    assert result.status == "unavailable"
    assert "closing" in result.message


def test_a_configured_closing_that_did_not_extract_blocks_a_walkable_statement() -> None:
    """The guard the running-balance case needs, and the only case that proves it is there.

    Every row states a balance and the walk holds, so without the guard the terminal balance
    would stand in for the closing anchor and the batch would import `matched` — with the
    statement's own declared closing never once consulted (§4.6).
    """
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
    ]
    result = verdict(rows, opening="170.0000", closing=None, hasRunningBalance=True)
    assert result.status == "unavailable"
    assert "closing balance pattern" in result.message


def test_a_layout_declaring_no_anchor_at_all_has_nothing_to_reconcile_against() -> None:
    """`has_a_balance_anchor` forbids this layout, and reconcile is a pure function all the same.

    It never receives one from the database; it may receive one from a caller, and it must say
    so rather than reconcile against a closing balance it does not have.
    """
    result = verdict([row(1, "-50.0000")], opening="170.0000", closingBalancePattern=None)
    assert result.status == "unavailable"
    assert "no closing balance" in result.message


def test_a_running_balance_column_that_extracted_nothing_is_unavailable() -> None:
    """Asserted with a closing anchor present, so the generic fallback cannot mask the guard."""
    rows = [row(1, "-50.0000", day=1), row(2, "-50.0000", day=2)]
    result = verdict(rows, opening="170.0000", closing="70.0000", hasRunningBalance=True)
    assert result.status == "unavailable"
    assert "running-balance" in result.message


def test_an_unparseable_declared_balance_is_unavailable_not_mismatched() -> None:
    """A shifted band captures text, not a number; calling that a mismatch would blame the rows."""
    result = verdict([row(1, "-50.0000")], opening="170.0000", closing="Closing balance")
    assert result.status == "unavailable"


def test_a_missing_opening_with_no_running_balance_is_unavailable() -> None:
    result = verdict([row(1, "-50.0000")], closing="70.0000")
    assert result.status == "unavailable"
    assert "opening" in result.message


def test_a_missing_opening_is_derived_from_the_first_stated_balance() -> None:
    """Arithmetic on stated values, not an invented fact — and the message says so."""
    rows = [
        row(1, "-50.0000", balance="120.0000", day=1),
        row(2, "-20.0000", balance="100.0000", day=2),
    ]
    result = verdict(rows, hasRunningBalance=True, closingBalancePattern=None)
    assert result.status == "matched"
    assert result.declared_opening == Decimal("170.0000")
    assert result.opening_is_derived is True
    assert "derived" in result.message


# --- the empty statement ---------------------------------------------------


def test_a_statement_with_no_rows_reconciles_only_if_the_balances_agree() -> None:
    assert verdict([], opening="70.0000", closing="70.0000").status == "matched"
    assert verdict([], opening="170.0000", closing="70.0000").status == "mismatched"


def test_row_numbers_are_document_numbers_whatever_the_evaluation_order() -> None:
    """The walk may reverse the rows; the reported number never follows it."""
    rows = [
        row(7, "-30.0000", balance="70.0000", day=3),
        row(8, "-20.0000", balance="95.0000", day=2),
        row(9, "-50.0000", balance="120.0000", day=1),
    ]
    result = verdict(
        rows,
        opening="170.0000",
        chronologicalOrder="descending",
        hasRunningBalance=True,
        closingBalancePattern=None,
    )
    assert result.status == "running_balance_break"
    assert result.first_divergent_row == 8
