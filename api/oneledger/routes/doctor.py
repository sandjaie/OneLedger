"""GET /doctor: the report behind Settings › Data Quality (testing-and-data-quality.html §5).

Its first section is the one whose absence cost the ledger twice on 10 September 2026: how old
the newest backup is, whether that run wrote all three of its files, and whether a restore drill
has ever proven one of them restores (spec "Diagnostics", Phase 2.4). This route never decrypts
anything and never needs BACKUP_PASSPHRASE — it reads filenames and the drill's own plaintext
JSON, both written by scripts/backup-db.sh and scripts/restore-drill.sh.
"""

import json
import os
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..coverage import months_from_anchor
from ..db import connect, scope_to_owner
from .coverage import coverage_rows

router = APIRouter(tags=["doctor"])

# The compose `backup` service takes one a day. Past two days a run has been missed, rather
# than merely being due (spec "Diagnostics": "warning over 48 hours").
STALE_AFTER = timedelta(days=2)
# The quarterly restore test in testing-and-data-quality.html §7 is the floor; a drill older than
# this is a warning rather than the proof it is meant to be.
DRILL_STALE_AFTER = timedelta(days=31)


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


def newest_backup_complete(directory: Path, prefix: str, newest: datetime) -> bool:
    """Whether the newest stamp's run wrote all three files of one backup (spec "Diagnostics":
    "checked by filename only" — this never opens or decrypts any of them)."""
    stamp = newest.strftime("%Y%m%dT%H%M%SZ")
    return all(
        (directory / f"{prefix}-{stamp}.{suffix}").exists()
        for suffix in ("dump.enc", "documents.tar.enc", "manifest.json.enc")
    )


def backup_finding(
    newest: datetime | None, not_held: int, now: datetime, files_complete: bool = True
) -> Finding:
    incomplete = "" if files_complete else " (missing the documents archive or manifest)"
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
            message=f"The newest backup is {age.days} days old ({taken}){incomplete}, and the "
            f"ledger has {_transactions(not_held)} it does not hold.",
        )
    if not files_complete:
        return Finding(
            level="warning",
            message=f"The newest backup{incomplete} was taken {taken}; "
            f"{_transactions(not_held)} added since.",
        )
    return Finding(
        level="ok",
        message=f"The newest backup was taken {taken}; {_transactions(not_held)} added since.",
    )


def newest_drill(directory: Path, prefix: str) -> dict[str, object] | None:
    """The most recently written `<prefix>-<stamp>.drill.json` (spec "Restore drill" step 5),
    read by its own file time — a drill can be run against any stamp at any moment, so the
    newest *drill* is not the same thing as the newest *backup*. `scripts/restore-drill.sh`
    writes this in plain text — structural pass/fail only, never a row — so this needs no
    passphrase. A file that is missing or does not parse is treated as no drill at all."""
    name = re.compile(rf"{re.escape(prefix)}-\d{{8}}T\d{{6}}Z\.drill\.json")
    candidates = [p for p in directory.glob(f"{prefix}-*.drill.json") if name.fullmatch(p.name)]
    if not candidates:
        return None
    newest_path = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        parsed = json.loads(newest_path.read_text())
    except OSError, ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def drill_finding(drill: dict[str, object] | None, now: datetime) -> Finding:
    if drill is None:
        return Finding(level="warning", message="No restore drill has ever been run.")
    try:
        drill_time = datetime.fromisoformat(str(drill["drillTime"]))
    except KeyError, ValueError:
        return Finding(
            level="warning", message="The newest restore drill result could not be read."
        )
    when = drill_time.strftime("%Y-%m-%d %H:%M UTC")
    if drill.get("result") != "pass":
        return Finding(level="warning", message=f"The newest restore drill ({when}) failed.")
    age = now - drill_time
    if age > DRILL_STALE_AFTER:
        return Finding(
            level="warning",
            message=f"The newest restore drill passed {when}, {age.days} days ago.",
        )
    return Finding(level="ok", message=f"The newest restore drill passed {when}.")


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


def coverage_findings(owner: OwnerSession) -> list[Finding]:
    """One warning per row with a missing or partial month (spec "API"), the account's name or
    "Payslips" for the payslip row; one `ok` finding when every row is reconciled or
    acknowledged. Reads `routes/coverage.py`'s own row computation, not a second copy of it."""
    as_of = date.today()
    months = months_from_anchor(as_of)
    with connect() as conn, conn.cursor() as cur:
        rows = coverage_rows(cur, owner.household_id, as_of)
    findings: list[Finding] = []
    for row in rows:
        if not row.counts.missing and not row.counts.partial:
            continue
        earliest = next(
            month
            for month, cell in zip(months, row.months, strict=True)
            if cell.status in ("missing", "partial")
        )
        label = row.accountName or "Payslips"
        month_word = "month" if row.counts.missing == 1 else "months"
        findings.append(
            Finding(
                level="warning",
                message=(
                    f"{label}: {row.counts.missing} {month_word} missing, {row.counts.partial} "
                    f"partial, earliest {earliest:%Y-%m}."
                ),
            )
        )
    if not findings:
        findings.append(
            Finding(level="ok", message="Every coverage row is reconciled or acknowledged.")
        )
    return findings


def wealth_history_counts(owner: OwnerSession) -> tuple[int, date | None]:
    """(months with an incomplete position, the latest such month) — a stored `status <>
    'available'` row is how `GET /wealth-history` itself already marks a month incomplete
    (routes/wealth_history.py's own `complete` flag), so this reads the same fact rather than a
    second copy of it.

    ponytail: the spec also asks for complete months whose base-currency attribution residual is
    non-zero. Computing that means running `_attribute_leg`'s full flow/funding/rate machinery
    per month (routes/wealth_history.py), which is not cheap enough to redo on every /doctor
    request across a whole 36-month history — so only incomplete months are reported here; a
    non-zero residual is visible in Wealth History's own attribution view for the month it
    affects. Upgrade path: cache the residual alongside `month_end_position` when it stops being
    fine to eyeball.
    """
    with connect() as conn, conn.cursor() as cur:
        scope_to_owner(conn, owner.user_id)
        cur.execute(
            """SELECT count(DISTINCT month_end), max(month_end) FROM month_end_position
                WHERE household_id = %s AND status <> 'available'""",
            (owner.household_id,),
        )
        row = cur.fetchone()
    assert row is not None
    count, latest = row
    return count or 0, latest


def wealth_history_findings_from_counts(count: int, latest: date | None) -> list[Finding]:
    if not count:
        return [Finding(level="ok", message="Every computed month of wealth history is complete.")]
    assert latest is not None
    month_word = "month" if count == 1 else "months"
    is_are = "is" if count == 1 else "are"
    return [
        Finding(
            level="warning",
            message=f"{count:,} {month_word} of wealth history {is_are} incomplete, "
            f"latest {latest:%Y-%m}.",
        )
    ]


def wealth_history_findings(owner: OwnerSession) -> list[Finding]:
    return wealth_history_findings_from_counts(*wealth_history_counts(owner))


@router.get("/doctor")
def report(owner: Annotated[OwnerSession, Depends(require_owner)]) -> DoctorReport:
    directory = backup_dir()
    prefix = os.environ.get("BACKUP_PREFIX") or "oneledger"
    now = datetime.now(UTC)
    newest = newest_backup(directory, prefix)
    files_complete = newest is not None and newest_backup_complete(directory, prefix, newest)
    finding = backup_finding(
        newest, transactions_since(owner.household_id, newest), now, files_complete
    )
    drill = drill_finding(newest_drill(directory, prefix), now)
    return DoctorReport(
        sections=[
            DoctorSection(title="Backups", findings=[finding, drill]),
            DoctorSection(
                title="Account identities",
                findings=[identity_backfill_finding(identity_backfills_remaining(owner))],
            ),
            DoctorSection(title="Investments", findings=investment_findings(owner)),
            DoctorSection(title="Coverage", findings=coverage_findings(owner)),
            DoctorSection(title="Wealth history", findings=wealth_history_findings(owner)),
        ]
    )
