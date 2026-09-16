# api/oneledger/recurring.py
"""Phase 4.1 Task 3: recurring discovery (spec "Discovery proposes, the owner decides").

Pure over rows the route reads — no database, no I/O — so cadence detection is unit-tested
directly against hand-built rows. `routes/recurring.py` is the only caller: it reads reportable
spending rows for the household, hands them to `detect_candidates`, and does the household-scoped
writing itself.

Identity is `merchant_id` when classification resolved one, otherwise the normalized description
(`identity_for`) — the same rule for every caller, so a candidate's identity, a confirmed series'
identity and a dismissal's identity are always computed the same way and can be compared directly.
Amounts here are always the positive spend magnitude (`-amount_base` of a reportable row), the
same sign convention `routes/spending.py` and `routes/budgets.py` already use for a spending
figure — never the ledger's own signed `amount_base`.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from .money import CENT

# weekly/monthly/quarterly/annual gap tolerances in days (spec "Discovery proposes, the owner
# decides"): the median gap between occurrences must land in exactly one of these windows, and at
# least two thirds of all gaps must land in that same window, or nothing is proposed.
CADENCE_WINDOWS: dict[str, tuple[int, int]] = {
    "weekly": (5, 9),
    "monthly": (26, 35),
    "quarterly": (82, 98),
    "annual": (350, 380),
}

# A representative day count per cadence, for "next expected" and "overdue" — the midpoint of
# each window above, used only once a cadence is already established for a confirmed series.
CADENCE_DAYS: dict[str, int] = {"weekly": 7, "monthly": 30, "quarterly": 91, "annual": 365}

_MIN_OCCURRENCES = 3
# `recurring_series.label` is bounded at 120 characters by V56's own CHECK.
_LABEL_LIMIT = 120
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Identity:
    """Exactly one of `merchant_id` or `match_key` is set — the same shape
    `recurring_series_identity_shape` enforces in the database."""

    merchant_id: UUID | None
    match_key: str | None


def normalized_description(description_clean: str | None, description_raw: str) -> str:
    """`lower(regexp_replace(btrim(coalesce(description_clean, description_raw)), '\\s+', ' ',
    'g'))` — `description_clean` is nullable and every manually posted row leaves it null, so the
    fallback to `description_raw` (never null on a transaction row) is what makes this total."""
    text = description_clean if description_clean else description_raw
    return _WHITESPACE.sub(" ", text.strip()).lower()


def identity_for(
    merchant_id: UUID | None, description_clean: str | None, description_raw: str
) -> Identity:
    if merchant_id is not None:
        return Identity(merchant_id=merchant_id, match_key=None)
    return Identity(
        merchant_id=None, match_key=normalized_description(description_clean, description_raw)
    )


@dataclass(frozen=True)
class SpendRow:
    """One reportable spending row, as `routes/recurring.py` reads it from
    `v_transaction_reportable` — `amount_base` here is already flipped positive by the caller."""

    transaction_id: UUID
    booked_date: date
    amount_base: Decimal
    merchant_id: UUID | None
    merchant_display_name: str | None
    description_clean: str | None
    description_raw: str
    category_id: UUID | None


@dataclass(frozen=True)
class Candidate:
    identity: Identity
    label: str
    cadence: str
    occurrences: int
    first_seen_on: date
    last_seen_on: date
    last_amount_base: Decimal
    typical_amount_base: Decimal
    category_id: UUID | None
    transaction_ids: list[UUID]


def _median_amount(amounts: list[Decimal]) -> Decimal:
    """The median, fixed at four places half away from zero when it falls between two stored
    amounts — the same rounding `money.translate` uses, so a typical amount is always a value the
    ledger itself could have posted."""
    ordered = sorted(amounts)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return ((ordered[mid - 1] + ordered[mid]) / 2).quantize(CENT, rounding=ROUND_HALF_UP)


def _cadence_for_gaps(gaps: list[int]) -> str | None:
    """The one cadence whose window both contains the median gap and holds at least two thirds of
    every gap — a group fitting no window, or only barely fitting on the median while its other
    gaps scatter outside it, is irregular and proposes nothing (spec)."""
    median_gap = statistics.median(gaps)
    for cadence, (low, high) in CADENCE_WINDOWS.items():
        if not (low <= median_gap <= high):
            continue
        matched = sum(1 for gap in gaps if low <= gap <= high)
        if matched * 3 >= len(gaps) * 2:
            return cadence
    return None


def detect_candidates(rows: list[SpendRow]) -> list[Candidate]:
    """Group `rows` by identity and propose one candidate per group that clears the occurrence
    and cadence bar. Rows need not be sorted; each group is sorted internally by `booked_date`,
    with `transaction_id` as the tiebreak so two same-day rows order deterministically."""
    groups: dict[Identity, list[SpendRow]] = {}
    for row in rows:
        identity = identity_for(row.merchant_id, row.description_clean, row.description_raw)
        groups.setdefault(identity, []).append(row)

    candidates: list[Candidate] = []
    for identity, group_rows in groups.items():
        if len(group_rows) < _MIN_OCCURRENCES:
            continue
        ordered = sorted(group_rows, key=lambda r: (r.booked_date, r.transaction_id))
        gaps = [
            (later.booked_date - earlier.booked_date).days
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        ]
        cadence = _cadence_for_gaps(gaps)
        if cadence is None:
            continue

        latest = ordered[-1]
        label = (
            latest.merchant_display_name
            if identity.merchant_id is not None and latest.merchant_display_name
            else latest.description_raw
        )
        # `recurring_series.label` is bounded at 120 characters, and a raw description can be far
        # longer, so the proposal is trimmed to what the confirm form can actually post.
        label = label[:_LABEL_LIMIT]
        candidates.append(
            Candidate(
                identity=identity,
                label=label,
                cadence=cadence,
                occurrences=len(ordered),
                first_seen_on=ordered[0].booked_date,
                last_seen_on=latest.booked_date,
                last_amount_base=latest.amount_base,
                typical_amount_base=_median_amount([r.amount_base for r in ordered]),
                category_id=latest.category_id,
                transaction_ids=[r.transaction_id for r in ordered],
            )
        )
    return candidates


def next_expected_on(last_seen_on: date, cadence: str) -> date:
    return last_seen_on + timedelta(days=CADENCE_DAYS[cadence])


def is_overdue(last_seen_on: date, cadence: str, *, today: date) -> bool:
    """More than half a cadence past the next expected date (spec)."""
    days_late = (today - next_expected_on(last_seen_on, cadence)).days
    return days_late > CADENCE_DAYS[cadence] // 2
