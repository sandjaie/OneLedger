"""Owner-authorized atomic commit of a staged investment import's ready groups.

Each non-terminal group commits in its own savepoint: a database refusal (an open blocking
review, an inexact split, a mismatched transfer pair) or a missing FX rate rolls back only that
group's savepoint and leaves it ``blocked`` with a fixed reason, never the whole import. A
committed group is never revisited; retrying an otherwise-unchanged commit call reattempts only
what is still ``ready`` and reports groups already ``committed`` as-is, so an identical replay is
naturally idempotent rather than requiring separate replay bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import HTTPException

from ..auth import OwnerSession
from ..db import connect, scope_to_owner
from ..money import _frozen, translate
from . import staging
from .cash_lifecycle import _DISPOSALS, _ensure_disposal_review
from .commands import InvestmentCommandError, _lock_reporting_pair
from .staging import StagedImportStatus, StagingError, _expected_state

_LEG_OUT_TO_IN: dict[str, str] = {
    "switch_out": "switch_in",
    "systematic_transfer_out": "systematic_transfer_in",
    "platform_transfer_out": "platform_transfer_in",
}

CommitBlockReason = Literal["open_review", "missing_fx_rate", "database_refusal"]


class _CommitDataError(Exception):
    """An internal shape mismatch (unpaired transfer legs); always a database_refusal."""


@dataclass(frozen=True, slots=True)
class CommittedGroupOutcome:
    group_number: int
    status: Literal["committed", "blocked"]
    reason: CommitBlockReason | None = None


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    import_id: UUID
    status: StagedImportStatus
    groups: tuple[CommittedGroupOutcome, ...]


@dataclass(frozen=True, slots=True)
class _DraftRow:
    draft_id: UUID
    account_id: UUID
    security_id: UUID
    event_date: date
    event_kind: str
    units: Decimal | None
    unit_ratio: Decimal | None
    price: Decimal | None
    gross_amount: Decimal | None
    charges: Decimal
    withheld_tax: Decimal
    net_amount: Decimal
    currency: str
    source_event_id: str | None
    dedupe_version: str
    dedupe_fingerprint: str

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> _DraftRow:
        return cls(
            draft_id=UUID(str(row[0])),
            account_id=UUID(str(row[1])),
            security_id=UUID(str(row[2])),
            event_date=row[3],
            event_kind=str(row[4]),
            units=None if row[5] is None else Decimal(row[5]),
            unit_ratio=None if row[6] is None else Decimal(row[6]),
            price=None if row[7] is None else Decimal(row[7]),
            gross_amount=None if row[8] is None else Decimal(row[8]),
            charges=Decimal(row[9]),
            withheld_tax=Decimal(row[10]),
            net_amount=Decimal(row[11]),
            currency=str(row[12]).strip(),
            source_event_id=row[13],
            dedupe_version=str(row[14]),
            dedupe_fingerprint=str(row[15]),
        )


def _ready_drafts(cur: psycopg.Cursor[Any], household_id: UUID, group_id: UUID) -> list[_DraftRow]:
    cur.execute(
        """SELECT draft.id, draft.account_id, draft.security_id, draft.event_date,
                  draft.event_kind::text, draft.units, draft.unit_ratio, draft.price,
                  draft.gross_amount, draft.charges, draft.withheld_tax, draft.net_amount,
                  draft.currency::text, evidence.source_event_id, evidence.fingerprint_version,
                  evidence.evidence_fingerprint
             FROM investment_event_draft draft
             JOIN investment_evidence evidence
               ON evidence.household_id = draft.household_id AND evidence.draft_id = draft.id
            WHERE draft.household_id = %s AND draft.group_id = %s AND draft.state = 'ready'
            ORDER BY draft.source_order
            FOR UPDATE OF draft""",
        (household_id, group_id),
    )
    return [_DraftRow.from_row(row) for row in cur.fetchall()]


def _dedupe_identity(draft: _DraftRow) -> tuple[str | None, str | None, str | None]:
    """(source_event_id, dedupe_version, dedupe_fingerprint), honoring their DB exclusivity.

    investment_transaction_dedupe_shape_agrees requires exactly one form: a source event id
    alone, or a version-and-fingerprint pair alone — never both, even though the evidence row
    behind a draft always carries a fingerprint regardless of whether it also has a source event
    id. Passing all three through unconditionally is a CHECK violation on the very statements
    whose adapter names its own source event ids.
    """
    if draft.source_event_id is not None:
        return draft.source_event_id, None, None
    return None, draft.dedupe_version, draft.dedupe_fingerprint


def _mark_committed(
    cur: psycopg.Cursor[Any], household_id: UUID, draft_id: UUID, event_id: UUID
) -> None:
    cur.execute(
        """UPDATE investment_event_draft
              SET state = 'committed', committed_transaction_id = %s, updated_at = now()
            WHERE household_id = %s AND id = %s""",
        (event_id, household_id, draft_id),
    )


def _frozen_flow(
    cur: psycopg.Cursor[Any],
    draft: _DraftRow,
    reporting_pair: tuple[str, str],
) -> tuple[date | None, Decimal | None, Decimal | None, Decimal | None, Decimal | None]:
    # A raw DecimalException (translate()'s quantize overflowing) propagates as-is rather than
    # being wrapped: _commit_group already catches it per group, so one draft's overflow blocks
    # only its own group instead of the InvestmentCommandError it used to raise, which had no
    # per-group boundary and would have aborted every other group in the same commit call too.
    if draft.net_amount == 0:
        return None, None, None, None, None
    rate_to_base, rate_to_secondary, _carried, rate_date = _frozen(
        cur, draft.event_date, draft.currency, *reporting_pair
    )
    return (
        rate_date,
        rate_to_base,
        rate_to_secondary,
        translate(draft.net_amount, rate_to_base),
        translate(draft.net_amount, rate_to_secondary),
    )


def _commit_simple(
    cur: psycopg.Cursor[Any],
    owner: OwnerSession,
    draft: _DraftRow,
    source_contract: str,
    reporting_pair: tuple[str, str],
) -> None:
    fx_date, rate_base, rate_secondary, amount_base, amount_secondary = _frozen_flow(
        cur, draft, reporting_pair
    )
    source_event_id, dedupe_version, dedupe_fingerprint = _dedupe_identity(draft)
    cur.execute(
        """SELECT append_investment_transaction(
             %s, %s, %s, %s::investment_event_kind,
             %s, %s, %s, %s, %s, %s, %s,
             %s, %s, %s, %s, %s,
             'statement'::investment_event_source, %s, %s, %s, %s, NULL, NULL)""",
        (
            draft.account_id,
            draft.security_id,
            draft.event_date,
            draft.event_kind,
            draft.units,
            draft.price,
            draft.gross_amount,
            draft.charges,
            draft.withheld_tax,
            draft.net_amount,
            draft.currency,
            fx_date,
            rate_base,
            rate_secondary,
            amount_base,
            amount_secondary,
            source_contract,
            source_event_id,
            dedupe_version,
            dedupe_fingerprint,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment append returned no event")
    event_id = UUID(str(row[0]))
    _mark_committed(cur, owner.household_id, draft.draft_id, event_id)
    if draft.event_kind in _DISPOSALS:
        _ensure_disposal_review(cur, owner.household_id, event_id, draft.event_kind)


def _commit_split(
    cur: psycopg.Cursor[Any], owner: OwnerSession, draft: _DraftRow, source_contract: str
) -> None:
    assert draft.unit_ratio is not None
    source_event_id, dedupe_version, dedupe_fingerprint = _dedupe_identity(draft)
    cur.execute(
        """SELECT append_investment_split(
             %s, %s, %s, %s, 'statement'::investment_event_source, %s, %s, %s, %s, NULL, NULL)""",
        (
            draft.account_id,
            draft.security_id,
            draft.event_date,
            draft.unit_ratio,
            source_contract,
            source_event_id,
            dedupe_version,
            dedupe_fingerprint,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment append returned no event")
    _mark_committed(cur, owner.household_id, draft.draft_id, UUID(str(row[0])))


def _commit_transfer(
    cur: psycopg.Cursor[Any],
    owner: OwnerSession,
    out_kind: str,
    out_draft: _DraftRow,
    in_draft: _DraftRow,
    source_contract: str,
) -> None:
    out_source_event_id, out_dedupe_version, out_dedupe_fingerprint = _dedupe_identity(out_draft)
    in_source_event_id, in_dedupe_version, in_dedupe_fingerprint = _dedupe_identity(in_draft)
    cur.execute(
        """SELECT out_event_id, in_event_id FROM append_investment_unit_transfer(
             %s::investment_event_kind, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
             'statement'::investment_event_source, %s,
             %s, %s, %s, %s, %s, %s, NULL, NULL)""",
        (
            out_kind,
            out_draft.event_date,
            out_draft.account_id,
            out_draft.security_id,
            out_draft.units,
            out_draft.price,
            out_draft.gross_amount,
            in_draft.account_id,
            in_draft.security_id,
            in_draft.units,
            in_draft.price,
            in_draft.gross_amount,
            out_draft.currency,
            source_contract,
            out_source_event_id,
            out_dedupe_version,
            out_dedupe_fingerprint,
            in_source_event_id,
            in_dedupe_version,
            in_dedupe_fingerprint,
        ),
    )
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("investment unit transfer returned no legs")
    out_id, in_id = row
    _mark_committed(cur, owner.household_id, out_draft.draft_id, UUID(str(out_id)))
    _mark_committed(cur, owner.household_id, in_draft.draft_id, UUID(str(in_id)))


def _leg_match_key(draft: _DraftRow) -> tuple[date, Decimal]:
    """The invariant a paired leg shares with its counterpart, by the DB's own pair_agrees rule.

    A switch or systematic-transfer pair agrees on gross value (V41: p_out_gross_amount =
    p_in_gross_amount); its two securities differ, so units legitimately do not match. A
    platform-transfer pair carries no price or gross at all (CHECK investment_event_kind_
    fields_agree) and instead agrees on equal-magnitude units. Matching on whichever applies —
    never blind position — is what lets an ambiguous or mismatched pairing be refused before any
    append, rather than silently wiring the wrong two legs together.
    """
    if draft.gross_amount is not None:
        return draft.event_date, draft.gross_amount
    return draft.event_date, abs(draft.units) if draft.units is not None else Decimal(0)


def _pair_legs(outs: list[_DraftRow], ins: list[_DraftRow]) -> list[tuple[_DraftRow, _DraftRow]]:
    if len(outs) != len(ins):
        raise _CommitDataError("investment transfer legs are unpaired")
    remaining = list(ins)
    pairs: list[tuple[_DraftRow, _DraftRow]] = []
    for out_draft in outs:
        key = _leg_match_key(out_draft)
        matches = [in_draft for in_draft in remaining if _leg_match_key(in_draft) == key]
        if len(matches) != 1:
            raise _CommitDataError("investment transfer legs are ambiguous")
        in_draft = matches[0]
        if out_draft.event_date != in_draft.event_date or out_draft.currency != in_draft.currency:
            raise _CommitDataError("investment transfer legs disagree on date or currency")
        remaining.remove(in_draft)
        pairs.append((out_draft, in_draft))
    return pairs


def _commit_drafts(
    cur: psycopg.Cursor[Any],
    owner: OwnerSession,
    drafts: list[_DraftRow],
    source_contract: str,
    reporting_pair: tuple[str, str],
) -> None:
    by_kind: dict[str, list[_DraftRow]] = {}
    for draft in drafts:
        by_kind.setdefault(draft.event_kind, []).append(draft)
    for out_kind, in_kind in _LEG_OUT_TO_IN.items():
        outs = by_kind.pop(out_kind, [])
        ins = by_kind.pop(in_kind, [])
        for out_draft, in_draft in _pair_legs(outs, ins):
            _commit_transfer(cur, owner, out_kind, out_draft, in_draft, source_contract)
    for split_draft in by_kind.pop("split", []):
        _commit_split(cur, owner, split_draft, source_contract)
    for remaining_drafts in by_kind.values():
        for draft in remaining_drafts:
            _commit_simple(cur, owner, draft, source_contract, reporting_pair)


def _classify_refusal(exc: Exception) -> CommitBlockReason:
    if isinstance(exc, HTTPException):
        return "missing_fx_rate"
    if (
        isinstance(exc, psycopg.Error)
        and exc.diag.constraint_name == "investment_group_has_open_blocker"
    ):
        return "open_review"
    return "database_refusal"


def _commit_group(
    cur: psycopg.Cursor[Any],
    owner: OwnerSession,
    group_id: UUID,
    group_number: int,
    source_contract: str,
    reporting_pair: tuple[str, str],
) -> CommittedGroupOutcome:
    savepoint = f"investment_commit_group_{group_number}"
    cur.execute(f"SAVEPOINT {savepoint}")  # noqa: S608 - fixed identifier, not user input
    try:
        drafts = _ready_drafts(cur, owner.household_id, group_id)
        _commit_drafts(cur, owner, drafts, source_contract, reporting_pair)
        cur.execute(
            """UPDATE investment_import_group
                  SET status = 'committed', committed_at = now(), updated_at = now()
                WHERE household_id = %s AND id = %s""",
            (owner.household_id, group_id),
        )
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")  # noqa: S608
        return CommittedGroupOutcome(group_number, "committed")
    except (psycopg.Error, HTTPException, DecimalException, _CommitDataError) as exc:
        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")  # noqa: S608
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")  # noqa: S608
        # The group is deliberately left exactly as it was (still 'ready'): a missing FX rate or
        # a transient database refusal describes today's attempt, not the group's staged content,
        # so a later commit call — the rate backfilled, the conflicting fact corrected — retries
        # it rather than finding it already written off as 'blocked'. Only an open blocking
        # review, which the caller already checked before ever reaching this function, or this
        # same V32 trigger firing here, means the group cannot commit until that review clears.
        return CommittedGroupOutcome(group_number, "blocked", _classify_refusal(exc))


def _provisional_status(outcomes: list[CommittedGroupOutcome]) -> StagedImportStatus:
    if not outcomes:
        return "completed"
    if any(outcome.status == "committed" for outcome in outcomes):
        return "partially_committed"
    return "awaiting_review"


def _finalize_status(
    owner: OwnerSession,
    import_id: UUID,
    provisional: StagedImportStatus,
    outcomes: list[CommittedGroupOutcome],
) -> StagedImportStatus:
    if provisional != "partially_committed" or any(
        outcome.status == "blocked" for outcome in outcomes
    ):
        return provisional
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE investment_import
                      SET status = 'completed', updated_at = now(), completed_at = now()
                    WHERE household_id = %s AND id = %s AND status = 'partially_committed'
                    RETURNING 1""",
                (owner.household_id, import_id),
            )
            if cur.fetchone() is None:
                return provisional
    except psycopg.Error as exc:
        raise InvestmentCommandError(422, "investment import could not be finalized") from exc
    return "completed"


def commit_staged_import(
    owner: OwnerSession, import_id: UUID, expected_state: str
) -> CommitOutcome:
    """Commit every ready group once, under the household lock, one savepoint per group."""
    try:
        with connect() as conn, conn.cursor() as cur:
            scope_to_owner(conn, owner.user_id)
            cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (owner.household_id,))
            cur.execute(
                """SELECT status::text, source_contract FROM investment_import
                     WHERE household_id = %s AND id = %s FOR UPDATE""",
                (owner.household_id, import_id),
            )
            row = cur.fetchone()
            if row is None:
                raise InvestmentCommandError(404, "investment import not found")
            status, source_contract = str(row[0]), str(row[1])
            if status in {"cancelled", "failed"}:
                raise InvestmentCommandError(409, "investment import is terminal")
            if _expected_state(cur, owner.household_id, import_id) != expected_state:
                raise InvestmentCommandError(409, "investment import state has changed")
            reporting_pair = _lock_reporting_pair(cur, owner.household_id)
            cur.execute(
                """SELECT id, group_number, status::text FROM investment_import_group
                     WHERE household_id = %s AND import_id = %s
                    ORDER BY group_number FOR UPDATE""",
                (owner.household_id, import_id),
            )
            groups = cur.fetchall()
            outcomes: list[CommittedGroupOutcome] = []
            for group_id_raw, group_number, group_status in groups:
                if group_status == "cancelled":
                    continue
                if group_status == "committed":
                    outcomes.append(CommittedGroupOutcome(int(group_number), "committed"))
                    continue
                # A blocking review already flipped this group to 'blocked' the moment it was
                # opened (trg_investment_review_item_blocks_group); an open blocker refuses the
                # very transition to 'committed' this function performs, so there is nothing to
                # gain by attempting it — only wasted appends that a rollback would discard.
                if group_status == "blocked":
                    outcomes.append(
                        CommittedGroupOutcome(int(group_number), "blocked", "open_review")
                    )
                    continue
                outcomes.append(
                    _commit_group(
                        cur,
                        owner,
                        UUID(str(group_id_raw)),
                        int(group_number),
                        source_contract,
                        reporting_pair,
                    )
                )
            provisional = _provisional_status(outcomes)
            # completed_at tracks its own CHECK (investment_import_completion_agrees): a status
            # other than 'completed' must carry a null completed_at, so a replay that finds
            # every group already committed and lands back on 'completed' via _finalize_status
            # below must first clear it here rather than leave a stale timestamp behind.
            cur.execute(
                """UPDATE investment_import
                      SET status = %s, updated_at = now(),
                          completed_at = CASE WHEN %s = 'completed' THEN now() ELSE NULL END
                    WHERE household_id = %s AND id = %s""",
                (provisional, provisional, owner.household_id, import_id),
            )
    except psycopg.Error as exc:
        raise InvestmentCommandError(422, "investment import could not be committed") from exc
    try:
        staging.reconcile_staged_checkpoints(owner, import_id)
    except StagingError:
        pass  # best-effort: committed financial facts are already durable regardless
    final_status = _finalize_status(owner, import_id, provisional, outcomes)
    return CommitOutcome(import_id, final_status, tuple(outcomes))
