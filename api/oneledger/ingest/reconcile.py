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
    """

    status: str
    declared_opening: Decimal | None
    declared_closing: Decimal | None
    computed_closing: Decimal | None
    delta: Decimal | None
    first_divergent_row: int | None
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

    if profile.closingBalancePattern is not None and closing is None:
        return _unavailable(
            "the layout declares a closing balance pattern and it did not extract a number; "
            "remap the layout rather than importing rows that bypassed reconciliation"
        )

    stated = [(r, _frame(r.running_balance, invert)) for r in chronological]
    walkable = profile.hasRunningBalance and any(balance is not None for _, balance in stated)
    if profile.hasRunningBalance and not walkable:
        return _unavailable(
            "the layout declares a running-balance column and no row carried one; remap the "
            "layout rather than importing rows that bypassed reconciliation"
        )

    derived = ""
    if opening is None:
        if not walkable:
            return _unavailable(
                "no opening balance extracted and no running-balance column to derive one from"
            )
        first, first_balance = next((r, b) for r, b in stated if b is not None)
        opening = first_balance - first.amount
        derived = (
            f" The opening balance was derived from row {first.source_row_number}'s stated "
            f"balance less its own amount, not extracted."
        )

    if walkable:
        broken = _walk(opening, stated)
        if broken is not None:
            return broken
        closing = closing if closing is not None else _terminal(stated)

    computed = opening + sum((r.amount for r in rows), Decimal(0))
    if closing is None:
        return _unavailable("no closing balance to reconcile against")

    if computed == closing:
        return Verdict(
            STATUS_MATCHED,
            opening,
            closing,
            computed,
            Decimal(0),
            None,
            [],
            f"Opening {opening} plus {len(rows)} rows reconciles to {closing}.{derived}",
        )

    delta = closing - computed
    # A row whose amount is exactly the shortfall's opposite is one that, removed, closes the
    # gap. A row missing from the extraction cannot be named at all — it is not here to name.
    candidates = [r.source_row_number for r in rows if r.amount == -delta]
    return Verdict(
        STATUS_MISMATCHED,
        opening,
        closing,
        computed,
        delta,
        None,
        candidates,
        f"Opening {opening} plus {len(rows)} rows computes {computed}, but the statement "
        f"declares {closing}: a difference of {delta}. "
        + (
            f"Rows {candidates} each carry that difference and may be one too many."
            if candidates
            else _EVIDENCE_UNAVAILABLE
        )
        + derived,
    )


def _walk(opening: Decimal, stated: list[tuple[NormalizedRow, Decimal | None]]) -> Verdict | None:
    """Step the running balance chronologically; return a break verdict, or None if it holds.

    A row that carried no balance still moves the running total — a continuation row states no
    balance of its own, and skipping its amount would break every row after it.
    """
    running = opening
    for row, balance in stated:
        running += row.amount
        if balance is None:
            continue
        if balance != running:
            return Verdict(
                STATUS_RUNNING_BALANCE_BREAK,
                opening,
                balance,
                running,
                balance - running,
                row.source_row_number,
                [],
                f"Row {row.source_row_number} states a balance of {balance}, but the previous "
                f"balance plus its amount of {row.amount} gives {running}.",
            )
    return None


def _terminal(stated: list[tuple[NormalizedRow, Decimal | None]]) -> Decimal:
    """The chronologically last stated balance, which plays the declared-closing role."""
    return next(balance for _, balance in reversed(stated) if balance is not None)


def _framed(text: str | None, invert: bool) -> Decimal | None:
    """Parse an extracted balance into the ledger's frame. Unparseable text is not a number."""
    return _frame(parse_amount(text) if text else None, invert)


def _frame(value: Decimal | None, invert: bool) -> Decimal | None:
    if value is None:
        return None
    return -value if invert else value


def _unavailable(message: str) -> Verdict:
    return Verdict(STATUS_UNAVAILABLE, None, None, None, None, None, [], message)
