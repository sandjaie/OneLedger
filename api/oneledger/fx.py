# api/oneledger/fx.py
"""Exchange rates: fetch, peg derivation, gap filling.

No currency code is hardcoded here. The ECB quotes everything against EUR, so any pair is a
division; a currency the ECB does not publish is reached through its peg, which is a row in
`currency`. See multi-currency-and-fx.html §3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import httpx

# The FX doc names api.frankfurter.app; that host now 301s to nothing. Verified live 2026-09-05.
FRANKFURTER = "https://api.frankfurter.dev/v1"


@dataclass(frozen=True)
class Peg:
    """`units_per_anchor` of this currency buy 1 `anchor`. From currency.pegged_to/peg_rate."""

    anchor: str
    units_per_anchor: Decimal


def units_per_eur(published: Mapping[str, Decimal], code: str, pegs: Mapping[str, Peg]) -> Decimal:
    """How many units of `code` buy 1 EUR, following pegs for currencies the ECB skips."""
    seen: set[str] = set()
    factor = Decimal(1)
    while code not in published:
        if code in seen:
            raise ValueError(f"peg cycle through {code}")
        seen.add(code)
        peg = pegs.get(code)
        if peg is None:
            raise ValueError(f"{code} is neither published nor pegged")
        factor *= peg.units_per_anchor
        code = peg.anchor
    return published[code] * factor


def cross_rate(
    published: Mapping[str, Decimal], base: str, quote: str, pegs: Mapping[str, Peg]
) -> Decimal:
    """1 `base` = <returned> `quote`. Rates derived through a peg are stored as 'derived_peg'."""
    return units_per_eur(published, quote, pegs) / units_per_eur(published, base, pegs)


def carry_forward[T](
    published: dict[date, T], start: date, end: date
) -> list[tuple[date, T, date | None]]:
    """Fill every calendar date from the last published value.

    Forward-fill, never interpolate: interpolating between Friday and Monday would fabricate a
    Saturday rate that never existed, and would make a Saturday transaction's base-currency
    value depend on what happened the following Monday.
    """
    prior = [d for d in sorted(published) if d <= start]
    if not prior:
        raise ValueError(f"no published rate at or before {start.isoformat()}")

    source_date = prior[-1]
    value = published[source_date]

    out: list[tuple[date, T, date | None]] = []
    day = start
    while day <= end:
        if day in published:
            source_date, value = day, published[day]
            out.append((day, value, None))
        else:
            out.append((day, value, source_date))
        day += timedelta(days=1)
    return out


class EcbFrankfurterSource:
    """ECB daily reference rates. No key, no account, no rate limit."""

    name = "ecb"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30.0)

    def fetch_range(
        self, start: date, end: date, codes: list[str]
    ) -> dict[date, dict[str, Decimal]]:
        """Return {published_date: {code: units_per_eur}}. Unpublished days are absent, not filled.

        `codes` is what the household needs quoted — its reporting pair plus every currency its
        accounts hold, minus the pegged ones, which are derived rather than fetched.
        """
        url = f"{FRANKFURTER}/{start.isoformat()}..{end.isoformat()}"
        response = self._client.get(url, params={"base": "EUR", "symbols": ",".join(codes)})
        response.raise_for_status()

        return {
            date.fromisoformat(day): {
                # str() first: json produces floats, and Decimal(float) inherits the binary
                # rounding error. This is the most common way float creeps into money code.
                code: Decimal(str(value))
                for code, value in rates.items()
            }
            for day, rates in response.json()["rates"].items()
        }
