# api/oneledger/recurring.py
"""Phase 4.1 Task 1: recurring discovery, opt-out (spec "Recurring payments are simply just not
a subscription", `docs/superpowers/plans/2026-09-16-phase-4-1-subscriptions.md` Task 1).

Pure over rows the route reads — no database, no I/O — so cadence detection and clustering are
unit-tested directly against hand-built rows. `routes/recurring.py` is the only caller: it reads
reportable spending rows for the household, hands them to `detect_candidates`, and does every
household-scoped write itself.

Identity is `merchant_id` when classification resolved one, otherwise the normalized description
(`identity_for`) — the same rule every caller uses, so a candidate's identity, an open series'
identity and a dismissal's identity are always computed the same way and can be compared
directly.

`SpendRow.amount`/`.currency` are the charge's own original pair — `original_amount`/
`original_currency` when the statement recorded one, otherwise `amount`/`currency`, always a
positive magnitude (the global execution contract's own rule, `abs(coalesce(original_amount,
amount))` with `coalesce(original_currency, currency)`) — never the household's base-currency
`amount_base`, which the route reads separately for `expected_amount_base`."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from uuid import UUID

from .money import CENT

# weekly/monthly/quarterly/semiannual/annual gap tolerances in days: the median gap between
# occurrences must land in exactly one of these windows, and at least two thirds of all gaps must
# land in that same window, or nothing is proposed. 'custom' has no window: detection never
# proposes it, only `POST /recurring/from-transaction` (Task 2) sets it, from the owner's own
# input.
CADENCE_WINDOWS: dict[str, tuple[int, int]] = {
    "weekly": (5, 9),
    "monthly": (26, 35),
    "quarterly": (82, 98),
    "semiannual": (175, 190),
    "annual": (350, 380),
}

# A representative day count per cadence, for "next expected" and "overdue" — the midpoint of
# each window above, used once a cadence is already established for an open series.
CADENCE_DAYS: dict[str, int] = {
    "weekly": 7,
    "monthly": 30,
    "quarterly": 91,
    "semiannual": 182,
    "annual": 365,
}

_MIN_OCCURRENCES = 3
# `recurring_series.label` is bounded at 120 characters by V56's own CHECK.
_LABEL_LIMIT = 120
_WHITESPACE = re.compile(r"\s+")

# A charge joins the cluster whose current median is within this band of it (spec "one
# descriptor, several subscriptions").
_CLUSTER_BAND = Decimal("0.2")
# Every charge within this band of the group's median is what "a fixed price" means for the
# subscription flag's amount-stability rule (spec).
_STABILITY_BAND = Decimal("0.05")


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
    `v_transaction_reportable` — `amount_base` here is already flipped positive by the caller,
    and `amount`/`currency` are the row's own original pair, likewise flipped positive."""

    transaction_id: UUID
    booked_date: date
    amount_base: Decimal
    amount: Decimal
    currency: str
    merchant_id: UUID | None
    merchant_display_name: str | None
    description_clean: str | None
    description_raw: str
    category_id: UUID | None
    # Already a member of some series. Such a charge still takes part in clustering, so the
    # groups a second refresh forms are the groups the first one formed, but a group holding any
    # such charge is never proposed again (idempotent refresh).
    in_series: bool = False


@dataclass(frozen=True)
class Candidate:
    """One detected group: an identity plus a currency plus an amount cluster (spec "one
    descriptor, several subscriptions" — a series is never an identity alone)."""

    identity: Identity
    currency: str
    label: str
    cadence: str
    occurrences: int
    first_seen_on: date
    last_seen_on: date
    native_amount: Decimal
    typical_amount: Decimal
    amounts_are_stable: bool
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


def amounts_are_stable(amounts: list[Decimal]) -> bool:
    """Every amount within 5% of the group's median (spec "a fixed price is what a subscription
    looks like") — the amount half of the deterministic subscription-flag rule, read both from a
    freshly detected candidate and, in `routes/recurring.py`'s refresh, from an open series' own
    members when no category settles the flag."""
    if not amounts:
        return True
    median = _median_amount(amounts)
    return all(abs(amount - median) <= _STABILITY_BAND * median for amount in amounts)


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


def _candidate_from_group(
    identity: Identity, currency: str, rows: list[SpendRow]
) -> Candidate | None:
    """`rows` already share `identity` and `currency`. Applies the occurrence, median-gap and
    two-thirds rules a resulting cluster (or, in the fallback, a whole identity) must clear."""
    if len(rows) < _MIN_OCCURRENCES:
        return None
    ordered = sorted(rows, key=lambda r: (r.booked_date, r.transaction_id))
    gaps = [
        (later.booked_date - earlier.booked_date).days
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    ]
    cadence = _cadence_for_gaps(gaps)
    if cadence is None:
        return None

    latest = ordered[-1]
    label = (
        latest.merchant_display_name
        if identity.merchant_id is not None and latest.merchant_display_name
        else latest.description_raw
    )
    # `recurring_series.label` is bounded at 120 characters, and a raw description can be far
    # longer, so the proposal is trimmed to what the series can actually store.
    label = label[:_LABEL_LIMIT]
    amounts = [row.amount for row in ordered]
    return Candidate(
        identity=identity,
        currency=currency,
        label=label,
        cadence=cadence,
        occurrences=len(ordered),
        first_seen_on=ordered[0].booked_date,
        last_seen_on=latest.booked_date,
        native_amount=latest.amount,
        typical_amount=_median_amount(amounts),
        amounts_are_stable=amounts_are_stable(amounts),
        category_id=latest.category_id,
        transaction_ids=[row.transaction_id for row in ordered],
    )


@dataclass
class _Cluster:
    created_order: int
    rows: list[SpendRow] = field(default_factory=list)

    @property
    def first(self) -> date:
        return self.rows[0].booked_date

    @property
    def last(self) -> date:
        return self.rows[-1].booked_date

    @property
    def median(self) -> Decimal:
        return _median_amount([row.amount for row in self.rows])

    @property
    def has_members(self) -> bool:
        return any(row.in_series for row in self.rows)


def _overlaps(a: _Cluster, b: _Cluster) -> bool:
    """Two date ranges share at least one day (spec: charged at the same time, so two concurrent
    subscriptions rather than one whose price changed)."""
    return a.first <= b.last and b.first <= a.last


def _cluster_rows(rows: list[SpendRow]) -> list[_Cluster]:
    """`rows` already share one identity and currency. Processed in `booked_date`, then
    `transaction_id` order (deterministic regardless of the caller's own row order): each row
    joins the cluster whose current median is nearest and within 20%, ties broken by the
    earlier-created cluster; otherwise it starts a new one (spec "one descriptor, several
    subscriptions")."""
    ordered = sorted(rows, key=lambda r: (r.booked_date, r.transaction_id))
    clusters: list[_Cluster] = []
    for row in ordered:
        nearest: tuple[Decimal, int] | None = None
        for index, cluster in enumerate(clusters):
            median = cluster.median
            distance = abs(row.amount - median)
            if distance <= _CLUSTER_BAND * median and (nearest is None or distance < nearest[0]):
                nearest = (distance, index)
        if nearest is not None:
            clusters[nearest[1]].rows.append(row)
        else:
            clusters.append(_Cluster(created_order=len(clusters), rows=[row]))
    return clusters


def _merge_sequential(clusters: list[_Cluster]) -> list[_Cluster]:
    """Walking clusters by first date: one that overlaps no other merges into the earlier,
    non-overlapping group whose last charge is latest, then whose amount is nearest — one series
    whose price changed. Overlapping clusters stay apart — concurrent subscriptions (spec)."""
    by_first = sorted(clusters, key=lambda c: (c.first, c.created_order))
    groups: list[_Cluster] = []
    for cluster in by_first:
        if any(_overlaps(cluster, other) for other in clusters if other is not cluster):
            groups.append(cluster)
            continue
        # Only clusters of the same kind merge. A run of new charges sequential to a series'
        # existing members is that series' price change, which is Extend's decision; when Extend
        # did not take them (the series was removed, or the timing did not fit), they must stand as
        # their own group rather than disappear into a group that is already a series.
        earlier = [
            group
            for group in groups
            if not _overlaps(cluster, group) and group.has_members == cluster.has_members
        ]
        if not earlier:
            groups.append(cluster)
            continue
        earlier.sort(
            key=lambda group: (-group.last.toordinal(), abs(cluster.median - group.median))
        )
        target = earlier[0]
        target.rows = sorted(
            target.rows + cluster.rows, key=lambda r: (r.booked_date, r.transaction_id)
        )
    return groups


def _unassigned(rows: list[SpendRow]) -> bool:
    return not any(row.in_series for row in rows)


def detect_candidates(rows: list[SpendRow]) -> list[Candidate]:
    """Group `rows` by identity, then by currency, cluster and merge each currency's rows, and
    propose one candidate per resulting group that clears the occurrence and cadence bar.
    Fallback: when nothing on an identity, in any of its currencies, clears that bar, each
    currency's full row set is evaluated as one group instead, so a bill whose amount varies more
    than 20% is still detected (spec).

    `rows` includes charges already in a series (`in_series`). They cluster like any other charge,
    and a group that clears the bar counts as found even when it is already a series, so the
    fallback does not fire on an identity that already has one. Only a group made entirely of
    unassigned charges is proposed. Without this, a second refresh saw only the leftovers: one-off
    purchases that had overlapped a subscription's cluster overlapped nothing once that cluster
    became members, merged together, and were proposed as a false monthly payment."""
    by_identity: dict[Identity, list[SpendRow]] = {}
    for row in rows:
        identity = identity_for(row.merchant_id, row.description_clean, row.description_raw)
        by_identity.setdefault(identity, []).append(row)

    candidates: list[Candidate] = []
    for identity, identity_rows in by_identity.items():
        by_currency: dict[str, list[SpendRow]] = {}
        for row in identity_rows:
            by_currency.setdefault(row.currency, []).append(row)

        found_by_currency: list[tuple[str, list[tuple[list[SpendRow], Candidate]]]] = []
        for currency, currency_rows in by_currency.items():
            groups = _merge_sequential(_cluster_rows(currency_rows))
            passed = [
                (group.rows, candidate)
                for group in groups
                if (candidate := _candidate_from_group(identity, currency, group.rows)) is not None
            ]
            found_by_currency.append((currency, passed))

        if any(passed for _, passed in found_by_currency):
            for _, passed in found_by_currency:
                candidates.extend(
                    candidate for group_rows, candidate in passed if _unassigned(group_rows)
                )
            continue

        for currency, currency_rows in by_currency.items():
            fallback = _candidate_from_group(identity, currency, currency_rows)
            if fallback is not None and _unassigned(currency_rows):
                candidates.append(fallback)
    return candidates


def interval_days_for(cadence: str, custom_interval_days: int | None) -> int:
    """The interval in days a series' cadence stands for — `CADENCE_DAYS[cadence]`, or
    `custom_interval_days` for a `custom` series (Phase 4.1 Task 5, global execution contract
    "Interval"). `custom_interval_days` is guaranteed non-null for a `custom` series by
    `recurring_series_custom_interval_shape` (V58)."""
    if cadence == "custom":
        assert custom_interval_days is not None
        return custom_interval_days
    return CADENCE_DAYS[cadence]


def next_expected_on(anchor: date, interval_days: int) -> date:
    return anchor + timedelta(days=interval_days)


def is_overdue(anchor: date, interval_days: int, *, today: date) -> bool:
    """More than half a cadence past the next expected date (spec). Every caller passes the
    interval and the anchor itself (Task 5, global execution contract) instead of a `cadence` this
    function would otherwise have to look up on its own."""
    days_late = (today - next_expected_on(anchor, interval_days)).days
    return days_late > interval_days // 2


def price_change_window(cadence: str, custom_interval_days: int | None) -> tuple[int, int] | None:
    """A `custom` series' own price-change window, `(floor(0.85 x I), ceil(1.15 x I))` from its
    `custom_interval_days` (Task 5) — `CADENCE_WINDOWS` carries no `custom` entry, since detection
    never proposes one. `None` only for a `custom` series not yet given an interval, which Extend's
    caller treats as "no window", refusing the join rather than guessing one."""
    if cadence != "custom":
        return CADENCE_WINDOWS.get(cadence)
    if custom_interval_days is None:
        return None
    interval = Decimal(custom_interval_days)
    low = int((interval * Decimal("0.85")).to_integral_value(rounding=ROUND_FLOOR))
    high = int((interval * Decimal("1.15")).to_integral_value(rounding=ROUND_CEILING))
    return (low, high)
