"""Phase 4.4 Task 1: the pure remittance-analysis engine (no database, `docs/superpowers/specs/
2026-09-21-phase-4-4-fx-remittance-analysis.md`). `routes/transfers.py` is the only caller: it
reads the household's confirmed remittance transfers and hands them here.

Nothing below computes a rate, a spread or a cost. Those were computed once, when the transfer
was confirmed, by `routes/transfers.py::FX_COST` per `docs/design/multi-currency-and-fx.html`
§5, and an owner's fee correction folds into the same stored `total_cost_source`. This module
only groups, sums and compares what is already frozen on the row (ADR-0002), which is why the
fee line is recovered by subtraction rather than re-read and re-translated: a later `fx_rate`
backfill can then never produce a row whose fees and spread do not add up to its total.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

_MONEY_QUANT = Decimal("0.0001")  # 4 dp — `transfer`'s own NUMERIC(20,4) money scale
_PERCENT_QUANT = Decimal("0.0001")  # 4 dp — `transfer.fx_spread_pct`'s NUMERIC(9,4) scale
_RATE_QUANT = Decimal("0.0000000001")  # 10 dp — `money.RATE`, the scale rates store at
_ZERO = Decimal(0)


@dataclass(frozen=True)
class TransferFact:
    """One confirmed remittance, as the route reads it.

    `fx_spread_cost_source` and `total_cost_source` are nullable because a same-currency
    remittance has no spread to compute and may carry no recorded fee: `FX_COST` runs only when
    the two legs' currencies differ. `cost_is_approximate` is the flag `GET /transfers` already
    derives — the market rate for that date was carried forward (Invariant I7).
    """

    source_currency: str
    destination_currency: str
    transfer_date: date
    source_amount: Decimal
    destination_amount: Decimal
    implied_rate: Decimal | None
    fx_spread_cost_source: Decimal | None
    total_cost_source: Decimal | None
    provider: str | None
    cost_is_approximate: bool


@dataclass(frozen=True)
class ProviderGroup:
    """One provider's figures inside one corridor-year, or the unattributed bucket.

    A null money field means "no transfer here has a cost yet", never zero. A negative one means
    the achieved rate beat the mid-market rate for that date, which `FX_COST` does not floor —
    reported as it stands, because that is the evidence.
    """

    provider: str | None
    transfer_count: int
    sent_source: Decimal
    costed_sent_source: Decimal
    fees_source: Decimal | None
    spread_source: Decimal | None
    total_cost_source: Decimal | None
    effective_cost_pct: Decimal | None
    avg_rate: Decimal | None
    approximate_count: int


@dataclass(frozen=True)
class CorridorYear:
    year: int
    total_sent_source: Decimal
    total_costed_sent_source: Decimal
    total_fees_source: Decimal | None
    total_spread_source: Decimal | None
    total_cost_source: Decimal | None
    total_effective_cost_pct: Decimal | None
    cheapest_provider: str | None
    potential_savings_source: Decimal | None
    providers: list[ProviderGroup]
    unattributed: ProviderGroup | None


@dataclass(frozen=True)
class Corridor:
    source_currency: str
    destination_currency: str
    years: list[CorridorYear]


def _effective_pct(total_cost: Decimal, costed_sent: Decimal) -> Decimal | None:
    """Cost as a percentage of the money it was charged on — of the costed subset only.

    `None` rather than a percentage of an unknown whole: dividing a known cost by every
    dirham sent, including the transfers whose cost is not known, understates it silently.
    """
    if costed_sent == 0:
        return None
    return (total_cost / costed_sent * 100).quantize(_PERCENT_QUANT, rounding=ROUND_HALF_EVEN)


def _money(total: Decimal) -> Decimal:
    """One money sum at the scale the wire states money in.

    A sum over no rows is `Decimal(0)`, which stringifies as `"0"` — every other amount in a
    response comes from `NUMERIC(20,4)` and reads `"0.0000"`. The screen formats these strings,
    so they all state the same number of places.
    """
    return total.quantize(_MONEY_QUANT, rounding=ROUND_HALF_EVEN)


def _group(provider: str | None, facts: Sequence[TransferFact]) -> ProviderGroup:
    costed = [fact for fact in facts if fact.total_cost_source is not None]
    costed_sent = _money(sum((fact.source_amount for fact in costed), _ZERO))

    total_cost: Decimal | None = None
    spread: Decimal | None = None
    fees: Decimal | None = None
    effective: Decimal | None = None
    if costed:
        total_cost = _money(sum((fact.total_cost_source or _ZERO for fact in costed), _ZERO))
        spread = _money(sum((fact.fx_spread_cost_source or _ZERO for fact in costed), _ZERO))
        # The fee line by subtraction, so `fees + spread == total` holds for every group without
        # re-translating a fee whose rate may since have been corrected.
        fees = total_cost - spread
        effective = _effective_pct(total_cost, costed_sent)

    # Only a cross-currency transfer has an implied rate, and only those can be averaged: a
    # same-currency leg would drag a corridor's achieved rate toward 1.
    rated = [fact for fact in facts if fact.implied_rate is not None]
    rated_sent = sum((fact.source_amount for fact in rated), _ZERO)
    avg_rate = (
        (sum((fact.destination_amount for fact in rated), _ZERO) / rated_sent).quantize(
            _RATE_QUANT, rounding=ROUND_HALF_UP
        )
        if rated and rated_sent != 0
        else None
    )

    return ProviderGroup(
        provider=provider,
        transfer_count=len(facts),
        sent_source=_money(sum((fact.source_amount for fact in facts), _ZERO)),
        costed_sent_source=costed_sent,
        fees_source=fees,
        spread_source=spread,
        total_cost_source=total_cost,
        effective_cost_pct=effective,
        avg_rate=avg_rate,
        approximate_count=sum(1 for fact in costed if fact.cost_is_approximate),
    )


def _cheapest(named: Sequence[ProviderGroup]) -> tuple[str | None, Decimal | None]:
    """The cheapest named channel and what routing everything through it would have saved.

    Comparable rows only: a provider with no costed transfer has no rate to be cheapest at, and
    the unattributed bucket is excluded by the caller — money with no recorded channel cannot be
    attributed a cheaper one. Fewer than two leaves both answers `None` rather than crowning a
    field of one.
    """
    comparable = [
        group
        for group in named
        if group.costed_sent_source > 0
        and group.total_cost_source is not None
        and group.effective_cost_pct is not None
    ]
    if len(comparable) < 2:
        return None, None

    cheapest = min(comparable, key=lambda g: (g.effective_cost_pct or _ZERO, g.provider or ""))
    incurred = sum((group.total_cost_source or _ZERO for group in comparable), _ZERO)
    costed_sent = sum((group.costed_sent_source for group in comparable), _ZERO)

    # Clamping the cheapest rate at zero refuses to project a favourable spread onto other
    # transfers as if it were repeatable: at best this would have been free, not profitable.
    best_pct = max(_ZERO, cheapest.effective_cost_pct or _ZERO)
    counterfactual = costed_sent * best_pct / 100
    # Bounded into `[0, max(0, incurred)]`: a stated saving never exceeds the money actually
    # spent, and a corridor that came out ahead overall is offered no saving at all rather than a
    # negative one.
    saving = min(max(_ZERO, incurred), max(_ZERO, incurred - counterfactual))
    return cheapest.provider, saving.quantize(_MONEY_QUANT, rounding=ROUND_HALF_EVEN)


def _corridor_year(year: int, facts: Sequence[TransferFact]) -> CorridorYear:
    by_provider: dict[str, list[TransferFact]] = {}
    unattributed_facts: list[TransferFact] = []
    for fact in facts:
        if fact.provider is None:
            unattributed_facts.append(fact)
        else:
            by_provider.setdefault(fact.provider, []).append(fact)

    named = [_group(provider, rows) for provider, rows in by_provider.items()]
    # Cheapest first, and a provider with no comparable rate last rather than sorted as zero.
    named.sort(
        key=lambda g: (
            g.effective_cost_pct is None,
            g.effective_cost_pct or _ZERO,
            g.provider or "",
        )
    )
    unattributed = _group(None, unattributed_facts) if unattributed_facts else None

    # The corridor-year total is over every transfer of the year, named and unattributed alike:
    # unlabelled money still left the household. Only the comparison excludes it.
    whole = _group(None, facts)
    cheapest_provider, saving = _cheapest(named)

    return CorridorYear(
        year=year,
        total_sent_source=whole.sent_source,
        total_costed_sent_source=whole.costed_sent_source,
        total_fees_source=whole.fees_source,
        total_spread_source=whole.spread_source,
        total_cost_source=whole.total_cost_source,
        total_effective_cost_pct=whole.effective_cost_pct,
        cheapest_provider=cheapest_provider,
        potential_savings_source=saving,
        providers=named,
        unattributed=unattributed,
    )


def analyse(facts: Sequence[TransferFact]) -> list[Corridor]:
    """Group confirmed remittances into corridors, years and providers.

    A corridor is one `(source_currency, destination_currency)` pair. An achieved rate, a spread
    and an effective cost only mean something next to another remittance through the same pair,
    so nothing is ever summed or compared across two of them.
    """
    by_corridor: dict[tuple[str, str], list[TransferFact]] = {}
    for fact in facts:
        by_corridor.setdefault((fact.source_currency, fact.destination_currency), []).append(fact)

    corridors: list[Corridor] = []
    for (source, destination), rows in by_corridor.items():
        by_year: dict[int, list[TransferFact]] = {}
        for fact in rows:
            by_year.setdefault(fact.transfer_date.year, []).append(fact)
        corridors.append(
            Corridor(
                source_currency=source,
                destination_currency=destination,
                years=[
                    _corridor_year(year, by_year[year]) for year in sorted(by_year, reverse=True)
                ],
            )
        )

    # Busiest corridor first, over the rows supplied — with `?year=` set that is that year's
    # figure, not a lifetime one.
    corridors.sort(
        key=lambda c: (
            -sum((year.total_sent_source for year in c.years), _ZERO),
            c.source_currency,
            c.destination_currency,
        )
    )
    return corridors


__all__ = [
    "Corridor",
    "CorridorYear",
    "ProviderGroup",
    "TransferFact",
    "analyse",
]
