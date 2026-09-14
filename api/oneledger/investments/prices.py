"""Official price refresh: bounded fetch, ISIN auto-mapping, idempotent price ingestion.

The 24-hour no-op gate is held in process memory only (per Task 18's contract — a restart
refetches once). That keeps this owner-only endpoint from hammering the configured source on
every page load without needing a scheduled job or a persisted "last checked" row.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import psycopg
from pydantic import BaseModel

from .price_adapters import (
    SOURCE_KEY,
    SOURCE_VERSION,
    NavRecord,
    fetch_official_nav_text_v1,
    parse_official_nav_text_v1,
)

_REFRESH_INTERVAL = timedelta(hours=24)

# ponytail: one process-memory dict, per household. Good enough for a single-process deployment;
# a multi-worker deployment would need a shared store (e.g. a `price_source_check` row) to keep
# the 24-hour gate honest across workers.
_last_success: dict[UUID, datetime] = {}


class PriceStatus(BaseModel):
    lastSuccessfulCheck: datetime | None
    mappedCount: int
    unmappedCount: int
    latestPriceDate: date | None


class RefreshOutcome(BaseModel):
    outcome: str  # "refreshed" | "not_due" | "source_unavailable"
    status: PriceStatus


def configured_url() -> str:
    return os.environ.get("OFFICIAL_NAV_URL", "")


def configured_currency() -> str:
    return os.environ.get("OFFICIAL_NAV_CURRENCY", "").strip().upper()


def _now() -> datetime:
    return datetime.now(UTC)


def _due(household_id: UUID, *, now: datetime) -> bool:
    last = _last_success.get(household_id)
    return last is None or now - last >= _REFRESH_INTERVAL


_COUNTS_SQL = """
    SELECT
        count(*) FILTER (WHERE mapping.id IS NOT NULL) AS mapped,
        count(*) FILTER (WHERE mapping.id IS NULL) AS unmapped
      FROM security
      LEFT JOIN security_price_source mapping
        ON mapping.security_id = security.id
       AND mapping.source_key = %(source_key)s AND mapping.is_active
     WHERE security.household_id = %(household)s
"""

_LATEST_DATE_SQL = """
    SELECT max(snapshot.price_date)
      FROM security
      JOIN price_snapshot snapshot
        ON snapshot.security_id = security.id AND snapshot.is_accepted
     WHERE security.household_id = %(household)s
"""


def _counts(cur: psycopg.Cursor[Any], household_id: UUID) -> tuple[int, int, date | None]:
    cur.execute(_COUNTS_SQL, {"household": household_id, "source_key": SOURCE_KEY})
    mapped, unmapped = cur.fetchone()  # type: ignore[misc]
    cur.execute(_LATEST_DATE_SQL, {"household": household_id})
    latest = cur.fetchone()[0]  # type: ignore[index]
    return int(mapped), int(unmapped), latest


def _status(
    cur: psycopg.Cursor[Any], household_id: UUID, *, checked_at: datetime | None
) -> PriceStatus:
    mapped, unmapped, latest = _counts(cur, household_id)
    return PriceStatus(
        lastSuccessfulCheck=checked_at,
        mappedCount=mapped,
        unmappedCount=unmapped,
        latestPriceDate=latest,
    )


def read_status(cur: psycopg.Cursor[Any], household_id: UUID) -> PriceStatus:
    return _status(cur, household_id, checked_at=_last_success.get(household_id))


def _auto_map(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    records_by_isin: dict[str, list[NavRecord]],
    source_currency: str,
) -> None:
    """Map a household security to the one publication code sharing its ISIN and currency."""
    cur.execute(
        """SELECT id, isin, currency FROM security
             WHERE household_id = %(household)s AND isin IS NOT NULL
               AND NOT EXISTS (
                 SELECT FROM security_price_source
                  WHERE security_id = security.id AND source_key = %(source_key)s
                    AND is_active)""",
        {"household": household_id, "source_key": SOURCE_KEY},
    )
    for security_id, isin, currency in cur.fetchall():
        candidates = records_by_isin.get(isin, [])
        if len(candidates) != 1 or str(currency).strip() != source_currency:
            continue
        try:
            with cur.connection.transaction(savepoint_name="price_mapping"):
                cur.execute(
                    """INSERT INTO security_price_source (
                           household_id, security_id, source_key, source_version,
                           source_security_code, effective_from)
                         VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        household_id,
                        security_id,
                        SOURCE_KEY,
                        SOURCE_VERSION,
                        candidates[0].scheme_code,
                        date.min,
                    ),
                )
        except psycopg.errors.ExclusionViolation:
            # ponytail: that scheme code is already actively mapped to a different security
            # (the global uniqueness the schema enforces); leave this one unmapped rather than
            # fail the whole refresh over one household's naming collision.
            continue


def _record_price(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    security_id: UUID,
    mapping_id: UUID,
    record: NavRecord,
    currency: str,
) -> None:
    """Append or idempotently confirm one dated observation; a conflicting value stays
    unaccepted."""
    cur.execute(
        """SELECT price FROM price_snapshot
             WHERE security_id = %s AND price_date = %s AND source_mapping_id = %s""",
        (security_id, record.price_date, mapping_id),
    )
    if any(Decimal(row[0]) == record.price for row in cur.fetchall()):
        return  # exact retry — already recorded, accepted or not
    cur.execute(
        """SELECT 1 FROM price_snapshot
             WHERE security_id = %s AND price_date = %s AND is_accepted""",
        (security_id, record.price_date),
    )
    accept = cur.fetchone() is None
    cur.execute(
        """INSERT INTO price_snapshot (
               household_id, security_id, source_mapping_id, price_date, price, currency,
               is_accepted, accepted_at)
             VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END)""",
        (
            household_id,
            security_id,
            mapping_id,
            record.price_date,
            record.price,
            currency,
            accept,
            accept,
        ),
    )


def _ingest(
    cur: psycopg.Cursor[Any], household_id: UUID, records: list[NavRecord], currency: str
) -> None:
    cur.execute(
        """SELECT id, security_id, source_security_code FROM security_price_source
             WHERE household_id = %s AND source_key = %s AND is_active""",
        (household_id, SOURCE_KEY),
    )
    by_code: dict[str, list[NavRecord]] = {}
    for record in records:
        by_code.setdefault(record.scheme_code, []).append(record)
    for mapping_id, security_id, code in cur.fetchall():
        for record in by_code.get(code, []):
            _record_price(cur, household_id, security_id, mapping_id, record, currency)


def refresh(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    client: httpx.Client,
    *,
    now: datetime | None = None,
) -> RefreshOutcome:
    """Bounded, idempotent refresh. Never called more than once per 24 hours per household."""
    now = now or _now()
    if not _due(household_id, now=now):
        return RefreshOutcome(
            outcome="not_due",
            status=_status(cur, household_id, checked_at=_last_success.get(household_id)),
        )

    url = configured_url()
    currency = configured_currency()
    try:
        if not url:
            raise ValueError("OFFICIAL_NAV_URL is not configured")
        text = fetch_official_nav_text_v1(client, url)
        records = parse_official_nav_text_v1(text)
        if not records:
            # A 200 that parses to nothing is not a successful check — a truncated, reformatted
            # or empty publication must not look "refreshed" and must not start the 24-hour gate.
            raise ValueError("official NAV publication parsed to no records")
    except httpx.HTTPError, ValueError:
        return RefreshOutcome(
            outcome="source_unavailable",
            status=_status(cur, household_id, checked_at=_last_success.get(household_id)),
        )

    records_by_isin: dict[str, list[NavRecord]] = {}
    for record in records:
        for isin in (record.isin_payout, record.isin_reinvestment):
            if isin is not None:
                records_by_isin.setdefault(isin, []).append(record)
    # A code that carries an ISIN under both dividend and growth options is not "exactly one
    # code carries that ISIN" — dedupe by scheme code per ISIN before counting candidates.
    for isin, candidates in records_by_isin.items():
        unique_codes = {candidate.scheme_code: candidate for candidate in candidates}
        records_by_isin[isin] = list(unique_codes.values())

    _auto_map(cur, household_id, records_by_isin, currency)
    _ingest(cur, household_id, records, currency)

    _last_success[household_id] = now
    return RefreshOutcome(outcome="refreshed", status=_status(cur, household_id, checked_at=now))
