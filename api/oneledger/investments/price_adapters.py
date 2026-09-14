"""Official price-source adapters, one price-history contract per capability key.

`official_nav_text_v1` reads a semicolon-delimited daily NAV publication (typical columns:
Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date)
into dated `Decimal` prices. The publisher's URL and currency are configuration
(`OFFICIAL_NAV_URL`, `OFFICIAL_NAV_CURRENCY`) — the capability key selects this parser, never an
institution or country name in code (docs/superpowers/specs/2026-09-12-phase-1-6-investments.md
"Prices and valuations").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

SOURCE_KEY = "official_nav_text_v1"
SOURCE_VERSION = "v1"

# The real publication is a few MB; this bounds a misbehaving or hostile source regardless of
# what Content-Length it claims.
_MAX_BYTES = 32 * 1024 * 1024
_TIMEOUT = httpx.Timeout(30.0)
_NA_VALUES = {"", "N.A.", "NA", "N.A", "-"}
# price_snapshot.price is NUMERIC(20,6): at most 6 decimal places and 14 integer digits. A row
# outside that range is skipped, never rounded — quantizing here would let a later identical
# re-fetch of the same untruncated source value read as a "different" price and land in
# price_snapshot as a spurious conflict (uq_price_snapshot_observation/idx_..._one_accepted).
_MAX_SCALE = 6
_MAX_INTEGER_DIGITS = 14
# Matches security.isin's own CHECK constraint (ISO 6166): two letters, nine alphanumerics,
# one checksum digit.
_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


@dataclass(frozen=True)
class NavRecord:
    scheme_code: str
    isin_payout: str | None
    isin_reinvestment: str | None
    scheme_name: str
    price: Decimal
    price_date: date


def _isin(value: str) -> str | None:
    value = value.strip().upper()
    return value if _ISIN.fullmatch(value) else None


def _price(value: str) -> Decimal | None:
    value = value.strip()
    if value.upper() in _NA_VALUES:
        return None
    try:
        price = Decimal(value)
    except InvalidOperation:
        return None
    if not price.is_finite() or price <= 0:
        return None
    _sign, digits, exponent = price.as_tuple()
    if not isinstance(exponent, int) or exponent < -_MAX_SCALE:
        return None
    if len(digits) + exponent > _MAX_INTEGER_DIGITS:
        return None
    return price


def _price_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_official_nav_text_v1(text: str) -> list[NavRecord]:
    """Parse the latest complete daily NAV publication.

    Fund-house and category header lines carry no semicolon and are skipped, as is the column
    header row itself. A row with an unparsable scheme code, price (including ``N.A.``) or date
    is skipped rather than failing the whole fetch — one bad row is not grounds to lose every
    other dated price in the publication.
    """
    records: list[NavRecord] = []
    for line in text.splitlines():
        fields = line.strip().split(";")
        if len(fields) != 6:
            continue
        scheme_code, isin_payout, isin_reinvestment, scheme_name, nav, nav_date = (
            field.strip() for field in fields
        )
        if not scheme_code.isdigit():
            continue
        price = _price(nav)
        parsed_date = _price_date(nav_date)
        if price is None or parsed_date is None or not scheme_name:
            continue
        records.append(
            NavRecord(
                scheme_code=scheme_code,
                isin_payout=_isin(isin_payout),
                isin_reinvestment=_isin(isin_reinvestment),
                scheme_name=scheme_name,
                price=price,
                price_date=parsed_date,
            )
        )
    return records


def fetch_official_nav_text_v1(client: httpx.Client, url: str) -> str:
    """GET the configured publication, streamed and bounded so a fetch cannot exhaust memory."""
    with client.stream("GET", url, timeout=_TIMEOUT) as response:
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > _MAX_BYTES:
                raise ValueError("official NAV publication exceeds the bounded fetch size")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")
