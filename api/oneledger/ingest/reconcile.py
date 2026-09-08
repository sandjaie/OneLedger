"""M2 Task 4c: reconciliation — Invariant I8's blocking check.

The specification's section 4.6. Pure functions: no database and no network, so a doomed batch is
recognised before anything is fetched or written.

Two rules carry the whole module, and each produces a batch that is internally consistent and
entirely wrong when half-applied:

**One frame.** `normalize.py` applies `invertsSign` to a row's *amount* and leaves the statement's
own balances in the statement's frame, signed by their own `Dr`/`Cr` suffix where they carry one.
So this module frames the balances and never the amounts — and it reframes only a balance that
stated no direction of its own, because a figure that said which way it goes has nothing left to
invert. The identity is then evaluated with both sides in the ledger's frame.

**Chronological order.** `balanceₙ = balanceₙ₋₁ + amountₙ` reads the *previous chronological* row.
On a descending statement, walking in display order flags every valid row as a break. Reported row
numbers are always document row numbers, whichever direction the walk ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from oneledger.ingest.normalize import (
    NormalizedRow,
    SignedAmount,
    frame_balance,
    parse_balance,
)

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

_EVIDENCE_WALK_HELD = (
    "row-level evidence is unavailable: the running balance never departs, so every row listed "
    "agrees with the statement's own balance column and the difference is against the declared "
    "closing alone — none of them can be named as its cause"
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

    `previous_balance` is the balance the walk held before the divergent row and `amount` is that
    row's own amount; section 4.6 names both among what a `running_balance_break` carries, and a
    consumer cannot derive the amount from the other fields because TypeScript does no arithmetic
    on money.

    **On a `running_balance_break` the three closing fields are row-level, not statement-level**:
    `declared_closing` is the divergent row's stated balance, `computed_closing` the balance its
    amount implies, and `delta` their difference — section 4.6 says a break carries "the previous
    balance, the amount and both the expected and stated balances", and a statement whose own
    balance column contradicts its amounts has no trustworthy closing figure to report instead. A
    consumer rendering these must read the status first.
    """

    status: str
    declared_opening: Decimal | None
    opening_is_derived: bool
    declared_closing: Decimal | None
    computed_closing: Decimal | None
    delta: Decimal | None
    first_divergent_row: int | None
    previous_balance: Decimal | None
    amount: Decimal | None
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

    # Section 4.6 requires *every* row-to-row step to hold, so a row carrying no balance under a
    # configured running-balance column is evidence that failed to extract, not a row to walk
    # past. A physical continuation line never arrives here as a row of its own — `extract_pages`
    # merges it into the row above — so a gap is a shifted band or a parse regression. Walking
    # past one lets two amounts misread in opposite directions cancel before the next stated
    # balance and reports `matched` for rows that are wrong.
    walk: list[tuple[NormalizedRow, Decimal]] = []
    if profile.hasRunningBalance:
        stated = [(r, _framed_balance(r, invert)) for r in chronological]
        unstated = [r.source_row_number for r, balance in stated if balance is None]
        if unstated:
            return _unavailable(
                f"the layout declares a running-balance column and {_rows(len(unstated))} "
                f"carried no balance, the first of them row {unstated[0]} in chronological "
                "order; remap the layout rather than importing rows that bypassed reconciliation"
            )
        if not stated and profile.closingBalancePattern is None:
            # Only where the column is the whole of the closing evidence. Where a closing anchor
            # extracted too, a month with no transactions has already proved itself against two
            # independent figures, and an extraction that lost every row leaves them disagreeing
            # — so the identity says more here than a refusal that sends the owner to remap a
            # layout that mapped correctly.
            return _unavailable(
                "the layout declares a running-balance column and the statement has no rows to "
                "walk; remap the layout rather than importing rows that bypassed reconciliation"
            )
        walk = [(r, balance) for r, balance in stated if balance is not None]

    derived = False
    if opening is None:
        if not walk:
            # Since the zero-row fall-through above, an empty walk means either of two things,
            # and a message naming the wrong one sends the owner to remap a mapped column.
            missing = (
                "the statement carried no rows to derive one from"
                if profile.hasRunningBalance
                else "no running-balance column to derive one from"
            )
            return _unavailable(f"no opening balance extracted and {missing}")
        # The first chronological row's stated balance less its own amount. Only that row
        # implies the opening directly: a balance further down implies it only after
        # subtracting every amount before it, and ignoring those skews it by their sum.
        first_row, first_balance = walk[0]
        opening = first_balance - first_row.amount
        derived = True

    divergent: tuple[NormalizedRow, Decimal, Decimal, Decimal] | None = None
    if walk:
        divergent = _walk(opening, walk)
        if closing is None:
            closing = walk[-1][1]

    if closing is None:
        # No saved layout reaches this: V12's `has_a_balance_anchor` CHECK means every one
        # declares a closing pattern or a running-balance column, and a draft preview with
        # neither is stopped by the opening guard above. It stays because this is a pure
        # function over values — a caller may hand it a profile Postgres never saw — not
        # because there is an input that produces it.
        return _unavailable("no closing balance to reconcile against")

    computed = opening + sum((r.amount for r in rows), Decimal(0))

    # Section 4.6 scopes `running_balance_break` to a layout with a running balance and **no**
    # declared closing. Where a closing was declared, the statement-level figures are the verdict
    # and the walk's row is the evidence — which is the only way `mismatched` can carry a
    # divergent row at all.
    if divergent is not None and profile.closingBalancePattern is None:
        row, previous, running, stated_balance = divergent
        return _verdict(
            STATUS_RUNNING_BALANCE_BREAK,
            opening,
            derived,
            stated_balance,
            running,
            stated_balance - running,
            row.source_row_number,
            previous,
            row.amount,
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
            # Exactly zero, computed the way every other verdict computes it: a literal would
            # carry no decimal places, and `delta` reaches the wire as `str(value)`.
            closing - computed,
            None,
            None,
            None,
            [],
            f"Opening {opening} plus {_rows(len(rows))} reconciles to {closing}.",
        )

    # A divergence blocks even when the totals agree. Two amounts misread in opposite
    # directions sum to the right closing balance while putting both wrong figures in the
    # ledger, and the walk departing is the only evidence of it — exactly the batch that is
    # internally consistent and entirely wrong.
    delta = closing - computed
    # Section 4.6's dropped-row case: extraction lost a row, so the shortfall *is* that row's
    # amount, and a surviving row carrying the same amount is the evidence of what went missing.
    # A corrupted amount leaves no such row, which is how the two are told apart.
    # Section 4.6 offers the divergent row *or* the candidates, never both: where the walk
    # named a row, a candidate list is a second story about the same discrepancy. It would also
    # name every zero-amount row whenever the delta is zero, which is a guess, not a finding.
    candidates = (
        [] if divergent is not None else [r.source_row_number for r in rows if r.amount == delta]
    )
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
        None,
        candidates,
        f"Opening {opening} plus {_rows(len(rows))} computes {computed}, but the statement "
        f"declares {closing}: a difference of {delta}. "
        + _evidence(candidates, first_divergent_row, bool(walk)),
    )


def _rows(count: int) -> str:
    return "1 row" if count == 1 else f"{count} rows"


def _evidence(candidates: list[int], divergent: int | None, walkable: bool) -> str:
    if divergent is not None:
        return (
            f"The running balance first departs from the amounts at row {divergent}. Where the "
            "totals nonetheless agree, two amounts misread in opposite directions would do "
            "exactly that, so the rows are not trustworthy on the strength of the total alone."
        )
    if candidates:
        named = ", ".join(str(number) for number in candidates)
        carries = "carries" if len(candidates) == 1 else "each carry"
        dropped = (
            f"Row {named} {carries} exactly that amount; an identical row may have been dropped "
            "in extraction."
        )
        # Section 4.6 attaches both to this case: the candidates, and the statement that none of
        # them can be named as *the* divergent row. Which reason applies depends on the evidence
        # the layout carries, so the caveat says which — but it is always attached, because a
        # candidate presented without it reads as a finding.
        caveat = _EVIDENCE_WALK_HELD if walkable else _EVIDENCE_UNAVAILABLE
        return f"{dropped} Otherwise, {caveat}"
    if walkable:
        return "No row carries the difference and the running balance never departs."
    return _EVIDENCE_UNAVAILABLE


def _walk(
    opening: Decimal, stated: list[tuple[NormalizedRow, Decimal]]
) -> tuple[NormalizedRow, Decimal, Decimal, Decimal] | None:
    """Step the running balance chronologically; return the first divergence, or None.

    Returns the row, the balance held before it and the balance its amount implies — section
    4.6 names all three among what a break carries. Every row here states a balance, so every
    step is checked, which is what section 4.6 asks for.
    """
    running = opening
    for row, balance in stated:
        previous = running
        running += row.amount
        if balance != running:
            return row, previous, running, balance
    return None


def _framed(text: str | None, invert: bool) -> Decimal | None:
    """Parse a declared anchor into the ledger's frame. Unparseable text is not a number.

    A `Dr`/`Cr` suffix states the balance's own direction, so it is read here and the profile's
    inversion is then withheld — applying both would reverse the statement rather than reframe
    it, which is the rule `normalize.py` already applies to a row that stated its direction.
    """
    balance = parse_balance(text) if text else None
    return None if balance is None else frame_balance(balance, invert)


def _framed_balance(row: NormalizedRow, invert: bool) -> Decimal | None:
    """One row's stated balance in the ledger's frame, under the same rule as a declared anchor."""
    if row.running_balance is None:
        return None
    return frame_balance(SignedAmount(row.running_balance, row.balance_direction_is_stated), invert)


def _verdict(
    status: str,
    opening: Decimal | None,
    derived: bool,
    closing: Decimal | None,
    computed: Decimal | None,
    delta: Decimal | None,
    divergent_row: int | None,
    previous: Decimal | None,
    amount: Decimal | None,
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
        amount,
        candidates,
        message + (_DERIVED if derived else ""),
    )


def _unavailable(message: str) -> Verdict:
    return Verdict(STATUS_UNAVAILABLE, None, False, None, None, None, None, None, None, [], message)
