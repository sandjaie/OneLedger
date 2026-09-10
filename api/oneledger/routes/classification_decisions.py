"""Guarded classification correction previews, application and conditional undo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect

router = APIRouter(prefix="/classification-runs", tags=["classification"])

TxnType = Literal[
    "income",
    "expense",
    "fee",
    "interest",
    "tax",
    "refund",
    "transfer_in",
    "transfer_out",
    "investment_buy",
    "investment_sell",
    "investment_income",
    "loan_disbursement",
    "loan_repayment",
    "adjustment",
    "opening_balance",
]
ClassifiedBy = Literal[
    "user_manual",
    "user_rule",
    "merchant_alias",
    "recurring_series",
    "prior_match",
    "seed_rule",
    "unclassified",
    "ai",
]
PaymentMethod = Literal[
    "card",
    "contactless",
    "atm",
    "cash",
    "cheque",
    "upi",
    "neft",
    "imps",
    "rtgs",
    "wire",
    "direct_debit",
    "standing_instruction",
    "wallet",
    "online",
    "other",
]


class DerivedChanges(BaseModel):
    """Only V23's derived-state allowlist; absence means preserve, null means clear."""

    model_config = ConfigDict(extra="forbid")

    txnType: TxnType | None = None
    categoryId: UUID | None = None
    merchantId: UUID | None = None
    paymentMethod: PaymentMethod | None = None
    classifiedBy: ClassifiedBy | None = None
    classificationRuleId: UUID | None = None
    classificationConfidence: Decimal | None = Field(default=None, ge=0, le=1)
    isReviewed: bool | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    tags: list[str] | None = Field(default=None, max_length=32)
    isExcluded: bool | None = None

    @field_validator("classificationConfidence")
    @classmethod
    def confidence_fits_the_ledger(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value != value.quantize(Decimal("0.001")):
            raise ValueError("classificationConfidence has at most three decimal places")
        return value

    @model_validator(mode="after")
    def contains_a_change(self) -> DerivedChanges:
        if not self.model_fields_set:
            raise ValueError("changes must name at least one derived classification field")
        for field in ("txnType", "classifiedBy", "isReviewed", "isExcluded"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} may be omitted but not cleared")
        if self.tags is not None and any(len(tag) > 100 for tag in self.tags):
            raise ValueError("tags may contain at most 100 characters each")
        return self


class PreviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactionId: UUID
    changes: DerivedChanges
    reason: str | None = Field(default=None, min_length=1, max_length=1000)


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledgeVersionId: UUID
    reason: str = Field(min_length=1, max_length=1000)
    items: list[PreviewItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def transactions_are_unique(self) -> PreviewBody:
        ids = [item.transactionId for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("each transaction may appear only once in a preview")
        return self


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotencyKey: str = Field(min_length=1, max_length=200)


class UndoBody(ApplyBody):
    reason: str = Field(min_length=1, max_length=1000)


class ClassificationState(BaseModel):
    txnType: TxnType
    categoryId: UUID | None
    merchantId: UUID | None
    paymentMethod: PaymentMethod | None
    classifiedBy: ClassifiedBy
    classificationRuleId: UUID | None
    classificationConfidence: Decimal | None
    isReviewed: bool
    country: str | None
    tags: list[str] | None
    isExcluded: bool


class ResultItem(BaseModel):
    transactionId: UUID
    status: Literal["changed", "conflicted", "unresolved"]
    reason: str


class RunResult(BaseModel):
    runId: UUID
    state: Literal["proposed", "applied", "conflict", "expired"]
    changed: int
    conflicted: int
    unresolved: int
    items: list[ResultItem]
    sourceRunId: UUID | None = None


class Proposal(BaseModel):
    transactionId: UUID
    expectedState: ClassificationState
    proposedState: ClassificationState
    reason: str


class PreviewResult(RunResult):
    proposals: list[Proposal]


_FIELD_NAMES = {
    "txnType": "txn_type",
    "categoryId": "category_id",
    "merchantId": "merchant_id",
    "paymentMethod": "payment_method",
    "classifiedBy": "classified_by",
    "classificationRuleId": "classification_rule_id",
    "classificationConfidence": "classification_confidence",
    "isReviewed": "is_reviewed",
    "country": "country",
    "tags": "tags",
    "isExcluded": "is_excluded",
}
_STATE_COLUMNS = """jsonb_build_object(
    'txn_type', {alias}.txn_type, 'category_id', {alias}.category_id,
    'merchant_id', {alias}.merchant_id, 'payment_method', {alias}.payment_method,
    'classified_by', {alias}.classified_by,
    'classification_rule_id', {alias}.classification_rule_id,
    'classification_confidence', {alias}.classification_confidence,
    'is_reviewed', {alias}.is_reviewed, 'country', {alias}.country,
    'tags', {alias}.tags, 'is_excluded', {alias}.is_excluded)"""
_RESULT_ITEMS = 3


def _state_sql(alias: str = "t") -> str:
    return _STATE_COLUMNS.format(alias=alias)


def _state_out(state: dict[str, Any]) -> ClassificationState:
    return ClassificationState(**{wire: state[column] for wire, column in _FIELD_NAMES.items()})


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _proposed_state(current: dict[str, Any], changes: DerivedChanges) -> dict[str, Any]:
    proposed = dict(current)
    for wire, value in changes.model_dump(exclude_unset=True).items():
        proposed[_FIELD_NAMES[wire]] = _json_value(value)
    return proposed


def _references_exist(cur: psycopg.Cursor[Any], household_id: UUID, state: dict[str, Any]) -> bool:
    checks = (
        ("category", state["category_id"], "household_id IS NULL OR household_id = %s"),
        ("merchant", state["merchant_id"], "household_id IS NULL OR household_id = %s"),
        ("classification_rule", state["classification_rule_id"], "household_id = %s"),
    )
    for table, reference, household_clause in checks:
        if reference is None:
            continue
        cur.execute(
            f"SELECT 1 FROM {table} WHERE id = %s AND ({household_clause})",  # noqa: S608
            (reference, household_id),
        )
        if cur.fetchone() is None:
            return False
    return True


def _payload(result: RunResult) -> dict[str, Any]:
    return result.model_dump(mode="json", exclude={"runId", "sourceRunId"}) | (
        {"sourceRunId": str(result.sourceRunId)} if result.sourceRunId is not None else {}
    )


def _stored_result(run_id: UUID, result: dict[str, Any]) -> RunResult:
    return RunResult.model_validate({"runId": run_id, **result})


def _summary(
    run_id: UUID,
    state: Literal["proposed", "applied", "conflict", "expired"],
    *,
    changed: int = 0,
    conflicted: int = 0,
    unresolved: int = 0,
    items: list[ResultItem] | None = None,
    source_run_id: UUID | None = None,
) -> RunResult:
    return RunResult(
        runId=run_id,
        state=state,
        changed=changed,
        conflicted=conflicted,
        unresolved=unresolved,
        items=(items or [])[:_RESULT_ITEMS],
        sourceRunId=source_run_id,
    )


def _finish_run(
    cur: psycopg.Cursor[Any], run_id: UUID, result: RunResult, idempotency_key: str
) -> None:
    cur.execute(
        """UPDATE classification_run
              SET state = %s, result = %s, result_reason = %s,
                  idempotency_key = %s, resolved_at = now()
            WHERE id = %s""",
        (
            result.state,
            Jsonb(_payload(result)),
            result.items[0].reason if result.items else None,
            idempotency_key,
            run_id,
        ),
    )


def _write_state(
    cur: psycopg.Cursor[Any], transaction_id: UUID, household_id: UUID, state: dict[str, Any]
) -> None:
    cur.execute(
        """UPDATE transaction SET
               txn_type = (%s->>'txn_type')::txn_type,
               category_id = NULLIF(%s->>'category_id', '')::uuid,
               merchant_id = NULLIF(%s->>'merchant_id', '')::uuid,
               payment_method = NULLIF(%s->>'payment_method', '')::payment_method,
               classified_by = (%s->>'classified_by')::classified_by,
               classification_rule_id = NULLIF(%s->>'classification_rule_id', '')::uuid,
               classification_confidence = NULLIF(%s->>'classification_confidence', '')::numeric,
               is_reviewed = (%s->>'is_reviewed')::boolean,
               country = NULLIF(%s->>'country', ''),
               tags = CASE WHEN %s->'tags' = 'null'::jsonb THEN NULL
                           ELSE ARRAY(SELECT jsonb_array_elements_text(%s->'tags')) END,
               is_excluded = (%s->>'is_excluded')::boolean,
               updated_at = now()
             WHERE id = %s AND household_id = %s""",
        (*([Jsonb(state)] * 12), transaction_id, household_id),
    )


def _idempotency_owner(
    cur: psycopg.Cursor[Any], household_id: UUID, key: str
) -> tuple[UUID, dict[str, Any]] | None:
    cur.execute(
        "SELECT id, result FROM classification_run"
        " WHERE household_id = %s AND idempotency_key = %s",
        (household_id, key),
    )
    row = cur.fetchone()
    return None if row is None else (row[0], row[1])


def _lock_household(cur: psycopg.Cursor[Any], household_id: UUID) -> None:
    cur.execute("SELECT 1 FROM household WHERE id = %s FOR UPDATE", (household_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail="household not found")


@router.post("/preview")
def preview(
    body: PreviewBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> PreviewResult:
    with connect() as conn, conn.cursor() as cur:
        _lock_household(cur, owner.household_id)
        cur.execute(
            """SELECT 1 FROM classification_knowledge_version
                WHERE id = %s AND household_id = %s AND state = 'active' FOR SHARE""",
            (body.knowledgeVersionId, owner.household_id),
        )
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="active knowledge version not found")
        cur.execute(
            """INSERT INTO classification_run
                 (household_id, knowledge_version_id, actor_user_id, reason, expires_at)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (
                owner.household_id,
                body.knowledgeVersionId,
                owner.user_id,
                body.reason,
                datetime.now(UTC) + timedelta(hours=1),
            ),
        )
        run_id = cur.fetchone()[0]  # type: ignore[index]
        proposals: list[Proposal] = []
        unresolved: list[ResultItem] = []
        state_sql = _state_sql()
        for requested in body.items:
            cur.execute(
                f"""SELECT t.updated_at, t.dedupe_hash, t.transfer_id, {state_sql}
                       FROM transaction t
                      WHERE t.id = %s AND t.household_id = %s""",  # noqa: S608
                (requested.transactionId, owner.household_id),
            )
            row = cur.fetchone()
            reason = requested.reason or body.reason
            refusal: str | None = None
            if row is None:
                refusal = "transaction not found"
            else:
                updated_at, source_key, transfer_id, current = row
                proposed = _proposed_state(current, requested.changes)
                if current["classified_by"] == "user_manual":
                    refusal = "manual classification is protected"
                elif transfer_id is not None:
                    refusal = "confirmed transfer leg is protected"
                elif proposed["txn_type"] in {"transfer_in", "transfer_out"}:
                    refusal = "classification repair cannot create a transfer leg"
                elif current == proposed:
                    refusal = "proposal makes no classification change"
                elif not _references_exist(cur, owner.household_id, proposed):
                    refusal = "classification reference not found"
            if refusal is not None:
                unresolved.append(
                    ResultItem(
                        transactionId=requested.transactionId,
                        status="unresolved",
                        reason=refusal,
                    )
                )
                continue
            cur.execute(
                """INSERT INTO classification_run_item
                     (household_id, run_id, transaction_id, expected_transaction_updated_at,
                      expected_source_key, expected_state, proposed_state, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    owner.household_id,
                    run_id,
                    requested.transactionId,
                    updated_at,
                    source_key,
                    Jsonb(current),
                    Jsonb(proposed),
                    reason,
                ),
            )
            proposals.append(
                Proposal(
                    transactionId=requested.transactionId,
                    expectedState=_state_out(current),
                    proposedState=_state_out(proposed),
                    reason=reason,
                )
            )
        result = _summary(
            run_id,
            "proposed",
            changed=len(proposals),
            unresolved=len(unresolved),
            items=unresolved,
        )
        cur.execute(
            "UPDATE classification_run SET result = %s WHERE id = %s",
            (Jsonb(_payload(result)), run_id),
        )
        return PreviewResult(**result.model_dump(), proposals=proposals)


@router.post("/{run_id}/apply")
def apply(
    run_id: UUID, body: ApplyBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RunResult:
    with connect() as conn, conn.cursor() as cur:
        claimed = _idempotency_owner(cur, owner.household_id, body.idempotencyKey)
        if claimed is not None and claimed[0] != run_id:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another run")
        _lock_household(cur, owner.household_id)
        claimed = _idempotency_owner(cur, owner.household_id, body.idempotencyKey)
        if claimed is not None and claimed[0] != run_id:
            raise HTTPException(status_code=409, detail="idempotency key belongs to another run")
        cur.execute(
            """SELECT state, result, idempotency_key, knowledge_version_id, expires_at <= now()
                 FROM classification_run
                WHERE id = %s AND household_id = %s FOR UPDATE""",
            (run_id, owner.household_id),
        )
        run = cur.fetchone()
        if run is None:
            raise HTTPException(status_code=404, detail="classification run not found")
        state, prior_result, prior_key, knowledge_version_id, expired = run
        if state != "proposed":
            if prior_key == body.idempotencyKey and state in {"applied", "conflict", "expired"}:
                return _stored_result(run_id, prior_result)
            raise HTTPException(status_code=409, detail=f"classification run is already {state}")

        cur.execute(
            """SELECT state FROM classification_knowledge_version
                WHERE id = %s AND household_id = %s FOR SHARE""",
            (knowledge_version_id, owner.household_id),
        )
        evidence = cur.fetchone()
        state_sql = _state_sql()
        cur.execute(
            f"""SELECT i.transaction_id, i.expected_transaction_updated_at,
                       i.expected_source_key, i.expected_state, i.proposed_state, i.reason,
                       t.updated_at, t.dedupe_hash, t.transfer_id, {state_sql},
                       latest.id, latest.after_state
                  FROM classification_run_item i
                  JOIN transaction t ON t.id = i.transaction_id
                                    AND t.household_id = i.household_id
                  LEFT JOIN LATERAL (
                    SELECT d.id, d.after_state FROM classification_decision d
                     WHERE d.household_id = i.household_id
                       AND d.transaction_id = i.transaction_id
                     ORDER BY d.created_at DESC, d.id DESC LIMIT 1
                  ) latest ON true
                 WHERE i.run_id = %s AND i.household_id = %s
                 ORDER BY i.transaction_id FOR UPDATE OF t""",  # noqa: S608
            (run_id, owner.household_id),
        )
        rows = cur.fetchall()
        if expired:
            items = [
                ResultItem(transactionId=row[0], status="unresolved", reason="preview expired")
                for row in rows
            ]
            result = _summary(run_id, "expired", unresolved=len(rows), items=items)
            _finish_run(cur, run_id, result, body.idempotencyKey)
            return result

        conflicts: list[ResultItem] = []
        supersedes: dict[UUID, UUID | None] = {}
        for row in rows:
            (
                transaction_id,
                expected_updated_at,
                expected_source,
                expected_state,
                proposed_state,
                _reason,
                actual_updated_at,
                actual_source,
                transfer_id,
                actual_state,
                latest_id,
                latest_after,
            ) = row
            conflict: str | None = None
            if evidence is None or evidence[0] != "active":
                conflict = "knowledge version is no longer active"
            elif transfer_id is not None:
                conflict = "confirmed transfer leg is protected"
            elif actual_updated_at != expected_updated_at or actual_source != expected_source:
                conflict = "transaction changed after preview"
            elif actual_state != expected_state:
                conflict = "classification changed after preview"
            elif not _references_exist(cur, owner.household_id, proposed_state):
                conflict = "classification reference is no longer available"
            elif latest_id is not None and latest_after != expected_state:
                conflict = "later classification correction exists"
            if conflict is not None:
                conflicts.append(
                    ResultItem(transactionId=transaction_id, status="conflicted", reason=conflict)
                )
            supersedes[transaction_id] = latest_id
        if conflicts:
            result = _summary(run_id, "conflict", conflicted=len(conflicts), items=conflicts)
            _finish_run(cur, run_id, result, body.idempotencyKey)
            return result

        changed: list[ResultItem] = []
        for row in rows:
            transaction_id, _, _, expected_state, proposed_state, reason = row[:6]
            _write_state(cur, transaction_id, owner.household_id, proposed_state)
            cur.execute(
                """INSERT INTO classification_decision
                     (household_id, run_id, transaction_id, knowledge_version_id,
                      actor_user_id, before_state, after_state, reason, supersedes_decision_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    owner.household_id,
                    run_id,
                    transaction_id,
                    knowledge_version_id,
                    owner.user_id,
                    Jsonb(expected_state),
                    Jsonb(proposed_state),
                    reason,
                    supersedes[transaction_id],
                ),
            )
            changed.append(
                ResultItem(transactionId=transaction_id, status="changed", reason=reason)
            )
        unresolved = int(prior_result.get("unresolved", 0))
        prior_items = [ResultItem.model_validate(item) for item in prior_result.get("items", [])]
        result = _summary(
            run_id,
            "applied",
            changed=len(changed),
            unresolved=unresolved,
            items=[*changed, *prior_items],
        )
        _finish_run(cur, run_id, result, body.idempotencyKey)
        return result


@router.post("/{run_id}/undo")
def undo(
    run_id: UUID, body: UndoBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> RunResult:
    with connect() as conn, conn.cursor() as cur:
        claimed = _idempotency_owner(cur, owner.household_id, body.idempotencyKey)
        if claimed is not None:
            repeated = _stored_result(claimed[0], claimed[1])
            if repeated.sourceRunId == run_id:
                return repeated
            raise HTTPException(status_code=409, detail="idempotency key belongs to another run")
        _lock_household(cur, owner.household_id)
        claimed = _idempotency_owner(cur, owner.household_id, body.idempotencyKey)
        if claimed is not None:
            repeated = _stored_result(claimed[0], claimed[1])
            if repeated.sourceRunId == run_id:
                return repeated
            raise HTTPException(status_code=409, detail="idempotency key belongs to another run")
        cur.execute(
            """SELECT knowledge_version_id, state FROM classification_run
                WHERE id = %s AND household_id = %s FOR UPDATE""",
            (run_id, owner.household_id),
        )
        source = cur.fetchone()
        if source is None:
            raise HTTPException(status_code=404, detail="classification run not found")
        knowledge_version_id, source_state = source
        if source_state != "applied":
            raise HTTPException(status_code=409, detail="only an applied run can be undone")
        cur.execute(
            """INSERT INTO classification_run
                 (household_id, knowledge_version_id, actor_user_id, reason, expires_at,
                  idempotency_key)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                owner.household_id,
                knowledge_version_id,
                owner.user_id,
                body.reason,
                datetime.now(UTC) + timedelta(hours=1),
                body.idempotencyKey,
            ),
        )
        undo_run_id = cur.fetchone()[0]  # type: ignore[index]
        state_sql = _state_sql()
        cur.execute(
            f"""SELECT d.id, d.transaction_id, d.before_state, d.after_state,
                       d.created_at, i.expected_source_key, t.updated_at, t.dedupe_hash,
                       t.transfer_id, {state_sql},
                       EXISTS (SELECT FROM classification_decision later
                                WHERE later.supersedes_decision_id = d.id)
                  FROM classification_decision d
                  JOIN classification_run_item i
                    ON i.run_id = d.run_id AND i.transaction_id = d.transaction_id
                   AND i.household_id = d.household_id
                  JOIN transaction t ON t.id = d.transaction_id
                                    AND t.household_id = d.household_id
                 WHERE d.run_id = %s AND d.household_id = %s
                 ORDER BY d.transaction_id FOR UPDATE OF t""",  # noqa: S608
            (run_id, owner.household_id),
        )
        rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=409, detail="applied run has no decisions to undo")
        conflicts: list[ResultItem] = []
        for row in rows:
            (
                _decision_id,
                transaction_id,
                before_state,
                after_state,
                applied_at,
                expected_source,
                actual_updated_at,
                actual_source,
                transfer_id,
                actual_state,
                superseded,
            ) = row
            conflict: str | None = None
            if superseded:
                conflict = "later classification correction exists"
            elif transfer_id is not None:
                conflict = "confirmed transfer leg is protected"
            elif actual_updated_at != applied_at or actual_source != expected_source:
                conflict = "transaction changed after the source run"
            elif actual_state != after_state:
                conflict = "classification changed after the source run"
            elif not _references_exist(cur, owner.household_id, before_state):
                conflict = "restored classification reference is no longer available"
            if conflict is not None:
                conflicts.append(
                    ResultItem(transactionId=transaction_id, status="conflicted", reason=conflict)
                )
        if conflicts:
            result = _summary(
                undo_run_id,
                "conflict",
                conflicted=len(conflicts),
                items=conflicts,
                source_run_id=run_id,
            )
            _finish_run(cur, undo_run_id, result, body.idempotencyKey)
            return result

        changed: list[ResultItem] = []
        for row in rows:
            decision_id, transaction_id, before_state, after_state, applied_at, source_key = row[:6]
            cur.execute(
                """INSERT INTO classification_run_item
                     (household_id, run_id, transaction_id, expected_transaction_updated_at,
                      expected_source_key, expected_state, proposed_state, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    owner.household_id,
                    undo_run_id,
                    transaction_id,
                    applied_at,
                    source_key,
                    Jsonb(after_state),
                    Jsonb(before_state),
                    body.reason,
                ),
            )
            _write_state(cur, transaction_id, owner.household_id, before_state)
            cur.execute(
                """INSERT INTO classification_decision
                     (household_id, run_id, transaction_id, knowledge_version_id,
                      actor_user_id, before_state, after_state, reason, supersedes_decision_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    owner.household_id,
                    undo_run_id,
                    transaction_id,
                    knowledge_version_id,
                    owner.user_id,
                    Jsonb(after_state),
                    Jsonb(before_state),
                    body.reason,
                    decision_id,
                ),
            )
            changed.append(
                ResultItem(transactionId=transaction_id, status="changed", reason=body.reason)
            )
        result = _summary(
            undo_run_id,
            "applied",
            changed=len(changed),
            items=changed,
            source_run_id=run_id,
        )
        _finish_run(cur, undo_run_id, result, body.idempotencyKey)
        return result
