"""M2 Task 4c: reconciliation — Invariant I8's blocking check.

The specification's section 4.6. Pure functions: no database and no network, so a doomed batch is
recognised before anything is fetched or written.

Two rules carry the whole module, and each produces a batch that is internally consistent and
entirely wrong when half-applied:

**One frame.** `normalize.py` applies `invertsSign` to a row's *amount* and leaves the statement's
own balances verbatim — the declared anchors arrive as extracted text, and `running_balance` is
the figure the statement printed. So this module inverts the balances and never the amounts, and
the identity is evaluated with both sides in the ledger's frame.

**Chronological order.** `balanceₙ = balanceₙ₋₁ + amountₙ` reads the *previous chronological* row.
On a descending statement, walking in display order flags every valid row as a break. Reported row
numbers are always document row numbers, whichever direction the walk ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from oneledger.ingest.normalize import NormalizedRow, parse_amount

STATUS_MATCHED = "matched"
STATUS_MISMATCHED = "mismatched"
STATUS_RUNNING_BALANCE_BREAK = "running_balance_break"
STATUS_UNAVAILABLE = "unavailable"

_DERIVED = (
    " The opening balance was derived from the first row's stated balance less its own amount, "
    "not extracted from the statement."
)

_EVIDENCE_UNAVAILABLE = (
    "row-level evidence is unavailable: with a closing balance alone, any single row could "
    "absorb the difference, so none can be named"
)


class ReconcilableProfile(Protocol):
    """Exactly the five profile semantics this module reads, and nothing else.

    Structural rather than inherited: the layout model satisfies it without this module
    importing the route that defines it, so reconciliation stays testable on values.
    """

    @property
    def invertsSign(self) -> bool: ...

    @property
    def chronologicalOrder(self) -> str: ...

    @property
    def openingBalancePattern(self) -> str | None: ...

    @property
    def closingBalancePattern(self) -> str | None: ...

    @property
    def hasRunningBalance(self) -> bool: ...


@dataclass(frozen=True)
class Verdict:
    """Exactly one verdict per statement, with the evidence a reviewer needs to act on it.

    `declared_closing` is never null on a verdict that reached the identity: for a
    running-balance-only layout the chronologically last stated balance plays that role, because
    staging records it on `import_batch` and the commit verifies against it (section 4.9).

    `opening_is_derived` travels with the value because `declared_opening` reaches
    `import_batch.declared_opening_balance`, a column with no companion flag. A number that was
    computed must not arrive in the field a declared number arrives in without saying so (I7) —
    the same reason `StatementPeriod` carries `is_derived`.

    `previous_balance` is the balance the walk held before the divergent row, which section 4.6
    names among what a `running_balance_break` carries.
    """

    status: str
    declared_opening: Decimal | None
    opening_is_derived: bool
    declared_closing: Decimal | None
    computed_closing: Decimal | None
    delta: Decimal | None
    first_divergent_row: int | None
    previous_balance: Decimal | None
    candidate_rows: list[int]
    message: str


def reconcile(
    profile: ReconcilableProfile,
    rows: list[NormalizedRow],
    *,
    declared_opening: str | None,
    declared_closing: str | None,
) -> Verdict:
    """Evaluate `opening + Σ amounts == closing` and say what the answer is evidence of."""
    invert = profile.invertsSign
    chronological = (
        list(reversed(rows)) if profile.chronologicalOrder == "descending" else list(rows)
    )

    opening = _framed(declared_opening, invert)
    closing = _framed(declared_closing, invert)

    # A configured anchor that did not extract is a shifted band or a changed label, never "this
    # statement has no balances" — and letting it through would commit rows that bypassed
    # reconciliation entirely (section 4.6). Both anchors are checked, not only the closing one:
    # a configured opening silently replaced by a derived one deletes the single independent
    # check a running-balance statement offers, and every batch then reconciles vacuously.
    if profile.closingBalancePattern is not None and closing is None:
        return _unavailable(
            "the layout declares a closing balance pattern and it did not extract a number; "
            "remap the layout rather than importing rows that bypassed reconciliation"
        )
    if profile.openingBalancePattern is not None and opening is None:
        return _unavailable(
            "the layout declares an opening balance pattern and it did not extract a number; "
            "remap the layout rather than reconciling against a balance derived from the rows"
        )

    stated = [(r, _frame(r.running_balance, invert)) for r in chronological]
    walkable = profile.hasRunningBalance and any(balance is not None for _, balance in stated)
    if profile.hasRunningBalance and not walkable:
        return _unavailable(
            "the layout declares a running-balance column and no row carried one; remap the "
            "layout rather than importing rows that bypassed reconciliation"
        )

    derived = False
    if opening is None:
        if not walkable:
            return _unavailable(
                "no opening balance extracted and no running-balance column to derive one from"
            )
        opening = _derived_opening(stated)
        if opening is None:
            return _unavailable(
                "no opening balance extracted, and the first row states no balance to derive "
                "one from without guessing at the amounts before it"
            )
        derived = True

    divergent: tuple[NormalizedRow, Decimal, Decimal] | None = None
    if walkable:
        last_row, last_balance = stated[-1]
        if last_balance is None and closing is None:
            return _unavailable(
                f"the chronologically last row ({last_row.source_row_number}) states no balance "
                "and no closing balance extracted, so the statement declares no closing figure"
            )
        divergent = _walk(opening, stated)
        if closing is None:
            closing = last_balance

    if closing is None:
        return _unavailable("no closing balance to reconcile against")

    computed = opening + sum((r.amount for r in rows), Decimal(0))

    # Section 4.6 scopes `running_balance_break` to a layout with a running balance and **no**
    # declared closing. Where a closing was declared, the statement-level figures are the verdict
    # and the walk's row is the evidence — which is the only way `mismatched` can carry a
    # divergent row at all.
    if divergent is not None and profile.closingBalancePattern is None:
        row, previous, running = divergent
        stated_balance = _frame(row.running_balance, invert)
        return _verdict(
            STATUS_RUNNING_BALANCE_BREAK,
            opening,
            derived,
            stated_balance,
            running,
            (stated_balance - running) if stated_balance is not None else None,
            row.source_row_number,
            previous,
            [],
            f"Row {row.source_row_number} states a balance of {stated_balance}, but the previous "
            f"balance of {previous} plus its amount of {row.amount} gives {running}.",
        )

    if computed == closing and divergent is None:
        return _verdict(
            STATUS_MATCHED,
            opening,
            derived,
            closing,
            computed,
            Decimal(0),
            None,
            None,
            [],
            f"Opening {opening} plus {len(rows)} rows reconciles to {closing}.",
        )

    # A divergence blocks even when the totals agree. Two amounts misread in opposite
    # directions sum to the right closing balance while putting both wrong figures in the
    # ledger, and the walk departing is the only evidence of it — exactly the batch that is
    # internally consistent and entirely wrong.
    delta = closing - computed
    # Section 4.6's dropped-row case: extraction lost a row, so the shortfall *is* that row's
    # amount, and a surviving row carrying the same amount is the evidence of what went missing.
    # A corrupted amount leaves no such row, which is how the two are told apart.
    candidates = list(dict.fromkeys(r.source_row_number for r in rows if r.amount == delta))
    first_divergent_row = divergent[0].source_row_number if divergent is not None else None
    return _verdict(
        STATUS_MISMATCHED,
        opening,
        derived,
        closing,
        computed,
        delta,
        first_divergent_row,
        None,
        candidates,
        f"Opening {opening} plus {len(rows)} rows computes {computed}, but the statement "
        f"declares {closing}: a difference of {delta}. "
        + _evidence(candidates, first_divergent_row, walkable),
    )


def _evidence(candidates: list[int], divergent: int | None, walkable: bool) -> str:
    if divergent is not None:
        return (
            f"The running balance first departs from the amounts at row {divergent}. Where the "
            "totals nonetheless agree, two amounts misread in opposite directions would do "
            "exactly that, so the rows are not trustworthy on the strength of the total alone."
        )
    if candidates:
        return (
            f"Rows {candidates} each carry exactly that amount; an identical row may have been "
            "dropped in extraction."
        )
    if walkable:
        return "No row carries the difference and the running balance never departs."
    return _EVIDENCE_UNAVAILABLE


def _derived_opening(stated: list[tuple[NormalizedRow, Decimal | None]]) -> Decimal | None:
    """The opening implied by the first stated balance, or None if it cannot be had honestly.

    Only the *first chronological* row's balance implies the opening directly. A balance further
    down implies it only after subtracting every amount before it — and if any of those rows is
    itself a continuation with no balance, the subtraction is arithmetic on stated values, not a
    guess. What cannot be done is ignoring them, which skews the opening by their sum.
    """
    row, balance = stated[0]
    if balance is None:
        return None
    return balance - row.amount


def _walk(
    opening: Decimal, stated: list[tuple[NormalizedRow, Decimal | None]]
) -> tuple[NormalizedRow, Decimal, Decimal] | None:
    """Step the running balance chronologically; return the first divergence, or None.

    Returns the row, the balance held before it and the balance its amount implies — section
    4.6 names all three among what a break carries.

    A row that carried no balance still moves the running total, and the walk keeps checking
    after it: a continuation row states no balance of its own, and either skipping its amount or
    stopping at it would hide every divergence downstream.
    """
    running = opening
    for row, balance in stated:
        previous = running
        running += row.amount
        if balance is None:
            continue
        if balance != running:
            return row, previous, running
    return None


def _framed(text: str | None, invert: bool) -> Decimal | None:
    """Parse an extracted balance into the ledger's frame. Unparseable text is not a number."""
    return _frame(parse_amount(text) if text else None, invert)


def _frame(value: Decimal | None, invert: bool) -> Decimal | None:
    if value is None:
        return None
    return -value if invert else value


def _verdict(
    status: str,
    opening: Decimal | None,
    derived: bool,
    closing: Decimal | None,
    computed: Decimal | None,
    delta: Decimal | None,
    divergent_row: int | None,
    previous: Decimal | None,
    candidates: list[int],
    message: str,
) -> Verdict:
    """Assemble a verdict, appending the derived-opening label to every message that needs it."""
    return Verdict(
        status,
        opening,
        derived,
        closing,
        computed,
        delta,
        divergent_row,
        previous,
        candidates,
        message + (_DERIVED if derived else ""),
    )


def _unavailable(message: str) -> Verdict:
    return Verdict(STATUS_UNAVAILABLE, None, False, None, None, None, None, None, [], message)
