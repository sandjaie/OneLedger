"""GET /doctor: the report behind Settings › Data Quality (testing-and-data-quality.html §5).

Its first section is the one whose absence cost the ledger twice on 10 September 2026: how old
the newest backup is, and how much of the ledger it does not hold. The other sections §5 lists
arrive with Phase 2.
"""

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect, scope_to_owner

router = APIRouter(tags=["doctor"])

# The compose `backup` service takes one a day. Past two days a run has been missed, rather
# than merely being due.
STALE_AFTER = timedelta(days=2)


class Finding(BaseModel):
    level: Literal["ok", "warning"]
    message: str


class DoctorSection(BaseModel):
    title: str
    findings: list[Finding]


class DoctorReport(BaseModel):
    sections: list[DoctorSection]


def backup_dir() -> Path:
    """Compose sets BACKUP_DIR for the api container; a `uv run` reads the checkout's backups/."""
    configured = os.environ.get("BACKUP_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / "backups"


def newest_backup(directory: Path, prefix: str = "oneledger") -> datetime | None:
    """When this stack's newest backup began, as its name records it; None when there is none.

    Only names carrying this stack's prefix count (scripts/backup-db.sh): a scratch stack's
    dump of an empty database, written beside the ledger's, is not a backup of the ledger.

    The name is stamped before pg_dump takes its snapshot, so a row created after the stamp is
    counted even when the dump caught it. The count is not a guarantee in the other direction:
    `created_at` is its transaction's start, so an import that began before the stamp and
    committed after the snapshot is missing from the dump and not counted.
    """
    name = re.compile(rf"{re.escape(prefix)}-(\d{{8}}T\d{{6}}Z)\.dump\.enc")
    stamps = []
    for path in directory.glob(f"{prefix}-*.dump.enc"):
        if match := name.fullmatch(path.name):
            try:
                stamps.append(datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
            except ValueError:
                continue  # an impossible date is a hand-placed file, not a backup
    return max(stamps, default=None)


def transactions_since(household_id: UUID, since: datetime | None) -> int:
    # The base table, not v_transaction_reportable: a backup holds rows, not a report.
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM transaction
                WHERE household_id = %s
                  AND created_at > coalesce(%s::timestamptz, '-infinity')""",
            (household_id, since),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def _transactions(count: int) -> str:
    return f"{count:,} transaction{'' if count == 1 else 's'}"


def backup_finding(newest: datetime | None, not_held: int, now: datetime) -> Finding:
    if newest is None:
        return Finding(
            level="warning",
            message=f"No backup has been taken, and the ledger has {_transactions(not_held)} "
            "no backup holds.",
        )
    taken = newest.strftime("%Y-%m-%d %H:%M UTC")
    age = now - newest
    if age > STALE_AFTER:
        return Finding(
            level="warning",
            message=f"The newest backup is {age.days} days old ({taken}), and the ledger has "
            f"{_transactions(not_held)} it does not hold.",
        )
    return Finding(
        level="ok",
        message=f"The newest backup was taken {taken}; {_transactions(not_held)} added since.",
    )


def identity_backfills_remaining(owner: OwnerSession) -> int:
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT count(DISTINCT account_id) FROM account_identifier
                WHERE household_id = %s AND is_active
                  AND legacy_fingerprint IS NOT NULL AND normalized_value IS NULL""",
            (owner.household_id,),
        )
        row = cur.fetchone()
    return row[0] if row else 0


def identity_backfill_finding(remaining: int) -> Finding:
    if remaining:
        noun = "identity" if remaining == 1 else "identities"
        return Finding(
            level="warning",
            message=f"{remaining:,} account {noun} await statement backfill.",
        )
    return Finding(level="ok", message="Every account identity is on the current local model.")


_INVESTMENT_COUNTS_SQL = """
    SELECT
        (SELECT count(*) FROM investment_review_item
          WHERE household_id = %(household)s AND status = 'open' AND is_blocking),
        (SELECT count(*) FROM holding h
           JOIN account a ON a.household_id = h.household_id AND a.id = h.account_id
          WHERE h.household_id = %(household)s AND h.current_units > 0 AND a.is_active
            AND NOT EXISTS (
                SELECT FROM price_snapshot p WHERE p.security_id = h.security_id
                  AND p.is_accepted AND p.price_date <= CURRENT_DATE)),
        (SELECT count(*) FROM holding h
           JOIN account a ON a.household_id = h.household_id AND a.id = h.account_id
          WHERE h.household_id = %(household)s AND h.current_units > 0 AND a.is_active
            AND (SELECT max(p.price_date) FROM price_snapshot p
                  WHERE p.security_id = h.security_id AND p.is_accepted
                    AND p.price_date <= CURRENT_DATE) < CURRENT_DATE - 7),
        (SELECT count(*) FROM investment_transaction t
          WHERE t.household_id = %(household)s
            AND t.event_kind IN ('purchase', 'recurring_purchase', 'rights')
            AND NOT EXISTS (
                SELECT FROM investment_transaction c WHERE c.corrects_transaction_id = t.id)
            AND NOT EXISTS (
                SELECT FROM investment_cash_allocation ica
                 WHERE ica.investment_transaction_id = t.id AND ica.role = 'funding')),
        (SELECT count(*) FROM investment_review_item
          WHERE household_id = %(household)s AND status = 'open'
            AND reason = 'incomplete_lot_allocation')
"""


def investment_counts(owner: OwnerSession) -> tuple[int, int, int, int, int]:
    """(blocking reviews, unvalued holdings, stale prices, unfunded purchases, withheld gains).

    ponytail: "unfunded" only means no funding allocation exists at all, not that the purchase
    is short of its full amount — a partial-funding tally would need funding.py's remaining-
    amount math, which this report does not otherwise need.
    """
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(_INVESTMENT_COUNTS_SQL, {"household": owner.household_id})
        row = cur.fetchone()
    assert row is not None
    return tuple(row)


def _plural(count: int, noun: str, plural_noun: str, verb: str, plural_verb: str) -> str:
    return f"{count:,} {noun if count == 1 else plural_noun} {verb if count == 1 else plural_verb}"


def investment_findings_from_counts(counts: tuple[int, int, int, int, int]) -> list[Finding]:
    blocking, unvalued, stale, unfunded, withheld_gain = counts
    findings = []
    if blocking:
        findings.append(
            Finding(
                level="warning",
                message=_plural(
                    blocking,
                    "investment import review",
                    "investment import reviews",
                    "blocks",
                    "block",
                )
                + " committing.",
            )
        )
    if unvalued:
        findings.append(
            Finding(
                level="warning",
                message=_plural(unvalued, "open holding", "open holdings", "has", "have")
                + " no dated price and cannot be valued.",
            )
        )
    if stale:
        findings.append(
            Finding(
                level="warning",
                message=_plural(stale, "open holding", "open holdings", "has", "have")
                + " a price older than seven days.",
            )
        )
    if unfunded:
        findings.append(
            Finding(
                level="warning",
                message=_plural(
                    unfunded, "investment purchase", "investment purchases", "has", "have"
                )
                + " no linked cash funding.",
            )
        )
    if withheld_gain:
        findings.append(
            Finding(
                level="warning",
                message=_plural(withheld_gain, "disposal", "disposals", "is", "are")
                + " awaiting lot evidence before realised gain is available.",
            )
        )
    if not findings:
        findings.append(Finding(level="ok", message="No investment data-quality issues found."))
    return findings


def investment_findings(owner: OwnerSession) -> list[Finding]:
    return investment_findings_from_counts(investment_counts(owner))


@router.get("/doctor")
def report(owner: Annotated[OwnerSession, Depends(require_owner)]) -> DoctorReport:
    newest = newest_backup(backup_dir(), os.environ.get("BACKUP_PREFIX") or "oneledger")
    finding = backup_finding(
        newest, transactions_since(owner.household_id, newest), datetime.now(UTC)
    )
    return DoctorReport(
        sections=[
            DoctorSection(title="Backups", findings=[finding]),
            DoctorSection(
                title="Account identities",
                findings=[identity_backfill_finding(identity_backfills_remaining(owner))],
            ),
            DoctorSection(title="Investments", findings=investment_findings(owner)),
        ]
    )
