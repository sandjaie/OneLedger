"""Household-private, evidenced counterparty matchers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg.types.json import Json
from pydantic import BaseModel, Field, field_validator, model_validator

from ..auth import OwnerSession, require_owner
from ..db import connect
from ..ingest.classify import Direction, PatternKind, validate_pattern
from ..ingest.engine import CONTEXTUAL_SECRET

router = APIRouter(prefix="/merchant-knowledge", tags=["merchant knowledge"])

Kind = Literal["merchant", "person", "own_account", "institution", "intermediary", "unresolved"]
Role = Literal[
    "purchase",
    "wallet",
    "processor",
    "transfer",
    "refund",
    "own_account",
    "institution",
    "unresolved",
    "other",
]
SourceKind = Literal["owner", "public_source", "statement", "model_proposal"]
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
    "investment_fee",
    "loan_disbursement",
    "loan_repayment",
    "adjustment",
    "opening_balance",
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


class KnowledgeBody(BaseModel):
    displayName: str = Field(min_length=1, max_length=200)
    kind: Kind
    merchantId: UUID | None = None
    merchantName: str | None = Field(default=None, min_length=1, max_length=200)
    ownAccountId: UUID | None = None
    pattern: str = Field(min_length=1, max_length=500)
    patternKind: PatternKind = "contains"
    role: Role
    priority: int = Field(default=100, ge=0, le=1000)
    sourceKind: SourceKind = "owner"
    sourceUrl: str | None = Field(default=None, max_length=2048)
    attribution: str = Field(min_length=1, max_length=200)
    evidenceNote: str = Field(min_length=1, max_length=1000)
    matcherVersion: str = Field(min_length=1, max_length=100)
    verifiedAt: datetime | None = None
    direction: Direction = "any"
    accountId: UUID | None = None
    categoryId: UUID | None = None
    txnType: TxnType | None = None
    paymentMethod: PaymentMethod | None = None

    @field_validator(
        "displayName", "merchantName", "pattern", "attribution", "evidenceNote", "matcherVersion"
    )
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def coherent(self) -> KnowledgeBody:
        validate_pattern(self.pattern, self.patternKind)
        merchant_ref = self.merchantId is not None or self.merchantName is not None
        if self.kind in {"merchant", "intermediary"}:
            if (self.merchantId is None) == (self.merchantName is None):
                raise ValueError("merchant and intermediary knowledge needs one merchant reference")
        elif merchant_ref:
            raise ValueError("this counterparty kind cannot reference a merchant")
        if (self.kind == "own_account") != (self.ownAccountId is not None):
            raise ValueError("only own-account knowledge needs an ownAccountId")
        expected = {
            "purchase": "merchant",
            "refund": "merchant",
            "wallet": "intermediary",
            "processor": "intermediary",
            "own_account": "own_account",
            "institution": "institution",
            "unresolved": "unresolved",
        }.get(self.role)
        if expected is not None and self.kind != expected:
            raise ValueError("the alias role disagrees with the counterparty kind")
        if self.kind != "merchant" and not any(
            value is not None for value in (self.categoryId, self.txnType, self.paymentMethod)
        ):
            raise ValueError("non-merchant knowledge needs a classification outcome")
        if self.sourceKind == "public_source":
            if self.sourceUrl is None:
                raise ValueError("public-source evidence needs a sourceUrl")
            parsed = urlsplit(self.sourceUrl)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("sourceUrl must be an http or https URL")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("sourceUrl must not contain credentials")
        elif self.sourceUrl is not None:
            raise ValueError("sourceUrl is only valid for public-source evidence")
        return self


class KnowledgeRow(BaseModel):
    aliasId: UUID
    counterpartyId: UUID
    knowledgeVersionId: UUID
    knowledgeId: UUID
    version: int
    displayName: str
    kind: Kind
    merchantId: UUID | None
    merchantName: str | None
    ownAccountId: UUID | None
    pattern: str
    patternKind: PatternKind
    role: Role
    priority: int
    sourceKind: SourceKind
    sourceUrl: str | None
    attribution: str
    evidenceNote: str
    matcherVersion: str
    verifiedAt: datetime | None
    direction: Direction
    accountId: UUID | None
    categoryId: UUID | None
    txnType: TxnType | None
    paymentMethod: PaymentMethod | None
    active: bool
    createdAt: datetime


_SELECT = """SELECT a.id, c.id, v.id, v.knowledge_id, v.version, c.display_name, c.kind,
                    c.merchant_id, COALESCE(m.display_name, m.canonical_name), c.own_account_id,
                    a.pattern, a.pattern_kind::text, a.role, a.priority, v.source_kind,
                    v.source_url, v.attribution, v.evidence_note, v.matcher_version,
                    v.verified_at, v.scope,
                    (a.is_active AND c.is_active AND v.state = 'active'), a.created_at
               FROM counterparty_alias a
               JOIN counterparty c ON c.id = a.counterparty_id AND c.household_id = a.household_id
               JOIN classification_knowledge_version v
                 ON v.id = a.knowledge_version_id AND v.household_id = a.household_id
          LEFT JOIN merchant m ON m.id = c.merchant_id"""


def _row(values: tuple[Any, ...]) -> KnowledgeRow:
    scope = values[20]
    return KnowledgeRow(
        aliasId=values[0],
        counterpartyId=values[1],
        knowledgeVersionId=values[2],
        knowledgeId=values[3],
        version=values[4],
        displayName=values[5],
        kind=values[6],
        merchantId=values[7],
        merchantName=values[8],
        ownAccountId=values[9],
        pattern=values[10],
        patternKind=values[11],
        role=values[12],
        priority=values[13],
        sourceKind=values[14],
        sourceUrl=values[15],
        attribution=values[16],
        evidenceNote=values[17],
        matcherVersion=values[18],
        verifiedAt=values[19],
        direction=scope.get("direction", "any"),
        accountId=scope.get("accountId"),
        categoryId=scope.get("categoryId"),
        txnType=scope.get("txnType"),
        paymentMethod=scope.get("paymentMethod"),
        active=values[21],
        createdAt=values[22],
    )


def _scope(body: KnowledgeBody) -> dict[str, str]:
    values: dict[str, Any] = {
        "direction": body.direction,
        "accountId": body.accountId,
        "categoryId": body.categoryId,
        "txnType": body.txnType,
        "paymentMethod": body.paymentMethod,
    }
    return {key: str(value) for key, value in values.items() if value is not None}


def _available(cur: psycopg.Cursor[Any], household_id: UUID, body: KnowledgeBody) -> None:
    checks = (
        (
            "category",
            body.categoryId,
            "SELECT 1 FROM category WHERE id = %s AND (household_id IS NULL OR household_id = %s)",
        ),
        ("account", body.accountId, "SELECT 1 FROM account WHERE id = %s AND household_id = %s"),
        (
            "own account",
            body.ownAccountId,
            "SELECT 1 FROM account WHERE id = %s AND household_id = %s",
        ),
        (
            "merchant",
            body.merchantId,
            "SELECT 1 FROM merchant WHERE id = %s AND (household_id IS NULL OR household_id = %s)",
        ),
    )
    for label, row_id, query in checks:
        if row_id is None:
            continue
        cur.execute(query, (row_id, household_id))
        if cur.fetchone() is None:
            raise HTTPException(
                status_code=422, detail=f"{label} is not available to this household"
            )


def _screen_private_text(cur: psycopg.Cursor[Any], body: KnowledgeBody) -> None:
    """Reject never-store identifiers in every string this endpoint persists."""
    values = (
        body.displayName,
        body.merchantName,
        body.pattern,
        body.sourceUrl,
        body.attribution,
        body.evidenceNote,
        body.matcherVersion,
    )
    for value in values:
        if value is None:
            continue
        if CONTEXTUAL_SECRET.search(value):
            raise HTTPException(
                status_code=422, detail="merchant knowledge must not contain a private secret"
            )
        cur.execute("SELECT counterparty_text_has_private_identifier(%s)", (value,))
        found = cur.fetchone()
        if found is not None and found[0]:
            raise HTTPException(
                status_code=422, detail="merchant knowledge must not contain a private identifier"
            )


def _merchant(cur: psycopg.Cursor[Any], household_id: UUID, body: KnowledgeBody) -> UUID | None:
    if body.merchantId is not None:
        return body.merchantId
    if body.merchantName is None:
        return None
    cur.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"{household_id}:{body.merchantName.casefold()}",),
    )
    cur.execute(
        """SELECT id FROM merchant
            WHERE household_id = %s
              AND lower(COALESCE(display_name, canonical_name)) = lower(%s)
            ORDER BY id LIMIT 1""",
        (household_id, body.merchantName),
    )
    found = cur.fetchone()
    if found is not None:
        return UUID(str(found[0]))
    cur.execute(
        """INSERT INTO merchant
             (household_id, canonical_name, display_name, default_category_id)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        (household_id, body.merchantName, body.merchantName, body.categoryId),
    )
    created = cur.fetchone()
    if created is None:  # RETURNING always yields, but keep the type boundary explicit.
        raise RuntimeError("merchant insert returned no identity")
    return UUID(str(created[0]))


def _insert_version(
    cur: psycopg.Cursor[Any],
    household_id: UUID,
    knowledge_id: UUID,
    version: int,
    body: KnowledgeBody,
) -> UUID:
    cur.execute(
        """INSERT INTO classification_knowledge_version
             (household_id, knowledge_id, version, source_kind, source_url, attribution,
              evidence_note, matcher_version, scope, verified_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            household_id,
            knowledge_id,
            version,
            body.sourceKind,
            body.sourceUrl,
            body.attribution,
            body.evidenceNote,
            body.matcherVersion,
            Json(_scope(body)),
            body.verifiedAt,
        ),
    )
    created = cur.fetchone()
    if created is None:
        raise RuntimeError("knowledge version insert returned no identity")
    return UUID(str(created[0]))


def _fetch(cur: psycopg.Cursor[Any], household_id: UUID, alias_id: UUID) -> KnowledgeRow:
    cur.execute(f"{_SELECT} WHERE a.household_id = %s AND a.id = %s", (household_id, alias_id))
    values = cur.fetchone()
    if values is None:
        raise HTTPException(status_code=404, detail="no such merchant knowledge")
    return _row(values)


def _safe_write(action: Callable[[], KnowledgeRow]) -> KnowledgeRow:
    try:
        return action()
    except HTTPException:
        raise
    except (
        psycopg.errors.CheckViolation,
        psycopg.errors.ForeignKeyViolation,
        psycopg.errors.InvalidTextRepresentation,
        psycopg.errors.UniqueViolation,
    ):
        raise HTTPException(status_code=422, detail="merchant knowledge was not accepted") from None


@router.get("")
def list_knowledge(
    owner: Annotated[OwnerSession, Depends(require_owner)],
    includeInactive: bool = False,
) -> list[KnowledgeRow]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""{_SELECT}
                 WHERE a.household_id = %s
                   AND (%s OR (a.is_active AND c.is_active AND v.state = 'active'))
                 ORDER BY v.version DESC, a.created_at DESC""",
            (owner.household_id, includeInactive),
        )
        return [_row(values) for values in cur.fetchall()]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_knowledge(
    body: KnowledgeBody, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> KnowledgeRow:
    def write() -> KnowledgeRow:
        with connect() as conn, conn.cursor() as cur:
            _screen_private_text(cur, body)
            _available(cur, owner.household_id, body)
            merchant_id = _merchant(cur, owner.household_id, body)
            cur.execute(
                "INSERT INTO classification_knowledge (household_id) VALUES (%s) RETURNING id",
                (owner.household_id,),
            )
            knowledge_id = cur.fetchone()[0]  # type: ignore[index]
            version_id = _insert_version(cur, owner.household_id, knowledge_id, 1, body)
            cur.execute(
                """INSERT INTO counterparty
                     (household_id, kind, display_name, merchant_id, own_account_id)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (owner.household_id, body.kind, body.displayName, merchant_id, body.ownAccountId),
            )
            counterparty_id = cur.fetchone()[0]  # type: ignore[index]
            cur.execute(
                """INSERT INTO counterparty_alias
                     (household_id, counterparty_id, knowledge_version_id, pattern,
                      pattern_kind, role, priority)
                   VALUES (%s, %s, %s, %s, %s::pattern_type, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    counterparty_id,
                    version_id,
                    body.pattern,
                    body.patternKind,
                    body.role,
                    body.priority,
                ),
            )
            return _fetch(cur, owner.household_id, cur.fetchone()[0])  # type: ignore[index]

    return _safe_write(write)


@router.patch("/{alias_id}")
def revise_knowledge(
    alias_id: UUID,
    body: KnowledgeBody,
    owner: Annotated[OwnerSession, Depends(require_owner)],
) -> KnowledgeRow:
    def write() -> KnowledgeRow:
        with connect() as conn, conn.cursor() as cur:
            _screen_private_text(cur, body)
            _available(cur, owner.household_id, body)
            cur.execute(
                f"""{_SELECT}
                     WHERE a.household_id = %s AND a.id = %s AND a.is_active
                       AND c.is_active AND v.state = 'active'
                     FOR UPDATE OF a, c, v""",
                (owner.household_id, alias_id),
            )
            values = cur.fetchone()
            if values is None:
                raise HTTPException(status_code=404, detail="no such active merchant knowledge")
            current = _row(values)
            if (
                (body.displayName, body.kind, body.ownAccountId)
                != (current.displayName, current.kind, current.ownAccountId)
                or (body.merchantId is not None and body.merchantId != current.merchantId)
                or (
                    body.merchantName is not None
                    and body.merchantName.casefold() != (current.merchantName or "").casefold()
                )
            ):
                raise HTTPException(
                    status_code=422, detail="stable counterparty identity cannot be revised"
                )
            cur.execute(
                """UPDATE counterparty_alias
                      SET is_active = false, deactivated_at = now()
                    WHERE id = %s""",
                (alias_id,),
            )
            cur.execute(
                """UPDATE classification_knowledge_version
                      SET state = 'revoked', revoked_at = now()
                    WHERE id = %s""",
                (current.knowledgeVersionId,),
            )
            version_id = _insert_version(
                cur, owner.household_id, current.knowledgeId, current.version + 1, body
            )
            cur.execute(
                """INSERT INTO counterparty_alias
                     (household_id, counterparty_id, knowledge_version_id, pattern,
                      pattern_kind, role, priority)
                   VALUES (%s, %s, %s, %s, %s::pattern_type, %s, %s) RETURNING id""",
                (
                    owner.household_id,
                    current.counterpartyId,
                    version_id,
                    body.pattern,
                    body.patternKind,
                    body.role,
                    body.priority,
                ),
            )
            return _fetch(cur, owner.household_id, cur.fetchone()[0])  # type: ignore[index]

    return _safe_write(write)


@router.delete("/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_knowledge(
    alias_id: UUID, owner: Annotated[OwnerSession, Depends(require_owner)]
) -> Response:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT counterparty_id, knowledge_version_id FROM counterparty_alias
                WHERE id = %s AND household_id = %s AND is_active FOR UPDATE""",
            (alias_id, owner.household_id),
        )
        found = cur.fetchone()
        if found is None:
            raise HTTPException(status_code=404, detail="no such active merchant knowledge")
        counterparty_id, version_id = found
        cur.execute(
            "UPDATE counterparty_alias SET is_active = false, deactivated_at = now() WHERE id = %s",
            (alias_id,),
        )
        cur.execute(
            """UPDATE classification_knowledge_version
                  SET state = 'revoked', revoked_at = now()
                WHERE id = %s""",
            (version_id,),
        )
        cur.execute(
            """SELECT 1 FROM counterparty_alias
                WHERE counterparty_id = %s AND household_id = %s AND is_active""",
            (counterparty_id, owner.household_id),
        )
        if cur.fetchone() is None:
            cur.execute(
                "UPDATE counterparty SET is_active = false, deactivated_at = now() WHERE id = %s",
                (counterparty_id,),
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
