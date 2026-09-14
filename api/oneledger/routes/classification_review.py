"""Read-only, grouped classification exceptions for the owner's review queue.

The route never changes a ledger row. It reuses the import classifier, groups only rows that
still need a judgement, and returns exact transaction IDs for the existing guarded preview API.
Amounts are deliberately absent: an equal amount is not merchant identity and the browser does
not need financial arithmetic to present a classification question.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..ingest.classify import Candidate, Classification, Knowledge, classify_row
from ..ingest.knowledge import load_knowledge
from .layouts import _preview_redactor

router = APIRouter(prefix="/classification-review", tags=["classification"])

MAX_CANDIDATES = 5000
MAX_GROUP_TRANSACTION_IDS = 200
_DESCRIPTION_REDACTOR = _preview_redactor(None)

ReviewReason = Literal[
    "conflicting_knowledge",
    "conflicting_prior",
    "excluded_role",
    "identity_unresolved",
    "low_confidence",
    "recognized_pending",
    "unclassified",
]

REPORT_EXCLUDING_TYPES = frozenset(
    {
        "transfer_in",
        "transfer_out",
        "investment_buy",
        "investment_sell",
        "investment_income",
        "investment_fee",
        "loan_disbursement",
        "loan_repayment",
        "adjustment",
        "opening_balance",
    }
)

EXPLANATIONS: dict[ReviewReason, str] = {
    "conflicting_knowledge": (
        "Saved knowledge has equally strong conflicting answers, so classification abstained."
    ),
    "conflicting_prior": (
        "Earlier reviewed rows have conflicting answers, so classification abstained."
    ),
    "excluded_role": (
        "This economic role is excluded from spending and needs confirmation before it is kept."
    ),
    "identity_unresolved": (
        "A broad category is known, but the merchant or counterparty identity is unresolved."
    ),
    "low_confidence": "A possible prior match is below the automatic-review threshold.",
    "recognized_pending": (
        "Saved knowledge recognises this row, but its derived classification is not applied yet."
    ),
    "unclassified": "No saved merchant, rule, or trusted prior recognises this wording.",
}


@dataclass(frozen=True)
class ReviewCandidate:
    transaction_id: UUID
    booked_date: date
    account_id: UUID
    account_name: str
    description: str
    amount: Decimal
    amount_base: Decimal
    currency: str
    reference_raw: str | None
    country: str | None
    txn_type: str
    category_id: UUID | None
    category_name: str | None
    merchant_id: UUID | None
    payment_method: str | None
    is_excluded: bool
    classified_by: str
    classification_confidence: Decimal | None
    is_reviewed: bool
    transfer_id: UUID | None
    is_split_parent: bool

    def classifier_candidate(self) -> Candidate:
        return Candidate(
            account_id=self.account_id,
            description_clean=self.description,
            amount=self.amount,
            currency=self.currency,
            reference_raw=self.reference_raw,
            country=self.country,
        )


class ClassificationReviewGroup(BaseModel):
    groupKey: str
    reason: ReviewReason
    explanation: str
    accountId: UUID
    accountName: str
    direction: Literal["debit", "credit"]
    description: str
    occurrences: int
    firstBookedDate: date
    lastBookedDate: date
    transactionIds: list[UUID]
    transactionIdsTruncated: bool
    currentCategoryId: UUID | None
    currentCategoryName: str | None
    currentTxnType: str
    currentPaymentMethod: str | None
    currentClassifiedBy: str
    proposedCategoryId: UUID | None
    proposedCategoryName: str | None
    proposedMerchantId: UUID | None
    proposedTxnType: str | None
    proposedPaymentMethod: str | None
    proposedClassifiedBy: str
    proposedConfidence: str
    knowledgeVersionId: UUID | None
    rememberable: bool


class ClassificationReviewResponse(BaseModel):
    groups: list[ClassificationReviewGroup]
    totalGroups: int
    totalOccurrences: int
    candidatesScanned: int
    candidateLimitReached: bool


@dataclass
class _GroupAccumulator:
    row: ReviewCandidate
    answer: Classification
    reason: ReviewReason
    transaction_ids: list[UUID]
    occurrences: int
    first_date: date
    last_date: date
    total_abs_amount_base: Decimal


def _question(row: ReviewCandidate, answer: Classification) -> ReviewReason | None:
    if (
        (row.classified_by == "user_manual" and row.is_reviewed)
        or row.transfer_id is not None
        or row.is_split_parent
    ):
        return None
    if answer.unresolved_reason == "conflicting highest-priority knowledge":
        return "conflicting_knowledge"
    if answer.unresolved_reason == "conflicting prior evidence":
        return "conflicting_prior"
    if (
        row.is_excluded
        or row.txn_type in REPORT_EXCLUDING_TYPES
        or answer.txn_type in REPORT_EXCLUDING_TYPES
    ):
        return "excluded_role"
    if answer.counterparty_role == "unresolved":
        return "identity_unresolved"
    if answer.classified_by == "unclassified":
        return "unclassified"
    if not answer.is_pre_ticked:
        return "low_confidence"
    if not _stored_agrees(row, answer):
        return "recognized_pending"
    return None


def _stored_agrees(row: ReviewCandidate, answer: Classification) -> bool:
    if row.classified_by != answer.classified_by:
        return False
    expected = (
        (answer.category_id, row.category_id),
        (answer.merchant_id, row.merchant_id),
        (answer.txn_type, row.txn_type),
        (answer.payment_method, row.payment_method),
    )
    return all(proposed is None or proposed == current for proposed, current in expected)


def _normalised_identity(description: str) -> str:
    return re.sub(r"\s+", " ", description).strip().casefold()


def _identity(
    household_id: UUID, row: ReviewCandidate, answer: Classification, reason: ReviewReason
) -> tuple[object, ...]:
    return (
        household_id,
        row.account_id,
        "debit" if row.amount < 0 else "credit",
        _normalised_identity(row.description),
        reason,
        row.category_id,
        row.txn_type,
        row.payment_method,
        row.classified_by,
        answer.category_id,
        answer.merchant_id,
        answer.txn_type,
        answer.payment_method,
        answer.knowledge_version_id,
    )


def _opaque_key(identity: tuple[object, ...]) -> str:
    material = "\x1f".join("" if value is None else str(value) for value in identity)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def build_review_groups(
    household_id: UUID,
    rows: list[ReviewCandidate],
    knowledge: Knowledge,
    *,
    category_names: dict[UUID, str],
    sort: Literal["default", "impact"] = "default",
) -> list[ClassificationReviewGroup]:
    """Resolve rows exactly as import does, then group identical unanswered questions."""
    grouped: dict[tuple[object, ...], _GroupAccumulator] = {}
    for row in rows:
        answer = classify_row(row.classifier_candidate(), knowledge)
        reason = _question(row, answer)
        if reason is None:
            continue
        identity = _identity(household_id, row, answer, reason)
        existing = grouped.get(identity)
        if existing is None:
            grouped[identity] = _GroupAccumulator(
                row=row,
                answer=answer,
                reason=reason,
                transaction_ids=[row.transaction_id],
                occurrences=1,
                first_date=row.booked_date,
                last_date=row.booked_date,
                total_abs_amount_base=abs(row.amount_base),
            )
            continue
        existing.occurrences += 1
        existing.first_date = min(existing.first_date, row.booked_date)
        existing.last_date = max(existing.last_date, row.booked_date)
        existing.total_abs_amount_base += abs(row.amount_base)
        if len(existing.transaction_ids) < MAX_GROUP_TRANSACTION_IDS:
            existing.transaction_ids.append(row.transaction_id)

    result: list[tuple[_GroupAccumulator, ClassificationReviewGroup]] = []
    for identity, group in grouped.items():
        row, answer = group.row, group.answer
        result.append(
            (
                group,
                ClassificationReviewGroup(
                    groupKey=_opaque_key(identity),
                    reason=group.reason,
                    explanation=EXPLANATIONS[group.reason],
                    accountId=row.account_id,
                    accountName=row.account_name,
                    direction="debit" if row.amount < 0 else "credit",
                    description=_DESCRIPTION_REDACTOR(row.description),
                    occurrences=group.occurrences,
                    firstBookedDate=group.first_date,
                    lastBookedDate=group.last_date,
                    transactionIds=group.transaction_ids,
                    transactionIdsTruncated=group.occurrences > len(group.transaction_ids),
                    currentCategoryId=row.category_id,
                    currentCategoryName=row.category_name,
                    currentTxnType=row.txn_type,
                    currentPaymentMethod=row.payment_method,
                    currentClassifiedBy=row.classified_by,
                    proposedCategoryId=answer.category_id,
                    proposedCategoryName=(
                        category_names.get(answer.category_id)
                        if answer.category_id is not None
                        else None
                    ),
                    proposedMerchantId=answer.merchant_id,
                    proposedTxnType=answer.txn_type,
                    proposedPaymentMethod=answer.payment_method,
                    proposedClassifiedBy=answer.classified_by,
                    proposedConfidence=f"{answer.classification_confidence:.3f}",
                    knowledgeVersionId=answer.knowledge_version_id,
                    rememberable=group.reason
                    not in {"conflicting_knowledge", "conflicting_prior", "recognized_pending"},
                ),
            )
        )
    severity = {
        "conflicting_knowledge": 0,
        "conflicting_prior": 1,
        "excluded_role": 2,
        "identity_unresolved": 3,
        "low_confidence": 4,
        "recognized_pending": 5,
        "unclassified": 6,
    }
    if sort == "impact":
        ordered = sorted(
            result,
            key=lambda item: (-item[0].total_abs_amount_base, -item[0].occurrences),
        )
    else:
        ordered = sorted(
            result,
            key=lambda item: (
                severity[item[1].reason],
                -item[1].lastBookedDate.toordinal(),
                item[1].description.casefold(),
                item[1].groupKey,
            ),
        )
    return [group for _, group in ordered]


def _row(values: tuple[Any, ...]) -> ReviewCandidate:
    return ReviewCandidate(
        transaction_id=values[0],
        booked_date=values[1],
        account_id=values[2],
        account_name=values[3],
        description=values[4],
        amount=values[5],
        amount_base=values[6],
        currency=str(values[7]).strip(),
        reference_raw=values[8],
        country=values[9],
        txn_type=values[10],
        category_id=values[11],
        category_name=values[12],
        merchant_id=values[13],
        payment_method=values[14],
        is_excluded=values[15],
        classified_by=values[16],
        classification_confidence=values[17],
        is_reviewed=values[18],
        transfer_id=values[19],
        is_split_parent=values[20],
    )


@router.get("/groups")
def review_groups(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    dateFrom: Annotated[date | None, Query()] = None,
    dateTo: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[Literal["default", "impact"], Query()] = "default",
) -> ClassificationReviewResponse:
    if dateFrom is not None and dateTo is not None and dateFrom > dateTo:
        raise HTTPException(status_code=422, detail="dateFrom must not be after dateTo")

    clauses = [
        "t.household_id = %s",
        "NOT t.is_reviewed",
        "t.transfer_id IS NULL",
        "NOT EXISTS (SELECT 1 FROM transaction child WHERE child.parent_transaction_id = t.id)",
    ]
    params: list[Any] = [owner.household_id]
    if dateFrom is not None:
        clauses.append("t.booked_date >= %s")
        params.append(dateFrom)
    if dateTo is not None:
        clauses.append("t.booked_date <= %s")
        params.append(dateTo)

    with connect() as conn, conn.cursor() as cur:
        knowledge = load_knowledge(cur, owner.household_id)
        cur.execute(
            f"""SELECT t.id, t.booked_date, t.account_id, a.name,
                       COALESCE(t.description_clean, t.description_raw), t.amount, t.amount_base,
                       t.currency,
                       t.reference_raw, t.country, t.txn_type::text, t.category_id, c.name,
                       t.merchant_id, t.payment_method::text, t.is_excluded,
                       t.classified_by::text,
                       t.classification_confidence, t.is_reviewed, t.transfer_id, false
                  FROM transaction t
                  JOIN account a ON a.id = t.account_id AND a.household_id = t.household_id
             LEFT JOIN category c ON c.id = t.category_id
                 WHERE {" AND ".join(clauses)}
                 ORDER BY t.booked_date DESC, t.id DESC
                 LIMIT %s""",
            (*params, MAX_CANDIDATES + 1),
        )
        fetched = list(cur.fetchall())
        limit_reached = len(fetched) > MAX_CANDIDATES
        rows = [_row(values) for values in fetched[:MAX_CANDIDATES]]
        cur.execute(
            """SELECT id, name FROM category
                WHERE household_id IS NULL OR household_id = %s""",
            (owner.household_id,),
        )
        category_names = {row[0]: row[1] for row in cur.fetchall()}

    groups = build_review_groups(
        owner.household_id, rows, knowledge, category_names=category_names, sort=sort
    )
    return ClassificationReviewResponse(
        groups=groups[offset : offset + limit],
        totalGroups=len(groups),
        totalOccurrences=sum(group.occurrences for group in groups),
        candidatesScanned=len(rows),
        candidateLimitReached=limit_reached,
    )
