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
from ..db import connect

router = APIRouter(tags=["doctor"])

# The compose `backup` service takes one a day. Past two days a run has been missed, rather
# than merely being due.
STALE_AFTER = timedelta(days=2)
BACKUP_NAME = re.compile(r"oneledger-(\d{8}T\d{6}Z)\.dump\.enc")


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


def newest_backup(directory: Path) -> datetime | None:
    """When the newest backup began, as its name records it; None when there is none.

    The name is stamped before pg_dump takes its snapshot, so counting rows created after it
    can only over-report what the backup lacks, never hide something it does not hold.
    """
    stamps = [
        datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        for path in directory.glob("oneledger-*.dump.enc")
        if (match := BACKUP_NAME.fullmatch(path.name))
    ]
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


@router.get("/doctor")
def report(owner: Annotated[OwnerSession, Depends(require_owner)]) -> DoctorReport:
    newest = newest_backup(backup_dir())
    finding = backup_finding(
        newest, transactions_since(owner.household_id, newest), datetime.now(UTC)
    )
    return DoctorReport(sections=[DoctorSection(title="Backups", findings=[finding])])
